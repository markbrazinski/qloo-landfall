"""Stage 6 · city research: how I could enter each city, with evidence (BUILD-PLAN slice 4; CONTRACTS B6 · Slice 4).

Records: CityResearch (rid per city and research version; Continue and Retry keep the version, Re-research makes a new
one), city_claim, lead (a version per presence check), approach_decision, founder_contact. Execution state lives in
`research_cities` (status, the ResearchInput pinned at city start, the agent transcript, the submission) and
`research_steps` (one row per tool call, model call and checker batch: the checkpoints and the counters). Page snapshots
are immutable rows in `research_sources`.
Provider stage strings: `research` (agent), `research_search` (grounding), `research_fetch`, `research_places` and
`research_urns` (Qloo), `research_check` (checker). No affinity reaches a prompt or a view.
"""
import hashlib
import json
import re
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Literal, get_origin

from pydantic import BaseModel, Field, model_validator
from strands import Agent, tool
from strands.hooks import MessageAddedEvent
from strands.tools.executors import SequentialToolExecutor

from . import brand, config, db, fetch, markets, qloo, ranking, runner, scan
from .brand import _norm, quote_found
from .cassette import NoRecording
from .ledger import BudgetExhausted
from .nova import make_model
from .runner import HANDLERS, Run

PROMPT_VERSION = "city_researcher@3"  # @2: limits as a plan, several tools per turn, ≤ 2 audiences (LIVE L4-A attempt 1); @3: audience place codes A{k}-{i} (review P4-I4)
CHECK_VERSION = "city_check@3"  # @2: unsupported parts, "shows the way in" (LIVE L4-B); @3: the full proposition restated, and the prose check (review P4-I1)
_M = json.loads((markets.DATA / "methods.json").read_text())
METHODS_VERSION = _M["version"]
METHODS = ("retail", "creator", "popup", "community", "digital")
METHOD_LABELS = {"retail": "Retail", "creator": "Creator", "popup": "Pop-up", "community": "Community", "digital": "Digital"}
SEARCHED = ("retail", "popup", "community")  # creator and digital have no Qloo place search
CODE_LETTER = {"retail": "R", "popup": "P", "community": "C", "audience": "A"}
RADIUS_M, TAKE = 15000, 25
MAX_CITIES, IN_FLIGHT = 8, 3
CITY_USD, EXPECTED_USD, FORCED_USD = 0.80, 0.36, 0.05
ANOMALY = 2  # a run over 2× its expected cost is stopped and explained (R6)  # FORCED_USD: kept back inside the cap for the forced submission
CAPS = {"model": 24, "search": 4, "page": 12, "presence": 6, "qloo": 6, "urn": 2, "check": 3, "prose": 2}
NUDGE_CALLS = 12  # from this many model calls, every tool result asks for the submission
WRAP_CALLS = 16   # from this many, no tool runs: the agent must submit (LIVE L4-A attempt 1 ran out of calls)
TOOL_CHARS, SUBMIT_AT_TOKENS, MAX_OUT = 4000, 60000, 3000
CHECK_BATCH = 12
LEAD_CHECK_LOADS = 3
SOCIAL = ("instagram.com", "tiktok.com", "facebook.com", "fb.com", "x.com", "twitter.com", "linkedin.com", "youtube.com",
          "youtu.be", "threads.net", "pinterest.com")
TERMINAL = ("complete", "partial", "failed")
_admit_lock = threading.Lock()  # concurrent cities reserve under one lock
_robots: dict[tuple[str, str], object] = {}


class CityCap(BudgetExhausted):
    """A per-city or per-run cap refused a call before dispatch (the global ledger was not asked)."""


def is_social(url: str) -> bool:
    h = fetch.site_of(url)
    return any(h == d or h.endswith("." + d) for d in SOCIAL)


def source_code(url: str) -> str:
    """The id the agent sees for a page: from its address, so a replayed run shows the agent identical text."""
    return "S" + hashlib.sha256(url.encode()).hexdigest()[:6]


def method_genres(pid: str) -> dict:
    """methods@1: the retail genre set by brand_genres@1 (fashion, accessories, drinks, food, beauty, home, unknown)."""
    genre = ranking.genre_for(db.latest("brief", pid))
    retail = _M["retail"].get(genre or "unknown") or _M["retail"]["unknown"]
    g = markets.ROOT_GENRE + ":"
    return {"version": METHODS_VERSION, "brand_genre": genre, "retail": [g + x for x in retail],
            "popup": [g + x for x in _M["popup"]], "community": [g + x for x in _M["community"]]}


# ---- ResearchInput (R1): the adapter from the slice-3.5 records, built at city start ----
def _ref(r: dict | None) -> dict | None:
    return r and {"id": r["id"], "version": r["version"]}


def current_generation(pid: str) -> dict | None:
    prof = scan.current_profile(pid)
    gen = scan.generation_for(pid, prof) if prof else None
    gens = scan.generations(pid)
    return gen or (gens[-1] if gens else None)


def card_of(pid: str, cid: str) -> tuple[dict | None, dict | None, dict | None]:
    """(AssessmentSet, its card for this city, its strategist evidence for this city) from the current shortlist."""
    sl = db.latest("shortlist", pid)
    a = (sl or {}).get("assessment_set")
    aset = db.get_record(a["id"], a["version"]) if a else None
    card = next((x for x in (aset or {}).get("assessments", []) if x["city_id"] == cid), None)
    ev = next((x for x in (aset or {}).get("input", {}).get("cities", []) if x["city_id"] == cid), None)
    return aset, card, ev


def build_input(pid: str, cid: str) -> dict:
    """One ResearchInput per city (BUILD-PLAN slice 4 table). An added city, a failed scan and the no-order route are
    ordinary inputs: absent parts are null or "not checked", never zero."""
    city = markets.CITIES[cid]
    aset, card, ev = card_of(pid, cid)
    pick = next((c for c in (db.latest("shortlist", pid) or {}).get("cities", []) if c["city_id"] == cid), {})
    gen = current_generation(pid)
    ref = (gen or {}).get("cities", {}).get(cid)
    cs = db.get_record(ref["scan_id"], ref["scan_version"]) if ref else None
    status = cs["status"] if cs else "not_scanned"
    places = []
    if cs and status == "returned":
        full = {p["qloo_id"]: p for p in cs["places"]}
        places = [{"qloo_id": p["qloo_id"], "name": p["name"], "genres": p["genres"],
                   "address": full[p["qloo_id"]].get("address"), "website": full[p["qloo_id"]].get("website")}
                  for p in markets.digest(cs)["top_places"]]
    home = ranking.home_for(pid)
    ef = ranking.entry_facts(cid, home)
    brief = db.latest("brief", pid)
    facts = [{"claim_id": c["id"], "label": c["label"], "value": c["value"], "state": c["state"]}
             for c in (db.get_record(i) for i in (brief or {}).get("facts", []))]
    goal = ranking.goal_of(pid)
    fi = db.latest("founder_input", pid) or {}
    pres = ranking.presence_of(pid)
    here = next((c for c in pres["cities"] if c["city_id"] == cid), None)
    located = next((c["status"] for c in pres["located_checks"] if c["city_id"] == cid), None)
    sig = ranking.ranking_signal(pid)
    ident = db.latest("identity", pid)
    ranking_signal = sig and {"kind": sig["kind"], "entity_ids": sig["entity_ids"], "names": sig["names"],
                              "label": sig["label"],
                              "ref": {"standins": sig["standins"]} if sig["standins"] else {"identity": _ref(ident)}}
    ap = db.latest("audience_profile", pid)
    audience = None
    if ap and sig and ranking.signal_key(ap["signal"]) == ranking.signal_key(sig):
        lines = ranking.demographics_words(ap["demographics"])["lines"]
        standins = ap["signal"]["kind"] == "standins"
        audience = {"profile": _ref(ap), "kind": ap["signal"]["kind"],
                    "offered": [{k: a[k] for k in ("uuid", "name", "type")} for a in ap["audiences"]
                                if a["uuid"] not in ap["removed"]],
                    "demographics_lines": [f"{x['signal_name']}: {x['text']}" for x in lines],
                    "caveat": ("people who like the stand-in brands, used as a stand-in for you: " if standins else "")
                    + ranking.CAVEAT}
    return {
        "city_id": cid, "name": city["name"], "country": city["country"], "lat": city["lat"], "lon": city["lon"],
        "selection": "RECOMMENDED" if card else "ADDED_BY_YOU", "added_by_founder": bool(pick.get("added_by_founder")),
        "recommendation": card and {"assessment_set": _ref(aset), "group": card["group"], "role": card["role"],
                                    "position_line": card["position_line"], "why": card["why"],
                                    "comparables": (ev or {}).get("comparables", [])},
        "scan": {"generation": _ref(gen), "city_scan": ref and {"id": ref["scan_id"], "version": ref["scan_version"]},
                 "status": status, "places": places},
        "entry": {**ef, "chip": ranking.entry_chip(ef)},
        "facts": facts, "brief": _ref(brief), "goal": {"scope": goal["scope"], "purpose": goal["purpose"]},
        "context": fi.get("research_context") or None,
        "presence": {"sources": [s["kind"] for s in here["sources"]] if here else [],
                     "excluded": bool(here and here["excluded"]), "located_check": located},
        "place_signal": {"signals": (gen or {}).get("request_config", {}).get("signals", []),
                         "profile": (gen or {}).get("profile")},
        "ranking_signal": ranking_signal, "audience": audience, "methods": method_genres(pid),
        "prompt_version": PROMPT_VERSION, "check_version": CHECK_VERSION,
    }


def place_params(inp: dict, method: str | None, urn: str | None = None) -> dict | None:
    """R1 routes: a method search sends the ranking signal's entities (brand or stand-ins) with the method's genres;
    with no ranking signal, the profile's own signals (expected flat, so LISTING). An audience search sends the URN
    alone, with no genre filter. Never a brand substituted for an unmatched identity."""
    params = {"filter.type": "urn:entity:place", "filter.location": f"POINT({inp['lon']} {inp['lat']})",
              "filter.location.radius": RADIUS_M, "take": TAKE}
    if urn:
        return {**params, "signal.demographics.audiences": urn}
    rs = inp["ranking_signal"]
    if rs:
        params["signal.interests.entities"] = ",".join(rs["entity_ids"])
    else:
        sigs = inp["place_signal"]["signals"]
        ents = [s["qloo_id"] for s in sigs if s["kind"] == "entity"]
        tags = [s["qloo_id"] for s in sigs if s["kind"] == "tag"]
        if not ents and not tags:
            return None
        if ents:
            params["signal.interests.entities"] = ",".join(ents)
        if tags:
            params["signal.interests.tags"] = ",".join(tags)
    params["filter.tags"] = ",".join(inp["methods"][method])
    return params


# ---- execution state, checkpoints and counters (R4, R6) ----
@dataclass
class Ctx:
    run: Run
    pid: str
    cid: str
    rv: int
    inp: dict
    forced: bool = False
    tokens_in: int = 0
    run_cities: int = 1
    # Slice 4.5 specialists reuse this context with their own limits and checkpoint key; the defaults are slice 4's.
    caps: dict | None = None
    usd_cap: float = CITY_USD
    expected_usd: float = EXPECTED_USD
    cr_key: str | None = None
    submit: str = "CityResearchSubmission"
    nudge_at: int = NUDGE_CALLS
    wrap_at: int = WRAP_CALLS
    stage: str = "research"  # provider stage prefix: a specialist's tool calls go live as specialist_*
    forced_usd: float = FORCED_USD  # kept back for a forced submission; 0 for one-call steps (LIVE L45-A: Develop refused)
    said: set = field(default_factory=set)  # plain refusal lines already shown for this investigation

    @property
    def cr(self) -> str:
        return self.cr_key or f"{self.pid}:{self.cid}:{self.rv}"

    @property
    def scope(self) -> str:  # slice 4's exact wording is kept: it is inside recorded model requests
        return "this city" if self.caps is None else "this investigation"


def city_row(pid: str, cid: str, rv: int | None = None):
    rows = (db.q("SELECT * FROM research_cities WHERE project_id=? AND city_id=? AND version=?", pid, cid, rv) if rv
            else db.q("SELECT * FROM research_cities WHERE project_id=? AND city_id=? ORDER BY version DESC LIMIT 1", pid, cid))
    return rows[0] if rows else None


def _set(pid: str, cid: str, rv: int, **fields) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    db.x(f"UPDATE research_cities SET {cols} WHERE project_id=? AND city_id=? AND version=?",
         *[json.dumps(v) if k in ("input", "transcript", "submission") and v is not None else v for k, v in fields.items()],
         pid, cid, rv)


def counts(cr: str) -> dict:
    """Counters from the checkpoint rows: they persist across restart, Stop and Continue, and a call whose completion
    is unknown stays counted at its reserved exposure."""
    out = dict.fromkeys(("model", "search", "page", "presence", "qloo", "urn", "check", "prose", "locate", "profile"), 0)
    usd, est, grounding = 0.0, 0.0, 0
    for r in db.q("SELECT kind, status, usd, result FROM research_steps WHERE cr=?", cr):
        k = r["kind"]
        if k.startswith("usd_"):
            usd += r["usd"]
            if r["status"] == "done":
                est += r["usd"]
                grounding += (json.loads(r["result"] or "{}")).get("grounding_calls", 0)
            if k == "usd_model":
                out["model"] += 1
        elif k in out:
            out[k] += 1
    return {**out, "usd_reserved": round(usd, 6), "usd_estimated": round(est, 6), "grounding_searches": grounding}


def _key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:16]


def memo(ctx: Ctx, kind: str, key: str, fn) -> tuple[dict | None, str | None]:
    """One checkpoint per logical call: a completed call returns its stored result (0 dispatches). A call started before
    a crash or a stop (completion unknown) is kept, still counted, and dispatched once more: exactly-once dispatch across
    a crash is not promised. Returns (result, refusal)."""
    k = f"{kind}:{key}"
    with db._lock:
        row = db.q("SELECT status, result FROM research_steps WHERE cr=? AND key=?", ctx.cr, k)
        if row and row[0]["status"] == "done":
            return json.loads(row[0]["result"]), None
        caps = ctx.caps or CAPS
        if caps.get(kind) is not None and counts(ctx.cr)[kind] - bool(row) >= caps[kind]:
            return None, f"the {caps[kind]}-call limit for {ctx.scope} is reached"
        if row:
            n = db.q("SELECT COUNT(*) n FROM research_steps WHERE cr=? AND key LIKE ?", ctx.cr, k + "#%")[0]["n"]
            db.x("UPDATE research_steps SET key=?, status='interrupted' WHERE cr=? AND key=?", f"{k}#{n + 1}", ctx.cr, k)
        db.x("INSERT INTO research_steps VALUES(?,?,?,?,?,?,?)", ctx.cr, k, kind, "running", None, 0, db.now())
    try:
        result = fn()
    except BaseException:
        db.x("UPDATE research_steps SET status='failed' WHERE cr=? AND key=?", ctx.cr, k)
        raise
    db.x("UPDATE research_steps SET status='done', result=? WHERE cr=? AND key=?", json.dumps(result), ctx.cr, k)
    return result, None


def steps(cr: str, kind: str) -> list[dict]:
    return [json.loads(r["result"]) for r in db.q("SELECT result FROM research_steps WHERE cr=? AND kind=? AND "
                                                  "status='done' ORDER BY at, rowid", cr, kind)]


