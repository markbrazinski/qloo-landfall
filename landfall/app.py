"""HTTP API (CONTRACTS §9) and the static frontend."""
import asyncio
import hashlib
import json
import secrets
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from . import autoplan, brand, config, db, delivery, fetch, grants, identity, influencers, markets, mockups, plans, products, qloo, ranking, research, runner, scan, scenarios, specialist, strategist  # these register their run handlers
from .cassette import NoRecording
from .ledger import BudgetExhausted

WEB = config.ROOT / "web" / "dist"
DEFAULT_DEMO = config.ROOT / "fixtures" / "synthetic" / "tidewell-brand-v6"
# Owner, 28 Oct 2026: the demo track replays two real brands, recorded end to end (scripts/record_demo.py).
DEMO_CASES = {"deathwishcoffee": config.ROOT / "fixtures" / "demo" / "deathwishcoffee",
              "untuckit": config.ROOT / "fixtures" / "demo" / "untuckit", "tidewell": DEFAULT_DEMO}
DEFAULT_CEILING_USD = 10_000
ROW_LABELS = dict(brand.ROWS)


@asynccontextmanager
async def lifespan(_):
    delivery.startup_check()  # 6.7 G1: judging never sends directly
    delivery.recover()  # P6-R1: an action interrupted mid-write is UNCERTAIN, never redispatched
    influencers.recover()  # P66-R4: a creator request interrupted mid-flight is UNKNOWN, never resent
    runner.resume_all()
    yield


app = FastAPI(title="Landfall", lifespan=lifespan)


COOKIES = ("__Host-lf_session", "lf_session")
LINK_PARAMS = ("judge_id", "owner_id")


def _secure() -> bool:
    return (config.env("LANDFALL_PUBLIC_URL") or "").startswith("https://")


def _set_session(resp: Response, sid: str) -> None:
    """`__Host-` (Secure, Path=/, no Domain) when served over https, so a sibling subdomain can't plant it."""
    resp.set_cookie(COOKIES[0] if _secure() else COOKIES[1], sid, httponly=True, samesite="lax", secure=_secure(),
                    path="/", max_age=60 * 60 * 24 * 90)


def _grant_redirect(raw: str | None, to: str) -> Response:
    """DEPLOYMENT-PLAN §5.1: a valid, unrevoked id gets a FRESH session bound to its grant; an unknown or revoked one
    gets the same 303 and nothing else, so the response never says whether an id exists. The id is never logged."""
    resp = RedirectResponse(to, status_code=303)
    resp.headers["Referrer-Policy"] = "no-referrer"
    sid = grants.bind(raw.strip()) if raw else None
    if sid:
        _set_session(resp, sid)
    return resp


@app.middleware("http")
async def session_cookie(request: Request, call_next):
    if request.method == "GET" and any(k in request.query_params for k in LINK_PARAMS):
        raw = request.query_params.get("judge_id") or request.query_params.get("owner_id")
        rest = urlencode([(k, v) for k, v in request.query_params.multi_items() if k not in LINK_PARAMS])
        return _grant_redirect(raw, request.url.path + (f"?{rest}" if rest else ""))
    have = request.cookies.get(COOKIES[0]) or request.cookies.get(COOKIES[1])
    sid = have or secrets.token_urlsafe(18)
    request.state.session = sid
    resp = await call_next(request)
    if (COOKIES[0] if _secure() else COOKIES[1]) not in request.cookies:
        _set_session(resp, sid)
    return resp


