"""Slice 2: Qloo identity and discovery profile (BUILD-PLAN slice 2 tests, CONTRACTS B3).

Qloo answers here are SYNTHETIC, injected at the provider boundary (httpx in landfall.qloo); the brand read replays the
synthetic Tidewell recording. Tests marked `captured` read the private live captures and skip in a fresh clone.
"""
import json
from types import SimpleNamespace

import pytest
from conftest import SYNTHETIC, wait_for, wait_for_identity

from landfall import config, db, identity, qloo, runner

PRIVATE = config.ROOT / "live-captures-private"


# ---- name derivation (P2-01) on the four captured titles ----
@pytest.mark.parametrize("url, final, title, name, source, q2", [
    ("https://komono.com/", "https://komono.com/", "KOMONO watches, sunglasses & opticals", "KOMONO",
     "title_matches_address", None),
    ("https://gotbag.com/", "https://got-bag.com/", "GOT BAG Taschen » From Trash to Treasure.", "GOT BAG",
     "title_matches_address", "GOT BAG Taschen"),
    ("https://tidewell.example/", None, "Sparkling cold-brewed tea · Tidewell", "Tidewell", "title_matches_address",
     "Sparkling cold-brewed tea"),
    ("https://stubbornsoda.com/", None, "Welcome to our store", "stubbornsoda", "address", "Welcome to our store"),
])
def test_name_derivation_on_captured_titles(url, final, title, name, source, q2):
    n = identity.derive_names(url, final, title)
    assert (n["site_name"], n["name_source"], n["query2"]) == (name, source, q2)


def test_host_labels_and_title_separators():
    assert identity.address_labels("https://www.got-bag.com/de", "https://gotbag.com") == ["got-bag", "gotbag"]
    assert identity.derive_names("https://my-brand.com", None, "")["site_name"] == "my brand"
    assert identity.derive_names("https://x.com", None, "Shop: Things")["query2"] == "Shop"
    assert identity.derive_names("https://x.com", None, "Cold-brewed tea")["query2"] == "Cold-brewed tea"
    # never from the brief: the only inputs are the URL, its redirect target and the homepage title
    assert set(identity.derive_names.__code__.co_varnames[:3]) == {"url", "final_url", "title"}


# ---- confidence rule ----
def cand(name, official=None, i=0):
    return identity.candidate({"entity_id": f"E{i}", "name": name, "properties": {"official_site": official} if official else {}},
                              "Komono", ["komono.com"], "Komono watches")


def test_confidence_rule():
    assert identity.confidence([cand("Komono")], "KOMONO") == ["only candidate", "name matches your site", "no website to confirm"]
    assert identity.confidence([cand("Komono", "https://www.komono.com")], "KOMONO")[2] == "website matches"
    assert identity.confidence([cand("Komono"), cand("Komono Inn", i=1)], "KOMONO") is None  # two or more → ask
    assert identity.confidence([cand("Komono Optics")], "KOMONO") is None  # name mismatch → ask
    assert identity.confidence([cand("Komono", "https://komono-hotel.jp")], "KOMONO") is None  # contradiction → ask
    assert identity.confidence([cand("The Komono")], "Komono") is not None  # a leading "the" is ignored


def test_candidate_order_and_corroboration():
    cs = identity.order([cand("Other", i=0), cand("Komono", i=1), cand("komono", "komono.com", i=2)])
    assert [c["qloo_id"] for c in cs] == ["E2", "E1", "E0"]
    assert cs[0]["corroboration"] == {"name_match": "normalised", "domain_match": True, "site_mentions_name": True}
    assert cs[2]["corroboration"]["domain_match"] is None  # Qloo gave no website: cannot tell


def _captured_candidates(path, query):
    f = PRIVATE / "probes" / path / "exchanges.jsonl"
    if not f.exists():
        pytest.skip("captured probe not present (private)")
    ex = next(json.loads(x) for x in f.read_text().split("\n") if x and json.loads(x)["request"].get("params", {}).get("query") == query)
    return [identity.candidate(e, query, [], None) for e in qloo.rows(ex["response"])[0]]


