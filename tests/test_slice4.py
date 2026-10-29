"""Slice 4: city research (BUILD-PLAN slice 4 tests R1–R6; CONTRACTS B6 · Slice 4).

SYNTHETIC unless they read a capture (`captured`, skipped in a fresh clone). Integration tests replay the synthetic
Tidewell brand read; Qloo is SYNTHETIC at the HTTP boundary (FakeQloo), pages at the fetch boundary (Web), grounding and
the checker at the model boundary (research.ground / research.judge), and the city agent is a script that calls the real
tools (research.run_agent replaced), so caps, checkpoints, snapshots and the validator run as built.
"""
import json
import re
import time
from types import SimpleNamespace

import pytest
from test_markets import LIVE, idle, markets_env, scanned  # noqa: F401

from landfall import config, db, fetch, markets, research, runner

STAGES = LIVE + ("research", "research_search", "research_fetch", "research_places", "research_urns", "research_check")
CASES = config.ROOT / "live-captures-private" / "cases"


def html(title: str, *paras: str) -> str:
    body = "".join(f"<p>{p}</p>" for p in paras)
    return f"<html><head><title>{title}</title></head><body><main><article><h1>{title}</h1>{body}</article></main></body></html>"


FILLER = ("The shop is open six days a week and its staff are happy to talk about the makers on the shelves. "
          "Visitors often come in after work, and the window display changes with the seasons.")


class Web:
    """SYNTHETIC pages: url → (status, html). Robots answers 404 (no restriction stated)."""

    def __init__(self):
        self.pages: dict[str, tuple[int, str]] = {}
        self.calls: list[str] = []
        self.fail: set[str] = set()

    def __call__(self, url, **_):  # fetch.py passes the run's cookie jar
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return {"url": url, "status": 404, "body": "", "hops": []}
        if url in self.fail:
            return {"url": url, "status": 0, "error": "ConnectError", "hops": []}
        st, body = self.pages.get(url, (404, ""))
        return {"url": url, "status": st, "content_type": "text/html", "hops": [], "body": body}

    def loads(self):
        return [u for u in self.calls if not u.endswith("/robots.txt")]


class Checker:
    """SYNTHETIC city_check@1: SUPPORTS unless a rule names a substring of the claim line."""

    def __init__(self):
        self.calls, self.rules, self.fail = 0, {}, False

    def __call__(self, ctx, listing):
        self.calls += 1
        if self.fail:
            raise RuntimeError("SYNTHETIC checker outage")
        out = []
        for line in listing.split("\n"):
            m = re.match(r"- id=(c\d+) \|", line)
            if not m:
                continue
            verdict, contra = "SUPPORTS", None
            for k, v in self.rules.items():
                if k in line:
                    verdict, contra = v if isinstance(v, tuple) else (v, None)
            out.append(research.CityVerdict(claim_id=m.group(1), verdict=verdict, contradicts=contra, reason="SYNTHETIC",
                                            shows_way_in=verdict == "SUPPORTS"))
        return research.CityVerdicts(verdicts=out)


class Prose:
    """SYNTHETIC prose check: UNSUPPORTED_FACT when a rule's substring is in the sentence, else PROPOSAL."""

    def __init__(self):
        self.unsupported: list[str] = []
        self.calls = 0

    def __call__(self, ctx, listing):
        self.calls += 1
        out = []
        for line in listing.split("\n"):
            m = re.match(r"- id=(s\d+) \|.*?sentence: \"(.*?)\" \|", line)
            if m:
                bad = any(u in m.group(2) for u in self.unsupported)
                out.append(research.ProseVerdict(id=m.group(1), kind="UNSUPPORTED_FACT" if bad else "PROPOSAL"))
        return research.ProseVerdicts(verdicts=out)


def grounding(cites, answer="Some leads."):
    def ground(ctx, question):
        return {"output": {"message": {"content": [{"text": answer}] + [
            {"citationsContent": {"citations": [{"location": {"web": {"url": u, "domain": fetch.site_of(u)}}}]}} for u in cites]}}}
    return ground


@pytest.fixture
def env(markets_env, monkeypatch):
    web, checker, prose = Web(), Checker(), Prose()
    monkeypatch.setattr(research, "judge_prose", prose)
    monkeypatch.setattr(config, "LIVE_STAGES", STAGES)
    monkeypatch.setattr(fetch, "_live_get", web)
    monkeypatch.setattr(fetch, "_live_render", lambda url: {"url": url, "status": 0, "error": "not rendered in tests"})
    monkeypatch.setattr(research, "judge", checker)
    monkeypatch.setattr(research, "ground", grounding([]))
    research._robots.clear()
    ns = SimpleNamespace(web=web, checker=checker, prose=prose, qloo=markets_env.qloo, scripts={}, agent_calls=[], mp=monkeypatch)

    def run_agent(ctx, row):
        ns.agent_calls.append(ctx.cid)
        tools = {t.tool_name: t for t in research.make_tools(ctx)}
        return ns.scripts.get(ctx.cid, default_script)(ctx, tools)
    monkeypatch.setattr(research, "run_agent", run_agent)
    return ns


def default_script(ctx, T):
    T["qloo_places"](method="retail")
    return {"learned": "", "approaches": []}


KC = "https://torontomag.example/makers"
KC_SITE = "https://kettlecrane.example/"


def toronto_pages(web):
    web.pages[KC] = (200, html("Toronto makers' shelves",
                               "Kettle & Crane, on Ossington Avenue in Toronto, keeps a makers shelf that stocks outside brands from small labels.",
                               FILLER, "Ossington Run Collective meets every Saturday at Ossington and Dundas in Toronto for a social run.",
                               "Write to the editor at editor@torontomag.example with tips."))
    web.pages[KC_SITE] = (200, html("Kettle & Crane",
                                    "Kettle & Crane is a neighbourhood grocer at 174 Ossington Ave, Toronto, ON M6J 2Z7.",
                                    FILLER, "Questions? Email hello@kettlecrane.example or call 416-555-0134."))