ACCESS_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,
initial-scale=1"><meta name="referrer" content="no-referrer"><title>Landfall access</title><style>body{font:16px/1.5
system-ui,sans-serif;max-width:28rem;margin:4rem auto;padding:0 16px;color:#1b2430;background:#f7f5f0}input,button{font:inherit;
padding:.6rem;width:100%;box-sizing:border-box;margin:.4rem 0}button{background:#1b2430;color:#fff;border:0;border-radius:6px}
</style></head><body><h1>Use your access link</h1><p>If your link was cut short, paste the id from it (the part after
<code>judge_id=</code>).</p><form method="post" action="/access"><label for="id">Access id</label><input id="id" name="id"
autocomplete="off" required><button type="submit">Continue</button></form><p><a href="/">Back to Landfall</a></p></body></html>"""


@app.get("/access", response_class=HTMLResponse)
def access_page():
    """The paste fallback for a mangled link (DEPLOYMENT-PLAN §5.1)."""
    return HTMLResponse(ACCESS_PAGE, headers={"Referrer-Policy": "no-referrer"})


@app.post("/access")
async def access_submit(request: Request):
    raw = (parse_qs((await request.body()).decode(errors="replace")).get("id") or [""])[0].strip()
    pasted = parse_qs(urlsplit(raw).query)  # the whole link pasted: take its id
    return _grant_redirect((pasted.get("judge_id") or pasted.get("owner_id") or [raw])[0], "/")


@app.get("/api/session")
def session_view(request: Request):
    """This browser's track and the recorded runs on this machine (a fresh clone has only Tidewell); never the session
    or link id."""
    cases = [k for k, d in DEMO_CASES.items() if k != "tidewell" and (d / "case.json").exists()] or ["tidewell"]
    return {"track": grants.track(request.state.session), "grant_kind": grants.kind_of(request.state.session),
            "demo_cases": cases}


@app.exception_handler(runner.NotAllowed)
async def not_allowed(_request: Request, e: runner.NotAllowed):
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": str(e)}, status_code=403)


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
    demo_case: Literal["deathwishcoffee", "untuckit", "tidewell"] | None = None
    objective: str | None = None
    ceiling_usd: int | None = Field(None, gt=0, le=100_000_000)
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
            mode = db.q("SELECT mode FROM projects WHERE id=?", hit[0]["id"])[0]["mode"]
            return {"project_id": hit[0]["id"], "run_id": run[0]["id"] if run else None,
                    "track": "demo" if mode == "replay" else "live"}
    # Owner, 27 Oct 2026: with LANDFALL_REQUIRE_GRANT=on, a session without a judge or owner grant always gets the
    # recorded demo, whatever URL was typed.
    goal = {}
    if body.demo or grants.track(request.state.session) == "demo":
        case = DEMO_CASES.get(body.demo_case or "") or Path(config.env("LANDFALL_DEMO_CASE", str(DEFAULT_DEMO)))
        meta = json.loads((case / "case.json").read_text())
        url, mode, demo = meta["input"]["url"], "replay", str(case)
        goal = meta["input"].get("goal") or {}  # a recorded walk replays only under the goal it was recorded with
    else:
        url, mode, demo = normalize_url(body.url or ""), "live", None
    pid = db.new_id("prj")
    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", pid, request.state.session, url, mode, demo, idem, db.now())
    ceiling = {"ceiling_usd": body.ceiling_usd, "ceiling_by": "YOU_SAID"} if body.ceiling_usd else \
        {"ceiling_usd": DEFAULT_CEILING_USD, "ceiling_by": "DEFAULT"}  # 6.5 B1: untouched → the US$10,000 default
    db.put_record("founder_input", pid, {
        "objective": body.objective or "Explore where to expand for me", **ceiling,
        "include": body.include or None, "exclude": body.exclude or None, "scope": body.scope, "purpose": body.purpose,
        "leave_out_presence": True, "home_country": None, **goal}, rid=f"input_{pid}")
    run_id = runner.start(pid, "brand_read", "attempt-1", {"url": url})
    runner.start(pid, "identity", "attempt-1")  # the first reading task, in parallel with the site read
    return {"project_id": pid, "run_id": run_id, "track": "demo" if demo else "live"}


@app.get("/api/projects/{pid}")
def get_project(pid: str, request: Request):
    p = _project(pid, request)
    return project_view(p)


@app.get("/api/projects/{pid}/brief")
def get_brief(pid: str, request: Request):
    return project_view(_project(pid, request))["brief"]


@app.get("/api/projects/{pid}/products")
def get_products(pid: str, request: Request):
    _project(pid, request)
    return products.view(pid) or {"route": None, "items": [], "attempts": []}


@app.get("/api/projects/{pid}/products/{prid}/snapshot")
def product_snapshot(pid: str, prid: str, request: Request, version: int | None = None):
    """A product's saved page snapshot (P45-I7): what Landfall read, when, and where; project-authorized."""
    _project(pid, request)
    pr = db.get_record(prid, version)
    if not pr or pr["project_id"] != pid or pr.get("kind") != "product":
        raise HTTPException(404, "No such product.")
    s = db.q("SELECT url, final_url, fetched_at, title, text, replayed FROM sources WHERE id=? AND project_id=?", pr["snapshot_id"], pid)
    if not s:
        raise HTTPException(404, "The product's page snapshot isn't available.")
    s = dict(s[0])
    return {"id": pr["id"], "version": pr["version"], "title": pr["title"], "product_url": pr["product_url"],
            "price_text": pr.get("price_text"), "category_terms": pr["category_terms"], "materials": pr.get("materials") or [],
            "discovered_via": pr["discovered_via"], "snapshot": {"url": s["url"], "final_url": s["final_url"],
            "fetched_at": s["fetched_at"], "title": s["title"], "replayed": bool(s["replayed"]), "excerpt": (s["text"] or "")[:1500]}}


@app.post("/api/projects/{pid}/products", status_code=202)
def refresh_products(pid: str, request: Request):
    """Shared product research (BUILD-PLAN §4.5.2a): runs once before the first specialist, or on request."""
    _project(pid, request)
    if not db.latest("brief", pid):
        raise HTTPException(409, "Products are read after the brand brief.")
    return {"run_id": products.start(pid)}


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
    research_context: str | None = None
    ceiling_usd: int | None = Field(None, gt=0, le=100_000_000)


GOAL_NOTICE = {"scope": "re-ranks your candidate cities (1–2 Qloo requests)", "leave_out_presence": "re-ranks your "
               "candidate cities (1–2 Qloo requests)", "home_country": "re-ranks your candidate cities (1–2 Qloo requests)",
               "purpose": "reorders within blocks of 4 and rewrites the explanations (no Qloo requests)"}


@app.patch("/api/projects/{pid}/intent")
def edit_intent(pid: str, body: IntentEdit, request: Request):
    _project(pid, request)
    change = body.model_dump(exclude_unset=True, exclude={"base_version"})
    if "research_context" in change:  # the research note: queued cities read it at their start (slice 4, R5)
        if len(change) > 1:
            raise HTTPException(422, "Change the research note on its own.")
        try:
            rec = research.set_context(pid, body.base_version, change["research_context"])
        except research.Refused as e:
            raise HTTPException(e.code, e.detail)
        started = [c["name"] for c in research.opportunities_view(pid)["cities"] if c["status"] not in ("queued", "not_started")]
        return {"version": rec["version"], "notice": "Cities not yet started will use your new note."
                + (f" {', '.join(started)} started with the earlier one; re-research to use it." if started else "")}
    if "ceiling_usd" in change:  # 6.5 SH2: the launch-test ceiling; pins nothing upstream and starts no run
        if change["ceiling_usd"] is None:
            raise HTTPException(422, "Enter a ceiling in whole US dollars.")
        change["ceiling_by"] = "YOU_SAID"
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
             "home_country": lambda v: f"home country: {markets.COUNTRY_NAME.get(v, 'not set')}",
             "ceiling_usd": lambda v: f"ceiling: US${v:,}"}
    db.event(pid, "founder", {"stage": "Markets", "task": "Changed your goal", "intent_version": rec["version"],
                              "detail": " · ".join(words[k](v) for k, v in change.items() if k in words)})
    has = bool(db.latest("assessment_set", pid))
    ranked = [k for k in change if k in GOAL_NOTICE]
    bh = plans.beachhead(pid)
    if "ceiling_usd" in change and bh and db.get_record(plans.plan_id(pid, bh)):  # AC5-1: fit re-evaluated, 0 model calls
        prop = db.get_record(strategist.proposal_id(pid, bh))
        n = 1 + len((prop or {}).get("options", []))
        return {"version": rec["version"], "notice": f"Goal v{rec['version']}. {n} fit{'s' * (n > 1)} recalculated; research not re-run."}
    return {"version": rec["version"], "notice": f"Goal v{rec['version']}." + (
        " Your recommendations are unchanged until you update: Update " + "; ".join(dict.fromkeys(GOAL_NOTICE[k] for k in ranked))
        + ". No new place scan." if has and ranked else "")}


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
            o["restore_countries"] = [*dict.fromkeys(o["restore_countries"] + [iso])]  # un-ticks a where-sold country
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
    beachhead: str | None = None  # 6.5 M2; sent as null to clear it


