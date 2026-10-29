"""Slice 4.5: the specialist runner and the retail spec, from the slice-4 research through Investigate to a saved
dossier. SYNTHETIC pages and checker verdicts; the specialist's tool loop is scripted over its real tools. Proves the
wiring, the validator and the readiness rules offline; it says nothing about model quality (the live cases do)."""
import json
import re

import pytest
from test_markets import idle, markets_env, scanned  # noqa: F401
from test_slice4 import FILLER, KC, KC_SITE, STAGES, env, html, research_ready  # noqa: F401

from landfall import config, db, research, specialist

KC_BRANDS = "https://kettlecrane.example/brands"


@pytest.fixture
def spec_env(env, monkeypatch):
    env.web.pages[KC_BRANDS] = (200, html(
        "Brands we carry · Kettle & Crane",
        "Brands we carry: Pluck Tea, Sloane and Genuine Tea, alongside drinks and teas from small Ontario makers.",
        FILLER, "Wholesale enquiries: makers can write to hello@kettlecrane.example."))
    monkeypatch.setattr(config, "LIVE_STAGES", STAGES + ("specialist", "specialist_check", "specialist_places", "specialist_search", "specialist_fetch", "products_fetch"))
    env.unsupported = ["award-winning"]

    def judge(ctx, system, listing, model_cls, kind, key):
        if kind == "check":
            return {"verdicts": [{"claim_id": i, "verdict": "SUPPORTS", "unsupported_parts": [], "shows_way_in": True,
                                  "reason": "SYNTHETIC"} for i in re.findall(r"- id=(c\d+) \|", listing)]}
        return {"verdicts": [{"id": i, "kind": "UNSUPPORTED_FACT" if any(u in s for u in env.unsupported) else "PROPOSAL"}
                             for i, s in re.findall(r"- id=(s\d+) \|.*?sentence: \"(.*?)\" \|", listing)]}
    monkeypatch.setattr(specialist, "_judge", judge)
    env.spec_script = retail_script
    monkeypatch.setattr(specialist, "run_loop", lambda ctx, spec, row, inp: env.spec_script(
        {t.tool_name: t for t in spec.tools(ctx)}))
    return env


def retail_script(T):
    brands = T["assortment"](url=KC_BRANDS)
    assert "OUTSIDE BRANDS NAMED: Pluck Tea, Sloane, Genuine Tea" in brands and "Wholesale enquiries" in brands
    code = re.search(r"SOURCE (S\w+)", brands).group(1)
    T["check_presence"](lead_name="Kettle & Crane", url=KC_SITE)
    return {"concept_name": "Shelf trial", "concept_line": "Trial the four canned teas on the makers shelf.",
            "partnership_hypothesis": "Kettle & Crane already sells teas from small makers.",
            "possible_activation": "An award-winning tasting night.",
            "shops": [{"name": "Kettle & Crane", "own_site": KC_SITE,
                       "claims": [{"text": "Carries teas from small makers", "source": code,
                                   "quote": "alongside drinks and teas from small Ontario makers"}]}],
            "rejected": [{"name": "Loblaws", "reason_code": "CHAIN", "reason": "A national chain"}],
            "missing": ["Whether it takes consignment"]}


def investigated(client, env, **prefs):
    pid, _ = research_ready(client, env)
    r = client.post(f"/api/projects/{pid}/cities/toronto/methods/retail/investigate", json=prefs)
    assert r.status_code == 200, r.text
    idle(pid)
    return pid, client.get(f"/api/projects/{pid}/dossiers/{r.json()['dossier_id']}").json()


def test_investigate_retail_saves_an_evidenced_dossier(client, spec_env):
    pid, d = investigated(client, spec_env)
    assert d["status"] == "complete", d["error"]
    shop = d["retail"]["shortlist"][0]
    assert [e["brand_or_category"] for e in shop["assortment_evidence"]][:3] == ["Pluck Tea", "Sloane", "Genuine Tea"]
    assert shop["category_overlap"] == "MATCH" and shop["stockist_route"] == "PUBLISHED_FORM"
    ax = shop["axes"]
    assert (ax["fit"], ax["grounding"]["identity"], ax["grounding"]["locality"], ax["operational"]["contact_route"]) == \
        ("BRAND_FACT_MATCH", "VERIFIED", "LOCAL", "OWN_SITE")
    assert d["readiness"]["overall"] == "EVIDENCED" and ax["operational"]["willingness"] == "UNKNOWN"
    assert d["next_action"]["kind"] == "INQUIRE"
    assert {q["state"] for q in d["retail"]["commercial_questions"]} == {"UNKNOWN"}
    rejected = {c["name"]: c["reason_code"] for c in d["candidates"] if c["decision"] == "REJECTED"}
    assert rejected == {"Loblaws": "CHAIN"}
    # P45-R4: the invented "award-winning" activation is dropped; the supported hypothesis stays
    assert d["retail"]["possible_activation"] == "" and d["retail"]["partnership_hypothesis"]
    assert d["check_version"] == "city_check@4" and d["counters"]["page"] <= specialist.SPEC_CAPS["page"]
    assert d["workspace"]["candidates"] and d["stale"] == []