def _run_spent(ctx: Ctx) -> float:
    crs = [f"{ctx.pid}:{c}:{v}" for c, v in ctx.run.params.get("cities", [])]
    if not crs:
        return 0.0
    marks = ",".join("?" * len(crs))
    return db.q(f"SELECT COALESCE(SUM(usd),0) s FROM research_steps WHERE kind LIKE 'usd_%' AND cr IN ({marks})", *crs)[0]["s"]


def admitter(ctx: Ctx, kind: str):
    """Dollar and call admission before every model, grounding or checker call: the city cap, then the run cap, then
    (in nova) the global ledger, which is authoritative and checks expiry. The exposure is reserved as a row."""
    def admit(exposure: float) -> None:
        with _admit_lock, db._lock:
            c = counts(ctx.cr)
            caps = ctx.caps or CAPS
            if kind == "model" and c["model"] >= caps["model"] - (0 if ctx.forced else 1):
                raise CityCap(f"the {caps['model']}-call model limit for {ctx.scope}")
            room = ctx.usd_cap - (0 if ctx.forced else ctx.forced_usd)
            if c["usd_reserved"] + exposure > room:
                raise CityCap(f"the ${ctx.usd_cap:.2f} cost limit for {ctx.scope}")
            limit = ANOMALY * ctx.expected_usd * max(1, len(ctx.run.params.get("cities", [])))
            if not ctx.forced and _run_spent(ctx) + exposure > limit:  # the plan's stop rule (the forced submission
                # is budgeted inside the city cap, so a stopped city still returns what it has)
                raise CityCap(f"this run reached twice its expected cost (${limit:.2f}); the rest was not sent")
            n = db.q("SELECT COUNT(*) n FROM research_steps WHERE cr=? AND kind LIKE 'usd_%'", ctx.cr)[0]["n"]
            db.x("INSERT INTO research_steps VALUES(?,?,?,?,?,?,?)", ctx.cr, f"usd:{n + 1}", f"usd_{kind}", "running",
                 None, round(exposure, 6), db.now())
    return admit


def usage_sink(ctx: Ctx, kind: str):
    """Project usage row, then the oldest open reservation of this kind becomes the recorded cost."""
    project = brand._usage_sink(ctx.run)

    def sink(u: dict) -> None:
        project(u)
        if kind == "model":
            ctx.tokens_in = max(ctx.tokens_in, u["input_tokens"])
        with db._lock:
            r = db.q("SELECT key FROM research_steps WHERE cr=? AND kind=? AND status='running' ORDER BY at, rowid LIMIT 1",
                     ctx.cr, f"usd_{kind}")
            if r and u.get("status") == 429:  # throttled: Bedrock didn't run or bill it, so it uses no call or dollar cap
                db.x("DELETE FROM research_steps WHERE cr=? AND key=?", ctx.cr, r[0]["key"])
            elif r:
                db.x("UPDATE research_steps SET status='done', usd=?, result=? WHERE cr=? AND key=?", round(u["est_usd"], 6),
                     json.dumps({k: u[k] for k in ("input_tokens", "output_tokens", "grounding_calls", "replayed")}),
                     ctx.cr, r[0]["key"])
    return sink


def emit(ctx: Ctx, kind: str, text: str, **extra) -> None:
    ctx.run.emit("research", {"city_id": ctx.cid, "version": ctx.rv, "kind": kind, "text": text, **extra})


def plain_refusal(err: str) -> str:
    """A tool's internal refusal or error ("not run: the $0.40 cost limit for this investigation") → one plain line for
    the founder (owner, 28 Oct 2026); the raw reason goes to Activity only."""
    if "cost limit" in err:
        m = re.search(r"\$(\d+\.\d\d)", err)
        return (f"Stopped searching: this {'investigation' if 'investigation' in err else 'city'} reached its budget"
                + (f" (US${m.group(1)})" if m else ""))
    if "call model limit" in err:
        return f"Stopped searching: this {'investigation' if 'investigation' in err else 'city'} used all its steps"
    if "expected cost" in err:
        return "Stopped searching: this run reached twice its expected cost"
    if "allowance" in err or err.startswith("not run"):
        return "Stopped searching: Landfall's operating allowance is used up for now"
    return "A web search didn't answer: what it would have found is unknown"


def emit_refusal(ctx: "Ctx", err: str, method: str | None) -> None:
    """Once per investigation and reason: repeated refusals collapse into one line; the detail goes to Activity."""
    line = plain_refusal(err)
    if line in ctx.said:
        return
    ctx.said.add(line)
    emit(ctx, "UNAVAILABLE", line, method=method)
    _activity(ctx.run, "Web search", "web search", err, status="UNAVAILABLE")


_FAILED = re.compile(r"\b(failed|refused|couldn't read|not run|HTTP \d{3})\b|Error\b", re.I)


def shown(e: dict) -> bool:
    """Progress and findings only (owner, 28 Oct 2026): a failed read, search or check stays in Activity and in the
    method page's "How we looked" count, never in the research the founder watches."""
    return e.get("kind") not in ("UNAVAILABLE", "FAILED", "REFUSED") and not (
        e.get("kind") in ("CHECKING", "CANDIDATE", "APPROACH") and _FAILED.search(e.get("text") or ""))  # findings quote the web


def plain_stream(events: list[dict]) -> list[dict]:
    """Stored research events as shown: failures are dropped (see `shown`); an older raw "Web search failed: …" line
    becomes its plain line, once per run."""
    out, seen = [], set()
    for e in events:
        if not shown(e):
            continue
        if e.get("text", "").startswith("Web search failed: "):
            e = {**e, "text": plain_refusal(e["text"][len("Web search failed: "):])}
            if (e.get("run_id"), e.get("method"), e["text"]) in seen:
                continue
            seen.add((e.get("run_id"), e.get("method"), e["text"]))
        out.append(e)
    return out


def _activity(run: Run, task: str, tool_name: str, detail: str, status: str = "DONE", role: str = "Researcher"):
    brand._activity(run, task, tool_name, detail, status, role=role, stage="Opportunities")


# ---- pages: immutable snapshots (R3) ----
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Separators are a space, dot or dash on one line, and at least 9 digits: LIVE L4-B read a street number and a postcode
# on two lines ("72\n1200-289") as a phone.
PHONE = re.compile(r"(?<![\d-])(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\) ?|\d{2,4}[ .-])\d{3,4}[ .-]\d{3,4}(?![\d-])")


def contacts_in(body: str, text: str) -> list[dict]:
    """Deterministic contacts on a page: mailto:, tel:, an email or a phone pattern. Attached to a lead only from its
    own site (R2)."""
    out: list[dict] = []
    for m in re.finditer(r"""href=["']mailto:([^"'?>\s]+)""", body or "", re.I):
        out.append({"kind": "email", "value": m.group(1)})
    for m in re.finditer(r"""href=["']tel:([^"'>\s]+)""", body or "", re.I):
        out.append({"kind": "phone", "value": m.group(1)})
    out += [{"kind": "email", "value": m.group(0)} for m in EMAIL.finditer(text or "")]
    out += [{"kind": "phone", "value": m.group(0).strip()} for m in PHONE.finditer(text or "")
            if len(re.sub(r"\D", "", m.group(0))) >= 9]
    seen, uniq = set(), []
    for c in out:
        if c["value"].lower() not in seen:
            seen.add(c["value"].lower())
            uniq.append(c)
    return uniq[:6]


def _robots_ok(run: Run, url: str, stage: str = "research_fetch") -> bool:
    key = (run.id, fetch.site_of(url))
    if key not in _robots:
        _robots[key] = fetch.robots_for(url, run.cassette, stage)
    return fetch.robots_allows(url, _robots[key])


def load_page(run: Run, pid: str, cid: str, url: str, tool_call: str, reuse: bool = True, stage: str = "research_fetch") -> dict:
    """One page load: a run-scoped reuse of an earlier successful snapshot (its use recorded, its date kept), or a new
    GET (≤ 5 redirects) plus at most one render. A failed fetch is stored but never reused; nothing is replaced.
    Social platforms are never fetched or rendered."""
    url = fetch.check_url(url)
    if is_social(url):
        raise fetch.FetchBlocked("social platforms are never read")
    hit = db.q("SELECT * FROM research_sources WHERE project_id=? AND run_id=? AND url=? AND status=200 AND error IS NULL "
               "ORDER BY rowid LIMIT 1", pid, run.id, url) if reuse else []
    if hit:
        s = dict(hit[0])
        db.x("INSERT OR IGNORE INTO research_source_uses VALUES(?,?,?)", s["id"], cid, run.id)
        return {**s, "reused": True}
    page, ex, rendered = {}, {"title": "", "text": ""}, 0
    if not _robots_ok(run, url, stage):
        page = {"url": url, "status": 0, "error": "robots.txt asks automated readers not to read it"}
    else:
        page = fetch.get(url, run.cassette, stage)
        if page.get("status") == 200:
            ex = fetch.extract(page)
            if fetch.needs_render(ex) and not is_social(page.get("url") or url):
                shown = fetch.render(url, run.cassette, stage)
                if shown.get("status") == 200 and len((rx := fetch.extract(shown))["text"]) > len(ex["text"]):
                    page, ex, rendered = shown, rx, 1
    ok = page.get("status") == 200 and bool(ex.get("text"))  # a blank HTTP 200 is unavailable, not an empty page
    err = None if ok else page.get("error") or ("no readable text" if page.get("status") == 200 else f"HTTP {page.get('status')}")
    sid = db.new_id("rsrc")
    db.x("INSERT INTO research_sources(id, project_id, run_id, city_id, tool_call_id, url, final_url, fetched_at, status, "
         "title, text, rendered, error, contacts, replayed) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
         sid, pid, run.id, cid, tool_call, url, page.get("url") or url, db.now(), page.get("status") or 0,
         (ex.get("title") or "")[:300], ex.get("text") or "", rendered, err,
         json.dumps(contacts_in(page.get("body", ""), ex.get("text", "")) if ok else []), int(run.cassette.mode == "replay"))
    db.x("INSERT OR IGNORE INTO research_source_uses VALUES(?,?,?)", sid, cid, run.id)
    return {**dict(db.q("SELECT * FROM research_sources WHERE id=?", sid)[0]), "reused": False}


def source(sid: str) -> dict | None:
    r = db.q("SELECT * FROM research_sources WHERE id=?", sid)
    return dict(r[0]) if r else None


def readable(s: dict) -> bool:
    return s.get("status") == 200 and not s.get("error")


# ---- presence and leads (R2) ----
CLOSED = re.compile(r"\b(permanently closed|has closed|have closed|closed its doors|closed down|is closing|has moved|"
                    r"moved to|relocated to)\b")
STREET_NO = re.compile(r"\b\d{1,5}[a-z]?\s+[a-z]")


def _in(name: str, text: str) -> int:
    n = _norm(name)
    return _norm(text).find(n) if len(n) >= 3 else -1


def _street(address: str | None) -> str | None:
    s = _norm((address or "").split(",")[0])
    return s if len(s) >= 6 and re.search(r"\d", s) else None


def _postcode(address: str | None) -> str | None:
    m = re.search(r"\b(\d{5}|[A-Z]\d[A-Z]\s?\d[A-Z]\d|[A-Z]{1,2}\d[A-Z\d]?\s\d[A-Z]{2})\b", address or "")
    return _norm(m.group(1)) if m else None


def names_city(text: str, cid: str) -> bool:
    return bool(markets.names_in(text, [cid]))


# A street address in normalised text: "174 ossington ave", "rua nova do almada 72", "istedgade 95".
ADDRESS = re.compile(r"\b\d{1,5}[a-z]?\s+(?:[a-z0-9]+\s+){1,3}(?:st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|ln|"
                     r"lane|way|pl|place|ct|court|pkwy|hwy|cres|tce|terrace|parade|pde)\b|\b(?:rua|r|avenida|av|largo|"
                     r"travessa|calle|via)\s+(?:[a-z]{2,}\s+){1,4}\d{1,4}\b|\b[a-z]{3,}(?:gade|vej|strasse|straat|weg|gatan|katu)"
                     r"\s+\d{1,4}\b")


def street_key(address: str | None) -> str | None:
    """A regex for the house number and street word of a known address ("1955 johnson", "212 n 3rd", "almada 72"),
    found with the same ADDRESS pattern used on pages. A postcode alone is never a key: neighbours share it."""
    m = ADDRESS.search(_norm(address or ""))
    if not m:
        return None
    toks = m.group(0).split()
    i = next(j for j, x in enumerate(toks) if x[:1].isdigit())
    if i == 0:
        word = next((x for x in toks[1:] if len(x) >= 2), toks[1])
        return rf"\b{toks[0]}\s+(?:[a-z]\s+)?{re.escape(word)}\b"
    word = next((x for x in reversed(toks[:i]) if len(x) >= 3), toks[i - 1])
    return rf"\b{re.escape(word)}\s+{toks[i]}\b"


def passages(name: str, text: str) -> list[str]:
    """The lead's own passages: each line naming it, plus the next line when that line is short (a directory entry
    with its address below). An address elsewhere on the page belongs to someone else (review P4-I2)."""
    units = [s.strip() for line in (text or "").split("\n") for s in SENT.split(line) if s.strip()]
    out = []
    for i, u in enumerate(units):
        if _in(name, u) >= 0:
            out.append(_norm(u + (" " + units[i + 1] if len(u) < 120 and i + 1 < len(units) else "")))
    return out


def presence_of_lead(lead: dict, pages: list[dict], cid: str, failed_check: str | None = None) -> dict:
    """VERIFIED: a page names the lead with its known street in the same passage, or is the lead's own site and gives
    its known street (or, with no known address, an address in the city). A different street beside its name, or a
    different address on its own site, → SOURCES_DIFFER. "Closed" or "moved" near its name → SOURCES_DIFFER. Name only
    → NOT_CHECKED. Unreadable → CHECK_FAILED. Willingness is never assessed here.
    ponytail: ADDRESS knows common English, Portuguese/Spanish and Nordic/German street forms; other forms read as no
    address (NOT_CHECKED), never as a match."""
    named = [p for p in pages if readable(p) and _in(lead["name"], p.get("text") or "") >= 0]
    if not named:
        if failed_check:
            return {"state": "CHECK_FAILED", "reason": f"The check couldn't read the page ({failed_check}).", "source_ids": []}
        return {"state": "NOT_CHECKED", "reason": "Not named on a page Landfall read; location not confirmed.",
                "source_ids": []}
    n = _norm(lead["name"])
    for p in named:
        t = _norm(p["text"])
        if any(CLOSED.search(t[max(0, m.start() - 200): m.end() + 200]) for m in re.finditer(re.escape(n), t)):
            return {"state": "SOURCES_DIFFER", "reason": "A page says it closed or moved; other sources name it.",
                    "source_ids": [x["id"] for x in named]}
    key = street_key(lead.get("address"))
    differs = []
    for p in named:
        own = bool(lead.get("own_site")) and fetch.same_site(p.get("final_url") or p["url"], lead["own_site"])
        spots = [_norm(p["text"])] if own else passages(lead["name"], p["text"])
        if key and any(re.search(key, s) for s in spots):
            return {"state": "VERIFIED", "reason": "Its own site gives its listed address." if own else
                    "The page gives its listed address in the same entry.", "source_ids": [p["id"]]}
        found = [a.group(0) for s in spots for a in ADDRESS.finditer(s)]
        if key and found:
            differs.append(p["id"])  # an address beside its name, but not the one Qloo lists
        elif own and not key and found and names_city(p["text"], cid):
            return {"state": "VERIFIED", "reason": "Its own site gives an address in the city.", "source_ids": [p["id"]]}
    if differs:
        return {"state": "SOURCES_DIFFER", "reason": "A page gives it a different address from the one Qloo lists.",
                "source_ids": differs}
    if failed_check:  # named elsewhere, but the check of its own site couldn't read the page (B09)
        return {"state": "CHECK_FAILED", "reason": f"The check couldn't read its page ({failed_check}); it is named on "
                "another page, location not confirmed.", "source_ids": [p["id"] for p in named]}
    return {"state": "NOT_CHECKED", "reason": "Named on a page; location not confirmed.", "source_ids": [p["id"] for p in named]}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\b(and|the)\b", "", _norm(s)))


