"""Stage 3 · Qloo identity and discovery profile (BUILD-PLAN slice 2, CONTRACTS B3).

The identity lookup uses only the URL and the homepage, never the brief (no cycle). A single confident match is used
automatically and can be corrected ("Not you?"); anything else asks the founder. With no match, the Profile agent
proposes Qloo tags, each tied to brief facts, and the founder edits and confirms them.
"""
import json
import re
import time
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field
from strands import Agent, tool

from . import brand, db, fetch, qloo, runner
from .cassette import NoRecording
from .ledger import BudgetExhausted
from .nova import make_model
from .runner import HANDLERS, Run

PROFILE_VERSION = "profile_agent@1"
MAX_SIGNALS = 12
MAX_TAG_SEARCHES = 8
MAX_BRAND_SEARCHES = 2
MAX_TOOL_CALLS = 16  # tool bodies entered, refused searches included
MAX_MODEL_CALLS = 18  # every Converse turn: also counts invalid or unknown tool calls and structured-output retries
HOMEPAGE_WAIT = 90  # seconds the lookup waits for the brand read's homepage before using the address alone

# Discovery signal tag types (BUILD-PLAN slice 2). Place categories are slice 3 channel filters; similar/competitor
# brand tags are brand names, not taste. customer_segment is the "audience-like" type seen on live brands.
ALLOWED = {"lifestyle", "aesthetic_property", "core_value", "interest_adjacency", "keyword", "emotional_tone",
           "influencer_appeal", "customer_segment"}
TYPE_LABELS = {"genre:brand": "Category", "lifestyle": "Lifestyle", "aesthetic_property": "Aesthetic",
               "core_value": "Values", "interest_adjacency": "Related interests", "keyword": "Keywords",
               "emotional_tone": "Tone", "influencer_appeal": "Influencer appeal", "customer_segment": "Audience"}


BRAND_TAG_TYPES = ("urn:tag:key_market", "urn:tag:similar_brand", "urn:tag:competitor_brand")


def _type_key(tag_type: str | None) -> str:
    t = (tag_type or "").removeprefix("urn:tag:")
    return "genre:brand" if t == "genre:brand" else t.split(":")[0]


def allowed(tag_type: str | None) -> bool:
    return _type_key(tag_type) in ALLOWED | {"genre:brand"}


def type_label(tag_type: str | None) -> str:
    return TYPE_LABELS.get(_type_key(tag_type), _type_key(tag_type).replace("_", " ").capitalize())


