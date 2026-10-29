"""Launch (owner, 28 Oct 2026): one checklist, prepared in the background, each item ending in one sign-off through the
owner's shared connection. Offline: SYNTHETIC pages, model answers, creators and providers (test_slice6's fixtures);
fictional ids and addresses only. These prove handling, never a live send or create."""
import json

import pytest
from test_markets import idle, markets_env  # noqa: F401
from test_shared_delivery import connect_shared, ga_test, shared_on
from test_slice4 import env, research_ready  # noqa: F401
from test_slice6 import approve_and_run, configure, deliver_env, dest, edit, provider, view  # noqa: F401
from test_slice45_runner import spec_env  # noqa: F401
from test_slice65_plan import beachhead, develop, dossiered, investigate, lead_of, plan_env, put_plan, sign, signed  # noqa: F401
from test_slice67 import INBOX, sent_mime, test_mode

from landfall import config, db, delivery, influencers


def launch(client, pid):
    r = client.get(f"/api/projects/{pid}/plans/toronto/launch")
    assert r.status_code == 200, r.text
    return r.json()


def item(lv, method):
    return next(i for i in lv["items"] if i["method"] == method)


def no_model(monkeypatch):
    def fail(ctx, prompt):
        raise RuntimeError("no model in this test")
    monkeypatch.setattr(delivery, "copy_call", fail)


def test_template_message_is_on_the_item_before_any_click_and_prepared_in_the_background(client, plan_env, monkeypatch):
    pid = signed(client, plan_env)
    lv = launch(client, pid)
    it = item(lv, "retail")
    assert it["packages"] == [] and it["preview"]["to"] == "Kettle & Crane"  # nothing prepared, the message already shows
    assert "Kettle & Crane" in it["preview"]["body"] and lv["settings"]["sender_name"] == "The Tidewell team"
    assert lv["settings"]["radius_km"] == delivery.LAUNCH_RADIUS_KM and lv["settings"]["start"] == delivery.next_monday()

    no_model(monkeypatch)  # the model pass fails: the template message stands, nothing waits on it
    out = delivery.prewarm_launch(pid, "toronto")
    idle(pid)
    assert delivery.prewarm_launch(pid, "toronto")["started"] == []  # idempotent
    it = item(launch(client, pid), "retail")
    v = view(client, pid, it["packages"][0]["id"])
    assert out["started"] == [v["id"]] and v["copy_origin"] == "TEMPLATE" and v["template_ref"]["id"].endswith("_retail_0")
    assert "Kettle & Crane" in v["email"]["body"] and "Hibiscus Berry Tea" in v["email"]["body"]
    assert v["from_name"] == "The Tidewell team" and v["recipients"]["to"] == ["hello@kettlecrane.example"]
    assert dest(v, "EMAIL")["state"]["missing"] == []  # the sender name is a setting, never a per-package blocker


def test_the_model_pass_tailors_the_template_and_keeps_the_honesty_checks(client, plan_env):
    pid = signed(client, plan_env)
    delivery.prewarm_launch(pid, "toronto")
    idle(pid)
    v = view(client, pid, item(launch(client, pid), "retail")["packages"][0]["id"])
    assert v["copy_origin"] == "ENRICHED" and "Start from this standard message" in plan_env.prompts[-1]
    assert "Shelf trial" not in plan_env.prompts[-1] and "Kettle & Crane" in plan_env.prompts[-1]
    flagged = [s for s in v["copy"]["segments"] if s["state"] == "TO_CHECK"]
    assert flagged and "award" in flagged[0]["text"]  # an unsupported claim stays flagged, as an inline fix
    assert any("award" in m["text"] for m in dest(v, "EMAIL")["state"]["missing"])


def test_send_test_email_is_one_sign_off_through_the_shared_connection(client, plan_env, monkeypatch, provider):
    pid = signed(client, plan_env)
    no_model(monkeypatch)
    delivery.prewarm_launch(pid, "toronto")
    idle(pid)
    configure(monkeypatch)
    test_mode(monkeypatch)
    shared_on(monkeypatch)
    connect_shared("EMAIL")
    v = view(client, pid, item(launch(client, pid), "retail")["packages"][0]["id"])
    d = dest(v, "EMAIL")
    assert d["shared"]["available"] and d["routing"] and d["next"] == ["approve"]
    a = approve_and_run(client, pid, v, "EMAIL").json()
    assert a["status"] == "SUCCEEDED" and a["mode"] == "LIVE"
    assert sent_mime(provider)["To"] == INBOX  # the partner is never contacted
    assert db.get_record(a["id"])["connection_id"] == "con__shared_email"
    assert dest(view(client, pid, v["id"]), "EMAIL")["summary"] == "TEST_SENT"


