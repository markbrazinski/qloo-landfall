"""Stages 4–5 · the 80-city scan and the explained shortlist (BUILD-PLAN slice 3; CONTRACTS B4/B5).

Records: ScanGeneration (one per confirmed profile version; holds each city's effective CityScan across retry runs),
CityScan (one rid per generation and city, a version per scan run), AssessmentSet (one version per strategist run,
with its immutable StrategistInput) and Shortlist (founder selections, carried over to each new AssessmentSet).
Provider stage strings: `scan` (Qloo) and `shortlist` (Nova).
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field
from strands import Agent

from . import brand, config, db, markets, qloo, ranking, runner
from .cassette import NoRecording
from .ledger import BudgetExhausted
from .nova import make_model
from .runner import HANDLERS, Run

STRATEGIST_VERSION = "market_strategist@5"
MAX_MODEL_CALLS = 3  # one structured call, plus at most two structured-output retries
TERMINAL = ("returned", "empty", "failed", "unresolved")
IN_FLIGHT = 4  # cities searched at once; the shared Qloo pacer holds the rate at 4.5 req/s whatever the latency


# ---- generation ----
def current_profile(pid: str) -> dict | None:
    """The confirmed profile that follows the current identity, with no fact-changed signal; else None."""
    from . import identity
    prof, ident = db.latest("profile", pid), db.latest("identity", pid)
    if not prof or not ident or not identity.follows(prof, ident) or not prof["confirmed"]:
        return None
    return prof


def generations(pid: str) -> list[dict]:
    """Latest version of each generation, oldest first."""
    rows = db.q("SELECT id, MAX(version) v FROM records WHERE kind='scan_generation' AND project_id=? GROUP BY id "
                "ORDER BY MIN(created_at), id", pid)
    return [db.get_record(r["id"], r["v"]) for r in rows]


def generation_for(pid: str, prof: dict) -> dict | None:
    return next((g for g in generations(pid) if g["profile"] == {"id": prof["id"], "version": prof["version"]}), None)


def _update_generation(gid: str, change) -> dict:
    """A new generation version, under the database lock: `change(payload)` edits a copy of the latest."""
    with db._lock:
        g = db.get_record(gid)
        payload = {k: v for k, v in g.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")}
        change(payload)
        return db.put_record("scan_generation", g["project_id"], payload, inputs=g["inputs"], rid=gid)


def start_scan(pid: str, by: str = "founder") -> dict:
    """Returns the existing generation for the confirmed profile version (a completed one is reused, never rerun), or
    creates one and starts the scan. The founder's "Re-scan" after a profile change is this same call."""
    with db._lock:
        prof = current_profile(pid)
        if prof is None:
            raise ValueError("Confirm your discovery profile first.")
        gen = generation_for(pid, prof)
        if gen:
            active = db.q("SELECT id FROM runs WHERE project_id=? AND kind='scan' AND status IN ('queued','running') "
                          "AND json_extract(detail, '$.params.generation_id')=?", pid, gen["id"])
            return {"generation_id": gen["id"], "run_id": active[0]["id"] if active else None}
        signals = markets.effective_signals(prof)
        gid = f"gen_{pid}_{len(generations(pid)) + 1}"
        db.put_record("scan_generation", pid, {
            "profile": {"id": prof["id"], "version": prof["version"]}, "route": prof["route"],
            "catalog": markets.CATALOG_VERSION,
            "request_config": {"radius_m": markets.RADIUS_M, "take": markets.TAKE, "signals": signals},
            "cities": {}, "status": "running"}, inputs=[{"id": prof["id"], "version": prof["version"], "keys": ["signals"]}],
            rid=gid)
    detail = f"{len(markets.ORDER)} cities · " + ", ".join(s["name"] for s in signals)
    if by == "founder":
        db.event(pid, "founder", {"stage": "Markets", "task": "Started exploring markets", "detail": detail})
    else:
        db.event(pid, "activity", {"stage": "Markets", "role": "Market scout", "tool": "Qloo", "status": "DONE", "detail": detail,
                                   "task": "Started exploring markets once your brief was ready"})
    run_id = runner.start(pid, "scan", f"{gid}-scan", {"generation_id": gid, "cities": markets.ORDER, "handoff": True})
    ranking.prepare(pid)  # a no-op once it has run for this identity and profile
    if not ranking.candidates(pid)["needs_home"]:
        runner.start(pid, "rank", f"{gid}-rank")  # the ranking row fills in while the cities are searched
    return {"generation_id": gid, "run_id": run_id}


