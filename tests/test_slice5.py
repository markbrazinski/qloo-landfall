"""Slice 5, server side: the Plan's money, the launch strategist's proposal, the digital scenario and the signed plan's
money in Act (BUILD-PLAN "Slice 5" AC5-1 to AC5-15; CONTRACTS B7/B10 P5-R). SYNTHETIC pages, rates, model answers and
providers: they prove handling, never a live integration or a live forecast."""
import json
import re

import pytest
from test_markets import idle, markets_env  # noqa: F401
from test_slice4 import env, research_ready  # noqa: F401
from test_slice45_runner import spec_env  # noqa: F401
from test_slice6 import deliver_env, view  # noqa: F401
from test_slice65_plan import beachhead, dossiered, develop, expect_of, investigate, plan_env, put_plan  # noqa: F401

from landfall import config, db, delivery, fetch, money, plans, scenarios, strategist

ECB = ("<gesmes:Envelope xmlns:gesmes='http://www.gesmes.org/xml/2002-08-01' xmlns='http://www.ecb.int/vocabulary/2002-08-01/eurofxref'>"
       "<Cube><Cube time='{day}'><Cube currency='USD' rate='1.0800'/><Cube currency='CAD' rate='1.5000'/>"
       "<Cube currency='AUD' rate='1.6500'/></Cube></Cube></gesmes:Envelope>")
DIGITAL = {"concept_name": "City waitlist", "concept_line": "Invite Toronto tea drinkers to a waitlist.",
           "channel_primary": "Instagram (Meta)", "channel_alternative": "Google Search", "objective": "SIGN_UP",
           "radius_km_hypothesis": 25, "targeting": [{"element": "city + radius", "basis": "PLATFORM_OPTION", "platforms_ref": "meta.city_radius"}],
           "message": "Sparkling cold-brewed tea, now on a Toronto waitlist.", "primary_metric": "Waitlist sign-ups",
           "stop_rule": "Stop after two weeks if sign-ups stay flat."}


@pytest.fixture
def money_env(plan_env, monkeypatch):
    """The 6.5 Toronto case plus a SYNTHETIC ECB publication; `rates.day` moves it, `rates.down` fails the fetch."""
    class Rates:
        day, down, calls = "2026-10-26", False, 0
    rates = Rates()
    real = fetch._live_get

    def live_get(url, binary=False):
        if url == money.ECB_URL:
            rates.calls += 1
            return {"url": url, "status": 0, "error": "ConnectError", "hops": []} if rates.down else \
                {"url": url, "status": 200, "body": ECB.format(day=rates.day), "hops": []}
        return real(url, binary)
    monkeypatch.setattr(fetch, "_live_get", live_get)
    monkeypatch.setattr(config, "LIVE_STAGES", config.LIVE_STAGES + ("fx_ecb",))
    plan_env.rates = rates
    return plan_env


def planned(client, env, digital=False):
    """Retail investigated and developed (and digital, when asked), Toronto the beachhead, retail the Main test."""
    pid = dossiered(client, env)
    develop(client, pid)
    if digital:
        env.spec_script = lambda T: DIGITAL
        investigate(client, pid, "digital")
        develop(client, pid, "digital")
    beachhead(client, pid)
    assert put_plan(client, pid, {"retail": "MAIN", **({"digital": "SUPPORTS"} if digital else {})}).status_code == 200
    return pid


def pv(client, pid):
    return client.get(f"/api/projects/{pid}/plans/toronto").json()


def line(client, pid, lid, amount=None, ccy="USD", **extra):
    v = pv(client, pid)["plan"]["version"]
    body = {"base_version": v, **extra}
    if amount is not None or "used_by" not in extra:
        body["amount"] = amount and {"amount": amount, "ccy": ccy}
    return client.patch(f"/api/projects/{pid}/plans/toronto/cost-lines/{lid}", json=body)


def ceiling(client, pid, usd):
    fi = db.latest("founder_input", pid)
    r = client.patch(f"/api/projects/{pid}/intent", json={"base_version": fi["version"], "ceiling_usd": usd})
    assert r.status_code == 200, r.text
    return r.json()


def usage_rows(pid):
    return db.q("SELECT COUNT(*) n FROM usage WHERE project_id=?", pid)[0]["n"]


# ---- AC5-1 [T1], AC5-2, AC5-3: lines, totals and fit ----
def test_every_item_is_a_line_unknowns_are_never_summed_and_fit_follows_the_truth_table(client, money_env):
    pid = planned(client, money_env)
    v = pv(client, pid)
    ids = [ln["id"] for ln in v["money"]["lines"]]
    assert ids == ["cl_retail_stock", "cl_retail_shipping", "cl_retail_other"]  # S5-1: the COSTING items, NOT_KNOWN from code
    assert {ln["basis"] for ln in v["money"]["lines"]} == {"NOT_KNOWN"} and v["money"]["text"] == "3 not known"  # never $0
    assert v["money"]["fit"] == "AFFORDABILITY_UNRESOLVED" and v["money"]["remaining"] is None

    n = usage_rows(pid)
    assert line(client, pid, "cl_retail_stock", "4000").status_code == 200
    assert line(client, pid, "cl_retail_shipping", "500", flow="expense").status_code == 200
    r = ceiling(client, pid, 10_000)
    assert r["notice"].endswith("1 fit recalculated; research not re-run.")
    m = pv(client, pid)["money"]
    assert m["text"] == "US$4,500 + 1 not known" and m["fit"] == "AFFORDABILITY_UNRESOLVED"  # unknown, cash below the ceiling
    assert m["inventory_known"] == money.usd(400000) and m["expense_known"] == money.usd(50000)  # stock is inventory
    ceiling(client, pid, 4_000)
    assert pv(client, pid)["money"]["fit"] == "OVER_CEILING"  # over takes priority over the unknown line
    assert line(client, pid, "cl_retail_other", "1000", flow="refundable_deposit").status_code == 200
    ceiling(client, pid, 5_500)
    m = pv(client, pid)["money"]
    assert m["fit"] == "FITS_KNOWN_COSTS" and m["remaining"] == money.usd(0)  # equality fits
    assert m["deposits_known"] == money.usd(100000) and m["expense_known"] == money.usd(50000)  # a deposit is cash, not expense
    fi = db.latest("founder_input", pid)
    db.put_record("founder_input", pid, {**plans._fields(fi), "ceiling_usd": None, "ceiling_by": None}, rid=fi["id"])
    assert pv(client, pid)["money"]["fit"] == "NOT_ASSESSED"
    assert usage_rows(pid) == n  # every recalculation: 0 model calls


