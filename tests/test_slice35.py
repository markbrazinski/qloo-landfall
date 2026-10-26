"""Slice 3.5: the goal, presence, stand-ins, Qloo's single-query ranking, audiences and the explained recommendations
(BUILD-PLAN slice 3.5 tests; CONTRACTS B3.5).

Unit tests are deterministic and SYNTHETIC unless they read a capture (`captured`, skipped in a fresh clone).
Integration tests replay the synthetic Tidewell brand read; Qloo answers are SYNTHETIC at the HTTP boundary (the fake in
test_markets) and the Market strategist's output is SYNTHETIC (scan.call_strategist replaced).
"""
import json
from types import SimpleNamespace

import pytest
from test_markets import G, LIVE, POINTS, RECOMMENDED, FakeQloo, Strategist, idle, markets_env, matched_project  # noqa: F401

from landfall import app as app_module, config, db, identity, markets, qloo, ranking, scan

PRIVATE = config.ROOT / "live-captures-private" / "probes" / "slice3_5_open" / "exchanges.jsonl"


def captured(line: int) -> dict:
    if not PRIVATE.exists():
        pytest.skip("captured probes not present (private)")
    return json.loads(PRIVATE.read_text().split("\n")[line - 1])


def body(line: int):
    return json.loads(captured(line)["response"]["body"])


# ---- goal ----
def test_tracking_parameters_are_stripped_at_intake():
    assert ranking.strip_tracking("https://untuckit.com/?utm_source=x&gclid=1&gad_source=2&fbclid=3&ref=keep") == \
        "https://untuckit.com/?ref=keep"
    assert app_module.normalize_url("untuckit.com/shirts?utm_campaign=x&tw_source=y") == "https://untuckit.com/shirts"


def test_s01_defaults_and_unknown_values(client, markets_env):
    pid = matched_project(client)
    intent = client.get(f"/api/projects/{pid}").json()["intent"]
    assert (intent["scope"], intent["purpose"], intent["leave_out_presence"], intent["home_country"]) == \
        ("international", "presence", True, None)
    assert client.post("/api/projects", json={"url": "http://tidewell.example/", "scope": "everywhere"}).status_code == 422
    v = intent["version"]
    assert client.patch(f"/api/projects/{pid}/intent", json={"base_version": v, "home_country": "XX"}).status_code == 422
    assert client.patch(f"/api/projects/{pid}/intent", json={"base_version": v + 5, "scope": "both"}).status_code == 409


# ---- destinations@1 ----
def test_destinations_are_distinct_and_wellington_and_lima_are_unmapped():
    ids = [v["qloo_id"] for v in ranking.DESTINATIONS.values() if "qloo_id" in v]
    assert len(ids) == 78 == len(set(ids)) and set(ranking.DESTINATIONS) == set(markets.ORDER)
    assert {c for c, v in ranking.DESTINATIONS.items() if "unmapped" in v} == {"wellington", "lima"}
    ams = ranking.DESTINATIONS["amsterdam"]
    assert ams["qloo_id"].startswith("4D5C88B0") and ams["alternative_id"].startswith("9DB26660")  # higher popularity
    assert ranking.DESTINATIONS["new-york"]["alternative_id"].startswith("99FB5527")  # "New York City" kept apart
    assert ranking.DESTINATIONS["sao-paulo"]["name"] == "São Paulo"  # not São Bernardo do Campo


def test_every_destination_id_came_back_as_a_destination_in_the_recording():
    captured(1)
    seen = set()
    for line in filter(None, PRIVATE.read_text().split("\n")):
        ex = json.loads(line)
        if ex["request"]["params"].get("types") == "urn:entity:destination":
            seen |= {e["entity_id"] for e in json.loads(ex["response"]["body"])["results"]}
    assert {v["qloo_id"] for v in ranking.DESTINATIONS.values() if "qloo_id" in v} <= seen


# ---- presence (R3) ----
def test_captured_komono_stores_map_to_five_catalog_cities():
    stores = [e for e in body(93)["results"] if ranking.store_match(e, "Komono")]
    assert len(stores) == 7 and not any(e["name"].startswith("Komonoya") or e["name"] == "Komonokaen" for e in stores)
    cities = {ranking.nearest_city(e["location"]["lat"], e["location"]["lon"]) for e in stores}
    assert cities - {None} == {"istanbul", "bogota", "madrid", "brussels", "amsterdam"}  # Gent 49 km out; Lyon not in it


@pytest.mark.parametrize("line, brand", [(90, "UNTUCKit"), (91, "Allbirds"), (92, "Warby Parker")])
def test_captured_prefix_names_match_all_fifty(line, brand):
    rows = body(line)["results"]
    assert len(rows) == 50 and sum(ranking.store_match(e, brand) for e in rows) == 50


def test_store_match_needs_the_prefix_and_retail():
    shop = {"name": "Komono Store Gent", "tags": [{"tag_id": "urn:tag:category:place:watch_store"}]}
    assert ranking.store_match(shop, "Komono") and ranking.store_match({**shop, "name": "komono"}, "Komono")
    assert not ranking.store_match({**shop, "name": "Komonoya"}, "Komono")
    assert not ranking.store_match({"name": "Komono Swimming School", "tags": [{"tag_id": f"{G}:swimming_pool"}]}, "Komono")


def _project(pid="prj_u"):
    db.x("INSERT OR IGNORE INTO projects VALUES(?,?,?,?,?,?,?)", pid, "s", "https://x.example", "live", None, None, db.now())
    return pid


def test_site_observations_use_supported_or_stated_only_and_skip_negations():
    pid = _project()
    a = db.put_record("claim", pid, {"subject": {"key": "where_sold"}, "label": "Where you sell", "state": "TO_CHECK",
                                     "value": "A shop at Frankfurt Airport"})
    b = db.put_record("claim", pid, {"subject": {"key": "where_sold"}, "label": "Where you sell", "state": "STATED",
                                     "value": "Our store in Berlin; not yet in Paris. Stockists across Japan"})
    cities, countries, unused = ranking.site_observations({"facts": [a["id"], b["id"]]})
    assert set(cities) == {"berlin"} and set(countries) == {"JP"} and unused[0]["claim_id"] == a["id"]


def matched(client, env, **intent):
    pid = client.post("/api/projects", json={"url": "http://tidewell.example/", **intent}).json()["project_id"]
    idle(pid)
    return pid


def scanned35(client, env, **intent):
    pid = matched(client, env, **intent)
    assert client.post(f"/api/projects/{pid}/scan").json()["run_id"]
    idle(pid)
    return pid, client.get(f"/api/projects/{pid}/markets").json()


def correct_home(client, pid, value):
    b = db.latest("brief", pid)
    cid = next(i for i in b["facts"] + b["interpretation"] if db.get_record(i)["label"] == "Home market")
    client.patch(f"/api/projects/{pid}/brief", json={"base_version": b["version"], "claim_id": cid, "value": value})


@pytest.mark.parametrize("scope, home_out, others_in", [("international", True, True), ("domestic", False, False),
                                                        ("both", False, True)])
def test_a_country_only_home_in_each_scope(client, markets_env, scope, home_out, others_in):
    pid = matched(client, markets_env, scope=scope)
    correct_home(client, pid, "Belgium")
    cand = ranking.candidates(pid)
    assert ("brussels" not in cand["city_ids"]) is home_out and ("paris" in cand["city_ids"]) is others_in
    pres = ranking.presence_of(pid)
    assert not any(c["city_id"] == "brussels" for c in pres["cities"])  # a country is never turned into a city
    assert {"iso": "BE", "kind": "HOME_COUNTRY"} in [{"iso": c["iso"], "kind": s["kind"]} for c in pres["countries"]
                                                     for s in c["sources"]]


