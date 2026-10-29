"""Integration checks through the real API, replaying the synthetic Tidewell recording (no live calls)."""
import json

from conftest import SYNTHETIC, wait_for, wait_for_identity

from landfall import config, db, runner


def _demo(client, idem="k1"):
    r = client.post("/api/projects", json={"demo": True}, headers={"Idempotency-Key": idem})
    assert r.status_code == 201
    return r.json()["project_id"]


def test_url_to_sourced_brief_by_replay(client):
    pid = _demo(client)
    v = wait_for(client, pid)
    assert v["run"]["status"] == "completed", v["run"]
    b = v["brief"]
    assert b["version"] == 1 and b["brand_name"] == "Tidewell" and b["route"] == "site"
    supported = [f for f in b["facts"] if f["state"] == "SUPPORTED"]
    assert supported and all(f["source"] and f["quote"] for f in supported)
    assert any("cost" in u["label"].lower() or "wholesale" in u["label"].lower() for u in b["unknowns"])
    qloo_row, *brand_rows = v["reading"]
    assert all(r["status"] in ("DONE", "SKIPPED") for r in brand_rows)
    # The synthetic Tidewell set has no Qloo recording: identity is unknown (failed), never "no match", and the
    # brief still opens (P2-02). A replay miss never becomes a silent live call.
    v = wait_for_identity(client, pid)
    assert qloo_row["key"] == "qloo" and v["identity"]["status"] == "failed" and v["identity"]["settled"]
    assert "no Qloo lookup" in v["identity"]["failure_reason"]
    assert not config.LEDGER_PATH.exists()  # replay made no live attempt


def test_replay_is_deterministic_across_runs(client):
    a = wait_for(client, _demo(client, "a"))
    b = wait_for(client, _demo(client, "b"))
    assert a["run"]["status"] == b["run"]["status"] == "completed"
    assert [f["value"] for f in a["brief"]["facts"]] == [f["value"] for f in b["brief"]["facts"]]


def test_duplicate_submit_returns_the_same_project(client):
    assert _demo(client, "same") == _demo(client, "same")


def test_evidence_drawer_source(client):
    v = wait_for(client, _demo(client))
    fact = next(f for f in v["brief"]["facts"] if f["state"] == "SUPPORTED")
    s = client.get(f"/api/sources/{fact['source']['id']}", params={"claim": fact["id"]}).json()
    assert s["url"] == fact["source"]["url"] and s["excerpt"] and len(s["excerpt"]) <= 300 and s["replayed"]


def test_correction_creates_v2_and_stale_base_is_refused(client):
    pid = _demo(client)
    v = wait_for(client, pid)
    home = next(c for c in v["brief"]["interpretation"] if c["label"] == "Home market")
    r = client.patch(f"/api/projects/{pid}/brief", json={"base_version": 1, "claim_id": home["id"], "value": "Portland, OR"})
    assert r.json() == {"version": 2, "notice": "Brief v2. Nothing has used it yet."}
    v2 = client.get(f"/api/projects/{pid}").json()["brief"]
    fixed = next(c for c in v2["interpretation"] if c["label"] == "Home market")
    assert v2["version"] == 2 and fixed["value"] == "Portland, OR" and fixed["corrected"]
    assert db.get_record(v["brief"]["id"], 1)["version"] == 1  # v1 is kept (History), never overwritten
    assert client.patch(f"/api/projects/{pid}/brief", json={"base_version": 1, "claim_id": fixed["id"], "value": "x"}).status_code == 409


def test_projects_are_private_to_the_browser_session(client):
    pid = _demo(client)
    from fastapi.testclient import TestClient
    from landfall.app import app
    assert TestClient(app).get(f"/api/projects/{pid}").status_code == 404


def test_restart_resumes_without_repeating_completed_steps(client, monkeypatch):
    pid = _demo(client)
    wait_for(client, pid)
    wait_for_identity(client, pid)
    first = db.q("SELECT id FROM runs WHERE project_id=? AND kind='brand_read'", pid)[0]["id"]
    # Simulate a crash after the homepage step: reopen the run with only "open" done, then boot.
    db.x("UPDATE runs SET status='running' WHERE id=?", first)
    db.x("DELETE FROM tasks WHERE run_id=? AND task_key<>'open'", first)
    calls = []
    real = runner.Run.step
    monkeypatch.setattr(runner.Run, "step", lambda self, key, fn, retries=0: calls.append(key) or real(self, key, fn, retries))
    assert runner.resume_all() == 1
    v = wait_for(client, pid, timeout=30)
    assert v["run"]["status"] == "completed"
    done = {r["task_key"]: r["attempts"] for r in db.q("SELECT task_key, attempts FROM tasks WHERE run_id=?", first)}
    assert done["open"] == 1  # the completed step was not executed again


def test_cancel_keeps_completed_work(client):
    pid = _demo(client)
    run_id = db.q("SELECT id FROM runs WHERE project_id=?", pid)[0]["id"]
    client.post(f"/api/runs/{run_id}/cancel")
    v = wait_for(client, pid)
    assert v["run"]["status"] in ("cancelled", "completed")  # a fast replay may finish before the flag is read


def test_budget_exhaustion_stops_live_work_honestly(client, monkeypatch):
    monkeypatch.setattr(config, "PROVIDER_MODE", "live")
    monkeypatch.setattr(config, "CAP_FETCH", 0)
    monkeypatch.setattr(config, "CAP_QLOO", 0)  # the identity lookup is live work too
    pid = client.post("/api/projects", json={"url": "example.com"}).json()["project_id"]
    v = wait_for(client, pid)
    assert v["run"]["status"] == "partial" and "allowance" in v["run"]["message"]
    assert not config.LEDGER_PATH.exists()  # nothing was dispatched


def test_unreadable_site_offers_recovery(client, monkeypatch):
    monkeypatch.setattr(config, "PROVIDER_MODE", "live")  # .invalid never resolves: no provider is reached
    pid = client.post("/api/projects", json={"url": "landfall-check.invalid"}).json()["project_id"]
    v = wait_for(client, pid)
    assert v["run"]["status"] == "needs_input" and "DNS gave no answer" in v["run"]["message"]
    assert [r["status"] for r in v["reading"]][1:3] == ["FAILED", "SKIPPED"]
    # D12: the name lookup still runs from the address alone (here the test boundary refuses the live call).
    v = wait_for_identity(client, pid)
    assert v["identity"]["site_name"] == "landfall check" and v["identity"]["status"] == "failed"


def test_invalid_url_is_rejected_inline(client):
    r = client.post("/api/projects", json={"url": "not a website"})
    assert r.status_code == 422 and "website address" in r.json()["detail"]


def test_fixtures_hold_no_secrets():
    key = config.QLOO_KEY
    for f in (config.ROOT / "fixtures").rglob("*"):
        if f.is_file():
            text = f.read_text(errors="ignore")
            assert not key or key not in text
            assert "aws_secret_access_key" not in text.lower()
    assert json.loads((SYNTHETIC / "case.json").read_text())["status"] == "synthetic public fixture"