def test_captured_komono_response_matches_automatically():
    n = identity.derive_names("https://komono.com/", "https://komono.com/", "KOMONO watches, sunglasses & opticals")
    cands = _captured_candidates("qloo-identity-path", n["site_name"])  # the derived query, probed live 24 Oct
    assert identity.confidence(cands, n["site_name"]) is not None and cands[0]["name"] == "Komono"


def test_captured_gotbag_response_asks():
    n = identity.derive_names("https://gotbag.com/", "https://got-bag.com/", "GOT BAG Taschen » From Trash to Treasure.")
    cands = _captured_candidates("qloo", n["site_name"])
    assert len(cands) == 5 and identity.confidence(cands, n["site_name"]) is None


# ---- Qloo shapes and validation ----
def test_tag_id_and_id_normalise_to_qloo_id():
    assert qloo.tag_ref({"tag_id": "urn:tag:a", "name": "A", "type": "t"})["qloo_id"] == "urn:tag:a"
    assert qloo.tag_ref({"id": "urn:tag:b", "name": "B", "type": "t"})["qloo_id"] == "urn:tag:b"


@pytest.mark.parametrize("t, ok", [("urn:tag:genre:brand", True), ("urn:tag:lifestyle:qloo", True),
                                   ("urn:tag:customer_segment:qloo", True), ("urn:tag:category:place", False),
                                   ("urn:tag:genre:place", False), ("urn:tag:similar_brand:qloo", False),
                                   ("urn:tag:competitor_brand:qloo", False), ("urn:tag:decor:qloo", False)])
def test_allowed_tag_types(t, ok):
    assert identity.allowed(t) is ok


def _brief_with_facts(pid):
    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", pid, "s", "https://x.example", "live", None, None, db.now())
    f1 = db.put_record("claim", pid, {"label": "What you sell", "value": "Backpacks from recycled ocean plastic",
                                      "state": "SUPPORTED", "source_ids": []})
    f2 = db.put_record("claim", pid, {"label": "Founded", "value": "2018", "state": "TO_CHECK", "source_ids": []})
    return db.put_record("brief", pid, {"brand_name": "X", "route": "site", "facts": [f1["id"], f2["id"]],
                                        "interpretation": [], "unknowns": [], "corrections": []}, rid=f"brief_{pid}"), [f1, f2]


def test_validation_rejects_unrecorded_invented_and_wrong_type_ids():
    brief, facts = _brief_with_facts("prj_v")
    for qid, kind, typ in [("urn:tag:lifestyle:qloo:outdoor", "tag", "urn:tag:lifestyle:qloo"),
                           ("urn:tag:category:place:bag_store", "tag", "urn:tag:category:place"),
                           ("ENT-1", "entity", "urn:entity:brand")]:
        db.x("INSERT INTO qloo_refs VALUES(?,?,?,?,?)", "prj_v", qid, kind, qid.split(":")[-1], typ)
    proposal = {"signals": [
        {"qloo_id": "urn:tag:lifestyle:qloo:outdoor", "kind": "tag", "name": "Outdoor", "basis_facts": [1], "reasoning": "r"},
        {"qloo_id": "urn:tag:made:up", "kind": "tag", "name": "Invented", "basis_facts": [1], "reasoning": "r"},
        {"qloo_id": "urn:tag:category:place:bag_store", "kind": "tag", "name": "Bag store", "basis_facts": [1], "reasoning": "r"},
        {"qloo_id": "urn:tag:lifestyle:qloo:outdoor", "kind": "tag", "name": "Outdoor", "basis_facts": [1], "reasoning": "r"},
        {"qloo_id": "ENT-1", "kind": "tag", "name": "Wrong kind", "basis_facts": [1], "reasoning": "r"},
        {"qloo_id": "ENT-1", "kind": "entity", "name": "Comparable", "basis_facts": [1, 9], "reasoning": "similar bags"},
    ]}
    signals, dropped = identity.validate("prj_v", proposal, brief, facts)
    assert [s["name"] for s in signals] == ["outdoor", "ENT-1"]
    assert [d["reason"] for d in dropped] == ["not returned by any Qloo response for this project",
                                             "tag type not used for discovery (Category)", "proposed twice",
                                             "is a Qloo entity, not a tag"]
    comparable = signals[1]
    assert comparable["origin"] == "COMPARABLE" and comparable["included"] is False  # comparables unticked
    # basis resolution is slice 1's resolver: unknown fact numbers are dropped and recorded
    assert comparable["basis_claim_ids"] == [facts[0]["id"]] and comparable["basis_dropped"] == ["fact 9"]