def named_domain(name: str, url: str | None) -> str | None:
    """A domain that carries the lead's name (kettlecrane.example for "Kettle & Crane"): its own site, as far as a
    deterministic check can tell. ponytail: a name-in-domain heuristic; a site that names itself differently stays
    unattributed (its contact UNKNOWN), never misattributed."""
    dom = fetch.site_of(url or "")
    s = _slug(name)
    return dom if dom and len(s) >= 4 and s in _slug(dom.rsplit(".", 1)[0]) and not is_social(url or "") else None


def same_name(a: str, b: str) -> bool:
    """The same place by name: equal, or one contains the other, ignoring case, accents, spaces and punctuation."""
    x, y = _slug(a), _slug(b)
    return bool(x and y) and (x == y or (min(len(x), len(y)) >= 4 and (x in y or y in x)))


def lead_key(lead: dict, cid: str) -> str:
    return lead.get("qloo_id") or f"{_norm(lead['name'])}|{cid}|{lead.get('first_domain') or ''}"


# ---- the agent's submission (city_researcher@2) ----
# Lenient on purpose (LIVE L4-A attempt 1): a null list or a missing reason once failed validation until the city's
# call budget ran out. Nulls take the default, numbers are read out of "claim 3", and method, status and kind are
# normalised by the validator (an unknown method is dropped, an unknown status reads NEEDS_RESEARCH).
def _ints(v) -> list[int]:
    out = []
    for x in v if isinstance(v, list) else [v] if v is not None else []:
        m = re.search(r"\d+", str(x))
        if m:
            out.append(int(m.group(0)))
    return out


def _json_list(v, annotation):
    """A list sent as a JSON string, read as the list. Nova also sends it with a stray closing bracket (LIVE community,
    27 Oct 2026: `…"}]}, "qloo_ref"…` five times until the run hung): drop the bracket the parser trips on and retry.
    ponytail: covers stray `}`/`]` only, up to 5 repairs; anything else stays a string and fails validation as before."""
    if not (isinstance(v, str) and v.strip()[:1] == "[" and get_origin(annotation) is list):
        return v
    def fails_at(text):
        try:
            json.loads(text)
            return None
        except json.JSONDecodeError as e:
            return e.pos
    s = v.strip()
    for _ in range(6):
        pos = fails_at(s)
        if pos is None:
            return json.loads(s)
        # the stray bracket is usually a few characters before where the parser complains: drop the nearest one whose
        # removal lets parsing get further
        for at in range(min(pos, len(s) - 1), max(pos - 40, -1), -1):
            if s[at] in "}]":
                t = s[:at] + s[at + 1:]
                after = fails_at(t)
                if after is None or after > pos:
                    s = t
                    break
        else:
            return v
    return v


class Lenient(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _nulls(cls, data):
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if v is not None}
            for k, f in cls.model_fields.items():
                if k in data and isinstance(data[k], str):  # a list sent as a (possibly malformed) JSON string
                    data[k] = _json_list(data[k], f.annotation)
                if k in data and f.annotation == list[int]:
                    data[k] = _ints(data[k])
                elif k in data and f.annotation == list[str] and not isinstance(data[k], list):
                    data[k] = [str(data[k])]
        return data


class LeadIn(Lenient):
    ref: str = Field("", description="Your short id for this lead, e.g. L1; claims and approaches refer to it")
    name: str = Field(description="The name exactly as Qloo or the page gives it")
    kind: str = Field("other", description="shop, venue, group, creator, online or other")
    qloo_ref: str | None = Field(None, description="The bracketed code from a Qloo search, e.g. R3, when it came from Qloo")
    website: str | None = Field(None, description="Its own website, only if a page you fetched gives it")


class ClaimIn(Lenient):
    subject: str = Field("city", description="A lead ref (L1) or 'city'")
    method: str = Field("", description="retail, creator, popup, community or digital")
    text: str = Field(description="One short sentence: what the quote shows about the subject in this city")
    source: str = Field(description="The SOURCE id of a page you fetched, e.g. S1a2b3c")
    quote: str = Field(description="Copied exactly from that page's text, 5-40 words")


class ApproachIn(Lenient):
    method: str = Field(description="retail, creator, popup, community or digital")
    status: str = Field("NEEDS_RESEARCH", description="DEVELOP, HOLD, NEEDS_RESEARCH or NOT_SUITABLE_NOW")
    name: str = Field("", description="A short name for this way in, e.g. 'A shelf in a North Loop menswear shop'")
    concept_name: str | None = Field(None, description="For DEVELOP: a campaign concept name (a proposal)")
    concept_line: str | None = Field(None, description="For DEVELOP: one line describing the concept (a proposal)")
    lead_refs: list[str] = Field(default_factory=list)
    claim_refs: list[int] = Field(default_factory=list, description="Numbers of your claims it rests on (1 = first)")
    hypotheses: list[str] = Field(default_factory=list, description="What would need to be true, as proposals")
    open_requirements: list[str] = Field(default_factory=list, description="What is still unknown or to check")
    reason: str = Field("", description="Why this status, citing claims by number, or 'no evidence found in this research'")
    from_your_note: bool = Field(False, description="True when the founder's note decided this status")
    audiences: list[str] = Field(default_factory=list, description="Names of audiences this approach uses")


class CityResearchSubmission(Lenient):
    learned: str = Field("", description="One paragraph on what was found in this city, citing claims by number")
    learned_claims: list[int] = Field(default_factory=list)
    facts_used: list[str] = Field(default_factory=list, description="The brief facts you relied on, by number, e.g. F2; may be empty")
    leads: list[LeadIn] = Field(default_factory=list)
    claims: list[ClaimIn] = Field(default_factory=list)
    approaches: list[ApproachIn] = Field(default_factory=list,
                                         description="Exactly one per method: retail, creator, popup, community, digital")


METHOD_ALIASES = {"pop-up": "popup", "pop_up": "popup", "pop up": "popup", "popups": "popup", "pop-ups": "popup",
                  "creators": "creator", "influencer": "creator", "events": "community", "community/events": "community",
                  "online": "digital", "stockist": "retail", "stockists": "retail"}
STATUSES = {"DEVELOP", "HOLD", "NEEDS_RESEARCH", "NOT_SUITABLE_NOW"}


def norm_method(s: str | None) -> str | None:
    s = (s or "").strip().lower()
    s = METHOD_ALIASES.get(s, s)
    return s if s in METHODS else None


def norm_status(s: str | None) -> str:
    s = re.sub(r"[\s-]+", "_", (s or "").strip().upper())
    return {"DEVELOPED": "DEVELOP", "READY": "DEVELOP", "READY_TO_COMPARE": "DEVELOP", "HELD": "HOLD"}.get(s, s) \
        if s in STATUSES | {"DEVELOPED", "READY", "READY_TO_COMPARE", "HELD"} else "NEEDS_RESEARCH"


SYSTEM = """You are the Researcher on Landfall, an international launch team for small consumer brands. You research ONE
city and assess five ways the brand could enter it: retail, creator, popup (pop-up), community and digital. A founder
reads your work, and every claim is checked against the page it quotes.

Tools:
- qloo_places(method): places Qloo relates to the brand's signal near the city, for retail, popup or community. These
  are cultural relationships, not demand, popularity or sales. LISTING means Qloo gave no taste order.
- qloo_audience_places(audience): places Qloo relates to one listed audience (its exact name).
- web_search(query, method): a web search returning page addresses and a short answer. The answer is only a lead; never
  write a claim from it.
- fetch_page(url, method): reads one page; gives its SOURCE id and text. Only pages you fetch can support a claim.
- check_presence(lead_name, url): reads a lead's own website to see whether it is at an address in this city.
Finish by calling CityResearchSubmission.

Evidence rules:
- A claim is one short sentence about a lead (its ref, e.g. L1) or about the city, with the SOURCE id of a fetched page
  and a quote copied exactly from that page's text (5-40 words). The quote itself must show the claim, and the passage
  around it must place it in this city. Paraphrase in `text`; never in `quote`.
- Never invent a name, address, number, website, contact or fact. What no fetched page says is unknown.
- Social platforms (Instagram, TikTok, Facebook, X, LinkedIn, YouTube, Threads, Pinterest) are never read. A creator
  found only there stays NEEDS_RESEARCH.
- Never claim demand, sales, popularity, audience size, market size, reach or cost figures, and never compare this city
  with another. Qloo's places and audiences are cultural relationships, not customers.
- The founder's note (YOU SAID) is their decision. If it rules a method out, that method is NOT_SUITABLE_NOW with
  from_your_note true. If it names someone they know, consider them.

The five methods, each assessed exactly once:
- retail: a shop that stocks outside brands, fitting the brand's category. Big chains are rarely a first stockist.
- popup: a venue that hosts events or rents space.
- community: a group or activity that meets in the city.
- creator: a local creator with a readable page that is not a social profile.
- digital: reaching the city's people online; needs an in-city fact about the audience's interests. No figures.
DEVELOP only when at least one of your claims for that method shows it; otherwise HOLD, NEEDS_RESEARCH (not enough
evidence yet) or NOT_SUITABLE_NOW (the evidence or the founder's note says no). Every reason cites its claims by number,
or says no evidence was found in this research. A concept (a name and one line) is a proposal; it may not state facts
beyond its claims. Name a lead in any text only with a claim about it.

Work efficiently, and call several tools in one turn when you can: search Qloo for retail, popup and community; search
the web for the strongest leads; fetch the pages that name them; check a lead's own site when it has one. Use
qloo_audience_places for at most two audiences. Limits per city: 4 web searches, 12 page reads, 6 presence checks and 6
Qloo searches. Submit by about your 12th turn; from the 16th no tool runs. A thin city submitted honestly is a good
result."""
STANDIN_RULE = ("\n\nThe Qloo signal is a set of stand-in brands with a similar following, because Qloo doesn't know this "
                "brand. Never describe their audiences as the brand's own customers.")
FORCED = ("A limit for this city is reached. Call CityResearchSubmission now with what you have: assess all five "
          "methods from the claims you can quote; anything not established is NEEDS_RESEARCH.")


def prompt_of(inp: dict) -> str:
    """Deterministic text from the ResearchInput: no ids of ours, no dates, no affinity or positions."""
    country = markets.COUNTRY_NAME.get(inp["country"], inp["country"])
    rec = inp["recommendation"]
    lines = [f"CITY: {inp['name']}, {country}",
             "WHY IT IS HERE: " + (f"recommended. {rec['why']}" if rec else "the founder added it; it is not a recommendation")]
    sc = inp["scan"]
    if sc["places"]:
        lines.append("PLACES QLOO RETURNED IN THE 80-CITY SCAN (not checked): " + "; ".join(
            f"{p['name']}" + (f" ({', '.join(p['genres'])})" if p["genres"] else "") for p in sc["places"]))
    else:
        lines.append({"empty": "THE 80-CITY SCAN: Qloo returned no related places here.",
                      "failed": "THE 80-CITY SCAN: the place search failed here, so its places are unknown.",
                      "unresolved": "THE 80-CITY SCAN: Qloo couldn't search this city area, so its places are unknown.",
                      "not_scanned": "THE 80-CITY SCAN: this city wasn't scanned, so its places are unknown."
                      }.get(sc["status"], "THE 80-CITY SCAN: places unknown."))
    if rec and rec["comparables"]:
        lines.append("RELATED-BRAND STORES WITHIN 15 MILES: " + "; ".join(
            f"{b['name']} {b['stores'] if b.get('state') == 'ok' and b.get('stores') is not None else 'not checked'}"
            for b in rec["comparables"]))
    lines.append(f"ENTRY: {ranking.ENTRY_CHIPS[inp['entry']['chip']].lower()}")
    facts = [f for f in inp["facts"] if f["state"] in ("SUPPORTED", "STATED")]
    lines.append("BRAND FACTS (from its own site or the founder):\n" + "\n".join(
        f"  F{i} {f['label']}: {f['value']}" for i, f in enumerate(facts, 1)) if facts else "BRAND FACTS: none established")
    g = inp["goal"]
    lines.append(f"GOAL: expand {ranking.SCOPE_WORDS[g['scope']]} to {ranking.PURPOSE_WORDS[g['purpose']]}")
    lines.append(f"FOUNDER'S NOTE (YOU SAID): {inp['context']}" if inp["context"] else "FOUNDER'S NOTE: none")
    rs = inp["ranking_signal"]
    lines.append(f"QLOO SIGNAL: {rs['label']}" if rs else
                 "QLOO SIGNAL: the brand's profile tags only; Qloo's place searches here are listings without taste order")
    a = inp["audience"]
    if a and a["offered"]:
        lines.append("AUDIENCES QLOO RELATES TO THE SIGNAL (" + a["caveat"] + "): " + ", ".join(x["name"] for x in a["offered"]))
        if a["demographics_lines"]:
            lines.append("DEMOGRAPHIC SKEWS (skews, not shares): " + "; ".join(a["demographics_lines"]))
    else:
        lines.append("AUDIENCES: none available")
    return "\n".join(lines) + "\n\nResearch this city, then call CityResearchSubmission."


# ---- tools ----
def _places_text(res: dict, label: str) -> str:
    if res.get("status") == "failed":
        return f"Qloo's search failed ({res.get('reason')}). Treat these places as unknown, not absent."
    if not res["places"]:
        return "Qloo returned no places for this search."
    head = ("LISTING: places of this kind near the city; Qloo gave no taste order.\n" if res["listing"] else
            f"Places Qloo relates to {label} (a cultural relationship, not demand):\n")
    return head + "\n".join(f"[{p['code']}] {p['name']} · {', '.join(g['name'] for g in p['genres'][:3]) or 'no category'}"
                            + (f" · {p['address']}" if p.get("address") else "")
                            + (f" · {p['website']}" if p.get("website") else "") for p in res["places"])


def _search_places(ctx: Ctx, key: str, params: dict, letter: str, method: str | None, audience: dict | None) -> dict:
    run = ctx.run

    def go():
        try:
            out = qloo.get("/v2/insights", params, run.cassette, f"{ctx.stage}_places", ctx.pid, run.id,
                           cache_ok=lambda o: ranking._ranking_rows(o) is not None)
        except BudgetExhausted as e:
            return {"status": "failed", "reason": f"not sent: {e}", "places": [], "listing": False, "method": method}
        rows = ranking._ranking_rows(out)
        rid = ranking.request_id("/v2/insights", params)
        if rows is None:  # unreadable is failed, never empty
            return {"status": "failed", "reason": out.get("error") or f"HTTP {out.get('status')}", "places": [],
                    "listing": False, "method": method, "request_id": rid}
        places = [markets.parse_place(e, i + 1) for i, e in enumerate(rows)]
        flat = len(places) >= 3 and len({p["affinity_in_request"] for p in places}) < 2
        kept = [{"code": f"{letter}{i}", "qloo_id": p["qloo_id"], "name": p["name"], "genres": p["genres"][:4],
                 "address": p["address"], "website": p["website"]} for i, p in enumerate(places, 1)]
        return {"status": "returned" if kept else "empty", "places": kept, "listing": flat, "method": method,
                "request_id": rid, "audience": audience}
    res, refused = memo(ctx, "qloo", key, go)
    if refused:
        return {"status": "refused", "reason": refused}
    return res


