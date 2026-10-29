"""Qloo-anchored community and creator investigations (owner, 27 Oct 2026). SYNTHETIC Qloo answers for the city target
(heatmap, audience URNs, audience-signalled places, taste tags) at the HTTP boundary, SYNTHETIC pages and grounding; the
specialist's tool loop is scripted over its real tools. Proves query anchoring, read-before-drop, LEADs that are shown
but never evidence, and the "Why: Qloo" lines; it says nothing about model quality (the live cases do)."""
import json
import re
from types import SimpleNamespace

import pytest
from test_markets import idle, markets_env, scanned  # noqa: F401
from test_slice4 import FILLER, KC_SITE, STAGES, env, grounding, html, research_ready  # noqa: F401
from test_slice45_runner import spec_env  # noqa: F401

from landfall import config, db, markets, qloo, research, specialist

TOR = markets.CITIES["toronto"]
WATCHCLUB = "https://ossingtonwatchclub.example/"
VENUES = [("Q-HALL", "Ossington Hall", 0.99, 0.002, "Ossington Strip"), ("Q-PARK", "Trinity Bellwoods Park", 0.97, 0.004, "Trinity Bellwoods"),
          ("Q-ROM", "Royal Ontario Museum", 0.95, 0.03, "Yorkville")]


def ok(body):
    return SimpleNamespace(status_code=200, headers={"content-type": "application/json"}, text=json.dumps(body))


@pytest.fixture
def target_env(spec_env, monkeypatch):
    """The slice-4.5 env plus SYNTHETIC answers for the target's Qloo requests; every other request goes to FakeQloo."""
    fake = qloo.httpx.get
    calls = []

    def get(url, params=None, **k):
        p, op = dict(params or {}), url.rsplit("/", 1)[-1]
        kind = p.get("filter.type")
        if op == "audiences":
            calls.append("urn")
            n = {"Watches": 1, "Surfing": 2, "Hiking": 3}[p["filter.query"]]
            return ok({"results": {"audiences": [{"id": f"urn:audience:hobbies_and_interests:{p['filter.query'].lower()}",
                                                  "entity_id": f"SYN-AUD-{n}", "name": p["filter.query"]}]}})
        if kind == "urn:heatmap":
            calls.append("heatmap")
            assert p["filter.location.query"] == "Toronto" and p["signal.interests.entities"] == "SYN-BRAND"
            return ok({"success": True, "results": {"heatmap": [
                {"location": {"latitude": TOR["lat"] + d, "longitude": TOR["lon"], "geohash": f"dpz{i}"},
                 "query": {"affinity": a, "popularity": a}} for i, (d, a) in enumerate([(0.003, 1.0), (0.005, 0.9), (0.2, 0.8)])]}})
        if kind == "urn:entity:place" and "signal.demographics.audiences" in p:
            calls.append("places")
            assert "signal.interests.entities" not in p and p["signal.demographics.audiences"].startswith("urn:audience:")
            return ok({"success": True, "results": {"entities": [
                {"entity_id": f"{q}-{p['signal.demographics.audiences'][-3:]}", "name": n, "location": {"lat": TOR["lat"] + d, "lon": TOR["lon"]},
                 "properties": {"address": f"{i} Ossington Ave, Toronto", "website": f"https://venue{i}.example/",
                                "geocode": {"name": hood, "city": "Toronto"}}, "query": {"affinity": a}}
                for i, (q, n, a, d, hood) in enumerate(VENUES, 1)]}})
        if kind == "urn:tag":
            calls.append("tags")
            assert p["signal.location"] == f"POINT({TOR['lon']} {TOR['lat']})"
            return ok({"success": True, "results": {"tags": [
                {"tag_id": "urn:tag:genre:music:indie", "name": " indie", "subtype": "urn:tag:genre:music", "query": {"affinity": 1}},
                {"tag_id": "urn:tag:genre:person:actor", "name": "Actor", "subtype": "urn:tag:genre:person", "query": {"affinity": 1}},
                {"tag_id": "urn:tag:genre:place:coffee_shop", "name": "Coffee shop", "subtype": "urn:tag:genre:place", "query": {"affinity": 0.9}}]}})
        return fake(url, params, **k)
    monkeypatch.setattr(qloo.httpx, "get", get)
    monkeypatch.setattr(config, "LIVE_STAGES", config.LIVE_STAGES + ("specialist_qloo",))
    spec_env.target_calls = calls
    spec_env.prompts = []
    monkeypatch.setattr(specialist, "run_loop", lambda ctx, spec, row, inp: (
        spec_env.prompts.append(spec.prompt(inp)), spec_env.spec_script({t.tool_name: t for t in spec.tools(ctx)}))[1])
    spec_env.web.pages[WATCHCLUB] = (200, html(
        "Ossington Watch Club", "Ossington Watch Club is a Toronto collectors' group. We meet on the first Thursday of each "
        "month at Ossington Hall.", FILLER))
    monkeypatch.setattr(research, "ground", grounding(
        [WATCHCLUB], answer="Ossington Watch Club and Le 6AM Run Club meet near Ossington; Phoenix Runners too."))
    return spec_env


