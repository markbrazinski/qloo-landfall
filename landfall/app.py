"""HTTP API (CONTRACTS §9) and the static frontend."""
import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import brand, config, db, fetch, runner  # brand registers its run handlers

WEB = config.ROOT / "web" / "dist"
DEFAULT_DEMO = config.ROOT / "fixtures" / "synthetic" / "tidewell-brand-v4"
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
    parts = urlsplit(raw)
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https") or "." not in host or " " in raw:
        raise HTTPException(422, "That doesn't look like a website address. Try something like yourbrand.com.")
    try:
        return fetch.check_url(raw)
    except fetch.FetchBlocked as e:
        raise HTTPException(422, f"That address can't be read: {e}.")


class NewProject(BaseModel):
    url: str | None = None
    demo: bool = False
    objective: str | None = None
    ceiling_usd: int | None = None
    include: str | None = None
    exclude: str | None = None


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
        "include": body.include or None, "exclude": body.exclude or None}, rid=f"input_{pid}")
    run_id = runner.start(pid, "brand_read", "attempt-1", {"url": url})
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
    # Interpretations inferred from the corrected fact are flagged for review; nothing else uses the brief yet.
    users = [db.get_record(i)["label"] for i in v2["interpretation"]
             if body.claim_id in (db.get_record(i).get("basis_claim_ids") or [])]
    if users:
        names = " and ".join(users)
        return {"version": v2["version"], "notice": f"Brief v{v2['version']}. Our reading of {names} used this fact; "
                f"review {'it' if len(users) == 1 else 'them'}. Nothing else has used it yet."}
    return {"version": v2["version"], "notice": f"Brief v{v2['version']}. Nothing has used it yet."}


@app.post("/api/projects/{pid}/brand-read/retry")
def retry(pid: str, request: Request):
    p = _project(pid, request)
    n = db.q("SELECT COUNT(*) n FROM runs WHERE project_id=? AND kind='brand_read'", pid)[0]["n"]
    return {"run_id": runner.start(pid, "brand_read", f"attempt-{n + 1}", {"url": p["url"]})}


class Description(BaseModel):
    text: str


@app.post("/api/projects/{pid}/brand-read/description")
def describe(pid: str, body: Description, request: Request):
    p = _project(pid, request)
    text = body.text.strip()
    if len(text) < 8:
        raise HTTPException(422, "Tell us in a sentence what you sell.")
    return {"run_id": runner.start(pid, "brand_describe", text[:200], {"url": p["url"], "description": text[:500]})}


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
                 "COALESCE(SUM(output_tokens),0) tout FROM usage WHERE project_id=?", pid)[0]
    pages = db.q("SELECT COUNT(*) n, SUM(status=200) ok FROM sources WHERE project_id=?", pid)[0]
    activity = [e for e in db.events(pid) if e["kind"] in ("activity", "founder")]
    detail = json.loads(run["detail"]) if run else {}
    return {
        "id": pid, "url": p["url"], "site": fetch.site_of(p["url"]), "mode": p["mode"],
        "demo": p["mode"] == "replay", "created_at": p["created_at"],
        "intent": fi and {k: fi[k] for k in ("objective", "ceiling_usd", "include", "exclude")},
        "run": run and {"id": run["id"], "kind": run["kind"], "status": run["status"], "message": detail.get("message", "")},
        "reading": [{"key": k, **v} for k, v in reading.items()],
        "brief": brief and _brief_view(brief),
        "activity": activity,
        "usage": {"model_calls": usage["calls"], "est_usd": round(usage["usd"], 4), "input_tokens": usage["tin"],
                  "output_tokens": usage["tout"], "pages_fetched": pages["n"], "pages_read": pages["ok"] or 0,
                  "replayed": p["mode"] == "replay"},
        "last_seq": (db.q("SELECT MAX(seq) s FROM events WHERE project_id=?", pid)[0]["s"] or 0),
    }


def _source_ref(sid: str) -> dict | None:
    s = db.q("SELECT id, url, fetched_at FROM sources WHERE id=?", sid)
    return s and {"id": s[0]["id"], "url": s[0]["url"], "fetched_at": s[0]["fetched_at"]}


def _claim_view(cid: str, current: set[str] | None = None) -> dict:
    c = db.get_record(cid)
    src = _source_ref(c["source_ids"][0]) if c.get("source_ids") and c.get("state") != "INFERRED" else None
    view = {"id": c["id"], "label": c["label"], "value": c["value"], "state": c["state"], "vc": c.get("vc"),
            "basis": c.get("basis"), "reason": c.get("reason"), "quote": c.get("quote"), "source": src,
            "quote_found": c.get("quote_found"), "support": c.get("support"), "checker": c.get("checker"),
            "corrected": bool(c.get("replaces"))}
    if c.get("state") == "INFERRED" and "basis_claim_ids" in c:
        facts = [db.get_record(f) for f in c["basis_claim_ids"]]
        view["inferred_from"] = {
            "reasoning": c.get("reasoning", ""), "established": c.get("basis_established", False),
            "facts": [{"id": f["id"], "label": f["label"], "value": f["value"], "state": f["state"],
                       "source": _source_ref(f["source_ids"][0]) if f.get("source_ids") else None,
                       "quote": f.get("quote"), "reason": f.get("reason"), "quote_found": f.get("quote_found"),
                       "support": f.get("support")} for f in facts],
            "pages": [_source_ref(s) for s in c.get("source_ids", [])],
            # a cited fact the founder has since corrected: the reading must be reviewed, not trusted
            "changed": [f["label"] for f in facts if current is not None and f["id"] not in current],
            "dropped": c.get("basis_dropped", [])}
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