def test_a_shared_line_exists_once_and_stays_while_another_method_uses_it(client, money_env):
    """AC5-3 [R2]: US$100 → US$200 changes every total once; removing one user keeps the line."""
    pid = planned(client, money_env, digital=True)
    assert line(client, pid, "cl_retail_stock", "100").status_code == 200
    assert line(client, pid, "cl_retail_stock", used_by=["retail", "digital"]).status_code == 200
    v = pv(client, pid)
    assert v["money"]["cash_out_known"] == money.usd(10000)
    assert line(client, pid, "cl_retail_stock", "200").status_code == 200
    v = pv(client, pid)
    shared = [ln for ln in v["money"]["lines"] if ln["shared"]]
    assert [ln["id"] for ln in shared] == ["cl_retail_stock"] and v["money"]["cash_out_known"] == money.usd(20000)
    by = {m["method"]: m["money"] for m in v["methods"] if m["money"]}
    assert by["retail"]["shared"] == by["digital"]["shared"] == 1 and by["digital"]["subtotal"] == money.usd(20000)
    assert put_plan(client, pid, {"retail": "MAIN", "digital": "NOT_NOW"}).status_code == 200
    v = pv(client, pid)
    assert v["money"]["cash_out_known"] == money.usd(20000)  # retail still uses it
    assert not any(ln["counts"] for ln in v["money"]["lines"] if ln["used_by"] == ["digital"])  # digital's own lines leave
    assert line(client, pid, "cl_retail_stock", used_by=["popup"]).status_code == 422  # only methods in the plan


def test_fx_pins_a_dated_publication_and_a_missing_rate_is_never_zero(client, money_env):
    """AC5-2a [T2]: CA$100 at USD 1.08 / CAD 1.50 per EUR is US$72.00; a failed fetch leaves the line visible and the
    fit unresolved; Retry pins rates; a signed snapshot keeps its rates when a newer publication arrives."""
    pid = planned(client, money_env)
    money_env.rates.down = True
    assert line(client, pid, "cl_retail_stock", "100", "CAD").status_code == 200
    m = pv(client, pid)["money"]
    stock = next(ln for ln in m["lines"] if ln["id"] == "cl_retail_stock")
    assert stock["unconverted"] and stock["text"] == "CA$100" and m["unconverted"] == ["cl_retail_stock"]
    assert m["cash_out_known"] == money.usd(0) and "1 not converted" in m["text"] and m["fit"] == "AFFORDABILITY_UNRESOLVED"
    money_env.rates.down = False
    v = pv(client, pid)["plan"]["version"]
    r = client.post(f"/api/projects/{pid}/plans/toronto/fx/retry", json={"base_version": v}).json()
    assert r["version"] == v + 1 and "2026-10-26" in r["notice"]
    m = pv(client, pid)["money"]
    assert next(ln for ln in m["lines"] if ln["id"] == "cl_retail_stock")["usd"] == money.usd(7200)
    assert m["fx"]["text"] == "converted at ECB rates of 2026-10-26"
    for ln in ("cl_retail_shipping", "cl_retail_other"):
        assert line(client, pid, ln, "10").status_code == 200
    r = client.post(f"/api/projects/{pid}/plans/toronto/sign-off", json={"base_version": pv(client, pid)["plan"]["version"],
                                                                          "expect": expect_of(client, pid)})
    assert r.status_code == 200, r.text
    signed = pv(client, pid)["signed"]["money"]
    money_env.rates.day = "2026-10-27"
    calls = money_env.rates.calls
    plans.ensure_fx(pid, db.get_record(plans.plan_id(pid, "toronto"))["cost_lines"], retry=True)  # a newer publication
    assert money_env.rates.calls == calls + 1 and plans.latest_fx(pid)["rate_date"] == "2026-10-27"
    assert line(client, pid, "cl_retail_shipping", "20").status_code == 200  # the next edit uses it; the signed one doesn't
    v = pv(client, pid)
    assert v["money"]["fx"]["rate_date"] == "2026-10-27" and v["signed"]["money"] == signed
    assert v["signed"]["money"]["fx"]["rate_date"] == "2026-10-26"