def test_an_unknown_home_never_broadens_the_goal(client, markets_env):
    pid = matched(client, markets_env, scope="domestic")
    correct_home(client, pid, "somewhere by the sea")
    p = client.get(f"/api/projects/{pid}").json()
    assert p["goal"]["needs_home"] and "home first" in p["markets_reason"]
    assert ranking.candidates(pid)["city_ids"] == []
    v = p["intent"]["version"]
    client.patch(f"/api/projects/{pid}/intent", json={"base_version": v, "scope": "international"})
    cand = ranking.candidates(pid)
    assert cand["home_unknown_international"] and "seattle" in cand["city_ids"]  # proceeds, and says so
    client.patch(f"/api/projects/{pid}/intent", json={"base_version": v + 1, "scope": "domestic", "home_country": "PT"})
    assert ranking.candidates(pid)["city_ids"] == ["lisbon"]


def test_key_market_regions_stay_text_and_countries_only_inform(client, markets_env):
    markets_env.qloo.brand["tags"] = [{"tag_id": "urn:tag:key_market:qloo:europe", "name": "Europe", "type": "urn:tag:key_market:qloo"},
                                      {"tag_id": "urn:tag:key_market:qloo:belgium", "name": "Belgium", "type": "urn:tag:key_market:qloo"}]
    pid = matched(client, markets_env)
    pres = client.get(f"/api/projects/{pid}/presence").json()
    assert pres["unresolved_regions"] == ["Europe"] and "brussels" in ranking.candidates(pid)["city_ids"]
    be = next(c for c in pres["countries"] if c["iso"] == "BE")
    assert be["sources"][0]["kind"] == "QLOO_KEY_MARKET" and not be["leave_out"]
    r = client.put(f"/api/projects/{pid}/presence", json={"base_version": pres["version"], "leave_out_countries": ["BE"]})
    assert r.status_code == 200 and "brussels" not in ranking.candidates(pid)["city_ids"]
    assert ranking.candidates(pid)["excluded"]["brussels"] == "LEFT_OUT_BY_YOU"


def test_store_coverage_partial_and_a_founder_restore_survives_recomputation(client, markets_env):
    markets_env.qloo.stores["Tidewell"] = ["toronto"] * 60
    markets_env.qloo.store_fail = {2}
    pid = matched(client, markets_env)
    pres = client.get(f"/api/projects/{pid}/presence").json()
    assert pres["store_search"]["status"] == "partial" and "stopped partway" in pres["coverage_line"]
    assert ranking.candidates(pid)["excluded"]["toronto"] == "ALREADY_HERE"
    client.put(f"/api/projects/{pid}/presence", json={"base_version": pres["version"], "remove_cities": ["toronto"]})
    assert "toronto" in ranking.candidates(pid)["city_ids"]
    ranking.located_checks(SimpleNamespace(id="r", cassette=runner_cassette(pid), project_id=pid), pid, ["london"], "Tidewell")
    rec = db.latest("current_presence", pid)
    assert rec["version"] == pres["version"] + 2 and rec["overrides"]["remove_cities"] == ["toronto"]  # kept
    assert "toronto" in ranking.candidates(pid)["city_ids"]


def runner_cassette(pid):
    from landfall import runner
    return runner.cassette_for(pid)


def test_capped_coverage_triggers_located_checks_and_a_found_store_gives_its_slot_to_the_next_city(client, markets_env):
    us = [c for c in markets.ORDER if markets.CITIES[c]["country"] == "US"]
    markets_env.qloo.stores["Tidewell"] = (us * 20)[:200]  # 4 full pages
    markets_env.qloo.located = {"Tidewell": ["toronto"]}  # a store on "page 5": only a located search finds it
    pid, m = scanned35(client, markets_env)
    assert m["presence"]["store_search"]["status"] == "capped" and "more may exist" in m["presence"]["coverage_line"]
    cards = [c["city_id"] for c in m["assessment"]["cards"]]
    assert cards == RECOMMENDED[1:] + ["copenhagen"]  # Toronto found, the next ranked city took the slot
    assert len(markets_env.qloo.of("urn:entity:destination")) == 2  # from the same ranking: no new ranking call
    assert len(markets_env.qloo.located_calls()) == 7  # 6, then the one replacement
    toronto = next(c for c in m["cities"] if c["city_id"] == "toronto")
    assert toronto["deferred"]["reason"] == "ALREADY_HERE" and "found one of your stores" in toronto["deferred"]["line"]
    assert all(c["located_check"] == "none_found" for c in m["assessment"]["cards"])
    assert not [n for n in m["notices"] if n["action"] == "update"]  # a located store doesn't outdate the ranking


# ---- stand-ins (R4) ----
def test_genre_map_and_charities():
    pid = _project()
    f = lambda v: db.put_record("claim", pid, {"subject": {"key": "products"}, "label": "What you sell", "value": v})  # noqa: E731
    assert ranking.genre_for({"facts": [f("Watches and sunglasses")["id"]]}) == "urn:tag:genre:brand:fashion:accessories"
    assert ranking.genre_for({"facts": [f("Backpacks made from ocean plastic")["id"]]}) == "urn:tag:genre:brand:fashion"
    assert ranking.genre_for({"facts": [f("Sparkling cold-brewed tea")["id"]]}) == "urn:tag:genre:brand:food_and_beverage:drinks"
    assert ranking.genre_for({"facts": [f("Hand-thrown ceramics")["id"]]}) is None  # unknown: no filter, labelled
    assert ranking.is_charity({"name": "Surfrider Foundation"}) and ranking.is_charity(
        {"name": "Waves For Water", "properties": {"short_description": "A non-profit organization"}})
    assert not ranking.is_charity({"name": "Vissla", "properties": {"short_description": "A brand you can trust"}})


def test_captured_standins_leave_the_charities_out():
    ents = body(94)["results"]["entities"]
    kept = [e["name"] for e in ents if not ranking.is_charity(e)]
    assert kept[:3] == ["Vissla", "Outerknown", "Twothirds"] and "Surfrider Foundation" not in kept
    assert "Waves For Water" not in kept


@pytest.fixture
def no_match(client, markets_env, monkeypatch):
    """GOT BAG-like: Qloo returns no brand; a SYNTHETIC Profile agent proposes one recorded tag, which is confirmed."""
    markets_env.qloo.brand = None

    def propose(run, brief, facts):
        db.x("INSERT OR IGNORE INTO qloo_refs VALUES(?,?,?,?,?)", run.project_id, "urn:tag:keyword:qloo:ocean", "tag",
             "Ocean", "urn:tag:keyword:qloo")
        return {"signals": [{"qloo_id": "urn:tag:keyword:qloo:ocean", "kind": "tag", "name": "Ocean", "basis_facts": [1],
                             "reasoning": "SYNTHETIC"}], "searches": {"tags": 1, "brands": 0}}
    monkeypatch.setattr(identity, "_propose", propose)
    pid = client.post("/api/projects", json={"url": "http://tidewell.example/", "purpose": "test_demand"}).json()["project_id"]
    idle(pid)
    prof = client.get(f"/api/projects/{pid}").json()["profile"]
    r = client.put(f"/api/projects/{pid}/profile", json={"base_version": prof["version"], "confirm": True,
                                                        "signals": [{"qloo_id": "urn:tag:keyword:qloo:ocean", "kind": "tag"}]})
    assert r.status_code == 200
    idle(pid)
    return pid