def toronto_script(ctx, T):
    T["qloo_places"](method="retail")
    T["qloo_places"](method="popup")
    T["web_search"](query="independent shops that stock makers", method="retail")
    text = T["fetch_page"](url=KC, method="retail")
    code = re.search(r"SOURCE (S\w+)", text).group(1)
    site = T["check_presence"](lead_name="Kettle & Crane", url=KC_SITE)
    scode = re.search(r"SOURCE (S\w+)", site).group(1)
    return {
        "learned": "Kettle & Crane keeps a makers shelf for outside brands (claim 1).", "learned_claims": [1],
        "facts_used": ["F1"],
        "leads": [{"ref": "L1", "name": "Kettle & Crane", "kind": "shop", "website": KC_SITE},
                  {"ref": "L2", "name": "Ossington Run Collective", "kind": "group"}],
        "claims": [{"subject": "L1", "method": "retail", "text": "Stocks outside brands on a makers shelf", "source": code,
                    "quote": "keeps a makers shelf that stocks outside brands from small labels"},
                   {"subject": "L2", "method": "community", "text": "Meets on Saturdays for a run", "source": code,
                    "quote": "Ossington Run Collective meets every Saturday at Ossington and Dundas"},
                   {"subject": "L1", "method": "retail", "text": "Is at 174 Ossington Ave", "source": scode,
                    "quote": "a neighbourhood grocer at 174 Ossington Ave, Toronto"}],
        "approaches": [
            {"method": "retail", "status": "DEVELOP", "name": "A makers shelf at Kettle & Crane", "concept_name": "Shelf trial",
             "concept_line": "Kettle & Crane trials the range on its makers shelf.", "lead_refs": ["L1"], "claim_refs": [1, 3],
             "reason": "Claim 1."},
            {"method": "community", "status": "DEVELOP", "name": "Sampling after the Saturday run", "concept_name": "Run sampling",
             "concept_line": "Samples after the run.", "lead_refs": ["L2"], "claim_refs": [2], "reason": "Claim 2."},
            {"method": "popup", "status": "NOT_SUITABLE_NOW", "name": "Pop-up", "reason": "You said no pop-ups this year.",
             "from_your_note": True},
            {"method": "creator", "status": "NEEDS_RESEARCH", "name": "Creators", "reason": "Only social profiles."},
            {"method": "digital", "status": "HOLD", "name": "Online", "reason": "Not yet.", "claim_refs": []}]}


def research_ready(client, env, cities=("toronto",), context=None):
    pid, m = scanned(client, env)
    toronto_pages(env.web)
    env.scripts.setdefault("toronto", toronto_script)
    r = client.post(f"/api/projects/{pid}/research", json={"city_ids": list(cities), "context": context})
    assert r.status_code == 200, r.text
    idle(pid)
    return pid, client.get(f"/api/projects/{pid}/opportunities").json()


def city(view, cid):
    return next(c for c in view["cities"] if c["city_id"] == cid)


# ---- the journey, end to end through the API ----
def test_a_city_is_researched_into_five_methods_with_checked_evidence(client, env):
    pid, v = research_ready(client, env, context="We won't do pop-ups this year.")
    t = city(v, "toronto")
    assert t["status"] == "complete" and set(t["matrix"]) == set(research.METHODS)
    m = {k: x["status"] for k, x in t["matrix"].items()}
    assert m["retail"] == "READY_TO_COMPARE" and m["community"] == "READY_TO_COMPARE"
    assert m["popup"] == "NOT_SUITABLE_NOW" and m["creator"] == "NEEDS_RESEARCH"
    assert m["digital"] == "NEEDS_RESEARCH"  # HOLD with no claim and no note: no evidence found
    assert t["matrix"]["digital"]["reason"] == "No evidence found in this research."
    retail = next(a for a in t["approaches"] if a["method"] == "retail")
    kc = v["leads"][retail["lead_ids"][0]]
    assert kc["presence"] == "VERIFIED" and kc["own_site"] == "kettlecrane.example"
    assert kc["contact"]["value"] == "hello@kettlecrane.example"  # from its own site only
    assert kc["willingness"] == "UNKNOWN"
    assert v["title"] == {"approaches": 2, "cities": 1, "methods": 2}
    assert t["learned_source"] == "DETERMINISTIC" and "makers shelf" in t["learned"]  # built from the checked claims


# ---- R1: the ResearchInput adapter and the Qloo routes ----
def test_r1_input_for_a_recommended_and_an_added_city_and_no_affinity(client, env):
    pid, m = scanned(client, env)
    sl = client.get(f"/api/projects/{pid}/markets").json()["shortlist"]
    client.patch(f"/api/projects/{pid}/shortlist", json={"base_version": sl["version"], "add": ["copenhagen"]})
    rec, added = research.build_input(pid, "toronto"), research.build_input(pid, "copenhagen")
    assert rec["selection"] == "RECOMMENDED" and rec["recommendation"]["position_line"].startswith("In one Qloo ranking")
    assert added["selection"] == "ADDED_BY_YOU" and added["recommendation"] is None
    assert added["scan"]["status"] == "returned" and added["scan"]["places"]
    assert rec["ranking_signal"]["kind"] == "brand" and rec["ranking_signal"]["ref"]["identity"]
    assert rec["audience"]["offered"] and "not your customer data" in rec["audience"]["caveat"]
    for inp in (rec, added):
        assert "affinity" not in json.dumps(inp) and "affinity" not in research.prompt_of(inp).lower()
    p = research.place_params(rec, "retail")
    assert p["signal.interests.entities"] == "SYN-BRAND" and p["filter.tags"] == ",".join(research.method_genres(pid)["retail"])
    assert rec["methods"]["brand_genre"] == "urn:tag:genre:brand:food_and_beverage:drinks"  # Tidewell sells drinks
    assert p["filter.location.radius"] == 15000 and p["take"] == 25


def test_r1_failed_scan_and_no_order_route(client, env):
    env.qloo.fail = {"montreal": 500}
    pid, m = scanned(client, env)
    inp = research.build_input(pid, "montreal")
    assert inp["scan"] == {**inp["scan"], "status": "failed", "places": []}
    assert "place search failed here" in research.prompt_of(inp)
    no_order = {**inp, "ranking_signal": None, "recommendation": None}
    p = research.place_params(no_order, "popup")
    assert "signal.interests.entities" in p or "signal.interests.tags" in p  # the profile's own signals
    assert research.place_params({**no_order, "place_signal": {"signals": [], "profile": None}}, "popup") is None


def test_r1_method_searches_are_listings_when_flat_and_audiences_send_the_urn_alone(client, env):
    def script(ctx, T):
        assert "LISTING" in T["qloo_places"](method="retail")  # FakeQloo answers one value for every place
        assert "Not searched: that isn't" in T["qloo_audience_places"](audience="Knitting")
        assert "unresolved" in T["qloo_audience_places"](audience="Surfing")  # no UUID match
        T["qloo_audience_places"](audience="Watches")
        return {"learned": "", "approaches": []}
    env.scripts["toronto"] = script
    pid, v = research_ready(client, env)
    sent = [p for op, p in env.qloo.calls if op == "insights" and "signal.demographics.audiences" in p]
    assert len(sent) == 1 and sent[0]["signal.demographics.audiences"] == "urn:audience:syn:watches"
    assert "filter.tags" not in sent[0] and "signal.interests.entities" not in sent[0]
    t = city(v, "toronto")
    assert t["status"] == "complete" and "affinity" not in json.dumps(v)
    cr = db.get_record(t["record"]["id"])
    assert cr["consumed"]["audiences_used"] == ["SYN-AUD-1"]


def test_r1_a_removed_audience_is_refused_before_dispatch(client, env):
    pid, m = scanned(client, env)
    ap = db.latest("audience_profile", pid)
    client.put(f"/api/projects/{pid}/audience", json={"base_version": ap["version"], "remove": ["SYN-AUD-1"]})
    toronto_pages(env.web)
    env.scripts["toronto"] = lambda ctx, T: (T["qloo_audience_places"](audience="Watches") and {"learned": "", "approaches": []})
    client.post(f"/api/projects/{pid}/research", json={"city_ids": ["toronto"]})
    idle(pid)
    assert not [p for op, p in env.qloo.calls if op == "audiences" or "signal.demographics.audiences" in p]


