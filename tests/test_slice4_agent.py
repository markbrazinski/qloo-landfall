"""Slice 4: the real city agent (Strands, nova.make_model, admission, transcript checkpoints, structured submission and the
city_check@1 checker) against SYNTHETIC Converse replies, served in order through the cassette's replay path. Proves the
wiring offline; it says nothing about the model's quality (the live cases do)."""
import json

import pytest
from test_markets import idle, markets_env, scanned  # noqa: F401
from test_slice4 import KC, STAGES, Web, city, toronto_pages

from landfall import config, db, fetch, research, runner
from landfall.cassette import Cassette

SUB = {"learned": "Kettle & Crane keeps a makers shelf for outside brands.", "learned_claims": [1], "facts_used": ["F1"],
       "leads": [{"ref": "L1", "name": "Kettle & Crane", "kind": "shop"}],
       "claims": [{"subject": "L1", "method": "retail", "text": "Stocks outside brands", "source": research.source_code(KC),
                   "quote": "keeps a makers shelf that stocks outside brands from small labels"}],
       "approaches": [{"method": m, "status": "DEVELOP" if m == "retail" else "NEEDS_RESEARCH", "name": f"{m} idea",
                       "concept_name": "Shelf trial" if m == "retail" else None, "concept_line": "A shelf trial." if m == "retail" else None,
                       "lead_refs": ["L1"] if m == "retail" else [], "claim_refs": [1] if m == "retail" else [],
                       "reason": "Claim 1." if m == "retail" else "no evidence found in this research"}
                      for m in research.METHODS]}


def reply(*tools, text=None):
    content = ([{"text": text}] if text else []) + [{"toolUse": {"toolUseId": f"t{n}{name}", "name": name, "input": inp}}
                                                    for n, (name, inp) in enumerate(tools)]
    return {"output": {"message": {"role": "assistant", "content": content}}, "stopReason": "tool_use" if tools else "end_turn",
            "usage": {"inputTokens": 1200, "outputTokens": 150, "totalTokens": 1350}, "metrics": {"latencyMs": 5}}


class Scripted(Cassette):
    """Replays every recorded exchange as usual, and answers Converse requests from a script, in order."""
    script: list = []
    bodies: list = []

    def lookup(self, key, stage=None):
        if (ex := self.recorded(key)) is not None or not Scripted.script:
            return ex
        return {"provider": "bedrock", "op": "Converse", "request": {}, "stage": "",
                "response": {"status": 200, "headers": {"content-type": "application/json"}, "body": json.dumps(Scripted.script.pop(0))}}


@pytest.fixture
def agent_env(markets_env, monkeypatch):
    web = Web()
    toronto_pages(web)
    monkeypatch.setattr(config, "LIVE_STAGES", STAGES)
    monkeypatch.setattr(fetch, "_live_get", web)
    monkeypatch.setattr(fetch, "_live_render", lambda url: {"url": url, "status": 0, "error": "not rendered"})
    research._robots.clear()
    real = runner.cassette_for

    def cassette_for(pid):
        c = real(pid)
        s = Scripted(c.mode, c.record_dir, live_stages=tuple(c.live_stages))
        s._index, s._origin = c._index, c._origin
        return s
    monkeypatch.setattr(runner, "cassette_for", cassette_for)
    Scripted.script = []
    return web


def prose_ok():
    return reply(("ProseVerdicts", {"verdicts": [{"id": f"s{n}", "kind": "PROPOSAL"} for n in range(1, 41)]}))


def verdicts(*ids):
    return reply(("CityVerdicts", {"verdicts": [{"claim_id": i, "verdict": "SUPPORTS", "reason": "SYNTHETIC", "shows_way_in": True} for i in ids]}))


