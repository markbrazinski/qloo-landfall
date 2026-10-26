"""HTTP API (CONTRACTS §9) and the static frontend."""
import asyncio
import json
import secrets
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import brand, config, db, fetch, identity, markets, qloo, ranking, runner, scan  # these register their run handlers
from .cassette import NoRecording
from .ledger import BudgetExhausted

WEB = config.ROOT / "web" / "dist"
DEFAULT_DEMO = config.ROOT / "fixtures" / "synthetic" / "tidewell-brand-v6"
ROW_LABELS = dict(brand.ROWS)


@asynccontextmanager
async def lifespan(_):
    runner.resume_all()
    yield


app = FastAPI(title="Landfall", lifespan=lifespan)


@app.middleware("http")
async def session_cookie(request: Request, call_next):
    sid = request.cookies.get("lf_session") or secrets.token_urlsafe(18)
    request.state.session = sid
    resp = await call_next(request)
    if "lf_session" not in request.cookies:
        resp.set_cookie("lf_session", sid, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 90)
    return resp


def _project(pid: str, request: Request):
    rows = db.q("SELECT * FROM projects WHERE id=? AND session=?", pid, request.state.session)
    if not rows:
        raise HTTPException(404, "No such project in this browser.")
    return rows[0]


def normalize_url(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        raise HTTPException(422, "Enter your brand's website.")
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        host = parts.hostname or ""
    except ValueError:
        raise HTTPException(422, "That doesn't look like a website address. Try something like yourbrand.com.")
    if parts.scheme not in ("http", "https") or "." not in host or " " in raw:
        raise HTTPException(422, "That doesn't look like a website address. Try something like yourbrand.com.")
    try:
        return fetch.check_url(ranking.strip_tracking(raw))
    except fetch.FetchBlocked as e:
        raise HTTPException(422, f"That address can't be read: {e}.")


class NewProject(BaseModel):
    url: str | None = None
    demo: bool = False
    objective: str | None = None
    ceiling_usd: int | None = None
    include: str | None = None
    exclude: str | None = None
    scope: Literal["international", "domestic", "both"] = "international"
    purpose: Literal["presence", "stockists", "test_demand"] = "presence"


@app.post("/api/projects", status_code=201)
def create_project(body: NewProject, request: Request):
    idem = request.headers.get("Idempotency-Key")
    if idem:
        hit = db.q("SELECT id FROM projects WHERE idem_key=? AND session=?", idem, request.state.session)
        if hit:
            run = db.q("SELECT id FROM runs WHERE project_id=? ORDER BY started_at LIMIT 1", hit[0]["id"])
            return {"project_id": hit[0]["id"], "run_id": run[0]["id"] if run else None}
    if body.demo:
        case = Path(config.env("LANDFALL_DEMO_CASE", str(DEFAULT_DEMO)))
        meta = json.loads((case / "case.json").read_text())
        url, mode, demo = meta["input"]["url"], "replay", str(case)
    else:
        url, mode, demo = normalize_url(body.url or ""), "live", None
    pid = db.new_id("prj")
    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", pid, request.state.session, url, mode, demo, idem, db.now())
    db.put_record("founder_input", pid, {
        "objective": body.objective or "Explore where to expand for me", "ceiling_usd": body.ceiling_usd,
        "include": body.include or None, "exclude": body.exclude or None, "scope": body.scope, "purpose": body.purpose,
        "leave_out_presence": True, "home_country": None}, rid=f"input_{pid}")
    run_id = runner.start(pid, "brand_read", "attempt-1", {"url": url})
    runner.start(pid, "identity", "attempt-1")  # the first reading task, in parallel with the site read
    return {"project_id": pid, "run_id": run_id}


@app.get("/api/projects/{pid}")
def get_project(pid: str, request: Request):
    p = _project(pid, request)
    return project_view(p)


@app.get("/api/projects/{pid}/brief")
def get_brief(pid: str, request: Request):
    return project_view(_project(pid, request))["brief"]


@app.get("/api/projects/{pid}/activity")
def get_activity(pid: str, request: Request):
    return project_view(_project(pid, request))["activity"]


class Correction(BaseModel):
    base_version: int
    claim_id: str
    value: str


@app.patch("/api/projects/{pid}/brief")
def correct_brief(pid: str, body: Correction, request: Request):
    _project(pid, request)
    value = body.value.strip()
    if not value:
        raise HTTPException(422, "Enter the corrected value.")
    with db._lock:
        brief = db.latest("brief", pid)
        if brief is None:
            raise HTTPException(409, "The brief isn't ready yet.")
        if brief["version"] != body.base_version:
            raise HTTPException(409, f"The brief changed (now v{brief['version']}). Reload and try again.")
        old = db.get_record(body.claim_id)
        if old is None or body.claim_id not in brief["facts"] + brief["interpretation"]:
            raise HTTPException(404, "That row isn't in the current brief.")
        new = db.put_record("claim", pid, {
            "subject": old["subject"], "label": old["label"], "value": value, "state": "STATED", "vc": "U",
            "source_ids": [], "replaces": body.claim_id, "produced_by": "founder"})
        swap = lambda ids: [new["id"] if i == body.claim_id else i for i in ids]
        payload = {k: v for k, v in brief.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")}
        payload.update(facts=swap(brief["facts"]), interpretation=swap(brief["interpretation"]),
                       corrections=brief["corrections"] + [{"label": old["label"], "old_claim_id": old["id"],
                                                            "new_claim_id": new["id"], "at": db.now()}])
        v2 = db.put_record("brief", pid, payload, inputs=brief["inputs"], rid=brief["id"])
    db.event(pid, "founder", {"task": f"Corrected {old['label'].lower()}", "detail": f"{old['value']} → {value}",
                              "brief_version": v2["version"]})
    db.event(pid, "brief", {"version": v2["version"]})
    # Interpretations and discovery-profile signals inferred from the corrected fact are flagged for review (nothing
    # re-runs without the founder); nothing else uses the brief yet.
    users = [db.get_record(i)["label"] for i in v2["interpretation"]
             if body.claim_id in (db.get_record(i).get("basis_claim_ids") or [])]
    prof = db.latest("profile", pid)
    signals = [s["name"] for s in (prof or {}).get("signals", []) if body.claim_id in (s.get("basis_claim_ids") or [])]
    parts = ([f"Our reading of {' and '.join(users)}"] if users else []) + (
        [f"your discovery profile's {'signal' if len(signals) == 1 else 'signals'} {', '.join(signals)}"] if signals else [])
    aset = db.latest("assessment_set", pid)
    shortlist = bool(aset) and body.claim_id in aset["inputs"][0]["keys"]  # the strategist was shown this fact (P3-01)
    tail = (" Your shortlist used it too: fit is updated now; its reasoning is not until you update the shortlist."
            if shortlist else " Nothing else has used it yet.")
    if parts:
        n = len(users) + len(signals)
        return {"version": v2["version"], "notice": f"Brief v{v2['version']}. {' and '.join(parts)} used this fact; "
                f"review {'it' if n == 1 else 'them'}.{tail}"}
    return {"version": v2["version"], "notice": f"Brief v{v2['version']}." + (tail if shortlist else " Nothing has used it yet.")}


def _start_identity_if_unsettled(pid: str) -> str | None:
    """D12: the name lookup also runs on a brand retry or the description route, unless it already has an answer."""
    ident = db.latest("identity", pid)
    if ident and ident["status"] != "failed":
        return None
    active = db.q("SELECT id FROM runs WHERE project_id=? AND kind='identity' AND status IN ('queued','running')", pid)
    if active:
        return active[0]["id"]
    n = db.q("SELECT COUNT(*) n FROM runs WHERE project_id=? AND kind='identity'", pid)[0]["n"]
    return runner.start(pid, "identity", f"attempt-{n + 1}")


@app.post("/api/projects/{pid}/brand-read/retry")
def retry(pid: str, request: Request):
    p = _project(pid, request)
    n = db.q("SELECT COUNT(*) n FROM runs WHERE project_id=? AND kind='brand_read'", pid)[0]["n"]
    run_id = runner.start(pid, "brand_read", f"attempt-{n + 1}", {"url": p["url"]})
    _start_identity_if_unsettled(pid)
    return {"run_id": run_id}


class Description(BaseModel):
    text: str


@app.post("/api/projects/{pid}/brand-read/description")
def describe(pid: str, body: Description, request: Request):
    p = _project(pid, request)
    text = body.text.strip()
    if len(text) < 8:
        raise HTTPException(422, "Tell us in a sentence what you sell.")
    run_id = runner.start(pid, "brand_describe", text[:200], {"url": p["url"], "description": text[:500]})
    _start_identity_if_unsettled(pid)
    return {"run_id": run_id}


# ---- identity and discovery profile (CONTRACTS B3) ----
class IdentityChoice(BaseModel):
    base_version: int
    entity_id: str  # a candidate's qloo_id, or "none"


@app.post("/api/projects/{pid}/identity")
def choose_identity(pid: str, body: IdentityChoice, request: Request):
    _project(pid, request)
    with db._lock:
        ident = db.latest("identity", pid)
        if ident is None or ident["status"] not in ("needs_founder", "decided"):
            raise HTTPException(409, "There is no Qloo answer to choose from yet." if not ident or ident["status"] != "failed"
                                else "The Qloo lookup failed; retry it first.")
        if ident["version"] != body.base_version:
            raise HTTPException(409, f"Your Qloo identity changed (now v{ident['version']}). Reload and try again.")
        if body.entity_id != "none" and body.entity_id not in {c["qloo_id"] for c in ident["candidates"]}:
            raise HTTPException(422, "That isn't one of the Qloo candidates.")
        rec = identity.decide_by_founder(pid, ident, body.entity_id)
    return {"version": rec["version"]}


@app.post("/api/projects/{pid}/identity/retry")
def retry_identity(pid: str, request: Request):
    """Identity only: the brand reader is never re-run and the brief is unchanged."""
    _project(pid, request)
    run_id = _start_identity_if_unsettled(pid)
    if run_id is None:
        raise HTTPException(409, "Your Qloo identity is already settled.")
    return {"run_id": run_id}


class SignalIn(BaseModel):
    qloo_id: str
    kind: Literal["tag", "entity"]
    included: bool = True


class ProfileEdit(BaseModel):
    base_version: int
    signals: list[SignalIn]
    confirm: bool = False


@app.put("/api/projects/{pid}/profile")
def edit_profile(pid: str, body: ProfileEdit, request: Request):
    _project(pid, request)
    with db._lock:
        ident, brief, prof = db.latest("identity", pid), db.latest("brief", pid), db.latest("profile", pid)
        if not ident or ident["status"] != "decided" or ident["decision"] != "none" or not brief:
            raise HTTPException(409, "Your profile is your Qloo brand. Use “Not you?” to change it."
                                if ident and ident.get("decision") not in (None, "none") else "There is no profile to edit yet.")
        if (prof["version"] if prof else 0) != body.base_version:
            raise HTTPException(409, f"Your profile changed (now v{prof['version']}). Reload and try again.")
        current = prof if identity.follows(prof, ident) else None
        existing = {s["qloo_id"]: s for s in (current or {}).get("signals", [])}
        out = []
        for s in body.signals:
            if any(x["qloo_id"] == s.qloo_id for x in out):
                continue
            if s.qloo_id in existing:
                out.append({**existing[s.qloo_id], "included": s.included})
                continue
            ref = qloo.recorded(pid, s.qloo_id)  # a founder search result counts; an invented id never does
            if not ref or ref["kind"] != s.kind or (s.kind == "tag" and not identity.allowed(ref["type"])):
                raise HTTPException(422, "That signal didn't come from a Qloo search for this project.")
            out.append({"kind": s.kind, "qloo_id": s.qloo_id, "name": ref["name"],
                        "tag_type": ref["type"] if s.kind == "tag" else None, "origin": "YOU_SAID",
                        "included": s.included, "basis_claim_ids": [], "reasoning": None, "basis_established": None})
        if sum(x["included"] for x in out) > identity.MAX_SIGNALS:
            raise HTTPException(422, f"A profile has at most {identity.MAX_SIGNALS} signals.")
        if body.confirm and not any(x["included"] for x in out):
            raise HTTPException(422, "Keep or add at least one signal to confirm the profile.")
        rec = db.put_record("profile", pid, {
            "route": (current or {}).get("route") or ("description_profile" if brief["route"] == "description"
                                                       else "proposed_profile"),
            "signals": out, "display_tags": [], "dropped": (current or {}).get("dropped", []), "confirmed": body.confirm,
            "confirmed_by": "founder" if body.confirm else None,
            "prompt_version": (current or {}).get("prompt_version")},
            inputs=identity.profile_inputs(brief, ident, out), rid=f"profile_{pid}")
    removed = [x["name"] for q, x in existing.items() if q not in {o["qloo_id"] for o in out}]
    added = [x["name"] for x in out if x["qloo_id"] not in existing]
    detail = " · ".join(filter(None, [removed and f"removed {', '.join(removed)}", added and f"added {', '.join(added)}",
                                      f"{sum(x['included'] for x in out)} signals in use"]))
    db.event(pid, "founder", {"task": "Confirmed discovery profile" if body.confirm else "Edited discovery profile",
                              "detail": detail, "profile_version": rec["version"]})
    if body.confirm:
        ranking.prepare(pid)  # stand-ins and their audiences, before the scan
    return {"version": rec["version"], "confirmed": body.confirm}


@app.post("/api/projects/{pid}/profile/retry")
def retry_profile(pid: str, request: Request):
    """Re-runs only the Profile agent; the editor stays usable meanwhile."""
    _project(pid, request)
    ident = db.latest("identity", pid)
    if not ident or ident.get("decision") != "none" or not db.latest("brief", pid):
        raise HTTPException(409, "A proposed profile needs a brief and no Qloo match.")
    n = db.q("SELECT COUNT(*) n FROM runs WHERE project_id=? AND kind='profile'", pid)[0]["n"]
    return {"run_id": runner.start(pid, "profile", f"identity-v{ident['version']}-retry-{n}",
                                   {"identity_version": ident["version"]})}


FOUNDER_SEARCHES_PER_HOUR = 20
_searching: dict[str, int] = {}  # founder searches admitted and not yet answered, per project


@contextmanager
def founder_search_slot(pid: str):
    """Atomic admission (review slice 2, blocker 4): searches answered in the last hour plus those in flight, counted
    and reserved under one lock before any provider work; the lock is never held across the request.
    ponytail: in-process count, fine for the single-instance design (BUILD-PLAN §2.4); a shared table if that changes."""
    hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    with db._lock:
        done = db.q("SELECT COUNT(*) n FROM usage WHERE project_id=? AND provider='qloo' "
                    "AND stage IN ('profile_edit','standins_edit') AND at>?", pid, hour_ago)[0]["n"]
        if done + _searching.get(pid, 0) >= FOUNDER_SEARCHES_PER_HOUR:
            raise HTTPException(429, f"That's {FOUNDER_SEARCHES_PER_HOUR} Qloo searches in the last hour. Try again later.")
        _searching[pid] = _searching.get(pid, 0) + 1
    try:
        yield  # an answered search writes its usage row before the slot is released, so it is never uncounted
    finally:
        with db._lock:
            _searching[pid] -= 1


@app.get("/api/projects/{pid}/qloo/tags")
def search_tags(pid: str, request: Request, q: str, type: str | None = None):
    """The founder's "Add a signal" search: server-side, ledgered, cached 7 days, at most 20 per project per hour."""
    _project(pid, request)
    term = " ".join(q.split())[:60]
    if len(term) < 2:
        raise HTTPException(422, "Type at least two letters.")
    try:
        with founder_search_slot(pid):
            out, usable, hidden = identity.tag_search(pid, None, runner.cassette_for(pid), "profile_edit", term, type)
    except NoRecording:
        raise HTTPException(503, "This recording has no Qloo search for that word. Searching runs live in a live project.")
    except BudgetExhausted:
        raise HTTPException(503, "Live Qloo search is paused: Landfall's operating allowance is reached.")
    if out.get("status") != 200:
        raise HTTPException(502, f"Qloo search failed ({out.get('error') or 'HTTP ' + str(out.get('status'))}). Try again.")
    db.event(pid, "activity", {"stage": "Brand", "role": "You", "tool": "Qloo tag search", "task": f"Search Qloo tags: '{term}'",
                               "detail": f"1 request · {len(usable)} usable tags" + (" · from cache" if out.get("cache") else ""),
                               "status": "DONE"})
    return {"query": term, "results": usable, "hidden": hidden}


# ---- slice 3.5: goal, presence, stand-ins, audiences, ranking (CONTRACTS B3.5) ----
def _fields(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")}


class IntentEdit(BaseModel):
    base_version: int
    scope: Literal["international", "domestic", "both"] | None = None
    purpose: Literal["presence", "stockists", "test_demand"] | None = None
    leave_out_presence: bool | None = None
    home_country: str | None = None


GOAL_NOTICE = {"scope": "re-ranks your candidate cities (1–2 Qloo requests)", "leave_out_presence": "re-ranks your "
               "candidate cities (1–2 Qloo requests)", "home_country": "re-ranks your candidate cities (1–2 Qloo requests)",
               "purpose": "reorders within blocks of 4 and rewrites the explanations (no Qloo requests)"}


@app.patch("/api/projects/{pid}/intent")
def edit_intent(pid: str, body: IntentEdit, request: Request):
    _project(pid, request)
    change = body.model_dump(exclude_unset=True, exclude={"base_version"})
    if "home_country" in change and change["home_country"] is not None:
        change["home_country"] = change["home_country"].upper()
        if change["home_country"] not in markets.COUNTRY_NAME:
            raise HTTPException(422, "That isn't a country code we know.")
    with db._lock:
        fi = db.latest("founder_input", pid)
        if fi["version"] != body.base_version:
            raise HTTPException(409, f"Your goal changed (now v{fi['version']}). Reload and try again.")
        rec = db.put_record("founder_input", pid, {**_fields(fi), **change}, inputs=fi["inputs"], rid=fi["id"])
    words = {"scope": lambda v: f"where: {ranking.SCOPE_WORDS[v]}", "purpose": lambda v: f"why: {ranking.PURPOSE_WORDS[v]}",
             "leave_out_presence": lambda v: "leave out where you already are" if v else "keep cities where you already are",
             "home_country": lambda v: f"home country: {markets.COUNTRY_NAME.get(v, 'not set')}"}
    db.event(pid, "founder", {"stage": "Markets", "task": "Changed your goal", "intent_version": rec["version"],
                              "detail": " · ".join(words[k](v) for k, v in change.items())})
    has = bool(db.latest("assessment_set", pid))
    return {"version": rec["version"], "notice": f"Goal v{rec['version']}." + (
        " Your recommendations are unchanged until you update: Update " + "; ".join(dict.fromkeys(GOAL_NOTICE[k] for k in change))
        + ". No new place scan." if has and change else "")}


class PresenceEdit(BaseModel):
    base_version: int
    add_cities: list[str] = []
    remove_cities: list[str] = []
    leave_out_countries: list[str] = []
    restore_countries: list[str] = []


@app.get("/api/projects/{pid}/presence")
def get_presence(pid: str, request: Request):
    _project(pid, request)
    return ranking.presence_view(pid)


@app.put("/api/projects/{pid}/presence")
def edit_presence(pid: str, body: PresenceEdit, request: Request):
    _project(pid, request)
    if set(body.add_cities + body.remove_cities) - set(markets.CITIES):
        raise HTTPException(422, "That city isn't in the catalog.")
    if {c.upper() for c in body.leave_out_countries + body.restore_countries} - set(markets.COUNTRY_NAME):
        raise HTTPException(422, "That isn't a country code we know.")
    with db._lock:
        cp = db.latest("current_presence", pid)
        if (cp["version"] if cp else 0) != body.base_version:
            raise HTTPException(409, f"Your presence changed (now v{cp['version'] if cp else 0}). Reload and try again.")
        o = ranking._overrides(cp)
        for c in body.add_cities:
            o["add_cities"] = [*dict.fromkeys(o["add_cities"] + [c])]
            o["remove_cities"] = [x for x in o["remove_cities"] if x != c]
        for c in body.remove_cities:
            o["remove_cities"] = [*dict.fromkeys(o["remove_cities"] + [c])]
            o["add_cities"] = [x for x in o["add_cities"] if x != c]
        for iso in (x.upper() for x in body.leave_out_countries):
            o["leave_out_countries"] = [*dict.fromkeys(o["leave_out_countries"] + [iso])]
            o["restore_countries"] = [x for x in o["restore_countries"] if x != iso]
        for iso in (x.upper() for x in body.restore_countries):
            o["leave_out_countries"] = [x for x in o["leave_out_countries"] if x != iso]
        payload = {**_fields(cp), "overrides": o} if cp else {"identity": None, "store_search": None, "stores": [],
                                                             "located_checks": [], "overrides": o}
        rec = db.put_record("current_presence", pid, payload, inputs=cp["inputs"] if cp else [], rid=f"cp_{pid}")
    names = lambda ids: ", ".join(markets.CITIES[i]["name"] for i in ids)  # noqa: E731
    countries = lambda ids: ", ".join(markets.COUNTRY_NAME[i.upper()] for i in ids)  # noqa: E731
    detail = " · ".join(filter(None, [body.add_cities and f"already in {names(body.add_cities)}",
                                      body.remove_cities and f"not yet in {names(body.remove_cities)}",
                                      body.leave_out_countries and f"leave out {countries(body.leave_out_countries)}",
                                      body.restore_countries and f"include {countries(body.restore_countries)}"]))
    db.event(pid, "founder", {"stage": "Markets", "task": "Changed where you already are", "detail": detail,
                              "presence_version": rec["version"]})
    return {"version": rec["version"]}


class StandInsEdit(BaseModel):
    base_version: int
    brands: list[str]


@app.get("/api/projects/{pid}/standins")
def get_standins(pid: str, request: Request):
    _project(pid, request)
    return ranking.standins_view(pid)


@app.put("/api/projects/{pid}/standins")
def edit_standins(pid: str, body: StandInsEdit, request: Request):
    """Swap, remove or add brands with a similar following. Never versions the discovery profile or the scan."""
    _project(pid, request)
    if ranking.brand_entity(pid) is not None:
        raise HTTPException(409, "Qloo knows your brand, so it ranks with your own brand, not stand-ins.")
    if len(set(body.brands)) > 5:
        raise HTTPException(422, "Choose at most 5 brands.")
    with db._lock:
        si = db.latest("standins", pid)
        if (si["version"] if si else 0) != body.base_version:
            raise HTTPException(409, f"Your stand-in brands changed (now v{si['version'] if si else 0}). Reload and try again.")
        known = {b["qloo_id"]: b for b in (si or {}).get("brands", []) + (si or {}).get("excluded", [])}
        brands = []
        for i, q in enumerate(dict.fromkeys(body.brands), 1):
            ref = qloo.recorded(pid, q)
            if q not in known and (not ref or ref["kind"] != "entity" or ref["type"] != "urn:entity:brand"):
                raise HTTPException(422, "That brand didn't come from a Qloo brand search for this project.")
            b = known.get(q) or {"qloo_id": q, "name": ref["name"], "origin": "YOU_SAID"}
            brands.append({**{k: v for k, v in b.items() if k != "reason"}, "rank_in_request": b.get("rank_in_request")})
        prof = db.latest("profile", pid)
        payload = {**(_fields(si) if si else {"basis_tags": [], "genre": None, "excluded": [], "request_id": None}),
                   "brands": brands, "status": "ok" if brands else "none"}
        rec = db.put_record("standins", pid, payload, inputs=si["inputs"] if si else
                            [{"id": prof["id"], "version": prof["version"], "keys": ["signals"]}], rid=f"si_{pid}")
    db.event(pid, "founder", {"stage": "Markets", "task": "Changed brands with a similar following",
                              "detail": ", ".join(b["name"] for b in brands) or "none", "standins_version": rec["version"]})
    return {"version": rec["version"]}


@app.get("/api/projects/{pid}/qloo/brands")
def search_brands(pid: str, request: Request, q: str):
    """The founder's "add a brand with a similar following" search: server-side, ledgered, shared hourly limit."""
    _project(pid, request)
    term = " ".join(q.split())[:60]
    if len(term) < 2:
        raise HTTPException(422, "Type at least two letters.")
    try:
        with founder_search_slot(pid):
            out = qloo.get("/search", {"query": term, "types": "urn:entity:brand", "take": 5}, runner.cassette_for(pid),
                           "standins_edit", pid)
    except NoRecording:
        raise HTTPException(503, "This recording has no Qloo search for that name. Searching runs live in a live project.")
    except BudgetExhausted:
        raise HTTPException(503, "Live Qloo search is paused: Landfall's operating allowance is reached.")
    if out.get("status") != 200:
        raise HTTPException(502, f"Qloo search failed ({out.get('error') or 'HTTP ' + str(out.get('status'))}). Try again.")
    ents, _ = qloo.rows(out)
    return {"query": term, "results": [{"qloo_id": e["entity_id"], "name": e.get("name"), "short_description":
                                        ((e.get("properties") or {}).get("short_description") or "")[:160]} for e in ents]}


class AudienceEdit(BaseModel):
    base_version: int
    remove: list[str]


@app.get("/api/projects/{pid}/audience")
def get_audience(pid: str, request: Request):
    _project(pid, request)
    return ranking.audience_view(pid)


@app.put("/api/projects/{pid}/audience")
def edit_audience(pid: str, body: AudienceEdit, request: Request):
    _project(pid, request)
    try:
        return {"version": ranking.remove_audiences(pid, body.base_version, body.remove)["version"]}
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except LookupError as e:
        raise HTTPException(422, str(e))


@app.post("/api/projects/{pid}/audience/retry")
def retry_audience(pid: str, request: Request):
    """Asks Qloo again for the part that failed (audiences or demographics); 1 Qloo each."""
    _project(pid, request)
    ap = db.latest("audience_profile", pid)
    if not ap or "failed" not in ap["status"].values():
        raise HTTPException(409, "Nothing about your audiences failed.")
    # keyed by the version that failed: a second click while it runs gets the same run, never a duplicate
    return {"run_id": runner.start(pid, "audience", f"retry-v{ap['version']}")}


@app.post("/api/projects/{pid}/ranking/retry")
def retry_ranking(pid: str, request: Request):
    _project(pid, request)
    if db.q("SELECT 1 FROM runs WHERE project_id=? AND kind IN ('scan','rank','shortlist') AND status IN ('queued','running')", pid):
        raise HTTPException(409, "A scan or ranking is still running.")
    rk = db.latest("qloo_ranking", pid)
    if not rk or rk["state"] not in ranking.RETRYABLE:
        raise HTTPException(409, "There is no failed or partial ranking to retry.")
    try:
        return {"run_id": scan.start_shortlist(pid, "retry_ranking")}
    except ValueError as e:
        raise HTTPException(409, str(e))


# ---- markets: scan and shortlist (CONTRACTS B4/B5) ----
@app.post("/api/projects/{pid}/scan")
def start_scan(pid: str, request: Request):
    """Returns the existing generation for the confirmed profile version, else creates one and starts the scan."""
    _project(pid, request)
    try:
        return scan.start_scan(pid)
    except ValueError as e:
        raise HTTPException(409, str(e))


def _shown_generation(pid: str) -> dict:
    prof = scan.current_profile(pid)
    gen = scan.generation_for(pid, prof) if prof else None
    if not gen:
        raise HTTPException(409, "There is no scan for your current profile.")
    return gen


@app.post("/api/projects/{pid}/scan/retry")
def retry_scan(pid: str, request: Request):
    _project(pid, request)
    gen = _shown_generation(pid)
    if db.q("SELECT 1 FROM runs WHERE project_id=? AND kind='scan' AND status IN ('queued','running')", pid):
        raise HTTPException(409, "The scan is still running.")
    try:
        return {"run_id": scan.retry_scan(pid, gen["id"])}
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/projects/{pid}/shortlist/retry")
def retry_shortlist(pid: str, request: Request):
    _project(pid, request)
    try:
        return {"run_id": scan.start_shortlist(pid, "retry")}
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/projects/{pid}/shortlist/update")
def update_shortlist(pid: str, request: Request):
    _project(pid, request)
    try:
        return {"run_id": scan.start_shortlist(pid, "update")}
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/api/projects/{pid}/markets")
def get_markets(pid: str, request: Request):
    _project(pid, request)
    return scan.markets_view(pid)


class ShortlistEdit(BaseModel):
    base_version: int
    include: list[str] = []
    exclude: list[str] = []
    add: list[str] = []


@app.patch("/api/projects/{pid}/shortlist")
def edit_shortlist(pid: str, body: ShortlistEdit, request: Request):
    _project(pid, request)
    try:
        rec = scan.edit_shortlist(pid, body.base_version, body.include, body.exclude, body.add)
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
    except LookupError as e:
        raise HTTPException(422, str(e))
    return {"version": rec["version"]}


@app.get("/api/projects/{pid}/qloo/places/{qloo_id}")
def get_place(pid: str, qloo_id: str, request: Request, city: str):
    _project(pid, request)
    v = scan.place_view(pid, qloo_id, city)
    if v is None:
        raise HTTPException(404, "That place didn't come back in this city's scan.")
    return v


@app.post("/api/runs/{run_id}/cancel")
def cancel(run_id: str, request: Request):
    r = db.q("SELECT project_id FROM runs WHERE id=?", run_id)
    if not r:
        raise HTTPException(404)
    _project(r[0]["project_id"], request)
    runner.cancel(run_id)
    return {"ok": True}


@app.get("/api/sources/{sid}")
def source(sid: str, request: Request, claim: str):
    """Evidence for one claim: the source, the claim and the requesting session must share a project, and the claim
    must cite this source. Anything else is "not found" (no cross-project or misattributed excerpts)."""
    rows = db.q("SELECT * FROM sources WHERE id=?", sid)
    if not rows:
        raise HTTPException(404)
    s = rows[0]
    _project(s["project_id"], request)
    c = db.get_record(claim)
    if c is None or c.get("project_id") != s["project_id"] or sid not in (c.get("source_ids") or []):
        raise HTTPException(404, "No such evidence for this claim.")
    return {"id": s["id"], "url": s["url"], "title": s["title"], "fetched_at": s["fetched_at"], "status": s["status"],
            "replayed": bool(s["replayed"]), "excerpt": (c.get("quote") or "")[:300],
            "claim": {"id": c["id"], "field": (c.get("subject") or {}).get("key"), "label": c.get("label"),
                      "value": c.get("value"), "state": c.get("state"), "reason": c.get("reason")}}


@app.get("/api/projects/{pid}/events")
async def stream(pid: str, request: Request, after: int = 0):
    _project(pid, request)

    async def gen():
        seq = after
        while not await request.is_disconnected():
            for e in db.events(pid, seq):
                seq = e["seq"]
                yield {"id": str(seq), "data": json.dumps(e)}
            await asyncio.sleep(0.4)
    return EventSourceResponse(gen(), ping=15)


# ---- view assembly (records → screen views; never stored) ----
def project_view(p) -> dict:
    pid = p["id"]
    run = db.q("SELECT * FROM runs WHERE project_id=? AND kind IN ('brand_read','brand_describe') "
               "ORDER BY started_at DESC, rowid DESC LIMIT 1", pid)
    run = run[0] if run else None
    reading = {k: {"label": v, "status": "QUEUED", "detail": ""} for k, v in ROW_LABELS.items()}
    reading["open"]["label"] = f"Opening {fetch.site_of(p['url'])}"
    if run:
        for e in db.events(pid, 0, "reading"):
            if e["run_id"] == run["id"]:
                reading[e["row"]].update(status=e["status"], detail=e["detail"])
    brief = db.latest("brief", pid)
    fi = db.latest("founder_input", pid)
    usage = db.q("SELECT COUNT(*) calls, COALESCE(SUM(est_usd),0) usd, COALESCE(SUM(input_tokens),0) tin, "
                 "COALESCE(SUM(output_tokens),0) tout FROM usage WHERE project_id=? AND provider='bedrock'", pid)[0]
    qloo_n = db.q("SELECT COALESCE(SUM(origin!='live' OR attempt IS NULL OR attempt=1),0) n, COALESCE(SUM(origin='live'),0) "
                  "live, COALESCE(SUM(origin='cache'),0) cached FROM usage WHERE project_id=? AND provider='qloo'", pid)[0]
    ident = identity_view(p)
    prof = profile_view(pid, ident, brief)
    pages = db.q("SELECT COUNT(*) n, SUM(status=200) ok FROM sources WHERE project_id=?", pid)[0]
    activity = [e for e in db.events(pid) if e["kind"] in ("activity", "founder")]
    detail = json.loads(run["detail"]) if run else {}
    return {
        "id": pid, "url": p["url"], "site": fetch.site_of(p["url"]), "mode": p["mode"],
        "demo": p["mode"] == "replay", "created_at": p["created_at"],
        "intent": fi and {k: fi.get(k) for k in ("objective", "ceiling_usd", "include", "exclude", "scope", "purpose",
                                                  "leave_out_presence", "home_country")} | {"version": fi["version"]},
        "goal": _goal_view(pid, ident, prof),
        "run": run and {"id": run["id"], "kind": run["kind"], "status": run["status"], "message": detail.get("message", "")},
        "reading": [ident.pop("row"), *({"key": k, **v} for k, v in reading.items())],
        "brief": brief and _brief_view(brief),
        "identity": ident, "profile": prof, "markets_reason": _markets_reason(ident, prof),
        "markets": _markets_stage(pid, ident, prof),
        "activity": activity,
        "usage": {"model_calls": usage["calls"], "est_usd": round(usage["usd"], 4), "input_tokens": usage["tin"],
                  "output_tokens": usage["tout"], "pages_fetched": pages["n"], "pages_read": pages["ok"] or 0,
                  "qloo_requests": qloo_n["n"], "qloo_lookups": qloo_n["n"], "qloo_live": qloo_n["live"],
                  "qloo_sent": qloo_n["live"], "qloo_cached": qloo_n["cached"],
                  "replayed": p["mode"] == "replay"},
        "last_seq": (db.q("SELECT MAX(seq) s FROM events WHERE project_id=?", pid)[0]["s"] or 0),
    }


def _source_ref(sid: str) -> dict | None:
    s = db.q("SELECT id, url, fetched_at FROM sources WHERE id=?", sid)
    return s and {"id": s[0]["id"], "url": s[0]["url"], "fetched_at": s[0]["fetched_at"]}


def _latest_run(pid: str, kind: str):
    r = db.q("SELECT * FROM runs WHERE project_id=? AND kind=? ORDER BY started_at DESC, rowid DESC LIMIT 1", pid, kind)
    return r[0] if r else None


def identity_view(p) -> dict:
    """Settled = decided or failed (P2-02): the brief may open; searching or needs_founder keep the reading screen."""
    pid = p["id"]
    ident, run = db.latest("identity", pid), _latest_run(pid, "identity")
    active = bool(run) and run["status"] in ("queued", "running")
    message = json.loads(run["detail"]).get("message", "") if run else ""
    if active or (run and not ident):
        status = "searching" if active else "failed"  # a run that ended without a record failed before deciding
    else:
        status = ident["status"] if ident else "not_started"
    names = db.q("SELECT result FROM tasks WHERE run_id=? AND task_key='names' AND status='done'", run["id"]) if run else []
    site_name = (ident or {}).get("site_name") or (json.loads(names[0]["result"])["site_name"] if names else None)
    cands = (ident or {}).get("candidates", []) if status != "searching" else []
    chosen = next((c for c in cands if isinstance((ident or {}).get("decision"), dict)
                   and c["qloo_id"] == ident["decision"]["entity_id"]), None)
    reason = (ident or {}).get("failure_reason") or message or "the lookup did not finish"
    if status == "searching":
        row = ("RUNNING", f"Searching Qloo for '{site_name}'" if site_name else "Waiting for your homepage")
    elif status == "needs_founder":
        row = ("NEEDS YOU", f"{len(cands)} possible match{'es' if len(cands) != 1 else ''} · needs you")
    elif status == "failed":
        row = ("FAILED", "Qloo unavailable · retry")
    elif status == "not_started":
        row = ("QUEUED", "Not looked up yet")
    elif chosen:
        row = ("DONE", f"{'Matched automatically' if ident['decided_by'] == 'landfall' else 'You chose'}: {chosen['name']}")
    else:
        row = ("DONE", "No match · we'll propose a profile" if ident["decided_by"] == "landfall"
               else "Neither · we'll propose a profile")
    return {
        "row": {"key": "qloo", "label": f"Finding {site_name or fetch.site_of(p['url'])} in Qloo", "status": row[0],
                "detail": row[1]},
        "status": status, "settled": status in ("decided", "failed", "not_started"),
        "id": ident and ident["id"], "version": ident and ident["version"], "site_name": site_name,
        "name_source": (ident or {}).get("name_source"), "queries": (ident or {}).get("queries", []),
        "candidates": cands, "chosen": chosen, "decision": (ident or {}).get("decision"),
        "decided_by": (ident or {}).get("decided_by"), "basis": (ident or {}).get("basis", []), "project_id": pid,
        "failure_reason": reason if status == "failed" else None, "attempts": (ident or {}).get("attempts"),
        "history": len(db.records("identity", pid))}


def profile_view(pid: str, ident: dict, brief: dict | None) -> dict | None:
    prof, run = db.latest("profile", pid), _latest_run(pid, "profile")
    run_view = run and {"id": run["id"], "status": run["status"], "message": json.loads(run["detail"]).get("message", "")}
    if not prof and not run:
        return None
    current = set(brief["facts"]) if brief else set()
    follows = bool(prof) and ident["id"] is not None and any(
        i["id"] == ident["id"] and i["version"] == ident["version"] for i in prof["inputs"])
    signals = []
    for s in (prof or {}).get("signals", []):
        view = {k: s.get(k) for k in ("kind", "qloo_id", "name", "tag_type", "origin", "included", "reasoning")}
        view["type_label"] = identity.type_label(s.get("tag_type")) if s.get("tag_type") else None
        if s.get("basis_claim_ids"):
            view["inferred_from"] = {**_basis_view(s["basis_claim_ids"], current), "reasoning": s.get("reasoning") or "",
                                     "established": bool(s.get("basis_established")), "pages": [],
                                     "dropped": s.get("basis_dropped") or []}
        signals.append(view)
    tags = (prof or {}).get("display_tags", [])
    groups: dict[str, list] = {}
    for t in tags:
        groups.setdefault(identity.type_label(t["type"]), []).append(t)
    # Judged against the current identity: an older profile (e.g. the brand route before "Not you? → Neither") never
    # stands in for the new route's proposal, its failure or its retry (review slice 2, blocker 3).
    run_current = bool(run) and ident["version"] is not None and \
        json.loads(run["detail"])["params"].get("identity_version") == ident["version"]
    active = bool(run) and run["status"] in ("queued", "running")
    state = ("ready" if follows else "proposing" if active and run_current
             else "failed" if run_current and run["status"] in ("failed", "partial", "cancelled") else "waiting")
    return {"id": prof and prof["id"], "version": prof["version"] if prof else 0, "route": prof and prof["route"],
            "confirmed": bool(prof and prof["confirmed"]) and follows, "confirmed_by": prof and prof.get("confirmed_by"),
            "outdated": bool(prof) and not follows, "signals": signals if follows else [],
            "display_tags": [{"type": k, "tags": v} for k, v in groups.items()] if follows else [],
            "dropped": (prof or {}).get("dropped", []) if follows else [],
            "fact_changed": [s["name"] for s in signals if s.get("inferred_from", {}).get("changed")] if follows else [],
            "run": run_view, "state": state, "proposing": state == "proposing"}


def _goal_view(pid: str, ident: dict, prof: dict | None) -> dict | None:
    """The brief's slice-3.5 panels: where you already are, who likes you, stand-ins and the home-country question."""
    if ident["status"] != "decided":
        return None
    prep = _latest_run(pid, "prepare")
    cand = ranking.candidates(pid)
    return {"presence": ranking.presence_view(pid), "standins": ranking.standins_view(pid),
            "audience": ranking.audience_view(pid), "needs_home": cand["needs_home"],
            "home_unknown_international": cand["home_unknown_international"],
            "preparing": bool(prep) and prep["status"] in ("queued", "running"),
            "countries": sorted(({"iso": k, "name": v} for k, v in markets.COUNTRY_NAME.items()), key=lambda c: c["name"]),
            "catalog": sorted(({"city_id": c, "name": markets.CITIES[c]["name"]} for c in markets.ORDER), key=lambda c: c["name"])}


def _markets_reason(ident: dict, prof: dict | None) -> str:
    if ident["status"] == "failed":
        return f"Qloo identity not established ({ident['failure_reason']}). Retry the Qloo lookup first."
    if ident["status"] in ("searching", "needs_founder"):
        return "Settle which Qloo brand you are first."
    if ident["status"] == "not_started":
        return "Qloo identity not established. Retry the Qloo lookup first."
    if not prof or prof["outdated"] or not prof["confirmed"]:
        return "Confirm your discovery profile first."
    if prof["fact_changed"]:
        return "Review your discovery profile: a fact it uses changed."
    if ranking.candidates(ident["project_id"])["needs_home"]:
        return "Tell us which country is home first: your goal includes your home market."
    return ""


def _markets_stage(pid: str, ident: dict, prof: dict | None) -> dict:
    """none (blocked, with the reason) · ready · scanning · scanned. An older generation keeps Markets readable."""
    reason = _markets_reason(ident, prof)
    gens = scan.generations(pid)
    if not gens:
        return {"stage": "none" if reason else "ready", "reason": reason}
    current = scan.current_profile(pid)
    gen = scan.generation_for(pid, current) if current and not reason else None
    shown = gen or gens[-1]
    sl = db.latest("shortlist", pid)
    return {"stage": "scanning" if shown["status"] == "running" else "scanned", "reason": reason,
            "outdated": gen is None, "shortlisted": sum(c["included"] for c in (sl or {}).get("cities", [])) if sl else None}


def _basis_view(claim_ids: list[str], current: set[str] | None) -> dict:
    facts = [db.get_record(f) for f in claim_ids]
    return {"facts": [{"id": f["id"], "label": f["label"], "value": f["value"], "state": f["state"],
                       "source": _source_ref(f["source_ids"][0]) if f.get("source_ids") else None,
                       "quote": f.get("quote"), "reason": f.get("reason"), "quote_found": f.get("quote_found"),
                       "support": f.get("support")} for f in facts],
            # a cited fact the founder has since corrected: the reading must be reviewed, not trusted
            "changed": [f["label"] for f in facts if current is not None and f["id"] not in current]}


def _claim_view(cid: str, current: set[str] | None = None) -> dict:
    c = db.get_record(cid)
    src = _source_ref(c["source_ids"][0]) if c.get("source_ids") and c.get("state") != "INFERRED" else None
    view = {"id": c["id"], "label": c["label"], "value": c["value"], "state": c["state"], "vc": c.get("vc"),
            "basis": c.get("basis"), "reason": c.get("reason"), "quote": c.get("quote"), "source": src,
            "quote_found": c.get("quote_found"), "support": c.get("support"), "checker": c.get("checker"),
            "corrected": bool(c.get("replaces"))}
    if c.get("state") == "INFERRED" and "basis_claim_ids" in c:
        view["inferred_from"] = {
            "reasoning": c.get("reasoning", ""), "established": c.get("basis_established", False),
            **_basis_view(c["basis_claim_ids"], current),
            "pages": [_source_ref(s) for s in c.get("source_ids", [])], "dropped": c.get("basis_dropped", [])}
    return view


def _unknown_basis(b: dict) -> str:
    """Never a site-wide absence claim: Landfall reads some pages, and some of those may fail."""
    if b["route"] != "site":
        return "Not in your description"
    failed = sum(1 for e in db.events(b["project_id"], 0, "activity") if e.get("status") == "UNAVAILABLE")
    return "Not established from the pages read" + (f" ({failed} page{'s' if failed > 1 else ''} unavailable)" if failed else "")


def _brief_view(b: dict) -> dict:
    return {"id": b["id"], "version": b["version"], "brand_name": b["brand_name"], "route": b["route"],
            "unknown_basis": _unknown_basis(b),
            "facts": [_claim_view(i) for i in b["facts"]],
            "interpretation": [_claim_view(i, set(b["facts"])) for i in b["interpretation"]],
            "unknowns": b["unknowns"], "corrections": b["corrections"], "pages_read": b.get("pages_read"),
            "read_at": b.get("read_at")}


if WEB.exists():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        """Serve a file only if its fully resolved path (symlinks followed) stays inside the public build; every other
        path is an app route and gets index.html."""
        root = WEB.resolve()
        f = (root / path).resolve()
        if path and f.is_relative_to(root) and f.is_file():
            return FileResponse(f)
        return FileResponse(root / "index.html")
