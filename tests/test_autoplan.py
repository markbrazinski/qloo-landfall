"""Owner, 27 Oct 2026: picking a city builds the plan (landfall/autoplan.py). Offline: research, the specialists and the
strategist are stand-ins that record the order they ran in; the run's own sequencing and guards are what's tested."""
import types

import pytest

from landfall import autoplan, config, db, delivery, mockups, plans, research, runner, specialist, strategist
from landfall.research import Refused

PID, CID = "prj_auto", "brisbane"


class FakeRun:
    project_id, id = PID, "run_auto"

    def __init__(self, **params):
        self.events, self.steps, self.params = [], {}, {"city_id": CID, **params}

    def check(self):
        pass

    def step(self, key, fn):
        if key not in self.steps:
            self.steps[key] = fn()
        return self.steps[key]

    def emit(self, kind, payload):
        self.events.append(payload["phase"])


def world(monkeypatch, plan_methods=None, proposal=True, beachhead=CID):
    monkeypatch.setattr(plans, "beachhead", lambda pid: beachhead)
    monkeypatch.setattr(autoplan, "_running", lambda *a: [])
    monkeypatch.setattr(autoplan, "_turn", lambda *a: None)
    calls, state = [], {"researched": False, "dossiers": set(), "drafts": set(), "plan": None}
    if plan_methods is not None:
        state["plan"] = {"version": 1, "methods": plan_methods}
    monkeypatch.setattr(research, "latest_research", lambda pid: {CID: {}} if state["researched"] else {})

    def start(pid, ids, ctx, refresh):
        calls.append("research"); state["researched"] = True
        return {"run_id": None}
    monkeypatch.setattr(research, "start", start)
    monkeypatch.setattr(research, "city_row", lambda pid, c: {"status": "complete"} if state["researched"] else None)
    monkeypatch.setattr(specialist, "spec_row", lambda *a: None)

    def investigate(pid, cid, m, prefs):
        calls.append(f"investigate:{m}"); state["dossiers"].add(m)
        return {"run_id": None}
    monkeypatch.setattr(specialist, "investigate", investigate)

    def develop(pid, did, base, why):
        calls.append(f"develop:{did.split('_')[-1]}"); state["drafts"].add(did.split("_")[-1])
        return {"run_id": None}
    monkeypatch.setattr(specialist, "develop", develop)

    def get_record(rid, v=None):
        if rid.startswith("dos_"):
            m = rid.split("_")[-1]
            return {"id": rid, "version": 1, "status": "complete", "method": m} if m in state["dossiers"] else None
        if rid.startswith("drf_"):
            return {"source": {"dossier_version": 1}} if rid.split("_")[-1] in state["drafts"] else None
        if rid == plans.plan_id(PID, CID):
            return state["plan"]
        if rid == strategist.proposal_id(PID, CID):
            return {"version": 1, "options": [{"id": "A"}]} if proposal and "propose" in calls else None
        return None
    monkeypatch.setattr(db, "get_record", get_record)
    monkeypatch.setattr(strategist, "evidence", lambda pid, cid: list(state["dossiers"]))

    def save(pid, cid, base, methods):
        calls.append("plan"); state["plan"] = {"version": 1, "methods": []}
        return {"version": 1}
    monkeypatch.setattr(plans, "save", save)

    def prepare(pid, cid):
        calls.append("prepared"); state["plan"] = {"version": 1, "methods": []}
        return {"version": 1}
    monkeypatch.setattr(plans, "prepare", prepare)
    monkeypatch.setattr(delivery, "prewarm_launch", lambda pid, cid: calls.append(f"launch:{cid}"), raising=False)
    monkeypatch.setattr(delivery, "draft_ads", lambda run, pid, cid: calls.append(f"ads:{cid}"))
    monkeypatch.setattr(mockups, "prewarm", lambda pid, cid: calls.append(f"scenes:{cid}"))
    monkeypatch.setattr(strategist, "start", lambda pid, cid: calls.append("propose") or {"run_id": None})

    def adopt(pid, cid, v, oid, base):
        calls.append(f"adopt:{oid}")
        state["plan"] = {"version": 2, "methods": [{"method": "community", "role": "MAIN"}, {"method": "digital", "role": "SUPPORTS"},
                                                   {"method": "retail", "role": "OPTIONAL"}]}
        return {"version": 2}
    monkeypatch.setattr(strategist, "adopt", adopt)
    return calls