def test_the_real_agent_submits_and_is_checked(client, agent_env):
    pid, m = scanned(client, agent_env)
    Scripted.script = [reply(("qloo_places", {"method": "retail"}), ("fetch_page", {"url": KC, "method": "retail"})),
                       reply(("CityResearchSubmission", SUB)), verdicts("c1"), prose_ok()]
    assert client.post(f"/api/projects/{pid}/research", json={"city_ids": ["toronto"]}).status_code == 200
    idle(pid)
    v = client.get(f"/api/projects/{pid}/opportunities").json()
    t = city(v, "toronto")
    assert t["status"] == "complete", t["error"]
    assert t["matrix"]["retail"]["status"] == "READY_TO_COMPARE" and t["counters"]["model"] == 2 and t["counters"]["check"] == 1
    row = research.city_row(pid, "toronto", 1)
    msgs = json.loads(row["transcript"])
    assert any("toolResult" in b for m_ in msgs for b in m_["content"])  # the turn's tool results were checkpointed


def test_a_stopped_agent_resumes_from_its_transcript_without_repeating_tools(client, agent_env, monkeypatch):
    pid, m = scanned(client, agent_env)
    calls = []
    orig = research.make_tools

    def tools(ctx):
        ts = orig(ctx)
        fp = next(t for t in ts if t.tool_name == "fetch_page")
        inner = fp._tool_func

        def stopping(url, method):
            out = inner(url=url, method=method)
            calls.append(url)
            if len(calls) == 1:
                runner.cancel(ctx.run.id)
            return out
        fp._tool_func = stopping
        return ts
    monkeypatch.setattr(research, "make_tools", tools)
    Scripted.script = [reply(("fetch_page", {"url": KC, "method": "retail"}))]
    client.post(f"/api/projects/{pid}/research", json={"city_ids": ["toronto"]})
    idle(pid)
    assert research.city_row(pid, "toronto", 1)["status"] == "stopped"
    loads = agent_env.loads().count(KC)
    Scripted.script = [reply(("CityResearchSubmission", SUB)), verdicts("c1"), prose_ok()]
    client.post(f"/api/projects/{pid}/research/continue", json={"city_ids": ["toronto"]})
    idle(pid)
    t = city(client.get(f"/api/projects/{pid}/opportunities").json(), "toronto")
    assert t["status"] == "complete", t["error"]
    assert agent_env.loads().count(KC) == loads and t["counters"]["model"] == 2  # rebuilt from the transcript


def test_a_model_call_cap_gets_one_forced_submission(client, agent_env, monkeypatch):
    monkeypatch.setitem(research.CAPS, "model", 3)
    pid, m = scanned(client, agent_env)
    Scripted.script = [reply(("qloo_places", {"method": "retail"})), reply(("fetch_page", {"url": KC, "method": "retail"})),
                       reply(("CityResearchSubmission", SUB)), verdicts("c1"), prose_ok()]
    client.post(f"/api/projects/{pid}/research", json={"city_ids": ["toronto"]})
    idle(pid)
    t = city(client.get(f"/api/projects/{pid}/opportunities").json(), "toronto")
    assert t["status"] == "complete", t["error"]
    assert t["counters"]["model"] == 3  # two regular calls, the third refused before dispatch, then the forced one
    assert any("A limit was reached" in a["detail"] for a in db.events(pid, 0, "activity"))


def test_live_l4a_counterexample_a_submission_with_nulls_and_missing_fields_is_accepted(client, agent_env):
    """LIVE L4-A attempt 1 (25 Oct): `facts_used: null`, approaches without `reason` or `name` and a "pop-up" method were
    rejected until the city's call budget ran out. They now validate; the validator fills what is missing."""
    pid, m = scanned(client, agent_env)
    live_like = {**SUB, "facts_used": None, "learned_claims": ["claim 1"],
                 "approaches": [{k: v for k, v in a.items() if k != "reason"} for a in SUB["approaches"][:3]]
                 + [{"method": "pop-up", "status": "Not suitable now"}, {"method": "digital", "status": None, "name": None}]}
    Scripted.script = [reply(("fetch_page", {"url": KC, "method": "retail"})), reply(("CityResearchSubmission", live_like)),
                       verdicts("c1"), prose_ok()]
    client.post(f"/api/projects/{pid}/research", json={"city_ids": ["toronto"]})
    idle(pid)
    t = city(client.get(f"/api/projects/{pid}/opportunities").json(), "toronto")
    assert t["status"] == "complete", t["error"]
    assert set(t["matrix"]) == set(research.METHODS) and t["counters"]["model"] == 2
    assert t["matrix"]["retail"]["status"] == "READY_TO_COMPARE"