@app.patch("/api/projects/{pid}/shortlist")
def edit_shortlist(pid: str, body: ShortlistEdit, request: Request):
    _project(pid, request)
    if body.beachhead and not runner.demo_allows(pid, "auto_plan", body.beachhead):  # before the edit, not after it
        raise HTTPException(409, runner.DEMO_LINE)
    try:
        rec = scan.edit_shortlist(pid, body.base_version, body.include, body.exclude, body.add,
                                  **({"beachhead": body.beachhead} if "beachhead" in body.model_fields_set else {}))
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
    except LookupError as e:
        raise HTTPException(422, str(e))
    if body.beachhead:  # owner, 27 Oct 2026: picking the city builds the plan
        autoplan.start(pid, body.beachhead)
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
        return research_evidence(sid, request, claim)
    s = rows[0]
    _project(s["project_id"], request)
    c = db.get_record(claim)
    if c is None or c.get("project_id") != s["project_id"] or sid not in (c.get("source_ids") or []):
        raise HTTPException(404, "No such evidence for this claim.")
    return {"id": s["id"], "url": s["url"], "title": s["title"], "fetched_at": s["fetched_at"], "status": s["status"],
            "replayed": bool(s["replayed"]), "excerpt": (c.get("quote") or "")[:300],
            "claim": {"id": c["id"], "field": (c.get("subject") or {}).get("key"), "label": c.get("label"),
                      "value": c.get("value"), "state": c.get("state"), "reason": c.get("reason")}}


def research_evidence(sid: str, request: Request, claim: str) -> dict:
    """A city claim mapped to the drawer: label = method · subject, value = text, the quote, its support judgment and the
    immutable snapshot it was checked against. SOURCES DIFFER shows both sources, newest first."""
    s = research.source(sid)
    if not s:
        raise HTTPException(404)
    _project(s["project_id"], request)
    c = db.get_record(claim)
    if c is None or c.get("kind") != "city_claim" or c["project_id"] != s["project_id"] or c.get("source_id") != sid:
        raise HTTPException(404, "No such evidence for this claim.")

    def one(c: dict, s: dict) -> dict:
        return {"id": s["id"], "url": s["url"], "final_url": s["final_url"], "title": s["title"], "fetched_at": s["fetched_at"],
                "status": s["status"], "replayed": bool(s["replayed"]), "rendered": bool(s["rendered"]),
                "excerpt": (c.get("quote") or "")[:300], "snapshot": (c.get("window") or "")[:900],
                "claim": {"id": c["id"], "label": f"{research.METHOD_LABELS[c['method']]} · {c['subject_name']}",
                          "value": c["text"], "state": c["state"], "quote": c["quote"], "quote_found": c["quote_found"],
                          "locality": c["locality"], "support": c["support"], "reason": c["reason"]}}
    out = one(c, s)
    cr = db.get_record(f"cr_{c['project_id']}_{c['research']['city_id']}_{c['research']['version']}")
    other = (cr or {}).get("contested", {}).get(c["id"])
    if other:
        oc = db.get_record(other)
        both = sorted([one(c, s), one(oc, research.source(oc["source_id"]))], key=lambda x: x["fetched_at"] or "", reverse=True)
        out["differ"] = both
    return out


# ---- slice 4: city research (CONTRACTS B6 · Slice 4) ----
class ResearchStart(BaseModel):
    city_ids: list[str] | None = None
    context: str | None = None
    refresh: bool = False


class Cities(BaseModel):
    city_ids: list[str]


class BaseVersion(BaseModel):
    base_version: int


class ContactIn(BaseModel):
    base_version: int
    contact: str


def _refused(fn):
    try:
        return fn()
    except research.Refused as e:
        raise HTTPException(e.code, delivery.scrub(e.detail))


@app.post("/api/projects/{pid}/research")
def start_research(pid: str, body: ResearchStart, request: Request):
    _project(pid, request)
    return _refused(lambda: research.start(pid, body.city_ids, body.context, body.refresh))


@app.post("/api/projects/{pid}/research/continue")
def continue_research(pid: str, body: Cities, request: Request):
    _project(pid, request)
    return _refused(lambda: research.resume(pid, body.city_ids, ("stopped", "queued"), "continue"))


@app.post("/api/projects/{pid}/research/retry")
def retry_research(pid: str, body: Cities, request: Request):
    _project(pid, request)
    return _refused(lambda: research.resume(pid, body.city_ids, ("failed",), "retry"))


# ---- slice 4.5: method specialists (CONTRACTS B6.5) ----
class Investigate(BaseModel):
    """Omitted fields keep your saved preferences; an explicit [] clears them (P45-I10)."""
    rationale: str | None = None
    include: list[str] | None = None
    exclude: list[str] | None = None


class WorkspaceEdit(BaseModel):
    base_version: int
    include: list[str] | None = None
    exclude: list[str] | None = None
    rationale: str | None = None


@app.patch("/api/projects/{pid}/workspaces/{wid}")
def patch_workspace(pid: str, wid: str, body: WorkspaceEdit, request: Request):
    _project(pid, request)
    return _refused(lambda: specialist.edit_workspace(pid, wid, body.base_version, body.include, body.exclude, body.rationale))