def test_picking_a_city_researches_investigates_proposes_adopts_and_drafts(monkeypatch):
    calls = world(monkeypatch)
    run = FakeRun()
    assert autoplan.auto_plan_run(run) is None  # completed
    assert calls == ["research", *[f"investigate:{m}" for m in research.METHODS], "plan", "propose", "adopt:A",
                     "develop:community", "develop:digital", f"ads:{CID}", f"launch:{CID}"]  # only the included methods are drafted
    assert run.events == ["research", "investigate", "propose", "develop", "develop", "ready"]  # then the Google ads are written


def test_a_plan_you_already_shaped_is_never_overwritten(monkeypatch):
    calls = world(monkeypatch, plan_methods=[{"method": "retail", "role": "MAIN"}])
    autoplan.auto_plan_run(FakeRun())
    assert "propose" not in calls and not any(c.startswith("adopt") for c in calls)
    assert "develop:retail" in calls


def test_no_proposal_ends_partial_with_a_way_forward(monkeypatch):
    world(monkeypatch, proposal=False)
    out = autoplan.auto_plan_run(FakeRun())
    assert out[0] == "partial" and "Choose a main test yourself" in out[1]


# ---- owner, 28 Oct 2026: the top three cities' plans build in parallel before the founder chooses ----
def test_prewarm_builds_only_the_top_city_ahead(monkeypatch):
    """Owner, 28 Oct 2026: only city #1 is built in full ahead of the pick; #2 and #3 build when picked."""
    monkeypatch.setattr(config, "AUTO_PLAN", True)
    started = []
    monkeypatch.setattr(runner, "start", lambda pid, kind, key, params: started.append((kind, key, params)) or f"run_{key}")
    assert autoplan.prewarm(PID, ["brisbane", "toronto", "austin", "denver"]) == ["run_brisbane"]
    assert [(k, key) for k, key, _ in started] == [("auto_plan", "brisbane")]  # the city's own plan run, so a pick joins it
    assert started[0][2]["prepared"] and started[0][2]["top"]


def test_a_prewarmed_city_gets_a_prepared_plan_never_the_beachhead(monkeypatch):
    calls = world(monkeypatch, beachhead=None)
    assert autoplan.auto_plan_run(FakeRun(prepared=True, top=True)) is None
    assert "plan" not in calls and "prepared" in calls  # plans.save (beachhead only) is never called
    assert calls[-2:] == [f"launch:{CID}", f"scenes:{CID}"]  # the launch kit and the top city's scenes start when ready


def test_a_build_joins_research_another_build_started(monkeypatch):
    """Live, 28 Oct 2026: the three builds race to start one research run; a loser finds the city's row queued before
    the winner's run row exists, and must wait for that row instead of giving up."""
    calls = world(monkeypatch, beachhead=None)
    seen = []

    def refused(*a):
        raise Refused(409, "Already researched")
    monkeypatch.setattr(research, "start", refused)

    def city_row(pid, c):
        seen.append(c)
        return {"status": "queued" if len(seen) < 3 else "complete", "created_at": db.now()}
    monkeypatch.setattr(research, "city_row", city_row)
    monkeypatch.setattr(research, "latest_research", lambda pid: {CID: {}} if len(seen) >= 3 else {})
    assert autoplan.auto_plan_run(FakeRun(prepared=True)) is None
    assert "research" not in calls and calls[0].startswith("investigate:") and "prepared" in calls