def test_the_return_estimate_is_gross_receipts_from_complete_inputs_only(client, money_env):
    """AC5-11 [T3]."""
    pid = planned(client, money_env)
    m = pv(client, pid)["money"]
    assert m["gross_receipts"] is None
    v = pv(client, pid)["plan"]["version"]
    r = client.patch(f"/api/projects/{pid}/plans/toronto/return", json={"base_version": v, "units": 200,
                                                                        "wholesale_price": {"amount": "3", "ccy": "CAD"}})
    assert r.status_code == 200
    assert pv(client, pid)["money"]["gross_receipts"] is None  # sell-through still missing → the success measure only
    r = client.patch(f"/api/projects/{pid}/plans/toronto/return", json={"base_version": v + 1, "sell_through": {"low": 0.25, "high": 0.5}})
    g = pv(client, pid)["money"]["gross_receipts"]
    assert g["price"] == "wholesale" and g["low"] == {"minor": 15000, "ccy": "CAD"} and g["high"] == {"minor": 30000, "ccy": "CAD"}
    assert g["label"] == "gross receipts if it sells through, not profit"
    assert client.patch(f"/api/projects/{pid}/plans/toronto/return", json={"base_version": v + 2, "sell_through": {"low": 0.9, "high": 0.2}}).status_code == 422


# ---- the strategist (AC5-4 to AC5-7, AC5-15) ----
EVIDENCE = [{"id": "E1", "role": "CLAIM", "text": "Kettle & Crane: stocks small Ontario makers (\"teas from Ontario makers\")", "claim_id": "clm_kc"},
            {"id": "E2", "role": "CLAIM", "text": "Kettle & Crane: 12 Queen St W, Toronto (\"12 Queen St W\")", "claim_id": "clm_addr"},
            {"id": "E3", "role": "PRODUCT", "text": "Hibiscus Berry Tea: CA$40 a case", "claim_id": "pro_tea"},
            {"id": "E4", "role": "QLOO", "text": "Audience: tea lovers", "claim_id": None}]


def answer(b_roles=None, **over):
    a = {"methods": [{"method": "retail", "role": "MAIN", "reason": "A shop already carries makers."},
                     {"method": "digital", "role": "SUPPORTS", "reason": "A waitlist measures interest."}],
         "cost_lines": [{"item": "retail:STOCK", "amount": "400", "currency": "CAD", "basis": "ASSUMED",
                         "basis_note": "4 cases × CA$100 a case, once, assumed"},
                        {"item": "retail:SHIPPING", "amount": "50", "currency": "CAD", "basis": "LISTED_RATE", "cites": ["E1"]},
                        {"item": "digital:MEDIA_SPEND", "amount": "300", "currency": "CAD", "basis": "ESTIMATE"}],  # no note: refused
         "why": "Retail tests the shelf; digital measures demand around it.", "trade_off": "Needs the shop to agree."}
    b = {"methods": b_roles or [{"method": "digital", "role": "MAIN", "reason": "No partner needed."},
                                {"method": "creator", "role": "SUPPORTS", "reason": "A local voice."}],
         "cost_lines": [{"item": "creator:CREATOR_FEE", "amount": "40", "currency": "CAD", "basis": "LISTED_RATE", "cites": ["E3"],
                         "basis_note": "1 post × CA$40, flat"}],
         "why": "Faster, no shop needed.", "trade_off": "Learns less about shelf sales."}
    return {"options": [a, b], "preference": ["A", "B"],
            "rationale": [{"field": "brand", "text": "Kettle & Crane stocks small Ontario makers.", "kind": "FACT", "cites": ["E1"]},
                          {"field": "city", "text": "Kettle & Crane is available for a March slot.", "kind": "FACT", "cites": ["E2"]},
                          {"field": "assumed", "text": "Four cases are enough for a month.", "kind": "ASSUMPTION"},
                          {"field": "known", "text": "Toronto is a better market than Vancouver.", "kind": "FACT", "cites": ["E1"]}], **over}


@pytest.fixture
def strat_env(money_env, monkeypatch):
    money_env.answers, money_env.listings = [answer()], []
    monkeypatch.setattr(strategist, "evidence", lambda pid, cid: EVIDENCE)

    def call(ctx, text):
        money_env.prompts.append(text)
        return money_env.answers.pop(0) if len(money_env.answers) > 1 else money_env.answers[0]
    monkeypatch.setattr(strategist, "call", call)

    def judge(ctx, items):
        money_env.listings.append(items)
        return {"verdicts": [{"id": it["id"], "kind": "SUPPORTED_FACT" if "stocks small" in it["text"] else "UNSUPPORTED_FACT"}
                             for it in items]}
    monkeypatch.setattr(strategist, "_judge", judge)
    return money_env


def propose(client, pid):
    r = client.post(f"/api/projects/{pid}/plans/toronto/propose", json={})
    assert r.status_code == 200, r.text
    idle(pid)
    return pv(client, pid)["proposal"]


def test_a_proposal_changes_nothing_until_adopted_and_its_facts_and_rates_are_checked(client, strat_env):
    """AC5-4, AC5-5 and AC5-7 [R4]."""
    pid = planned(client, strat_env, digital=True)
    before = db.get_record(plans.plan_id(pid, "toronto"))
    est = client.post(f"/api/projects/{pid}/plans/toronto/propose", json={"dry_run": True}).json()
    assert est["run_id"] is None and est["est"]["max_usd"] == 0.40  # the cost is shown first
    p = propose(client, pid)
    assert db.get_record(plans.plan_id(pid, "toronto"))["version"] == before["version"]  # nothing changed
    assert [m["role"]["by"] for m in pv(client, pid)["methods"] if m["role"]] == ["founder", "founder"]
    r = p["rationale"]
    assert r["brand"] == [{"text": "Kettle & Crane stocks small Ontario makers.", "kind": "FACT", "cites": ["E1"]}]
    assert r["city"][0]["kind"] == "ASSUMPTION" and r["city"][0]["cites"] == []  # an address cited as availability
    assert "known" not in r and "success" not in r  # another city compared: dropped; a field not returned: absent
    a, b = p["options"]
    lines = {ln["item_ref"]: ln for ln in a["cost_lines"]}
    assert lines["retail:STOCK"]["basis"] == "ASSUMED" and lines["retail:STOCK"]["basis_note"].startswith("4 cases")
    assert lines["retail:SHIPPING"]["basis"] == "NOT_KNOWN" and lines["retail:SHIPPING"]["amount"] is None  # E1 states no CA$50
    assert lines["digital:MEDIA_SPEND"]["basis"] == "NOT_KNOWN"  # an amount without quantity, unit and period is refused
    assert lines["retail:OTHER"]["by"] == "code" and lines["digital:OTHER"]["by"] == "code"  # never free by omission
    fee = next(ln for ln in b["cost_lines"] if ln["item_ref"] == "creator:CREATOR_FEE")
    assert fee["basis"] == "ASSUMED" and fee["source_claim_ids"] == []  # a product price passed the amount test, not the item test
    judged = [it["text"] for it in strat_env.listings[-1]]
    assert any(t.startswith("Creator fee costs CAD 40.00") for t in judged) and len(strat_env.listings) == 1  # one check call
    assert {f["field"] for f in p["faults"]} >= {"rationale.city", "options.A.cl_retail_shipping"}
    assert "QLOO" in strat_env.prompts[-1] and "Return ProposalIn." in strat_env.prompts[-1]


