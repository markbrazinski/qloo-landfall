"""Pick a city → a plan (owner, 27 Oct 2026: "When I pick a city, we should get a plan … it should just happen like a
real product"; costs are not shown or asked about, spending stops only at the ledger caps).

One durable run per city: research the city, investigate every way in, ask the strategist, adopt its first option,
then develop the drafts of the methods it includes. Each phase is a run step, so a restart resumes where it stopped.
Sign-off stays the founder's: it is what the Act checklist follows. A plan the founder has already shaped is never
overwritten: the strategist runs only on an empty plan. Since 28 Oct 2026 the shortlist's top three cities build this
way before any is chosen (`prewarm`)."""
import threading
import traceback
from datetime import datetime, timezone

from . import config, db, delivery, plans, research, runner, specialist, strategist
from .research import Refused
from .runner import HANDLERS, Run

ACTIVE = ("queued", "running")


def start(pid: str, cid: str, asked: bool = False) -> str | None:
    """On choosing the beachhead (unless switched off), or when the founder asks ("Build my plan")."""
    if not (config.AUTO_PLAN or asked):
        return None
    if cid != plans.beachhead(pid):
        raise Refused(409, "Choose this city as your beachhead in Markets first.")
    return runner.start(pid, "auto_plan", cid, {"city_id": cid})


def status(pid: str, cid: str) -> dict | None:
    """The latest auto-plan run for this city as four steps, each read from the records it produces (so it is right
    after a restart too), with the current step's live detail for the Plan page."""
    import json
    r = db.q("SELECT id, status, detail, started_at, ended_at FROM runs WHERE project_id=? AND kind='auto_plan' AND input_key=? "
             "ORDER BY started_at DESC, rowid DESC LIMIT 1", pid, cid)
    if not r:
        return None
    r = r[0]
    ev = [e for e in db.events(pid, kind="auto_plan") if e.get("run_id") == r["id"]]
    phase = ev[-1]["phase"] if ev else "research"
    order = ["research", "investigate", "propose", "develop", "ready"]
    rows = {m: specialist.spec_row(pid, cid, m) for m in research.METHODS}
    done = [m for m, row in rows.items() if row and row["status"] not in ACTIVE]
    # the step advances as soon as its work is saved, not when the run gets round to announcing it (owner, 28 Oct 2026)
    at = max(order.index(phase) if phase in order else 0, _saved_step(pid, cid, done))
    last = next((e for e in reversed(db.events(pid, kind="activity")) if e.get("stage") == "Opportunities"), None)
    # what the researchers are doing right now, streamed as it happens (pages read, searches, checks): the Plan page
    # shows the latest lines so a 2–4 minute build visibly moves every few seconds (owner, 28 Oct 2026)
    feed = [{"at": e["at"], "text": e["text"]} for e in db.events(pid, kind="research")
            if e.get("city_id") == cid and e.get("text") and research.shown(e)][-5:][::-1]
    steps = [
        {"key": "research", "label": f"Research {research.markets.CITIES[cid]['name']}",
         "detail": (last or {}).get("task") if at == 0 else None},
        {"key": "investigate", "label": "Investigate five ways in",
         "detail": f"{len(done)} of {len(research.METHODS)} done · " + ", ".join(
             research.METHOD_LABELS[m].lower() + (" ✓" if m in done else "…") for m in research.METHODS) if at == 1 else None},
        {"key": "propose", "label": "Choose the main test and cost the plan", "detail": None},
        {"key": "develop", "label": "Draft the methods in the plan", "detail": None}]
    stopped = r["status"] not in ACTIVE
    for i, s in enumerate(steps):
        s["state"] = "done" if i < at or phase == "ready" else ("running" if not stopped else "stopped") if i == at else "waiting"
    return {"status": r["status"], "phase": phase, "text": ev[-1]["text"] if ev else "Starting", "steps": steps,
            "run_id": r["id"], "started_at": r["started_at"], "ended_at": r["ended_at"],
            "message": json.loads(r["detail"] or "{}").get("message"), "feed": feed}


def _until(run: Run, done) -> None:
    # ponytail: polls; a completion hook in runner would free the waiting thread if many plans build at once
    while not done():
        run.check()
        threading.Event().wait(0.2 if config.PROVIDER_MODE == "replay" else 2)  # not time.sleep, which tests stub