def test_prepare_writes_the_plan_without_choosing_the_city(monkeypatch):
    pid = "prj_prep"
    db.x("INSERT INTO projects(id, session, url, mode, created_at) VALUES(?,?,?,?,?)", pid, "s", "https://x.example/", "live", db.now())
    db.put_record("founder_input", pid, {"ceiling_usd": None})
    sl = {"cities": [{"city_id": CID, "included": True}], "beachhead": None}
    db.put_record("shortlist", pid, sl, rid=f"sl_{pid}")
    assert plans.prepare(pid, CID) == {"version": 1}
    p = db.get_record(plans.plan_id(pid, CID))
    assert p["prepared_in_advance"] and p["methods"] == [] and not p["signed_off"]
    assert plans.beachhead(pid) is None and plans.prepare(pid, CID) == {"version": 1}  # kept as it is
    with pytest.raises(Refused):  # the founder's own edits still need the city chosen
        plans.save(pid, CID, 1, [{"method": "retail", "role": "MAIN"}])
    # picking the city: the prepared plan is simply the current plan, and the pick joins the city's build run
    db.put_record("shortlist", pid, {**sl, "beachhead": {"city_id": CID}}, rid=f"sl_{pid}")
    db.x("INSERT INTO runs(id, project_id, kind, input_key, status, detail, started_at) VALUES(?,?,?,?,?,?,?)",
         "run_pre", pid, "auto_plan", CID, "running", "{}", db.now())
    assert autoplan.start(pid, CID, asked=True) == "run_pre"
    assert plans.save(pid, CID, 1, [{"method": "retail", "role": "MAIN"}]) == {"version": 2}
    assert db.get_record(plans.plan_id(pid, CID))["prepared_in_advance"]
    db.x("UPDATE runs SET status='completed' WHERE id='run_pre'")  # a stand-in row: no thread runs it


def test_prepared_builds_take_turns_in_recommendation_order_but_the_chosen_city_never_waits(monkeypatch):
    pid = "prj_turn"
    db.x("INSERT INTO runs(id, project_id, kind, input_key, status, detail, started_at) VALUES(?,?,?,?,?,?,?)",
         "run_first", pid, "auto_plan", "toronto", "running", "{}", db.now())
    bh = {"city": None}
    monkeypatch.setattr(plans, "beachhead", lambda p: bh["city"])

    class R(FakeRun):
        project_id = pid
        polls = 0

        def check(self):
            R.polls += 1
            if R.polls == 3:
                bh["city"] = CID  # the founder picks this city while it waits
    run = R(prepared=True, together=["toronto", CID])
    autoplan._turn(run, pid, CID)
    assert R.polls >= 3
    autoplan._turn(R(prepared=True, together=[CID, "toronto"]), pid, CID)  # first in order: no wait
    db.x("UPDATE runs SET status='completed'")


def test_the_research_the_founder_watches_shows_progress_and_findings_never_failures():
    """Owner, 28 Oct 2026: failed reads, searches and checks stay in Activity; the stream and the build feed drop them."""
    ev = [{"kind": "CHECKING", "text": "Read artshed.com.au/"}, {"kind": "UNAVAILABLE", "text": "Couldn't read humblelion.ca/ (ConnectError)"},
          {"kind": "UNAVAILABLE", "text": "Presence check for Cork & Co: check failed"},
          {"kind": "CANDIDATE", "text": "Web search failed: HTTP 403"}, {"kind": "CHECKING", "text": "Re-checked Goods: check failed"},
          {"kind": "CANDIDATE", "text": "Qloo retail search: 6 places"},
          {"kind": "SUPPORTED", "text": "Le Fix: a shop whose first store failed in 2019 and reopened"}]
    assert [e["text"] for e in research.plain_stream(ev)] == [
        "Read artshed.com.au/", "Qloo retail search: 6 places", "Le Fix: a shop whose first store failed in 2019 and reopened"]


def test_no_return_is_assumed_without_the_founders_units_and_price():
    """Owner, 28 Oct 2026: no default 30% of the planned cost; the founder's units × price still give their return."""
    planned, roles = {"minor": 1000000, "ccy": "USD"}, {"popup": "MAIN"}
    assert plans.assumed_return({}, planned, [], roles, None) is None
    assert plans.assumed_return({"return_estimate": {"retail_price": {"minor": 2000, "ccy": "USD"}}}, planned, [], roles, None) is None
    a = plans.assumed_return({"return_estimate": {"units": 100, "retail_price": {"minor": 2000, "ccy": "USD"},
                                                  "by": {"retail_price": "YOU_SAID", "units": "YOU_SAID"}}}, planned, [], roles, None)
    assert a["amount"] == {"minor": 100000, "ccy": "USD"} and "you said" in a["note"]