def test_signal_resting_only_on_an_unestablished_fact_is_not_established():
    brief, facts = _brief_with_facts("prj_w")
    db.x("INSERT INTO qloo_refs VALUES(?,?,?,?,?)", "prj_w", "urn:tag:genre:brand:x", "tag", "X", "urn:tag:genre:brand")
    signals, _ = identity.validate("prj_w", {"signals": [
        {"qloo_id": "urn:tag:genre:brand:x", "kind": "tag", "name": "X", "basis_facts": [2], "reasoning": "r"}]}, brief, facts)
    assert signals[0]["basis_established"] is False


# ---- integration: synthetic Qloo at the provider boundary, synthetic Tidewell brand read replayed ----
class FakeQloo:
    """SYNTHETIC Qloo answers at the HTTP boundary."""

    def __init__(self):
        self.calls, self.status, self.search, self.tags = [], 503, [], []

    def __call__(self, url, params=None, **_):
        self.calls.append((url.rsplit("/", 1)[-1], dict(params or {})))
        body = {"results": self.search} if url.endswith("/search") else {"results": {"tags": self.tags}}
        return SimpleNamespace(status_code=self.status, headers={"content-type": "application/json"},
                               text=json.dumps(body) if self.status == 200 else "")


SYN_TAG = {"id": "urn:tag:lifestyle:qloo:running", "name": "Running", "type": "urn:tag:lifestyle:qloo"}
SYN_PLACE = {"id": "urn:tag:category:place:tea_house", "name": "Tea house", "type": "urn:tag:category:place"}


def syn_brand(name, i):
    return {"entity_id": f"SYN-{i}", "name": name, "types": ["urn:entity:brand"],
            "properties": {"short_description": "SYNTHETIC test entity"},
            "tags": [{"tag_id": SYN_TAG["id"], "name": "Running", "type": SYN_TAG["type"]}]}


@pytest.fixture
def mixed(monkeypatch, tmp_path):
    """URL-entered projects replay the synthetic brand read; only the named stages may go to the fake Qloo."""
    fake = FakeQloo()
    monkeypatch.setattr(qloo.httpx, "get", fake)
    monkeypatch.setattr(config, "REPLAY_CASES", [SYNTHETIC])
    monkeypatch.setattr(config, "LIVE_STAGES", ("identity", "profile_edit"))
    monkeypatch.setattr(config, "PRIVATE", tmp_path / "private")
    return fake


def _start(client):
    pid = client.post("/api/projects", json={"url": "http://tidewell.example/"}).json()["project_id"]
    assert wait_for(client, pid)["run"]["status"] == "completed"
    return pid, wait_for_identity(client, pid)


def brand_runs(pid):
    return db.q("SELECT COUNT(*) n FROM runs WHERE project_id=? AND kind='brand_read'", pid)[0]["n"]


def test_p2_02_qloo_failure_keeps_the_brief_and_identity_retry_recovers(client, mixed):
    pid, v = _start(client)  # SYNTHETIC 503 on every attempt
    assert v["brief"] and v["identity"]["status"] == "failed" and v["identity"]["settled"]
    assert v["identity"]["decision"] is None and "503" in v["identity"]["failure_reason"]  # unknown, never "no match"
    assert v["reading"][0]["status"] == "FAILED" and "Retry the Qloo lookup" in v["markets_reason"]
    assert len(mixed.calls) == 3  # retried, and every attempt is a ledgered dispatch
    assert sum(1 for x in config.LEDGER_PATH.read_text().split("\n") if '"attempt"' in x and "qloo" in x) == 3
    again = client.get(f"/api/projects/{pid}").json()  # refresh keeps the failure
    assert again["identity"]["status"] == "failed"

    mixed.status, mixed.search = 200, [syn_brand("Tidewell", 1)]  # SYNTHETIC success
    assert client.post(f"/api/projects/{pid}/identity/retry").status_code == 200
    v = wait_for_identity(client, pid)
    assert v["identity"]["status"] == "decided" and v["identity"]["decided_by"] == "landfall"
    assert v["identity"]["chosen"]["name"] == "Tidewell" and v["identity"]["version"] == 2
    assert brand_runs(pid) == 1 and v["brief"]["version"] == 1  # the brand reader never re-ran
    assert v["profile"]["route"] == "brand_entity" and v["profile"]["confirmed"]
    assert v["markets_reason"] == "" and v["markets"]["stage"] == "ready"  # slice 3: Markets opens
    assert client.post(f"/api/projects/{pid}/identity/retry").status_code == 409  # settled: nothing to retry