def test_dry_run_shows_the_cost_and_starts_nothing(client, spec_env):
    pid, _ = research_ready(client, spec_env)
    r = client.post(f"/api/projects/{pid}/cities/toronto/methods/retail/investigate?dry_run=1").json()
    assert r == {"cost": {"cap_usd": 1.00, "expected_usd": 0.20}}
    assert not db.q("SELECT 1 FROM specialist_runs WHERE project_id=?", pid)


def test_founder_exclude_rejects_and_include_overrides_a_rejection(client, spec_env):
    pid, d = investigated(client, spec_env, exclude=["Kettle & Crane"])
    assert next(c for c in d["candidates"] if c["name"] == "Kettle & Crane")["reason_code"] == "FOUNDER_EXCLUDED"
    assert d["readiness"]["overall"] == "NOT_YET" and d["next_action"]["kind"] == "INVESTIGATE_FURTHER"


def test_a_shop_whose_categories_dont_match_is_a_category_mismatch(client, spec_env):
    spec_env.web.pages[KC_BRANDS] = (200, html("Brands we carry", "Brands we carry: Mont Marte, Art Spectrum and "
                                               "Winsor & Newton for art supplies, acrylics and easels.", FILLER))

    def script(T):
        code = re.search(r"SOURCE (S\w+)", T["assortment"](url=KC_BRANDS)).group(1)
        T["check_presence"](lead_name="Kettle & Crane", url=KC_SITE)
        return {"shops": [{"name": "Kettle & Crane", "own_site": KC_SITE, "claims": [
            {"text": "Sells art supplies", "source": code, "quote": "for art supplies, acrylics and easels"}]}]}
    spec_env.spec_script = script
    pid, d = investigated(client, spec_env)
    c = next(c for c in d["candidates"] if c["name"] == "Kettle & Crane")
    assert (c["decision"], c["reason_code"]) == ("REJECTED", "CATEGORY_MISMATCH")
    assert d["readiness"]["overall"] == "NOT_YET"


def test_an_unreadable_own_site_is_check_failed_not_no_brands(client, spec_env):
    spec_env.web.fail.add(KC_BRANDS)

    def script(T):
        out = T["assortment"](url=KC_BRANDS)
        assert "Unknown, not absent" in out
        T["check_presence"](lead_name="Kettle & Crane", url=KC_SITE)
        return {"shops": [{"name": "Kettle & Crane", "own_site": KC_SITE}]}
    spec_env.spec_script = script
    pid, d = investigated(client, spec_env)
    failed = [a for a in d["workspace"]["attempts"] if a["outcome"] == "FAILED"]
    assert failed, d["workspace"]["attempts"]
    c = next(c for c in d["candidates"] if c["name"] == "Kettle & Crane")
    assert c["decision"] == "PENDING" and c["reason_code"] == "NO_ASSORTMENT_EVIDENCE"


def test_a_method_your_note_rules_out_and_a_city_without_research_are_refused(client, spec_env):
    pid, _ = research_ready(client, spec_env, context="We won't do pop-ups this year.")
    r = client.post(f"/api/projects/{pid}/cities/toronto/methods/popup/investigate")
    assert r.status_code == 422 and "Edit your note" in r.json()["detail"]
    assert client.post(f"/api/projects/{pid}/cities/vancouver/methods/retail/investigate").status_code == 409


def test_specialist_limits_never_change_slice_4_wording():
    ctx = research.Ctx(None, "p", "toronto", 1, {})
    assert ctx.scope == "this city" and ctx.cr == "p:toronto:1" and ctx.caps is None


# ---- pop-up (SYNTHETIC hire page; Qloo /search answered with a SYNTHETIC placement) ----
HALL = "https://ossingtonhall.example/hire"


def popup_env(env, monkeypatch, twins=1):
    env.web.pages[HALL] = (200, html(
        "Private hire · Ossington Hall",
        "Ossington Hall at 210 Ossington Ave, Toronto is available for private hire for up to 120 guests.",
        FILLER, "We hosted the Toronto Makers Market on 12 September 2026. Write to events@ossingtonhall.example."))
    real = specialist.qloo.get

    def qget(path, params, cassette, stage="", *a, **k):
        if stage != "specialist_places":
            return real(path, params, cassette, stage, *a, **k)
        rows = [{"entity_id": f"Q-HALL-{i}", "name": "Ossington Hall", "location": {"lat": 43.6487 + i / 100, "lon": -79.4206},
                 "properties": {"address": f"{210 + i} Ossington Ave, Toronto"}} for i in range(twins)]
        return {"status": 200, "body": json.dumps({"results": rows}), "headers": {}}
    monkeypatch.setattr(specialist.qloo, "get", qget)

    def script(T):
        code = re.search(r"SOURCE (S\w+)", T["fetch_page"](url=HALL, method="popup")).group(1)
        return {"concept_name": "Weekend pop-up", "concept_line": "A two-day stall during a makers market.",
                "activation_outline": {"format": "Market stall", "duration": "Two days", "footprint": "One table"},
                "venues": [{"name": "Ossington Hall", "own_site": HALL, "event_use": [
                    {"kind": "HIRE_OFFERED", "text": "Offers private hire", "source": code,
                     "quote": "is available for private hire for up to 120 guests"},
                    {"kind": "HOSTED_EVENT", "text": "Hosted a makers market", "source": code,
                     "quote": "We hosted the Toronto Makers Market on 12 September 2026"}]}],
                "rejected": [{"name": "Enercare Centre", "reason_code": "SCALE_MISMATCH", "reason": "A convention hall"}]}
    env.spec_script = script


