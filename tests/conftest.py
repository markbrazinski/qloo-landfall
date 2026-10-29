import ipaddress
import socket
import tempfile
import threading
import time
from pathlib import Path

import httpx

import pytest
from fastapi.testclient import TestClient

from landfall import config, db, qloo, runner

# Tests stub qloo's pacing with `qloo.time.sleep`, which is the global time.sleep: polling loops wait on an Event instead,
# or they spin and starve the run threads under load (flaky "runs did not finish" timeouts).
pause = threading.Event().wait

SYNTHETIC = config.ROOT / "fixtures" / "synthetic" / "tidewell-brand-v6"

# ---- process-lifetime guards: set once at import and never undone ----
# A worker that outlives its test (review slice 2, blocker 1) still finds no route to a provider and no real ledger:
# monkeypatch undo restores these session values, never the real ones.
REAL_LEDGER = config.LEDGER_PATH
_SESSION = Path(tempfile.mkdtemp(prefix="landfall-tests-"))
config.LEDGER_PATH = _SESSION / "ledger.jsonl"
config.PRIVATE = _SESSION / "private"
# deployment switches never come from the developer's .env; tests that need them set them explicitly
for _k in ("LANDFALL_SHARED_DELIVERY", "LANDFALL_REQUIRE_GRANT"):
    config._ENV.pop(_k, None)
config.AUTO_PLAN = False  # the existing journeys drive research, investigation and the plan step by step
_connect, _connect_ex = socket.socket.connect, socket.socket.connect_ex


def _loopback(address) -> bool:
    if not isinstance(address, tuple):
        return True  # AF_UNIX and the like
    try:
        return ipaddress.ip_address(str(address[0]).split("%")[0]).is_loopback
    except ValueError:
        return address[0] == "localhost"


def _guard(real):
    def connect(self, address):
        if not _loopback(address):
            raise ConnectionRefusedError(f"tests never reach the network ({address[0]})")
        return real(self, address)
    return connect


socket.socket.connect, socket.socket.connect_ex = _guard(_connect), _guard(_connect_ex)


def drain_runs(timeout: float = 30) -> None:
    """Cancel every run and wait until each has ended, before any test guard is restored. A run that will not end is
    a test failure, never something to leave running beneath restored configuration."""
    active = lambda: db.q("SELECT id FROM runs WHERE status IN ('queued','running')")  # noqa: E731
    for r in active():
        runner.cancel(r["id"])
    end = time.monotonic() + timeout
    while left := active():
        if time.monotonic() > end:
            raise RuntimeError(f"{len(left)} run(s) still active at teardown; the process guards still hold")
        pause(0.05)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test gets its own database and ledger; nothing touches the real usage ledger or a live provider."""
    db.reset_for_tests(tmp_path / "t.db")
    monkeypatch.setattr(config, "LEDGER_PATH", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(config, "PROVIDER_MODE", "replay")
    monkeypatch.setattr(config, "REPLAY_CASES", [])
    monkeypatch.setattr(config, "LIVE_STAGES", ())

    def no_live_qloo(*a, **k):  # the provider boundary: a test that needs Qloo answers replaces this
        raise httpx.ConnectError("live Qloo is blocked in tests")
    monkeypatch.setattr(qloo.httpx, "get", no_live_qloo)
    monkeypatch.setattr(qloo.time, "sleep", lambda s: None)
    monkeypatch.setattr(qloo, "PACE_S", 0)  # the pacer has its own test
    yield
    # A run that outlived its test once reached live Qloo with the real ledger (2 attempts, 24 Oct; noted there).
    drain_runs()
    db.reset_for_tests(tmp_path / "t.db")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LANDFALL_DEMO_CASE", str(SYNTHETIC))
    from landfall.app import app
    with TestClient(app) as c:
        yield c


def wait_for(client, pid, statuses=("completed", "needs_input", "failed", "partial", "cancelled"), timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        v = client.get(f"/api/projects/{pid}").json()
        if v["run"] and v["run"]["status"] in statuses:
            return v
        pause(0.2)
    raise AssertionError(f"run did not finish: {v['run']}")


def wait_for_identity(client, pid, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        v = client.get(f"/api/projects/{pid}").json()
        if v["identity"]["status"] not in ("searching",):
            return v
        pause(0.2)
    raise AssertionError(f"identity did not settle: {v['identity']}")