def test_empty_search_is_a_decided_no_match_not_a_failure(client, mixed):
    mixed.status, mixed.search = 200, []
    pid, v = _start(client)
    assert v["identity"]["status"] == "decided" and v["identity"]["decision"] == "none"
    assert v["identity"]["queries"] == ["Tidewell", "Sparkling cold-brewed tea"]  # query 2 only after zero brands
    assert v["identity"]["basis"][0] == "Qloo returned no brand for 'Tidewell'"
    while (p := client.get(f"/api/projects/{pid}").json()["profile"]) is None or p["proposing"]:
        pass  # the no-match route starts the Profile agent (no recording here, so it fails cleanly)
    assert p["run"]["status"] == "failed"


def test_several_candidates_ask_then_neither_and_the_profile_editor(client, mixed, monkeypatch):
    mixed.status, mixed.search = 200, [syn_brand("Tidewell", 1), syn_brand("Tidewell Inn", 2)]
    pid, v = _start(client)
    ident = v["identity"]
    assert ident["status"] == "needs_founder" and not ident["settled"] and v["reading"][0]["status"] == "NEEDS YOU"
    assert client.post(f"/api/projects/{pid}/identity", json={"base_version": 1, "entity_id": "SYN-9"}).status_code == 422
    assert client.post(f"/api/projects/{pid}/identity", json={"base_version": 1, "entity_id": "none"}).json() == {"version": 2}
    assert client.post(f"/api/projects/{pid}/identity", json={"base_version": 1, "entity_id": "none"}).status_code == 409
    # The Profile agent has no recording and no live stage here, so the profile run fails; the editor still works.
    end = __import__("time").time() + 20
    while (p := client.get(f"/api/projects/{pid}").json()["profile"]) is None or p["proposing"]:
        assert __import__("time").time() < end
    assert p["run"]["status"] == "failed" and p["version"] == 0 and "Confirm your discovery profile" in \
        client.get(f"/api/projects/{pid}").json()["markets_reason"]

    mixed.tags = [SYN_TAG, SYN_PLACE]
    r = client.get(f"/api/projects/{pid}/qloo/tags", params={"q": "run"}).json()
    assert [t["name"] for t in r["results"]] == ["Running"] and r["hidden"] == 1  # place categories left out
    assert client.put(f"/api/projects/{pid}/profile", json={"base_version": 0, "confirm": True, "signals": [
        {"qloo_id": "urn:tag:made:up", "kind": "tag"}]}).status_code == 422  # never an unrecorded id
    assert client.put(f"/api/projects/{pid}/profile", json={"base_version": 0, "confirm": True, "signals": []}).status_code == 422
    ok = client.put(f"/api/projects/{pid}/profile", json={"base_version": 0, "confirm": True, "signals": [
        {"qloo_id": SYN_TAG["id"], "kind": "tag"}]})
    assert ok.json() == {"version": 1, "confirmed": True}
    assert client.put(f"/api/projects/{pid}/profile", json={"base_version": 0, "signals": []}).status_code == 409  # stale
    v = client.get(f"/api/projects/{pid}").json()
    assert v["profile"]["confirmed"] and v["profile"]["signals"][0]["origin"] == "YOU_SAID"
    assert v["markets_reason"] == "" and v["markets"]["stage"] == "ready"  # slice 3: Markets opens

    # A successful retry of the agent never overwrites what the founder already confirmed.
    monkeypatch.setattr(identity, "_propose", lambda run, brief, facts: {"signals": [], "searches": {"tags": 0, "brands": 0}})
    run_id = client.post(f"/api/projects/{pid}/profile/retry").json()["run_id"]
    while db.q("SELECT status FROM runs WHERE id=?", run_id)[0]["status"] in ("queued", "running"):
        pass
    assert client.get(f"/api/projects/{pid}").json()["profile"]["version"] == 1