@app.post("/api/projects/{pid}/cities/{cid}/methods/{method}/investigate")
def investigate(pid: str, cid: str, method: str, request: Request, body: Investigate | None = None, dry_run: int = 0):
    """Starts one specialist run for (city, method); `?dry_run=1` returns its cost first and starts nothing."""
    _project(pid, request)
    return _refused(lambda: specialist.investigate(pid, cid, method, (body or Investigate()).model_dump(), bool(dry_run)))


@app.get("/api/projects/{pid}/cities/{cid}/methods/{method}")
def get_investigation(pid: str, cid: str, method: str, request: Request):
    _project(pid, request)
    if cid not in markets.CITIES or method not in research.METHODS:
        raise HTTPException(404, "No such city or method.")
    return specialist.investigation_view(pid, cid, method)


@app.post("/api/projects/{pid}/cities/{cid}/methods/{method}/continue")
def continue_investigation(pid: str, cid: str, method: str, request: Request):
    _project(pid, request)
    return _refused(lambda: specialist.resume(pid, cid, method))


class CreatorLead(BaseModel):
    city_id: str
    url: str
    note: str | None = None


@app.post("/api/projects/{pid}/leads")
def add_lead(pid: str, body: CreatorLead, request: Request):
    _project(pid, request)
    return _refused(lambda: specialist.add_creator(pid, body.city_id, body.url, body.note))


class LocationChoice(BaseModel):
    base_version: int
    option: int


@app.put("/api/projects/{pid}/leads/{lid}/location")
def choose_location(pid: str, lid: str, body: LocationChoice, request: Request):
    _project(pid, request)
    return _refused(lambda: specialist.choose_location(pid, lid, body.base_version, body.option))


@app.get("/api/projects/{pid}/dossiers/{did}")
def get_dossier(pid: str, did: str, request: Request):
    _project(pid, request)
    d = specialist.dossier_view(pid, did)
    if not d:
        raise HTTPException(404, "No such investigation.")
    return d


class DevelopIn(BaseModel):
    base_version: int | None = None
    rationale: str | None = None


class DraftEdit(BaseModel):
    base_version: int
    fields: dict


@app.post("/api/projects/{pid}/dossiers/{did}/develop")
def develop(pid: str, did: str, body: DevelopIn, request: Request):
    _project(pid, request)
    return _refused(lambda: specialist.develop(pid, did, body.base_version, body.rationale))


@app.get("/api/projects/{pid}/drafts/{rid}")
def get_draft(pid: str, rid: str, request: Request):
    _project(pid, request)
    d = specialist.draft_view(pid, rid)
    if not d:
        raise HTTPException(404, "No such draft.")
    return d


@app.patch("/api/projects/{pid}/drafts/{rid}")
def patch_draft(pid: str, rid: str, body: DraftEdit, request: Request):
    _project(pid, request)
    return _refused(lambda: specialist.edit_draft(pid, rid, body.base_version, body.fields))


# ---- slice 6: delivery packages, Action Center, approvals and adapters (CONTRACTS B8 "Slice 6 delivery") ----
class PackageEdit(BaseModel):
    base_version: int
    fields: dict


class AssetIn(BaseModel):
    base_version: int
    filename: str
    media_type: str
    data_b64: str
    role: Literal["CREATIVE", "ATTACHMENT"] = "CREATIVE"


class ApprovalIn(BaseModel):
    base_version: int


class ExecuteIn(BaseModel):
    approval_id: str


class ManualIn(BaseModel):
    date: str | None = None
    note: str | None = None


class ResolveIn(BaseModel):
    happened: bool
    provider_id: str | None = None
    note: str | None = None


class ConnectIn(BaseModel):
    package_id: str | None = None


class AccountIn(BaseModel):
    account_id: str
    page_id: str | None = None
    login_customer_id: str | None = None
    city_id: str | None = None


def _dst(dst: str) -> str:
    if dst not in delivery.DESTINATIONS:
        raise HTTPException(404, "No such destination.")
    return dst


def _pkg(pid: str, pkg_id: str, version: int | None = None) -> dict:
    pkg = db.get_record(pkg_id, version)
    if not pkg or pkg["project_id"] != pid or pkg["kind"] != "package":
        raise HTTPException(404, "No such package.")
    return pkg


class PrepareIn(BaseModel):
    base_version: int | None = None
    partner_lead_id: str | None = None  # 6.5 A3: the chosen recipient [P65-R2]


@app.post("/api/projects/{pid}/drafts/{rid}/package")
def prepare_package(pid: str, rid: str, request: Request, body: PrepareIn | None = None):
    _project(pid, request)
    b = body or PrepareIn()
    return _refused(lambda: delivery.prepare(pid, rid, b.base_version, b.partner_lead_id))


class SetAsideIn(BaseModel):
    base_version: int
    reason: str = "deselected"


@app.post("/api/projects/{pid}/packages/{pkg_id}/set-aside")
def set_aside(pid: str, pkg_id: str, body: SetAsideIn, request: Request):
    _project(pid, request)
    return _refused(lambda: delivery.set_aside(pid, pkg_id, body.base_version, body.reason))


class WordingIn(BaseModel):
    base_version: int
    field: str
    choice: Literal["mine", "new"]


@app.post("/api/projects/{pid}/packages/{pkg_id}/wording")
def choose_wording(pid: str, pkg_id: str, body: WordingIn, request: Request):
    _project(pid, request)
    return _refused(lambda: delivery.choose_wording(pid, pkg_id, body.base_version, body.field, body.choice))


class TemplateRef(BaseModel):
    id: str
    version: int


class ApplyTemplateIn(BaseModel):
    base_version: int
    template: TemplateRef


@app.post("/api/projects/{pid}/packages/{pkg_id}/apply-template")
def apply_template(pid: str, pkg_id: str, body: ApplyTemplateIn, request: Request):
    _project(pid, request)
    return _refused(lambda: delivery.apply_template(pid, pkg_id, body.base_version, body.template.model_dump()))