# ---- name derivation (P2-01): inputs are the URL, its redirect target and the homepage <title> only ----
def norm(s: str) -> str:
    """Letters and digits only, case-folded."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", s or "").casefold() if ch.isalnum())


SUFFIX = re.compile(r"[\s,]+(co|company|inc|incorporated|llc|ltd|limited|corp|corporation|gmbh|ag|sa|bv|pty)\.?\s*$", re.I)


def name_key(s: str) -> str:
    """The confidence rule's comparison: `norm` with a leading "the" and a trailing company suffix ignored
    ("Death Wish Coffee Co." is "Death Wish Coffee", 27 Oct 2026)."""
    return norm(SUFFIX.sub("", re.sub(r"^\s*the\s+", "", s or "", flags=re.I).strip()))


def address_labels(*urls: str | None) -> list[str]:
    """First label of each host, without www. and the TLD: komono.com → komono; got-bag.com → got-bag."""
    out = []
    for u in urls:
        host = fetch.site_of(u or "")
        if host and host.split(".")[0] not in out:
            out.append(host.split(".")[0])
    return out


_SEPARATOR = re.compile(r"\s*(?:[|»·–—]|:\s|\s-\s)\s*")  # a hyphen inside a word (cold-brewed) is not a separator


def derive_names(url: str, final_url: str | None, title: str | None) -> dict:
    labels = address_labels(url, final_url)
    keys = {norm(x) for x in labels}
    words = " ".join((title or "").split()).split(" ") if (title or "").strip() else []
    site_name = None
    for n in range(1, len(words) + 1):  # the shortest run of consecutive title words, anywhere in the title
        site_name = next((" ".join(words[i:i + n]) for i in range(len(words) - n + 1)
                          if norm(" ".join(words[i:i + n])) in keys), None)
        if site_name:
            break
    if site_name:
        site_name, source = site_name.strip(" ,.;:!?|»·–—-\"'()[]"), "title_matches_address"
    else:
        site_name, source = labels[0].replace("-", " "), "address"
    segment = _SEPARATOR.split(" ".join((title or "").split()), maxsplit=1)[0].strip()
    query2 = segment if segment and norm(segment) != norm(site_name) and len(segment.split()) <= 4 else None
    return {"site_name": site_name, "name_source": source, "labels": labels, "query2": query2}


# ---- candidates, order and the confidence rule ----
def _domain_match(official: str | None, sites: list[str]) -> bool | None:
    if not official:
        return None  # Qloo gives no website: cannot tell
    site = fetch.site_of(official if "://" in official else "https://" + official)
    return any(site == s or site.endswith("." + s) or s.endswith("." + site) for s in sites if s and site)


def candidate(e: dict, site_name: str, sites: list[str], home_text: str | None) -> dict:
    p = e.get("properties") or {}
    official = p.get("official_site") or p.get("website")
    name = e.get("name") or ""
    tags = [qloo.tag_ref(t) for t in e.get("tags") or []]
    return {
        "qloo_id": e["entity_id"], "name": name, "types": e.get("types") or [], "popularity": e.get("popularity"),
        "short_description": p.get("short_description"), "external": p.get("external"), "official_site": official,
        "tags": [t for t in tags if allowed(t["type"])][:40], "tag_count": len(tags),
        # raw market tags, kept apart from discovery tags (slice 3.5, R3): presence and related-brand stores read them
        "brand_tags": [t for t in tags if (t["type"] or "").startswith(BRAND_TAG_TYPES)],
        "corroboration": {
            "name_match": "exact" if name.strip() == site_name.strip()
            else "normalised" if name_key(name) == name_key(site_name) else "different",
            "domain_match": _domain_match(official, sites),
            "site_mentions_name": (name_key(name) in norm(home_text)) if home_text and name_key(name) else None}}


def order(cands: list[dict]) -> list[dict]:
    """Name match, then website match, then the site mentioning the name, then Qloo's own order."""
    return [c for _, c in sorted(enumerate(cands), key=lambda ic: (
        ic[1]["corroboration"]["name_match"] == "different", ic[1]["corroboration"]["domain_match"] is not True,
        ic[1]["corroboration"]["site_mentions_name"] is not True, ic[0]))]


def confidence(cands: list[dict], site_name: str) -> list[str] | None:
    """Auto-match only when exactly one candidate's name equals the site name and its website doesn't contradict yours.
    Look-alikes with other names (a popular name always brings some) don't block it; two same-named candidates do."""
    same = [c for c in cands if name_key(c["name"]) == name_key(site_name)]
    if len(same) != 1 or same[0]["corroboration"]["domain_match"] is False:
        return None
    c = same[0]
    return ["only candidate" if len(cands) == 1 else "the only candidate with your site's name", "name matches your site",
            "website matches" if c["corroboration"]["domain_match"] else "no website to confirm"]


# ---- identity run ----
def _homepage(pid: str, url: str) -> dict | None:
    rows = db.q("SELECT final_url, title, text FROM sources WHERE project_id=? AND url=? AND status=200", pid,
                fetch.check_url(url))
    return dict(rows[0]) if rows else None