def ground(ctx: Ctx, question: str) -> dict:
    """One Converse call with Nova web grounding (each nova_grounding invocation is a billed search)."""
    model = make_model(ctx.run.cassette, f"{ctx.stage}_search", usage_sink(ctx, "search"), max_tokens=800,
                       admit=admitter(ctx, "search"))
    return model.client.converse(modelId=config.NOVA_MODEL, messages=[{"role": "user", "content": [{"text": question}]}],
                                 toolConfig={"tools": [{"systemTool": {"name": "nova_grounding"}}]},
                                 inferenceConfig={"maxTokens": 800})


def make_tools(ctx: Ctx) -> list:
    run, inp = ctx.run, ctx.inp
    label = (inp["ranking_signal"] or {}).get("label") or "the brand's profile"

    def nudge() -> str:
        c = counts(ctx.cr)
        if c["model"] >= ctx.nudge_at or ctx.tokens_in >= SUBMIT_AT_TOKENS:
            return f"\n\nLIMIT NEAR: call no more tools; call {ctx.submit} now."
        return ""

    def guard() -> str | None:
        run.check()
        if counts(ctx.cr)["model"] >= ctx.wrap_at or ctx.tokens_in >= SUBMIT_AT_TOKENS:
            return (f"Not run: the limit for {ctx.scope} is reached and no more tools will run. Call {ctx.submit} now "
                    "with what you have; anything not established is NEEDS_RESEARCH.")
        return None

    @tool
    def qloo_places(method: Literal["retail", "popup", "community"]) -> str:
        """Places Qloo relates to the brand's signal within 15 km of the city, filtered to one method's place kinds."""
        if w := guard():
            return w
        params = place_params(inp, method)
        if params is None:
            return "Not searched: there is no Qloo signal for this brand." + nudge()
        res = _search_places(ctx, method, params, CODE_LETTER[method], method, None)
        if res["status"] == "refused":
            return f"Not searched: {res['reason']}." + nudge()
        emit(ctx, "CANDIDATE" if res["status"] == "returned" else "UNAVAILABLE",
             f"Qloo {METHOD_LABELS[method].lower()} search: " + (f"{len(res['places'])} places" + (" (a listing, no taste order)"
                                                                                         if res["listing"] else "")
                                                               if res["status"] == "returned" else res["status"]),
             method=method)
        return _places_text(res, label) + nudge()

    @tool
    def qloo_audience_places(audience: str) -> str:
        """Places Qloo relates to one of the listed audiences near the city (the audience's exact name)."""
        if w := guard():
            return w
        offered = {a["name"].casefold(): a for a in ((inp["audience"] or {}).get("offered") or [])}
        a = offered.get(audience.strip().casefold())
        if not a:
            return "Not searched: that isn't one of the listed audiences." + nudge()
        ap = db.latest("audience_profile", ctx.pid)
        if ap and a["uuid"] in ap["removed"]:  # a removal after city start is refused before dispatch
            return "Not searched: the founder removed this audience." + nudge()
        urn, refused = memo(ctx, "urn", a["uuid"], lambda: ranking.resolve_urns(
            ctx.pid, run.cassette, run.id, [a["uuid"]], stage="research_urns").get(a["uuid"]) or {"state": "unresolved"})
        if refused:
            return f"Not searched: {refused}. Continue without more audience searches." + nudge()
        if (urn or {}).get("state") != "resolved":
            return "Not searched: Qloo has no audience id that matches this audience (unresolved)." + nudge()
        k = next(i for i, x in enumerate(inp["audience"]["offered"], 1) if x["uuid"] == a["uuid"])
        # one code space per audience (A2-1, A2-2 …): two audience searches never share a code (review P4-I4)
        res = _search_places(ctx, f"audience:{a['uuid']}", place_params(inp, None, urn["urn"]), f"A{k}-", None,
                             {"uuid": a["uuid"], "name": a["name"]})
        if res["status"] == "refused":
            return f"Not searched: {res['reason']}." + nudge()
        emit(ctx, "CANDIDATE", f"Qloo places for the audience {a['name']}: {len(res['places'])}")
        return _places_text(res, f"people in the audience {a['name']}") + nudge()

    @tool
    def web_search(query: str, method: Literal["retail", "creator", "popup", "community", "digital"]) -> str:
        """Search the web for local leads. Returns page addresses and a short answer that is only a lead."""
        if w := guard():
            return w
        city = f"{inp['name']}, {markets.COUNTRY_NAME.get(inp['country'], inp['country'])}"
        question = f"{query.strip()[:200]} — in {city}. Name specific places, groups or people, with their websites."

        def go():
            try:
                resp = ground(ctx, question)
            except (NoRecording, runner.Cancelled):
                raise
            except CityCap as e:
                return {"error": f"not run: {e}", "citations": [], "answer": ""}
            except BudgetExhausted as e:
                return {"error": f"not run: operating allowance reached ({e})", "citations": [], "answer": ""}
            except Exception as e:
                return {"error": type(e).__name__, "citations": [], "answer": ""}
            blocks = resp["output"]["message"]["content"]
            cites = [c["location"]["web"] for b in blocks for c in (b.get("citationsContent") or {}).get("citations", [])
                     if "web" in c.get("location", {})]
            uniq = list({c["url"]: c for c in cites if c.get("url")}.values())
            return {"citations": [{"url": c["url"], "domain": c.get("domain") or fetch.site_of(c["url"]),
                                   "social": is_social(c["url"])} for c in uniq],
                    "answer": " ".join(b.get("text", "") for b in blocks if "text" in b)[:1500], "method": method,
                    "query": question}
        res, refused = memo(ctx, "search", _key(question, method), go)
        if refused:
            return f"Not searched: {refused}." + nudge()
        if res.get("error"):
            emit_refusal(ctx, res["error"], method)
            return f"Search failed ({res['error']}). Treat what it would have found as unknown." + nudge()
        emit(ctx, "CANDIDATE", f"Web search ({METHOD_LABELS[method].lower()}): {len(res['citations'])} pages to read",
             method=method)
        pages = "\n".join(f"- {c['url']}" + (" (social platform: never read)" if c["social"] else "")
                          for c in res["citations"]) or "- none"
        return (f"PAGES:\n{pages}\n\nSEARCH ANSWER (leads only, not evidence; fetch a page before claiming anything):\n"
                f"{res['answer']}") + nudge()

    def _page_text(s: dict) -> str:
        code = source_code(s["url"])
        if not readable(s):
            return f"Not read: page unavailable ({s.get('error')}). Treat its contents as unknown, not absent."
        return f"SOURCE {code}\nURL {s['final_url']}\nTITLE {s['title']}\nTEXT\n{s['text'][:TOOL_CHARS]}"

    @tool
    def fetch_page(url: str, method: Literal["retail", "creator", "popup", "community", "digital"]) -> str:
        """Read one public web page. Only pages read this way can support a claim."""
        if w := guard():
            return w
        if is_social(url):
            return "Not read: social platforms are never read. A lead found only there stays not checked." + nudge()
        try:
            url = fetch.check_url(url)
        except fetch.FetchBlocked as e:
            return f"Not read: {e}." + nudge()
        res, refused = memo(ctx, "page", url, lambda: {
            **{k: v for k, v in load_page(run, ctx.pid, ctx.cid, url, f"{ctx.cr}:page:{url}", stage=f"{ctx.stage}_fetch").items()
               if k in ("id", "url", "final_url", "status", "error", "reused")}, "method": method})
        if refused:
            return f"Not read: {refused}. Work with the pages you have." + nudge()
        s = source(res["id"])
        path = fetch.site_of(s["final_url"]) + (s["final_url"].split(fetch.site_of(s["final_url"]), 1)[-1][:40])
        emit(ctx, "CHECKING" if readable(s) else "UNAVAILABLE",
             f"Read {path}" if readable(s) else f"Couldn't read {path} ({s['error']})", source_id=s["id"], method=method)
        if not readable(s):  # the stream hides it; Activity keeps it
            _activity(run, "Read a page", "page read", f"Couldn't read {path} ({s['error']})", status="UNAVAILABLE")
        return _page_text(s) + nudge()

    @tool
    def check_presence(lead_name: str, url: str) -> str:
        """Read a lead's own website to see whether it names an address in this city."""
        if w := guard():
            return w
        if is_social(url):
            return "Not checked: social platforms are never read." + nudge()
        try:
            url = fetch.check_url(url)
        except fetch.FetchBlocked as e:
            return f"Not checked: {e}." + nudge()

        def go():
            s = load_page(run, ctx.pid, ctx.cid, url, f"{ctx.cr}:presence:{url}", stage=f"{ctx.stage}_fetch")
            p = presence_of_lead({"name": lead_name, "own_site": fetch.site_of(url)}, [s], ctx.cid,
                                 s.get("error") if not readable(s) else None)
            return {"lead": lead_name, "url": url, "source_id": s["id"], "status": s["status"], "error": s.get("error"),
                    "presence": p["state"]}
        res, refused = memo(ctx, "presence", _key(_norm(lead_name), url), go)
        if refused:
            return f"Not checked: {refused}." + nudge()
        s = source(res["source_id"])
        emit(ctx, "CHECKING" if readable(s) else "UNAVAILABLE",
             f"Presence check for {lead_name}: " + ({"VERIFIED": "address confirmed", "SOURCES_DIFFER": "sources differ",
                                                     "CHECK_FAILED": "check failed"}.get(res["presence"], "location not confirmed")),
             source_id=s["id"])
        return f"PRESENCE: {res['presence'].replace('_', ' ')}\n" + _page_text(s) + nudge()

    return [qloo_places, qloo_audience_places, web_search, fetch_page, check_presence]


def _no_submission(e: Exception) -> bool:
    """The model answered in prose instead of calling the submission tool (seen live 27 Oct 2026, Nova, community).
    ponytail: matched on the SDK's message; the transcript keeps every tool result, so one forced submission recovers."""
    while e is not None:
        if "No valid tool use" in str(e):
            return True
        e = e.__cause__ or e.__context__
    return False


def run_agent(ctx: Ctx, row) -> dict:
    """The city agent, rebuilt from its last persisted transcript after a restart or a Continue; completed tool calls
    return their stored results. A cap refusal gets one forced submission, budgeted inside the cap."""
    inp = ctx.inp
    system = SYSTEM + (STANDIN_RULE if (inp["ranking_signal"] or {}).get("kind") == "standins" else "")
    saved = json.loads(row["transcript"]) if row["transcript"] else None
    model = make_model(ctx.run.cassette, "research", usage_sink(ctx, "model"), max_tokens=MAX_OUT, admit=admitter(ctx, "model"))
    agent = Agent(model=model, system_prompt=system, tools=make_tools(ctx), messages=saved, callback_handler=None,
                  tool_executor=SequentialToolExecutor())

    def persist(event: MessageAddedEvent) -> None:
        if event.message.get("role") == "user":  # a consistent point: the prompt, or a turn's tool results
            _set(ctx.pid, ctx.cid, ctx.rv, transcript=json.loads(json.dumps(agent.messages, default=str)))
    agent.hooks.add_callback(MessageAddedEvent, persist)
    try:
        res = agent(None if saved else prompt_of(inp), structured_output_model=CityResearchSubmission,
                    cancel_signal=ctx.run.cancel)
        ctx.run.check()
        out = res.structured_output
    except Exception as e:
        if not (_caused_by(e, CityCap) or _no_submission(e)):
            raise
        out = None
    ctx.run.check()
    if out is None:
        ctx.forced = True
        _activity(ctx.run, f"Submit {inp['name']}", "city researcher", "A limit was reached: submitted with what it had",
                  "PARTIAL")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            out = agent.structured_output(CityResearchSubmission, FORCED)
    return out.model_dump()


def _caused_by(e: BaseException | None, cls) -> bool:
    seen = 0
    while e is not None and seen < 8:
        if isinstance(e, cls):
            return True
        e, seen = e.__cause__ or e.__context__, seen + 1
    return False


# ---- the checker (city_check@1) ----
CHECKER = ("You check whether a verbatim quote from a web page supports a claim about a subject in a city, exactly as "
           "the claim is shown to a founder. Each claim reads '{subject} in {city}: {text}'. SUPPORTS: the quote itself "
           "states the whole claim; a paraphrase or translation is fine. PARTIAL: it states only part, or the claim needs "
           "an inference beyond the quote. UNRELATED: the quote does not establish the claim. CONTRADICTS: the quote says "
           "the opposite of another listed claim about the same subject (for example that a venue closed, when another "
           "claim says it hosts events); give that claim's id in `contradicts`. In unsupported_parts list every part of "
           "the claim the quote does not itself state (what it sells, where, since when, any qualifier); leave it empty "
           "only when the quote states the whole claim. First restate the full claim in claim_as_read, naming every "
           "place, activity, quality and audience it mentions, then check each one against the quote. Each claim also names a way in; set shows_way_in true only when "
           "the quote itself shows it (for example that the shop stocks other brands, or that the venue hosts events or "
           "rents space). Judge only from the quote, never from what you know about the place.")
WAY_IN = {"retail": "the shop stocks other brands or sells this kind of product", "popup": "the venue hosts events or rents space",
          "community": "the group or activity meets in the city", "creator": "a local creator publishes here",
          "digital": "people in the city share an interest the brand fits"}


class CityVerdict(BaseModel):
    claim_id: str
    claim_as_read: str = ""
    verdict: Literal["SUPPORTS", "PARTIAL", "UNRELATED", "CONTRADICTS"]
    contradicts: str | None = None
    unsupported_parts: list[str] = Field(default_factory=list)
    shows_way_in: bool = False  # fail closed: an omitted judgment never grounds an approach
    reason: str


class CityVerdicts(BaseModel):
    verdicts: list[CityVerdict]


def judge(ctx: Ctx, listing: str) -> CityVerdicts | None:
    agent = Agent(model=make_model(ctx.run.cassette, "research_check", usage_sink(ctx, "check"), max_tokens=3000,
                                   admit=admitter(ctx, "check")), system_prompt=CHECKER, callback_handler=None)
    res = agent(f"Judge each claim against its quote:\n{listing}", structured_output_model=CityVerdicts,
                cancel_signal=ctx.run.cancel)
    return res.structured_output


PROSE = ("You sort sentences a founder will read about one way into a city. Each comes with the checked facts its "
         "approach rests on. PROPOSAL: it proposes something to do or try (an action, a test, a collaboration) and states "
         "nothing about the world beyond those facts. QUESTION: it names something unknown or still to check. "
         "SUPPORTED_FACT: every statement of fact in it is stated by its checked facts (a paraphrase is fine). "
         "UNSUPPORTED_FACT: it states anything about a place, person, group, its members or audience, or the city that its "
         "checked facts do not state: what they are like, what they do, offer or care about, or any number. A proposal "
         "that also states an unsupported fact is UNSUPPORTED_FACT. Judge only from the listed facts.")


class ProseVerdict(BaseModel):
    id: str
    kind: Literal["PROPOSAL", "QUESTION", "SUPPORTED_FACT", "UNSUPPORTED_FACT"]
    reason: str = ""


class ProseVerdicts(BaseModel):
    verdicts: list[ProseVerdict]


PROSE_BATCH, PROSE_CALLS = 25, 2