def test_popup_keeps_a_hire_venue_places_it_and_keeps_a_past_event_past(client, spec_env, monkeypatch):
    popup_env(spec_env, monkeypatch)
    pid, _ = research_ready(client, spec_env)
    r = client.post(f"/api/projects/{pid}/cities/toronto/methods/popup/investigate").json()
    idle(pid)
    d = client.get(f"/api/projects/{pid}/dossiers/{r['dossier_id']}").json()
    assert d["status"] == "complete", d["error"]
    venue = d["popup"]["venues"][0]
    assert {u["kind"] for u in venue["event_use"]} == {"HIRE_OFFERED", "HOSTED_EVENT"}
    assert venue["capacity"]["value"] == 120 and venue["inquiry_route"] == "EMAIL"
    assert venue["location"]["state"] == "PLACED" and venue["location"]["source"] == "QLOO_SEARCH"
    dates = [e for u in venue["event_use"] for e in u["event_dates"]]
    assert dates and dates[0]["date"] == "2026-09-12" and dates[0]["when"] == "PAST"  # computed when viewed (P45-R6)
    req = {x["item"]: x["state"] for x in d["popup"]["requirements"]}
    assert req["CAPACITY"] == "STATED" and req["AVAILABILITY"] == "UNKNOWN" and req["PERMITS"] == "TO_CHECK"
    assert d["proximity"]["placed"][0]["name"] == "Ossington Hall"
    assert d["proximity"]["distances"][0]["label"].endswith("km straight-line")
    assert d["proximity"]["caveat"].startswith("Being near each other")
    scale = next(c for c in d["candidates"] if c["name"] == "Enercare Centre")
    assert scale["reason_code"] == "SCALE_MISMATCH" and scale["decision"] == "REJECTED"


def test_two_places_of_the_same_name_are_ambiguous_never_placed(client, spec_env, monkeypatch):
    popup_env(spec_env, monkeypatch, twins=2)
    pid, _ = research_ready(client, spec_env)
    r = client.post(f"/api/projects/{pid}/cities/toronto/methods/popup/investigate").json()
    idle(pid)
    d = client.get(f"/api/projects/{pid}/dossiers/{r['dossier_id']}").json()
    loc = d["popup"]["venues"][0]["location"]
    assert loc["state"] == "AMBIGUOUS" and len(loc["options"]) == 2 and loc["lat"] is None
    assert d["proximity"]["placed"] == [] and d["proximity"]["unplaced"][0]["state"] == "AMBIGUOUS"


# ---- community (SYNTHETIC organiser page) ----
RUNCLUB = "https://ossingtonrunners.example/"


def test_community_needs_a_quoted_schedule_and_drops_invented_member_traits(client, spec_env):
    spec_env.web.pages[RUNCLUB] = (200, html(
        "Ossington Runners", "Ossington Runners is a free social run club in Toronto. We meet every Saturday 6am at "
        "Trinity Bellwoods Park for a 5k loop.", FILLER, "Contact: hello@ossingtonrunners.example"))
    spec_env.unsupported = ["environmentally aware"]

    def script(T):
        code = re.search(r"SOURCE (S\w+)", T["fetch_page"](url=RUNCLUB, method="community")).group(1)
        T["check_presence"](lead_name="Ossington Runners", url=RUNCLUB)
        return {"concept_name": "Post-run tasting", "concept_line": "Cold tea after the Saturday run.",
                "concept": "Offer cold tea after the Saturday run.",
                "groups": [{"name": "Ossington Runners", "own_site": RUNCLUB, "role": "ORGANISER",
                            "venue": "Trinity Bellwoods Park", "activities": [
                                {"text": "Runs a weekly social run", "source": code,
                                 "quote": "We meet every Saturday 6am at Trinity Bellwoods Park for a 5k loop"}]}],
                "participation_assumptions": ["About 30 runners attend.", "Its members are environmentally aware."]}
    spec_env.spec_script = script
    pid, _ = research_ready(client, spec_env)
    r = client.post(f"/api/projects/{pid}/cities/toronto/methods/community/investigate").json()
    idle(pid)
    d = client.get(f"/api/projects/{pid}/dossiers/{r['dossier_id']}").json()
    assert d["status"] == "complete", d["error"]
    g = d["community"]["groups"][0]
    act = g["activities"][0]
    assert g["role"] == "ORGANISER" and act["recurrence"] == "RECURRING" and act["schedule_quote"] == "every Saturday"
    assert act["next_date"] and act["next_date"] >= "2026-10-26"  # computed when viewed, from the schedule
    texts = [a["text"] for a in d["community"]["participation_assumptions"]]
    assert "Its members are environmentally aware." not in " ".join(texts)  # the Function Well rule
    assert all(a["state"] == "ASSUMED" for a in d["community"]["participation_assumptions"])
    assert next(c for c in d["candidates"] if c["name"] == "Ossington Runners")["decision"] == "KEPT"