def _saved_step(pid: str, cid: str, done: list[str]) -> int:
    """How far the city's plan is, read from what is saved: 0 research, 1 investigate, 2 propose, 3 develop, 4 ready."""
    if cid not in research.latest_research(pid):
        return 0
    if len(done) < len(research.METHODS):
        return 1
    plan = db.get_record(plans.plan_id(pid, cid))
    inc = [m["method"] for m in (plan or {}).get("methods", []) if m["role"] in plans.INCLUDED]
    if not inc:
        return 2
    for m in inc:
        d = db.get_record(f"dos_{pid}_{cid}_{m}")
        dr = d and db.get_record(f"drf_{d['id'][4:]}")
        if not dr or dr["source"]["dossier_version"] != d["version"]:
            return 3
    return 4


def _wait(run: Run, rids: list[str]) -> None:
    _until(run, lambda: not [r for r in rids if r and db.q("SELECT 1 FROM runs WHERE id=? AND status IN ('queued','running')", r)])


def _research_ended(pid: str, cid: str) -> bool:
    """The city's research row is written a moment before its run is, so the row is what's watched; a row left open with
    no research run for 30 s (a crash) counts as ended."""
    row = research.city_row(pid, cid)
    if not row or row["status"] not in ACTIVE:
        return True
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["created_at"])).total_seconds()
    return age > 30 and not research._active(pid)


def _turn(run: Run, pid: str, cid: str) -> None:
    """Prepared builds investigate and draft one city at a time, in recommendation order (live, 28 Oct 2026: fifteen
    investigations at once drew Bedrock throttling, and every throttled retry counts against an investigation's call
    cap, so most failed). The city the founder has chosen never waits."""
    order = run.params.get("together") or []
    before = order[:order.index(cid)] if run.params.get("prepared") and cid in order else []
    _until(run, lambda: cid == plans.beachhead(pid) or not any(
        db.q("SELECT 1 FROM runs WHERE project_id=? AND kind='auto_plan' AND input_key=? AND status IN ('queued','running')",
             pid, c) for c in before))


def _running(pid: str, kind: str, key: str = "") -> list[str]:
    return [r["id"] for r in db.q("SELECT id FROM runs WHERE project_id=? AND kind=? AND input_key LIKE ? AND "
                                  "status IN ('queued','running')", pid, kind, f"{key}%")]