def digital_signed(client, env):
    """Retail MAIN and Digital SUPPORTS, signed; the digital draft's channel is Meta (the Launch path still makes Google Ads)."""
    pid = dossiered(client, env)
    env.spec_script = lambda T: {"concept_name": "City waitlist", "concept_line": "Invite Toronto tea drinkers to a waitlist.",
                                 "channel_primary": "Instagram (Meta)", "channel_alternative": None, "objective": "SIGN_UP",
                                 "radius_km_hypothesis": 25, "targeting": [
                                     {"element": "city + radius", "basis": "PLATFORM_OPTION", "platforms_ref": "meta.city_radius"}],
                                 "message": "Sparkling cold-brewed tea.", "primary_metric": "Waitlist sign-ups",
                                 "stop_rule": "Stop after two weeks if sign-ups stay flat."}
    investigate(client, pid, "digital")
    develop(client, pid)
    develop(client, pid, "digital")
    beachhead(client, pid)
    assert put_plan(client, pid, {"retail": "MAIN", "digital": "SUPPORTS"}).status_code == 200
    assert sign(client, pid).status_code == 200, sign(client, pid).text
    env.copy = {"headlines": [{"text": "Sparkling tea, lightly fizzy"}, {"text": "Cold-brewed in a can"}, {"text": "Try Tidewell"}],
                "descriptions": [{"text": "Hibiscus and berry sparkling tea in a can."}, {"text": "Join the waitlist today."}],
                "primary_text": {"text": "Sparkling cold-brewed tea."}, "ad_text": {"text": "Sparkling cold-brewed tea."},
                "keywords": ["sparkling canned tea", "hibiscus tea"], "cta": "SIGN_UP"}
    return pid


def test_digital_is_google_ads_only_and_the_picked_ad_is_the_campaigns(client, plan_env, monkeypatch, provider):
    pid = digital_signed(client, plan_env)
    delivery.prewarm_launch(pid, "toronto")
    idle(pid)
    it = item(launch(client, pid), "digital")
    v = view(client, pid, it["packages"][0]["id"])
    assert v["destinations"] == ["GOOGLE_ADS"]  # never Meta or TikTok on this path, whatever the draft's channel
    assert [x["headline"] for x in it["variants"]] == ["Sparkling tea, lightly fizzy", "Cold-brewed in a can", "Try Tidewell"]
    assert it["picked"] is None
    s = launch(client, pid)["settings"]  # the launch settings filled the package: no per-package blockers for them
    assert v["targeting"]["geo"]["radius_km"] == delivery.LAUNCH_RADIUS_KM and v["timing"]["start"] == s["start"] and v["budget"]["amount"] == s["budget"]
    v = edit(client, pid, v, {"ad_pick": 2, "rights_confirmed": True})
    assert item(launch(client, pid), "digital")["picked"] == 2
    assert v["copy"]["headlines"][0]["text"] == "Try Tidewell" and v["copy"]["descriptions"][0]["text"].startswith("Hibiscus")
    assert [x["headline"] for x in item(launch(client, pid), "digital")["variants"]][2] == "Try Tidewell"  # variants keep their order

    configure(monkeypatch)
    ga_test(monkeypatch)
    monkeypatch.setitem(config._ENV, "LANDFALL_DELIVERY_ALLOWLIST", "5550001111")
    shared_on(monkeypatch)
    connect_shared("GOOGLE_ADS")
    # the test account bills in CAD; this offline plan has no FX, so its estimate is in USD: set once, at the top
    r = client.put(f"/api/projects/{pid}/plans/toronto/launch/settings", json={"fields": {"budget": {"amount": "25", "ccy": "CAD"}}})
    assert r.status_code == 200, r.text
    v = view(client, pid, v["id"])
    d = dest(v, "GOOGLE_ADS")
    assert d["next"] == ["approve"], (d["summary"], d["text"], d["plan"]["blockers"])
    a = approve_and_run(client, pid, v, "GOOGLE_ADS").json()
    assert a["status"] == "SUCCEEDED"
    ops = next(r for s, r in provider.calls if s == "mutate")["json"]["mutateOperations"]
    rsa = next(o for o in ops if "adGroupAdOperation" in o)["adGroupAdOperation"]["create"]["ad"]["responsiveSearchAd"]
    assert rsa["headlines"][0]["text"] == "Try Tidewell"  # the founder's pick leads the campaign
    assert dest(view(client, pid, v["id"]), "GOOGLE_ADS")["summary"] == "CREATED_PAUSED"