def test_profile_retry_after_failure_proposes_and_comparables_stay_unticked(client, mixed, monkeypatch):
    mixed.status, mixed.search = 200, [syn_brand("Tidewell", 1), syn_brand("Tidewell Inn", 2)]
    pid, v = _start(client)
    client.post(f"/api/projects/{pid}/identity", json={"base_version": 1, "entity_id": "none"})
    while (p := client.get(f"/api/projects/{pid}").json()["profile"]) is None or p["proposing"]:
        pass
    assert p["run"]["status"] == "failed"
    proposal = {"searches": {"tags": 1, "brands": 1}, "signals": [  # SYNTHETIC agent output; ids came from Qloo above
        {"qloo_id": SYN_TAG["id"], "kind": "tag", "name": "Running", "basis_facts": [1], "reasoning": "SYNTHETIC"},
        {"qloo_id": "SYN-2", "kind": "entity", "name": "Tidewell Inn", "basis_facts": [1], "reasoning": "SYNTHETIC"}]}
    monkeypatch.setattr(identity, "_propose", lambda run, brief, facts: proposal)
    client.post(f"/api/projects/{pid}/profile/retry")
    while (p := client.get(f"/api/projects/{pid}").json()["profile"])["proposing"] or p["version"] == 0:
        pass
    assert p["route"] == "proposed_profile" and not p["confirmed"]
    assert [(s["origin"], s["included"]) for s in p["signals"]] == [("QLOO_CATEGORY", True), ("COMPARABLE", False)]
    assert p["signals"][0]["inferred_from"]["facts"][0]["label"]  # "Inferred from" reuses the brief's facts

    # Staleness: correcting a cited fact flags the signal and says so; nothing re-runs.
    brief = client.get(f"/api/projects/{pid}").json()["brief"]
    cited = p["signals"][0]["inferred_from"]["facts"][0]["id"]
    r = client.patch(f"/api/projects/{pid}/brief", json={"base_version": brief["version"], "claim_id": cited, "value": "Tea"})
    assert "your discovery profile's signals Running, Tidewell Inn used this fact; review them" in r.json()["notice"]
    v = client.get(f"/api/projects/{pid}").json()
    assert v["profile"]["fact_changed"] == ["Running", "Tidewell Inn"] and v["profile"]["version"] == 1

    # An identity change outdates the profile and its route recomputes as a new version.
    client.post(f"/api/projects/{pid}/identity", json={"base_version": 2, "entity_id": "SYN-1"})
    v = client.get(f"/api/projects/{pid}").json()
    assert v["profile"]["route"] == "brand_entity" and v["profile"]["version"] == 2 and v["profile"]["confirmed"]
    assert db.get_record(f"profile_{pid}", 1)["route"] == "proposed_profile"  # history kept


def test_restart_mid_identity_does_not_repeat_the_qloo_request(client, mixed):
    mixed.status, mixed.search = 200, [syn_brand("Tidewell", 1), syn_brand("Tidewell Inn", 2)]
    pid, v = _start(client)
    run_id = db.q("SELECT id FROM runs WHERE project_id=? AND kind='identity'", pid)[0]["id"]
    db.x("UPDATE runs SET status='running' WHERE id=?", run_id)
    db.x("DELETE FROM tasks WHERE run_id=? AND task_key='decide'", run_id)
    calls = len(mixed.calls)
    runner.resume_all()
    v = wait_for_identity(client, pid)
    assert v["identity"]["status"] == "needs_founder" and len(mixed.calls) == calls