def test_preview_is_what_adoption_writes_and_founder_amounts_win(client, strat_env):
    """AC5-15 [R3] and AC5-4: an option introducing an excluded method carries all its items; a founder amount changed
    before adoption is in both the preview and the adopted plan; a stale base is 409; labels follow role_by."""
    pid = planned(client, strat_env, digital=True)
    p = propose(client, pid)
    b = next(o for o in p["options"] if o["id"] == "B")
    refs = {ln["item_ref"] for ln in b["preview"]["cost_lines"] if ln["counts"]}
    assert {"creator:CREATOR_FEE", "creator:STOCK", "digital:MEDIA_SPEND", "digital:OTHER"} <= refs
    assert b["preview"]["fit"] == "AFFORDABILITY_UNRESOLVED"
    assert line(client, pid, "cl_retail_stock", "250").status_code == 200  # the founder's amount, before adopting
    pre = client.get(f"/api/projects/{pid}/plans/toronto/proposals/{p['id']}/options/A/preview").json()
    stock = next(ln for ln in pre["cost_lines"] if ln["id"] == "cl_retail_stock")
    assert stock["amount"] == {"minor": 25000, "ccy": "USD"} and stock["by"] == "founder"
    url = f"/api/projects/{pid}/plans/toronto/proposals/{p['id']}/adopt"
    assert client.post(url, json={"proposal_version": p["version"], "option": "A", "base_version": pre["base_version"] - 1}).status_code == 409
    r = client.post(url, json={"proposal_version": p["version"], "option": "A", "base_version": pre["base_version"]})
    assert r.status_code == 200, r.text
    v = pv(client, pid)
    plan = db.get_record(plans.plan_id(pid, "toronto"))
    assert plan["source"] == "chosen" and plan["proposal"]["option"] == "A" and plan["alternative"]["option"] == "B"
    assert next(ln for ln in v["money"]["lines"] if ln["id"] == "cl_retail_stock")["amount"]["minor"] == 25000
    assert [ln for ln in v["money"]["lines"] if ln["id"] != "cl_retail_stock"] == \
        [ln for ln in pre["cost_lines"] if ln["id"] != "cl_retail_stock"]  # the same computation
    assert {m["role"]["label"] for m in v["methods"] if m["role"]} == {"Proposed by Landfall · you adopted it"}
    assert v["proposal"]["adopted"] == "A" and v["money"]["rationale"]["brand"][0]["kind"] == "FACT"
    roles = {m["method"]: m["role"]["role"] for m in v["methods"] if m["role"]}
    assert put_plan(client, pid, {**roles, "digital": "OPTIONAL"}).status_code == 200
    labels = {m["method"]: m["role"]["label"] for m in pv(client, pid)["methods"] if m["role"]}
    assert labels == {"retail": "Proposed by Landfall · you adopted it", "digital": "Selected by you"}


def test_a_ceiling_change_recomputes_both_options_with_no_model_call(client, strat_env):
    """AC5-15 and the recommendation: the first option in preference order that isn't over the ceiling."""
    pid = planned(client, strat_env, digital=True)
    p = propose(client, pid)
    assert p["recommendation"] == "A"
    n, calls = usage_rows(pid), len(strat_env.prompts)
    r = ceiling(client, pid, 100)  # option A's assumed CA$400 of stock is over US$100
    assert r["notice"].endswith("3 fits recalculated; research not re-run.")
    p = pv(client, pid)["proposal"]
    assert {o["id"]: o["preview"]["fit"] for o in p["options"]} == {"A": "OVER_CEILING", "B": "AFFORDABILITY_UNRESOLVED"}
    assert p["recommendation"] == "B" and usage_rows(pid) == n and len(strat_env.prompts) == calls


def test_no_alternative_is_fabricated_and_an_unusable_answer_changes_nothing(client, strat_env):
    """AC5-6 and the recovery path: an option B with A's roles is dropped; no MAIN twice → FAILED, plan unchanged."""
    pid = planned(client, strat_env, digital=True)
    same = [{"method": "retail", "role": "MAIN"}, {"method": "digital", "role": "SUPPORTS"}]
    strat_env.answers[:] = [answer(b_roles=same)]
    p = propose(client, pid)
    assert [o["id"] for o in p["options"]] == ["A"] and any(f["field"] == "options.B" for f in p["faults"])
    v0 = db.get_record(plans.plan_id(pid, "toronto"))["version"]
    bad = answer()
    for o in bad["options"]:
        o["methods"] = [{"method": "retail", "role": "SUPPORTS"}]
    strat_env.answers[:] = [bad, bad]
    n = len(strat_env.prompts)
    put_plan(client, pid, {"retail": "MAIN", "digital": "SUPPORTS"})  # a new base, so a new run
    v0 += 1
    p = propose(client, pid)
    assert len(strat_env.prompts) == n + 2 and "couldn't be used" in strat_env.prompts[-1]  # the one repair
    assert p["run"]["status"] == "failed" and "nothing in your plan changed" in p["run"]["message"]
    assert db.get_record(plans.plan_id(pid, "toronto"))["version"] == v0 and p["version"] == 1  # the earlier proposal stays