def test_no_match_stand_ins_rank_and_explain_and_a_swap_updates_without_a_scan(client, markets_env, no_match):
    pid = no_match
    goal = client.get(f"/api/projects/{pid}").json()["goal"]
    si = goal["standins"]
    assert [b["name"] for b in si["brands"]] == ["Vissla", "Outerknown", "Twothirds"]
    assert si["excluded"][0]["name"] == "Surfrider Foundation" and "Brands with a similar following" in si["line"]
    aud = goal["audience"]
    assert aud["kind"] == "standins" and "used as a stand-in for you" in aud["label"] and ranking.CAVEAT in aud["label"]
    assert len(aud["demographics"]["lines"]) == 3  # one attributed row per stand-in, never blended
    prof_v = db.latest("profile", pid)["version"]
    client.post(f"/api/projects/{pid}/scan")
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    first = m["assessment"]["cards"][0]
    assert first["position_line"].endswith("for people who like Vissla, Outerknown and Twothirds (stand-ins).")
    rank_calls, places = len(markets_env.qloo.of("urn:entity:destination")), len(markets_env.qloo.places())
    # swap Twothirds for OluKai (returned in the stand-in response, so it is a recorded brand)
    r = client.put(f"/api/projects/{pid}/standins", json={"base_version": si["version"], "brands": ["SYN-SI-1", "SYN-SI-3", "SYN-SI-5"]})
    assert r.status_code == 200
    m = client.get(f"/api/projects/{pid}/markets").json()
    n = next(x for x in m["notices"] if x["action"] == "update")
    assert n["rerank"] and m["audience"]["from_previous_standins"]
    aud_calls = len(markets_env.qloo.of("urn:audience"))
    client.post(f"/api/projects/{pid}/shortlist/update")
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert len(markets_env.qloo.of("urn:entity:destination")) - rank_calls == 2 and len(markets_env.qloo.places()) == places
    assert len(markets_env.qloo.of("urn:audience")) - aud_calls == 1 and not m["audience"]["from_previous_standins"]
    assert "OluKai" in m["assessment"]["cards"][0]["position_line"] and db.latest("profile", pid)["version"] == prof_v
    assert m["generation"]["current"]  # the place scan keeps the profile's own signal
    # removing every stand-in leaves the no-order state; a brand search adds one back
    v = db.latest("standins", pid)["version"]
    client.put(f"/api/projects/{pid}/standins", json={"base_version": v, "brands": []})
    client.post(f"/api/projects/{pid}/shortlist/update")
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert m["ranking"]["state"] == "no_signal" and m["assessment"]["cards"] == []
    found = client.get(f"/api/projects/{pid}/qloo/brands", params={"q": "Outer"}).json()["results"]
    assert client.put(f"/api/projects/{pid}/standins", json={"base_version": v + 1, "brands": [found[0]["qloo_id"]]}).status_code == 200
    assert client.put(f"/api/projects/{pid}/standins", json={"base_version": v + 2, "brands": ["INVENTED"]}).status_code == 422


def test_test_demand_keys_use_language_bloc_and_hours():
    home = {"country": "DE", "lat": 52.5, "lon": 13.4}
    vienna, tokyo = ranking.entry_facts("vienna", home), ranking.entry_facts("tokyo", home)
    assert (vienna["shared_language"], vienna["same_bloc"], vienna["overlap_hours"]) == (True, True, 8)
    assert ranking.test_demand_key(vienna) == 3 and ranking.test_demand_key(tokyo) == 0
    assert ranking.entry_chip(ranking.entry_facts("berlin", home)) == "SAME_COUNTRY"
    assert ranking.entry_chip(ranking.entry_facts("london", home)) == "IMPORT_NEW_LANGUAGE"
    unknown = ranking.entry_facts("paris", {"country": None})
    assert ranking.entry_chip(unknown) == "FIT_NOT_KNOWN" and ranking.test_demand_key(unknown) == 0


# ---- QlooRanking (R1) ----
class QG:
    """SYNTHETIC qloo.get for rank_query: page → (status, rows)."""

    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def __call__(self, path, params, *a, **k):
        self.calls.append(dict(params))
        status, rows = self.pages.get(int(params["page"]), (200, []))
        return {"status": status, "body": json.dumps({"success": True, "results": {"entities": rows}})}


def dest(cid, a):
    return {"entity_id": ranking.DESTINATIONS[cid]["qloo_id"], "name": cid, "query": {"affinity": a}}


RUN = SimpleNamespace(id="run_t", cassette=None)
SIG = {"entity_ids": ["B"], "names": ["Brand"], "label": "people who like Brand"}


def cand(ids):
    return {"city_ids": sorted(ids), "excluded": {"wellington": "NOT_RANKED", "lima": "NOT_RANKED"}}


def rank(monkeypatch, pages, ids):
    g = QG(pages)
    monkeypatch.setattr(qloo, "get", g)
    return ranking.rank_query(RUN, "prj", SIG, cand(ids)), g


def test_one_query_and_its_pages_only(monkeypatch):
    ids = markets.ORDER[:60]
    ids = [c for c in ids if c not in ("wellington", "lima")]
    p1 = [dest(c, 0.9 - i / 100) for i, c in enumerate(ids[:50])]
    p2 = [dest(c, 0.3 - i / 100) for i, c in enumerate(ids[50:])] + [dest("paris", 0.01), dest("lagos", 0.01)]
    r, g = rank(monkeypatch, {1: (200, p1), 2: (200, p2)}, ids)
    assert [c["page"] for c in g.calls] == [1, 2]
    assert all({k: v for k, v in c.items() if k != "page"} == {k: v for k, v in g.calls[0].items() if k != "page"}
               for c in g.calls)  # identical parameters except the page: never chunked
    assert r["state"] == "ranked" and r["positions"][ids[0]] == 1 and r["positions"][ids[-1]] == len(ids)
    assert [x["why"] for x in r["ignored"]] == ["repeated", "not a candidate"]  # paris again; lagos not a candidate
    assert not hasattr(ranking, "merge") and not hasattr(ranking, "chunks")


@pytest.mark.parametrize("pages, state", [
    ({1: (500, [])}, "failed"), ({1: (200, [])}, "empty"),
    ({1: (200, [dest(c, 0.5) for c in ["paris", "berlin", "oslo"]])}, "flat"),
    ({1: (200, [dest("paris", 0.9), dest("berlin", 0.8)])}, "ranked"),
])
def test_outcome_classes(monkeypatch, pages, state):
    r, _ = rank(monkeypatch, pages, ["paris", "berlin", "oslo"])
    assert r["state"] == state
    if state != "ranked":
        assert r["positions"] == {}  # no recommendation from an empty, flat or failed ranking
    else:
        assert r["unranked"]["oslo"] == "omitted" and r["unranked"]["wellington"] == "unmapped"


def test_a_second_page_failure_keeps_the_first_pages_positions(monkeypatch):
    ids = [c for c in markets.ORDER if c not in ("wellington", "lima")][:70]
    r, _ = rank(monkeypatch, {1: (200, [dest(c, 0.9 - i / 100) for i, c in enumerate(ids[:50])]), 2: (503, [])}, ids)
    assert r["state"] == "ranked_partial" and len(r["positions"]) == 50
    assert set(r["unranked"]) - {"wellington", "lima"} == set(ids[50:])
    assert all(v == "beyond_failed_page" for c, v in r["unranked"].items() if c in ids)