class TemplateIn(BaseModel):
    base_version: int | None = None
    method: str | None = None
    name: str | None = None
    subject: str | None = None
    body: str | None = None
    status: Literal["active", "retired"] | None = None


@app.get("/api/projects/{pid}/templates")
def list_templates(pid: str, request: Request):
    _project(pid, request)
    return delivery.templates(pid)


@app.post("/api/projects/{pid}/templates", status_code=201)
def new_template(pid: str, body: TemplateIn, request: Request):
    _project(pid, request)
    return _refused(lambda: delivery.save_template(pid, None, None, body.model_dump(exclude={"base_version"}, exclude_none=True)))


@app.put("/api/projects/{pid}/templates/{tid}")
def edit_template(pid: str, tid: str, body: TemplateIn, request: Request):
    _project(pid, request)
    return _refused(lambda: delivery.save_template(pid, tid, body.base_version,
                                                   body.model_dump(exclude={"base_version", "method"}, exclude_none=True)))


# ---- slice 6.5: the Plan for the beachhead city and the Act checklist (CONTRACTS B10) ----
class PlanMethodIn(BaseModel):
    method: str
    role: str
    note: str | None = None


class PlanIn(BaseModel):
    base_version: int | None = None
    methods: list[PlanMethodIn]


class SignOffIn(BaseModel):
    base_version: int
    expect: dict[str, dict[str, int] | int]


# ---- slice 5: costs, the proposal and the digital scenario (BUILD-PLAN "Slice 5" §5.9) ----
class MoneyIn(BaseModel):
    amount: str
    ccy: str


class CostLineEdit(BaseModel):
    base_version: int
    amount: MoneyIn | None = None
    flow: str | None = None
    used_by: list[str] | None = None
    use_estimate: bool | None = None  # accept Landfall's estimate as an ESTIMATE line


class ReturnEdit(BaseModel):
    base_version: int
    units: int | None = None
    retail_price: MoneyIn | None = None
    wholesale_price: MoneyIn | None = None
    sell_through: dict[str, float] | None = None


class ProposeIn(BaseModel):
    dry_run: bool = False


class AdoptIn(BaseModel):
    proposal_version: int
    option: Literal["A", "B"]
    base_version: int


class ScenarioIn(BaseModel):
    base_version: int
    scenario_version: int | None = None
    inputs: dict


def _city(cid: str) -> str:
    if cid not in markets.CITIES:
        raise HTTPException(404, "No such city.")
    return cid


@app.get("/api/projects/{pid}/plans/{cid}")
def get_plan(pid: str, cid: str, request: Request):
    _project(pid, request)
    return plans.view(pid, _city(cid))


@app.put("/api/projects/{pid}/plans/{cid}")
def put_plan(pid: str, cid: str, body: PlanIn, request: Request):
    _project(pid, request)
    return _refused(lambda: plans.save(pid, _city(cid), body.base_version, [m.model_dump() for m in body.methods]))


@app.post("/api/projects/{pid}/plans/{cid}/sign-off")
def sign_off(pid: str, cid: str, body: SignOffIn, request: Request):
    _project(pid, request)
    return _refused(lambda: plans.sign_off(pid, _city(cid), body.base_version, body.expect))


@app.patch("/api/projects/{pid}/plans/{cid}/cost-lines/{lid}")
def edit_cost_line(pid: str, cid: str, lid: str, body: CostLineEdit, request: Request):
    _project(pid, request)
    fields = body.model_dump(exclude_unset=True, exclude={"base_version"})
    if (sc := next((ln for ln in (db.get_record(plans.plan_id(pid, cid)) or {}).get("cost_lines", []) if ln["id"] == lid), None)) \
            and sc["by"] == "scenario" and ("amount" in fields or fields.get("use_estimate")):
        raise HTTPException(422, "This cost comes from your digital scenario: change the daily budget or the test length there.")
    return _refused(lambda: plans.edit_line(pid, _city(cid), body.base_version, lid, fields))


@app.patch("/api/projects/{pid}/plans/{cid}/return")
def edit_return(pid: str, cid: str, body: ReturnEdit, request: Request):
    _project(pid, request)
    return _refused(lambda: plans.edit_return(pid, _city(cid), body.base_version,
                                              body.model_dump(exclude_unset=True, exclude={"base_version"})))


class RetryIn(BaseModel):
    base_version: int


@app.post("/api/projects/{pid}/plans/{cid}/fx/retry")
def retry_fx(pid: str, cid: str, body: RetryIn, request: Request):
    """"Retry rates": one more ECB fetch (cost-free). A plan with no rates pinned takes them as Plan v+1 (unsigned); a
    newer publication for a plan that has rates is applied with its next edit, never by itself."""
    _project(pid, request)
    cur = db.get_record(plans.plan_id(pid, _city(cid)))
    if not cur:
        raise HTTPException(404, "No plan yet.")
    fx = plans.ensure_fx(pid, cur.get("cost_lines") or [], retry=True)
    if not fx:
        return {"version": cur["version"], "notice": "Rates are still unavailable; those lines stay not converted."}
    if cur.get("fx") or not plans.fx_pin(pid, cur.get("cost_lines") or []):
        return {"version": cur["version"], "notice": f"ECB rates of {fx['rate_date']} are stored; your next change uses them."}
    out = _refused(lambda: plans.write(pid, cid, body.base_version, lambda p: p, "Applied exchange rates", f"ECB {fx['rate_date']}"))
    return {"version": out["version"], "notice": f"Converted at ECB rates of {fx['rate_date']}."}


@app.post("/api/projects/{pid}/plans/{cid}/auto")
def auto_plan(pid: str, cid: str, request: Request):
    _project(pid, request)
    return {"run_id": _refused(lambda: autoplan.start(pid, _city(cid), asked=True))}