def _names(run: Run, url: str) -> dict:
    """Wait (bounded) for the brand read's homepage step; an unreadable site still gets a lookup by address (D12)."""
    end = time.time() + HOMEPAGE_WAIT
    while time.time() < end:
        run.check()
        br = db.q("SELECT id, status FROM runs WHERE project_id=? AND kind='brand_read' "
                  "ORDER BY started_at DESC, rowid DESC LIMIT 1", run.project_id)
        if not br or br[0]["status"] not in ("queued", "running"):
            break
        opened = db.q("SELECT status FROM tasks WHERE run_id=? AND task_key='open'", br[0]["id"])
        if opened and opened[0]["status"] in ("done", "failed"):
            break
        run.cancel.wait(0.3)
    home = _homepage(run.project_id, url) or {}
    opened = db.q("SELECT t.result FROM tasks t JOIN runs r ON r.id=t.run_id WHERE r.project_id=? AND r.kind='brand_read' "
                  "AND t.task_key='open' AND t.status='done' ORDER BY r.started_at DESC LIMIT 1", run.project_id)
    first = (json.loads(opened[0]["result"]).get("first_title") if opened else None) or home.get("title")
    return {**derive_names(url, home.get("final_url"), first), "title": first or ""}


def _search(run: Run, query: str, names: dict, url: str) -> dict:
    try:
        out = qloo.get("/search", {"query": query, "types": "urn:entity:brand", "take": 5}, run.cassette, "identity",
                       run.project_id, run.id)
    except BudgetExhausted as e:
        out = {"status": 0, "error": f"live Qloo work is paused: operating allowance reached ({e})", "attempts": 0}
    except NoRecording:
        out = {"status": 0, "error": "the recorded data has no Qloo lookup for this brand", "attempts": 0}
    if out.get("status") != 200:
        reason = out.get("error") or (f"Qloo answered HTTP {out['status']}" if out.get("status") else "no response")
        tries = out.get("attempts", 1)
        brand._activity(run, f"Find {query} in Qloo", "Qloo brand search",
                        f"{reason}{f' · {tries} attempts' if tries > 1 else ''}", "FAILED")
        return {"ok": False, "reason": reason, "attempts": tries}
    home = _homepage(run.project_id, url) or {}
    sites = [fetch.site_of(url), fetch.site_of(home.get("final_url") or "")]
    entities, _ = qloo.rows(out)
    cands = order([candidate(e, names["site_name"], sites, home.get("text")) for e in entities])
    brand._activity(run, f"Find {query} in Qloo", "Qloo brand search",
                    f"1 request · {len(cands)} brand{'s' if len(cands) != 1 else ''} returned"
                    + (" · from cache" if out.get("cache") else ""))
    return {"ok": True, "candidates": cands}


def identity_run(run: Run):
    pid = run.project_id
    url = db.q("SELECT url FROM projects WHERE id=?", pid)[0]["url"]
    names = run.step("names", lambda: _names(run, url))
    queries = [names["site_name"]]
    res = run.step("search-1", lambda: _search(run, names["site_name"], names, url))
    if res["ok"] and not res["candidates"] and names["query2"]:  # a second query only after zero brands
        queries.append(names["query2"])
        res = run.step("search-2", lambda: _search(run, names["query2"], names, url))
    run.step("decide", lambda: _decide(run, names, queries, res))
    after_identity(pid)