def judge_prose(ctx: Ctx, listing: str) -> ProseVerdicts | None:
    agent = Agent(model=make_model(ctx.run.cassette, "research_check", usage_sink(ctx, "check"), max_tokens=3000,
                                   admit=admitter(ctx, "check")), system_prompt=PROSE, callback_handler=None)
    res = agent(f"Sort each sentence:\n{listing}", structured_output_model=ProseVerdicts, cancel_signal=ctx.run.cancel)
    return res.structured_output


ASSERTS = r"(?:is|are|was|has|have|hosts?|sells?|stocks?|offers?|runs?|holds?|carries|features?|serves?|attracts?|draws?|" \
          r"welcomes?|specializes|specialises|focuses|caters|organi[sz]es)"
STOP = set("this that with from their there they them have will would could should about into over more most also "
           "very such than then when where which while your ours city".split())


def asserts_unchecked(sentence: str, leads: list[dict], facts: list[str]) -> bool:
    """A sentence stating something about a named lead ("Maple & Co hosts Saturday tastings") whose content words are
    not in that approach's checked claims. Dropped whatever the prose judge says (review P4-I1)."""
    known = set(_norm(" ".join(facts)).split())
    # a presupposition about a place: "their retail area", "its members" (LIVE L4-B v2: a gym's "retail area")
    for m in re.finditer(r"\b(?:their|its)\s+((?:[a-z]+\s+){0,2}[a-z]+)", _norm(sentence)):
        if any(len(w) >= 4 and w not in STOP and w not in known for w in m.group(1).split()):
            return True
    for lead in leads:
        m = re.search(rf"{re.escape(_norm(lead['name']))}\s+{ASSERTS}\b(.*)", _norm(sentence))
        if m and any(len(w) >= 4 and w not in STOP and w not in known for w in m.group(1).split()):
            return True
    return False