def auto_plan_run(run: Run):
    pid, cid = run.project_id, run.params["city_id"]
    city = research.markets.CITIES[cid]["name"]

    def phase(name: str, text: str):
        run.emit("auto_plan", {"city_id": cid, "phase": name, "text": text})

    # 0 · prepared builds run one city at a time, #1 first, then #2, then #3 (owner, 28 Oct 2026: serialize to avoid
    # Bedrock throttling); the city the founder picks never waits
    _turn(run, pid, cid)

    # 1 · research the city (or wait for research already running). A prewarmed plan first waits for the shortlist
    # that named it to be stored: research is only for shortlisted cities.
    if cid not in research.latest_research(pid):
        phase("research", f"Researching {city}")
        _wait(run, _running(pid, "shortlist"))
        together = run.params.get("together") or [cid]


        def go():
            for _ in range(6):  # research for another city may be running: wait for it, then start this one
                try:
                    return research.start(pid, [cid], None, False)["run_id"]
                except Refused:
                    if research.city_row(pid, cid):
                        return None  # another build already started it (or it ended earlier: see below)
                    _wait(run, _running(pid, "research"))
            return None
        rid = run.step("research", go)
        _until(run, lambda: _research_ended(pid, cid))
        _wait(run, [rid])
        if cid not in research.latest_research(pid):
            return "partial", f"Research of {city} didn't finish. Open Activity for why; nothing else was started."

    # 2 · investigate every way in that isn't done (a method your note rules out is skipped)
    todo = [m for m in research.METHODS if (db.get_record(f"dos_{pid}_{cid}_{m}") or {}).get("status") not in ("complete", "partial")]
    if todo:
        phase("investigate", f"Investigating {len(todo)} ways in: " + ", ".join(research.METHOD_LABELS[m].lower() for m in todo))

        def go():
            out = []
            for m in todo:
                row = specialist.spec_row(pid, cid, m)
                if row and row["status"] in ACTIVE:
                    continue
                try:
                    out.append(specialist.investigate(pid, cid, m, {})["run_id"])
                except Refused:
                    pass
            return out
        _wait(run, [*run.step("investigate", go), *_running(pid, "specialist", f"{cid}:")])
    if not strategist.evidence(pid, cid):
        return "partial", "No way in could be investigated, so there is nothing to plan from yet."

    # 3 · the strategist's proposal, adopted, on an empty plan only
    cur = db.get_record(plans.plan_id(pid, cid))
    if not cur or not any(m["role"] in plans.INCLUDED for m in cur["methods"]):
        phase("propose", "Choosing the main test and costing the plan")
        if not cur:  # a city that isn't the beachhead (yet) gets a plan prepared in advance, never chosen for the founder
            run.step("plan", lambda: plans.save(pid, cid, None, []) if cid == plans.beachhead(pid) else plans.prepare(pid, cid))
        rid = run.step("propose", lambda: strategist.start(pid, cid)["run_id"])
        _wait(run, [rid])

        def adopt():
            prop = db.get_record(strategist.proposal_id(pid, cid))
            now = db.get_record(plans.plan_id(pid, cid))
            if not prop or not prop.get("options"):
                return None
            return strategist.adopt(pid, cid, prop["version"], prop["options"][0]["id"], now["version"])["version"]
        if run.step("adopt", adopt) is None:
            return "partial", "Landfall couldn't propose a plan this time. Choose a main test yourself, or try again."

    # 4 · drafts for the included methods, so the plan can be signed
    plan = db.get_record(plans.plan_id(pid, cid))
    need = []
    for m in plan["methods"]:
        d = db.get_record(f"dos_{pid}_{cid}_{m['method']}")
        dr = d and db.get_record(f"drf_{d['id'][4:]}")
        if m["role"] in plans.INCLUDED and d and (not dr or dr["source"]["dossier_version"] != d["version"]):
            need.append(d)
    if need:
        phase("develop", "Drafting " + ", ".join(research.METHOD_LABELS[d["method"]].lower() for d in need))

        def go():
            out = []
            for d in need:
                try:
                    out.append(specialist.develop(pid, d["id"], None, None)["run_id"])
                except Refused:
                    pass
            return out
        _wait(run, run.step("develop", go))
    # 5 · the Google Search ads, written now so the Launch page has them (owner, 28 Oct 2026: "each time we will write
    # ads … front load that"); kept beside the digital draft, adopted by the Launch package after sign-off. Never fails the plan.
    plan = db.get_record(plans.plan_id(pid, cid))
    if plans.role_of(plan, "digital") in plans.INCLUDED:
        phase("develop", "Writing the Google ads")
        try:
            run.step("ads", lambda: delivery.draft_ads(run, pid, cid))
        except runner.Cancelled:
            raise
        except Exception:
            traceback.print_exc()
    phase("ready", "Your plan is ready to review")
    _after_ready(run, pid, cid)


def _after_ready(run: Run, pid: str, cid: str) -> None:
    """What a ready plan starts (owner, 28 Oct 2026): the launch kit (delivery.prewarm_launch, when built) and, for the
    shortlist's top city, the mockup scenes, so neither waits for sign-off. Neither can fail the plan."""
    from . import delivery, mockups
    launch = getattr(delivery, "prewarm_launch", None)
    for name, fn in (("launch", launch), ("scenes", run.params.get("top") and mockups.prewarm)):
        if not fn:
            continue
        try:
            run.step(name, lambda: fn(pid, cid) and None)
        except runner.Cancelled:
            raise
        except Exception:
            traceback.print_exc()


def prewarm(pid: str, recommended: list[str]) -> list[str] | None:
    """Owner, 28 Oct 2026: "Once we ID the top 3 cities, we should in parallel start building the plans for them." As
    soon as the shortlist decides, the full plan of each of its top three cities builds at once: research (one run for
    all three), every way in, the strategist's proposal adopted, the drafts. Each is the city's own auto_plan run, so
    choosing one of them as the beachhead joins its run (runner.start reuses a running run) or finds its plan ready;
    nothing here sets the beachhead (plans.prepare)."""
    top = [c for c in recommended[:3] if c in research.markets.CITIES]
    if not (config.AUTO_PLAN and top):
        return None
    if db.q("SELECT 1 FROM projects WHERE id=? AND mode='replay'", pid):
        return None  # a recorded demo replays only what was recorded
    # owner, 28 Oct 2026: only city #1 is built ahead in full (~US$2.50 a run instead of ~US$6); #2 and #3 build when picked
    c = top[0]
    return [runner.start(pid, "auto_plan", c, {"city_id": c, "together": [c], "top": True, "prepared": True})]


HANDLERS["auto_plan"] = auto_plan_run
