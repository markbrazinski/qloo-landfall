"""SQLite persistence: versioned records, sources, runs/tasks, append-only events and per-project usage."""
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, session TEXT NOT NULL, url TEXT NOT NULL, mode TEXT NOT NULL,
  demo_case TEXT, idem_key TEXT UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS records(id TEXT NOT NULL, version INTEGER NOT NULL, kind TEXT NOT NULL, project_id TEXT NOT NULL,
  payload TEXT NOT NULL, inputs TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, PRIMARY KEY(id, version));
CREATE INDEX IF NOT EXISTS records_project ON records(project_id, kind);
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, url TEXT NOT NULL, final_url TEXT,
  status INTEGER, fetched_at TEXT, title TEXT, text TEXT, error TEXT, replayed INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS sources_project ON sources(project_id, url);
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL, input_key TEXT NOT NULL,
  status TEXT NOT NULL, detail TEXT, started_at TEXT NOT NULL, ended_at TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS runs_active ON runs(project_id, kind, input_key)
  WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS tasks(run_id TEXT NOT NULL, task_key TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  detail TEXT, result TEXT, PRIMARY KEY(run_id, task_key));
CREATE TABLE IF NOT EXISTS events(project_id TEXT NOT NULL, seq INTEGER NOT NULL, at TEXT NOT NULL, run_id TEXT,
  kind TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(project_id, seq));
CREATE TABLE IF NOT EXISTS usage(project_id TEXT NOT NULL, run_id TEXT, at TEXT NOT NULL, provider TEXT NOT NULL, stage TEXT,
  replayed INTEGER NOT NULL, input_tokens INTEGER, output_tokens INTEGER, est_usd REAL, status INTEGER);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
    return _conn


def reset_for_tests(path) -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
        config.DB_PATH = path


def q(sql: str, *args) -> list[sqlite3.Row]:
    with _lock:
        return conn().execute(sql, args).fetchall()


def x(sql: str, *args) -> int:
    with _lock:
        return conn().execute(sql, args).rowcount


# ---- records (immutable versions) ----
def put_record(kind: str, project_id: str, payload: dict, inputs: list | None = None, rid: str | None = None) -> dict:
    with _lock:
        rid = rid or new_id(kind[:3])
        row = q("SELECT MAX(version) v FROM records WHERE id=?", rid)
        version = (row[0]["v"] or 0) + 1
        x("INSERT INTO records VALUES(?,?,?,?,?,?,?)", rid, version, kind, project_id, json.dumps(payload),
          json.dumps(inputs or []), now())
        return {"id": rid, "version": version, **payload}


def latest(kind: str, project_id: str) -> dict | None:
    rows = q("SELECT * FROM records WHERE kind=? AND project_id=? ORDER BY created_at DESC, version DESC LIMIT 1", kind, project_id)
    return _rec(rows[0]) if rows else None


def get_record(rid: str, version: int | None = None) -> dict | None:
    rows = (q("SELECT * FROM records WHERE id=? AND version=?", rid, version) if version
            else q("SELECT * FROM records WHERE id=? ORDER BY version DESC LIMIT 1", rid))
    return _rec(rows[0]) if rows else None


def records(kind: str, project_id: str) -> list[dict]:
    return [_rec(r) for r in q("SELECT * FROM records WHERE kind=? AND project_id=? ORDER BY created_at, version", kind, project_id)]


def _rec(r) -> dict:
    return {"id": r["id"], "version": r["version"], "kind": r["kind"], "project_id": r["project_id"], "created_at": r["created_at"],
            "inputs": json.loads(r["inputs"]), **json.loads(r["payload"])}


# ---- events ----
def event(project_id: str, kind: str, payload: dict, run_id: str | None = None) -> int:
    with _lock:
        seq = (q("SELECT MAX(seq) s FROM events WHERE project_id=?", project_id)[0]["s"] or 0) + 1
        x("INSERT INTO events VALUES(?,?,?,?,?,?)", project_id, seq, now(), run_id, kind, json.dumps(payload))
        return seq


def events(project_id: str, after: int = 0, kind: str | None = None) -> list[dict]:
    sql = "SELECT * FROM events WHERE project_id=? AND seq>?" + (" AND kind=?" if kind else "") + " ORDER BY seq"
    rows = q(sql, project_id, after, kind) if kind else q(sql, project_id, after)
    return [{"seq": r["seq"], "at": r["at"], "run_id": r["run_id"], "kind": r["kind"], **json.loads(r["payload"])} for r in rows]