# ---- creator (SYNTHETIC article + TikTok oEmbed) ----
ARTICLE = "https://torontolife.example/style-creators"
TT = "https://www.tiktok.com/@ossingtonstyle"


def test_creator_identified_by_two_signals_and_an_undated_figure_is_unused(client, spec_env, monkeypatch):
    oembed = "https://www.tiktok.com/oembed?url=https%3A%2F%2Fwww.tiktok.com%2F%40ossingtonstyle"
    spec_env.web.pages[ARTICLE] = (200, html(
        "Toronto style creators", "Maya Chen (@ossingtonstyle on TikTok) is a Toronto creator who reviews tea and "
        "sparkling drinks from small makers.", FILLER, "Maya Chen has 1.4 million followers, a fan page says.",
        "Pernille (instagram.com/pernille.style) posts outfits."))
    spec_env.web.pages[oembed] = (200, json.dumps({"author_name": "Maya Chen", "author_url": TT}))
    monkeypatch.setattr(config, "LIVE_STAGES", config.LIVE_STAGES + ("creator_profile",))

    def script(T):
        code = re.search(r"SOURCE (S\w+)", T["fetch_page"](url=ARTICLE, method="creator")).group(1)
        assert "PLATFORM NAME: Maya Chen" in T["creator_profile"](url=TT)
        assert "never fetched" in T["creator_profile"](url="https://instagram.com/pernille.style")
        ev = {"source": code, "quote": "Maya Chen (@ossingtonstyle on TikTok) is a Toronto creator"}
        return {"collaboration_concepts": ["A tasting video featuring the four canned teas."],
                "creators": [{"name": "Maya Chen", "profile_url": TT, "evidence": [ev], "location_quote": ev,
                              "content": [{"source": code, "quote": "who reviews tea and sparkling drinks from small makers"}],
                              "figures": [{"source": code, "quote": "Maya Chen has 1.4 million followers, a fan page says"}]},
                             {"name": "Pernille", "profile_url": "https://instagram.com/pernille.style", "evidence": [
                                 {"source": code, "quote": "Pernille (instagram.com/pernille.style) posts outfits"}]}]}
    spec_env.spec_script = script
    pid, _ = research_ready(client, spec_env)
    r = client.post(f"/api/projects/{pid}/cities/toronto/methods/creator/investigate").json()
    idle(pid)
    d = client.get(f"/api/projects/{pid}/dossiers/{r['dossier_id']}").json()
    assert d["status"] == "complete", d["error"]
    maya = next(c for c in d["creator"]["creators"] if c["name"] == "Maya Chen")["creator"]
    assert maya["identity"] == "IDENTIFIED" and {e["kind"] for e in maya["identity_evidence"]} == {"PAGE_NAMES_HANDLE", "OEMBED"}
    assert maya["location"]["state"] == "REPORTED" and maya["metrics"] == []
    assert maya["unused_figures"][0]["value"].startswith("1.4") and maya["unused_figures"][0]["observed_at"] is None
    pern = next(c for c in d["candidates"] if c["name"] == "Pernille")
    assert pern["reason_code"] == "IDENTITY_UNCONFIRMED"
    assert not any("instagram.com" in u for u in spec_env.web.calls)  # never fetched
    assert next(c for c in d["candidates"] if c["name"] == "Maya Chen")["decision"] == "KEPT"


# ---- digital (one structured call; SYNTHETIC outline) ----
def test_digital_downgrades_unsourced_options_drops_figures_and_keeps_the_shipping_rule(client, spec_env):
    def script(T):
        assert T == {}  # no tools: one structured call
        return {"concept_name": "City waitlist", "concept_line": "Invite Toronto tea drinkers to a waitlist.",
                "channel_primary": "Instagram (Meta)", "objective": "PURCHASE", "radius_km_hypothesis": 25,
                "targeting": [{"element": "city + radius", "basis": "PLATFORM_OPTION", "platforms_ref": "google.location_radius"},
                              {"element": "people who recently moved", "basis": "PLATFORM_OPTION", "platforms_ref": "meta.life_events"},
                              {"element": "Luxury travellers", "basis": "QLOO_DERIVED"}],
                "message": "Reach 40,000 tea lovers for $200.",
                "creative_requirements": [{"text": "A square image of the four cans", "platforms_ref": "meta.city_radius"}],
                "primary_metric": "Waitlist sign-ups", "stop_rule": "Stop after two weeks if sign-ups stay flat."}
    spec_env.spec_script = script
    pid, _ = research_ready(client, spec_env)
    r = client.post(f"/api/projects/{pid}/cities/toronto/methods/digital/investigate?dry_run=1").json()
    assert r["cost"]["cap_usd"] == 0.20  # raised from 0.05 (owner, 28 Oct 2026)
    r = client.post(f"/api/projects/{pid}/cities/toronto/methods/digital/investigate").json()
    idle(pid)
    d = client.get(f"/api/projects/{pid}/dossiers/{r['dossier_id']}").json()
    assert d["status"] == "complete", d["error"]
    dg = d["digital"]
    assert dg["channel"] == {"primary": "Google Search", "alternative": None}  # @3: Google Search ads, whatever was named
    bases = {t["element"]: t["basis"] for t in dg["targeting"]}
    assert bases == {"city + radius": "PLATFORM_OPTION", "people who recently moved": "TO_CHECK_IN_ADS_MANAGER",
                     "Luxury travellers": "TO_CHECK_IN_ADS_MANAGER"}
    assert dg["message"]["line"] == ""  # a reach and money figure is slice 5's, never here
    assert dg["objective"] == ("PURCHASE" if specialist.ships_to(db.get_record(d["id"])["input"]) else "SIGN_UP")
    assert dg["measurement"]["stop_rule_label"] == "HYPOTHESIS" and dg["horizon_days"]["state"] == "ASSUMED"
    assert d["readiness"]["grounding"]["method_evidence"] == "SOURCED" and d["next_action"]["kind"] == "DEVELOP"