# ---- the digital scenario and Act (AC5-8, AC5-9, AC5-14) ----
def scenario(client, pid, inputs, scenario_version="current"):
    v = pv(client, pid)
    sv = (v["scenario"] or {}).get("version") if scenario_version == "current" else scenario_version
    return client.put(f"/api/projects/{pid}/plans/toronto/scenario",
                      json={"base_version": v["plan"]["version"], "scenario_version": sv, "inputs": inputs})


THREE = [{"platform": "META_ADS", "rates": {"cpm": {"low": "8", "mid": "10", "high": "12"}, "ctr": {"low": "0.008", "mid": "0.01", "high": "0.012"}},
          "input_states": {"cpm": "SOURCED", "ctr": "SOURCED"},
          "source": {"name": "SYNTHETIC benchmark", "as_of": "2026-10-01", "context": "test fixture, not a published source"}},
         {"platform": "GOOGLE_ADS"}, {"platform": "TIKTOK_ADS"}]


def test_a_scenario_write_is_scenario_and_plan_together_and_rates_stay_on_their_platform(client, strat_env):
    """AC5-8 [R1] on the Toronto plan (CAD): CA$30 × 14 → CA$420 media spend, CA$10 each; Meta's rates never reach
    Google or TikTok; a destination change rewrites the split; a YOU SAID allocation is kept."""
    pid = planned(client, strat_env, digital=True)
    r = scenario(client, pid, {"total_daily": {"amount": "30", "ccy": "CAD"}, "platforms": THREE})
    assert r.status_code == 200, r.text
    v = pv(client, pid)
    s = v["scenario"]
    assert s["version"] == 1 and db.get_record(plans.plan_id(pid, "toronto"))["scenario"] == {"id": s["id"], "version": 1}
    media = next(ln for ln in v["money"]["lines"] if ln["item_ref"] == "digital:MEDIA_SPEND")
    assert media["amount"] == {"minor": 42000, "ccy": "CAD"} and media["by"] == "scenario" and "a day × 14 days" in media["basis_note"]
    rows = {p["platform"]: p for p in s["results"]["platforms"]}
    assert {p: x["allocation"]["minor"] for p, x in rows.items()} == {"META_ADS": 1000, "GOOGLE_ADS": 1000, "TIKTOK_ADS": 1000}
    assert rows["META_ADS"]["outputs"]["impressions"]["mid"] == 14000 and rows["GOOGLE_ADS"]["outputs"]["impressions"] is None
    assert s["results"]["aggregate"]["impressions"] is None and s["results"]["note"] == "1 of 3 platforms have rates"
    assert rows["META_ADS"]["outputs"]["unique_reach"] is None and rows["META_ADS"]["basis"] == "BENCHMARK"
    assert scenario(client, pid, {"total_daily": {"amount": "30", "ccy": "USD"}, "platforms": THREE}).status_code == 422  # the test's currency
    assert scenario(client, pid, {"total_daily": {"amount": "30", "ccy": "CAD"}, "platforms": THREE}, 0).status_code == 409
    tik = {**THREE[2], "allocation_by": "YOU_SAID", "allocation": {"amount": "20", "ccy": "CAD"}}
    assert scenario(client, pid, {"total_daily": {"amount": "30", "ccy": "CAD"}, "platforms": [*THREE[:2], tik]}).status_code == 200
    alloc = {p["platform"]: p["allocation"]["minor"] for p in pv(client, pid)["scenario"]["platforms"]}
    assert alloc == {"META_ADS": 500, "GOOGLE_ADS": 500, "TIKTOK_ADS": 2000}
    assert scenario(client, pid, {"total_daily": {"amount": "30", "ccy": "CAD"}, "platforms": THREE[:2]}).status_code == 200
    s = pv(client, pid)["scenario"]
    assert s["version"] == 3 and [p["allocation"]["minor"] for p in s["platforms"]] == [1500, 1500]
    over = [{**THREE[0], "allocation_by": "YOU_SAID", "allocation": {"amount": "40", "ccy": "CAD"}}, THREE[1]]
    r = scenario(client, pid, {"total_daily": {"amount": "30", "ccy": "CAD"}, "platforms": over})
    assert r.status_code == 422 and "more than your total" in r.json()["detail"]
    ln = client.patch(f"/api/projects/{pid}/plans/toronto/cost-lines/cl_digital_media_spend",
                      json={"base_version": pv(client, pid)["plan"]["version"], "amount": {"amount": "1", "ccy": "CAD"}})
    assert ln.status_code == 422 and "digital scenario" in ln.json()["detail"]


def sign_now(client, pid):
    r = client.post(f"/api/projects/{pid}/plans/toronto/sign-off",
                    json={"base_version": pv(client, pid)["plan"]["version"], "expect": expect_of(client, pid)})
    assert r.status_code == 200, r.text
    return r.json()["version"]