# ---- R2: subject, locality, presence, contacts, text fields ----
DIR = "https://cityguide.example/shops"


def scenario(client, env, pages, claims, approaches, leads, learned="", learned_claims=()):
    def script(ctx, T):
        codes = {}
        for url in pages:
            out = T["fetch_page"](url=url, method="retail")
            m = re.search(r"SOURCE (S\w+)", out)
            codes[url] = m.group(1) if m else None
        return {"learned": learned, "learned_claims": list(learned_claims), "leads": leads,
                "claims": [{**c, "source": codes[c["source"]]} for c in claims], "approaches": approaches}
    for url, body in pages.items():
        env.web.pages[url] = body
    env.scripts["toronto"] = script
    pid, v = research_ready(client, env)
    return pid, v, city(v, "toronto")


def develop(method="retail", refs=(1,), leads=("L1",), name="A shelf", concept="A shelf trial."):
    return {"method": method, "status": "DEVELOP", "name": name, "concept_name": "Trial", "concept_line": concept,
            "lead_refs": list(leads), "claim_refs": list(refs), "reason": "c1"}


def test_r2_a_quote_whose_window_names_another_city_is_not_local(client, env):
    page = (200, html("Shops by city", "Montreal. " + FILLER * 3, "Maple & Co stocks outside brands from small makers on its front shelf.",
                      FILLER * 3, "Toronto. " + FILLER))
    pid, v, t = scenario(client, env, {DIR: page},
                         [{"subject": "L1", "method": "retail", "text": "Stocks outside brands", "source": DIR,
                           "quote": "Maple & Co stocks outside brands from small makers on its front shelf"}],
                         [develop()], [{"ref": "L1", "name": "Maple & Co", "kind": "shop"}])
    c = db.get_record(next(iter(v["leads"].values()))["claims"][0]["id"])
    assert c["quote_found"] and not c["locality"] and c["state"] == "TO_CHECK"
    assert t["matrix"]["retail"]["status"] == "NEEDS_RESEARCH"


def test_r2_closed_store_differs_and_a_publisher_email_is_never_attached(client, env):
    page = (200, html("Toronto shops", "In Toronto, Maple & Co stocks outside brands from small makers on its front shelf.",
                      FILLER, "Sadly Maple & Co has closed its doors on Queen Street in Toronto this spring.",
                      "Send tips to editor@cityguide.example or call 416-555-0199."))
    pid, v, t = scenario(client, env, {DIR: page},
                         [{"subject": "L1", "method": "retail", "text": "Stocks outside brands", "source": DIR,
                           "quote": "Maple & Co stocks outside brands from small makers on its front shelf"}],
                         [develop()], [{"ref": "L1", "name": "Maple & Co", "kind": "shop", "website": "https://cityguide.example/"}])
    lead = next(iter(v["leads"].values()))
    assert lead["presence"] == "SOURCES_DIFFER"
    assert lead["contact"] is None and lead["own_site"] is None  # a directory is not the shop's own site
    a = next(x for x in t["approaches"] + t["considered"] if x["method"] == "retail")
    assert a["status"] == "HELD"  # its only partner's location is contested


def test_r2_a_same_name_business_elsewhere_and_an_invented_lead_are_not_kept(client, env):
    page = (200, html("Shops", "Maple & Co in Vancouver stocks outside brands from small makers on its front shelf.", FILLER))
    pid, v, t = scenario(client, env, {DIR: page},
                         [{"subject": "L1", "method": "retail", "text": "Stocks outside brands", "source": DIR,
                           "quote": "Maple & Co in Vancouver stocks outside brands from small makers on its front shelf"}],
                         [develop(leads=("L1", "L2"))],
                         [{"ref": "L1", "name": "Maple & Co", "kind": "shop"}, {"ref": "L2", "name": "Ghost Goods", "kind": "shop"}])
    assert [x["name"] for x in v["leads"].values()] == ["Maple & Co"]
    lead = next(iter(v["leads"].values()))
    assert lead["presence"] == "NOT_CHECKED" and lead["claims"][0]["state"] == "TO_CHECK"


def test_r2_invented_facts_in_learned_and_concept_are_replaced(client, env):
    page = (200, html("Toronto shops", "In Toronto, Maple & Co stocks outside brands from small makers on its front shelf.", FILLER))
    claims = [{"subject": "L1", "method": "retail", "text": "Stocks outside brands", "source": DIR,
               "quote": "Maple & Co stocks outside brands from small makers on its front shelf"}]
    pid, v, t = scenario(client, env, {DIR: page}, claims,
                         [develop(concept="Maple & Co hosts Saturday tastings for 400 people, where demand is high."),
                          {"method": "popup", "status": "HOLD", "name": "Pop-up at Birch Hall", "reason": "Birch Hall rents space.",
                           "claim_refs": [1]}],
                         [{"ref": "L1", "name": "Maple & Co", "kind": "shop"}, {"ref": "L2", "name": "Birch Hall", "kind": "venue"}],
                         learned="Maple & Co sells 500 units a week to eager buyers.", learned_claims=[1])
    assert t["learned_source"] == "DETERMINISTIC" and t["learned"].startswith("Checked on local pages: ")
    assert "500 units" not in t["learned"]
    retail = next(a for a in t["approaches"] if a["method"] == "retail")
    assert "demand" not in json.dumps(retail).lower()


def test_r2_an_unrelated_local_quote_does_not_ground(client, env):
    env.checker.rules["Stocks outside"] = "UNRELATED"
    page = (200, html("Toronto", "In Toronto, Maple & Co opened a second café counter by the window this year.", FILLER))
    pid, v, t = scenario(client, env, {DIR: page},
                         [{"subject": "L1", "method": "retail", "text": "Stocks outside brands", "source": DIR,
                           "quote": "Maple & Co opened a second café counter by the window this year"}],
                         [develop()], [{"ref": "L1", "name": "Maple & Co", "kind": "shop"}])
    assert t["matrix"]["retail"]["status"] == "NEEDS_RESEARCH" and v["title"]["approaches"] == 0


# ---- R3: snapshots, conflicts, checker failure, social, the evidence endpoint ----
def test_r3_a_contradiction_is_contested_for_both_and_never_ready(client, env):
    env.checker.rules["has closed"] = ("CONTRADICTS", "c1")
    page = (200, html("Toronto venues", "In Toronto, Birch Hall rents its main room for pop-up markets on weekends.", FILLER,
                      "A reader wrote that Birch Hall has closed for good after the last season in Toronto."))
    claims = [{"subject": "L1", "method": "popup", "text": "Rents its room for pop-ups", "source": DIR,
               "quote": "Birch Hall rents its main room for pop-up markets on weekends"},
              {"subject": "L1", "method": "popup", "text": "Birch Hall has closed", "source": DIR,
               "quote": "Birch Hall has closed for good after the last season"}]
    pid, v, t = scenario(client, env, {DIR: page}, claims, [develop("popup", (1, 2))],
                         [{"ref": "L1", "name": "Birch Hall", "kind": "venue"}])
    states = sorted(c["state"] for c in next(iter(v["leads"].values()))["claims"])
    assert states == ["CONTESTED", "CONTESTED"] and t["matrix"]["popup"]["status"] == "HELD"
    c = next(iter(v["leads"].values()))["claims"][0]
    ev = client.get(f"/api/sources/{c['source']['id']}?claim={c['id']}").json()
    assert len(ev["differ"]) == 2 and ev["claim"]["label"] == "Pop-up · Birch Hall"