def test_allowance_denied_before_the_first_page(monkeypatch):
    def deny(*a, **k):
        raise ranking.BudgetExhausted("cap")
    monkeypatch.setattr(qloo, "get", deny)
    assert ranking.rank_query(RUN, "prj", SIG, cand(["paris"]))["state"] == "denied"


def test_zero_candidates_make_zero_calls(client, markets_env):
    pid = matched(client, markets_env, scope="domestic")
    correct_home(client, pid, "Lisbon, Portugal")  # the only Portuguese catalog city is home: nothing left to rank
    before = len(markets_env.qloo.of("urn:entity:destination"))
    client.post(f"/api/projects/{pid}/scan")
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert m["ranking"]["state"] == "no_candidates" and len(markets_env.qloo.of("urn:entity:destination")) == before
    assert m["assessment"]["cards"] == [] and "every catalog city" in m["ranking"]["text"]


def test_the_ranking_is_never_shown_with_its_affinity(client, markets_env):
    pid, m = scanned35(client, markets_env)
    assert "affinity" not in json.dumps(m) and m["ranking"]["state"] == "ranked"


# ---- the decision rule (R2) ----
P = {c: i for i, c in enumerate(["toronto", "paris", "berlin", "lisbon", "oslo", "vienna", "milan", "tokyo", "seoul"], 1)}


def ids(groups, key="all"):
    return [c["city_id"] for c in next(g for g in groups if g["key"] == key)["cities"]]


def test_positions_1_3_5_with_rising_entry_ease_give_no_cycle():
    pos = {"toronto": 1, "paris": 2, "berlin": 3, "lisbon": 4, "oslo": 5}
    keys = {"toronto": 0, "berlin": 1, "oslo": 2}  # entry ease rises with position: a pairwise rule would cycle
    out = ids(ranking.decide(pos, "international", None, keys))
    assert out == ["berlin", "toronto", "paris", "lisbon", "oslo"]  # oslo is in the next block: it can't jump it


def test_window_edges_and_shuffled_input():
    keys = {"lisbon": 1, "oslo": 1}  # lisbon 4th (last of block 1) moves to 1st; oslo 5th stays first of block 2
    out = ids(ranking.decide(P, "international", None, keys))
    assert out == ["lisbon", "toronto", "paris", "berlin", "oslo", "vienna"]
    shuffled = dict(sorted(P.items(), key=lambda kv: kv[0][::-1]))
    assert ids(ranking.decide(shuffled, "international", None, dict(reversed(list(keys.items()))))) == out
    moved = ranking.decide(P, "international", None, keys)[0]["cities"]
    assert [c["moved_by_purpose"] for c in moved] == [True, True, True, True, False, False]
    assert max(abs(c["group_rank"] - c["final_position"]) for c in moved) <= 3
    assert ids(ranking.decide(P, "international", None, {})) == list(P)[:6]  # missing keys count 0: Qloo's order


def test_both_groups_never_transfer_slots_or_pad():
    pos = {"seattle": 3, "london": 1, "paris": 2, "berlin": 4, "tokyo": 5}
    g = ranking.decide(pos, "both", "US", {})
    assert ids(g, "home") == ["seattle"] and ids(g, "abroad") == ["london", "paris", "berlin"]
    assert [c["role"] for c in g[0]["cities"]] == ["TOP_RECOMMENDATION"]
    g = ranking.decide({"london": 1, "paris": 2}, "both", "US", {})
    assert ids(g, "home") == [] and ids(g, "abroad") == ["london", "paris"]  # an empty group has no top; no padding
    assert ranking.decide({}, "international", None, {})[0]["cities"] == []


def test_the_stockist_band_rejects_cafes_tagged_shopping():
    p = lambda *gs: {"genres": [{"qloo_id": f"{G}:{g}"} for g in gs]}  # noqa: E731
    assert ranking.is_stockist(p("clothing_store")) and ranking.is_stockist(p("boutique"))
    assert ranking.is_stockist(p("shopping", "department_store")) and ranking.is_stockist(p("barber_shop", "shoe_store"))
    assert not ranking.is_stockist(p("shopping"))  # the generic genre alone (REVIEW-SLICE-03 counterexample)
    assert not ranking.is_stockist(p("shopping", "restaurant:cafe", "gift_shop"))
    assert not ranking.is_stockist(p("coffee_shop", "book_store")) and not ranking.is_stockist(p("wine_bar", "wine_store"))


def test_captured_shopping_cafes_are_not_stockists():
    from test_markets import captured_places
    places = [markets.parse_place(e, 1) for _, es in captured_places() for e in es]
    cafes = [p for p in places if any(g["qloo_id"].endswith(":shopping") for g in p["genres"])
             and any("cafe" in g["qloo_id"] or "restaurant" in g["qloo_id"] for g in p["genres"])]
    assert cafes and not any(ranking.is_stockist(p) for p in cafes)


def test_stockists_reorder_only_near_ties_and_disclose_it(client, markets_env):
    markets_env.qloo.stores["Tidewell"] = ["amsterdam", "brussels", "madrid", "istanbul", "bogota"]
    markets_env.qloo.rank_order = ["amsterdam", "paris", "berlin", "copenhagen", "lisbon", "oslo", *markets.ORDER]
    boutiques = [f"{G}:boutique", f"{G}:clothing_store", f"{G}:shoe_store", G]
    markets_env.qloo.place_kinds = {"lisbon": boutiques, "oslo": boutiques}
    pid, m = scanned35(client, markets_env, purpose="stockists")
    cards = m["assessment"]["cards"]
    assert [c["city_id"] for c in cards] == ["lisbon", "paris", "berlin", "copenhagen", "oslo", "toronto"]
    assert cards[0]["moved_line"].startswith("Moved up for your goal (Qloo's position: 4th)")
    assert cards[1]["moved_line"].startswith("Moved down within its block of 4") and cards[4]["moved_line"] is None
    assert cards[0]["position_line"].startswith("In one Qloo ranking of your") and "came 4th" in cards[0]["position_line"]
    assert {c["city_id"]: c["excluded"] for c in m["cities"]}["amsterdam"] == "ALREADY_HERE"
    assert "stockists" in m["assessment"]["why_order"]


def test_both_with_stores_at_home_gives_two_tops(client, markets_env):
    us = [c for c in markets.ORDER if markets.CITIES[c]["country"] == "US"]
    markets_env.qloo.stores["Tidewell"] = us[:8]
    pid, m = scanned35(client, markets_env, scope="both")
    groups = {g["key"]: g for g in m["assessment"]["groups"]}
    assert groups["home"]["city_ids"] == ["minneapolis", "philadelphia"] and groups["abroad"]["city_ids"] == ["toronto", "vancouver", "montreal"]
    cards = {c["city_id"]: c for c in m["assessment"]["cards"]}
    assert cards["minneapolis"]["role_label"] == cards["toronto"]["role_label"] == "TOP RECOMMENDATION"
    assert not set(us[:8]) & set(cards) and m["presence"]["store_search"]["status"] == "complete"
    assert m["assessment"]["lead"]["source"] in ("MODEL", "DETERMINISTIC")


# ---- AudienceProfile (R5) ----
@pytest.mark.parametrize("index, word", [(None, "not known"), (0.149, "neutral"), (-0.15, "leans"), (0.49, "leans"),
                                         (0.5, "strongly"), (-0.71, "strongly")])
def test_index_to_words(index, word):
    assert ranking.words_for(index) == word