def test_act_reads_only_the_signed_money_and_a_change_needs_re_prepare_and_new_approval(client, strat_env, monkeypatch):
    """AC5-9 and AC5-14 [R2]: the package is prefilled from the signed version's allocations; a scenario edit after
    approval changes neither the approved bytes nor the signed totals; re-signing flags the package and re-preparing
    writes v+1, which needs a new approval; an edit concurrent with sign-off gets 409; reopening keeps every version."""
    pid = planned(client, strat_env, digital=True)
    assert put_plan(client, pid, {"retail": "SUPPORTS", "digital": "MAIN"}).status_code == 200
    scenario(client, pid, {"total_daily": {"amount": "30", "ccy": "CAD"}, "platforms": THREE[:2]})
    v1 = sign_now(client, pid)
    strat_env.copy = {"headlines": [{"text": "Sparkling tea in Toronto"}, {"text": "Cold-brewed, lightly fizzy"}, {"text": "Try Tidewell"}],
                      "descriptions": [{"text": "Hibiscus and berry sparkling tea."}, {"text": "Join the Toronto waitlist today."}],
                      "primary_text": {"text": "Sparkling cold-brewed tea, now on a Toronto waitlist."},
                      "ad_text": {"text": "Sparkling cold-brewed tea for Toronto."}, "keywords": ["sparkling tea toronto"], "cta": "SIGN_UP"}
    r = client.post(f"/api/projects/{pid}/drafts/drf_{pid}_toronto_digital/package", json={})
    assert r.status_code == 200, r.text
    idle(pid)
    pkg = db.get_record(r.json()["package_id"])
    assert pkg["budget"]["source"] == "PLAN" and pkg["budget"]["plan"]["version"] == v1 and pkg["destinations"] == ["META_ADS", "GOOGLE_ADS"]
    assert pkg["selection"]["state"] == "IN_SIGNED_PLAN" and pkg["selection"]["role"] == "MAIN"
    assert {d: delivery.daily_for(pkg, d)["minor"] for d in pkg["destinations"]} == {"META_ADS": 1500, "GOOGLE_ADS": 1500}
    meta = delivery.provider_plan(pid, pkg, "META_ADS")
    adset = next(x for x in meta["requests"] if x["step"] == "adset")
    assert adset["form"]["daily_budget"] == "1500"  # its own allocation, never the CA$30 total
    pv_ = view(client, pid, pkg["id"])
    assert pv_["budget_view"]["split"] == {"META_ADS": "CA$15", "GOOGLE_ADS": "CA$15"} and not pv_["budget_view"]["differs_from_plan"]
    assert any(i["text"].startswith("Costs in your signed plan: US$302.40 + 1 not known") for i in pv_["info"]), pv_["info"]
    assert not re.search(r"slice 5|next build", json.dumps([pv_, pv(client, pid)]), re.I)  # AC5-12 on the plan route
    approved = delivery.h(delivery.provider_plan(pid, pkg, "META_ADS")["requests"])
    signed_money = pv(client, pid)["signed"]["money"]

    scenario(client, pid, {"total_daily": {"amount": "40", "ccy": "CAD"}, "platforms": THREE[:2]})  # after the package
    assert db.get_record(pkg["id"])["version"] == pkg["version"] and delivery.h(delivery.provider_plan(pid, pkg, "META_ADS")["requests"]) == approved
    v = pv(client, pid)
    assert v["signed"]["money"] == signed_money and v["plan"]["changed_since_sign_off"]
    assert client.get(f"/api/projects/{pid}/plans/toronto/act").json()["plan"]["version"] == v1  # Act keeps the signed one
    assert not delivery.stale_of(pid, db.get_record(pkg["id"]))
    base = v["plan"]["version"]
    assert line(client, pid, "cl_retail_stock", "5").status_code == 200  # a concurrent edit lands first
    r = client.post(f"/api/projects/{pid}/plans/toronto/sign-off", json={"base_version": base, "expect": expect_of(client, pid)})
    assert r.status_code == 409 and db.get_record(plans.plan_id(pid, "toronto"))["signed_off"] is None
    v2 = sign_now(client, pid)
    stale = delivery.stale_of(pid, db.get_record(pkg["id"]))
    assert stale == [f"Your signed plan's budget changed (v{v1} → v{v2}) · Re-prepare"]
    r = client.post(f"/api/projects/{pid}/drafts/drf_{pid}_toronto_digital/package", json={})
    idle(pid)
    new = db.get_record(pkg["id"])
    assert new["version"] == pkg["version"] + 1 and new["budget"]["amount"]["minor"] == 4000 and new["budget"]["plan"]["version"] == v2
    assert delivery.daily_for(new, "META_ADS")["minor"] == 2000 and not delivery.stale_of(pid, new)
    assert db.get_record(pkg["id"], pkg["version"])["budget"]["amount"]["minor"] == 3000  # the earlier version is unchanged
    you = client.patch(f"/api/projects/{pid}/packages/{pkg['id']}", json={"base_version": new["version"], "fields": {"budget": {"amount": "50", "ccy": "CAD"}}})
    assert you.status_code == 200, you.text
    bv = view(client, pid, pkg["id"])["budget_view"]
    assert bv["source"] == "YOU_SAID" and bv["differs_from_plan"] and bv["split"] == {"META_ADS": "CA$25", "GOOGLE_ADS": "CA$25"}

    versions = {r["id"]: r["version"] for r in db.records("plan", pid) + db.records("scenario", pid) + db.records("package", pid)}
    db.reset_for_tests(config.DB_PATH)  # a warm resume: the same database reopened
    assert {r["id"]: r["version"] for r in db.records("plan", pid) + db.records("scenario", pid) + db.records("package", pid)} == versions