def _decide(run: Run, names: dict, queries: list[str], res: dict) -> dict:
    cands = res.get("candidates", [])
    base = {"site_name": names["site_name"], "name_source": names["name_source"], "queries": queries,
            "candidates": cands, "at": db.now(), "run_id": run.id, "failure_reason": None, "attempts": None}
    if not res["ok"]:  # identity unknown, never "no match"
        payload = {**base, "status": "failed", "failure_reason": res["reason"], "attempts": res["attempts"],
                   "decision": None, "decided_by": None, "basis": []}
    elif not cands:
        payload = {**base, "status": "decided", "decision": "none", "decided_by": "landfall",
                   "basis": [f"Qloo returned no brand for '{q}'" for q in queries]}
    elif basis := confidence(cands, names["site_name"]):
        match = next(c for c in cands if name_key(c["name"]) == name_key(names["site_name"]))
        payload = {**base, "status": "decided", "decision": {"entity_id": match["qloo_id"]},
                   "decided_by": "landfall", "basis": basis}
    else:
        payload = {**base, "status": "needs_founder", "decision": None, "decided_by": None, "basis": []}
    rec = db.put_record("identity", run.project_id, payload, inputs=[{"id": run.project_id, "version": 1, "keys": ["url"]}],
                        rid=f"identity_{run.project_id}")
    run.emit("identity", {"version": rec["version"], "status": payload["status"]})
    return {"version": rec["version"]}