# ---- Develop (approach_drafter@1; SYNTHETIC draft) ----
def test_develop_an_evidenced_retail_dossier_into_a_draft_then_edit_it(client, spec_env, monkeypatch):
    monkeypatch.setattr(specialist, "draft_call", lambda ctx, prompt: {
        "concept_name": "Makers-shelf trial", "concept_line": "Four canned teas on Kettle & Crane's makers shelf for a month.",
        "activities": ["Send a sample case", "Agree a four-week shelf slot", "Offer a $5 discount code"],
        "success_measure": "Units sold in four weeks", "audience_hypothesis": "Shoppers who already buy small-maker teas",
        "partners": ["Kettle & Crane", "Loblaws"], "products": []})
    pid, d = investigated(client, spec_env)
    assert d["readiness"]["overall"] == "EVIDENCED"
    r = client.post(f"/api/projects/{pid}/dossiers/{d['id']}/develop", json={"base_version": d["version"]})
    assert r.status_code == 200, r.text
    idle(pid)
    dr = client.get(f"/api/projects/{pid}/drafts/{r.json()['draft_id']}").json()
    assert dr["origin"] == "EVIDENCED" and dr["label"] == "EVIDENCED DRAFT"
    assert [p["name"] for p in dr["partners"]] == ["Kettle & Crane"]  # Loblaws was never kept
    assert dr["partners"][0]["participation"] == "NOT_CONFIRMED"
    assert "Offer a $5 discount code" not in dr["activities"]  # money belongs to costing (slice 5)
    tasks = [t["text"] for t in dr["validation_tasks"]]
    assert "wholesale or consignment" in tasks and "Whether it takes consignment" in tasks
    assert {c["state"] for c in dr["costing_inputs"]} == {"UNKNOWN"} and {c["kind"] for c in dr["costing_inputs"]} == {"STOCK", "SHIPPING", "OTHER"}
    assert dr["concept_preview_input"]["participation"] == "not confirmed" and dr["stale"] == []
    e = client.patch(f"/api/projects/{pid}/drafts/{dr['id']}", json={"base_version": dr["version"],
                                                                     "fields": {"success_measure": "Re-order within six weeks"}})
    assert e.status_code == 200 and e.json()["version"] == dr["version"] + 1
    v2 = client.get(f"/api/projects/{pid}/drafts/{dr['id']}").json()
    assert v2["success_measure"] == "Re-order within six weeks" and v2["you_said"] == ["success_measure"]
    assert client.patch(f"/api/projects/{pid}/drafts/{dr['id']}", json={"base_version": dr["version"],
                                                                        "fields": {"success_measure": "x"}}).status_code == 409


def test_a_dossier_without_evidence_still_develops_as_hypothesis_led(client, spec_env, monkeypatch):
    monkeypatch.setattr(specialist, "draft_call", lambda ctx, prompt: {
        "concept_name": "Shelf trial", "concept_line": "A small trial.", "activities": ["Ask about a trial"], "partners": []})
    pid, d = investigated(client, spec_env, exclude=["Kettle & Crane"])
    assert d["readiness"]["overall"] == "NOT_YET"
    r = client.post(f"/api/projects/{pid}/dossiers/{d['id']}/develop", json={}).json()
    idle(pid)
    dr = client.get(f"/api/projects/{pid}/drafts/{r['draft_id']}").json()
    assert dr["origin"] == "HYPOTHESIS_LED" and dr["label"] == "HYPOTHESIS-LED DRAFT" and dr["partners"] == []


def test_a_shop_named_with_different_spacing_is_still_found(client, spec_env):
    def script(T):
        out = retail_script(T)
        out["shops"][0]["name"] = "KettleCrane"  # the page says "Kettle & Crane" (LIVE L45-A: "Martin Patrick 3")
        return out
    spec_env.spec_script = script
    pid, d = investigated(client, spec_env)
    assert not [f for f in d["faults"] if "dropped" in f["reason"]]
    assert d["retail"]["shortlist"] and d["readiness"]["overall"] == "EVIDENCED"