@app.post("/api/projects/{pid}/plans/{cid}/propose")
def propose(pid: str, cid: str, body: ProposeIn, request: Request):
    _project(pid, request)
    return _refused(lambda: strategist.start(pid, _city(cid), body.dry_run))


@app.get("/api/projects/{pid}/plans/{cid}/proposals/{prop}/options/{opt}/preview")
def preview_option(pid: str, cid: str, prop: str, opt: str, request: Request, version: int | None = None):
    _project(pid, request)
    if prop != strategist.proposal_id(pid, _city(cid)) or not db.get_record(plans.plan_id(pid, cid)):
        raise HTTPException(404, "No such proposal.")
    return _refused(lambda: strategist.preview(pid, cid, version, opt))


@app.post("/api/projects/{pid}/plans/{cid}/proposals/{prop}/adopt")
def adopt_option(pid: str, cid: str, prop: str, body: AdoptIn, request: Request):
    _project(pid, request)
    if prop != strategist.proposal_id(pid, _city(cid)):
        raise HTTPException(404, "No such proposal.")
    return _refused(lambda: strategist.adopt(pid, cid, body.proposal_version, body.option, body.base_version))


@app.get("/api/projects/{pid}/plans/{cid}/scenario")
def get_scenario(pid: str, cid: str, request: Request):
    _project(pid, request)
    return scenarios.view(pid, _city(cid), db.get_record(plans.plan_id(pid, cid)))


@app.put("/api/projects/{pid}/plans/{cid}/scenario")
def put_scenario(pid: str, cid: str, body: ScenarioIn, request: Request):
    _project(pid, request)
    return _refused(lambda: scenarios.save(pid, _city(cid), body.base_version, body.scenario_version, body.inputs))


@app.get("/api/projects/{pid}/plans/{cid}/act")
def get_act(pid: str, cid: str, request: Request):
    _project(pid, request)
    return delivery.scrub(plans.act(pid, _city(cid)))


# ---- Launch (owner, 28 Oct 2026): one checklist; packages prepared in the background ----
@app.get("/api/projects/{pid}/plans/{cid}/launch")
def get_launch(pid: str, cid: str, request: Request):
    _project(pid, request)
    return delivery.scrub(delivery.launch_view(pid, _city(cid), request.state.session))


@app.post("/api/projects/{pid}/plans/{cid}/launch/prewarm")
def prewarm_launch(pid: str, cid: str, request: Request):
    _project(pid, request)
    out = delivery.prewarm_launch(pid, _city(cid))
    if delivery.simulated(pid):
        _demo_connections(pid, _city(cid))
    return out


def _demo_connections(pid: str, cid: str) -> None:
    """A recorded demo's Launch sign-offs run SIMULATED (owner, 28 Oct 2026): Launch asks nothing about accounts, so the
    demo is connected to simulated Gmail and Google Ads (billing in the plan's currency, so nothing is converted)."""
    for dst in ("EMAIL", "GOOGLE_ADS"):
        if not delivery._own(pid, dst):
            delivery.connect_start(pid, dst, None)  # simulated(pid): a SIMULATED record, no provider called
    c = db.get_record(delivery.conn_id(pid, "GOOGLE_ADS"))
    ccy = ((delivery.launch_settings(pid, cid) or {}).get("budget") or {}).get("ccy")
    if c and c.get("simulated") and ccy and not c.get("selected"):
        db.put_record("connection", pid, {**delivery._payload(c), "selected": {
            "id": "0000000000", "name": "Demonstration account", "currency": ccy, "test_account": True, "login_customer_id": None}},
            rid=c["id"])


class LaunchSettingsIn(BaseModel):
    fields: dict


@app.put("/api/projects/{pid}/plans/{cid}/launch/settings")
def launch_settings(pid: str, cid: str, body: LaunchSettingsIn, request: Request):
    _project(pid, request)
    return _refused(lambda: delivery.save_launch_settings(pid, _city(cid), body.fields))


class LaunchPickIn(BaseModel):
    method: str
    lead_id: str | None = None
    search_id: str | None = None
    index: int | None = None


@app.post("/api/projects/{pid}/plans/{cid}/launch/pick")
def launch_pick(pid: str, cid: str, body: LaunchPickIn, request: Request):
    _project(pid, request)
    try:
        return _refused(lambda: delivery.launch_pick(pid, _city(cid), request.state.session, body.method, body.lead_id,
                                                     body.search_id, body.index))
    except influencers.Refused as e:
        raise HTTPException(e.code, e.detail)


@app.post("/api/projects/{pid}/plans/{cid}/rights")
def confirm_rights(pid: str, cid: str, request: Request):
    _project(pid, request)
    return _refused(lambda: plans.confirm_rights(pid, _city(cid)))


# ---- 6.5 increment D: launch-kit mockups (UI.13) ----
class MockupsIn(BaseModel):
    methods: list[str] | None = None
    dry_run: bool = False


@app.get("/api/projects/{pid}/plans/{cid}/mockups")
def get_mockups(pid: str, cid: str, request: Request):
    _project(pid, request)
    return mockups.view(pid, _city(cid))


@app.post("/api/projects/{pid}/plans/{cid}/mockups")
def start_mockups(pid: str, cid: str, body: MockupsIn, request: Request):
    _project(pid, request)
    try:
        return mockups.start(pid, _city(cid), body.methods, body.dry_run)
    except mockups.Refused as e:
        raise HTTPException(e.code, e.detail)