def auto_start(pid: str) -> str | None:
    """Owner, 28 Oct 2026 (judges sat 46 s on a finished brief): Markets start by themselves once the brief is done and
    the identity is settled, the way "Explore markets" starts them: a proposed profile is confirmed as it stands, then
    the scan starts. Not when the founder is needed first: an unreadable site (B02), "Which is you?", a profile still
    being proposed or with nothing kept, or a goal that needs a home country. Only a project's first scan starts this
    way; a recorded demo replays only what the founder asks for. Called whenever one of those settles; never raises."""
    from . import identity
    if not config.AUTO_PLAN:
        return None
    try:
        with db._lock:
            if generations(pid) or db.q("SELECT 1 FROM projects WHERE id=? AND mode='replay'", pid):
                return None
            brief, ident, prof = db.latest("brief", pid), db.latest("identity", pid), db.latest("profile", pid)
            if (not brief or brand.read_summary(brief)["unreadable"] or not ident or ident["status"] != "decided"
                    or not identity.follows(prof, ident) or ranking.candidates(pid)["needs_home"]):
                return None
            if not prof["confirmed"]:
                if not any(s["included"] for s in prof["signals"]):
                    return None
                keep = {k: v for k, v in prof.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")}
                db.put_record("profile", pid, {**keep, "confirmed": True, "confirmed_by": "landfall"},
                              inputs=prof["inputs"], rid=prof["id"])
            return start_scan(pid, by="landfall")["run_id"]
    except Exception:  # a brand read, identity or profile run never fails because Markets couldn't start
        import traceback
        traceback.print_exc()
        return None


def retry_scan(pid: str, gid: str) -> str:
    gen = db.get_record(gid)
    failed = [c for c in markets.ORDER if gen["cities"].get(c, {}).get("status", "failed") == "failed"]  # or never reached
    if not failed:
        raise ValueError("No city failed in this scan.")
    n = db.q("SELECT COUNT(*) n FROM runs WHERE project_id=? AND kind='scan' AND json_extract(detail, '$.params.generation_id')=?",
             pid, gid)[0]["n"]
    db.event(pid, "founder", {"stage": "Markets", "task": "Retried failed cities", "detail": f"{len(failed)} cities"})
    # the handoff still runs only once per generation: it starts the strategist only if none has run for it
    return runner.start(pid, "scan", f"{gid}-retry-{n}", {"generation_id": gid, "cities": failed, "handoff": True})


# ---- the scan run ----
def _scan_city(run: Run, gen: dict, cid: str) -> dict:
    """One city, terminal: writes a CityScan and the generation's effective reference before the task is done."""
    rid = f"cs_{gen['id']}_{cid}"
    prior = db.get_record(rid)
    if prior and prior["scan_run_id"] == run.id:  # written before a crash, task not yet marked done
        return {"scan_id": rid, "version": prior["version"], "status": prior["status"]}
    city = markets.CITIES[cid]
    tries = db.q("SELECT attempts FROM tasks WHERE run_id=? AND task_key=?", run.id, f"city:{cid}")
    interrupted = max(0, (tries[0]["attempts"] if tries else 1) - 1)
    run.emit("progress", {"city_id": cid, "status": "searching"})
    params = markets.scan_params(city, gen["request_config"]["signals"])
    try:
        out = qloo.get("/v2/insights", params, run.cassette, "scan", run.project_id, run.id,
                       cache_ok=lambda o: markets.classify(o, city)["status"] in ("returned", "empty"))
    except BudgetExhausted as e:  # allowance denied: nothing dispatched
        out = {"status": 0, "error": f"operating allowance reached ({e})", "origin": "denied", "attempt_log": []}
    except (NoRecording, runner.Cancelled):
        raise  # a replay miss fails the run visibly; a stop is a stop
    except Exception as e:  # anything else fails this city only (review slice 3, blocker 1)
        out = {"status": 0, "error": f"unreadable answer ({type(e).__name__})", "origin": "live", "attempt_log": []}
    cls = markets.classify(out, city)
    origin = out.get("origin", "replay")
    attempts = len(out.get("attempt_log") or []) if origin == "live" else 0
    if cls["error"]:
        cls["error"] = {**cls["error"], "attempts": attempts, "dispatched": origin != "denied"}
    payload = {"city_id": cid, "scan_run_id": run.id, "generation_id": gen["id"], "status": cls["status"],
               "request": {k: str(v) for k, v in sorted(params.items())}, "places": cls["places"], "error": cls["error"],
               "origin": origin, "attempts": attempts, "interrupted": interrupted,
               "attempt_log": [{k: a.get(k) for k in ("n", "status", "error", "at")} for a in out.get("attempt_log") or []],
               "fetched_at": (out.get("cache") or {}).get("fetched_at") or db.now()}
    with db._lock:
        rec = db.put_record("city_scan", run.project_id, payload, inputs=gen["inputs"], rid=rid)
        _update_generation(gen["id"], lambda g: g["cities"].__setitem__(
            cid, {"scan_id": rid, "scan_version": rec["version"], "run_id": run.id, "status": cls["status"]}))
    finding = ""
    if cls["status"] in ("returned", "empty"):
        d = markets.digest(payload)
        finding = f"{d['returned_count']} places" + (f" · {markets.channel_line(d, 2)}" if d["returned_count"] else "")
    run.emit("progress", {"city_id": cid, "status": cls["status"], "finding": finding,
                          "reason": (cls["error"] or {}).get("reason")})
    if cls["status"] in ("failed", "unresolved"):
        brand._activity(run, f"Search {city['name']} in Qloo", "Qloo place search",
                        f"{cls['error']['reason']} · {attempts} attempt{'s' if attempts != 1 else ''}",
                        "FAILED" if cls["status"] == "failed" else "UNRESOLVED", role="Market scout", stage="Markets")
    return {"scan_id": rid, "version": rec["version"], "status": cls["status"]}


def scan_run(run: Run):
    gid = run.params["generation_id"]
    gen = db.get_record(gid)
    results = {}
    try:  # every exit, including a stop between the last city, close and handoff, closes the generation (closure)
        with ThreadPoolExecutor(max_workers=IN_FLIGHT) as pool:  # each city is still its own memoized step
            futures = {cid: pool.submit(run.step, f"city:{cid}", lambda cid=cid: _scan_city(run, gen, cid))
                       for cid in run.params["cities"]}
        for cid, f in futures.items():  # after all finish: a stop or replay miss is raised, never left running
            results[cid] = f.result()["status"]
        closed = run.step("close", lambda: _close(gid))
        counts = {s: sum(1 for x in results.values() if x == s) for s in TERMINAL}
        brand._activity(run, f"Search {len(results)} cities in Qloo", "Qloo place search",
                        " · ".join(f"{n} {s}" for s, n in counts.items() if n), role="Market scout", stage="Markets")
        if run.params.get("handoff"):
            run.step("handoff", lambda: _handoff(run, gid, closed["version"]))
    except BaseException:
        _close(gid)
        raise
    if counts["failed"]:
        return "partial", f"{counts['failed']} of {len(results)} cities failed; the others are kept. Retry the failed ones."
    return None


def _close(gid: str) -> dict:
    """complete only when every city has a terminal, non-failed result; otherwise partial (retryable)."""
    g = db.get_record(gid)
    done = all(g["cities"].get(c, {}).get("status") in TERMINAL for c in markets.ORDER)
    status = "complete" if done and not any(c["status"] == "failed" for c in g["cities"].values()) else "partial"
    if g["status"] != status:
        g = _update_generation(gid, lambda p: p.__setitem__("status", status))
    return {"version": g["version"], "status": status}


def _handoff(run: Run, gid: str, version: int) -> dict:
    """Memoized: the strategist starts automatically once per generation, at its first terminal state. Any earlier
    strategist run for this generation, even a failed one, means the automatic handoff happened: recovery is then the
    founder's explicit Try again or Update (closure C2)."""
    if db.q("SELECT 1 FROM records WHERE kind='assessment_set' AND project_id=? "
            "AND json_extract(payload, '$.generation.id')=?", run.project_id, gid) or _runs(run.project_id, "shortlist", gid) or \
            db.q("SELECT 1 FROM runs WHERE project_id=? AND kind='shortlist' AND status IN ('queued','running')", run.project_id):
        return {"started": None}
    return {"started": runner.start(run.project_id, "shortlist", f"gen-{gid}-v{version}",
                                    {"generation_id": gid, "generation_version": version, "reason": "first"})}


# ---- the Market strategist (market_strategist@2): it explains; the order is decided before it runs (R6) ----
SYSTEM = """You are the Market strategist on Landfall, an international launch team for small consumer brands.
The cities below, their groups and their order are already decided: one Qloo ranking ordered the founder's candidate
cities for the brand's signal, and the founder's goal broke near-ties. You explain each city from the evidence listed
under it. You never choose, add, drop or reorder cities, and you never mention a ranking, position, score or affinity:
Landfall states each city's position itself.

The evidence for each city is what Qloo returned in that city: up to 8 places with their kinds, the place mix of that
search, and counts of related-brand stores within 15 miles. That is all you know about the city.

For each city, by its id, write plain, factual sentences:
- why: one sentence saying what Qloo returned in this city for the brand's signal. Name one or two of its places and
  their kind; add a related-brand store count only when it is above zero. If no places are listed, use the
  related-brand stores, or say Qloo's place search returned nothing here. State it; don't speculate. In refs, list what the sentence uses: each place
  by its bracketed code (for example P2), each related brand by its name exactly as listed. Never write a code in
  the prose.
- different: one sentence on what this city's evidence has that the other listed cities' evidence doesn't: a kind of
  place, a named place, or related-brand stores here. Describe the thing itself ("an art center and an event venue",
  "seven Banana Republic stores within 15 miles"); never compare numbers between cities and never use superlatives
  ("the most", "the highest", "more than"). Each city's sentence must be different.
- could_change: one sentence naming a concrete check the team could make next about one named place here, fitting its
  kind (never ask a restaurant, café, bar or hotel whether it stocks brands): a shop or boutique, whether it stocks
  outside brands; a café, bakery, coffee or bagel shop, bar or
  restaurant, whether it hosts pop-ups; a
  hotel, venue or gallery, whether it hosts events or retail pop-ups; a park or outdoor place, whether community
  groups meet there.
- ways_in: one or two ways in, taken from this city's place kinds. Only shops and boutiques can be stockists; a
  café, restaurant, bar, coffee or bagel shop, hotel or venue never is. Shops or boutiques → stockists; cafés, bars or
  restaurants (including coffee, bagel and bakery shops) → pop-ups; venues, galleries or event spaces → events; gyms, parks or outdoor places → community;
  related-brand stores → retail. Write them as a short phrase, for example "stockists and pop-ups".
- lead: two plain sentences. For each group listed under GROUPS, name its first city first, with the evidence for it.
  There are no other groups.

Examples of the form (invented places, not evidence):
- why: "Qloo returned Harbour Goods, a gift shop, and Pier Coffee, a coffee shop, along with 3 Patagonia stores
  within 15 miles."
- different: "Harbour Goods is a gift shop, and Qloo returned a surf school here."
- could_change: "Check whether Harbour Goods would stock an outside brand."
- ways_in: "stockists and pop-ups"
Don't write filler such as "for the brand's signal" or "in this city".

Rules:
- Every sentence is complete and ends with a full stop.
- Only the evidence listed under that city. No general knowledge or reputation ("known for", "vibrant", "vibe",
  "scene", "culture"), and no adjectives the evidence doesn't support.
- Don't guess who goes to a place. The audiences describe people who like the brand's signal in general; never say
  they are at, near or drawn to a particular place or city.
- No hedged speculation ("could connect", "might resonate", "aligns with", "potentially").
- Numbers: only counts listed in that city's evidence.
- Copy place and brand names exactly as listed.
- Never compare counts, scores or popularity across cities. Never claim demand, sales, market size, audience size or
  market potential, or that anything is proven.
- Never name one city in another city's text."""
STANDIN_RULE = "\n- The signal is a set of stand-in brands with a similar following, because Qloo doesn't know this brand. Name them\n  as stand-ins where relevant, and never describe their audiences as the brand's own customers."
PROSE = ("why", "different", "could_change", "ways_in")


class CityProse(BaseModel):
    city_id: str = Field(description="The city's id exactly as listed, e.g. paris")
    why: str
    different: str
    could_change: str
    ways_in: str
    refs: list[str] = Field(description="Place ids and related-brand names from this city's own evidence used in why")


class StrategistOut(BaseModel):
    lead: str
    cities: list[CityProse]


def _home_claim(brief: dict) -> dict | None:
    for cid in brief["facts"] + brief["interpretation"]:
        c = db.get_record(cid)
        if (c.get("subject") or {}).get("key") == "home_market":
            return c
    return None


def home_of(brief: dict | None) -> dict:
    c = _home_claim(brief) if brief else None
    return markets.parse_home(c["value"] if c else None, c and c["id"],
                              "YOU_SAID" if c and c.get("replaces") else "INFERRED")


def ships_of(brief: dict | None):
    return markets.shipping_rule([db.get_record(i) for i in brief["facts"]] if brief else [])


# ---- the ranking and the decision (R1, R2) ----
def _active(pid: str, kind: str, but: str | None = None) -> bool:
    return bool(db.q("SELECT 1 FROM runs WHERE project_id=? AND kind=? AND status IN ('queued','running') AND id!=?",
                     pid, kind, but or ""))


def _wait_for(run: Run, kind: str, timeout: float = 180) -> None:
    """Wait (bounded) for another run of `kind`, so its provider work is reused rather than repeated. A predecessor
    still running at the bound stops this run visibly (Try again), never overlaps it."""
    end = time.monotonic() + timeout
    while _active(run.project_id, kind, run.id):
        run.check()
        if time.monotonic() >= end:
            raise RuntimeError(f"an earlier {kind} step is still running; try again when it finishes")
        run.cancel.wait(0.3)


def _rank(run: Run) -> dict:
    pid = run.project_id
    _wait_for(run, "prepare")
    _wait_for(run, "rank")
    ranking.presence_step(run, pid)  # each returns at once when its record already follows its inputs
    if ranking.brand_entity(pid) is None:
        ranking.standins_step(run, pid)
    return ranking.rank_step(run, pid)


def rank_run(run: Run):
    """Started with the scan, so the ranking row fills in while the cities are searched."""
    run.step("rank", lambda: _rank(run))


def purpose_keys(pid: str, gen: dict, positions: dict, purpose: str) -> tuple[dict, list]:
    """(keys, cities whose key is not known and counted 0)."""
    home = ranking.home_for(pid)
    keys, unknown = {}, []
    for cid in positions:
        k = 0
        if purpose == "stockists":
            ref = gen["cities"].get(cid)
            k = ranking.stockist_key(ref and db.get_record(ref["scan_id"], ref["scan_version"]))
        elif purpose == "test_demand":
            f = ranking.entry_facts(cid, home)
            k = ranking.test_demand_key(f)
            if None in (f["shared_language"], f["same_bloc"], f["overlap_hours"]):
                unknown.append(cid)
        if k is None:
            unknown.append(cid)
            k = 0
        keys[cid] = k
    return keys, unknown


def _decide(run: Run, gen: dict, ref: dict) -> dict:
    """The decision rule, with located checks when store coverage is capped or partial: a store found makes the city
    presence and the next ranked city takes its slot, from the same ranking (no new ranking call)."""
    pid = run.project_id
    rk = db.get_record(ref["id"], ref["version"])
    purpose = ranking.goal_of(pid)["purpose"]
    positions = rk["positions"]
    keys, unknown = purpose_keys(pid, gen, positions, purpose)
    pres = ranking.presence_of(pid)
    skip = ranking.located_out(pid)
    groups = ranking.decide(positions, rk["scope"], rk["home_country"], keys, skip)
    ent = ranking.brand_entity(pid)
    checked = {c["city_id"] for c in pres["located_checks"]}
    if ent and pres["store_search"]["status"] in ("capped", "partial"):
        for _ in range(2):  # the cities about to be recommended, then one round of replacements
            todo = [c["city_id"] for g in groups for c in g["cities"] if c["city_id"] not in checked]
            todo = todo[:ranking.CHECKS_PER_ROUND]
            if not todo:
                break
            found = ranking.located_checks(run, pid, todo, ent["name"])
            checked |= set(todo)
            new = {c["city_id"] for c in found if c["status"] == "store_found"}
            if not new:
                break
            skip = ranking.located_out(pid)
            groups = ranking.decide(positions, rk["scope"], rk["home_country"], keys, skip)
    checks = {c["city_id"]: c["status"] for c in ranking.presence_of(pid)["located_checks"]}
    for g in groups:
        for c in g["cities"]:
            c["located_check"] = checks.get(c["city_id"])
    return {"ranking": {"id": rk["id"], "version": rk["version"], "state": rk["state"]}, "groups": groups,
            "keys": keys, "key_unknown": unknown, "skipped": sorted(skip), "purpose": purpose,
            "coverage": pres["store_search"]["status"],
            "recommended": [c["city_id"] for g in groups for c in g["cities"]]}


def strategist_input(pid: str, gen: dict, dec: dict, cmp_ref: dict) -> dict:
    """The immutable snapshot the strategist is shown: the decided cities in final order with typed evidence (places
    from each city's pinned CityScan, related-brand stores, entry facts), the goal and the audience names. No affinity."""
    brief, fi = db.latest("brief", pid), db.latest("founder_input", pid)
    rk = db.get_record(dec["ranking"]["id"], dec["ranking"]["version"])
    cmp = db.get_record(cmp_ref["id"], cmp_ref["version"])
    comps = {c["city_id"]: c["brands"] for c in cmp["cities"]}
    home = ranking.home_for(pid)
    ap = db.latest("audience_profile", pid)
    used = [a for a in ap["audiences"] if a["uuid"] not in ap["removed"]][:8] if ap else []
    pres, si = db.latest("current_presence", pid), db.latest("standins", pid)
    cities = []
    for g in dec["groups"]:
        for c in g["cities"]:
            cid, ref = c["city_id"], gen["cities"].get(c["city_id"])
            scan = db.get_record(ref["scan_id"], ref["scan_version"]) if ref else None
            d = markets.digest(scan) if scan and scan["status"] in ("returned", "empty") else None
            f = ranking.entry_facts(cid, home)
            city = markets.CITIES[cid]
            cities.append({**c, "group": g["key"], "name": city["name"], "country": city["country"],
                           "scan_status": ref["status"] if ref else "not_scanned",
                           "scan": ref and {"id": ref["scan_id"], "version": ref["scan_version"]},
                           "places": d["top_places"] if d else [], "channels": d["channels"] if d else None,
                           "comparables": comps.get(cid, []), "entry": {**f, "chip": ranking.entry_chip(f)}})
    ref = lambda r: r and {"id": r["id"], "version": r["version"]}  # noqa: E731
    return {"brand_name": brief["brand_name"], "signal": rk["signal"], "n_candidates": len(rk["candidates"]["city_ids"]),
            "goal": {"scope": rk["scope"], "purpose": dec["purpose"]},
            "audience_names": [a["name"] for a in used],
            "audience_profile": ap and {**ref(ap), "keys": [a["uuid"] for a in used]},
            "cities": cities,
            "groups": [{**{k: g[k] for k in ("key", "label", "cap", "available")},
                        "city_ids": [c["city_id"] for c in g["cities"]]} for g in dec["groups"]],
            "ranking": dec["ranking"], "generation": {"id": gen["id"], "version": gen["version"]},
            "home": {"value": home["text"], "country": home["country"], "claim_id": home["claim_id"],
                     "provenance": home["provenance"]},
            "key_unknown": dec["key_unknown"], "coverage": dec["coverage"],
            "presence": ref(pres), "standins": ref(si), "comparables": cmp_ref,
            "prompt_version": STRATEGIST_VERSION, "reference": ranking.REFERENCE,
            "refs": {"brief": ref(brief), "founder_input": ref(fi), "profile": gen["profile"]}}


PURPOSE_LINE = {"presence": "your goal", "stockists": "stockists", "test_demand": "testing demand"}


def prompt_of(inp: dict) -> str:
    """Deterministic text: no ids of ours, no dates, no positions or scores."""
    sig = inp["signal"]
    lines = []
    for i, c in enumerate(inp["cities"], 1):
        if c["places"]:
            places = "; ".join(f"[P{j}] {p['name']}" + (f" ({', '.join(p['genres'])})" if p["genres"] else "")
                               for j, p in enumerate(c["places"], 1))
        else:
            places = ("Qloo returned no related places here" if c["scan_status"] == "empty"
                      else "Qloo's place search failed here, so there are no places")
        comps = "; ".join(f"{b['name']} {'not checked' if b['state'] != 'ok' else _stores_text(b)}"
                          for b in c["comparables"]) or "none checked"
        lines.append(f"{c['city_id']} | {c['name']}, {markets.COUNTRY_NAME.get(c['country'], c['country'])} | "
                     f"{ranking.GROUP_LABELS[c['group']]}, order {i}\n  places Qloo returned within 15 km: {places}\n"
                     + (f"  place mix in this search: {markets.channel_line({'channels': c['channels']}, 4)}\n"
                        if c["channels"] and any(c["channels"].values()) else "")
                     + f"  related-brand stores within 15 miles: {comps}\n  entry: {ranking.ENTRY_CHIPS[c['entry']['chip']].lower()}")
    g = inp["goal"]
    names = {c["city_id"]: c["name"] for c in inp["cities"]}
    groups = " · ".join(f"{gr['label']}: {', '.join(names[i] for i in gr['city_ids']) or 'none'}" for gr in inp["groups"])
    return (f"Brand: {inp['brand_name']}\nQLOO SIGNAL: {sig['label'] if sig else 'none'}\nGROUPS: {groups}\n"
            f"GOAL: expand {ranking.SCOPE_WORDS[g['scope']]} to {ranking.PURPOSE_WORDS[g['purpose']]}\n"
            f"AUDIENCES QLOO RELATES TO THIS SIGNAL (cultural relationships, not customers): "
            f"{', '.join(inp['audience_names']) or 'not known'}\n\n"
            f"CITIES, in the order already decided (do not reorder):\n" + "\n".join(lines) + "\n\nWrite the explanations.")


def call_strategist(run: Run, inp: dict) -> dict:
    """One structured Nova call (no tools), bounded at the Converse boundary."""
    sink = brand._usage_sink(run)
    calls = [0]
    agent_ref: list[Agent] = []

    def per_turn(u: dict):
        sink(u)
        calls[0] += 1
        if calls[0] >= MAX_MODEL_CALLS:
            agent_ref[0].cancel()

    system = SYSTEM + (STANDIN_RULE if (inp["signal"] or {}).get("kind") == "standins" else "")
    agent = Agent(model=make_model(run.cassette, "shortlist", per_turn, max_tokens=8000), system_prompt=system,
                  callback_handler=None)
    agent_ref.append(agent)
    try:
        res = agent(prompt_of(inp), structured_output_model=StrategistOut, cancel_signal=run.cancel)
    except (NoRecording, BudgetExhausted):
        raise
    except Exception as e:
        raise RuntimeError(f"the Market strategist returned no explanations ({type(e).__name__})") from None
    run.check()
    if res.structured_output is None:
        raise RuntimeError("the Market strategist returned no explanations")
    brand._activity(run, "Explain the recommendations", "Market strategist",
                    f"{len(res.structured_output.cities)} cities explained · {calls[0]} model call"
                    + ("s" if calls[0] != 1 else ""), role="Market strategist", stage="Markets")
    return res.structured_output.model_dump()


def _strategist(run: Run, inp: dict) -> dict:
    """No cities, no model call. A failed model is not a failed shortlist: every text falls back to Landfall's own."""
    if not inp["cities"]:
        return {"out": None, "error": None}
    try:
        return {"out": call_strategist(run, inp), "error": None}
    except (NoRecording, runner.Cancelled):
        raise
    except BudgetExhausted as e:
        return {"out": None, "error": f"operating allowance reached ({e})"}
    except Exception as e:
        return {"out": None, "error": str(e)[:200]}


# ---- the validator (R6): prose is joined by id; the order, groups and ids never come from the model ----
NUM_WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                         "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
NUM_WORDS.update(thirty=30, forty=40, fifty=50)
NUM = re.compile(r"\b(\d[\d,]*(?:\.\d+)?|" + "|".join(NUM_WORDS) + r")\b", re.I)
CODE = re.compile(r"\[?\bP\d+\b\]?|\b[0-9A-F]{8}-[0-9A-F]{4}-", re.I)
PROXY = re.compile(r"\byour (own )?(customers|audiences?|fans|buyers|shoppers)\b|\b(customers|buyers) of\b|"
                   r"\bcustomer (data|base)\b", re.I)
WAYS = {"retail": "retail and stockists", "venues_culture": "community and events", "fitness_outdoors": "community and pop-ups",
        "food_drink": "pop-ups", "stays": "pop-ups"}


def _stores_text(b: dict) -> str:
    return f"at least {b['stores']}" if b["capped"] else str(b["stores"])


def not_distinct(text: str, c: dict, inp: dict) -> str | None:
    """M3: a `different` sentence fails when every kind of place it names is also on another recommended card (LIVE
    L35-B v2: Auckland's "a bagel shop" beside Brisbane's bagel shop; Perth's "a park" beside Sydney's park). The
    city's own place names are left out first, so naming "Shelley Beach Park" is not naming "a park"."""
    plain = text
    for p in c["places"]:
        plain = plain.replace(p["name"], " ")
    folded = f" {markets.fold(plain)} "
    kinds = {markets.fold(g) for x in inp["cities"] for p in x["places"] for g in p["genres"]}
    named = [k for k in kinds if k and f" {k} " in folded]
    others = {markets.fold(g): x["name"] for x in inp["cities"] if x["city_id"] != c["city_id"] for p in x["places"]
              for g in p["genres"]}
    if named and all(k in others for k in named):
        return f"not distinctive: {others[named[0]]} also has a {named[0]}"
    return None


def count_fault(text: str, cities: list[dict], cited_places: int | None = None) -> str | None:
    """Every number, in digits or words, is bound to what it claims (review slice 3.5, finding 5):
    - beside a related brand's name ("5 J.Crew stores", "Aesop's 14 stores") → that brand's count in one of `cities`,
      and a capped count only as "at least 50";
    - before miles or km → the 15-mile radius;
    - otherwise → a count of one of these cities' own place mix, or how many places the sentence cites."""
    brands = [b for c in cities for b in c["comparables"] if b["state"] == "ok"]
    mix = {n for c in cities for n in (c["channels"] or {}).values()} | ({cited_places} if cited_places else set())
    for m in NUM.finditer(text or ""):
        tok = m.group(1)
        n = NUM_WORDS.get(tok.lower()) if not tok[0].isdigit() else float(tok.replace(",", ""))
        before, after = text[max(0, m.start() - 30):m.start()], text[m.end():m.end() + 40]
        named = [b for b in brands if re.match(rf"\s+(?:\S+\s+)?{re.escape(b['name'])}\b", after, re.I)
                 or re.search(rf"\b{re.escape(b['name'])}(?:'s)?(?:\s+\S+)?\s+$", before, re.I)]
        if named:
            ok = [b for b in named if b["stores"] == n and (not b["capped"] or re.search(r"at least\s+$", before, re.I))]
            if not ok:
                return f"a count that doesn't match {named[0]['name']}'s stores in its evidence"
        elif re.match(r"\s*(miles?|mi\b|km|kilomet)", after, re.I):
            if n != 15 or not re.match(r"\s*(miles?|mi\b)", after, re.I):
                return "a distance other than the 15-mile search radius"
        elif n not in mix:
            return "a number that isn't a count in its cited evidence"
    return None


def fallback(field: str, c: dict) -> str:
    """Deterministic text from the city's typed references only; it says what is unknown."""
    name = c["name"]
    if field == "why":
        parts = []
        ok = [b for b in c["comparables"] if b["state"] == "ok" and b["stores"]]
        if ok:
            parts.append("Qloo lists " + ranking.join([f"{_stores_text(b)} {b['name']} store{'s' if b['stores'] != 1 else ''}"
                                                       for b in ok]) + f" within 15 miles of {name}.")
        if c["places"]:
            parts.append(f"Qloo's place search returned {ranking.join([p['name'] for p in c['places'][:3]])} here.")
        elif c["scan_status"] == "empty":
            parts.append("Qloo's place search returned no related places here.")
        else:
            parts.append("Qloo's place search failed here; this rests on the ranking alone.")
        if any(b["state"] != "ok" for b in c["comparables"]):
            parts.append("Some related-brand stores were not checked.")
        return " ".join(parts)
    if field == "different":
        if c["channels"] and any(c["channels"].values()):
            return f"In {name}, the places Qloo returned are {markets.channel_line({'channels': c['channels']}, 3)}."
        return f"In {name}, entry reads {ranking.ENTRY_CHIPS[c['entry']['chip']].lower()}; Qloo's places are not known."
    if field == "could_change":
        return "Whether the places and stores Qloo returned here exist as listed and would work with you."
    ways = [WAYS[k] for k, n in sorted((c["channels"] or {}).items(), key=lambda kv: -kv[1]) if n and k in WAYS]
    return ", ".join(dict.fromkeys(ways)) if ways else "not known yet"


def resolve_ref(ref, c: dict) -> str | None:
    """A typed reference of this city only: a place id, or the exact name of one of its places or checked related
    brands (a trailing count such as "J.Crew 5" is ignored). Returns the place id or the brand name."""
    if not isinstance(ref, str):
        return None
    code = re.fullmatch(r"\[?P(\d+)\]?", ref.strip())
    if code:
        n = int(code.group(1))
        return c["places"][n - 1]["qloo_id"] if 1 <= n <= len(c["places"]) else None
    key = markets.fold(re.sub(r"\s+\d+$", "", ref.strip()))
    for p in c["places"]:
        if ref.strip().split(" ")[0] == p["qloo_id"] or key == markets.fold(p["name"]):
            return p["qloo_id"]
    return next((b["name"] for b in c["comparables"] if b["state"] == "ok" and key == markets.fold(b["name"])), None)


def _fault(text, c: dict, inp: dict) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return "missing text"
    if CODE.search(text):
        return "shows a reference code"
    if re.search(r"\\u[0-9a-fA-F]{4}", text):
        return "garbled text"
    if why := markets.guard(text):
        return why
    if (inp["signal"] or {}).get("kind") == "standins" and PROXY.search(text):
        return "describes the stand-ins' audiences as your customers"
    own = text  # a city named inside one of this city's own place names ("Copenhagen Coffee Lab" in Lisbon) is not a city
    for p in c.get("places") or []:
        own = own.replace(p["name"], " ")
    if set(markets.names_in(own, markets.ORDER)) - {c["city_id"]}:
        return "names another city"
    return None


def tops_of(inp: dict) -> list[str]:
    return [g["city_ids"][0] for g in inp["groups"] if g["city_ids"]]


def _tops_first(named: list[str], inp: dict) -> bool:
    """The lead names every group's top, and in each group the first city it names is that group's top (LIVE L35-A:
    "Helsinki is the first city in the Abroad group" when Montreal was)."""
    for g in inp["groups"]:
        mine = [c for c in named if c in g["city_ids"]]
        if g["city_ids"] and (not mine or mine[0] != g["city_ids"][0]):
            return False
    return True


def deterministic_lead(inp: dict) -> str:
    names = {c["city_id"]: c["name"] for c in inp["cities"]}
    tops = [(g, g["city_ids"][0]) for g in inp["groups"] if g["city_ids"]]
    if not tops:
        return ""
    tail = f" Each card states where it came in one Qloo ranking of your {inp['n_candidates']} candidate cities, and why."
    if len(inp["groups"]) == 1:
        return f"{names[tops[0][1]]} is the top recommendation for {inp['signal']['label']}." + tail
    said = {g["key"]: names[c] for g, c in tops}
    home = f"At home, {said['home']} is the top recommendation" if "home" in said else "At home, no city was ranked"
    abroad = f"abroad, {said['abroad']}" if "abroad" in said else "abroad, no city was ranked"
    return f"{home}; {abroad}." + tail


def explain(out: dict | None, inp: dict) -> dict:
    """Per city id, in the decided order: each field is the model's text if it passes, else deterministic text. A why
    must cite one of its own places or related-brand stores; every number must be a count from its cited evidence."""
    replies: dict = {}
    for c in (out or {}).get("cities") or []:  # the first reply for a city wins (LIVE: a city repeated without refs)
        if isinstance(c, dict):
            replies.setdefault(c.get("city_id"), c)
    cards, faults, seen = [], [], set()
    for c in inp["cities"]:
        m = replies.get(c["city_id"]) or {}
        refs = [x for r in m.get("refs") or [] if (x := resolve_ref(r, c))]
        n_places = sum(r not in {b["name"] for b in c["comparables"]} for r in refs)
        prose, source = {}, {}
        for k in PROSE:
            text = m.get(k)
            why = "no reply for this city" if not m else _fault(text, c, inp)
            if not why and k == "why" and not refs:
                why = "cites none of this city's places or related-brand stores"
            if not why:
                why = count_fault(text, [c], n_places)
            if not why and k == "different" and markets.fold(text) in seen:
                why = "repeats another card"
            if not why and k == "different":
                why = not_distinct(text, c, inp)
            if why:
                if m:
                    faults.append({"city_id": c["city_id"], "field": k, "reason": why})
                prose[k], source[k] = fallback(k, c), "DETERMINISTIC"
            else:
                prose[k], source[k] = text, "MODEL"
            if k == "different":
                seen.add(markets.fold(prose[k]))
        cards.append({"city_id": c["city_id"], **prose, "prose_source": source,
                      "refs": refs if source["why"] == "MODEL" else []})
    ids = {c["city_id"] for c in inp["cities"]}
    lead, src = (out or {}).get("lead") or "", "MODEL"
    plain = lead
    for p in (p for c in inp["cities"] for p in c["places"]):  # "Copenhagen Coffee Lab" names a place, not a city
        plain = plain.replace(p["name"], " ")
    named = markets.names_in(plain, markets.ORDER)
    by_id = {c["city_id"]: c for c in inp["cities"]}
    lead_count = next((f for sentence in re.split(r"(?<=[.;])\s+", lead) if (f := count_fault(
        sentence, [by_id[i] for i in markets.names_in(sentence, markets.ORDER) if i in by_id] or inp["cities"]))), None)
    if not ids:
        lead, src = "", "NONE"
    elif (_fault(lead, {"city_id": None}, inp) not in (None, "names another city") or not named
          or set(named) - ids or not _tops_first(named, inp) or lead_count):
        lead, src = deterministic_lead(inp), "DETERMINISTIC"
    return {"cards": cards, "faults": faults, "lead": {"text": lead, "source": src},
            "ignored": sorted(k for k in replies if k not in ids)}


def moved_line(c: dict, purpose: str) -> str | None:
    if not c["moved_by_purpose"]:
        return None
    pos = ranking.ordinal(c["qloo_position"])
    if c["final_position"] > c["group_rank"]:
        return f"Moved down within its block of 4 for your goal (Qloo's position: {pos})."
    why = {"stockists": "Qloo returned 3 or more shops and boutiques here",
           "test_demand": "a shared language, the same trade bloc or working hours that overlap"}.get(purpose, "your goal")
    return f"Moved up for your goal (Qloo's position: {pos}): {why}."


def _store(run: Run, inp: dict, strat: dict, pinned: dict | None = None) -> dict:
    """Publishes a new AssessmentSet and carries the founder's selections over. If a founder input changed during the
    run, the result is kept as history but fenced: no consumed-input claims, no new Shortlist, not shown as current
    (review slice 3.5, finding 4)."""
    ex = explain(strat["out"], inp)
    label = (inp["signal"] or {}).get("label", "")
    prose = {c["city_id"]: c for c in ex["cards"]}
    assessments = [{**{k: c[k] for k in ("city_id", "group", "qloo_position", "group_rank", "final_position",
                                         "moved_by_purpose", "role", "purpose_key", "located_check", "scan_status")},
                    **prose[c["city_id"]],
                    "position_line": ranking.position_line(inp["n_candidates"], c["name"], c["qloo_position"], label),
                    "moved_line": moved_line(c, inp["goal"]["purpose"])} for c in inp["cities"]]
    with db._lock:
        changed = fenced_by(run.project_id, pinned) if pinned else []
        aset = db.put_record("assessment_set", run.project_id, {
            "generation": inp["generation"], "input": inp, "output": strat["out"], "model_error": strat["error"],
            "run_id": run.id, "reason": run.params.get("reason"), "prompt_version": STRATEGIST_VERSION,
            "ranking": inp["ranking"], "groups": inp["groups"], "assessments": assessments, "featured": tops_of(inp),
            "lead": ex["lead"], "faults": ex["faults"], "ignored": ex["ignored"],
            "fenced": {"changed": changed, "pinned": pinned} if changed else None},
            inputs=[] if changed else assessment_inputs(inp), rid=f"as_{run.project_id}")
        sl = db.latest("shortlist", run.project_id) if changed else _carry_over(run.project_id, aset)
    if changed:
        brand._activity(run, "Check the explanations", "validator", "your change arrived during this run: kept as "
                        "history, not applied · Update", "SKIPPED", role="Market strategist", stage="Markets")
        run.emit("shortlist", {"assessment_set": aset["version"], "fenced": changed})
        return {"assessment_set": aset["version"], "fenced": changed}
    n_det = sum(1 for a in assessments for s in a["prose_source"].values() if s == "DETERMINISTIC")
    brand._activity(run, "Check the explanations", "validator",
                    f"{len(assessments)} recommended · {len(ex['faults'])} texts replaced · {n_det} deterministic"
                    + (f" · model: {strat['error']}" if strat["error"] else ""), role="Market strategist", stage="Markets")
    run.emit("shortlist", {"version": sl["version"], "assessment_set": aset["version"]})
    return {"assessment_set": aset["version"], "shortlist": sl["version"]}


def assessment_inputs(inp: dict) -> list[dict]:
    r = inp["refs"]
    out = [{**r["brief"], "keys": [inp["home"]["claim_id"]] if inp["home"]["claim_id"] else []},
           {**r["profile"], "keys": ["signals"]},
           {**r["founder_input"], "keys": ["scope", "purpose", "leave_out_presence", "home_country", "exclude"]},
           {**inp["generation"], "keys": []},
           {"id": inp["ranking"]["id"], "version": inp["ranking"]["version"], "keys": ["positions"]}]
    out += [{**inp[k], "keys": []} for k in ("presence", "standins") if inp.get(k)]
    if inp["audience_profile"]:
        out.append(inp["audience_profile"])
    return out


def _carry_over(pid: str, aset: dict) -> dict:
    """A new Shortlist version for a new AssessmentSet: a still-recommended city the founder unticked stays unticked,
    and founder-added cities stay added."""
    prev = db.latest("shortlist", pid)
    old = {c["city_id"]: c for c in (prev or {}).get("cities", [])}
    bh = (prev or {}).get("beachhead")
    bh_city = bh and bh["city_id"]
    cities = [{"city_id": a["city_id"], "included": old.get(a["city_id"], {}).get("included", True) or a["city_id"] == bh_city,
               "added_by_founder": old.get(a["city_id"], {}).get("added_by_founder", False),
               "featured": a["city_id"] in aset["featured"]} for a in aset["assessments"]]
    kept = {c["city_id"] for c in cities}
    cities += [{**c, "featured": False} for c in old.values() if c["added_by_founder"] and c["city_id"] not in kept]
    if bh_city and bh_city not in {c["city_id"] for c in cities}:  # P65-R7: the beachhead survives every writer
        cities.append({"city_id": bh_city, "included": True, "added_by_founder": False, "featured": False,
                       "kept_as_beachhead": True})
    return db.put_record("shortlist", pid, {"assessment_set": {"id": aset["id"], "version": aset["version"]},
                                            "cities": cities, "cap": markets.CAP, "beachhead": bh}, rid=f"sl_{pid}")


def founder_pins(pid: str) -> dict:
    """The founder-owned inputs a run consumes, pinned at its start. The run's own writes (store search, located
    checks, a first stand-in pick, an audience refresh, the ranking) are not founder decisions and are left out."""
    fi, brief = db.latest("founder_input", pid), db.latest("brief", pid)
    si, ap = db.latest("standins", pid), db.latest("audience_profile", pid)
    return {"founder_input": fi and fi["version"], "brief": brief and brief["version"],
            "presence_overrides": ranking._overrides(db.latest("current_presence", pid)),
            "standins": [b["qloo_id"] for b in si["brands"]] if si and si["status"] != "failed" else None,
            "audience_removed": sorted((ap or {}).get("removed", []))}


def fenced_by(pid: str, pinned: dict) -> list[str]:
    """Founder inputs that changed after the run pinned them (stand-ins only when the founder had chosen some)."""
    now = founder_pins(pid)
    return [k for k in pinned if pinned[k] != now[k] and not (k == "standins" and pinned[k] is None)]


def shortlist_run(run: Run):
    pid = run.project_id
    gen = db.get_record(run.params["generation_id"], run.params["generation_version"])
    pinned = run.step("pin", lambda: founder_pins(pid))
    rk = run.step("rank", lambda: _rank(run))
    run.step("audience", lambda: ranking.audience_step(run, pid))  # refreshed only when the signal changed or failed
    dec = run.step("decide", lambda: _decide(run, gen, rk))
    from . import autoplan  # owner, 28 Oct 2026: build the top three cities' plans while the recommendation is written
    run.step("prewarm", lambda: autoplan.prewarm(pid, dec["recommended"]))
    cmp = run.step("comparables", lambda: ranking.comparables_step(run, pid, dec["recommended"]))
    inp = run.step("input", lambda: strategist_input(pid, gen, dec, cmp))
    strat = run.step("strategist", lambda: _strategist(run, inp))
    run.step("assemble", lambda: _store(run, inp, strat, pinned))


def start_shortlist(pid: str, why: Literal["retry", "update", "retry_ranking"]) -> str:
    """retry: after a failed run. update: the current generation, re-ranking only when the ranking's inputs changed.
    retry_ranking: re-rank even a partial ranking. All carry the founder's selections over; none scans."""
    if _active(pid, "shortlist"):
        raise ValueError("The recommendations are already being written.")
    prof = current_profile(pid)
    gen = generation_for(pid, prof) if prof else None
    if not gen or gen["status"] == "running":
        raise ValueError("There is no finished scan for your current profile.")
    n = db.q("SELECT COUNT(*) n FROM runs WHERE project_id=? AND kind='shortlist'", pid)[0]["n"]
    db.event(pid, "founder", {"stage": "Markets", "task": {"update": "Updated the recommendations", "retry": "Retried the "
                              "recommendations", "retry_ranking": "Retried the ranking"}[why], "detail": "no place scan"})
    return runner.start(pid, "shortlist", f"gen-{gen['id']}-v{gen['version']}-{why}-{n}",
                        {"generation_id": gen["id"], "generation_version": gen["version"], "reason": why,
                         "rerank": why == "retry_ranking"})


_UNSET = object()


def edit_shortlist(pid: str, base_version: int, include: list[str], exclude: list[str], add: list[str],
                   beachhead=_UNSET) -> dict:
    """Shortlist v+1 from the founder's ticks, additions and beachhead (6.5 M2). Runs no research; an addition never
    becomes a recommendation. The beachhead is added and included if needed, because research requires that."""
    with db._lock:
        sl = db.latest("shortlist", pid)
        if (sl["version"] if sl else 0) != base_version:
            raise PermissionError(f"The shortlist changed (now v{sl['version'] if sl else 0}). Reload and try again.")
        prof = current_profile(pid)
        gen = generation_for(pid, prof) if prof else None
        if not gen:
            raise ValueError("There is no scan for your current profile.")
        cities = [dict(c) for c in (sl or {}).get("cities", [])]
        have = {c["city_id"] for c in cities}
        bh = (sl or {}).get("beachhead")
        for cid in include + exclude + add + ([beachhead] if isinstance(beachhead, str) else []):
            if cid not in markets.CITIES:
                raise LookupError(f"{cid} isn't in the catalog.")
        if beachhead is not _UNSET:
            fi = db.latest("founder_input", pid)
            bh = beachhead and {"city_id": beachhead, "at": db.now(), "by": "founder",
                                "chosen_under": {"assessment_set": (sl or {}).get("assessment_set"),
                                                 "founder_input_version": fi["version"]}}
            if beachhead and beachhead not in have:
                cities.append({"city_id": beachhead, "included": True, "added_by_founder": True, "featured": False})
                have.add(beachhead)
        if bh and bh["city_id"] in exclude:
            raise PermissionError(f"{markets.CITIES[bh['city_id']]['name']} is your beachhead. Choose another first.")
        for cid in add:
            if cid not in have and cid in gen["cities"]:
                cities.append({"city_id": cid, "included": True, "added_by_founder": True, "featured": False})
        for c in cities:
            if c["city_id"] in include or c["city_id"] in add or (bh and c["city_id"] == bh["city_id"]):
                c["included"] = True
            if c["city_id"] in exclude:
                c["included"] = False
        rec = db.put_record("shortlist", pid, {"assessment_set": (sl or {}).get("assessment_set"), "cities": cities,
                                               "cap": markets.CAP, "beachhead": bh}, rid=f"sl_{pid}")
    names = lambda ids: ", ".join(markets.CITIES[i]["name"] for i in ids)  # noqa: E731
    detail = " · ".join(filter(None, [exclude and f"unticked {names(exclude)}", include and f"ticked {names(include)}",
                                      add and f"added {names(add)}",
                                      beachhead is not _UNSET and (f"beachhead: {names([beachhead])}" if beachhead
                                                                   else "cleared the beachhead")]))
    db.event(pid, "founder", {"stage": "Markets", "task": "Adjusted the shortlist", "detail": detail, "shortlist_version": rec["version"]})
    return rec


def beachhead_view(pid: str) -> dict | None:
    """The founder's beachhead from the latest Shortlist, whatever generation it belongs to (P65-R7), with the notes
    that say when the recommendations have moved on since it was chosen."""
    sl = db.latest("shortlist", pid)
    bh = (sl or {}).get("beachhead")
    if not bh:
        return None
    city = markets.CITIES[bh["city_id"]]
    aset = db.latest("assessment_set", pid)
    under = bh["chosen_under"]
    recommended = bool(aset) and bh["city_id"] in {a["city_id"] for a in aset["assessments"]}
    notes = []
    if aset and (under.get("assessment_set") or {}).get("version") != aset["version"]:
        notes.append(f"Chosen under goal v{under['founder_input_version']}")
    if aset and not recommended:
        notes.append("No longer recommended · your beachhead")
    return {**bh, "name": city["name"], "country": city["country"], "recommended": recommended, "notes": notes,
            "shortlist_version": sl["version"]}


def research_status(pid: str) -> dict[str, str]:
    """Each city's latest CityResearch status (6.5 M4), for the all-cities browser."""
    rows = db.q("SELECT city_id, status FROM research_cities r WHERE project_id=? AND version=(SELECT MAX(version) FROM "
                "research_cities WHERE project_id=r.project_id AND city_id=r.city_id)", pid)
    return {r["city_id"]: r["status"] for r in rows}


# ---- MarketsView (records → screen view; never stored) ----
FOUND_NOUNS = {"retail": ("shop", "shops"), "popup": ("pop-up venue", "pop-up venues"),
               "community": ("community group", "community groups"), "creator": ("creator", "creators")}


def _reachable(c: dict) -> bool:
    return ((c.get("axes") or {}).get("operational") or {}).get("contact_route") not in (None, "UNKNOWN", "NA")


def found_of(pid: str, cid: str, research: str | None, busy: bool, queued: bool) -> dict:
    """Owner, 28 Oct 2026: a city card says what the city's investigations kept, in plain words ("1 pop-up venue with a
    contact · a creator lead"), never Qloo's top places, which are not partners. Per way in: its KEPT candidates, else
    its PENDING ones as leads; a contact counts only when there is a route to it. Digital is a channel, not a partner."""
    parts, investigated = [], False
    for method, (one, many) in FOUND_NOUNS.items() if research else ():
        d = db.get_record(f"dos_{pid}_{cid}_{method}")
        if not d or d.get("status") not in ("complete", "partial"):
            continue
        investigated = True
        cands = d.get("candidates") or []
        kept = [c for c in cands if c.get("decision") == "KEPT"]
        cs = kept or [c for c in cands if c.get("decision") == "PENDING"]  # leads only where nothing is kept
        if not cs:
            continue
        n, reach = len(cs), sum(map(_reachable, cs))
        text = f"{n} {one if n == 1 else many}" if kept else f"a {one} lead" if n == 1 else f"{n} {one} leads"
        parts.append(text + ("" if not reach else " with a contact" if reach == n else f", {reach} with a contact"))
    busy = busy or research in ("queued", "running")
    if parts:
        return {"state": "found", "line": " · ".join(parts) + (" · still researching" if busy else "")}
    if busy:
        return {"state": "researching", "line": "Researching now…"}
    if queued:
        return {"state": "queued", "line": "Queued for research"}
    if investigated:
        return {"state": "none", "line": "Investigated: nothing kept yet"}
    return {"state": "not_started", "line": "Researched when you build a plan"}


def _run_view(r) -> dict | None:
    if not r:
        return None
    d = json.loads(r["detail"])
    return {"id": r["id"], "status": r["status"], "message": d.get("message", ""), "params": d["params"]}


def _runs(pid: str, kind: str, gid: str) -> list:
    return db.q("SELECT * FROM runs WHERE project_id=? AND kind=? AND json_extract(detail, '$.params.generation_id')=? "
                "ORDER BY started_at, rowid", pid, kind, gid)


def _latest_run(pid: str, kind: str):
    r = db.q("SELECT * FROM runs WHERE project_id=? AND kind=? ORDER BY started_at DESC, rowid DESC LIMIT 1", pid, kind)
    return r[0] if r else None


DEFERRED_LINES = {
    "ALREADY_HERE": "You already have a presence here, so it's left out of the ranking.",
    "OUTSIDE_GOAL": "Outside the goal you set on the first page.",
    "LEFT_OUT_BY_YOU": "You asked us to leave this out.",
    "omitted": "Qloo's ranking returned no relationship for this city. Unknown, not rejected.",
    "beyond_failed_page": "This city fell on a page of Qloo's ranking that failed. Retry the ranking.",
    "unmapped": "Qloo has no destination for this city, so it can't be ranked.",
}


def deferred_of(pid: str, aset: dict | None, cand: dict, rk: dict | None) -> dict[str, dict]:
    """Every catalog city not recommended, with its reason (C: recomputed from current records)."""
    rec = {a["city_id"] for a in (aset or {}).get("assessments", [])}
    located = ranking.located_out(pid, cand)
    positions = (rk or {}).get("positions", {})
    caps = {g["key"]: g["cap"] for g in (aset or {}).get("groups", [])}
    out = {}
    for cid in markets.ORDER:
        if cid in rec:
            continue
        r = cand["excluded"].get(cid)
        if cid in located:
            out[cid] = {"reason": "ALREADY_HERE", "line": "A Qloo store search near this city found one of your stores."}
        elif r == "NOT_RANKED":
            out[cid] = {"reason": "NOT_RANKED", "line": DEFERRED_LINES["unmapped"]}
        elif r:
            out[cid] = {"reason": r, "line": DEFERRED_LINES[r]}
        elif cid in positions:
            n = len(rk["candidates"]["city_ids"])
            cap = caps.get("home" if rk.get("home_country") == markets.CITIES[cid]["country"] and "home" in caps else
                           "abroad" if "abroad" in caps else "all", markets.CAP)
            out[cid] = {"reason": "NOT_PRIORITISED", "line": f"Qloo ranks it #{positions[cid]} of {n} candidate cities; "
                        f"not among the first {cap} for your goal."}
        elif rk and cid in rk.get("unranked", {}):
            out[cid] = {"reason": "NOT_RANKED", "line": DEFERRED_LINES.get(rk["unranked"][cid], "")}
        elif rk:
            out[cid] = {"reason": "NOT_RANKED", "line": ranking.RANK_STATES.get(rk["state"], "")}
    return out


def _changes(pid: str, aset: dict) -> list[str]:
    """What changed after this AssessmentSet was written (P3-01 for slice 3.5)."""
    inp = aset["input"]
    out = []
    rk = db.get_record(inp["ranking"]["id"], inp["ranking"]["version"])
    if not ranking.ranking_current(rk, ranking.ranking_signal(pid), ranking.candidates(pid)):
        out.append("rerank")
    if ranking.goal_of(pid)["purpose"] != inp["goal"]["purpose"]:
        out.append("purpose")
    ap = db.latest("audience_profile", pid)
    if inp["audience_profile"] and ap and set(ap["removed"]) & set(inp["audience_profile"]["keys"]):
        out.append("audience")
    if ranking.home_for(pid)["claim_id"] != inp["home"]["claim_id"] or ranking.home_for(pid)["country"] != inp["home"]["country"]:
        out.append("home")
    return out


CHANGE_WORDS = {"rerank": "your goal, presence or brands with a similar following changed",
                "purpose": "your purpose changed", "audience": "you removed an audience they used",
                "home": "your home market changed"}


def markets_view(pid: str) -> dict:
    prof = current_profile(pid)
    gens = generations(pid)
    gen = generation_for(pid, prof) if prof else None
    shown = gen or (gens[-1] if gens else None)  # an older generation stays readable after a profile change
    brief = db.latest("brief", pid)
    home, ships = ranking.home_for(pid), ships_of(brief)
    cand = ranking.candidates(pid)
    goal = ranking.goal_of(pid)
    base = {"catalog": len(markets.ORDER), "home": home, "regions": markets.REGIONS, "goal": goal,
            "can_start": prof is not None, "generation": None, "cities": [], "notices": [],
            "presence": ranking.presence_view(pid), "audience": ranking.audience_view(pid),
            "standins": ranking.standins_view(pid)}
    if not shown:
        return base
    runs = _runs(pid, "scan", shown["id"])
    run = runs[-1] if runs else None
    sruns = _runs(pid, "shortlist", shown["id"])
    srun = sruns[-1] if sruns else None
    rrun = _latest_run(pid, "rank")
    current = shown is gen
    # records are read after the runs that write them: a finished run never pairs with an unfinished generation,
    # assessment or shortlist
    shown = db.get_record(shown["id"])
    active = bool(run) and run["status"] in ("queued", "running")
    pending = json.loads(run["detail"])["params"]["cities"] if active else []
    every = [a for a in db.records("assessment_set", pid) if a["generation"]["id"] == shown["id"]
             and a.get("prompt_version") == STRATEGIST_VERSION]
    asets = [a for a in every if not a.get("fenced")]
    aset = asets[-1] if asets else (every[-1] if every else None)  # a fenced result shows only when nothing else exists
    late = bool(every) and bool(every[-1].get("fenced"))
    sl = db.latest("shortlist", pid)
    if sl and sl["assessment_set"] and sl["assessment_set"]["version"] not in {a["version"] for a in asets}:
        sl = None  # it belongs to another generation's assessments
    rk = db.get_record(aset["ranking"]["id"], aset["ranking"]["version"]) if aset else db.latest("qloo_ranking", pid)
    deferred = deferred_of(pid, aset, cand, rk)
    picks = {c["city_id"]: c for c in (sl or {}).get("cities", [])}
    proposed = {a["city_id"] for a in (aset or {}).get("assessments", [])}
    counts = dict.fromkeys(("queued", "searching", *TERMINAL), 0)
    attempted, in_flight = 0, 0
    researched = research_status(pid)
    planning = {r["input_key"] for r in db.q("SELECT input_key FROM runs WHERE project_id=? AND kind='auto_plan' AND "
                                             "status IN ('queued','running')", pid)}
    investigating = {r["city_id"] for r in db.q("SELECT DISTINCT city_id FROM specialist_runs WHERE project_id=? AND "
                                                "status IN ('queued','running')", pid)}
    rows, scans = [], {}
    for cid in markets.ORDER:
        city, ref = markets.CITIES[cid], shown["cities"].get(cid)
        if active and cid in pending and (ref is None or ref["run_id"] != run["id"]):
            status, in_flight = ("searching" if in_flight < IN_FLIGHT else "queued"), in_flight + 1
        else:
            status = ref["status"] if ref else "queued"
        scan = db.get_record(ref["scan_id"], ref["scan_version"]) if ref else None
        if scan:
            scans[cid] = scan
            attempted += scan["origin"] != "denied"
        counts[status] += 1
        d = markets.digest(scan) if scan and scan["status"] in ("returned", "empty") else None
        fit = markets.practical_fit(city, home, ships)
        ef = ranking.entry_facts(cid, home)
        rows.append({**city, "status": status, "digest": d, "fit": fit, "fit_label": markets.CHIPS[fit["chip"]],
                     "fit_line": markets.fit_line(fit, home), "entry": ef,
                     "entry_label": ranking.ENTRY_CHIPS[ranking.entry_chip(ef)],
                     "finding": d and (f"{d['returned_count']} places" + (f" · {markets.channel_line(d, 2)}" if d["returned_count"] else "")),
                     "reason": scan and (scan.get("error") or {}).get("reason"),
                     "attempts": scan and scan["attempts"], "origin": scan and scan["origin"],
                     "proposed": cid in proposed, "deferred": deferred.get(cid), "excluded": cand["excluded"].get(cid),
                     "pick": picks.get(cid), "research": researched.get(cid),
                     # a build waiting its turn has no research row yet: queued, not researching
                     "found": found_of(pid, cid, researched.get(cid), cid in investigating
                                       or (cid in planning and cid in researched), cid in planning)})
    signals = shown["request_config"]["signals"]
    view = {**base, "auto": config.AUTO_PLAN, "generation": {
        "id": shown["id"], "version": shown["version"], "status": shown["status"], "route": shown["route"],
        "signals": signals, "profile": shown["profile"], "current": current},
        "run": _run_view(run), "coverage": {"catalog": len(markets.ORDER), "attempted": attempted, **counts},
        "cities": rows}
    lookups = db.q("SELECT COUNT(*) n, COALESCE(SUM(origin='cache'),0) cached, COALESCE(SUM(origin='replay'),0) replayed, "
                   "COALESCE(SUM(origin='live'),0) live FROM usage WHERE project_id=? AND provider='qloo' AND stage='scan' "
                   "AND (origin!='live' OR attempt IS NULL OR attempt=1)", pid)[0]
    sent = db.q("SELECT COUNT(*) n FROM usage WHERE project_id=? AND provider='qloo' AND stage='scan' AND origin='live'",
                pid)[0]["n"]
    view["usage"] = {"lookups": lookups["n"], "cached": lookups["cached"], "replayed": lookups["replayed"], "sent": sent}
    writing = bool(srun) and srun["status"] in ("queued", "running")
    view["shortlist_run"] = _run_view(srun)
    ranking_active = writing or (bool(rrun) and rrun["status"] in ("queued", "running"))
    sig = ranking.ranking_signal(pid)
    view["ranking"] = rk and {
        "id": rk["id"], "version": rk["version"], "state": rk["state"], "text": ranking.RANK_STATES[rk["state"]],
        "signal_label": (rk["signal"] or {}).get("label"), "n_candidates": len(rk["candidates"]["city_ids"]),
        "positions": rk["positions"], "unranked": rk["unranked"], "running": ranking_active,
        "retryable": rk["state"] in ranking.RETRYABLE, "current": ranking.ranking_current(rk, sig, cand)}
    view["ranking_pending"] = {"running": ranking_active, "signal_label": sig and sig["label"],
                               "n_candidates": len(cand["city_ids"]), "needs_home": cand["needs_home"]}
    notices = []
    if not gen:
        notices.append({"kind": "changed", "action": "rescan" if prof else None,
                        "text": "Your discovery profile changed after this scan. This scan stays readable; "
                        + ("re-scan to use the new profile." if prof else "confirm your profile to re-scan.")})
    unreached = 0 if active else counts["queued"]
    if (counts["failed"] or unreached) and not active:
        stopped = run and run["status"] in ("failed", "cancelled")
        notices.append({"kind": "failed", "action": "retry_scan", "text": " ".join(filter(None, [
            f"The scan stopped: {json.loads(run['detail']).get('message') or run['status']}." if stopped else "",
            f"{counts['failed']} cit{'ies' if counts['failed'] != 1 else 'y'} failed." if counts["failed"] else "",
            f"{unreached} cit{'ies were' if unreached != 1 else 'y was'} not searched." if unreached else "",
            "The others are kept."]))})
    if not srun and not aset and not active and shown["status"] != "running" and counts["returned"] + counts["empty"]:
        notices.append({"kind": "failed", "action": "retry_shortlist",
                        "text": "The scan stopped before the recommendations started."})
    if srun and srun["status"] in ("failed", "partial", "cancelled") and (not aset or aset["run_id"] != srun["id"]):
        notices.append({"kind": "failed", "action": "retry_shortlist",
                        "text": "The recommendations didn't finish ("
                        + (json.loads(srun["detail"]).get("message") or "no reason given") + ")."})
    if aset:
        view["assessment"] = _assessment_view(pid, aset, rows, rk, home, ships)
        if rk["state"] in ranking.RETRYABLE and not writing:
            notices.append({"kind": "failed", "action": "retry_ranking", "text": ranking.RANK_STATES[rk["state"]]
                            + " The scan is kept, and you can add any city from All 80."})
        changed = [c for c in _changes(pid, aset) if not (c == "rerank" and cand["needs_home"])]
        if (changed or late) and not writing:
            notices.append({"kind": "changed", "action": "update", "rerank": "rerank" in changed, "text": " ".join(filter(None, [
                "Your change arrived while the last recommendations were being written, so that result was kept as "
                "history and not applied." if late else "",
                ("Since these recommendations were made, " + "; ".join(CHANGE_WORDS[c] for c in changed) + ".") if changed else "",
                "Fit is recomputed; the order and the explanations are not until you update."]))})
    if cand["needs_home"]:
        notices.append({"kind": "changed", "action": "home", "text": "Which country is home? Landfall couldn't tell from "
                        "your site or your stores, and needs it before Qloo ranks your cities; your goal is never broadened silently."})
    view["notices"] = notices
    if sl:
        included = [c for c in sl["cities"] if c["included"]]
        view["shortlist"] = {"version": sl["version"], "cities": sl["cities"], "cap": sl["cap"],
                             "included": len(included), "added": sum(c["added_by_founder"] for c in included),
                             "assessment_set": sl.get("assessment_set")}
    view["beachhead"] = beachhead_view(pid)
    view["shortlist_version"] = (db.latest("shortlist", pid) or {}).get("version", 0)  # the base for a beachhead choice
    return view


ENTRY_WORDS = {"shared_language": "shared language", "same_bloc": "same trade bloc"}


def entry_line(f: dict, home: dict) -> str:
    if not home.get("country"):
        return "Not known: your home country isn't known."
    parts = ["same country" if f["same_country"] else "another country"]
    parts.append("shared official language" if f["shared_language"] else "a new language" if f["shared_language"] is False
                 else "language fit not known")
    parts.append("same trade bloc" if f["same_bloc"] else "a customs border" if f["same_bloc"] is False else "trade bloc not known")
    h = f["overlap_hours"]
    parts.append("working hours not known" if h is None else f"{h:g} working hours overlap with home")
    return " · ".join(parts)


def _assessment_view(pid: str, aset: dict, rows: list[dict], rk: dict, home: dict, ships) -> dict:
    """Prose as written; entry facts and fit recomputed now (C fields)."""
    by_id = {r["city_id"]: r for r in rows}
    inp = aset["input"]
    ev = {c["city_id"]: c for c in inp["cities"]}
    cards = []
    for a in aset["assessments"]:
        r, c = by_id[a["city_id"]], ev[a["city_id"]]
        ef = ranking.entry_facts(a["city_id"], home)
        no_ship = r["fit"]["inside_ship_range"] is False and not ef["same_country"]
        cards.append({**{k: a[k] for k in ("city_id", "group", "qloo_position", "final_position", "moved_by_purpose",
                                          "position_line", "moved_line", "prose_source", "located_check", *PROSE)},
                      "role": a["role"], "role_label": ranking.ROLE_LABELS[a["role"]],
                      "name": r["name"], "region": r["region"], "country": r["country"],
                      "places": c["places"], "comparables": c["comparables"], "scan_status": c["scan_status"],
                      "scan_missing": c["scan_status"] not in ("returned", "empty"),
                      "entry_label": ranking.ENTRY_CHIPS[ranking.entry_chip(ef)], "entry_line": entry_line(ef, home)
                      + ("; needs a shipping or partner route" if no_ship else ""),
                      "fit": r["fit"], "featured": a["city_id"] in aset["featured"],
                      "key_unknown": a["city_id"] in inp["key_unknown"]})
    g = inp["goal"]
    sig = inp["signal"] or {}
    purpose = {"presence": "", "stockists": "; within each block of 4, your purpose (stockists) breaks near-ties",
               "test_demand": "; within each block of 4, your purpose (testing demand: shared language, trade bloc, "
                              "working hours) breaks near-ties"}[g["purpose"]]
    return {"id": aset["id"], "version": aset["version"], "generation": aset["generation"], "lead": aset["lead"],
            "groups": [{**gr, "top": gr["city_ids"][0] if gr["city_ids"] else None} for gr in aset["groups"]],
            "cards": cards, "faults": aset["faults"], "model_error": aset.get("model_error"),
            "ranking_state": rk["state"], "n_candidates": inp["n_candidates"], "signal": sig,
            "why_order": f"Qloo's ranking of the cities that are new for you and fit your goal{purpose}. Qloo ranked "
                         f"them in one query for {sig.get('label', 'your signal')}; nothing is compared across requests.",
            "audience_names": inp["audience_names"], "goal": g, "coverage": inp["coverage"],
            "still": "Whether any place or store exists as listed and would work with you, which ways in suit each "
                     "city, and what import, labeling and launch would cost.",
            "prompt_version": aset["prompt_version"]}


def place_view(pid: str, qloo_id: str, city_id: str) -> dict | None:
    """The stored place from the city's effective scan in the shown generation; no provider call."""
    v = markets_view(pid)
    if not v["generation"] or city_id not in markets.CITIES:
        return None
    gen = db.get_record(v["generation"]["id"])
    ref = gen["cities"].get(city_id)
    scan = ref and db.get_record(ref["scan_id"], ref["scan_version"])
    p = next((x for x in (scan or {}).get("places", []) if x["qloo_id"] == qloo_id), None)
    if not p:
        return None
    place = {k: v for k, v in p.items() if k != "affinity_in_request"}  # never shown, compared or sent to a model
    return {"place": place, "city": markets.CITIES[city_id], "class": markets.CHANNEL_LABELS.get(markets.place_class(p), "no category"),
            "request": {"signals": gen["request_config"]["signals"], "radius_m": gen["request_config"]["radius_m"],
                        "rank_note": f"#{p['rank_in_request']} of {len(scan['places'])} in this search only",
                        "origin": scan["origin"], "fetched_at": scan["fetched_at"]}}


HANDLERS["scan"] = scan_run
HANDLERS["shortlist"] = shortlist_run
HANDLERS["rank"] = rank_run