def test_r3_checker_failure_is_per_claim_and_never_fails_the_city(client, env):
    env.checker.fail = True
    pid, v = research_ready(client, env)
    t = city(v, "toronto")
    assert t["status"] == "complete" and v["title"]["approaches"] == 0
    claims = [c for lead in v["leads"].values() for c in lead["claims"]]
    assert claims and all(c["state"] == "TO_CHECK" and "not checked" in c["support"]["reason"].lower() for c in claims)
    assert next(iter(v["leads"].values()))["presence"] == "VERIFIED"  # deterministic presence still stands


def test_r3_blank_pages_and_social_urls(client, env):
    env.web.pages["https://blank.example/"] = (200, "<html><body></body></html>")

    def script(ctx, T):
        assert "Not read: page unavailable (no readable text)" in T["fetch_page"](url="https://blank.example/", method="retail")
        assert "social platforms are never read" in T["fetch_page"](url="https://www.tiktok.com/@maker", method="creator")
        assert "never read" in T["check_presence"](lead_name="Maker", url="https://instagram.com/maker")
        return {"learned": "", "approaches": []}
    env.scripts["toronto"] = script
    pid, v = research_ready(client, env)
    assert not [u for u in env.web.calls if "tiktok" in u or "instagram" in u]
    assert db.q("SELECT error FROM research_sources WHERE url='https://blank.example/'")[0]["error"] == "no readable text"


def test_r3_shared_url_reused_within_a_run_fresh_on_re_research(client, env, monkeypatch):
    monkeypatch.setattr(research, "IN_FLIGHT", 1)
    env.scripts["vancouver"] = toronto_script
    pid, v = research_ready(client, env, cities=("toronto", "vancouver"))
    snaps = db.q("SELECT id FROM research_sources WHERE url=?", KC)
    assert len(snaps) == 1 and len(db.q("SELECT * FROM research_source_uses WHERE source_id=?", snaps[0]["id"])) == 2
    old_claim = next(iter(v["leads"].values()))["claims"][0]
    env.web.pages[KC] = (200, html("Changed", "Kettle & Crane, in Toronto, no longer keeps a makers shelf.", FILLER))
    assert client.post(f"/api/projects/{pid}/research", json={"city_ids": ["toronto"]}).status_code == 409
    assert client.post(f"/api/projects/{pid}/research", json={"city_ids": ["toronto"], "refresh": True}).status_code == 200
    idle(pid)
    assert len(db.q("SELECT id FROM research_sources WHERE url=?", KC)) == 2  # a new snapshot; the old one kept
    s = research.source(old_claim["source"]["id"])
    assert "keeps a makers shelf that stocks outside brands" in s["text"]  # the old claim still points at its snapshot
    assert city(client.get(f"/api/projects/{pid}/opportunities").json(), "toronto")["version"] == 2


def test_r3_the_evidence_endpoint_maps_a_city_claim_and_keeps_project_checks(client, env):
    pid, v = research_ready(client, env)
    c = next(iter(v["leads"].values()))["claims"][0]
    ev = client.get(f"/api/sources/{c['source']['id']}?claim={c['id']}").json()
    assert ev["claim"]["label"].startswith("Retail · Kettle & Crane") and ev["claim"]["state"] == "SUPPORTED"
    assert ev["snapshot"] and ev["fetched_at"] and ev["final_url"] == KC
    brief_claim = db.latest("brief", pid)["facts"][0]
    assert client.get(f"/api/sources/{c['source']['id']}?claim={brief_claim}").status_code == 404
    client.cookies.clear()
    assert client.get(f"/api/sources/{c['source']['id']}?claim={c['id']}").status_code == 404


# ---- R4: stop, continue, retry, re-research, lead check, overflow ----
def test_r4_stop_during_a_tool_then_continue_repeats_no_completed_call(client, env, monkeypatch):
    monkeypatch.setattr(research, "IN_FLIGHT", 1)
    first = [True]

    def stopping(ctx, T):
        T["qloo_places"](method="retail")
        T["fetch_page"](url=KC, method="retail")
        if first[0]:
            first[0] = False
            runner.cancel(ctx.run.id)
            T["fetch_page"](url=KC_SITE, method="retail")  # stopped here
        return toronto_script(ctx, T)
    env.scripts["toronto"] = stopping
    pid, v = research_ready(client, env, cities=("toronto", "vancouver"))
    assert {c["city_id"]: c["status"] for c in v["cities"] if c["version"]} == {"toronto": "stopped", "vancouver": "queued"}
    assert all(x["status"] == "NOT_ASSESSED_STOPPED" for x in city(v, "toronto")["matrix"].values())
    assert "continue" in city(v, "vancouver")["can"]
    qloo_before, loads_before = len(env.qloo.places()), env.web.loads().count(KC)
    r = client.post(f"/api/projects/{pid}/research/continue", json={"city_ids": ["toronto", "vancouver"]})
    assert r.status_code == 200
    idle(pid)
    v = client.get(f"/api/projects/{pid}/opportunities").json()
    assert city(v, "toronto")["status"] == "complete" and city(v, "vancouver")["status"] == "complete"
    assert env.web.loads().count(KC) == loads_before  # completed page loads: 0 new dispatches
    retail = [p for p in env.qloo.places() if p[1]["filter.location"] == POINT("toronto") and "grocery_store" in p[1].get("filter.tags", "")]
    assert len(retail) == 1 and qloo_before  # the retail search ran once, before the stop


def POINT(cid):
    c = markets.CITIES[cid]
    return f"POINT({c['lon']} {c['lat']})"


def test_r4_a_running_checkpoint_is_redispatched_once_and_stays_counted(client, env):
    pid, v = research_ready(client, env)
    cr = f"{pid}:toronto:1"
    db.x("INSERT INTO research_steps VALUES(?,?,?,?,?,?,?)", cr, "page:https://x.example/", "page", "running", None, 0, db.now())
    ctx = research.Ctx(SimpleNamespace(id="r", params={"cities": [["toronto", 1]]}), pid, "toronto", 1, {})
    n0 = research.counts(cr)["page"]
    res, refused = research.memo(ctx, "page", "https://x.example/", lambda: {"ok": 1})
    assert res == {"ok": 1} and research.counts(cr)["page"] == n0 + 1  # the interrupted attempt stays counted