def test_opposing_rows_read_mixed_and_three_rows_stay_attributed():
    rows = [{"signal_id": d["entity_id"], "signal_name": d["entity_id"].split(":")[-1], **d["query"]}
            for d in body(96)["results"]["demographics"]]
    words = ranking.demographics_words(rows)
    assert words["summary"] == "mixed" and len(words["lines"]) == 2
    assert words["lines"][0]["text"] == "strongly 45–54, leans men" and words["lines"][1]["text"].endswith("leans women")
    three = [{"signal_id": d["entity_id"], "signal_name": d["entity_id"][:4], **d["query"]}
             for d in body(105)["results"]["demographics"]]
    assert [ln["signal_id"] for ln in ranking.demographics_words(three)["lines"]] == [r["signal_id"] for r in three]


def test_removal_urns_and_a_racing_removal(client, markets_env, monkeypatch):
    pid = matched(client, markets_env)
    ap = client.get(f"/api/projects/{pid}/audience").json()
    assert [a["name"] for a in ap["audiences"]] == ["Watches", "Surfing", "Hiking"] and ranking.CAVEAT in ap["label"]
    v = ap["version"]
    cas = runner_cassette(pid)
    urns = ranking.resolve_urns(pid, cas, None, ["SYN-AUD-1", "SYN-AUD-2"])
    assert urns["SYN-AUD-1"]["urn"] == "urn:audience:syn:watches" and urns["SYN-AUD-1"]["state"] == "resolved"
    assert urns["SYN-AUD-2"]["urn"] is None and urns["SYN-AUD-2"]["state"] == "unresolved"  # the row was another UUID
    assert db.latest("audience_profile", pid)["version"] == v  # enrichment never versions AudienceProfile
    # a removal racing a resolution: the removal wins and is never resurrected
    real = qloo.get

    def slow_get(path, params, *a, **k):
        if path == "/v2/audiences":
            ranking.remove_audiences(pid, db.latest("audience_profile", pid)["version"], ["SYN-AUD-3"])
        return real(path, params, *a, **k)
    monkeypatch.setattr(qloo, "get", slow_get)
    out = ranking.resolve_urns(pid, cas, None, ["SYN-AUD-3"])
    assert "SYN-AUD-3" not in out
    ap = client.get(f"/api/projects/{pid}/audience").json()
    assert [a["name"] for a in ap["removed"]] == ["Hiking"]
    r = client.put(f"/api/projects/{pid}/audience", json={"base_version": ap["version"], "remove": ["SYN-AUD-1", "SYN-AUD-2"]})
    assert r.status_code == 200 and client.get(f"/api/projects/{pid}/audience").json()["audiences"] == []  # all removed
    assert client.put(f"/api/projects/{pid}/audience", json={"base_version": 1, "remove": []}).status_code == 409


def test_an_audience_failure_is_not_known_with_a_retry(client, markets_env):
    markets_env.qloo.audience_fail = True
    pid = matched(client, markets_env)
    ap = client.get(f"/api/projects/{pid}/audience").json()
    assert ap["status"] == {"audiences": "failed", "demographics": "failed"} and ap["audiences"] == []
    assert ap["demographics"]["summary"] == "not known"
    markets_env.qloo.audience_fail = False
    n = len(markets_env.qloo.of("urn:audience")) + len(markets_env.qloo.of("urn:demographics"))
    assert client.post(f"/api/projects/{pid}/audience/retry").status_code == 200
    idle(pid)
    ap = client.get(f"/api/projects/{pid}/audience").json()
    assert ap["status"] == {"audiences": "ok", "demographics": "ok"}
    assert len(markets_env.qloo.of("urn:audience")) + len(markets_env.qloo.of("urn:demographics")) == n + 2
    assert client.post(f"/api/projects/{pid}/audience/retry").status_code == 409


# ---- the validator (R6) ----
def city(cid, places=1, comps=(), status="returned"):
    name = markets.CITIES[cid]["name"]
    return {"city_id": cid, "name": name, "country": markets.CITIES[cid]["country"], "group": "all", "scan_status": status,
            "places": [{"qloo_id": f"{cid}-p{i}", "name": f"{name} shop {chr(65 + i)}", "genres": ["Shop"]} for i in range(places)],
            "channels": {"retail": places} if places else None, "comparables": list(comps),
            "entry": {"chip": "SAME_TRADE_BLOC"}}


def inp_of(*cs, kind="brand"):
    return {"cities": list(cs), "groups": [{"key": "all", "label": "Recommended", "cap": 6, "available": 9,
                                            "city_ids": [c["city_id"] for c in cs]}], "n_candidates": 64,
            "signal": {"kind": kind, "label": "people who like Komono", "names": ["Komono"]}}


def reply(cid, **kw):
    return {"city_id": cid, "why": f"Komono fans find {markets.CITIES[cid]['name']} shop A here.", "refs": [f"{cid}-p0"],
            "different": f"Shops in {markets.CITIES[cid]['name']}.", "could_change": "A shop visit.", "ways_in": "Retail", **kw}


AESOP = {"name": "Aesop", "stores": 14, "capped": False, "state": "ok"}


def test_an_invented_store_count_fails_and_a_cited_count_passes():
    inp = inp_of(city("paris", comps=[AESOP]))
    bad = scan.explain({"lead": "Paris first.", "cities": [reply("paris", why="Qloo lists 99 Aesop stores here.", refs=["Aesop"])]}, inp)
    assert bad["cards"][0]["prose_source"]["why"] == "DETERMINISTIC" and "14 Aesop stores" in bad["cards"][0]["why"]
    ok = scan.explain({"lead": "Paris first.", "cities": [reply("paris", why="Qloo lists 14 Aesop stores near here.", refs=["Aesop"])]}, inp)
    assert ok["cards"][0]["prose_source"]["why"] == "MODEL"


@pytest.mark.parametrize("field, text", [
    ("why", "Qloo ranks Paris high for Komono."), ("why", "Paris is a mature market with a reputation for watches."),
    ("different", "Paris outranks the others."), ("different", "Twice as many shops as elsewhere."),
    ("why", "Unlike Lisbon, Paris has shop A."),
])
def test_ranking_reputation_and_cross_city_prose_is_replaced(field, text):
    out = scan.explain({"lead": "", "cities": [reply("paris", **{field: text})]}, inp_of(city("paris"), city("lisbon")))
    assert out["cards"][0]["prose_source"][field] == "DETERMINISTIC" and out["faults"][0]["field"] == field


def test_proxy_audiences_described_as_your_customers_are_replaced():
    text = "Your customers love Paris shop A."
    standin = scan.explain({"lead": "", "cities": [reply("paris", why=text)]}, inp_of(city("paris"), kind="standins"))
    assert standin["cards"][0]["prose_source"]["why"] == "DETERMINISTIC"
    assert "your customers" in scan.explain({"lead": "", "cities": [reply("paris", why=text)]},
                                            inp_of(city("paris")))["cards"][0]["why"].lower()