@app.get("/api/projects/{pid}/mockups/{rid}/image")
def mockup_image(pid: str, rid: str, request: Request, version: int | None = None):
    """The saved scene's bytes: reading it never generates anything."""
    _project(pid, request)
    rec = db.get_record(rid, version)
    data = rec and rec["project_id"] == pid and rec["kind"] == "concept_preview" and rec.get("image") and \
        delivery.artifact_bytes(rec["image"]["artifact"])
    if not data:
        raise HTTPException(404, "No saved image for this mockup.")
    return Response(data, media_type=rec["image"]["media_type"], headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/projects/{pid}/artifacts/{sha}")
def artifact_image(pid: str, sha: str, request: Request):
    """A project's saved image by digest (the real product photos a mockup lays over its scene)."""
    _project(pid, request)
    art = db.q("SELECT payload FROM records WHERE kind='artifact' AND project_id=? AND json_extract(payload, '$.sha256')=? LIMIT 1", pid, sha)
    data = art and delivery.artifact_bytes({"sha256": sha})
    if not data:
        raise HTTPException(404, "No such image.")
    return Response(data, media_type=json.loads(art[0]["payload"])["media_type"], headers={"Cache-Control": "private, max-age=86400"})


# ---- slice 6.6: creator discovery with Influencers.club (CONTRACTS B11) ----
class CreatorSearchIn(BaseModel):
    platform: Literal["instagram", "tiktok", "youtube"] = "instagram"
    follower_min: int | None = Field(None, ge=0, le=100_000_000)
    more_of: str | None = None
    dry_run: bool = False


def _ic(fn):
    try:
        return fn()
    except influencers.Refused as e:
        raise HTTPException(e.code, e.detail)


@app.get("/api/projects/{pid}/plans/{cid}/creators")
def get_creators(pid: str, cid: str, request: Request):
    _project(pid, request)
    return influencers.view(pid, _city(cid), request.state.session)


@app.post("/api/projects/{pid}/plans/{cid}/creators/search")
def search_creators(pid: str, cid: str, body: CreatorSearchIn, request: Request):
    _project(pid, request)
    return _ic(lambda: influencers.search(pid, _city(cid), request.state.session, body.platform, body.follower_min,
                                          body.more_of, body.dry_run))


@app.post("/api/projects/{pid}/creators/searches/{sid}/accounts/{i}/choose")
def choose_creator(pid: str, sid: str, i: int, request: Request):
    _project(pid, request)
    return _ic(lambda: influencers.choose(pid, sid, i))


@app.post("/api/projects/{pid}/leads/{lid}/enrich")
def enrich_creator(pid: str, lid: str, request: Request):
    _project(pid, request)
    return _ic(lambda: influencers.enrich(pid, lid, request.state.session))


@app.get("/api/projects/{pid}/packages/{pkg_id}")
def get_package(pid: str, pkg_id: str, request: Request):
    _project(pid, request)
    v = delivery.package_view(pid, pkg_id)
    if not v:
        raise HTTPException(404, "No such package.")
    return delivery.scrub(v)


@app.patch("/api/projects/{pid}/packages/{pkg_id}")
def patch_package(pid: str, pkg_id: str, body: PackageEdit, request: Request):
    _project(pid, request)
    return _refused(lambda: delivery.edit_package(pid, pkg_id, body.base_version, body.fields))


@app.post("/api/projects/{pid}/packages/{pkg_id}/assets")
def add_asset(pid: str, pkg_id: str, body: AssetIn, request: Request):
    _project(pid, request)
    return _refused(lambda: delivery.upload_asset(pid, pkg_id, body.base_version, body.filename, body.media_type,
                                                  body.data_b64, body.role))


@app.get("/api/projects/{pid}/packages/{pkg_id}/exports/{fmt}")
def export(pid: str, pkg_id: str, fmt: str, request: Request, version: int | None = None, artifact: str | None = None,
           preview: int = 0):
    """The bytes of exactly one package version. A download records an Export, never a SENT record; `preview=1`
    (an image shown on the page) records nothing."""
    _project(pid, request)
    pkg = _pkg(pid, pkg_id, version)
    data, media, name = _refused(lambda: delivery.export_bytes(pid, pkg, fmt, artifact))
    if not preview:
        delivery.record_export(pid, pkg, fmt, data)
    disp = "inline" if preview else "attachment"
    return Response(data, media_type=media, headers={"Content-Disposition": f'{disp}; filename="{name}"',
                                                     "X-Content-SHA256": hashlib.sha256(data).hexdigest()})


@app.post("/api/projects/{pid}/packages/{pkg_id}/destinations/{dst}/approve")
def approve_package(pid: str, pkg_id: str, dst: str, body: ApprovalIn, request: Request):
    _project(pid, request)
    return _refused(lambda: delivery.approve(pid, pkg_id, _dst(dst), body.base_version))


@app.post("/api/projects/{pid}/packages/{pkg_id}/destinations/{dst}/validate")
def validate_package(pid: str, pkg_id: str, dst: str, request: Request):
    _project(pid, request)
    return _refused(lambda: delivery.provider_validate(pid, pkg_id, _dst(dst)))


@app.post("/api/projects/{pid}/packages/{pkg_id}/destinations/{dst}/execute")
def execute_package(pid: str, pkg_id: str, dst: str, body: ExecuteIn, request: Request):
    _project(pid, request)
    a = _refused(lambda: delivery.execute(pid, pkg_id, _dst(dst), body.approval_id))
    return delivery.scrub({"id": a["id"], "status": a["status"], "mode": a["mode"], "error": a.get("error")})


@app.post("/api/projects/{pid}/packages/{pkg_id}/destinations/{dst}/manual")
def manual(pid: str, pkg_id: str, dst: str, body: ManualIn, request: Request):
    _project(pid, request)
    return _refused(lambda: delivery.manual_record(pid, pkg_id, _dst(dst), body.date, body.note))


class WithdrawIn(BaseModel):
    record_id: str


@app.post("/api/projects/{pid}/packages/{pkg_id}/destinations/{dst}/manual/withdraw")
def withdraw_manual(pid: str, pkg_id: str, dst: str, body: WithdrawIn, request: Request):
    _project(pid, request)
    return _refused(lambda: delivery.withdraw_manual(pid, pkg_id, _dst(dst), body.record_id))


@app.post("/api/projects/{pid}/executions/{aid}/resolve")
def resolve_execution(pid: str, aid: str, body: ResolveIn, request: Request):
    _project(pid, request)
    return _refused(lambda: delivery.resolve(pid, aid, body.happened, body.provider_id, body.note))


@app.post("/api/projects/{pid}/connections/{dst}/start")
def connect_start(pid: str, dst: str, body: ConnectIn, request: Request):
    _project(pid, request)
    return _refused(lambda: delivery.connect_start(pid, _dst(dst), body.package_id))


@app.get("/api/connections/callback")
def connect_callback(request: Request):
    """The one OAuth redirect for every provider; the single-use state names the project, checked against this browser."""
    params = dict(request.query_params)
    row = db.q("SELECT project_id FROM oauth_states WHERE state=?", params.get("state") or "")
    if row and row[0]["project_id"] != delivery.SHARED:  # a shared state is the owner's, from scripts/owner_connect.py
        _project(row[0]["project_id"], request)
    return RedirectResponse(_refused(lambda: delivery.connect_callback(params)), status_code=303)


@app.put("/api/projects/{pid}/connections/{dst}/account")
def choose_account(pid: str, dst: str, body: AccountIn, request: Request):
    _project(pid, request)
    return _refused(lambda: delivery.select_account(pid, _dst(dst), body.account_id, body.model_dump()))


@app.delete("/api/projects/{pid}/connections/{dst}")
def disconnect(pid: str, dst: str, request: Request):
    _project(pid, request)
    return _refused(lambda: delivery.disconnect(pid, _dst(dst)))


@app.get("/api/projects/{pid}/opportunities")
def get_opportunities(pid: str, request: Request):
    _project(pid, request)
    return {**research.opportunities_view(pid), "specialists": specialist.summaries(pid)}


@app.post("/api/projects/{pid}/approaches/{aid}/rule-out")
def rule_out(pid: str, aid: str, body: BaseVersion, request: Request):
    _project(pid, request)
    return _refused(lambda: research.decide(pid, aid, body.base_version, "ruled_out"))


@app.post("/api/projects/{pid}/approaches/{aid}/undo")
def undo_rule_out(pid: str, aid: str, body: BaseVersion, request: Request):
    _project(pid, request)
    return _refused(lambda: research.decide(pid, aid, body.base_version, "undone"))


def _lead(pid: str, lid: str) -> dict:
    lead = db.get_record(lid)
    if not lead or lead["project_id"] != pid or lead["kind"] != "lead":
        raise HTTPException(404, "No such lead.")
    return lead


@app.get("/api/projects/{pid}/leads/{lid}")
def get_lead(pid: str, lid: str, request: Request):
    _project(pid, request)
    return research.lead_view(pid, _lead(pid, lid))


@app.post("/api/projects/{pid}/leads/{lid}/contact")
def lead_contact(pid: str, lid: str, body: ContactIn, request: Request):
    _project(pid, request)
    _lead(pid, lid)
    return _refused(lambda: research.add_contact(pid, lid, body.base_version, body.contact))


@app.post("/api/projects/{pid}/leads/{lid}/check")
def lead_check(pid: str, lid: str, request: Request):
    _project(pid, request)
    _lead(pid, lid)
    return _refused(lambda: research.start_lead_check(pid, lid))


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
                                                  "leave_out_presence", "home_country")}
        | {"version": fi["version"], "ceiling_by": ceiling_by(fi)},
        "beachhead": scan.beachhead_view(pid),
        "plan": plans.stage(pid),
        "goal": _goal_view(pid, ident, prof),
        "run": run and {"id": run["id"], "kind": run["kind"], "status": run["status"], "message": detail.get("message", "")},
        "reading": [ident.pop("row"), *({"key": k, **v} for k, v in reading.items())],
        "brief": brief and _brief_view(brief),
        "identity": ident, "profile": prof, "markets_reason": _markets_reason(ident, prof),
        "markets": _markets_stage(pid, ident, prof),
        "opportunities": _opps_stage(pid),
        "activity": activity,
        "usage": {"model_calls": usage["calls"], "est_usd": round(usage["usd"], 4), "input_tokens": usage["tin"],
                  "output_tokens": usage["tout"], "pages_fetched": pages["n"], "pages_read": pages["ok"] or 0,
                  "qloo_requests": qloo_n["n"], "qloo_lookups": qloo_n["n"], "qloo_live": qloo_n["live"],
                  "qloo_sent": qloo_n["live"], "qloo_cached": qloo_n["cached"],
                  "replayed": p["mode"] == "replay"},
        "last_seq": (db.q("SELECT MAX(seq) s FROM events WHERE project_id=?", pid)[0]["s"] or 0),
    }