def test_r4_failed_city_retry_and_lead_check_failure_then_success(client, env):
    leaf = "https://ossingtonruncollective.example/"
    env.web.fail.add(leaf)

    def script(ctx, T):
        out = toronto_script(ctx, T)
        T["check_presence"](lead_name="Ossington Run Collective", url=leaf)
        return out
    env.scripts["toronto"] = script
    pid, v = research_ready(client, env)
    lead = next(x for x in v["leads"].values() if x["name"] == "Ossington Run Collective")
    assert lead["presence"] == "CHECK_FAILED"
    assert city(v, "toronto")["matrix"]["community"]["status"] == "READY_TO_COMPARE"  # a useful city around a failed check
    client.post(f"/api/projects/{pid}/leads/{lead['id']}/check")
    idle(pid)
    again = client.get(f"/api/projects/{pid}/leads/{lead['id']}").json()
    assert again["presence"] == "CHECK_FAILED" and again["version"] == 2
    env.web.fail.discard(leaf)
    env.web.pages[leaf] = (200, html("Ossington Run Collective", "Ossington Run Collective meets at 900 Dundas St W, Toronto.", FILLER))
    loads = len(env.web.loads())
    client.post(f"/api/projects/{pid}/leads/{lead['id']}/check")
    idle(pid)
    ok = client.get(f"/api/projects/{pid}/leads/{lead['id']}").json()
    assert ok["presence"] == "VERIFIED" and ok["version"] == 3 and len(env.web.loads()) - loads <= research.LEAD_CHECK_LOADS
    assert research.counts(f"{pid}:toronto:1")["model"] == 0  # no model call, no other research


def test_r4_a_failed_city_shows_check_failed_and_retries(client, env):
    calls = [0]

    def flaky(ctx, T):
        calls[0] += 1
        if calls[0] == 1:
            raise RuntimeError("SYNTHETIC agent failure")
        return toronto_script(ctx, T)
    env.scripts["toronto"] = flaky
    pid, v = research_ready(client, env)
    t = city(v, "toronto")
    assert t["status"] == "failed" and all(x["status"] == "CHECK_FAILED" for x in t["matrix"].values()) and "retry" in t["can"]
    assert client.post(f"/api/projects/{pid}/research/retry", json={"city_ids": ["toronto"]}).status_code == 200
    idle(pid)
    t = city(client.get(f"/api/projects/{pid}/opportunities").json(), "toronto")
    assert t["status"] == "complete" and t["version"] == 1


def test_r4_nine_cities_are_refused_and_duplicates_conflict(client, env):
    pid, m = scanned(client, env)
    sl = client.get(f"/api/projects/{pid}/markets").json()["shortlist"]
    client.patch(f"/api/projects/{pid}/shortlist", json={"base_version": sl["version"], "add": ["berlin", "paris", "tokyo"]})
    r = client.post(f"/api/projects/{pid}/research", json={})
    assert r.status_code == 422 and "untick 1" in r.json()["detail"]
    assert client.post(f"/api/projects/{pid}/research", json={"city_ids": ["lima"]}).status_code == 422
    env.scripts["toronto"] = lambda ctx, T: (time.sleep(0.6), {"learned": "", "approaches": []})[1]
    assert client.post(f"/api/projects/{pid}/research", json={"city_ids": ["toronto"]}).status_code == 200
    assert client.post(f"/api/projects/{pid}/research", json={"city_ids": ["toronto"]}).status_code == 409
    idle(pid)


# ---- R5: visibility, consumed values, context timing, decisions ----
def test_r5_untick_hides_without_a_call_and_add_researches_only_the_new_city(client, env):
    pid, v = research_ready(client, env)
    sl = client.get(f"/api/projects/{pid}/markets").json()["shortlist"]
    client.patch(f"/api/projects/{pid}/shortlist", json={"base_version": sl["version"], "exclude": ["toronto"]})
    v = client.get(f"/api/projects/{pid}/opportunities").json()
    assert not city(v, "toronto")["visible"] and v["title"]["approaches"] == 0
    sl = client.get(f"/api/projects/{pid}/markets").json()["shortlist"]
    client.patch(f"/api/projects/{pid}/shortlist", json={"base_version": sl["version"], "include": ["toronto"]})
    v = client.get(f"/api/projects/{pid}/opportunities").json()
    assert city(v, "toronto")["visible"] and v["title"]["approaches"] == 2 and env.agent_calls == ["toronto"]
    client.post(f"/api/projects/{pid}/research", json={"city_ids": ["toronto", "vancouver"]})
    idle(pid)
    assert env.agent_calls == ["toronto", "vancouver"]


def _correct(client, pid, n):
    b = db.latest("brief", pid)
    facts = [i for i in b["facts"] if db.get_record(i)["state"] in ("SUPPORTED", "STATED")]
    client.patch(f"/api/projects/{pid}/brief", json={"base_version": b["version"], "claim_id": facts[n], "value": "Corrected"})


def test_r5_only_a_used_fact_flags_the_research(client, env):
    pid, v = research_ready(client, env)  # the script used F1
    _correct(client, pid, 1)
    assert city(client.get(f"/api/projects/{pid}/opportunities").json(), "toronto")["stale"] == []
    _correct(client, pid, 0)
    assert city(client.get(f"/api/projects/{pid}/opportunities").json(), "toronto")["stale"] == \
        ["A fact this research used changed"]


def test_r5_only_a_used_audience_flags_the_research(client, env):
    def script(ctx, T):
        T["qloo_audience_places"](audience="Watches")
        return toronto_script(ctx, T)
    env.scripts["toronto"] = script
    pid, v = research_ready(client, env)
    ap = db.latest("audience_profile", pid)
    client.put(f"/api/projects/{pid}/audience", json={"base_version": ap["version"], "remove": ["SYN-AUD-2"]})
    assert city(client.get(f"/api/projects/{pid}/opportunities").json(), "toronto")["stale"] == []
    ap = db.latest("audience_profile", pid)
    client.put(f"/api/projects/{pid}/audience", json={"base_version": ap["version"], "remove": ["SYN-AUD-1"]})
    assert "You removed an audience this research used" in city(client.get(f"/api/projects/{pid}/opportunities").json(), "toronto")["stale"]


def test_r5_a_note_changed_mid_run_reaches_queued_cities_and_flags_started_ones(client, env, monkeypatch):
    monkeypatch.setattr(research, "IN_FLIGHT", 1)

    def editing(ctx, T):
        fi = db.latest("founder_input", ctx.pid)
        r = client.patch(f"/api/projects/{ctx.pid}/intent", json={"base_version": fi["version"], "research_context": "No retail."})
        assert r.status_code == 200 and "Toronto started with the earlier one" in r.json()["notice"]
        return toronto_script(ctx, T)
    env.scripts["toronto"] = editing
    env.scripts["vancouver"] = toronto_script
    pid, v = research_ready(client, env, cities=("toronto", "vancouver"), context="No pop-ups.")
    t, van = city(v, "toronto"), city(v, "vancouver")
    assert t["context_used"] == "No pop-ups." and t["stale"] == ["Your note changed after Toronto started"]
    assert van["context_used"] == "No retail." and van["stale"] == []