def test_order_omissions_extras_repeats_and_total_failure():
    cs = [city("paris"), city("berlin"), city("lisbon", places=0, status="failed")]
    out = scan.explain({"lead": "Berlin first.", "cities": [reply("lisbon"), reply("berlin", different="Shops in Paris."),
                                                             reply("tokyo"), reply("paris", different="Shops in Paris.")]},
                       inp_of(*cs))
    assert [c["city_id"] for c in out["cards"]] == ["paris", "berlin", "lisbon"] and out["ignored"] == ["tokyo"]
    assert out["lead"]["source"] == "DETERMINISTIC"  # Berlin isn't the top
    assert out["cards"][1]["prose_source"]["different"] == "DETERMINISTIC"  # names another city
    lisbon = out["cards"][2]
    assert lisbon["prose_source"]["why"] == "DETERMINISTIC"  # cites a place that didn't come back in its own scan
    assert "failed here; this rests on the ranking alone" in lisbon["why"]
    same = scan.explain({"lead": "", "cities": [reply("paris", different="Shops beside cafés."),
                                                reply("berlin", different="Shops beside cafés.")]}, inp_of(city("paris"), city("berlin")))
    assert [c["prose_source"]["different"] for c in same["cards"]] == ["MODEL", "DETERMINISTIC"]
    none = scan.explain(None, inp_of(*cs))
    assert all(set(c["prose_source"].values()) == {"DETERMINISTIC"} for c in none["cards"]) and not none["faults"]
    assert none["lead"] == {"text": "Paris is the top recommendation for people who like Komono. Each card states where it "
                                    "came in one Qloo ranking of your 64 candidate cities, and why.", "source": "DETERMINISTIC"}
    assert scan.explain({"lead": "Paris first: shop A.", "cities": []}, inp_of(city("paris")))["lead"]["source"] == "MODEL"


def test_position_lines_and_ordinals():
    assert [ranking.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 64)] == \
        ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "64th"]
    assert ranking.position_line(64, "Paris", 2, "people who like Komono") == \
        "In one Qloo ranking of your 64 candidate cities, Paris came 2nd for people who like Komono."


# ---- the update matrix (R4) ----
def test_purpose_change_reuses_the_ranking_and_audience_removal_rewrites_prose_only(client, markets_env):
    pid, m = scanned35(client, markets_env)
    gid = m["generation"]["id"]
    counts = lambda: (len(markets_env.qloo.of("urn:entity:destination")), len(markets_env.qloo.places()),  # noqa: E731
                      len(markets_env.qloo.of("urn:audience")))
    before = counts()
    v = client.get(f"/api/projects/{pid}").json()["intent"]["version"]
    r = client.patch(f"/api/projects/{pid}/intent", json={"base_version": v, "purpose": "test_demand"}).json()
    assert "no Qloo requests" in r["notice"]
    n = next(x for x in client.get(f"/api/projects/{pid}/markets").json()["notices"] if x["action"] == "update")
    assert not n["rerank"] and "your purpose changed" in n["text"]
    client.post(f"/api/projects/{pid}/shortlist/update")
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert counts() == before and markets_env.strategist.calls == 2 and m["assessment"]["goal"]["purpose"] == "test_demand"
    ap = m["audience"]
    client.put(f"/api/projects/{pid}/audience", json={"base_version": ap["version"], "remove": ["SYN-AUD-1"]})
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert "you removed an audience they used" in next(x for x in m["notices"] if x["action"] == "update")["text"]
    client.post(f"/api/projects/{pid}/shortlist/update")
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert counts() == before and markets_env.strategist.calls == 3
    assert "Watches" not in db.latest("assessment_set", pid)["input"]["audience_names"]
    assert m["generation"]["id"] == gid and not [x for x in m["notices"] if x["action"] == "update"]


def test_scope_and_presence_changes_rerank_without_a_scan_and_keep_selections(client, markets_env):
    pid, m = scanned35(client, markets_env)
    sl = m["shortlist"]
    client.patch(f"/api/projects/{pid}/shortlist", json={"base_version": sl["version"], "exclude": ["vancouver"], "add": ["paris"]})
    h = ranking.candidates(pid)["hash"]
    pres = client.get(f"/api/projects/{pid}/presence").json()
    client.put(f"/api/projects/{pid}/presence", json={"base_version": pres["version"], "add_cities": ["toronto"]})
    assert ranking.candidates(pid)["hash"] != h and ranking.candidates(pid)["excluded"]["toronto"] == "ALREADY_HERE"
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert next(x for x in m["notices"] if x["action"] == "update")["rerank"]
    pages, places = len(markets_env.qloo.of("urn:entity:destination")), len(markets_env.qloo.places())
    client.post(f"/api/projects/{pid}/shortlist/update")
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert len(markets_env.qloo.of("urn:entity:destination")) - pages == 2 and len(markets_env.qloo.places()) == places
    picks = {c["city_id"]: c for c in m["shortlist"]["cities"]}
    assert "toronto" not in picks and picks["vancouver"]["included"] is False and picks["paris"]["added_by_founder"]
    assert [c["city_id"] for c in m["assessment"]["cards"]][0] == "vancouver"