def decide_by_founder(pid: str, ident: dict, entity_id: str) -> dict:
    keep = {k: v for k, v in ident.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")}
    rec = db.put_record("identity", pid, {
        **keep, "status": "decided", "decision": "none" if entity_id == "none" else {"entity_id": entity_id},
        "decided_by": "founder", "basis": ["chosen by you"], "at": db.now()}, inputs=ident["inputs"], rid=ident["id"])
    chosen = next((c["name"] for c in ident["candidates"] if c["qloo_id"] == entity_id), None)
    db.event(pid, "founder", {"task": "Qloo identity", "detail": f"This is us: {chosen}" if chosen
                              else "Neither. Continue without a Qloo match", "identity_version": rec["version"]})
    after_identity(pid)
    return rec


# ---- profile routes ----
def follows(prof: dict | None, ident: dict) -> bool:
    return bool(prof) and any(i["id"] == ident["id"] and i["version"] == ident["version"] for i in prof["inputs"])


def after_identity(pid: str) -> None:
    """A decided identity sets the profile route: a matched brand is the profile; no match proposes one."""
    ident = db.latest("identity", pid)
    if not ident or ident["status"] != "decided" or follows(db.latest("profile", pid), ident):
        return
    if ident["decision"] == "none":
        brief_ready(pid)
        return
    c = next(c for c in ident["candidates"] if c["qloo_id"] == ident["decision"]["entity_id"])
    db.put_record("profile", pid, {
        "route": "brand_entity", "signals": [{
            "kind": "entity", "qloo_id": c["qloo_id"], "name": c["name"], "origin": "QLOO", "included": True,
            "basis_claim_ids": [], "reasoning": None, "basis_established": None}],
        "display_tags": c["tags"][:12], "dropped": [], "confirmed": True, "confirmed_by": ident["decided_by"]},
        inputs=[{"id": ident["id"], "version": ident["version"], "keys": ["decision"]}], rid=f"profile_{pid}")
    db.event(pid, "profile", {"route": "brand_entity"})
    from . import ranking, scan
    ranking.prepare(pid)  # store search and audiences, before the scan
    scan.auto_start(pid)  # if the brief is already done, Markets start now


def brief_ready(pid: str) -> None:
    """Start the Profile agent once the decision is "none" and the brief exists, whichever comes second."""
    ident, brief = db.latest("identity", pid), db.latest("brief", pid)
    if (brief and ident and ident["status"] == "decided" and ident["decision"] == "none"
            and not follows(db.latest("profile", pid), ident)):
        runner.start(pid, "profile", f"identity-v{ident['version']}", {"identity_version": ident["version"]})


# ---- Profile agent ----
PROFILE_SYSTEM = """You are the Profile agent on Landfall, an international launch team for small consumer brands.
Qloo, a cultural-taste service, has no confirmed record of this brand. Your job is to describe the taste of the
brand's customers in Qloo's own vocabulary (Qloo tags), so a later step can find places related to that taste.

Rules:
- Use qloo_tag_search with short, singular words (for example 'backpack', 'sustainability', 'outdoor'); Qloo
  matches tag names, so 'watches' finds nothing where 'watch' does. At most 8 searches. Only tags that came back
  from your searches can be proposed; copy their ids exactly. Never invent an id.
- Prefer tags that describe taste, values, lifestyle, aesthetic or audience. A generic category (for example
  'Fashion') is weaker than a specific one.
- Each signal cites basis_facts: the numbers of the brief facts it rests on (1 = first). Cite only facts whose
  content concerns what the tag describes: product category, material, values, audience or lifestyle. A fact that
  merely exists (for example a founding year) does not support a category tag. Facts marked TO_CHECK are not
  established; prefer established ones. reasoning is one sentence linking those facts to the tag.
- You may use qloo_brand_search (at most 2) to find a comparable brand. Qloo finds brands by matching words, not
  shared audiences, so propose one only if its description clearly resembles this brand, and say specifically why.
  Comparables are shown to the founder unticked.
- Propose at most 12 signals. Fewer good signals are better than many weak ones. If nothing fits, return none."""


class ProposedSignal(BaseModel):
    qloo_id: str = Field(description="The id exactly as a search returned it")
    kind: Literal["tag", "entity"] = Field(description="tag for a Qloo tag, entity for a comparable brand")
    name: str
    basis_facts: list[int] = Field(description="Numbers of the brief facts this signal rests on (1 = first)")
    reasoning: str = Field(description="One sentence: how those facts lead to this signal")


class ProposedProfile(BaseModel):
    signals: list[ProposedSignal]


def _fact_lines(facts: list[dict]) -> str:
    return "\n".join(f"{i}. {f['label']}: {f['value']} [{f['state'].replace('_', ' ')}]" for i, f in enumerate(facts, 1))


def tag_search(pid: str, run_id: str | None, cassette, stage: str, term: str, tag_type: str | None = None,
               take: int = 10) -> tuple[dict, list[dict], int]:
    """(response, usable tags, hidden count): only allowed discovery types are returned for use."""
    params = {"filter.query": term.strip(), "take": take}
    if tag_type:
        params["filter.tag.types"] = tag_type if tag_type.startswith("urn:") else (
            "urn:tag:genre:brand" if tag_type == "genre:brand" else f"urn:tag:{tag_type}:qloo")
    out = qloo.get("/v2/tags", params, cassette, stage, pid, run_id)
    refs = [qloo.tag_ref(t) for t in qloo.rows(out)[1]]
    usable = [{**r, "type_label": type_label(r["type"])} for r in refs if r["qloo_id"] and allowed(r["type"])]
    return out, usable, len(refs) - len(usable)


def _propose(run: Run, brief: dict, facts: list[dict]) -> dict:
    used = {"tags": 0, "brands": 0, "calls": 0, "model": 0}
    stop: list[tuple[str, bool]] = []  # (reason, budget): set once, and the agent loop is cancelled
    agent_ref: list[Agent] = []

    def halt(reason: str, budget: bool = False) -> str:
        if not stop:
            stop.append((reason, budget))
        agent_ref[0].cancel()  # Strands turns a tool exception into a tool result; only cancel ends the loop
        return "Stopped."

    def admit() -> bool:
        run.check()
        used["calls"] += 1
        if used["calls"] > MAX_TOOL_CALLS:
            halt(f"the Profile agent exceeded {MAX_TOOL_CALLS} tool calls")
        return not stop

    @tool
    def qloo_tag_search(term: str, tag_type: str | None = None) -> str:
        """Search Qloo's tag vocabulary by tag name. tag_type optionally narrows to one type: genre:brand,
        lifestyle, aesthetic_property, core_value, interest_adjacency, keyword, emotional_tone, influencer_appeal
        or customer_segment."""
        if not admit():
            return "Stopped."
        if used["tags"] >= MAX_TAG_SEARCHES:
            return f"Not searched: the {MAX_TAG_SEARCHES}-search limit is reached. Work with the tags you have."
        used["tags"] += 1
        try:
            out, usable, hidden = tag_search(run.project_id, run.id, run.cassette, "profile", term, tag_type)
        except BudgetExhausted as e:
            return halt(str(e), budget=True)
        if out.get("status") != 200:
            brand._activity(run, f"Search Qloo tags: '{term}'", "Qloo tag search", f"HTTP {out.get('status')}",
                            "FAILED", role="Profile agent")
            return f"Search failed (HTTP {out.get('status')}). Try another word."
        brand._activity(run, f"Search Qloo tags: '{term}'", "Qloo tag search",
                        f"1 request · {len(usable)} usable tag{'s' if len(usable) != 1 else ''}"
                        + (f" · {hidden} place or other types left out" if hidden else ""), role="Profile agent")
        if not usable:
            return f"No usable tags for '{term}'" + (f" ({hidden} were place categories or other types)" if hidden
                                                     else "") + ". Try a shorter or singular word."
        return "\n".join(f"{t['qloo_id']} | {t['name']} | {t['type_label']}" for t in usable)

    @tool
    def qloo_brand_search(words: str) -> str:
        """Find up to 5 Qloo brands by words, as possible comparable brands (shown to the founder unticked)."""
        if not admit():
            return "Stopped."
        if used["brands"] >= MAX_BRAND_SEARCHES:
            return f"Not searched: the {MAX_BRAND_SEARCHES}-search limit is reached."
        used["brands"] += 1
        try:
            out = qloo.get("/search", {"query": words.strip(), "types": "urn:entity:brand", "take": 5}, run.cassette,
                           "profile", run.project_id, run.id)
        except BudgetExhausted as e:
            return halt(str(e), budget=True)
        entities, _ = qloo.rows(out)
        brand._activity(run, f"Search Qloo brands: '{words}'", "Qloo brand search",
                        f"1 request · {len(entities)} brand{'s' if len(entities) != 1 else ''}", role="Profile agent")
        if not entities:
            return f"No brands for '{words}'."
        return "\n".join(f"{e['entity_id']} | {e.get('name')} | {((e.get('properties') or {}).get('short_description') or '')[:160]}"
                         for e in entities)

    prompt = (f"Brand: {brief['brand_name']}\n\nBRIEF FACTS (cite by number):\n{_fact_lines(facts)}\n\n"
              "Search Qloo's tags, then return the profile.")
    sink = brand._usage_sink(run)

    def per_turn(u: dict):
        """The bound that holds whatever the model asks for: it runs at the Converse boundary, after each turn."""
        sink(u)
        used["model"] += 1
        if used["model"] >= MAX_MODEL_CALLS:
            halt(f"the Profile agent reached {MAX_MODEL_CALLS} model calls without a valid profile")

    agent = Agent(model=make_model(run.cassette, "profile", per_turn, max_tokens=3000),
                  system_prompt=PROFILE_SYSTEM, tools=[qloo_tag_search, qloo_brand_search], callback_handler=None)
    agent_ref.append(agent)
    try:
        res = agent(prompt, structured_output_model=ProposedProfile, cancel_signal=run.cancel)
    except Exception:
        if not stop:
            raise
        res = None  # a cancelled structured-output loop may raise instead of returning
    run.check()
    if stop:
        reason, budget = stop[0]
        raise BudgetExhausted(reason) if budget else RuntimeError(reason)
    if res.structured_output is None:
        raise RuntimeError("the Profile agent returned no profile")
    return {"signals": [s.model_dump() for s in res.structured_output.signals],
            "searches": {"tags": used["tags"], "brands": used["brands"]}}


def validate(pid: str, proposal: dict, brief: dict, facts: list[dict]) -> tuple[list[dict], list[dict]]:
    """Every id must have come back in a Qloo response recorded for this project, be an allowed type and cite brief
    facts; anything else is dropped and recorded. Structure only: relevance is judged by the founder and the review."""
    states = {f["id"]: f["state"] for f in facts}
    signals, dropped = [], []
    for s in proposal["signals"]:
        ref = qloo.recorded(pid, s["qloo_id"])
        basis = brand._resolve_basis({"basis_facts": s.get("basis_facts"), "basis_pages": []}, brief["facts"], states, {})
        why = ("not returned by any Qloo response for this project" if not ref
               else f"is a Qloo {ref['kind']}, not a {s['kind']}" if ref["kind"] != s["kind"]
               else f"tag type not used for discovery ({type_label(ref['type'])})" if ref["kind"] == "tag" and not allowed(ref["type"])
               else "proposed twice" if any(x["qloo_id"] == s["qloo_id"] for x in signals)
               else "cites no fact in the brief" if not basis["claim_ids"] else None)
        if why:
            dropped.append({"qloo_id": s["qloo_id"], "name": s.get("name"), "reason": why})
            continue
        comparable = ref["kind"] == "entity"
        signals.append({"kind": ref["kind"], "qloo_id": s["qloo_id"], "name": ref["name"] or s["name"],
                        "tag_type": None if comparable else ref["type"],
                        "origin": "COMPARABLE" if comparable else "QLOO_CATEGORY", "included": not comparable,
                        "basis_claim_ids": basis["claim_ids"], "reasoning": s.get("reasoning", ""),
                        "basis_established": basis["established"], "basis_dropped": basis["dropped"]})
    for s in signals[MAX_SIGNALS:]:
        dropped.append({"qloo_id": s["qloo_id"], "name": s["name"], "reason": f"over the {MAX_SIGNALS}-signal limit"})
    return signals[:MAX_SIGNALS], dropped


def profile_inputs(brief: dict, ident: dict, signals: list[dict]) -> list[dict]:
    keys = list(dict.fromkeys(c for s in signals for c in s.get("basis_claim_ids") or []))
    return [{"id": brief["id"], "version": brief["version"], "keys": keys},
            {"id": ident["id"], "version": ident["version"], "keys": ["decision"]}]


def profile_run(run: Run):
    pid = run.project_id
    ident, brief = db.latest("identity", pid), db.latest("brief", pid)
    if not brief or not ident or ident["version"] != run.params["identity_version"]:
        return  # the identity changed since this run was started: nothing to propose for
    facts = [db.get_record(i) for i in brief["facts"]]
    proposal = run.step("agent", lambda: _propose(run, brief, facts))

    def assemble():
        if follows(db.latest("profile", pid), db.latest("identity", pid)) or db.latest("identity", pid)["version"] != ident["version"]:
            # ponytail: the founder edited first (or changed identity); a late proposal never overwrites their work
            brand._activity(run, "Propose a discovery profile", "Profile agent",
                            "Proposal arrived after your edits; not applied", "SKIPPED", role="Profile agent")
            return {"applied": False}
        signals, dropped = validate(pid, proposal, brief, facts)
        rec = db.put_record("profile", pid, {
            "route": "description_profile" if brief["route"] == "description" else "proposed_profile",
            "signals": signals, "display_tags": [], "dropped": dropped, "confirmed": False, "confirmed_by": None,
            "prompt_version": PROFILE_VERSION}, inputs=profile_inputs(brief, ident, signals), rid=f"profile_{pid}")
        brand._activity(run, "Propose a discovery profile", "Profile agent",
                        f"{len(signals)} signals proposed · {len(dropped)} dropped by validation"
                        f" · {proposal['searches']['tags']} tag and {proposal['searches']['brands']} brand searches",
                        role="Profile agent")
        run.emit("profile", {"version": rec["version"], "route": rec["route"]})
        return {"applied": True, "version": rec["version"]}

    run.step("assemble", assemble)
    from . import scan
    scan.auto_start(pid)  # the proposal is what Markets waited for


HANDLERS["identity"] = identity_run
HANDLERS["profile"] = profile_run
