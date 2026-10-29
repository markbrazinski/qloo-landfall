"""Durable runs and tasks (BUILD-PLAN §3): idempotent steps, restart resume, cancellation, budget-aware status."""
import functools
import json
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import config, db
from .cassette import Cassette, NoRecording
from .ledger import BudgetExhausted

# ponytail: a plan build waits in its thread. Since 28 Oct 2026 three build at once (autoplan.prewarm): 3 waiting builds
# + the shortlist + research + 15 investigations fit in 24; an event-driven wait would free those threads if more do.
EXEC = ThreadPoolExecutor(max_workers=24)
HANDLERS: dict = {}
_cancels: dict[str, threading.Event] = {}


class Cancelled(Exception):
    pass


class NeedsInput(Exception):
    """Stop the run and wait for a founder decision (e.g. the site could not be read)."""


@dataclass
class Run:
    id: str
    project_id: str
    kind: str
    params: dict
    cassette: Cassette
    cancel: threading.Event = field(default_factory=threading.Event)

    def check(self):
        if self.cancel.is_set():
            raise Cancelled()

    def step(self, key: str, fn, retries: int = 0):
        """Run `fn` once per (run, key). A completed step returns its stored result, so restarts never repeat work."""
        rows = db.q("SELECT status, result, attempts FROM tasks WHERE run_id=? AND task_key=?", self.id, key)
        if rows and rows[0]["status"] == "done":
            return json.loads(rows[0]["result"])
        attempts = rows[0]["attempts"] if rows else 0
        while True:
            self.check()
            attempts += 1
            db.x("INSERT INTO tasks(run_id, task_key, status, attempts) VALUES(?,?,?,?) "
                 "ON CONFLICT(run_id, task_key) DO UPDATE SET status=excluded.status, attempts=excluded.attempts",
                 self.id, key, "running", attempts)
            try:
                result = fn()
            except (Cancelled, BudgetExhausted, NoRecording, NeedsInput):
                db.x("UPDATE tasks SET status='failed' WHERE run_id=? AND task_key=?", self.id, key)
                raise
            except Exception as e:
                if attempts <= retries:
                    continue
                db.x("UPDATE tasks SET status='failed', detail=? WHERE run_id=? AND task_key=?", str(e)[:300], self.id, key)
                raise
            db.x("UPDATE tasks SET status='done', result=? WHERE run_id=? AND task_key=?", json.dumps(result), self.id, key)
            return result

    def emit(self, kind: str, payload: dict):
        db.event(self.project_id, kind, payload, self.id)


def cassette_for(project_id: str) -> Cassette:
    p = db.q("SELECT mode, demo_case FROM projects WHERE id=?", project_id)[0]
    if p["mode"] == "replay":
        return Cassette("replay", replay_dirs=[Path(d) for d in p["demo_case"].split(",")])
    if config.REPLAY_CASES:  # accepted upstream cases replay; only LIVE_STAGES may go live, and those are recorded
        return Cassette("replay", config.PRIVATE / "runs" / project_id if config.LIVE_STAGES else None,
                        replay_dirs=config.REPLAY_CASES, live_stages=config.LIVE_STAGES)
    mode = config.PROVIDER_MODE
    return Cassette(mode, config.PRIVATE / "runs" / project_id if mode == "record" else None)


class NotAllowed(Exception):
    """A live run for a project whose judge or owner link was revoked (or never held one)."""


class NotInDemo(NotAllowed):
    """A recorded demo replays only the steps its recording made (owner, 28 Oct 2026): a choice it didn't record ends
    here, before any work starts, instead of failing on a missing recording partway through."""


DEMO_LINE = ("Not in the recorded demo: the recording didn't make this choice, so it can't be shown here. "
             "Live research is open to judges through the link they were given.")


@functools.lru_cache(maxsize=16)
def _recorded_runs(case: str) -> frozenset | None:
    f = Path(case.split(",")[0]) / "case.json"
    runs = json.loads(f.read_text()).get("runs") if f.exists() else None
    return None if runs is None else frozenset(runs)