def test_an_edit_during_a_run_is_flagged_not_overwritten(client, markets_env):
    pid = matched(client, markets_env)

    def founder_edits_meanwhile(out):  # SYNTHETIC: the founder changes the purpose while the strategist writes
        fi = db.latest("founder_input", pid)
        payload = {k: v for k, v in fi.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")}
        db.put_record("founder_input", pid, {**payload, "purpose": "stockists"}, rid=fi["id"])
        db.event(pid, "founder", {"task": "Changed your goal", "detail": "SYNTHETIC"})
        return out
    markets_env.strategist.mutate = founder_edits_meanwhile
    client.post(f"/api/projects/{pid}/scan")
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    n = next(x for x in m["notices"] if x["action"] == "update")
    assert "Your change arrived while the last recommendations were being written" in n["text"]
    late = db.latest("assessment_set", pid)
    assert late["fenced"]["changed"] == ["founder_input"] and late["inputs"] == []  # history, no consumed-input claim
    assert db.latest("shortlist", pid) is None  # not made current
    assert db.latest("founder_input", pid)["purpose"] == "stockists"  # the founder's newer decision stands
    markets_env.strategist.mutate = None
    client.post(f"/api/projects/{pid}/shortlist/update")
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert m["assessment"]["goal"]["purpose"] == "stockists" and not [x for x in m["notices"] if x["action"] == "update"]
    fi = db.latest("founder_input", pid)
    assert {"id": fi["id"], "version": fi["version"], "keys": ["scope", "purpose", "leave_out_presence", "home_country",
            "exclude"]} in db.latest("assessment_set", pid)["inputs"] and m["shortlist"]["version"] == 1


def test_a_ranking_failure_gives_the_no_order_state_and_retry_succeeds(client, markets_env):
    markets_env.qloo.rank_fail = {1}
    pid, m = scanned35(client, markets_env)
    assert m["ranking"]["state"] == "failed" and m["assessment"]["cards"] == [] and m["ranking"]["retryable"]
    assert [n["action"] for n in m["notices"]] == ["retry_ranking"] and m["coverage"]["returned"] == 80
    markets_env.qloo.rank_fail = set()
    client.post(f"/api/projects/{pid}/ranking/retry")
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert [c["city_id"] for c in m["assessment"]["cards"]] == RECOMMENDED and m["notices"] == []
    assert client.post(f"/api/projects/{pid}/ranking/retry").status_code == 409  # nothing to retry


def test_a_partial_ranking_recommends_from_its_first_page(client, markets_env):
    markets_env.qloo.rank_fail = {2}
    pid, m = scanned35(client, markets_env)
    assert m["ranking"]["state"] == "ranked_partial" and [c["city_id"] for c in m["assessment"]["cards"]] == RECOMMENDED
    assert "retry_ranking" in [n["action"] for n in m["notices"]]
    late = [c for c in m["cities"] if m["ranking"]["unranked"].get(c["city_id"]) == "beyond_failed_page"]
    assert late and all(c["deferred"]["reason"] == "NOT_RANKED" for c in late)


def test_l35a_counterexamples_refs_by_name_and_demand_phrasing():
    """LIVE L35-A (Untuckit, market_strategist@2): the model cited names, not ids, and claimed receptive markets."""
    paris = city("paris", places=2, comps=[AESOP, {"name": "J.Crew", "stores": 5, "capped": False, "state": "ok"}])
    assert scan.resolve_ref("Paris shop B", paris) == "paris-p1" and scan.resolve_ref("J.Crew 5", paris) == "J.Crew"
    assert scan.resolve_ref("aesop", paris) == "Aesop" and scan.resolve_ref("Lisbon shop A", paris) is None
    ok = scan.explain({"lead": "", "cities": [reply("paris", why="J.Crew has 5 stores near Paris shop B.", refs=["J.Crew 5", "Paris shop B"])]},
                      inp_of(paris))
    assert ok["cards"][0]["prose_source"]["why"] == "MODEL" and ok["cards"][0]["refs"] == ["J.Crew", "paris-p1"]
    for text in ("J.Crew has 5 stores, indicating a receptive market for similar brands.",
                 "Aesop's 14 stores show potential for similar brands here.", "Strong market potential near Paris shop A."):
        assert markets.guard(text), text


def test_l35a_counterexamples_codes_in_prose_and_invented_groups():
    """LIVE L35-A re-record (market_strategist@3): ids copied into prose and refs; a lead with invented groups."""
    paris = city("paris", places=3)
    assert scan.resolve_ref("P2", paris) == "paris-p1" and scan.resolve_ref("[P3]", paris) == "paris-p2"
    assert scan.resolve_ref("P9", paris) is None and scan.resolve_ref("paris-p0 Paris shop A", paris) == "paris-p0"
    out = scan.explain({"lead": "", "cities": [reply("paris", why="Near P1 Paris shop A.", refs=["P1"]),
                                               ]}, inp_of(paris))
    assert out["faults"][0]["reason"] == "shows a reference code"
    guid = "Near the 9A56597E-6654-4F1D-BD30-2F24A7CBDE89 Topgolf."
    assert scan._fault(guid, paris, inp_of(paris)) == "shows a reference code"
    prompt = scan.prompt_of({**inp_of(paris), "brand_name": "Komono", "goal": {"scope": "international", "purpose": "presence"},
                             "audience_names": []})
    assert "[P1] Paris shop A" in prompt and "GROUPS: Recommended: Paris" in prompt and "paris-p0" not in prompt


def test_l35a_tuning_counterexamples_lead_tops_and_place_mix_counts():
    """Prompt tuning on L35-A (25 Oct): a lead naming the wrong top of a group; place-mix counts in `different`."""
    home, mont, hel = city("minneapolis"), city("montreal"), city("helsinki")
    inp = {**inp_of(home, mont, hel), "groups": [{"key": "home", "label": "At home", "cap": 2, "available": 1, "city_ids": ["minneapolis"]},
                                                 {"key": "abroad", "label": "Abroad", "cap": 3, "available": 3, "city_ids": ["montreal", "helsinki"]}]}
    wrong = "Minneapolis is first at home, with shop A. Helsinki is first abroad."
    assert scan.explain({"lead": wrong, "cities": []}, inp)["lead"]["source"] == "DETERMINISTIC"
    right = "Minneapolis is first at home, with shop A. Montreal is first abroad, then Helsinki."
    assert scan.explain({"lead": right, "cities": []}, inp)["lead"]["source"] == "MODEL"
    mix = {**city("paris"), "channels": {"retail": 5, "food_drink": 17}}
    out = scan.explain({"lead": "", "cities": [reply("paris", different="Mostly cafés: 17 food and drink places and 5 shops.")]}, inp_of(mix))
    assert out["cards"][0]["prose_source"]["different"] == "MODEL"


def test_a_city_name_inside_an_own_place_name_is_not_another_city():
    lisbon = {**city("lisbon"), "places": [{"qloo_id": "L1", "name": "Copenhagen Coffee Lab & Bakery", "genres": ["Cafe"]}]}
    out = scan.explain({"lead": "", "cities": [reply("lisbon", why="Qloo returned Copenhagen Coffee Lab & Bakery here.", refs=["L1"])]},
                       inp_of(lisbon))
    assert out["cards"][0]["prose_source"]["why"] == "MODEL"
    assert scan._fault("Like Copenhagen, it has cafés.", lisbon, inp_of(lisbon)) == "names another city"


def test_a_lead_naming_a_place_with_a_city_in_it_stays():
    lisbon = {**city("lisbon"), "places": [{"qloo_id": "L1", "name": "Copenhagen Coffee Lab & Bakery", "genres": ["Cafe"]}]}
    out = scan.explain({"lead": "Lisbon leads with Copenhagen Coffee Lab & Bakery.", "cities": []}, inp_of(lisbon))
    assert out["lead"]["source"] == "MODEL"


def test_tuning_counterexamples_repeated_city_and_escaped_text():
    """Prompt tuning v5 on L35-A: each city came back twice (the second without refs); a lead with literal \\u escapes."""
    out = scan.explain({"lead": "Paris leads with H\\u00f4tel A.", "cities": [reply("paris"), reply("paris", refs=[])]},
                       inp_of(city("paris")))
    assert out["cards"][0]["prose_source"]["why"] == "MODEL" and out["lead"]["source"] == "DETERMINISTIC"


@pytest.mark.parametrize("text", ["This city has fifteen food and drink places, more than any other city.",
                                  "Unlike the other cities, it has a park.", "It has more cafés than the others."])
def test_l35a_v2_counterexample_comparisons_with_other_cities(text):
    """LIVE L35-A v2 (market_strategist@5): Helsinki's `different` said "more than any other city"."""
    assert markets.guard(text)
    assert markets.guard("More than half the places here are cafés.") is None


@pytest.mark.parametrize("text, ok", [
    ("Qloo lists 14 Aesop stores here.", True), ("Qloo lists 15 Aesop stores here.", False),
    ("Qloo lists fourteen Aesop stores here.", True), ("Qloo lists fifteen Aesop stores here.", False),
    ("Aesop's 14 stores are within 15 miles.", True), ("Aesop has 14 stores within 20 miles.", False),
    ("Qloo lists 17 Aesop stores here.", False),  # 17 is a place-mix count, not Aesop's
    ("Seventeen of the places here are cafés.", True),
    ("Qloo lists 50 Muji stores here.", False), ("Qloo lists at least 50 Muji stores here.", True),
])
def test_review_finding_5_counts_are_bound_to_what_they_claim(text, ok):
    """Review slice 3.5, finding 5: counts bound to the named brand, spelled-out counts, radius, capped counts."""
    paris = {**city("paris", comps=[AESOP, {"name": "Muji", "stores": 50, "capped": True, "state": "ok"}]),
             "channels": {"food_drink": 17, "retail": 5}}
    assert (scan.count_fault(text, [paris]) is None) is ok


def test_review_finding_5_an_invented_count_in_the_lead_is_replaced():
    paris = city("paris", comps=[AESOP])
    bad = scan.explain({"lead": "Paris has 99 Aesop stores.", "cities": []}, inp_of(paris))
    good = scan.explain({"lead": "Paris has 14 Aesop stores within 15 miles.", "cities": []}, inp_of(paris))
    assert bad["lead"]["source"] == "DETERMINISTIC" and good["lead"]["source"] == "MODEL"


# ---- review slice 3.5 (implementation review, 25 Oct 2026): frozen reproductions ----
@pytest.mark.parametrize("pages", [
    {1: (200, [dest("paris", None), dest("berlin", None)])},
    {1: (200, [dest("paris", 0.9), dest("berlin", None), dest("oslo", 0.8)])},
    {1: (200, [dest("paris", "high"), dest("berlin", 0.8)])},
])
def test_review_finding_1_unreadable_affinities_never_become_positions(monkeypatch, pages):
    r, _ = rank(monkeypatch, pages, ["paris", "berlin", "oslo"])
    assert r["state"] == "failed" and r["positions"] == {}


def test_review_finding_1_a_flat_first_page_stays_flat_when_page_two_fails(monkeypatch):
    ids = [c for c in markets.ORDER if c not in ("wellington", "lima")][:70]
    r, _ = rank(monkeypatch, {1: (200, [dest(c, 0.5) for c in ids[:50]]), 2: (503, [])}, ids)
    assert r["state"] == "flat" and r["positions"] == {}
    good, _ = rank(monkeypatch, {1: (200, [dest(c, 0.9 - i / 100) for i, c in enumerate(ids[:50])]), 2: (503, [])}, ids)
    assert good["state"] == "ranked_partial" and len(good["positions"]) == 50  # a genuine partial ranking stays


class SG:
    """SYNTHETIC qloo.get for store searches: page → (status, body text)."""

    def __init__(self, pages):
        self.pages = pages

    def __call__(self, path, params, *a, **k):
        status, text = self.pages.get(int(params.get("page", 1)), (200, json.dumps({"results": []})))
        return {"status": status, "body": text}


def store_rows(n, brand="Tidewell"):
    return json.dumps({"results": [{"entity_id": f"S{i}", "name": f"{brand} Toronto", "location": {"lat": 43.7, "lon": -79.4},
                                    "tags": [{"tag_id": f"{G}:clothing_store"}]} for i in range(n)]})


@pytest.mark.parametrize("pages, status", [
    ({1: (200, "<html>error</html>")}, "failed"), ({1: (200, json.dumps({"results": {"oops": 1}}))}, "failed"),
    ({1: (200, store_rows(50)), 2: (200, "<html>error</html>")}, "partial"), ({1: (200, json.dumps({"results": []}))}, "complete"),
])
def test_review_finding_2_an_unreadable_store_search_is_never_complete(monkeypatch, pages, status):
    monkeypatch.setattr(qloo, "get", SG(pages))
    r = ranking._store_search(SimpleNamespace(id="r", cassette=None), "prj", "Tidewell")
    assert r["status"] == status
    if status != "partial":  # a located or comparable search reads page 1 only
        near = ranking.near_search(SimpleNamespace(id="r", cassette=None), "prj", "comparables", "Tidewell", "toronto")
        assert (near["state"], near["stores"]) == (("ok", 0) if status == "complete" else ("failed", None))


def test_review_finding_3_a_founder_restore_overrides_a_located_store(client, markets_env):
    us = [c for c in markets.ORDER if markets.CITIES[c]["country"] == "US"]
    markets_env.qloo.stores["Tidewell"] = (us * 20)[:200]
    markets_env.qloo.located = {"Tidewell": ["toronto"]}
    pid, m = scanned35(client, markets_env)
    assert "toronto" not in [c["city_id"] for c in m["assessment"]["cards"]]
    pres = client.get(f"/api/projects/{pid}/presence").json()
    client.put(f"/api/projects/{pid}/presence", json={"base_version": pres["version"], "remove_cities": ["toronto"]})
    places = len(markets_env.qloo.places())
    client.post(f"/api/projects/{pid}/shortlist/update")
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert [c["city_id"] for c in m["assessment"]["cards"]][0] == "toronto" and m["ranking"]["current"]
    assert not [n for n in m["notices"] if n["action"] == "update"] and len(markets_env.qloo.places()) == places
    toronto = next(c for c in m["cities"] if c["city_id"] == "toronto")
    assert toronto["excluded"] is None and toronto["proposed"]


def test_review_finding_4_a_removal_during_an_audience_refresh_is_kept(client, markets_env, monkeypatch):
    pid = matched(client, markets_env)
    ap = db.latest("audience_profile", pid)
    payload = {k: v for k, v in ap.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")}
    db.put_record("audience_profile", pid, {**payload, "status": {**ap["status"], "demographics": "failed"}}, rid=ap["id"])
    real = qloo.get

    def remove_while_waiting(path, params, *a, **k):
        if params.get("filter.type") == "urn:demographics":
            ranking.remove_audiences(pid, db.latest("audience_profile", pid)["version"], ["SYN-AUD-1"])
        return real(path, params, *a, **k)
    monkeypatch.setattr(qloo, "get", remove_while_waiting)
    assert client.post(f"/api/projects/{pid}/audience/retry").status_code == 200
    idle(pid)
    final = db.latest("audience_profile", pid)
    assert final["status"]["demographics"] == "ok" and final["removed"] == ["SYN-AUD-1"]


def test_review_finding_4_a_goal_change_between_steps_fences_the_result(client, markets_env, monkeypatch):
    pid, m = scanned35(client, markets_env)
    real = scan._decide

    def edit_then_decide(run, gen, ref):  # SYNTHETIC: the founder changes scope and home after the ranking step
        fi = db.latest("founder_input", pid)
        payload = {k: v for k, v in fi.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")}
        db.put_record("founder_input", pid, {**payload, "scope": "domestic", "home_country": "PT"}, rid=fi["id"])
        return real(run, gen, ref)
    monkeypatch.setattr(scan, "_decide", edit_then_decide)
    sl = db.latest("shortlist", pid)["version"]
    client.post(f"/api/projects/{pid}/shortlist/update")
    idle(pid)
    late = db.latest("assessment_set", pid)
    assert late["fenced"]["changed"] == ["founder_input"] and late["inputs"] == []
    assert db.latest("shortlist", pid)["version"] == sl  # not made current; no record claims the domestic version
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert m["assessment"]["version"] < late["version"] and "Your change arrived" in json.dumps(m["notices"])


def test_review_finding_6_a_kind_other_cards_share_is_not_distinctive():
    """LIVE L35-B v2 (market_strategist@5): Auckland "a bagel shop" (Brisbane has one), Perth "a park" (Sydney has one)."""
    def c(cid, *places):
        return {**city(cid, places=0), "places": [{"qloo_id": f"{cid}-{i}", "name": n, "genres": g} for i, (n, g) in enumerate(places)]}
    brisbane = c("brisbane", ("Art Shed Brisbane", ["Craft store", "Shopping"]), ("The Bagel Boys", ["Bagel shop", "Restaurant"]))
    sydney = c("sydney", ("Croydon Park", ["Park", "Tourist attraction"]))
    auckland = c("auckland", ("Goodness Gracious Cafe", ["Restaurant", "Cafe", "Bagel shop"]))
    perth = c("perth", ("Shelley Beach Park", ["Park"]))
    inp = inp_of(brisbane, sydney, auckland, perth)
    assert scan.not_distinct("Qloo returned a bagel shop here.", auckland, inp) == "not distinctive: Brisbane also has a bagel shop"
    assert scan.not_distinct("Qloo returned a park here.", perth, inp) == "not distinctive: Sydney also has a park"
    assert scan.not_distinct("Qloo returned Shelley Beach Park here.", perth, inp) is None  # its own named place
    assert scan.not_distinct("Art Shed Brisbane is a craft store, and Qloo returned a bagel shop here.", brisbane, inp) is None
    out = scan.explain({"lead": "", "cities": [reply("auckland", different="Qloo returned a bagel shop here.", refs=["auckland-0"])]}, inp)
    assert out["cards"][2]["prose_source"]["different"] == "DETERMINISTIC"