def test_the_creator_search_pins_the_signed_creator_fee(monkeypatch):
    """AC5-10 (S5-12) with the SYNTHETIC creator provider: none until the signed plan prices the creator fee."""
    from test_slice66 import Fake, KEY, project
    from datetime import date
    from landfall import influencers as ic
    monkeypatch.setitem(config._ENV, "influencers_club_api_key", KEY)
    monkeypatch.setattr(ic, "TERMS_FROM", date(2020, 1, 1))
    monkeypatch.setattr(config, "PROVIDER_MODE", "live")
    monkeypatch.setattr(ic, "transport", Fake())
    pid = project()
    assert ic.view(pid, "brisbane", "s1")["creator_budget"] is None
    fee = {**plans.code_line("creator:CREATOR_FEE", "Creator fee", "expense"), "amount": {"minor": 40000, "ccy": "AUD"},
           "basis": "ASSUMED", "basis_note": "one reel, flat, assumed", "by": "strategist"}
    db.put_record("plan", pid, {"city_id": "brisbane", "methods": [{"method": "creator", "role": "MAIN", "role_by": "founder"}],
                                "cost_lines": [fee], "signed_off": {"at": db.now(), "by": "founder"}, "source": "founder_selection",
                                "status": "active"}, rid=plans.plan_id(pid, "brisbane"))
    v = ic.view(pid, "brisbane", "s1")
    assert v["creator_budget"]["line_id"] == "cl_creator_creator_fee" and v["creator_budget_line"] == "From your signed plan v1: A$400 (assumed)"
    sid = ic.search(pid, "brisbane", "s1")["search_id"]
    pin = db.get_record(sid)["inputs"]["creator_budget"]
    assert pin["plan"] == {"id": plans.plan_id(pid, "brisbane"), "version": 1} and pin["amount"] == {"minor": 40000, "ccy": "AUD"}
    assert "40000" not in json.dumps(db.get_record(sid)["request"]["sent"])  # pinned, never a filter (6.6 R7)


def test_an_unreadable_answer_is_a_clean_failure_after_one_repair(client, strat_env):
    """LIVE L5-A @1: a structured answer that can't be read (a refused retry inside it) fails with Retry, no crash."""
    pid = planned(client, strat_env, digital=True)
    strat_env.answers[:] = [{"error": "EventLoopException"}, {"error": "EventLoopException"}]
    v0 = db.get_record(plans.plan_id(pid, "toronto"))["version"]
    p = propose(client, pid)
    assert p["run"]["status"] == "failed" and "nothing in your plan changed" in p["run"]["message"] and p["id"] is None
    assert db.get_record(plans.plan_id(pid, "toronto"))["version"] == v0


# ---- REVIEW-SLICE-05 repairs ----
def test_r1_a_cleared_signed_budget_clears_the_plan_budget_on_re_prepare(client, strat_env):
    """R1: the signed plan withdrew its daily budget, so the re-prepared package has none (a budget is needed again);
    the earlier version keeps its own, and a YOU SAID budget would have been kept."""
    pid = planned(client, strat_env, digital=True)
    scenario(client, pid, {"total_daily": {"amount": "30", "ccy": "CAD"}, "platforms": THREE[:2]})
    sign_now(client, pid)
    r = client.post(f"/api/projects/{pid}/drafts/drf_{pid}_toronto_digital/package", json={})
    idle(pid)
    old = db.get_record(r.json()["package_id"])
    assert old["budget"]["source"] == "PLAN"
    assert scenario(client, pid, {"total_daily": None, "platforms": THREE[:2]}).status_code == 200
    sign_now(client, pid)
    assert delivery.signed_money_changed(pid, old)
    client.post(f"/api/projects/{pid}/drafts/drf_{pid}_toronto_digital/package", json={})
    idle(pid)
    new = db.get_record(old["id"])
    assert new["version"] == old["version"] + 1 and new["budget"]["amount"] is None and new["budget"]["source"] is None
    assert not new["budget"].get("allocations") and not delivery.stale_of(pid, new)
    assert any(m["field"] == "budget" for m in delivery.adapters()["META_ADS"].required(new))  # export needs a budget again
    assert db.get_record(old["id"], old["version"])["budget"]["amount"]["minor"] == 3000  # history unchanged


def test_r2_sign_off_signs_the_reviewed_money_not_a_newer_rate(client, money_env, monkeypatch):
    """R2: a publication stored after the founder reviewed the plan never reprices the version they sign."""
    import test_slice5
    pid = planned(client, money_env)
    line(client, pid, "cl_retail_stock", "100", "CAD")
    before = pv(client, pid)
    money_env.rates.day = "2026-10-28"
    monkeypatch.setattr(test_slice5, "ECB", ECB.replace("currency='CAD' rate='1.5000'", "currency='CAD' rate='3.0000'"))
    r = client.post(f"/api/projects/{pid}/plans/toronto/fx/retry", json={"base_version": before["plan"]["version"]})
    assert r.json()["version"] == before["plan"]["version"] and plans.latest_fx(pid)["rate_date"] == "2026-10-28"
    sign_now(client, pid)
    signed = pv(client, pid)["signed"]["money"]
    assert signed["cash_out_known"] == before["money"]["cash_out_known"] == money.usd(7200)
    assert signed["fx"]["rate_date"] == "2026-10-26" and signed["text"] == before["money"]["text"]