def test_r5_rule_out_races_and_carries_over_by_identity(client, env):
    pid, v = research_ready(client, env)
    a = next(x for x in city(v, "toronto")["approaches"] if x["method"] == "retail")
    assert client.post(f"/api/projects/{pid}/approaches/{a['id']}/rule-out", json={"base_version": 0}).json()["version"] == 1
    assert client.post(f"/api/projects/{pid}/approaches/{a['id']}/rule-out", json={"base_version": 0}).status_code == 409
    v = client.get(f"/api/projects/{pid}/opportunities").json()
    assert city(v, "toronto")["matrix"]["retail"]["status"] == "RULED_OUT_BY_YOU"
    client.post(f"/api/projects/{pid}/research", json={"city_ids": ["toronto"], "refresh": True})
    idle(pid)
    v = client.get(f"/api/projects/{pid}/opportunities").json()
    assert city(v, "toronto")["matrix"]["retail"]["status"] == "RULED_OUT_BY_YOU"  # same concept and leads
    renamed = lambda ctx, T: {**(s := toronto_script(ctx, T)), "approaches": [  # noqa: E731
        {**x, "concept_name": "A different idea"} if x["method"] == "retail" else x for x in s["approaches"]]}
    env.scripts["toronto"] = renamed
    client.post(f"/api/projects/{pid}/research", json={"city_ids": ["toronto"], "refresh": True})
    idle(pid)
    t = city(client.get(f"/api/projects/{pid}/opportunities").json(), "toronto")
    assert t["matrix"]["retail"]["status"] == "READY_TO_COMPARE" and "You ruled out an earlier retail idea here." in t["notes"]


def test_r5_a_founder_contact_is_you_said_and_fenced(client, env):
    pid, v = research_ready(client, env)
    lead = next(iter(v["leads"].values()))
    assert client.post(f"/api/projects/{pid}/leads/{lead['id']}/contact", json={"base_version": 0, "contact": "Ana, owner"}).status_code == 200
    assert client.post(f"/api/projects/{pid}/leads/{lead['id']}/contact", json={"base_version": 0, "contact": "x"}).status_code == 409
    assert client.get(f"/api/projects/{pid}/leads/{lead['id']}").json()["founder_contact"]["value"] == "Ana, owner"


# ---- R6: caps and admission ----
def _ctx(pid="prj_cap", forced=False):
    return research.Ctx(SimpleNamespace(id="run_cap", params={"cities": [["toronto", 1]]}), pid, "toronto", 1, {}, forced=forced)


def test_r6_dollar_and_model_call_admission_keeps_the_forced_submission():
    ctx = _ctx()
    db.x("INSERT INTO research_steps VALUES(?,?,?,?,?,?,?)", ctx.cr, "usd:1", "usd_model", "done", "{}", 0.74, db.now())
    with pytest.raises(research.CityCap):
        research.admitter(ctx, "search")(0.02)  # 0.76 > 0.80 − 0.05 kept for the forced submission
    research.admitter(_ctx(forced=True), "model")(0.02)  # the forced submission fits inside the cap
    with pytest.raises(research.CityCap):
        research.admitter(_ctx(forced=True), "model")(0.10)
    ctx2 = _ctx("prj_cap2")
    for i in range(research.CAPS["model"] - 1):
        db.x("INSERT INTO research_steps VALUES(?,?,?,?,?,?,?)", ctx2.cr, f"usd:{i}", "usd_model", "done", "{}", 0.0, db.now())
    with pytest.raises(research.CityCap):
        research.admitter(ctx2, "model")(0.001)
    research.admitter(_ctx("prj_cap2", forced=True), "model")(0.001)  # the 24th call is the forced one


def test_r6_each_tool_cap_refuses_before_dispatch(client, env):
    def script(ctx, T):
        for i in range(research.CAPS["page"] + 1):
            env.web.pages[f"https://p{i}.example/"] = (200, html(f"P{i}", FILLER, FILLER))
            out = T["fetch_page"](url=f"https://p{i}.example/", method="retail")
        assert "12-call limit" in out
        for m in ("retail", "popup", "community", "retail", "popup", "community"):
            T["qloo_places"](method=m)  # repeats return the checkpoint: no new call
        for i in range(research.CAPS["search"] + 1):
            out = T["web_search"](query=f"q{i}", method="retail")
        assert "4-call limit" in out
        return {"learned": "", "approaches": []}
    env.scripts["toronto"] = script
    pid, v = research_ready(client, env)
    assert len(env.web.loads()) == research.CAPS["page"]
    assert len([p for p in env.qloo.places() if p[1]["filter.location"] == POINT("toronto") and "filter.tags" in p[1]]) == 3
    c = city(v, "toronto")["counters"]
    assert (c["page"], c["search"], c["qloo"]) == (12, 4, 3)


def test_r6_an_allowance_denial_in_a_tool_is_reported_not_dispatched(client, env, monkeypatch):
    def denied(ctx, q):
        raise research.BudgetExhausted("live authority expired")
    monkeypatch.setattr(research, "ground", denied)
    env.scripts["toronto"] = lambda ctx, T: (T["web_search"](query="x", method="retail"), {"learned": "", "approaches": []})[1]
    pid, v = research_ready(client, env)
    assert city(v, "toronto")["status"] == "complete"
    # owner, 28 Oct 2026: a refusal stays in Activity, never in the research the founder watches
    assert not any("operating allowance" in e["text"] for e in city(v, "toronto")["stream"])
    assert any(e.get("status") == "UNAVAILABLE" and "live authority expired" in e.get("detail", "")
               for e in db.events(pid, kind="activity"))


def test_methods_genres_appear_in_recorded_qloo_responses():
    seen = set()
    for f in config.PRIVATE.parent.glob("live-captures-private/**/exchanges.jsonl") if False else \
            (config.ROOT / "live-captures-private").glob("**/exchanges.jsonl"):
        for line in filter(None, f.read_text().split("\n")):
            seen |= set(re.findall(r'urn:tag:genre:place:[a-z_:]+', line))
    if not seen:
        pytest.skip("captured probes not present (private)")
    m = json.loads((markets.DATA / "methods.json").read_text())
    ids = {f"urn:tag:genre:place:{g}" for v in m["retail"].values() for g in v} | \
        {f"urn:tag:genre:place:{g}" for g in m["popup"] + m["community"]}
    assert ids <= seen, ids - seen


def test_tools_stop_running_at_the_wrap_up_point(client, env):
    """LIVE L4-A attempt 1: Montreal kept reading pages until its model calls ran out. From WRAP_CALLS no tool runs."""
    def script(ctx, T):
        for i in range(research.WRAP_CALLS):
            db.x("INSERT INTO research_steps VALUES(?,?,?,?,?,?,?)", ctx.cr, f"usd:x{i}", "usd_model", "done", "{}", 0.0, db.now())
        out = T["fetch_page"](url=KC, method="retail")
        assert "no more tools will run" in out and "CityResearchSubmission" in out
        return {"learned": "", "approaches": []}
    env.scripts["toronto"] = script
    pid, v = research_ready(client, env)
    assert KC not in env.web.loads()