def ceiling_by(fi: dict) -> str | None:
    """Older FounderInputs have no ceiling_by: a set ceiling was the founder's, an unset one means no ceiling."""
    return fi.get("ceiling_by") or ("YOU_SAID" if fi.get("ceiling_usd") else None)


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


DEMO_ENDS = ("Tidewell's recording covers the Brand stage. Markets, Plan and Launch run as live research with your own Qloo "
             "and AWS keys (see the README).")


def _markets_reason(ident: dict, prof: dict | None) -> str:
    if ident["status"] == "failed" and db.q("SELECT 1 FROM projects WHERE id=? AND mode='replay'", ident["project_id"]):
        return DEMO_ENDS  # A1/A8: the public demo replays the brand read only; never "retry" a recording that can't answer
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


def _opps_stage(pid: str) -> dict:
    rows = db.q("SELECT status FROM research_cities r WHERE project_id=? AND version=(SELECT MAX(version) FROM "
                "research_cities WHERE project_id=r.project_id AND city_id=r.city_id)", pid)
    if not rows:
        return {"stage": "none"}
    running = any(r["status"] in ("queued", "running") for r in rows) and research._active(pid)
    return {"stage": "researching" if running else "researched", "cities": len(rows)}


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
            "read_at": b.get("read_at"), "products": products.view(b["project_id"]),
            "read": brand.read_summary(b)}  # v4 Brand: pages read and failed; B02 recovery when most failed


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