def test_r4_an_adopted_rationale_keeps_its_own_evidence(client, strat_env, monkeypatch):
    """R4: adopt v1 → evidence renumbered → propose v2: the plan's FACT still resolves against v1's E1."""
    pid = planned(client, strat_env, digital=True)
    p = propose(client, pid)
    client.post(f"/api/projects/{pid}/plans/toronto/proposals/{p['id']}/adopt",
                json={"proposal_version": p["version"], "option": "A", "base_version": p["options"][0]["preview"]["base_version"]})
    other = [{**EVIDENCE[3], "id": "E1"}, *[{**e, "id": f"E{n}"} for n, e in enumerate(EVIDENCE[:3], 2)]]
    monkeypatch.setattr(strategist, "evidence", lambda pid, cid: other)
    put_plan(client, pid, {"retail": "MAIN", "digital": "SUPPORTS"})
    p2 = propose(client, pid)
    m = pv(client, pid)["money"]
    assert p2["version"] == 2 and p2["evidence"][0]["text"] == "Audience: tea lovers"
    fact = m["rationale"]["brand"][0]
    assert fact["kind"] == "FACT" and fact["cites"] == ["E1"]
    assert next(e for e in m["rationale_evidence"] if e["id"] == "E1")["text"].startswith("Kettle & Crane: stocks small")
    sign_now_ok = client.post(f"/api/projects/{pid}/plans/toronto/sign-off", json={"base_version": pv(client, pid)["plan"]["version"],
                                                                                  "expect": expect_of(client, pid)})
    assert sign_now_ok.status_code == 200
    assert pv(client, pid)["signed"]["money"]["rationale_evidence"] == m["rationale_evidence"]


def test_an_interrupted_proposal_resumes_without_a_second_strategist_call(client, strat_env):
    """§5.12 recovery: a stop after the strategist answered and before the check and publish; a warm resume reuses the
    stored answer (0 new strategist calls), publishes once and changes no plan version."""
    from landfall import runner
    pid = planned(client, strat_env, digital=True)
    v0 = db.get_record(plans.plan_id(pid, "toronto"))["version"]
    propose(client, pid)
    run_id = db.q("SELECT id FROM runs WHERE project_id=? AND kind='plan_propose'", pid)[0]["id"]
    db.x("UPDATE runs SET status='running' WHERE id=?", run_id)  # the process stopped mid-run
    db.x("DELETE FROM tasks WHERE run_id=? AND task_key IN ('rates', 'check', 'publish')", run_id)
    db.x("DELETE FROM records WHERE kind='proposal' AND project_id=?", pid)
    calls = len(strat_env.prompts)
    assert runner.resume_all() == 1
    idle(pid)
    p = pv(client, pid)["proposal"]
    assert len(strat_env.prompts) == calls and p["version"] == 1 and p["run"]["status"] == "completed"
    assert db.get_record(plans.plan_id(pid, "toronto"))["version"] == v0


def test_the_proposal_uses_the_budget_from_the_estimate_table_and_a_split(client, strat_env):
    """Owner, 28 Oct 2026 ("if the budget is 10k, then use the 10k"): every item without a founder amount or a listed
    rate takes the ESTIMATE table's figure, labelled; what is left under the ceiling goes to the levers by the split;
    the total is near the ceiling and never over it; the daily ad budget is on the media line."""
    pid = planned(client, strat_env, digital=True)
    ceiling(client, pid, 10000)
    assert line(client, pid, "cl_retail_shipping", "300").status_code == 200  # the founder's amount stays and counts
    a = answer()["options"][0] | {"budget_split": [{"lever": "digital:MEDIA_SPEND", "percent": 60, "why": "Reach the waitlist."},
                                                   {"lever": "retail:STOCK", "percent": 40, "why": "Enough on the shelf."},
                                                   {"lever": "creator:CREATOR_FEE", "percent": 10}]}  # creator isn't in A
    strat_env.answers[:] = [answer(options=[a])]
    p = propose(client, pid)
    o = p["options"][0]
    assert [x["lever"] for x in o["split"]] == ["digital:MEDIA_SPEND", "retail:STOCK"] and "%" in o["split"][0]["text"]
    assert any("creator:CREATOR_FEE" in f["reason"] for f in p["faults"])
    lines = {ln["item_ref"]: ln for ln in o["preview"]["cost_lines"] if ln["counts"]}
    assert lines["retail:SHIPPING"]["basis"] == "YOU_SAID" and lines["retail:SHIPPING"]["amount"]["minor"] == 30000
    assert lines["retail:STOCK"]["basis"] == "ESTIMATE" and "sized to use 40%" in lines["retail:STOCK"]["basis_note"]
    assert lines["digital:OTHER"]["basis"] == "ESTIMATE" and lines["digital:OTHER"]["basis_note"].startswith("Estimate: ")
    media = lines["digital:MEDIA_SPEND"]
    assert media["amount"]["minor"] == media["daily"]["minor"] * media["days"] and media["daily"]["minor"] % 100 == 0
    assert lines["retail:OTHER"]["amount"] is None  # margin terms: no table figure, so still not known
    planned_ = o["preview"]["money"]["planned"]
    assert 9000_00 <= planned_["amount"]["minor"] <= 10000_00 and planned_["known"]["minor"] == 30000
    assert planned_["text"].endswith("planned of US$10,000 · US$300 known, the rest labeled estimates · 1 not known")
    r = client.post(f"/api/projects/{pid}/plans/toronto/proposals/{p['id']}/adopt",
                    json={"proposal_version": p["version"], "option": "A", "base_version": o["preview"]["base_version"]})
    assert r.status_code == 200, r.text
    m = pv(client, pid)["money"]
    assert m["planned"]["amount"] == planned_["amount"] and m["fit"] != "OVER_CEILING"
    ln = next(x for x in m["lines"] if x["item_ref"] == "digital:MEDIA_SPEND")
    assert ln["daily"] == media["daily"]  # what the digital package reads
    assert line(client, pid, ln["id"], "500").status_code == 200
    assert "daily" not in next(x for x in pv(client, pid)["money"]["lines"] if x["item_ref"] == "digital:MEDIA_SPEND")