def test_founder_tag_search_is_cached_and_limited(client, mixed, monkeypatch):
    mixed.status, mixed.search = 200, []
    pid, v = _start(client)
    mixed.tags = [SYN_TAG]
    n = len(mixed.calls)
    client.get(f"/api/projects/{pid}/qloo/tags", params={"q": "run"})
    client.get(f"/api/projects/{pid}/qloo/tags", params={"q": "run"})
    assert len(mixed.calls) == n + 1  # the second identical search came from the 7-day cache
    usage = db.q("SELECT origin FROM usage WHERE project_id=? AND stage='profile_edit'", pid)
    assert [u["origin"] for u in usage] == ["live", "cache"]
    monkeypatch.setattr("landfall.app.FOUNDER_SEARCHES_PER_HOUR", 2)
    assert client.get(f"/api/projects/{pid}/qloo/tags", params={"q": "tea"}).status_code == 429


def test_a_looping_profile_agent_is_stopped_by_the_tool_cap():
    """SYNTHETIC Nova at the botocore boundary: every turn asks for one more tag search."""
    from landfall.cassette import Cassette

    class Looping(Cassette):
        def __init__(self):
            super().__init__("replay")
            self.turns = 0

        def lookup(self, key):
            self.turns += 1
            body = {"output": {"message": {"role": "assistant", "content": [{"toolUse": {
                "toolUseId": f"t{self.turns}", "name": "qloo_tag_search", "input": {"term": "bag"}}}]}},
                "stopReason": "tool_use", "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}}
            return {"provider": "bedrock", "op": "Converse", "request": {},
                    "response": {"status": 200, "headers": {"content-type": "application/json"}, "body": json.dumps(body)}}

        def call(self, provider, op, request, live_fn, stage=""):
            return {"status": 200, "headers": {}, "body": json.dumps({"results": {"tags": []}})}

    brief, facts = _brief_with_facts("prj_loop")
    cas = Looping()
    run = runner.Run("run_loop", "prj_loop", "profile", {}, cas)
    with pytest.raises(RuntimeError, match="exceeded 16 tool calls"):
        identity._propose(run, brief, facts)
    assert cas.turns <= identity.MAX_TOOL_CALLS + 2  # the loop ended at the cap, not at Nova's whim


L2 = [PRIVATE / "cases" / "L2-A-komono" / "v1", PRIVATE / "cases" / "L2-B-gotbag" / "v1"]


@pytest.mark.skipif(not all(d.exists() for d in L2), reason="private live cases not present")
def test_locked_l2_cases_replay_through_current_code(client, monkeypatch):
    """Accepted-upstream replay: slice-1 brand reads and the recorded Qloo/Profile-agent exchanges, current code."""
    monkeypatch.setattr(config, "REPLAY_CASES", L2)
    pid, v = None, None
    pid = client.post("/api/projects", json={"url": "komono.com"}).json()["project_id"]
    wait_for(client, pid)
    v = wait_for_identity(client, pid)
    assert v["identity"]["decided_by"] == "landfall" and v["identity"]["chosen"]["name"] == "Komono"
    assert v["profile"]["route"] == "brand_entity" and v["profile"]["display_tags"]

    pid = client.post("/api/projects", json={"url": "gotbag.com"}).json()["project_id"]
    wait_for(client, pid)
    v = wait_for_identity(client, pid)
    assert v["identity"]["status"] == "needs_founder" and len(v["identity"]["candidates"]) == 5
    client.post(f"/api/projects/{pid}/identity", json={"base_version": 1, "entity_id": "none"})
    while (p := client.get(f"/api/projects/{pid}").json()["profile"]) is None or p["proposing"] or not p["version"]:
        pass
    trace = json.loads((L2[1] / "trace.json").read_text())
    live_v1 = [s["name"] for s in trace["record_history"]["profile"][0]["signals"]]
    assert [s["name"] for s in p["signals"]] == live_v1 and not config.LEDGER_PATH.exists()


def test_a_worker_that_outlives_its_test_reaches_no_provider_and_no_real_ledger(monkeypatch):
    """Review slice 2, blocker 1: a worker ignoring cancellation survives teardown, the test's patches are undone,
    then it is released into a cassette that allows live identity dispatch."""
    import threading

    import conftest
    real = conftest.REAL_LEDGER
    before = real.stat().st_mtime_ns if real.exists() else None
    reached, release = threading.Event(), threading.Event()
    monkeypatch.setattr(config, "REPLAY_CASES", [SYNTHETIC])
    monkeypatch.setattr(config, "LIVE_STAGES", ("identity",))
    monkeypatch.setattr(identity, "_names", lambda run, url: (reached.set(), release.wait(20)) and {
        "site_name": "Review sentinel", "name_source": "address", "query2": None, "title": ""})
    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", "prj_late", "s", "https://review.example/", "live", None, None, db.now())
    rid = runner.start("prj_late", "identity", "attempt-1")
    assert reached.wait(5)
    with pytest.raises(RuntimeError, match="still active at teardown"):
        conftest.drain_runs(timeout=0.3)  # the worker ignores cancellation: teardown refuses to pretend it ended
    monkeypatch.undo()  # what pytest does next: real httpx.get, real sleep, outer config restored
    release.set()
    while db.q("SELECT status FROM runs WHERE id=?", rid)[0]["status"] in ("queued", "running"):
        __import__("time").sleep(0.05)
    assert config.LEDGER_PATH != real  # the process guard, not the test's patch
    assert (real.stat().st_mtime_ns if real.exists() else None) == before
    # The cancel set at teardown stops the released worker before its Qloo step: no request, no record.
    assert db.q("SELECT status FROM runs WHERE id=?", rid)[0]["status"] == "cancelled"
    assert db.latest("identity", "prj_late") is None and not db.q("SELECT 1 FROM usage WHERE project_id='prj_late'")
    # And a worker already past that check has no route out: the real adapter, unpatched, is refused at the socket.
    monkeypatch.setattr(qloo.time, "sleep", lambda s: None)
    assert qloo._live("/search", {"query": "x"})["status"] == 0
    assert (real.stat().st_mtime_ns if real.exists() else None) == before
    with pytest.raises(ConnectionRefusedError):
        __import__("socket").create_connection(("93.184.216.34", 443), timeout=1)


@pytest.mark.parametrize("name, bad", [("qloo_tag_search", {}), ("no_such_tool", {"x": 1}),
                                       ("ProposedProfile", {"signals": "invalid"})])
def test_invalid_tool_and_output_loops_stop_at_the_model_call_bound(name, bad):
    """Review slice 2, blocker 2 (SYNTHETIC Nova): replies the tool bodies never see still count."""
    from landfall.cassette import Cassette

    class Invalid(Cassette):
        def __init__(self):
            super().__init__("replay")
            self.turns = 0

        def lookup(self, key):
            self.turns += 1
            body = {"output": {"message": {"role": "assistant", "content": [{"toolUse": {
                "toolUseId": f"t{self.turns}", "name": name, "input": bad}}]}},
                "stopReason": "tool_use", "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}}
            return {"provider": "bedrock", "op": "Converse", "request": {},
                    "response": {"status": 200, "headers": {"content-type": "application/json"}, "body": json.dumps(body)}}

    brief, facts = _brief_with_facts("prj_bad")
    cas = Invalid()
    with pytest.raises(RuntimeError, match="model calls without a valid profile"):
        identity._propose(runner.Run("run_bad", "prj_bad", "profile", {}, cas), brief, facts)
    assert cas.turns == identity.MAX_MODEL_CALLS


WEB_BUILT = (config.ROOT / "web" / "dist" / "index.html").exists()


@pytest.mark.skipif(not WEB_BUILT, reason="frontend not built (make web)")
def test_browser_neither_after_a_match_shows_profile_failure_and_retry_recovers(mixed, monkeypatch):
    """Review slice 2, blocker 3. SYNTHETIC Qloo at the HTTP boundary; the profile run has no recording, so it fails."""
    import re
    import socket
    import threading
    import time

    import uvicorn
    from playwright.sync_api import sync_playwright

    from landfall.app import app
    mixed.status, mixed.search = 200, [syn_brand("Tidewell", 1)]
    mixed.tags = [SYN_TAG, {"id": "urn:tag:lifestyle:qloo:tea", "name": "Tea culture", "type": "urn:tag:lifestyle:qloo"}]
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/")
            page.get_by_label("Your brand's website").fill("http://tidewell.example/")
            page.keyboard.press("Enter")
            page.get_by_text("Matched automatically").wait_for(timeout=30000)
            pid = page.url.rsplit("/", 1)[-1]
            page.get_by_role("button", name="Not you?").click()
            dlg = page.get_by_role("dialog", name="QLOO IDENTITY")
            dlg.get_by_role("radio", name=re.compile("Neither")).check()
            dlg.get_by_role("button", name="Continue without a match").click()
            prof = page.locator(".profile")
            prof.get_by_text("We couldn't propose a profile").wait_for(timeout=30000)
            page.reload()
            prof.get_by_text("We couldn't propose a profile").wait_for(timeout=15000)
            assert prof.get_by_role("button", name="Retry").count() == 1
            assert "No usable Qloo tags were found" not in prof.inner_text()
            assert "No profile yet: the proposal failed" in page.locator("section", has_text="Discovery route").first.inner_text()
            assert "What Qloo associates with you" not in page.locator("main").inner_text()  # the old route is gone

            prof.get_by_label("Add a signal").fill("tea")  # manual search stays usable during the failure
            prof.get_by_role("button", name="Search Qloo").click()
            prof.locator(".found li", has_text="Tea culture").wait_for(timeout=10000)

            monkeypatch.setattr(identity, "_propose", lambda run, brief, facts: {"searches": {"tags": 1, "brands": 0},
                "signals": [{"qloo_id": SYN_TAG["id"], "kind": "tag", "name": "Running", "basis_facts": [1],
                             "reasoning": "SYNTHETIC"}]})
            prof.get_by_role("button", name="Retry").click()
            prof.get_by_text("QLOO · CATEGORY").wait_for(timeout=30000)
            prof.get_by_label("Add a signal").fill("tea")
            prof.get_by_role("button", name="Search Qloo").click()
            prof.locator(".found li", has_text="Tea culture").get_by_role("button", name="Add").click()
            prof.get_by_role("button", name="Confirm profile").click()
            prof.get_by_text("Profile confirmed").wait_for(timeout=15000)
            browser.close()
        assert brand_runs(pid) == 1  # recovery never re-read the site
        names = [s["name"] for s in db.latest("profile", pid)["signals"]]
        assert names == ["Running", "Tea culture"] and db.latest("profile", pid)["confirmed"]
    finally:
        server.should_exit = True


def test_concurrent_founder_searches_cannot_pass_the_hourly_limit(client, mixed):
    """Review slice 2, blocker 4: from 19 answered searches, 25 concurrent requests admit exactly one, and the 24
    rejections happen before any provider work."""
    import threading

    from fastapi import HTTPException

    from landfall import app as app_module
    mixed.status, mixed.search = 200, []
    pid, _ = _start(client)
    for _ in range(19):
        db.x("INSERT INTO usage(project_id, at, provider, stage, replayed, origin) VALUES(?,?,?,?,?,?)",
             pid, db.now(), "qloo", "profile_edit", 0, "live")
    gate, admitted, rejected = threading.Event(), [], []

    def search():
        try:
            with app_module.founder_search_slot(pid):
                admitted.append(1)
                gate.wait(5)  # provider work in flight while the other 24 arrive
        except HTTPException as e:
            rejected.append(e.status_code)

    threads = [threading.Thread(target=search) for _ in range(25)]
    for t in threads:
        t.start()
    while len(admitted) + len(rejected) < 25:
        pass
    gate.set()
    for t in threads:
        t.join()
    assert len(admitted) == 1 and rejected == [429] * 24
    assert app_module._searching[pid] == 0  # the slot is released afterwards


def test_the_name_lookup_uses_the_document_title_not_icon_titles():
    """Live 24 Oct: stubbornsoda.com's titles joined read 'Stubborn SodaStubborn Soda', so the title could not
    supply the name. The joined `title` stays as the Brand reader has always seen it."""
    from landfall import fetch
    html = ("<html><head><title>Stubborn Soda</title></head><body><svg><title>Stubborn Soda</title></svg>"
            "<p>" + "Craft soda. " * 30 + "</p></body></html>")
    ex = fetch.extract({"url": "https://www.stubbornsoda.com/", "body": html})
    assert ex["title"] == "Stubborn SodaStubborn Soda" and ex["first_title"] == "Stubborn Soda"
    n = identity.derive_names("https://www.stubbornsoda.com/", None, ex["first_title"])
    assert (n["site_name"], n["name_source"]) == ("Stubborn Soda", "title_matches_address")
