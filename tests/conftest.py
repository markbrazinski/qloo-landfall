import time

import pytest
from fastapi.testclient import TestClient

from landfall import config, db

SYNTHETIC = config.ROOT / "fixtures" / "synthetic" / "tidewell-brand-v4"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test gets its own database and ledger; nothing touches the real usage ledger or a live provider."""
    db.reset_for_tests(tmp_path / "t.db")
    monkeypatch.setattr(config, "LEDGER_PATH", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(config, "PROVIDER_MODE", "replay")
    yield
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
        time.sleep(0.2)
    raise AssertionError(f"run did not finish: {v['run']}")