def test_only_people_we_can_contact_are_offered(client, plan_env):
    pid = signed(client, plan_env)
    names = lambda: [o["name"] for o in item(launch(client, pid), "retail")["options"]]  # noqa: E731
    assert names() == ["Kettle & Crane"]  # Bramble Grocer has no email yet: it stays in the research
    br = lead_of(pid, "Bramble Grocer")
    r = client.post(f"/api/projects/{pid}/plans/toronto/launch/pick", json={"method": "retail", "lead_id": br})
    assert r.status_code == 422  # and can't be picked around the list
    client.post(f"/api/projects/{pid}/leads/{br}/contact", json={"base_version": 0, "contact": "hi@bramble.example"})
    assert names() == ["Kettle & Crane", "Bramble Grocer"]
    # a research creator with a guessed handle and no evidence (identity POSSIBLE) and no email: no route
    omar = {"key": "creator|https://www.youtube.com/@x", "contact": None,
            "creator": {"canonical_url": "https://www.youtube.com/@x", "identity": "POSSIBLE"}}
    assert delivery.contact_route(pid, omar) is None
    assert delivery.contact_route(pid, {**omar, "creator": {**omar["creator"], "identity": "PROVIDER"}})["kind"] == "profile"


@pytest.fixture
def creator_plan(plan_env, monkeypatch, client):
    pid = dossiered(client, plan_env)
    plan_env.spec_script = lambda T: {"concept_name": "Creator tasting", "concept_line": "A creator films a first taste.",
                                      "creators": [], "missing": ["Whether a local creator would take part"]}
    investigate(client, pid, "creator")
    develop(client, pid, "creator")
    beachhead(client, pid)
    put_plan(client, pid, {"creator": "MAIN"})
    assert sign(client, pid).status_code == 200
    monkeypatch.setattr(influencers, "_demo", lambda p: True)  # SYNTHETIC creators; nothing dispatched
    return pid


def test_one_background_creator_search_then_a_creator_without_email_is_a_dm(client, creator_plan, monkeypatch):
    pid = creator_plan
    no_model(monkeypatch)
    delivery.prewarm_launch(pid, "toronto")
    delivery._ic_threads[(pid, "toronto")].join(10)
    delivery.prewarm_launch(pid, "toronto")  # a second call never searches again
    assert len({r["id"] for r in db.records("creator_search", pid)}) == 1
    it = item(launch(client, pid), "creator")
    opts = it["options"]
    assert opts and all(o["route"]["kind"] == "profile" for o in opts)
    assert [o["followers"] for o in opts] == sorted((o["followers"] for o in opts), reverse=True)
    assert it["packages"] == [] and opts[0]["name"] in it["preview"]["body"]  # the message shows before the pick

    r = client.post(f"/api/projects/{pid}/plans/toronto/launch/pick",
                    json={"method": "creator", "search_id": opts[0]["search"]["id"], "index": opts[0]["search"]["index"]})
    assert r.status_code == 200, r.text
    assert "Instagram DM" in r.json()["note"]  # enrichment found no email: the DM route, never a dead end
    idle(pid)
    enr = [e for e in db.records("creator_enrichment", pid)]
    assert len(enr) == 1  # one contact lookup, only for the creator picked
    v = view(client, pid, r.json()["package_id"])
    assert v["recipients"]["to"] == [] and opts[0]["name"] in v["email"]["body"]
    # "Copy message for Instagram DM" records it as yours
    m = client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/EMAIL/manual", json={"note": "Instagram DM"})
    assert m.status_code == 200, m.text
    deliver = next(i for i in client.get(f"/api/projects/{pid}/plans/toronto/act").json()["items"] if i["kind"] == "DELIVER")
    assert deliver["status"] == "MARKED_SENT_BY_YOU"


def test_launch_settings_are_asked_once_and_reach_every_package(client, plan_env, monkeypatch):
    pid = signed(client, plan_env)
    no_model(monkeypatch)
    delivery.prewarm_launch(pid, "toronto")
    idle(pid)
    pkg = item(launch(client, pid), "retail")["packages"][0]["id"]
    r = client.put(f"/api/projects/{pid}/plans/toronto/launch/settings", json={"fields": {"sender_name": "Maya at Tidewell"}})
    assert r.status_code == 200 and r.json()["by"]["sender_name"] == "YOU_SAID"
    assert view(client, pid, pkg)["from_name"] == "Maya at Tidewell"
    assert client.put(f"/api/projects/{pid}/plans/toronto/launch/settings", json={"fields": {"radius_km": 500}}).status_code == 422
    assert json.dumps(launch(client, pid)).count("Maya at Tidewell") >= 1