def demo_allows(project_id: str, kind: str, input_key: str) -> bool:
    """True unless the project is a recorded demo whose case lists its runs and this one isn't among them."""
    p = db.q("SELECT mode, demo_case FROM projects WHERE id=?", project_id)[0]
    if p["mode"] != "replay" or not p["demo_case"]:
        return True
    runs = _recorded_runs(p["demo_case"])
    return runs is None or f"{kind}:{input_key.replace(project_id, '{pid}')}" in runs


def start(project_id: str, kind: str, input_key: str, params: dict | None = None) -> str:
    from . import grants  # every live run is admitted here, so a revoked link stops new work on its next request
    if db.q("SELECT 1 FROM projects WHERE id=? AND mode='live'", project_id) and not grants.live_project(project_id):
        raise NotAllowed("Live research is open through a judge link. This link was revoked or isn't valid.")
    if not demo_allows(project_id, kind, input_key):
        raise NotInDemo(DEMO_LINE)
    existing = db.q("SELECT id FROM runs WHERE project_id=? AND kind=? AND input_key=? AND status IN ('queued','running')",
                    project_id, kind, input_key)
    if existing:
        return existing[0]["id"]
    rid = db.new_id("run")
    db.x("INSERT INTO runs(id, project_id, kind, input_key, status, detail, started_at) VALUES(?,?,?,?,?,?,?)",
         rid, project_id, kind, input_key, "queued", json.dumps({"params": params or {}}), db.now())
    db.event(project_id, "run", {"status": "queued", "run_kind": kind}, rid)
    _submit(rid)
    return rid


def _submit(run_id: str):
    _cancels.setdefault(run_id, threading.Event())
    EXEC.submit(_execute, run_id)


def _finish(run: Run, status: str, message: str = ""):
    db.x("UPDATE runs SET status=?, ended_at=?, detail=json_set(detail, '$.message', ?) WHERE id=?",
         status, db.now(), message, run.id)
    run.emit("run", {"status": status, "run_kind": run.kind, "message": message})


def _execute(run_id: str):
    r = db.q("SELECT * FROM runs WHERE id=?", run_id)[0]
    try:
        cas = cassette_for(r["project_id"])
    except Exception as e:  # never leave a run silently queued
        traceback.print_exc()
        db.x("UPDATE runs SET status='failed', ended_at=? WHERE id=?", db.now(), run_id)
        db.event(r["project_id"], "run", {"status": "failed", "run_kind": r["kind"], "message": f"Could not start: {e}"}, run_id)
        return
    run = Run(r["id"], r["project_id"], r["kind"], json.loads(r["detail"])["params"], cas,
              _cancels.setdefault(run_id, threading.Event()))
    if r["cancel_requested"]:
        run.cancel.set()
    db.x("UPDATE runs SET status='running' WHERE id=?", run_id)
    run.emit("run", {"status": "running", "run_kind": run.kind})
    try:
        ended = HANDLERS[run.kind](run)  # a handler may end its run (status, message), e.g. ("partial", "…")
        _finish(run, *(ended or ("completed",)))
    except Cancelled:
        _finish(run, "cancelled", "Stopped by you. Completed work is kept.")
    except NeedsInput as e:
        _finish(run, "needs_input", str(e))
    except BudgetExhausted as e:
        _finish(run, "partial", f"Live work paused: operating allowance reached ({e}). Completed work is kept.")
    except NoRecording as e:
        _finish(run, "failed", f"Recorded demonstration data does not cover this step ({e}).")
    except Exception as e:
        traceback.print_exc()
        _finish(run, "failed", f"{type(e).__name__}: {str(e)[:200]}")


def cancel(run_id: str) -> None:
    db.x("UPDATE runs SET cancel_requested=1 WHERE id=?", run_id)
    _cancels.setdefault(run_id, threading.Event()).set()


def resume_all() -> int:
    """On boot: runs that were queued or running when the process stopped continue from their last completed step."""
    rows = db.q("SELECT id FROM runs WHERE status IN ('queued','running')")
    db.x("UPDATE tasks SET status='queued' WHERE status='running'")
    for row in rows:
        _submit(row["id"])
    return len(rows)