def test_replay_serves_repeated_exchanges_in_recorded_order(tmp_path):
    """LIVE L4-A: Bedrock answered 429 and the retry 200 under one request key; replay must give 429 then 200."""
    from landfall.cassette import Cassette
    d = tmp_path / "case"
    d.mkdir()
    rows = [{"key": "k1", "provider": "bedrock", "op": "Converse", "request": {}, "response": {"status": s, "headers": {}, "body": "{}"}}
            for s in (429, 429, 200)] + [{"key": "k2", "provider": "qloo", "op": "x", "request": {}, "response": {"status": 200}}]
    (d / "exchanges.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    c = Cassette("replay", replay_dirs=[d])
    assert [c.lookup("k1")["response"]["status"] for _ in range(4)] == [429, 429, 200, 200]
    assert c.lookup("k2") is c.lookup("k2")  # a key recorded once answers as it always did


def test_live_l4b_counterexamples_subject_binding_partial_support_and_address_phone(client, env):
    """LIVE L4-B (25 Oct): a quote about Art Shed Brisbane was bound to the lead Target Brisbane; an address block
    "supported" what a shop sells; a street number and postcode on two lines read as a phone. Frozen here."""
    env.checker.rules["sells Portuguese-made goods"] = "SUPPORTS"
    page = (200, html("Toronto shops", "In Toronto, Art Shed sells art supplies and stocks outside brands on its shelf.", FILLER))
    env.web.pages["https://maple.example/"] = (200, html("Maple & Co", "Maple & Co, 72 Queen St W", "M5H 2N2 Toronto. Open daily.", FILLER))

    def script(ctx, T):
        code = re.search(r"SOURCE (S\w+)", T["fetch_page"](url=DIR, method="retail")).group(1)
        own = re.search(r"SOURCE (S\w+)", T["fetch_page"](url="https://maple.example/", method="retail")).group(1)
        return {"learned": "", "leads": [{"ref": "L1", "name": "Maple & Co", "kind": "shop"}],
                "claims": [{"subject": "L1", "method": "retail", "text": "Stocks outside brands", "source": code,
                            "quote": "Art Shed sells art supplies and stocks outside brands on its shelf"},
                           {"subject": "L1", "method": "retail", "text": "Has a store in Toronto that sells Portuguese-made goods",
                            "source": own, "quote": "Maple & Co, 72 Queen St W"}],
                "approaches": [develop(refs=(1, 2))]}
    env.web.pages[DIR] = page
    env.scripts["toronto"] = script
    original = env.checker.__call__

    def partial(ctx, listing):
        out = original(ctx, listing)
        for v in out.verdicts:
            if "Portuguese" in listing.split(f"id={v.claim_id} ")[1].split("\n")[0]:
                v.unsupported_parts = ["what it sells"]
        return out
    env.mp.setattr(research, "judge", partial)
    pid, v = research_ready(client, env)
    claims = {c["text"]: c for lead in v["leads"].values() for c in lead["claims"]}
    assert claims["Stocks outside brands"]["state"] == "TO_CHECK"  # the passage doesn't name Maple & Co
    assert "doesn't name Maple & Co" in claims["Stocks outside brands"]["reason"]
    assert claims["Has a store in Toronto that sells Portuguese-made goods"]["state"] == "TO_CHECK"
    assert city(v, "toronto")["matrix"]["retail"]["status"] == "NEEDS_RESEARCH"
    assert research.contacts_in("", "Rua Nova do Almada 72\n1200-289 Lisboa") == []


def test_r6_a_run_over_twice_its_expected_cost_stops_before_dispatch():
    """The plan's anomalous-run stop: two cities expect ≈ $0.72, so the run admits nothing past $1.44."""
    run = SimpleNamespace(id="run_x", params={"cities": [["toronto", 1], ["vancouver", 1]]})
    a, b = research.Ctx(run, "prj_x", "toronto", 1, {}), research.Ctx(run, "prj_x", "vancouver", 1, {})
    db.x("INSERT INTO research_steps VALUES(?,?,?,?,?,?,?)", a.cr, "usd:1", "usd_model", "done", "{}", 0.74, db.now())
    db.x("INSERT INTO research_steps VALUES(?,?,?,?,?,?,?)", b.cr, "usd:1", "usd_model", "done", "{}", 0.69, db.now())
    with pytest.raises(research.CityCap, match="twice its expected cost"):
        research.admitter(b, "search")(0.02)


# ---- implementation review REVIEW-SLICE-04 (P4-I1 to I4), frozen ----
TASTINGS = "Maple & Co hosts Saturday tastings."
MAPLE = [{"subject": "L1", "method": "retail", "text": "Stocks outside brands", "source": DIR,
          "quote": "Maple & Co stocks outside brands from small makers on its front shelf"}]
MAPLE_PAGE = (200, html("Toronto shops", "In Toronto, Maple & Co stocks outside brands from small makers on its front shelf.", FILLER))


def test_i1_a_checked_lead_does_not_license_unchecked_prose(client, env):
    env.prose.unsupported = ["Saturday tastings"]
    pid, v, c = scenario(client, env, {DIR: MAPLE_PAGE}, MAPLE, [develop(concept=TASTINGS)],
                         [{"ref": "L1", "name": "Maple & Co", "kind": "shop"}], learned=TASTINGS, learned_claims=[1])
    a = c["approaches"][0]
    assert "Saturday tastings" not in c["learned"] and "Saturday tastings" not in json.dumps(a["concept"])
    assert a["status"] == "READY_TO_COMPARE" and a["concept"]["line"].startswith("A proposal resting on one checked fact")


def test_i1_captured_function_well_audience_attribute_is_dropped_and_a_real_proposal_kept(client, env):
    """LIVE L4-B: "appealing to … environmentally aware members" beside a claim that only shows a wellness hub."""
    env.prose.unsupported = ["environmentally aware members"]
    concept = ("Collaborate with Maple & Co to integrate our bags into their events, appealing to health-conscious and "
               "environmentally aware members.")
    hyp = "Maple & Co could trial a small range on its front shelf."
    a = develop(concept=concept)
    a["hypotheses"] = [hyp, "Its members care about sustainability and environmentally aware members shop there."]
    pid, v, c = scenario(client, env, {DIR: MAPLE_PAGE}, MAPLE, [a], [{"ref": "L1", "name": "Maple & Co", "kind": "shop"}])
    r = c["approaches"][0]
    assert "environmentally aware" not in json.dumps(r)
    assert r["hypotheses"] == [hyp]  # a genuine proposed experiment survives
    assert "Stocks outside brands" in c["learned"]  # a valid paraphrase of the checked claim survives


def test_i2_presence_does_not_join_unrelated_directory_sections():
    lead = {"name": "Maple & Co", "address": "174 Ossington Ave, Toronto, ON M6J 2Z7", "own_site": None}
    page = {"id": "dir", "status": 200, "error": None, "url": DIR, "final_url": DIR,
            "text": "Vancouver: Maple & Co is at 99 Main Street.\n" + FILLER * 4
                    + "\nToronto: Birch Market is at 174 Ossington Ave, Toronto, ON M6J 2Z7."}
    assert research.presence_of_lead(lead, [page], "toronto")["state"] == "SOURCES_DIFFER"  # 99 Main St beside its name
    no_conflict = {**page, "text": "Maple & Co\n" + FILLER * 4 + "\nToronto: Birch Market is at 174 Ossington Ave."}
    assert research.presence_of_lead(lead, [no_conflict], "toronto")["state"] == "NOT_CHECKED"


def test_i2_shared_postcode_conflicting_own_site_and_a_valid_same_entry_address():
    lead = {"name": "Maple & Co", "address": "174 Ossington Ave, Toronto, ON M6J 2Z7", "own_site": None}
    page = lambda text: {"id": "p", "status": 200, "error": None, "url": DIR, "final_url": DIR, "text": text}  # noqa: E731
    shared = page("Maple & Co, Toronto M6J 2Z7 — open Saturdays.\nBirch Market, 12 Dundas St W, M6J 2Z7.")
    assert research.presence_of_lead(lead, [shared], "toronto")["state"] != "VERIFIED"  # a postcode alone never verifies
    entry = page("Maple & Co\n174 Ossington Avenue, Toronto\nBirch Market\n12 Dundas St W")
    assert research.presence_of_lead(lead, [entry], "toronto")["state"] == "VERIFIED"
    own = {**lead, "own_site": "maple.example"}
    site = {"id": "o", "status": 200, "error": None, "url": "https://maple.example/", "final_url": "https://maple.example/",
            "text": "Maple & Co\nVisit us at 50 Queen St W, Toronto."}
    assert research.presence_of_lead(own, [site], "toronto")["state"] == "SOURCES_DIFFER"


def _fence(client, env, mutate):
    def editing(ctx, T):
        mutate(ctx)
        return toronto_script(ctx, T)
    env.scripts["toronto"] = editing
    return research_ready(client, env, context="No pop-ups.")


def test_i3_a_fenced_publication_is_history_not_current_ready(client, env):
    def note(ctx):
        fi = db.latest("founder_input", ctx.pid)
        client.patch(f"/api/projects/{ctx.pid}/intent", json={"base_version": fi["version"], "research_context": "No retail."})
    pid, view = _fence(client, env, note)
    c = city(view, "toronto")
    assert c["fenced"] and not c["approaches"] and c["history"] and view["title"]["approaches"] == 0
    assert set(x["status"] for x in c["matrix"].values()) == {"HISTORY"}
    env.scripts["toronto"] = toronto_script
    client.post(f"/api/projects/{pid}/research", json={"city_ids": ["toronto"], "refresh": True})
    idle(pid)
    c = city(client.get(f"/api/projects/{pid}/opportunities").json(), "toronto")
    assert not c["fenced"] and c["context_used"] == "No retail." and c["approaches"]  # re-research is current


def test_i3_an_in_flight_fact_or_goal_change_fences_and_unchanged_inputs_do_not(client, env):
    def fact(ctx):
        b = db.latest("brief", ctx.pid)
        used = [i for i in b["facts"] if db.get_record(i)["state"] in ("SUPPORTED", "STATED")][0]  # the script uses F1
        client.patch(f"/api/projects/{ctx.pid}/brief", json={"base_version": b["version"], "claim_id": used, "value": "X"})
    pid, view = _fence(client, env, fact)
    assert city(view, "toronto")["fenced"] and view["title"]["approaches"] == 0


def test_i3_goal_change_fences(client, env):
    def goal(ctx):
        fi = db.latest("founder_input", ctx.pid)
        client.patch(f"/api/projects/{ctx.pid}/intent", json={"base_version": fi["version"], "purpose": "stockists"})
    pid, view = _fence(client, env, goal)
    assert city(view, "toronto")["fenced"]


def test_i3_unchanged_inputs_are_current(client, env):
    pid, view = _fence(client, env, lambda ctx: None)
    c = city(view, "toronto")
    assert not c["fenced"] and c["approaches"] and view["title"]["approaches"] == 2


def _audiences(env, lists):
    """SYNTHETIC: each audience URN answers its own place list."""
    original = research.qloo.httpx.get

    def http(url, params=None, **kw):
        p = dict(params or {})
        if url.endswith("/audiences"):
            n = {"Watches": 1, "Surfing": 2}[p["filter.query"]]
            data = {"results": {"audiences": [{"id": f"urn:audience:syn:{n}", "entity_id": f"SYN-AUD-{n}", "name": p["filter.query"]}]}}
        elif p.get("signal.demographics.audiences"):
            n = int(p["signal.demographics.audiences"].rsplit(":", 1)[-1])
            data = {"success": True, "results": {"entities": [
                {"entity_id": pid_, "name": name, "location": {"lat": 43.7, "lon": -79.4}, "query": {"affinity": 0.5 - i / 10}, "tags": []}
                for i, (pid_, name) in enumerate(lists[n])]}}
        else:
            return original(url, params=params, **kw)
        return SimpleNamespace(status_code=200, headers={}, text=json.dumps(data))
    env.mp.setattr(research.qloo.httpx, "get", http)


@pytest.mark.parametrize("pick, expect", [("River Park", "PLACE-1"), ("Surf Park", "PLACE-2"), ("Harbour Hall", "PLACE-9")])
def test_i4_two_audience_searches_keep_each_places_identity(client, env, pick, expect):
    _audiences(env, {1: [("PLACE-1", "River Park"), ("PLACE-9", "Harbour Hall")],
                     2: [("PLACE-2", "Surf Park"), ("PLACE-9", "Harbour Hall")]})  # one entity in both lists
    seen = {}

    def script(ctx, T):
        for name in ("Watches", "Surfing"):
            seen[name] = T["qloo_audience_places"](audience=name)
        code = re.search(rf"\[(\S+)\] {re.escape(pick)}", seen["Watches"] + seen["Surfing"]).group(1)
        return {"learned": "", "approaches": [], "leads": [{"ref": "L1", "name": pick, "kind": "venue", "qloo_ref": code}]}
    env.scripts["toronto"] = script
    pid, view = research_ready(client, env)
    assert "[A1-1]" in seen["Watches"] and "[A2-1]" in seen["Surfing"]  # one code space per audience
    assert [(x["name"], x["qloo_id"]) for x in view["leads"].values()] == [(pick, expect)]


def test_i4_an_inconsistent_reference_is_rejected(client, env):
    _audiences(env, {1: [("PLACE-1", "River Park")], 2: [("PLACE-2", "Surf Park")]})

    def script(ctx, T):
        T["qloo_audience_places"](audience="Watches")
        T["qloo_audience_places"](audience="Surfing")
        return {"learned": "", "approaches": [], "leads": [{"ref": "L1", "name": "River Park", "kind": "venue", "qloo_ref": "A2-1"}]}
    env.scripts["toronto"] = script
    pid, view = research_ready(client, env)
    assert view["leads"] == {}  # A2-1 is Surf Park: the reference is not used, and River Park is on no page read


def test_i1_live_l4b_v2_a_presupposed_retail_area_is_dropped():
    """LIVE L4-B v2: "…for sale in their retail area" beside a claim that only shows a gym and wellness hub."""
    facts = ["Function Well is a gym in Brisbane that offers fitness and wellness programs. Function Well is more than a gym."]
    lead = [{"name": "Function Well"}]
    bad = "Collaborate with Function Well to offer our bags as part of their wellness packages or for sale in their retail area."
    assert research.asserts_unchecked(bad, lead, facts)
    assert not research.asserts_unchecked("Run a sampling morning with Function Well for its fitness programs.", lead, facts)
    assert not research.asserts_unchecked("Kettle & Crane trials the range on its makers shelf.", [{"name": "Kettle & Crane"}],
                                          ["Stocks outside brands on a makers shelf"])