def test_the_ad_budget_and_dates_come_from_the_plans_sized_media_line(client, plan_env, monkeypatch):
    from datetime import date, timedelta

    from landfall import plans
    pid = signed(client, plan_env)
    rec = plans.latest_signed(pid, "toronto")
    line = {"item_ref": "digital:MEDIA_SPEND", "amount": {"minor": 40000, "ccy": "CAD"}, "by": "strategist",
            "daily": {"minor": 4000, "ccy": "CAD"}, "days": 10}
    monkeypatch.setattr(plans, "latest_signed", lambda p, c: {**rec, "cost_lines": [line]})
    d = delivery.launch_defaults(pid, "toronto")
    assert (d["budget"], d["budget_by"]) == ({"minor": 4000, "ccy": "CAD"}, "PLAN")
    assert d["end"] == (date.fromisoformat(d["start"]) + timedelta(days=9)).isoformat()
    line.update(by="founder", amount={"minor": 28000, "ccy": "CAD"}, daily=None, days=None)  # the founder's own amount ÷ the plan's days
    assert delivery.launch_defaults(pid, "toronto")["budget"] == {"minor": 2000, "ccy": "CAD"}


def test_ad_copy_is_topped_up_to_googles_minimums():
    """LIVE 28 Oct 2026: the model's headlines ran over 30 characters, 2 survived, and Create campaign stayed blocked."""
    from landfall.delivery import fill_ad_copy
    sub = {"headlines": [{"text": "A headline that is far too long for Google"}, {"text": "Bold Coffee"}], "descriptions": []}
    out = fill_ad_copy(sub, "Death Wish Coffee", ["Gingerdead Coffee", "Pumpkin Chai Coffee"], "Montreal")
    fit = [h["text"] for h in out["headlines"] if len(h["text"]) <= 30]
    assert len(fit) >= 3 and "Death Wish Coffee" in fit
    assert len([d for d in out["descriptions"] if len(d["text"]) <= 90]) >= 2


def test_ads_written_with_the_plan_are_styled_from_qloo_and_adopted_at_launch(client, plan_env, monkeypatch):
    """Owner, 28 Oct 2026: every plan writes its Google ads while it is built (autoplan → delivery.draft_ads), styled from
    the brand's Qloo taste in the city; the Launch package, still only from the signed plan, adopts them unwritten."""
    from landfall import runner
    pid = digital_signed(client, plan_env)
    db.put_record("qloo_target", pid, {"themes": [{"name": "Bar", "subtype": "urn:tag:genre:place"},
                                                  {"name": "Alternative Rock", "subtype": "urn:tag:genre:music"}]}, rid=f"qt_{pid}_toronto")
    monkeypatch.setitem(runner.HANDLERS, "t_ads", lambda run: delivery.draft_ads(run, run.project_id, "toronto") and None)
    runner.start(pid, "t_ads", "toronto", {})
    idle(pid)
    adc = db.get_record(delivery.ad_copy_id(f"drf_{pid}_toronto_digital"))
    assert adc["style"]["themes"] == ["Alternative Rock"]  # a place genre ("Bar") is where, not a taste
    assert "Style cues" in plan_env.prompts[-1] and "Alternative Rock" in plan_env.prompts[-1]
    assert delivery.packages_of(pid, "toronto", "digital") == []  # nothing is prepared before the Launch path asks

    no_model(monkeypatch)  # the Launch package must not write again
    delivery.prewarm_launch(pid, "toronto")
    idle(pid)
    it = item(launch(client, pid), "digital")
    assert [x["headline"] for x in it["variants"]] == ["Sparkling tea, lightly fizzy", "Cold-brewed in a can", "Try Tidewell"]
    assert it["styled"][0] == "Alternative Rock"
    assert view(client, pid, it["packages"][0]["id"])["ad_style"]["themes"] == ["Alternative Rock"]


def test_every_plan_runs_google_search_ads(monkeypatch):
    from landfall import strategist
    sub = {"options": [{"methods": [{"method": "retail", "role": "MAIN"}, {"method": "digital", "role": "OPTIONAL"}]},
                       {"methods": [{"method": "digital", "role": "MAIN"}]}]}
    monkeypatch.setattr(db, "get_record", lambda rid, v=None: {"status": "complete"} if rid.endswith("_digital") else None)
    out = strategist.with_search_ads(sub, "p", "toronto")
    assert out["options"][0]["methods"][-1] == {"method": "digital", "role": "SUPPORTS", "reason": "Google Search ads run alongside every plan."}
    assert len(out["options"][0]["methods"]) == 2 and out["options"][1]["methods"] == [{"method": "digital", "role": "MAIN"}]
    from landfall import specialist
    assert specialist.NOT_SEARCH.search("a bold, high-energy TikTok campaign") and not specialist.NOT_SEARCH.search("Search ads for coffee")