def community_script(T):
    T["web_search"](query="watches club, group or meetup in Ossington Strip", method="community")
    return {"concept_name": "Collectors' evening", "concept_line": "A tasting at the watch club's monthly meeting.",
            "concept": "Bring the canned teas to a watch club meeting.",
            "groups": [{"name": "Ossington Watch Club", "own_site": WATCHCLUB, "role": "ORGANISER"},
                       {"name": "Ossington Hall", "qloo_ref": "[Q1-1]", "role": "VENUE"}],
            "leads": [{"name": "Le 6AM Run Club", "url": "https://made-up.example/"}, {"name": "Invented Collective"},
                      {"name": "Toronto watches clubs"}],
            "rejected": [{"name": "Phoenix Runners", "reason_code": "NO_RECURRING_ACTIVITY", "reason": "site unavailable"}]}


def investigate(client, env, method, script):
    env.spec_script = script
    pid = env.pid if getattr(env, "pid", None) else research_ready(client, env)[0]
    env.pid = pid
    r = client.post(f"/api/projects/{pid}/cities/toronto/methods/{method}/investigate").json()
    idle(pid)
    d = client.get(f"/api/projects/{pid}/dossiers/{r['dossier_id']}").json()
    assert d["status"] == "complete", d["error"]
    return pid, d


def test_qloo_target_anchors_the_searches_and_names_neighbourhoods(client, target_env):
    pid, d = investigate(client, target_env, "community", community_script)
    prompt = target_env.prompts[0]
    assert "QLOO TARGET for Toronto" in prompt and "most related first: Watches, Surfing, Hiking" in prompt
    assert "taste concentrates (Qloo heatmap): Ossington Strip, Trinity Bellwoods" in prompt  # the cells' nearest Qloo places
    assert "[Q1-1] Ossington Hall · Ossington Strip" in prompt
    searches = prompt.split("SEARCHES TO RUN FIRST")[1]
    assert "- watches club, group or meetup in Ossington Strip" in searches
    assert "- surfing club, group or meetup in Trinity Bellwoods" in searches
    assert "- indie event organizer in Ossington Strip" in searches and "Actor" not in prompt  # a person isn't a theme
    assert "lovers" not in searches  # no category query without a category
    q = d["qloo"]
    assert [c["hood"] for c in q["cells"][:2]] == ["Ossington Strip", "Trinity Bellwoods"] and "hood" not in q["cells"][2]
    assert q["caveat"] == "a cultural relationship, not demand" and "uuid" not in json.dumps(q["audiences"])
    assert d["prompt_version"] == "community_specialist@3"
    assert specialist.SPECS["community"].caps["search"] == 6 and specialist.SPECS["community"].caps["page"] == 10
    assert "about 8 turns" in specialist.SPECS["creator"].system and "about 6 turns" in specialist.SPECS["retail"].system


def test_read_before_drop_and_leads_are_shown_but_never_evidence(client, target_env):
    pid, d = investigate(client, target_env, "community", community_script)
    assert WATCHCLUB in target_env.web.calls  # the agent never read it: validation read it before it could be dropped
    groups = {g["name"]: g for g in d["community"]["groups"]}
    assert set(groups) == {"Ossington Watch Club", "Ossington Hall"}
    assert groups["Ossington Watch Club"]["why_qloo"] == "Watches audience × Ossington Strip → Ossington Watch Club"
    assert groups["Ossington Hall"]["location"]["state"] == "PLACED" and groups["Ossington Hall"]["location"]["source"] == "QLOO"
    leads = d["community"]["leads"]
    assert [x["name"] for x in leads] == ["Phoenix Runners", "Le 6AM Run Club"]
    assert all(x["state"] == "LEAD" and x["found"] == "named in search" for x in leads)
    assert leads[0]["reason"].startswith("its page couldn't be read")  # an unread page is unknown, not a rejection
    assert leads[1]["url"] is None  # an address this run never saw is not kept
    assert leads[1]["why_qloo"] == "Watches audience × Ossington Strip → Le 6AM Run Club"  # the anchored search that named it
    assert any(f["reason"].startswith("Invented Collective: not named") for f in d["faults"])  # never fabricated
    assert any(f["reason"].startswith("Toronto watches clubs: a category") for f in d["faults"])  # not a named group
    names = {c["name"] for c in d["candidates"]}
    assert "Le 6AM Run Club" not in names and "Phoenix Runners" not in names  # leads are not candidates
    assert not [c for c in db.records("city_claim", pid) if c["subject_name"] in ("Le 6AM Run Club", "Phoenix Runners")]
    assert d["readiness"]["overall"] == "NOT_YET" and d["next_action"]["text"].startswith("Confirm Phoenix Runners")