def test_digital_targeting_is_checked_and_bound_before_readiness(client, spec_env, monkeypatch):
    """P45-I1: a valid but unrelated option id can't badge a reach claim; unjudged elements and invented traits drop."""
    real = specialist._judge

    def judge(ctx, system, listing, model_cls, kind, key):
        out = real(ctx, system, listing, model_cls, kind, key)
        if kind == "prose":  # the checker omits its verdict for one element, and flags an invented trait
            ids = dict(re.findall(r"- id=(s\d+) \|.*?sentence: \"(.*?)\" \|", listing))
            out["verdicts"] = [v for v in out["verdicts"] if ids.get(v["id"]) != "women"]
            for v in out["verdicts"]:
                if "eco-minded" in ids.get(v["id"], ""):
                    v["kind"] = "UNSUPPORTED_FACT"
        return out
    monkeypatch.setattr(specialist, "_judge", judge)
    spec_env.spec_script = lambda T: {
        "channel_primary": "Meta", "objective": "SIGN_UP",
        "targeting": [{"element": "Guaranteed reach 100000 for $5", "basis": "PLATFORM_OPTION", "platforms_ref": "meta.age"},
                      {"element": "women", "basis": "PLATFORM_OPTION", "platforms_ref": "meta.gender"},
                      {"element": "Sparkling tea fans", "basis": "PLATFORM_OPTION", "platforms_ref": "meta.interests"}],
        "message": "Sparkling cold-brewed tea, now on a waitlist.",
        "primary_metric": "Sign-ups from eco-minded tea lovers who already buy organic"}
    pid, _ = research_ready(client, spec_env)
    r = client.post(f"/api/projects/{pid}/cities/toronto/methods/digital/investigate").json()
    idle(pid)
    d = client.get(f"/api/projects/{pid}/dossiers/{r['dossier_id']}").json()
    assert d["status"] == "complete", d["error"]
    elements = {t["element"]: t["basis"] for t in d["digital"]["targeting"]}
    assert "Guaranteed reach 100000 for $5" not in elements  # money and reach: refused before anything else
    assert "women" not in elements  # unjudged: dropped (fail closed)
    assert elements == {"Sparkling tea fans": "TO_CHECK_IN_ADS_MANAGER"}  # a Meta option can't badge the Google Search channel
    assert d["digital"]["measurement"]["primary_metric"] == ""  # the invented audience trait
    assert d["readiness"]["overall"] == "HYPOTHESIS_LED" and d["readiness"]["grounding"]["method_evidence"] == "TO_CHECK"


@pytest.mark.parametrize("quote, author, expect", [
    ("Maya Chen (@ossingtonstyle on TikTok) is a Toronto creator", "Maya Chen", "IDENTIFIED"),  # agreeing signals
    ("Maya Chen is a Toronto creator who reviews tea", "Maya Chen", "POSSIBLE"),  # name only: no page signal
    ("Maya Chen (@ossingtonstyle on TikTok) is a Toronto creator", "Jordan Lee", "POSSIBLE"),  # platform names someone else
    ("Maya Chen (@mayachen.eats on TikTok) is a Toronto creator", "Maya Chen", "POSSIBLE"),  # the page names another handle
])
def test_creator_identity_needs_the_same_person_and_profile(client, spec_env, monkeypatch, quote, author, expect):
    """P45-I4: name-only, mismatched-name and unrelated-handle evidence never reach IDENTIFIED."""
    article = "https://torontolife.example/style-creators"
    oembed = "https://www.tiktok.com/oembed?url=https%3A%2F%2Fwww.tiktok.com%2F%40ossingtonstyle"
    spec_env.web.pages[article] = (200, html("Toronto style creators", quote + " and reviews tea and sparkling drinks from "
                                             "small makers.", FILLER))
    spec_env.web.pages[oembed] = (200, json.dumps({"author_name": author, "author_url": "https://www.tiktok.com/@ossingtonstyle"}))
    monkeypatch.setattr(config, "LIVE_STAGES", config.LIVE_STAGES + ("creator_profile",))

    def script(T):
        code = re.search(r"SOURCE (S\w+)", T["fetch_page"](url=article, method="creator")).group(1)
        T["creator_profile"](url="https://www.tiktok.com/@ossingtonstyle")
        ev = {"source": code, "quote": quote}
        return {"creators": [{"name": "Maya Chen", "profile_url": "https://www.tiktok.com/@ossingtonstyle", "evidence": [ev],
                              "location_quote": ev, "content": [{"source": code, "quote": "reviews tea and sparkling drinks from small makers"}]}]}
    spec_env.spec_script = script
    pid, _ = research_ready(client, spec_env)
    r = client.post(f"/api/projects/{pid}/cities/toronto/methods/creator/investigate").json()
    idle(pid)
    d = client.get(f"/api/projects/{pid}/dossiers/{r['dossier_id']}").json()
    maya = next(c for c in d["creator"]["creators"] if c["name"] == "Maya Chen")["creator"]
    assert maya["identity"] == expect, maya["identity_evidence"]