def prose_check(ctx: Ctx, approaches: list[dict], claims: dict[str, dict], faults: list[dict],
                leads: list[dict] = ()) -> None:
    """Keeps a model sentence only when it is a proposal, a question, or a fact its approach's own checked claims state.
    Anything else, or anything left unjudged, is dropped (fail closed); an emptied field gets Landfall's own text."""
    items: list[tuple[dict, str, int, str]] = []
    for a in approaches:
        if a["prose_source"] == "DETERMINISTIC":
            continue
        if a["name_source"] == "MODEL":
            items.append((a, "name", 0, a["name"]))
        if a["concept"] and a["concept_source"] == "MODEL":
            items += [(a, "concept_name", 0, a["concept"]["name"])] if a["concept"]["name"] else []
            items += [(a, "concept_line", i, s) for i, s in enumerate(SENT.split(a["concept"]["line"] or "")) if s.strip()]
        items += [(a, "hypotheses", i, h) for i, h in enumerate(a["hypotheses"])]
        if a["reason"] and a["reason_source"] in ("MODEL", "YOU_SAID"):
            items += [(a, "reason", i, s) for i, s in enumerate(SENT.split(a["reason"])) if s.strip()]
        items += [(a, "open_requirements", i, r["text"]) for i, r in enumerate(a["open_requirements"])]
    if not items:
        return
    kinds: dict[str, str] = {}
    for b in range(0, len(items), PROSE_BATCH):
        batch = items[b:b + PROSE_BATCH]
        lines = []
        for n, (a, field, i, text) in enumerate(batch, b + 1):
            facts = [claims[c]["text"] for c in a["claim_ids"] if c in claims and claims[c]["state"] == "SUPPORTED"]
            if a["reason_source"] == "YOU_SAID" and ctx.inp["context"]:
                facts.append(f"The founder said: {ctx.inp['context']}")
            lines.append(f"- id=s{n} | {METHOD_LABELS[a['method']].lower()} · {field.replace('_', ' ')} | sentence: \"{text}\""
                         f" | checked facts: {' / '.join(facts) or 'none'}")

        def go(listing="\n".join(lines)):
            try:
                out = judge_prose(ctx, listing)
            except (NoRecording, runner.Cancelled):
                raise
            except Exception as e:
                return {"error": f"the prose check failed ({type(e).__name__})", "verdicts": []}
            return {"verdicts": [v.model_dump() for v in out.verdicts] if out else []}
        res, refused = memo(ctx, "prose", str(b // PROSE_BATCH), go)
        ctx.run.check()
        kinds.update({v["id"]: v["kind"] for v in (res or {}).get("verdicts", [])})
    keep: dict[int, dict] = {}
    for n, (a, field, i, text) in enumerate(items, 1):
        kind = kinds.get(f"s{n}")
        facts = [claims[c]["text"] + " " + claims[c]["quote"] for c in a["claim_ids"]
                 if c in claims and claims[c]["state"] == "SUPPORTED"]
        if kind and asserts_unchecked(text, leads, facts):
            kind = "UNSUPPORTED_FACT"
        ok = kind in ("PROPOSAL", "QUESTION", "SUPPORTED_FACT")
        if not ok:
            faults.append({"field": f"{a['method']} {field}", "reason": f"\"{text[:80]}\": "
                           + ("states a fact its checked claims don't" if kind else "not judged; left out")})
        keep.setdefault(id(a), {}).setdefault(field, {})[i] = ok
    for a in approaches:
        k = keep.get(id(a))
        if not k:
            continue
        if "name" in k and not k["name"][0]:
            a["name"] = f"{METHOD_LABELS[a['method']]}{(a.get('ground') or {}).get('with') or ' in ' + ctx.inp['name']}"
        if a["concept"] and ("concept_name" in k or "concept_line" in k):
            line = " ".join(s for i, s in enumerate(SENT.split(a["concept"]["line"] or "")) if k.get("concept_line", {}).get(i))
            name_ok = k.get("concept_name", {}).get(0, False)
            if not (name_ok and line) and a.get("ground"):  # a READY concept never shows an unsupported fact
                a["concept"] = dict(zip(("name", "line"), own_concept(a["method"], a["ground"])))
                a["concept_source"] = "DETERMINISTIC"
            else:
                a["concept"] = {"name": a["concept"]["name"] if name_ok else None, "line": line or None}
        a["hypotheses"] = [h for i, h in enumerate(a["hypotheses"]) if k.get("hypotheses", {}).get(i, True)]
        a["open_requirements"] = [r for i, r in enumerate(a["open_requirements"]) if k.get("open_requirements", {}).get(i, True)]
        if "reason" in k:
            kept = " ".join(s for i, s in enumerate(SENT.split(a["reason"])) if k["reason"].get(i))
            if not kept:
                kept, a["reason_source"] = ("Not established yet: its claims don't show this way in." if a["claim_ids"]
                                            else "No evidence found in this research."), "DETERMINISTIC"
            a["reason"] = kept


def check_claims(ctx: Ctx, claims: list[dict]) -> dict[str, dict]:
    """≤ 12 claims per call, ≤ 3 calls per city. Fails closed per claim: a missing verdict, malformed output, a denied
    call or a transport failure leaves that claim TO_CHECK with the reason; it never fails the city."""
    todo = [c for c in claims if c["quote_found"]]
    out: dict[str, dict] = {}
    for b in range(0, len(todo), CHECK_BATCH):
        batch = todo[b:b + CHECK_BATCH]
        listing = "\n".join(f"- id={c['cid']} | {c['subject_name']} in {ctx.inp['name']}: {c['text']} | way in: "
                            f"{WAY_IN.get(c['method'], 'none')} | quote: \"{c['quote']}\"" for c in batch)

        def go(listing=listing):
            try:
                out = judge(ctx, listing)
            except (NoRecording, runner.Cancelled):
                raise
            except Exception as e:
                return {"error": "the check was not allowed" if _caused_by(e, BudgetExhausted) else
                        f"the checker failed ({type(e).__name__})", "verdicts": []}
            if out is None:
                return {"error": "the checker gave no answer", "verdicts": []}
            return {"verdicts": [v.model_dump() for v in out.verdicts]}
        res, refused = memo(ctx, "check", str(b // CHECK_BATCH), go)
        ctx.run.check()
        for c in batch:
            v = next((x for x in (res or {}).get("verdicts", []) if x["claim_id"] == c["cid"]), None)
            out[c["cid"]] = v or {"error": refused or (res or {}).get("error") or "no verdict for this claim"}
    return out


# ---- the validator (R2): every displayed field is checked; facts resolve to checked claims ----
URL = re.compile(r"https?://\S+|www\.\S+")
CITES = re.compile(r"\s*[(\[]\s*(?:see\s+)?(?:claims?|c)?\s*#?\d+(?:\s*(?:,|and|&|-)\s*#?\d+)*\s*[)\]]|"
                   r"\b(?:see\s+)?claims?\s+#?\d+(?:\s*(?:,|and|&)\s*#?\d+)*", re.I)
SENT = re.compile(r"(?<=[.!?])\s+")
NUM = re.compile(r"\b\d[\d,.]*\b")


def window_of(quote: str, text: str) -> str | None:
    nt, nq = _norm(text), _norm(quote)
    i = nt.find(nq) if len(nq) >= 8 else -1
    return None if i < 0 else nt[max(0, i - 300): i + len(nq) + 300]


def _has(text: str, name: str) -> bool:
    n = _norm(name)
    return len(n) >= 3 and f" {n} " in f" {_norm(text)} "


class Cleaner:
    """The slice-3.5 guard, URL removal and the stand-in proxy rule on every displayed text; a sentence naming a lead
    without a checked claim about it is rewritten "{lead}: not yet checked"; an audience never used, or removed, and
    another city's name are dropped. An unsupported factual assertion is never relabeled HYPOTHESIS to keep it."""

    def __init__(self, ctx: Ctx, leads: list[dict], used_audiences: set[str]):
        self.ctx, self.leads = ctx, leads
        ap = db.latest("audience_profile", ctx.pid)
        removed = set((ap or {}).get("removed", []))
        offered = {x["uuid"]: x["name"] for x in ((ctx.inp["audience"] or {}).get("offered") or [])}
        self.allowed = {u: n for u, n in offered.items() if u not in removed}
        self.banned = [x["name"] for x in (ap or {}).get("audiences", []) if x["uuid"] not in self.allowed]
        self.mentioned = set(used_audiences)  # tool use, plus offered audiences named in kept text
        self.standins = (ctx.inp["ranking_signal"] or {}).get("kind") == "standins"

    def __call__(self, text: str | None, ok_keys: set[str]) -> tuple[str, list[str]]:
        if not text:
            return "", []
        faults, out = [], []
        for s in SENT.split(CITES.sub("", URL.sub("", text)).strip()):
            s = s.strip()
            if not s:
                continue
            plain = s
            for lead in self.leads:
                plain = re.sub(re.escape(lead["name"]), " ", plain, flags=re.I)
            if why := markets.guard(s):
                faults.append(why)
                continue
            if self.standins and scan.PROXY.search(s):
                faults.append("describes the stand-ins' audiences as your customers")
                continue
            if any(_has(s, n) for n in self.banned if n):
                faults.append("uses an audience this research didn't use or you removed")
                continue
            if set(markets.names_in(plain, markets.ORDER)) - {self.ctx.cid}:
                faults.append("names another city")
                continue
            bad = [lead for lead in self.leads if _has(s, lead["name"]) and lead["key"] not in ok_keys]
            if bad:
                faults.append(f"names {bad[0]['name']} without a checked claim")
                s = f"{bad[0]['name']}: not yet checked."
            self.mentioned |= {u for u, n in self.allowed.items() if n and _has(s, n)}
            out.append(s if s[-1] in ".!?" else s + ".")
        return " ".join(dict.fromkeys(out)), faults


def _fact_ids(inp: dict, used: list[str]) -> dict:
    facts = [f for f in inp["facts"] if f["state"] in ("SUPPORTED", "STATED")]
    out = {}
    for ref in used:
        m = re.fullmatch(r"F?(\d+)", (ref or "").strip(), re.I)
        if m and 1 <= int(m.group(1)) <= len(facts):
            f = facts[int(m.group(1)) - 1]
            out[f["claim_id"]] = f["value"]
    return out


KINDS = ("shop", "venue", "group", "creator", "online", "other")


def normalized(sub: dict) -> dict:
    """Method, status and kind as the validator knows them; an approach for no known method is dropped (and reported)."""
    out = {**sub, "leads": [{**x, "kind": x.get("kind") if x.get("kind") in KINDS else "other"} for x in sub.get("leads") or []],
           "claims": [{**c, "method": norm_method(c.get("method")) or ""} for c in sub.get("claims") or []],
           "approaches": [{**a, "method": norm_method(a.get("method")), "status": norm_status(a.get("status"))}
                          for a in sub.get("approaches") or []]}
    out["dropped_approaches"] = [a.get("method") for a in sub.get("approaches") or [] if not norm_method(a.get("method"))]
    out["approaches"] = [a for a in out["approaches"] if a["method"]]
    return out


def validate(ctx: Ctx, sub: dict) -> dict:
    """Submission → claims, leads, approaches, learned. Deterministic except the checker's verdicts (memoized)."""
    cid, inp = ctx.cid, ctx.inp
    sub = normalized(sub)
    # pages this city read, and Qloo places it saw
    page_steps = steps(ctx.cr, "page") + steps(ctx.cr, "presence")
    pages = {}
    for st in page_steps:
        s = source(st.get("id") or st.get("source_id"))
        if s:
            pages[source_code(s["url"])] = s
    qloo_steps = steps(ctx.cr, "qloo")
    by_code: dict[str, list[dict]] = {}
    for r in qloo_steps:
        for p in r.get("places", []):
            by_code.setdefault(p["code"], []).append({**p, "listing": r["listing"]})
    # a code resolves to its one original entity; a code two responses share is ambiguous and resolves to nothing
    places = {c: ps[0] for c, ps in by_code.items() if len({p["qloo_id"] for p in ps}) == 1}
    name_count: dict[str, int] = {}
    for p in (p for ps in by_code.values() for p in ps):
        name_count[_norm(p["name"])] = name_count.get(_norm(p["name"]), 0) + 1
    used_aud = {r["audience"]["uuid"] for r in qloo_steps if r.get("audience")}
    failed_checks = {_norm(r["lead"]): r.get("error") or f"HTTP {r['status']}" for r in steps(ctx.cr, "presence")
                     if r.get("error")}
    faults: list[dict] = []

    # leads: from Qloo, or named on a page this city read; anything else is dropped
    leads, by_ref = [], {}
    for li in sub.get("leads", []):
        ref = (li.get("qloo_ref") or "").strip("[] ")
        q = places.get(ref)
        if q and not same_name(q["name"], li["name"]):  # the reference points at a different place than the lead names
            faults.append({"field": "lead", "reason": f"{li['name']}: its Qloo reference {ref} is {q['name']}; not used"})
            q = None
        elif ref and ref in by_code and not q:
            faults.append({"field": "lead", "reason": f"{li['name']}: its Qloo reference {ref} is ambiguous; not used"})
        elif not q:  # no usable reference: exactly one place of this exact name in the city's Qloo results
            named = {p["qloo_id"]: p for ps in by_code.values() for p in ps if _slug(p["name"]) == _slug(li["name"])}
            q = next(iter(named.values())) if len(named) == 1 else None
        named_on = [s for s in pages.values() if readable(s) and _in(li["name"], s["text"]) >= 0]
        if q:
            lead = {"name": q["name"], "type": li["kind"], "origin": "QLOO", "qloo_id": q["qloo_id"],
                    "address": q.get("address"), "qloo_website": q.get("website"), "listing": q["listing"],
                    "chain": name_count.get(_norm(q["name"]), 0) >= 2,
                    "first_domain": fetch.site_of(named_on[0]["final_url"]) if named_on else None}
        elif named_on:
            lead = {"name": li["name"], "type": li["kind"], "origin": "PAGE", "qloo_id": None, "address": None,
                    "qloo_website": None, "listing": False, "chain": name_count.get(_norm(li["name"]), 0) >= 2,
                    "first_domain": fetch.site_of(named_on[0]["final_url"])}
        else:
            faults.append({"field": "lead", "reason": f"{li['name']}: not in a Qloo result or on a page read; dropped"})
            continue
        lead["key"] = lead_key(lead, cid)
        lead["model_website"] = li.get("website")
        existing = next((x for x in leads if x["key"] == lead["key"]), None)
        by_ref[li["ref"]] = existing or lead
        if not existing:
            leads.append(lead)

    # claims: bound to a subject and this city; locality on the quote's own window
    claims = []
    for i, c in enumerate(sub.get("claims", []), 1):
        s = pages.get((c.get("source") or "").strip())
        subj = by_ref.get(c["subject"]) if c["subject"] != "city" else None
        if c["subject"] != "city" and subj is None:
            faults.append({"field": f"claim {i}", "reason": "its subject isn't a known lead; dropped"})
            claims.append(None)
            continue
        if s is None or not readable(s):
            faults.append({"field": f"claim {i}", "reason": "cites a page this city didn't read; dropped"})
            claims.append(None)
            continue
        found = quote_found(c["quote"], s["text"])
        win = window_of(c["quote"], s["text"]) if found else None
        local = bool(win) and (names_city(win, cid) or bool(subj and _street(subj.get("address"))
                                                             and _street(subj["address"]) in win))
        own = bool(subj) and bool((subj.get("qloo_website") and fetch.same_site(s["final_url"], fetch.site_of(subj["qloo_website"])))
                                  or named_domain(subj["name"], s["final_url"]))
        if found and not local and own and names_city(s["text"], cid) and STREET_NO.search(_norm(s["text"])):
            local = True  # the lead's own site, giving an address in the city
        # a lead's claim needs the lead in its own passage, or its own site (LIVE L4-B: an Art Shed quote bound to Target)
        about = not subj or own or bool(win and _has(win, subj["name"]))
        claims.append({"cid": f"c{i}", "n": i, "subject": subj["key"] if subj else "city",
                       "subject_name": subj["name"] if subj else inp["name"], "method": c["method"],
                       "text": c["text"], "quote": c["quote"], "source_id": s["id"], "quote_found": found,
                       "window": win, "locality": local, "about_subject": about})
    live = [c for c in claims if c]
    verdicts = check_claims(ctx, live)
    for c in live:
        v = verdicts.get(c["cid"]) or {"error": "no verdict for this claim"}
        c["support"] = {k: v.get(k) for k in ("verdict", "contradicts", "reason", "unsupported_parts", "shows_way_in")} if "verdict" in v else \
            {"verdict": None, "reason": f"not checked: {v['error']}"}
        if not c["quote_found"]:
            c["state"], c["reason"] = "TO_CHECK", "The quote isn't on the page, so the claim is not established."
        elif "verdict" not in v:
            c["state"], c["reason"] = "TO_CHECK", f"Not checked: {v['error']}."
        elif v["verdict"] == "SUPPORTS" and v.get("unsupported_parts"):
            c["state"], c["reason"] = "TO_CHECK", ("The quote supports part of this but not: " + "; ".join(v["unsupported_parts"])
                                                   + ".")
        elif v["verdict"] == "SUPPORTS" and not c["about_subject"]:
            c["state"], c["reason"] = "TO_CHECK", f"The quote's passage doesn't name {c['subject_name']}."
        elif v["verdict"] == "SUPPORTS" and not c["locality"]:
            c["state"], c["reason"] = "TO_CHECK", (f"The quote supports it, but its passage doesn't place it in "
                                                   f"{inp['name']}.")
        elif v["verdict"] == "SUPPORTS":
            c["state"], c["reason"] = "SUPPORTED", v["reason"]
        elif v["verdict"] == "CONTRADICTS":
            c["state"], c["reason"] = "TO_CHECK", v["reason"]  # becomes CONTESTED below when it names a same-subject claim
        else:
            c["state"], c["reason"] = "TO_CHECK", f"The quote doesn't establish this: {v['reason']}"
    by_cid = {c["cid"]: c for c in live}
    for c in live:
        other = by_cid.get((c["support"].get("contradicts") or "").strip())
        if c["support"].get("verdict") == "CONTRADICTS" and other and other["subject"] == c["subject"] and other is not c:
            for x in (c, other):
                x["state"], x["contested_with"] = "CONTESTED", (other if x is c else c)["cid"]

    # leads: own site, presence and contacts
    for lead in leads:
        own = fetch.site_of(lead["qloo_website"]) if lead.get("qloo_website") else None
        checked = [r["url"] for r in steps(ctx.cr, "presence") if _norm(r["lead"]) == _norm(lead["name"])]
        own = own or next((d for u in [lead.get("model_website"), *checked] if (d := named_domain(lead["name"], u))), None)
        lead["own_site"] = own
        lead["website"] = lead.get("qloo_website") or (f"https://{own}" if own else None)
        pres = presence_of_lead(lead, list(pages.values()), cid, failed_checks.get(_norm(lead["name"])))
        lead["presence"], lead["presence_check"] = pres["state"], {**pres, "at": db.now()}
        contact = None
        for s in pages.values():
            if own and readable(s) and fetch.same_site(s["final_url"], own):
                found = json.loads(s["contacts"] or "[]")
                if found:
                    contact = {**found[0], "source_id": s["id"]}
                    break
        lead["contact"] = contact  # a magazine, directory or listing page never supplies one
        lead["willingness"] = "UNKNOWN"
        lead["claim_ids"] = [c["cid"] for c in live if c["subject"] == lead["key"]]

    clean = Cleaner(ctx, leads, used_aud)
    supported = {c["cid"] for c in live if c["state"] == "SUPPORTED"}
    social_only = any(c["social"] for r in steps(ctx.cr, "search") for c in r.get("citations", [])
                      if r.get("method") == "creator")

    approaches, seen = [], set()
    ruled = {a["method"] for a in sub.get("approaches", []) if a.get("from_your_note") and inp["context"]
             and a["status"] in ("NOT_SUITABLE_NOW", "HOLD")}
    for a in sub.get("approaches", []):
        if a["method"] in seen:
            faults.append({"field": f"approach {a['method']}", "reason": "a second entry for this method; ignored"})
            continue
        seen.add(a["method"])
        approaches.append(_approach(ctx, a, claims, by_ref, clean, supported, social_only, faults, ruled=ruled,
                                    used_aud=used_aud))
    for m in METHODS:
        if m not in seen:
            approaches.append(_approach(ctx, {"method": m, "status": "NEEDS_RESEARCH", "name": f"{METHOD_LABELS[m]} in "
                                              f"{inp['name']}", "reason": "", "claim_refs": [], "lead_refs": []},
                                        claims, by_ref, clean, supported, social_only, faults, missing=True))
    approaches.sort(key=lambda x: METHODS.index(x["method"]))
    ready = [x for x in approaches if x["status"] == "READY_TO_COMPARE"]
    for x in ready[3:]:
        x["status"], x["reason"], x["reason_source"] = "HELD", "Three are ready here; held for later.", "DETERMINISTIC"

    # every other displayed sentence is judged against the particular checked claims its approach cites (review P4-I1)
    prose_check(ctx, approaches, {c["cid"]: c for c in live}, faults, leads)
    # learned: built from the checked claims themselves, never from model prose (review P4-I1)
    if sub.get("learned"):
        faults.append({"field": "learned", "reason": "the model's paragraph is not shown; Landfall writes it from the checked claims"})
    learned, source_kind = deterministic_learned(ctx, leads, live, approaches), "DETERMINISTIC"
    return {"claims": live, "leads": leads, "approaches": approaches, "learned": learned, "learned_source": source_kind,
            "faults": faults, "facts_used": _fact_ids(inp, sub.get("facts_used", [])), "audiences_used": sorted(clean.mentioned)}


METHOD_WORDS = {"popup": r"\bpop[- ]?ups?\b", "creator": r"\b(creators?|influencers?)\b",
                "retail": r"\b(stockists?|retail(ers)?|wholesale)\b", "community": r"\b(community|run club|meetups?)\b",
                "digital": r"\b(digital|online ads?|social ads?)\b"}


def _drop_ruled(text: str, ruled: set[str], method: str) -> tuple[str, list[str]]:
    """A sentence proposing a way in the founder's note ruled out is dropped from every other approach (LIVE L4-A:
    a community concept built on a "pop-up boutique village" after "We won't do pop-ups this year")."""
    out, faults = [], []
    for s in SENT.split(text or ""):
        hit = next((m for m in ruled if m != method and re.search(METHOD_WORDS[m], s, re.I)), None)
        if hit:
            faults.append(f"proposes a {METHOD_LABELS[hit].lower()} you ruled out")
        elif s.strip():
            out.append(s.strip())
    return " ".join(out), faults


def _approach(ctx, a, claims, by_ref, clean, supported, social_only, faults, missing=False, ruled=frozenset(),
              used_aud=frozenset()) -> dict:
    method = a["method"]
    cited = [claims[n - 1] for n in a.get("claim_refs") or [] if isinstance(n, int) and 1 <= n <= len(claims) and claims[n - 1]]
    lead_objs = [by_ref[r] for r in a.get("lead_refs") or [] if r in by_ref]
    for c in cited:  # a cited claim's lead is a partner of the approach
        lead = next((x for x in by_ref.values() if x["key"] == c["subject"]), None)
        if lead and lead not in lead_objs:
            lead_objs.append(lead)
    keys_in = {x["key"] for x in lead_objs}
    ok_keys = {c["subject"] for c in cited if c["cid"] in supported}
    # READY rests on a checked claim for this way in about one of its own partners, never a city-wide fact about some
    # other place; digital rests on a checked in-city claim and an audience this research actually used (LIVE L4-A)
    relevant = [c for c in cited if c["cid"] in supported and c["method"] == method and c["support"].get("shows_way_in")
                and (c["subject"] in keys_in if method != "digital" else bool(used_aud))]
    contested = [c for c in cited if c["state"] == "CONTESTED"]
    name, f1 = clean(a.get("name"), ok_keys)
    concept_name, f2 = clean(a.get("concept_name"), ok_keys)
    concept_line, f3 = clean(a.get("concept_line"), ok_keys)
    hyps = [h for h in (clean(x, ok_keys)[0] for x in a.get("hypotheses") or []) if h]
    reqs = [{"text": r, "state": "UNKNOWN"} for r in (clean(x, ok_keys)[0] for x in a.get("open_requirements") or []) if r]
    reason, f4 = clean(a.get("reason"), ok_keys)
    concept_name, f5 = _drop_ruled(concept_name, ruled, method)
    concept_line, f6 = _drop_ruled(concept_line, ruled, method)
    hyps = [h for h in (_drop_ruled(x, ruled, method)[0] for x in hyps) if h]
    for f, field in ((f1, "name"), (f2, "concept"), (f3, "concept"), (f4, "reason"), (f5, "concept"), (f6, "concept")):
        for why in f:
            faults.append({"field": f"{method} {field}", "reason": why})
    context = ctx.inp["context"]
    from_note = bool(a.get("from_your_note") and context)
    status, reason_source = a["status"], "MODEL"
    concept_source, ground = "MODEL", None
    if status == "DEVELOP":
        if contested:
            status, reason, reason_source = "HELD", "Sources differ on what it rests on; held until that is settled.", "DETERMINISTIC"
        elif relevant:
            status = "READY_TO_COMPARE"
            lead = next(x for x in lead_objs if x["key"] == relevant[0]["subject"]) if method != "digital" else None
            ground = {"with": f" with {lead['name']}" if lead else f" in {ctx.inp['name']}", "text": relevant[0]["text"]}
            if f1 or f2 or f3 or f5 or f6 or not (concept_name or concept_line):  # rewritten or emptied: Landfall's own
                name = name if not f1 and name else f"{METHOD_LABELS[method]}{ground['with']}"
                concept_name, concept_line = own_concept(method, ground)
                concept_source = "DETERMINISTIC"
        else:
            status, reason_source = "NEEDS_RESEARCH", "DETERMINISTIC"
            reason = ("Not established yet: none of its evidence is a checked local source about one of its partners for "
                      "this way in." if method != "digital" or used_aud else
                      "Not established yet: no audience was searched in this city, so its people's interests are unknown."
                      ) if cited else "No evidence found in this research."
    elif status == "HOLD":
        status = "NOT_SUITABLE_NOW" if from_note else "HELD"  # the founder's note decides, it doesn't hold
    if status not in ("READY_TO_COMPARE",) and reason_source == "MODEL":
        if from_note:
            reason_source = "YOU_SAID"
        elif not cited or not reason:
            reason, reason_source = "No evidence found in this research.", "DETERMINISTIC"
            if status in ("NOT_SUITABLE_NOW", "HELD"):
                status = "NEEDS_RESEARCH"  # "not suitable" or "held" needs evidence or the founder's note
    if method == "creator" and status != "READY_TO_COMPARE" and social_only and reason_source == "DETERMINISTIC":
        reason += " Only social-platform profiles came up, and those aren't read."
    if missing:
        reason = "The Researcher didn't assess this way in. No evidence found in this research."
    ap = (ctx.inp["audience"] or {}).get("profile")
    auds = [u for u, n in clean.allowed.items() if n in (a.get("audiences") or [])]
    clean.mentioned |= set(auds)
    keys = sorted(x["key"] for x in lead_objs)
    concept_key = _norm(concept_name or name or "") + "|" + ",".join(keys)
    return {"id": f"apr_{_key(ctx.cr, method)}", "city_id": ctx.cid, "method": method, "status": status,
            "name": name or f"{METHOD_LABELS[method]} in {ctx.inp['name']}",
            "concept": {"name": concept_name, "line": concept_line} if status == "READY_TO_COMPARE" and (concept_name or concept_line) else None,
            "grounding_claim_ids": [c["cid"] for c in relevant] if status == "READY_TO_COMPARE" else [],
            "claim_ids": [c["cid"] for c in cited], "lead_keys": keys, "hypotheses": hyps, "open_requirements": reqs,
            "reason": reason if status != "READY_TO_COMPARE" else None, "reason_source": reason_source,
            "audience_profile": ap and auds and {**ap, "keys": auds}, "concept_key": concept_key,
            "prose_source": "MODEL" if not missing else "DETERMINISTIC", "concept_source": concept_source,
            "name_source": "MODEL" if name and not f1 else "DETERMINISTIC", "ground": ground}


def own_concept(method: str, ground: dict) -> tuple[str, str]:
    return (f"{METHOD_LABELS[method]} test{ground['with']}",
            f"A proposal resting on one checked fact: {ground['text'].rstrip('.')}.")


def deterministic_learned(ctx: Ctx, leads: list[dict], claims: list[dict], approaches: list[dict]) -> str:
    """"Checked: 3 retail leads, 1 venue; not established: creators." From the checked claims only."""
    kinds = {"retail": ("retail lead", "retail leads"), "popup": ("venue", "venues"),
             "community": ("community group", "community groups"), "creator": ("creator", "creators"),
             "digital": ("online lead", "online leads")}
    facts = []
    for c in claims:
        if c["state"] == "SUPPORTED" and len(facts) < 4:
            text = c["text"].strip().rstrip(".")
            facts.append(text if c["subject"] == "city" or same_name(c["subject_name"], text[:len(c["subject_name"]) + 2])
                         or _has(text, c["subject_name"]) else f"{c['subject_name']}: {text}")
    missing = [kinds[a["method"]][1] for a in approaches if a["status"] != "READY_TO_COMPARE"
               and not any(c["state"] == "SUPPORTED" and c["method"] == a["method"] for c in claims)]
    head = ("Checked on local pages: " + ". ".join(facts) + ".") if facts else "Nothing was checked against a local page yet."
    return head + (f" Not established: {', '.join(missing)}." if missing else "")


# ---- consumed values and staleness (R5) ----
def consumed_of(inp: dict, v: dict) -> dict:
    rec = inp["recommendation"]
    return {"fact_ids": v["facts_used"], "goal": inp["goal"], "presence": inp["presence"],
            "ranking_signal": sorted((inp["ranking_signal"] or {}).get("entity_ids") or []),
            "place_signal": sorted(s["qloo_id"] for s in inp["place_signal"]["signals"]),
            "audiences_used": v["audiences_used"], "context": inp["context"], "selection": inp["selection"],
            "card": rec and {"position_line": rec["position_line"], "group": rec["group"]}}


STALE_WORDS = {"fact": "A fact this research used changed", "audience": "You removed an audience this research used",
               "ranking": "Qloo's ranking or your goal changed since this research",
               "context": "Your note changed after {city} started"}


def stale_of(pid: str, cid: str, consumed: dict) -> list[str]:
    """Compares the values this city actually used with today's, never whole record versions."""
    out = []
    brief = db.latest("brief", pid)
    now = {i: db.get_record(i)["value"] for i in (brief or {}).get("facts", [])}
    if any(now.get(k) != v for k, v in consumed["fact_ids"].items()):
        out.append("fact")
    ap = db.latest("audience_profile", pid)
    if ap and set(ap["removed"]) & set(consumed["audiences_used"]):
        out.append("audience")
    sig = ranking.ranking_signal(pid)
    g = ranking.goal_of(pid)
    _, card, _ = card_of(pid, cid)
    now_card = card and {"position_line": card["position_line"], "group": card["group"]}
    if sorted((sig or {}).get("entity_ids") or []) != consumed["ranking_signal"] or \
            {"scope": g["scope"], "purpose": g["purpose"]} != consumed["goal"] or \
            (consumed["selection"] == "RECOMMENDED" and now_card != consumed["card"]):
        out.append("ranking")
    fi = db.latest("founder_input", pid) or {}
    if (fi.get("research_context") or None) != consumed["context"]:
        out.append("context")
    return out


# ---- publication ----
def publish(ctx: Ctx, sub: dict | None, status: str, error: str | None = None) -> dict:
    """The CityResearch record and its claims and leads, written before the city's task is marked done."""
    pid, cid, rv = ctx.pid, ctx.cid, ctx.rv
    v = validate(ctx, sub) if sub else {"claims": [], "leads": [], "approaches": [], "learned": None,
                                        "learned_source": None, "faults": [], "facts_used": {}, "audiences_used": []}
    ids = {}
    for c in v["claims"]:
        rec = db.put_record("city_claim", pid, {
            **{k: c.get(k) for k in ("subject", "subject_name", "method", "text", "quote", "source_id", "window",
                                     "quote_found", "locality", "about_subject", "support", "state", "reason")},
            "city_id": cid, "vc": "F", "checker": CHECK_VERSION, "research": {"city_id": cid, "version": rv}},
            rid=f"ccl_{_key(ctx.cr, c['cid'])}")
        ids[c["cid"]] = rec["id"]
    lead_ids = []
    for lead in v["leads"]:
        rec = db.put_record("lead", pid, {**{k: lead.get(k) for k in (
            "key", "name", "type", "origin", "qloo_id", "address", "website", "own_site", "listing", "chain",
            "presence", "presence_check", "contact", "willingness")},
            "city_id": cid, "claim_ids": [ids[x] for x in lead["claim_ids"]],
            "research": {"city_id": cid, "version": rv}}, rid=f"lead_{_key(ctx.cr, lead['key'])}")
        lead_ids.append(rec["id"])
        lead["id"] = rec["id"]
    key_to_id = {lead["key"]: lead["id"] for lead in v["leads"]}
    approaches = [{**a, "grounding_claim_ids": [ids[x] for x in a["grounding_claim_ids"]],
                   "claim_ids": [ids[x] for x in a["claim_ids"]], "lead_ids": [key_to_id[k] for k in a["lead_keys"]
                                                                              if k in key_to_id]}
                  for a in v["approaches"]]
    contested = {ids[c["cid"]]: ids[c["contested_with"]] for c in v["claims"] if c.get("contested_with")}
    inv = investigations(ctx)
    consumed = consumed_of(ctx.inp, v)
    stale = stale_of(pid, cid, consumed)
    payload = {"city_id": cid, "run_id": ctx.run.id, "research_version": rv, "input": ctx.inp, "consumed": consumed,
               "status": status, "learned": v["learned"], "learned_source": v["learned_source"],
               "counters": counts(ctx.cr), "approaches": approaches, "lead_ids": lead_ids,
               "claim_ids": list(ids.values()), "contested": contested, "investigations": inv, "faults": v["faults"],
               "fenced": stale or None, "error": error, "prompt_version": PROMPT_VERSION, "check_version": CHECK_VERSION}
    rec = db.put_record("city_research", pid, payload, inputs=research_inputs(ctx.inp, consumed),
                        rid=f"cr_{pid}_{cid}_{rv}")
    _set(pid, cid, rv, status=status, error=error, ended_at=db.now())
    for a in approaches:
        emit(ctx, "APPROACH", f"{METHOD_LABELS[a['method']]}: {STATUS_WORDS[a['status']]}" + (f" · {a['name']}" if
                                                                                          a["status"] == "READY_TO_COMPARE" else ""))
    for c in v["claims"]:
        emit(ctx, {"SUPPORTED": "SUPPORTED", "CONTESTED": "SOURCES DIFFER"}.get(c["state"], "TO CHECK"),
             f"{c['subject_name']}: {c['text']}", claim_id=ids[c["cid"]], source_id=c["source_id"])
    ready = sum(a["status"] == "READY_TO_COMPARE" for a in approaches)
    _activity(ctx.run, f"Research {ctx.inp['name']}", "city researcher",
              f"{status} · {ready} ready · {len(v['claims'])} claims ({sum(c['state'] == 'SUPPORTED' for c in v['claims'])} "
              f"supported) · {len(lead_ids)} leads" + (f" · {error}" if error else ""),
              "DONE" if status == "complete" else "PARTIAL" if status == "partial" else "FAILED")
    return {"id": rec["id"], "version": rec["version"], "status": status}


STATUS_WORDS = {"READY_TO_COMPARE": "ready to compare", "HELD": "held", "NEEDS_RESEARCH": "needs research",
                "NOT_SUITABLE_NOW": "not suitable now", "RULED_OUT_BY_YOU": "ruled out by you", "CHECK_FAILED": "check failed",
                "NOT_ASSESSED_STOPPED": "not assessed · stopped"}


def research_inputs(inp: dict, consumed: dict) -> list[dict]:
    out = []
    if inp["brief"]:
        out.append({**inp["brief"], "keys": sorted(consumed["fact_ids"])})
    rs = inp["ranking_signal"]
    if rs:
        ref = rs["ref"].get("standins") or rs["ref"].get("identity")
        if ref:
            out.append({**ref, "keys": ["brands" if "standins" in rs["ref"] else "decision"]})
    if inp["recommendation"]:
        out.append({**inp["recommendation"]["assessment_set"], "keys": [inp["city_id"]]})
    if inp["audience"] and consumed["audiences_used"]:
        out.append({**inp["audience"]["profile"], "keys": consumed["audiences_used"]})
    return out


def investigations(ctx: Ctx) -> list[dict]:
    """One per method, from the checkpoint rows: which tools were used and what came back."""
    rows = db.q("SELECT kind, key, status, result FROM research_steps WHERE cr=? AND kind IN "
                "('qloo','search','page','presence') ORDER BY at, rowid", ctx.cr)
    by: dict[str | None, list] = {}
    for n, r in enumerate(rows, 1):
        res = json.loads(r["result"] or "{}")
        method = res.get("method") if r["kind"] != "presence" else None
        if r["kind"] == "qloo" and res.get("audience"):
            method = "digital"
        tool_name = {"qloo": "Qloo place search", "search": "web search", "page": "page reader",
                     "presence": "presence check"}[r["kind"]]
        outcome = (f"{len(res.get('places', []))} places" if r["kind"] == "qloo" else
                   f"{len(res.get('citations', []))} pages" if r["kind"] == "search" else
                   ("read" if res.get("status") == 200 and not res.get("error") else "unavailable") if r["kind"] == "page" else res.get("presence"))
        by.setdefault(method, []).append({"n": n, "tool": tool_name, "args_hash": r["key"].split(":", 1)[-1][:16],
                                          "outcome": r["status"] if r["status"] != "done" else outcome,
                                          "source_id": res.get("id") or res.get("source_id"),
                                          "request_id": res.get("request_id"),
                                          **({"audiences_used": [res["audience"]["uuid"]]} if res.get("audience") else {})})
    return [{"city_id": ctx.cid, "method": m, "title": f"{METHOD_LABELS.get(m, 'Leads')} · {ctx.inp['name']}",
             "role": "Researcher", "tools_used": t, "status": "DONE" if all(x["outcome"] not in ("failed", "running")
                                                                              for x in t) else "TO_CHECK"}
            for m, t in by.items()]


# ---- the research run ----
def research_city(run: Run, cid: str, rv: int) -> dict:
    pid = run.project_id
    row = city_row(pid, cid, rv)
    if row["status"] in TERMINAL and row["run_id"] == run.id:  # written before a crash, task not yet marked done
        return {"status": row["status"]}
    run.check()
    inp = json.loads(row["input"]) if row["input"] else build_input(pid, cid)  # pinned at city start, never re-pinned
    _set(pid, cid, rv, status="running", run_id=run.id, input=inp, started_at=row["started_at"] or db.now(), error=None)
    ctx = Ctx(run, pid, cid, rv, inp, run_cities=len(run.params["cities"]))
    run.emit("progress", {"city_id": cid, "status": "running", "research": True})
    try:
        sub = json.loads(row["submission"]) if row["submission"] else None
        if sub is None:
            sub = run_agent(ctx, row)
            _set(pid, cid, rv, submission=sub)
        out = publish(ctx, sub, "complete")
    except runner.Cancelled:
        _set(pid, cid, rv, status="stopped")
        raise
    except NoRecording:
        _set(pid, cid, rv, status="failed", error="recorded data does not cover this step")
        raise
    except Exception as e:
        reason = (f"operating allowance reached ({e})" if _caused_by(e, BudgetExhausted)
                  else f"{type(e).__name__}: {str(e)[:160]}")
        try:
            out = publish(ctx, None, "failed", reason)
        except (runner.Cancelled, NoRecording):
            raise
    run.emit("progress", {"city_id": cid, "status": out["status"], "research": True})
    return out


def research_run(run: Run):
    cities = run.params["cities"]
    with ThreadPoolExecutor(max_workers=IN_FLIGHT) as pool:
        futures = {cid: pool.submit(run.step, f"city:{cid}:{rv}", lambda c=cid, v=rv: research_city(run, c, v))
                   for cid, rv in cities}
    results, stop = {}, None
    for cid, f in futures.items():
        try:
            results[cid] = f.result()["status"]
        except (runner.Cancelled, NoRecording) as e:
            stop = stop or e
    if stop:
        raise stop
    failed = [c for c, s in results.items() if s == "failed"]
    if failed:
        return "partial", f"{len(failed)} of {len(results)} cities failed; the others are kept. Retry the failed ones."
    return None


def _active(pid: str) -> bool:
    return bool(db.q("SELECT 1 FROM runs WHERE project_id=? AND kind='research' AND status IN ('queued','running')", pid))


class Refused(Exception):
    def __init__(self, code: int, detail: str):
        super().__init__(detail)
        self.code, self.detail = code, detail


def selectable(pid: str) -> list[str]:
    sl = db.latest("shortlist", pid)
    return [c["city_id"] for c in (sl or {}).get("cities", []) if c["included"]]


def start(pid: str, city_ids: list[str] | None, context: str | None, refresh: bool) -> dict:
    """POST …/research: new cities only, unless `refresh` (Re-research, a new version per city). Identical without
    `refresh` → 409; more than 8 cities, or a city not included in the shortlist → 422."""
    with db._lock:
        if _active(pid):
            raise Refused(409, "Research is already running. Stop it, or wait for it to finish.")
        included = selectable(pid)
        ids = list(dict.fromkeys(city_ids if city_ids is not None else included))
        if len(ids) > MAX_CITIES:
            raise Refused(422, f"Research up to {MAX_CITIES} cities at a time: untick {len(ids) - MAX_CITIES}.")
        if not ids:
            raise Refused(422, "No city is selected for research.")
        if set(ids) - set(included):
            raise Refused(422, "That city isn't in your shortlist. Add it in Markets first.")
        if context is not None:
            set_context(pid, None, context)
        todo = []
        for cid in ids:
            cur = city_row(pid, cid)
            if refresh or cur is None:
                todo.append((cid, (cur["version"] + 1) if cur else 1))
        if not todo:
            raise Refused(409, "Already researched; use Re-research to refresh, Continue for stopped cities or Retry for "
                               "failed ones.")
        for cid, rv in todo:
            db.x("INSERT INTO research_cities(project_id, city_id, version, status, created_at) VALUES(?,?,?,?,?)",
                 pid, cid, rv, "queued", db.now())
        n = db.q("SELECT COUNT(*) n FROM runs WHERE project_id=? AND kind='research'", pid)[0]["n"]
    names = ", ".join(markets.CITIES[c]["name"] for c, _ in todo)
    db.event(pid, "founder", {"stage": "Opportunities", "task": "Re-researched" if refresh else "Started research",
                              "detail": f"{names} · expected ≈ ${EXPECTED_USD * len(todo):.2f}, at most "
                                        f"${CITY_USD * len(todo):.2f}"})
    return {"run_id": runner.start(pid, "research", f"research-{n + 1}", {"cities": todo, "reason": "refresh" if refresh
                                                                            else "new"})}


def resume(pid: str, city_ids: list[str], states: tuple[str, ...], why: str) -> dict:
    """Continue (stopped or queued cities) and Retry (failed cities): the same version, its checkpoints and its
    remaining bounds."""
    with db._lock:
        if _active(pid):
            raise Refused(409, "Research is already running.")
        todo = []
        for cid in city_ids:
            cur = city_row(pid, cid)
            if not cur or cur["status"] not in states:
                raise Refused(409, f"{markets.CITIES.get(cid, {}).get('name', cid)} isn't "
                                   f"{' or '.join(states)}.")
            todo.append((cid, cur["version"]))
        if not todo:
            raise Refused(422, "Choose a city.")
        n = db.q("SELECT COUNT(*) n FROM runs WHERE project_id=? AND kind='research'", pid)[0]["n"]
        for cid, rv in todo:
            _set(pid, cid, rv, status="queued")
    db.event(pid, "founder", {"stage": "Opportunities", "task": "Continued research" if why == "continue" else
                              "Retried research", "detail": ", ".join(markets.CITIES[c]["name"] for c, _ in todo)})
    return {"run_id": runner.start(pid, "research", f"research-{n + 1}", {"cities": todo, "reason": why})}


def set_context(pid: str, base_version: int | None, text: str | None) -> dict:
    """The research note (YOU SAID). Cities not yet started read the newest note at their start; started ones keep theirs."""
    with db._lock:
        fi = db.latest("founder_input", pid)
        if base_version is not None and fi["version"] != base_version:
            raise Refused(409, f"Your goal changed (now v{fi['version']}). Reload and try again.")
        text = (text or "").strip()[:500] or None
        payload = {k: v for k, v in fi.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")}
        if payload.get("research_context") == text:
            return fi
        rec = db.put_record("founder_input", pid, {**payload, "research_context": text}, inputs=fi["inputs"], rid=fi["id"])
    db.event(pid, "founder", {"stage": "Opportunities", "task": "Changed your research note", "detail": text or "cleared",
                              "intent_version": rec["version"]})
    return rec


# ---- founder decisions (R5): carried by stable identity, fenced by base_version ----
def find_approach(pid: str, aid: str) -> tuple[dict, dict] | None:
    for cr in latest_research(pid).values():
        for a in cr["approaches"]:
            if a["id"] == aid:
                return cr, a
    return None


def decide(pid: str, aid: str, base_version: int, decision: Literal["ruled_out", "undone"]) -> dict:
    with db._lock:
        found = find_approach(pid, aid)
        if not found:
            raise Refused(404, "No such approach.")
        cr, a = found
        rid = f"ad_{pid}_{a['city_id']}_{a['method']}"
        prev = db.get_record(rid)
        if (prev["version"] if prev else 0) != base_version:
            raise Refused(409, f"This approach changed (now v{prev['version'] if prev else 0}). Reload and try again.")
        rec = db.put_record("approach_decision", pid, {"city_id": a["city_id"], "method": a["method"], "approach_id": aid,
                                                       "approach_version": cr["research_version"],
                                                       "concept_key": a["concept_key"], "decision": decision,
                                                       "by": "founder", "at": db.now()}, rid=rid)
    db.event(pid, "founder", {"stage": "Opportunities", "task": "Ruled out an approach" if decision == "ruled_out"
                              else "Undid a rule-out", "detail": f"{markets.CITIES[a['city_id']]['name']} · "
                              f"{METHOD_LABELS[a['method']]} · {a['name']}"})
    return {"version": rec["version"]}


def add_contact(pid: str, lead_id: str, base_version: int, contact: str) -> dict:
    lead = db.get_record(lead_id)
    if not lead or lead["project_id"] != pid or lead["kind"] != "lead":
        raise Refused(404, "No such lead.")
    contact = contact.strip()[:200]
    if not contact:
        raise Refused(422, "Enter the contact.")
    with db._lock:
        rid = f"fc_{pid}_{_key(lead['key'])}"
        prev = db.get_record(rid)
        if (prev["version"] if prev else 0) != base_version:
            raise Refused(409, f"This contact changed (now v{prev['version'] if prev else 0}). Reload and try again.")
        rec = db.put_record("founder_contact", pid, {"lead_key": lead["key"], "value": contact, "by": "founder"}, rid=rid)
    db.event(pid, "founder", {"stage": "Opportunities", "task": "Added a contact", "detail": lead["name"]})
    return {"version": rec["version"]}


# ---- the lead check retry (B09) ----
def start_lead_check(pid: str, lead_id: str) -> dict:
    lead = db.get_record(lead_id)
    if not lead or lead["project_id"] != pid or lead["kind"] != "lead":
        raise Refused(404, "No such lead.")
    return {"run_id": runner.start(pid, "lead_check", f"{lead_id}-v{lead['version']}", {"lead_id": lead_id})}


def lead_check_run(run: Run):
    """≤ 3 page loads (its website and the pages it was found on), fresh snapshots; a new Lead version. Deterministic:
    no model call, no other research. The approach's partner chip and status follow from the view."""
    lead = db.get_record(run.params["lead_id"])
    urls = ([lead["website"]] if lead.get("website") and not is_social(lead["website"]) else [])
    for sid in (lead.get("presence_check") or {}).get("source_ids", []) + [
            s for c in lead.get("claim_ids", []) for s in [db.get_record(c).get("source_id")]]:
        s = source(sid) if sid else None
        if s and s["url"] not in urls and not is_social(s["url"]):
            urls.append(s["url"])
    for st in db.q("SELECT result FROM research_steps WHERE cr=? AND kind='presence'",
                   f"{run.project_id}:{lead['city_id']}:{lead['research']['version']}"):
        r = json.loads(st["result"] or "{}")
        if _norm(r.get("lead") or "") == _norm(lead["name"]) and r.get("url") not in urls:
            urls.append(r["url"])

    def check():
        pages, err = [], None
        for u in urls[:LEAD_CHECK_LOADS]:
            run.check()
            try:
                s = load_page(run, run.project_id, lead["city_id"], u, f"leadcheck:{lead['id']}", reuse=False)
            except fetch.FetchBlocked as e:
                err = str(e)
                continue
            pages.append(s)
            if not readable(s):
                err = s.get("error")
        ok = [p for p in pages if readable(p)]
        p = presence_of_lead(lead, ok, lead["city_id"], err) if ok else {
            "state": "CHECK_FAILED", "reason": f"The check couldn't read the page ({err or 'no page to read'}).",
            "source_ids": []}
        return {"presence": p, "loads": len(pages), "source_ids": [x["id"] for x in pages]}
    res = run.step("check", check)
    with db._lock:
        cur = db.get_record(lead["id"])
        payload = {k: v for k, v in cur.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")}
        payload.update(presence=res["presence"]["state"], presence_check={**res["presence"], "at": db.now(),
                                                                          "loads": res["loads"], "retry": True})
        rec = db.put_record("lead", run.project_id, payload, inputs=cur["inputs"], rid=lead["id"])
    _activity(run, f"Check {lead['name']}", "presence check", f"{res['presence']['state'].replace('_', ' ')} · "
              f"{res['loads']} page load{'s' if res['loads'] != 1 else ''}",
              "FAILED" if res["presence"]["state"] == "CHECK_FAILED" else "DONE")
    run.emit("research", {"city_id": lead["city_id"], "version": lead["research"]["version"], "kind": "CHECKING",
                          "text": f"Re-checked {lead['name']}: {res['presence']['state'].replace('_', ' ').lower()}",
                          "lead_id": lead["id"], "lead_version": rec["version"]})
    if res["presence"]["state"] == "CHECK_FAILED":
        return "failed", res["presence"]["reason"]
    return None


# ---- OpportunitiesView (records → screen view; never stored) ----
def latest_research(pid: str) -> dict[str, dict]:
    """The newest published CityResearch per city, at its latest research version."""
    out: dict[str, dict] = {}
    for r in db.q("SELECT city_id, MAX(version) v FROM research_cities WHERE project_id=? GROUP BY city_id", pid):
        for v in range(r["v"], 0, -1):
            rec = db.get_record(f"cr_{pid}_{r['city_id']}_{v}")
            if rec:
                out[r["city_id"]] = rec
                break
    return out


def lead_view(pid: str, lead: dict) -> dict:
    fc = db.get_record(f"fc_{pid}_{_key(lead['key'])}")
    claims = [db.get_record(c) for c in lead.get("claim_ids", [])]
    return {**{k: lead.get(k) for k in ("id", "version", "key", "name", "type", "origin", "qloo_id", "address", "website",
                                         "own_site", "listing", "chain", "presence", "presence_check", "willingness",
                                         "city_id")},
            "contact": lead.get("contact") and {**lead["contact"], "source": _src_ref(lead["contact"].get("source_id"))},
            "founder_contact": fc and {"value": fc["value"], "version": fc["version"]},
            "contact_version": fc["version"] if fc else 0,
            "claims": [claim_view(c) for c in claims if c]}


def _src_ref(sid: str | None) -> dict | None:
    s = source(sid) if sid else None
    return s and {"id": s["id"], "url": s["url"], "final_url": s["final_url"], "fetched_at": s["fetched_at"],
                  "status": s["status"], "replayed": bool(s["replayed"])}


def claim_view(c: dict) -> dict:
    return {"id": c["id"], "subject_name": c["subject_name"], "method": c["method"], "text": c["text"], "state": c["state"],
            "quote": c["quote"], "quote_found": c["quote_found"], "locality": c["locality"], "support": c["support"],
            "reason": c["reason"], "source": _src_ref(c["source_id"])}


def opportunities_view(pid: str) -> dict:
    runs = db.q("SELECT * FROM runs WHERE project_id=? AND kind='research' ORDER BY started_at DESC, rowid DESC", pid)
    run = runs[0] if runs else None
    active = bool(run) and run["status"] in ("queued", "running")
    included = selectable(pid)
    research = latest_research(pid)
    rows = {r["city_id"]: r for r in db.q("SELECT * FROM research_cities WHERE project_id=? ORDER BY version", pid)}
    shown = [c for c in dict.fromkeys(included + list(rows)) if c in rows or c in included]
    cities, all_ready, methods_ready, leads = [], 0, set(), {}
    events = db.events(pid, 0, "research")
    for cid in shown:
        st = rows.get(cid)
        cr = research.get(cid)
        status = st["status"] if st else "not_started"
        if cr and st and cr["research_version"] != st["version"]:
            prior = cr  # a newer version is underway: the older one stays readable
        else:
            prior = None
        visible = cid in included
        view = {"city_id": cid, "name": markets.CITIES[cid]["name"], "country": markets.CITIES[cid]["country"],
                "region": markets.CITIES[cid]["region"], "visible": visible, "status": status,
                "version": st["version"] if st else None, "error": st and st["error"],
                "can": [x for x, ok in (("continue", status in ("stopped", "queued") and not active),
                                        ("retry", status == "failed" and not active),
                                        ("rerun", status in TERMINAL and not active)) if ok],
                "selection": None, "learned": None, "matrix": {}, "approaches": [], "considered": [], "stale": [],
                "notes": [], "counters": counts(f"{pid}:{cid}:{st['version']}") if st else None,
                "stream": plain_stream([e for e in events if e["city_id"] == cid and st and e["version"] == st["version"]])[-40:],
                "context_used": None, "investigations": [], "previous": prior and prior["research_version"], "history": []}
        rec = cr if cr and (not st or cr["research_version"] == st["version"] or status not in TERMINAL) else None
        if rec:
            view.update(selection=rec["input"]["selection"], learned=rec["learned"], learned_source=rec["learned_source"],
                        context_used=rec["input"]["context"], investigations=rec["investigations"],
                        record={"id": rec["id"], "version": rec["version"], "research_version": rec["research_version"]},
                        input_summary=_input_summary(rec["input"]), faults=len(rec["faults"]))
            stale = stale_of(pid, cid, rec["consumed"])
            view["stale"] = [STALE_WORDS[s].format(city=view["name"]) for s in stale]
            view["fenced"] = bool(rec.get("fenced"))
            for lid in rec["lead_ids"]:
                lead = db.get_record(lid)
                leads[lid] = lead_view(pid, lead)
            for a in rec["approaches"]:
                a = _decided(pid, a)
                a = _with_partners(a, leads)
                if rec["status"] == "failed":
                    a = {**a, "status": "CHECK_FAILED", "reason": rec.get("error")}
                view["matrix"][a["method"]] = {"status": a["status"], "reason": a.get("reason") or a["name"]}
                if a["status"] in ("READY_TO_COMPARE",) or (a["status"] == "RULED_OUT_BY_YOU" and a.get("was") == "READY_TO_COMPARE"):
                    view["approaches"].append(a)
                else:
                    view["considered"].append(a)
                if a.get("earlier_ruled_out"):
                    view["notes"].append(f"You ruled out an earlier {METHOD_LABELS[a['method']].lower()} idea here.")
            if rec["status"] == "failed":
                view["matrix"] = {m: {"status": "CHECK_FAILED", "reason": rec.get("error") or "the research failed"}
                                  for m in METHODS}
            elif rec.get("fenced"):  # a founder change arrived mid-research: history, never current work (review P4-I3)
                view["history"] = view["approaches"] + view["considered"]
                view["approaches"], view["considered"] = [], []
                view["matrix"] = {m: {"status": "HISTORY", "reason": "Researched before your change; kept as history."}
                                  for m in METHODS}
                view["fenced_reason"] = "; ".join(STALE_WORDS[s].format(city=view["name"]) for s in rec["fenced"])
        if status == "failed" and not rec:  # failed before anything was published (e.g. a replay miss)
            view["matrix"] = {m: {"status": "CHECK_FAILED", "reason": st["error"] or "the research failed"} for m in METHODS}
        if status in ("stopped", "queued") and not active and not rec:
            view["matrix"] = {m: {"status": "NOT_ASSESSED_STOPPED", "reason": "Research stopped before this was assessed."}
                              for m in METHODS}
        if visible and rec:
            ready = [a for a in view["approaches"] if a["status"] == "READY_TO_COMPARE"]
            all_ready += len(ready)
            methods_ready |= {a["method"] for a in ready}
        cities.append(view)
    vis = [c for c in cities if c["visible"] and c["matrix"]]
    fi = db.latest("founder_input", pid) or {}
    spent = sum((c["counters"] or {}).get("usd_reserved", 0) for c in cities)
    todo = [c for c in included if c not in rows]
    return {"run": run and {"id": run["id"], "status": run["status"], "message": json.loads(run["detail"]).get("message", ""),
                            "active": active},
            "title": {"approaches": all_ready, "cities": len(vis), "methods": len(methods_ready)},
            "cities": cities, "leads": leads, "methods": [{"key": m, "label": METHOD_LABELS[m]} for m in METHODS],
            "context": fi.get("research_context"), "intent_version": fi.get("version"),
            "start": {"included": included, "new": todo, "max": MAX_CITIES, "over": max(0, len(included) - MAX_CITIES),
                      "expected_usd": EXPECTED_USD, "max_usd": CITY_USD},
            "usage": {"expected": round(EXPECTED_USD * len(cities), 2), "reserved": round(spent, 4)},
            "researched": bool(rows)}


def _input_summary(inp: dict) -> dict:
    return {"selection": inp["selection"], "scan_status": inp["scan"]["status"],
            "signal": (inp["ranking_signal"] or {}).get("label") or "profile tags (listings)",
            "audiences": [a["name"] for a in (inp["audience"] or {}).get("offered", [])],
            "caveat": (inp["audience"] or {}).get("caveat"), "context": inp["context"],
            "position_line": (inp["recommendation"] or {}).get("position_line")}


def _decided(pid: str, a: dict) -> dict:
    d = db.get_record(f"ad_{pid}_{a['city_id']}_{a['method']}")
    out = {**a, "decision_version": d["version"] if d else 0}
    if d and d["decision"] == "ruled_out":
        if d["concept_key"] == a["concept_key"]:
            out.update(status="RULED_OUT_BY_YOU", was=a["status"])
        else:
            out["earlier_ruled_out"] = True
    return out


def _with_partners(a: dict, leads: dict) -> dict:
    partners = [leads[i] for i in a.get("lead_ids", []) if i in leads]
    out = {**a, "partners": [{k: p[k] for k in ("id", "name", "presence", "chain", "listing", "willingness")} for p in partners],
           "claims": [claim_view(db.get_record(c)) for c in a.get("claim_ids", [])]}
    if a["status"] == "READY_TO_COMPARE" and partners and all(p["presence"] == "SOURCES_DIFFER" for p in partners):
        out.update(status="HELD", reason="Sources differ on where its partner is; held until that is settled.")
    return out


HANDLERS["research"] = research_run
HANDLERS["lead_check"] = lead_check_run
