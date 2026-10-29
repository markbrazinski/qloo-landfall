"""The recorded demo track (owner decision, 28 Oct 2026): Death Wish Coffee and UNTUCKit replay offline, hold no
Influencers.club exchange (their terms) and no secret. The full browser walk to the Launch page is scripts/record_demo.py."""
import json
import re

import pytest
from conftest import pause

from landfall import app as app_mod, config, db, runner
from landfall.cassette import Cassette, key_of, read_exchanges
from scripts.record_demo import IC, IC_HOST, secrets

CASES = {k: v for k, v in app_mod.DEMO_CASES.items() if k != "tidewell"}
# the recordings are git-ignored until a curated set is chosen (owner, 28 Oct 2026): a fresh clone skips these
pytestmark = pytest.mark.skipif(not all((v / "case.json").exists() for v in CASES.values()), reason="demo recordings not present")


@pytest.mark.parametrize("name", CASES)
def test_case_loads_and_holds_no_influencers_club_data(name):
    d = CASES[name]
    case = json.loads((d / "case.json").read_text())
    assert case["input"]["url"].startswith("https://") and case["walk"]["city_id"] and case["input"]["goal"]["scope"]
    text = read_exchanges(d)
    rows = [json.loads(x) for x in text.split("\n") if x]
    assert rows and all(r["provider"] != IC for r in rows)
    assert IC_HOST not in text  # a web search may cite their public site; never their API
    assert Cassette("replay", replay_dirs=[d])._index  # the replay loader reads it


@pytest.mark.parametrize("name", CASES)
def test_case_files_hold_no_secret(name):
    for f in (CASES[name] / "case.json", CASES[name] / "exchanges.jsonl.gz"):
        text = read_exchanges(f.parent) if f.suffix == ".gz" else f.read_text()
        for s in secrets():  # read in memory from the environment and .env; never printed
            assert s not in text, f"a configured secret or private address is in {f.name}"
        assert not re.search(r"Bearer (?=[\w.-]*\d)[\w.-]{20,}|(?<![\w+/])AKIA[0-9A-Z]{16}(?![\w+/=])", text), f.name
        for row in (json.loads(x) for x in text.split("\n") if x) if f.suffix == ".gz" else ():
            headers = (row["response"].get("headers") or {}) if isinstance(row["response"], dict) else {}
            assert not {k.lower() for k in headers} & {"set-cookie", "authorization", "x-api-key", "cookie"}, f.name


def _wait(client, pid, done, what, timeout=120):
    for _ in range(int(timeout / 0.2)):
        v = client.get(f"/api/projects/{pid}").json()
        if done(v):
            return v
        pause(0.2)
    raise AssertionError(f"{what} did not finish: {v.get('run')} {v.get('markets')}")


@pytest.mark.parametrize("name", CASES)
def test_demo_replays_brief_and_markets_offline(client, name, monkeypatch):
    """No grant → the demo; the recorded brief, Qloo identity, the 80-city scan and the recommendation replay with no
    live call (conftest blocks the network and the ledger is the test's own)."""
    monkeypatch.setitem(config._ENV, "LANDFALL_REQUIRE_GRANT", "on")
    case = json.loads((CASES[name] / "case.json").read_text())
    r = client.post("/api/projects", json={"url": "https://example.com", "demo_case": name}, headers={"Idempotency-Key": name}).json()
    assert r["track"] == "demo"
    pid = r["project_id"]
    v = _wait(client, pid, lambda v: v["brief"] and v["run"]["status"] not in ("queued", "running")
              and v["identity"]["status"] not in ("searching", "not_started") and not (v["profile"] or {}).get("proposing"), "brief")
    assert v["url"] == case["input"]["url"] and v["demo"] and v["run"]["status"] == "completed", v["run"]
    assert v["intent"]["scope"] == case["input"]["goal"]["scope"] and v["intent"]["ceiling_usd"] == case["input"]["goal"]["ceiling_usd"]
    assert v["identity"]["status"] == "decided"
    prof = v["profile"]
    if v["markets_reason"] and prof and prof["route"] != "brand_entity":  # what Explore markets does (Brand.tsx)
        client.put(f"/api/projects/{pid}/profile", json={"base_version": prof["version"], "confirm": True, "signals": [
            {k: s[k] for k in ("qloo_id", "kind", "included")} for s in prof["signals"]]})
    assert client.post(f"/api/projects/{pid}/scan").status_code == 200
    for _ in range(1500):
        m = client.get(f"/api/projects/{pid}/markets").json()
        if (m.get("assessment") or {}).get("cards") and m.get("shortlist_run", {}).get("status") not in ("queued", "running"):
            break
        pause(0.2)
    names = [c["name"] for c in m["assessment"]["cards"]]
    assert case["walk"]["city_name"] in names, names
    failed = db.q("SELECT kind, detail FROM runs WHERE project_id=? AND status NOT IN ('completed')", pid)
    assert not failed, [dict(f) for f in failed]
    assert not config.LEDGER_PATH.exists() or not config.LEDGER_PATH.read_text().strip()  # nothing live was admitted


def test_a_repeated_key_replays_its_own_stages_recording(tmp_path):
    """The same page read by research, then by a specialist, can differ (a fresh error reference); each run's cassette
    starts counting at zero, so the specialist must get its own copy, not research's (DWC replay, 28 Oct 2026)."""
    k = key_of("fetch", "GET", {})
    rows = [{"key": k, "stage": s, "provider": "fetch", "op": "GET", "request": {}, "response": {"body": b}}
            for s, b in (("research_fetch", "ref 1"), ("specialist_fetch", "ref 2"))]
    (tmp_path / "exchanges.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    cas = Cassette("replay", replay_dirs=[tmp_path])
    assert cas.call("fetch", "GET", {}, None, "specialist_fetch")["body"] == "ref 2"
    assert Cassette("replay", replay_dirs=[tmp_path]).call("fetch", "GET", {}, None, "research_fetch")["body"] == "ref 1"
    cas.call("fetch", "GET", {}, None, "specialist_fetch")["body"] = "mutated"  # a served answer is a copy
    assert cas.recorded(k, "specialist_fetch")["response"]["body"] == "ref 2"


def test_a_demo_starts_only_the_runs_its_recording_made(client):
    pid = client.post("/api/projects", json={"demo": True, "demo_case": "deathwishcoffee"}).json()["project_id"]
    case = json.loads((CASES["deathwishcoffee"] / "case.json").read_text())
    assert runner.demo_allows(pid, "auto_plan", case["walk"]["city_id"])
    assert not runner.demo_allows(pid, "auto_plan", "dublin")
    r = client.patch(f"/api/projects/{pid}/shortlist", json={"base_version": 0, "beachhead": "dublin"})
    assert r.status_code == 409 and "Not in the recorded demo" in r.json()["detail"]
    tidewell = client.post("/api/projects", json={"demo": True, "demo_case": "tidewell"}).json()["project_id"]
    assert runner.demo_allows(tidewell, "auto_plan", "dublin")  # a case without a run list is unrestricted