def test_changed_dependencies_flag_the_dossier_and_its_draft_and_unused_ones_dont(client, spec_env, monkeypatch):
    """P45-R5 / P45-I2 controls through the real dossier and draft views."""
    monkeypatch.setattr(specialist, "draft_call", lambda ctx, prompt: {
        "concept_name": "Shelf trial", "concept_line": "A month on the makers shelf.", "activities": ["Send a sample case"],
        "partners": ["Kettle & Crane"]})
    pid, _ = research_ready(client, spec_env)
    used = db.put_record("product", pid, {"title": "Hibiscus Berry Tea", "product_url": "http://tidewell.example/products/hibiscus",
                                          "snapshot_id": "src_x", "price_text": None, "category_terms": ["drinks"],
                                          "materials": [], "discovered_via": "BRAND_PAGES", "mcp": None, "vc": "F"}, rid="pro_used")
    other = db.put_record("product", pid, {**{k: used[k] for k in ("product_url", "snapshot_id", "price_text", "discovered_via", "mcp", "vc")},
                                           "title": "Gift card", "category_terms": [], "materials": []}, rid="pro_other")
    db.put_record("product_research", pid, {"route": "BRAND_PAGES", "mcp": {"state": "DISABLED"}, "queries": [],
                                            "products": [{"id": used["id"], "version": used["version"]}], "attempts": [], "at": db.now()},
                  rid=f"prr_{pid}")
    r = client.post(f"/api/projects/{pid}/cities/toronto/methods/retail/investigate", json={}).json()
    idle(pid)
    did = r["dossier_id"]
    d = client.get(f"/api/projects/{pid}/dossiers/{did}").json()
    assert d["stale"] == [] and [p["title"] for p in d["products"]] == ["Hibiscus Berry Tea"]
    rid = client.post(f"/api/projects/{pid}/dossiers/{did}/develop", json={}).json()["draft_id"]
    idle(pid)
    stale = lambda: (client.get(f"/api/projects/{pid}/dossiers/{did}").json()["stale"],  # noqa: E731
                     client.get(f"/api/projects/{pid}/drafts/{rid}").json()["stale"])
    db.put_record("product", pid, {**{k: v for k, v in db.get_record("pro_other").items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")},
                                   "materials": ["paper"]}, rid="pro_other")  # an unused product: flags nothing
    assert stale() == ([], [])
    claim = db.get_record(d["facts"][0])
    db.put_record("city_claim", pid, {**{k: v for k, v in claim.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")},
                                      "state": "SOURCES_DIFFER"}, rid=claim["id"])  # same text, new support state
    assert "A checked claim this used changed" in stale()[0] and "A checked claim this used changed" in stale()[1]
    sells = next(f for f in db.get_record(d["id"])["input"]["research_input"]["facts"] if f["label"] == "What you sell")
    fact = db.get_record(sells["claim_id"])
    db.put_record("claim", pid, {**{k: v for k, v in fact.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")},
                                 "state": "TO_CHECK"}, rid=fact["id"])  # identical text
    assert "A brand fact this used changed" in stale()[0]
    db.put_record("product", pid, {**{k: v for k, v in db.get_record("pro_used").items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")},
                                   "materials": ["hibiscus"]}, rid="pro_used")  # a field the prompts read
    assert "A product this used changed" in stale()[0]
    lead_key = next(iter(db.get_record(d["id"])["consumed"]["lead_locations"]))
    loc = db.get_record(f"loc_{specialist._k(pid, 'toronto', lead_key)}")
    db.put_record("lead_location", pid, {**{k: v for k, v in loc.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")},
                                         "state": "PLACED", "lat": 43.65, "lon": -79.42, "source": "YOU_CHOSE"}, rid=loc["id"])
    assert "A location changed" in stale()[1]  # the draft too, not only the dossier
    del other


def test_a_not_yet_draft_carries_pending_candidates_gaps(client, spec_env, monkeypatch):
    """P45-I5 / O8: pending candidates' unknowns become tasks without relying on the model's `missing`."""
    monkeypatch.setattr(specialist, "draft_call", lambda ctx, prompt: {"concept_name": "Run partnership",
                                                                       "concept_line": "Cold tea after a run.", "activities": ["Ask"]})
    spec_env.web.pages[RUNCLUB] = (200, html("Ossington Runners", "Ossington Runners is a free social run club in Toronto. "
                                             "We hold regular run club meetings for all abilities.", FILLER))

    def script(T):
        code = re.search(r"SOURCE (S\w+)", T["fetch_page"](url=RUNCLUB, method="community")).group(1)
        return {"groups": [{"name": "Ossington Runners", "own_site": RUNCLUB, "role": "ORGANISER", "activities": [
                    {"text": "Holds regular meetings", "source": code, "quote": "We hold regular run club meetings for all abilities"}]},
                           {"name": "Phantom Makerspace", "role": "ORGANISER"}],
                "missing": ["Exact schedule for Phantom Makerspace"]}
    spec_env.spec_script = script
    pid, _ = research_ready(client, spec_env)
    r = client.post(f"/api/projects/{pid}/cities/toronto/methods/community/investigate").json()
    idle(pid)
    d = client.get(f"/api/projects/{pid}/dossiers/{r['dossier_id']}").json()
    assert d["readiness"]["overall"] == "NOT_YET"
    rid = client.post(f"/api/projects/{pid}/dossiers/{d['id']}/develop", json={}).json()["draft_id"]
    idle(pid)
    tasks = [t["text"] for t in client.get(f"/api/projects/{pid}/drafts/{rid}").json()["validation_tasks"]]
    assert "Ossington Runners: no recurring schedule quoted" in tasks
    assert any(t.startswith("Ossington Runners: Identity not verified") or t.startswith("Ossington Runners: ") for t in tasks)
    assert not any("Phantom Makerspace" in t for t in tasks)  # a candidate the validator dropped isn't a task


def test_founder_preferences_persist_and_can_be_cleared_on_purpose(client, spec_env):
    """P45-I10: exclude → default re-investigate → still excluded; a deliberate clear restores it."""
    pid, d = investigated(client, spec_env, exclude=["Kettle & Crane"])
    assert next(c for c in d["candidates"] if c["name"] == "Kettle & Crane")["reason_code"] == "FOUNDER_EXCLUDED"
    r = client.post(f"/api/projects/{pid}/cities/toronto/methods/retail/investigate", json={})  # "Investigate further"
    idle(pid)
    d2 = client.get(f"/api/projects/{pid}/dossiers/{r.json()['dossier_id']}").json()
    assert d2["version"] == 2 and next(c for c in d2["candidates"] if c["name"] == "Kettle & Crane")["reason_code"] == "FOUNDER_EXCLUDED"
    v = client.get(f"/api/projects/{pid}/cities/toronto/methods/retail").json()["preferences"]
    e = client.patch(f"/api/projects/{pid}/workspaces/{v['workspace_id']}", json={"base_version": v["version"], "exclude": []})
    assert e.status_code == 200
    assert "Your include or exclude list changed" in client.get(f"/api/projects/{pid}/dossiers/{d2['id']}").json()["stale"]
    client.post(f"/api/projects/{pid}/cities/toronto/methods/retail/investigate", json={})
    idle(pid)
    d3 = client.get(f"/api/projects/{pid}/dossiers/{d2['id']}").json()
    assert next(c for c in d3["candidates"] if c["name"] == "Kettle & Crane")["decision"] == "KEPT"


def test_an_include_overrides_a_rejection_and_says_so(client, spec_env):
    spec_env.web.pages[KC_BRANDS] = (200, html("Brands we carry", "Brands we carry: Mont Marte, Art Spectrum and "
                                               "Winsor & Newton for art supplies, acrylics and easels.", FILLER))

    def script(T):
        code = re.search(r"SOURCE (S\w+)", T["assortment"](url=KC_BRANDS)).group(1)
        T["check_presence"](lead_name="Kettle & Crane", url=KC_SITE)
        return {"shops": [{"name": "Kettle & Crane", "own_site": KC_SITE, "claims": [
            {"text": "Sells art supplies", "source": code, "quote": "for art supplies, acrylics and easels"}]}]}
    spec_env.spec_script = script
    pid, d = investigated(client, spec_env, include=["Kettle & Crane"])
    c = next(c for c in d["candidates"] if c["name"] == "Kettle & Crane")
    assert (c["decision"], c["reason_code"], c["override"]) == ("KEPT", "CATEGORY_MISMATCH", True)


def test_choosing_an_ambiguous_shop_rebuilds_the_projection(client, spec_env, monkeypatch):
    """P45-I9: retail choice; afterwards no contradictory AMBIGUOUS + PLACED state, and no research call."""
    real = specialist.qloo.get
    searches = []

    def qget(path, params, cassette, stage="", *a, **k):
        if stage != "specialist_places":
            return real(path, params, cassette, stage, *a, **k)
        searches.append(params["query"])
        rows = [{"entity_id": f"Q-KC-{i}", "name": "Kettle & Crane", "location": {"lat": 43.645 + i / 50, "lon": -79.42},
                 "properties": {"address": f"{174 + i} Ossington Ave, Toronto"}} for i in range(2)]
        return {"status": 200, "body": json.dumps({"results": rows}), "headers": {}}
    monkeypatch.setattr(specialist.qloo, "get", qget)
    pid, d = investigated(client, spec_env)
    row = d["retail"]["shortlist"][0]
    assert row["location"]["state"] == "AMBIGUOUS" and d["proximity"]["unplaced"][0]["state"] == "AMBIGUOUS"
    before = len(searches)
    lead_id = next(c["lead_id"] for c in d["candidates"] if c["name"] == "Kettle & Crane")
    r = client.put(f"/api/projects/{pid}/leads/{lead_id}/location", json={"base_version": row["location"]["version"], "option": 0})
    assert r.status_code == 200, r.text
    d2 = client.get(f"/api/projects/{pid}/dossiers/{d['id']}").json()
    row2 = d2["retail"]["shortlist"][0]
    assert row2["location"]["state"] == "PLACED" and row2["location"]["source"] == "YOU_CHOSE"
    assert [x["name"] for x in d2["proximity"]["placed"]] == ["Kettle & Crane"] and d2["proximity"]["unplaced"] == []
    assert row2["distance_km"] is not None and "A location changed" in d2["stale"]
    assert len(searches) == before  # no research call