def test_why_qloo_lines_carry_the_real_values(client, target_env):
    pid, d = investigate(client, target_env, "community", community_script)
    why = {x["about"]: x["text"] for x in d["qloo_why"]}
    assert d["qloo_why"][0]["about"] == "group"  # most specific first: the plan card shows this one
    assert why["venue"] == "Ossington Hall: #1 of 3 places Qloo relates to the Watches audience near Toronto"
    assert why["neighbourhood"].startswith("Tidewell's taste concentrates in Ossington Strip and Trinity Bellwoods (Qloo heatmap")
    assert why["audiences"] == "Qloo relates people who like Tidewell to Watches (#1 of 3), Surfing (#2 of 3) and Hiking (#3 of 3)"
    assert why["themes"] == "Local themes Qloo relates to Tidewell in Toronto: indie, Coffee shop"
    assert all(x["refs"] == ["QLOO"] for x in d["qloo_why"])
    assert not any(re.search(r"\b(demand|sales|customers want)\b", x["text"]) for x in d["qloo_why"] if x["about"] != "city")
    assert db.get_record(d["id"])["why_it_fits"] == d["qloo_why"]  # the plan's method card reads why_it_fits


def test_creator_shares_the_target_and_keeps_an_unconfirmed_creator_as_a_lead(client, target_env):
    investigate(client, target_env, "community", community_script)
    before = list(target_env.target_calls)

    def script(T):
        T["web_search"](query="watches content creator or blogger", method="creator")
        return {"collaboration_concepts": ["A watch-and-tea video."],
                "creators": [{"name": "Ossington Watch Club", "profile_url": WATCHCLUB}],
                "leads": [{"name": "Le 6AM Run Club", "audience": "Surfing"}]}  # a model-chosen anchor is never used
    pid, d = investigate(client, target_env, "creator", script)
    assert target_env.target_calls == before  # one target per (project, city): the creator run sent no Qloo request
    assert "QLOO TARGET for Toronto" in target_env.prompts[-1] and "content creator or blogger" in target_env.prompts[-1]
    leads = d["creator"]["leads"]
    assert [x["name"] for x in leads] == ["Ossington Watch Club", "Le 6AM Run Club"]  # not a profile URL: a lead, not dropped
    assert leads[1]["why_qloo"] == "Watches audience → Le 6AM Run Club"
    assert d["readiness"]["overall"] == "NOT_YET" and d["next_action"]["text"].startswith("Confirm Ossington Watch Club")
    assert d["qloo"]["cells"] and d["prompt_version"] == "creator_specialist@3"


def test_without_a_target_the_prompt_is_unchanged(client, spec_env):
    """A target that couldn't be built (here: no recording for its stage) leaves the prompt as before and the run going."""
    seen = []
    spec_env.spec_script = lambda T: {"groups": []}
    pid, _ = research_ready(client, spec_env)
    import landfall.specialist as sp
    real = sp.prompt_of
    sp_prompt = lambda inp, spec: (seen.append(real(inp, spec)), seen[-1])[1]  # noqa: E731
    spec_env.mp.setattr(sp, "prompt_of", sp_prompt)
    spec_env.mp.setattr(sp, "run_loop", lambda ctx, spec, row, inp: (sp.prompt_of(inp, spec), {"groups": []})[1])
    r = client.post(f"/api/projects/{pid}/cities/toronto/methods/community/investigate").json()
    idle(pid)
    d = client.get(f"/api/projects/{pid}/dossiers/{r['dossier_id']}").json()
    assert d["status"] == "complete" and "QLOO TARGET" not in seen[0] and d["qloo"] is None and d["qloo_why"] == []
    assert db.get_record(f"qt_{pid}_toronto")["state"] == "failed"  # failed, never shown as absence


def test_a_list_sent_as_a_json_string_is_read_as_the_list():
    """LIVE creator @3 (27 Oct 2026): `creators` arrived as a JSON string and validation failed until the model limit."""
    sub = specialist.CreatorDossierIn.model_validate({"creators": json.dumps([{"name": "Oncle Vov", "profile_url": "https://x.example"}]),
                                                      "collaboration_concepts": json.dumps(["A video"]), "leads": None})
    assert sub.creators[0].name == "Oncle Vov" and sub.collaboration_concepts == ["A video"] and sub.leads == []
    assert specialist.no_qloo_name({"concept_name": "Qloo Coffee Community Engagement", "concept_line": "Qloo's racing fans"}) == \
        {"concept_name": "Coffee Community Engagement", "concept_line": "racing fans"}
