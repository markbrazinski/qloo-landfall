"""Slice 3: the 80-city scan and the explained shortlist (BUILD-PLAN slice 3 tests; CONTRACTS B4/B5).

Unit tests are deterministic and use SYNTHETIC inputs unless they read a capture (`captured`, skipped in a fresh clone).
Integration tests replay the synthetic Tidewell brand read; Qloo answers are SYNTHETIC at the HTTP boundary
(httpx in landfall.qloo) and the Market strategist's output is SYNTHETIC (scan.call_strategist replaced).
"""
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import SYNTHETIC, wait_for, wait_for_identity, pause

from landfall import config, db, ledger, markets, qloo, ranking, runner, scan
from landfall.cassette import Cassette

PRIVATE = config.ROOT / "live-captures-private"
CAPTURES = [PRIVATE / "probes" / "qloo" / "exchanges.jsonl", PRIVATE / "probes" / "slice3" / "exchanges.jsonl"]


def captured_places():
    if not all(f.exists() for f in CAPTURES):
        pytest.skip("captured probes not present (private)")
    for f in CAPTURES:
        for line in filter(None, f.read_text().split("\n")):
            ex = json.loads(line)
            if ex["op"] == "/v2/insights":
                yield ex["request"]["params"], json.loads(ex["response"]["body"])["results"]["entities"]


def place(genres, i=1, lat=None, lon=None, dist=1000):
    """A SYNTHETIC QlooPlace with the given genre ids."""
    return {"qloo_id": f"P{i}", "name": f"Place {i}", "rank_in_request": i, "lat": lat, "lon": lon, "distance_m": dist,
            "genres": [{"qloo_id": g, "name": g.rsplit(":", 1)[-1]} for g in genres]}


G = "urn:tag:genre:place"


# ---- catalog and reference data ----
def test_catalog_has_80_unique_cities_with_valid_countries_and_coordinates():
    assert len(markets.ORDER) == 80 == len(set(markets.ORDER))
    for c in markets.CITIES.values():
        assert c["country"] in markets.COUNTRY_NAME and -90 <= c["lat"] <= 90 and -180 <= c["lon"] <= 180
    assert markets.RADIUS_M == 15000 and len(markets.EU) == 27
    assert (markets.CITIES["toronto"]["country"], markets.CITIES["lisbon"]["country"]) == ("CA", "PT")


# ---- channels@1, distinct places, evidence strength (P3-04) ----
def test_every_captured_genre_id_is_mapped():
    ids = {t.get("id") or t.get("tag_id") for _, ents in captured_places() for e in ents for t in e.get("tags") or []}
    genres = {i for i in ids if i and i.startswith(G + ":")}
    assert len(genres) > 200
    assert [g for g in genres if markets.genre_class(g) is None] == []


def test_a_place_with_overlapping_genres_counts_once_by_precedence():
    p = place([G, f"{G}:restaurant", f"{G}:hotel", f"{G}:tourist_attraction"])
    assert markets.place_class(p) == "food_drink"
    d = markets.digest({"city_id": "toronto", "places": [p]})
    assert sum(d["channels"].values()) == 1 and d["channels"]["food_drink"] == 1
    assert markets.place_class(place([f"{G}:restaurant:cafe", f"{G}:shopping"])) == "retail"


def test_captured_overlap_and_root_only_places():
    ents = [e for params, es in captured_places() for e in es]
    parsed = [markets.parse_place(e, 1) for e in ents]
    drake = next(p for p in parsed if p["name"] == "The Drake Hotel")  # Komono/Toronto: bar, concert hall, hotel,
    d = markets.digest({"city_id": "toronto", "places": [drake]})     # restaurant and tourist attraction
    assert sum(d["channels"].values()) == 1 and d["channels"]["venues_culture"] == 1  # once, by precedence
    root_only = [p for p in parsed if not p["genres"]]
    assert root_only and all(markets.place_class(p) == "unknown_category" for p in root_only)  # Q3 Lagos-style


def test_root_only_place_is_unknown_category_not_other():
    assert markets.place_class(place([G])) == "unknown_category"
    assert markets.place_class(place([])) == "unknown_category"


@pytest.mark.parametrize("n, non_attraction, unknown, strength", [
    (9, 9, 0, "THIN_SEARCH_RESULT"), (10, 2, 0, "LIMITED"), (10, 3, 0, "MODERATE"),
    (10, 2, 8, "LIMITED"),  # unknown categories never strengthen evidence
])
def test_evidence_strength_boundaries(n, non_attraction, unknown, strength):
    ps = ([place([f"{G}:shopping"], i) for i in range(non_attraction)]
          + [place([G], 100 + i) for i in range(unknown)])
    ps += [place([f"{G}:tourist_attraction"], 200 + i) for i in range(n - len(ps))]
    assert markets.digest({"city_id": "x", "places": ps})["evidence_strength"] == strength


# ---- home (P3-04) ----
@pytest.mark.parametrize("text, country, city, note", [
    ("Belgium", "BE", None, "country_only"), ("Germany", "DE", None, "country_only"),
    ("Portland, Oregon, USA", "US", "Portland", "placed"), ("Portland, USA", "US", None, "ambiguous"),
    ("somewhere by the sea", None, None, "unknown"), ("", None, None, "unknown"),
    ("Antwerp, Belgium", "BE", "Antwerp", "placed"),
])
def test_home_parsing(text, country, city, note):
    h = markets.parse_home(text)
    assert (h["country"], h["city"], h["note"]) == (country, city, note)
    if note != "placed":
        assert h["lat"] is None and h["lon"] is None


def test_home_keeps_its_provenance_after_a_founder_correction():
    pid = "prj_home"
    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", pid, "s", "https://x.example", "live", None, None, db.now())
    inferred = db.put_record("claim", pid, {"subject": {"key": "home_market"}, "label": "Home market",
                                            "value": "Belgium", "state": "INFERRED"})
    said = db.put_record("claim", pid, {"subject": {"key": "home_market"}, "label": "Home market",
                                        "value": "Lisbon, Portugal", "state": "STATED", "replaces": inferred["id"]})
    b1 = {"facts": [], "interpretation": [inferred["id"]]}
    b2 = {"facts": [], "interpretation": [said["id"]]}
    assert scan.home_of(b1)["provenance"] == "INFERRED" and scan.home_of(b1)["claim_id"] == inferred["id"]
    h = scan.home_of(b2)
    assert (h["provenance"], h["country"], h["city"]) == ("YOU_SAID", "PT", "Lisbon")


# ---- practical fit and chips ----
none = lambda iso: None  # noqa: E731


def test_fit_same_country_eu_and_import():
    be = markets.parse_home("Belgium")
    assert markets.practical_fit(markets.CITIES["brussels"], be, none)["chip"] == "SAME_COUNTRY"
    paris = markets.practical_fit(markets.CITIES["paris"], be, none)
    assert (paris["import_needed"], paris["chip"]) == (False, "NO_IMPORT_STEP_EU")
    london = markets.practical_fit(markets.CITIES["london"], be, none)
    assert (london["import_needed"], london["chip"]) == (True, "IMPORT_NEEDED")
    us = markets.parse_home("Portland, Oregon, USA")
    sea = markets.practical_fit(markets.CITIES["seattle"], us, none)
    assert sea["chip"] == "SAME_COUNTRY" and 200 < sea["distance_km"] < 260


def test_unknown_home_leaves_every_fit_field_null():
    f = markets.practical_fit(markets.CITIES["paris"], markets.parse_home("somewhere"), none)
    assert f == {"distance_km": None, "same_country": None, "import_needed": None, "inside_ship_range": None,
                 "chip": "FIT_NOT_KNOWN"}


def test_chip_precedence_inside_range_over_eu():
    f = markets.practical_fit(markets.CITIES["paris"], markets.parse_home("Germany"), lambda iso: True)
    assert f["chip"] == "INSIDE_YOUR_RANGE"
    f = markets.practical_fit(markets.CITIES["london"], markets.parse_home("Germany"), lambda iso: True)
    assert f["chip"] == "IMPORT_NEEDED"  # import precedes the shipping range


# ---- shipping range ----
def ship(value, state="SUPPORTED"):
    return markets.shipping_rule([{"state": state, "subject": {"key": "shipping"}, "value": value}])


def test_shipping_rules():
    r = ship("Ships to France")
    assert (r("FR"), r("DE")) == (True, None)  # positive: Germany stays unknown
    r = ship("We do not ship to Germany")
    assert (r("DE"), r("FR")) == (False, None)  # negative: never makes Germany inside
    r = ship("Ships within Germany only")
    assert (r("DE"), r("FR")) == (True, False)  # exhaustive
    r = ship("Ships worldwide")
    assert (r("JP"), r("BR")) == (True, True)
    r = ship("Ships worldwide except Russia")
    assert (r("RU"), r("JP")) == (False, True)
    assert ship("Ships within the US only", state="TO_CHECK")("US") is None  # a TO_CHECK fact is ignored
    assert ship("Free delivery to us in 2 days")("US") is None  # the pronoun is not a country


# ---- request builder ----
def test_request_builder_sends_only_effective_signals():
    brand = {"signals": [{"kind": "entity", "qloo_id": "E1", "name": "Komono", "origin": "QLOO", "included": True}]}
    p = markets.scan_params(markets.CITIES["toronto"], markets.effective_signals(brand))
    assert p == {"filter.type": "urn:entity:place", "filter.location": "POINT(-79.4 43.7)",
                 "filter.location.radius": 15000, "take": 25, "signal.interests.entities": "E1"}
    prof = {"signals": [
        {"kind": "tag", "qloo_id": "urn:tag:keyword:qloo:sustainability", "name": "Sustainability", "origin": "QLOO_CATEGORY", "included": True},
        {"kind": "tag", "qloo_id": "urn:tag:keyword:qloo:ocean", "name": "Ocean", "origin": "YOU_SAID", "included": True},
        {"kind": "entity", "qloo_id": "C1", "name": "Ticked", "origin": "COMPARABLE", "included": True},
        {"kind": "entity", "qloo_id": "C2", "name": "Unticked", "origin": "COMPARABLE", "included": False}]}
    p = markets.scan_params(markets.CITIES["toronto"], markets.effective_signals(prof))
    assert p["signal.interests.tags"] == "urn:tag:keyword:qloo:sustainability,urn:tag:keyword:qloo:ocean"
    assert p["signal.interests.entities"] == "C1"  # the unticked comparable is never sent


# ---- response classification (empty, failed and unresolved are SYNTHETIC; never observed live) ----
def body(entities, success=True):
    return json.dumps({"success": success, "results": {"entities": entities}})


def ent(i, lat=43.7, lon=-79.4, dist=1000):
    return {"entity_id": f"E{i}", "name": f"P{i}", "location": {"lat": lat, "lon": lon}, "query": {"distance": dist},
            "tags": [{"id": f"{G}:shopping", "name": "Shopping", "type": G}]}


TOR = markets.CITIES["toronto"]


def test_classification():
    c = lambda out: markets.classify(out, TOR)  # noqa: E731
    assert c({"status": 200, "body": body([ent(1), ent(2)])})["status"] == "returned"
    assert c({"status": 200, "body": body([])})["status"] == "empty"  # a genuine successful empty answer
    for bad in ["not json", json.dumps({"results": {"entities": []}}), body([], success=False),
                json.dumps({"success": True, "results": {}}), body([{"name": "no id"}])]:
        r = c({"status": 200, "body": bad})
        assert (r["status"], r["error"]["reason"]) == ("failed", "unreadable response")  # never "empty"
    assert c({"status": 503, "body": "", "attempts": 3})["status"] == "failed"
    assert c({"status": 0, "error": "operating allowance reached"})["status"] == "failed"
    loc = c({"status": 400, "body": json.dumps({"error": {"message": "Invalid filter.location: POINT out of range"}})})
    assert loc["status"] == "unresolved"
    echo = c({"status": 400, "body": json.dumps({"message": "Invalid take", "params": {"filter.location": "POINT(1 2)"}})})
    assert echo["status"] == "failed"  # echoing params is not a location error
    far = c({"status": 200, "body": body([ent(i, lat=44.1, lon=-79.4) for i in range(5)] + [ent(9)])})  # ~45 km out
    assert (far["status"], far["error"]["reason"]) == ("unresolved", "results are not in this city")
    half = c({"status": 200, "body": body([ent(1, lat=44.1), ent(2)])})  # exactly half out: not "more than half"
    assert half["status"] == "returned"


def test_affinity_is_kept_on_the_place_but_never_in_the_digest_or_prompt():
    e = {**ent(1), "query": {"affinity": 0.97, "distance": 900}}
    p = markets.parse_place(e, 1)
    assert p["affinity_in_request"] == 0.97
    d = markets.digest({"city_id": "toronto", "places": [p]})
    assert "0.97" not in json.dumps(d)


# ---- attempt log and accounting (P3-03; SYNTHETIC provider boundary) ----
class Seq:
    """SYNTHETIC Qloo: answers from a list of statuses, the last one repeating."""

    def __init__(self, statuses):
        self.statuses, self.calls = list(statuses), 0

    def __call__(self, url, params=None, **_):
        self.calls += 1
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return SimpleNamespace(status_code=status, headers={"content-type": "application/json"},
                               text=body([ent(1)]) if status == 200 else '{"error":"busy"}')


def _project(pid="prj_acc"):
    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", pid, "s", "https://x.example", "live", None, None, db.now())
    return pid


def ledger_attempts():
    return sum(1 for x in config.LEDGER_PATH.read_text().split("\n") if '"attempt"' in x) if config.LEDGER_PATH.exists() else 0


def test_attempt_log_cache_and_replay(monkeypatch, tmp_path):
    pid = _project()
    fake = Seq([503, 503, 200])
    monkeypatch.setattr(qloo.httpx, "get", fake)
    rec = Cassette("record", tmp_path / "rec")
    out = qloo.get("/v2/insights", {"take": 25}, rec, "scan", pid)
    assert out["status"] == 200 and out["attempts"] == 3 and fake.calls == 3 == ledger_attempts()
    assert [a["status"] for a in out["attempt_log"]] == [503, 503, 200]
    assert out["attempt_log"][0]["body"] == '{"error":"busy"}' and "body" not in out["attempt_log"][2]
    saved = json.loads((tmp_path / "rec" / "exchanges.jsonl").read_text())["response"]
    assert [a["status"] for a in saved["attempt_log"]] == [503, 503, 200]  # all three outcomes inspectable
    usage = db.q("SELECT origin, attempt, status FROM usage WHERE project_id=?", pid)
    assert [(u["origin"], u["attempt"], u["status"]) for u in usage] == [("live", 1, 503), ("live", 2, 503), ("live", 3, 200)]

    again = qloo.get("/v2/insights", {"take": 25}, Cassette("live"), "scan", pid)  # the same request: 7-day cache
    assert again["attempts"] == 0 and again["attempt_log"] == [] and again["cache"]["fetched_at"]
    assert fake.calls == 3 == ledger_attempts() and qloo.attempts_of(again) == 0

    replayed = qloo.get("/v2/insights", {"take": 25}, Cassette("replay", replay_dirs=[tmp_path / "rec"]), "scan", pid)
    assert replayed["origin"] == "replay" and fake.calls == 3 == ledger_attempts()
    rows = [(u["origin"], u["attempt"]) for u in db.q("SELECT origin, attempt FROM usage WHERE project_id=?", pid)]
    assert rows[3:] == [("cache", None), ("replay", None)]
    from landfall.app import project_view
    u = project_view(db.q("SELECT * FROM projects WHERE id=?", pid)[0])["usage"]
    assert (u["qloo_sent"], u["qloo_lookups"], u["qloo_cached"]) == (3, 3, 1)  # sent = the ledger delta


def test_a_legacy_exchange_without_a_log_reads_as_one_attempt():
    assert qloo.attempts_of({"status": 200, "body": "{}"}) == 1
    assert qloo.attempts_of({"status": 503, "attempts": 3}) == 3
    assert qloo.attempts_of({"status": 200, "attempts": 3, "cache": {"fetched_at": "x"}}) == 0


def test_accepted_cassettes_stay_readable():
    dirs = [PRIVATE / "cases" / c for c in ("L2-A-komono/v1", "L2-B-gotbag/v1")]
    if not all(d.exists() for d in dirs):
        pytest.skip("accepted cases not present (private)")
    cas = Cassette("replay", replay_dirs=dirs)
    q = [ex for ex in cas._index.values() if ex["provider"] == "qloo"]
    assert q and all(qloo.attempts_of(ex["response"]) >= 1 for ex in q)


def test_allowance_denied_before_a_retry_keeps_the_attempts_made(monkeypatch):
    fake = Seq([503])
    monkeypatch.setattr(qloo.httpx, "get", fake)
    real = ledger.reserve
    n = [0]

    def reserve(*a, **k):
        n[0] += 1
        if n[0] > 1:
            raise ledger.BudgetExhausted("qloo cap reached")
        return real(*a, **k)
    monkeypatch.setattr(ledger, "reserve", reserve)
    out = qloo._live("/v2/insights", {})
    assert out["attempts"] == 1 and "retry not sent" in out["error"] and fake.calls == 1


def test_pacer_holds_four_and_a_half_requests_a_second(monkeypatch):
    monkeypatch.setattr(qloo, "PACE_S", 1 / 4.5)
    starts = []
    monkeypatch.setattr(qloo.httpx, "get", lambda *a, **k: starts.append(time.monotonic()) or
                        SimpleNamespace(status_code=200, headers={}, text=body([])))
    for _ in range(4):
        qloo._attempt("/v2/insights", {})
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(g >= 0.21 for g in gaps), gaps


# ---- prose guard, validator and assembly (P3-05) ----
@pytest.mark.parametrize("text", [
    "Toronto returned more places than Lisbon.", "Toronto is the strongest market because Qloo returned the most related places.",
    "There is strong demand for bags here.", "Lisbon ranks higher than Porto.", "Berlin is the top city for us.",
    "Qloo's affinity is high here.", "A proven market for watches.", "These shops will sell your range.",
    "Its market size suits a launch.", "A popular café scene.",
])
def test_prose_guard_rejects_comparisons_and_demand(text):
    assert markets.guard(text)


@pytest.mark.parametrize("text", [
    "Qloo returned surf shops and climbing gyms in Toronto.", "10 of the 25 places here are shops.",
    "More than half the places here are cafés.", "The only proposed city with an outdoor shop near a climbing gym.",
])
def test_prose_guard_permits_within_city_descriptions(text):
    assert markets.guard(text) is None


# ---- integration: SYNTHETIC Qloo places, synthetic Tidewell upstream ----
POINTS = {f"POINT({c['lon']} {c['lat']})": cid for cid, c in markets.CITIES.items()}
KINDS = [f"{G}:shopping", f"{G}:restaurant:cafe", f"{G}:tourist_attraction", f"{G}:gym", G]


class FakeQloo:
    """SYNTHETIC Qloo at the HTTP boundary: a brand search with one exact match; 25 places per city; one destination
    ranking (catalog order unless `rank_order`; `rank_fail` fails pages, `rank_flat`, `rank_empty`); own or related
    stores from `stores` ({brand: [city_id, …]}); audiences, demographics and stand-in brands."""

    def __init__(self):
        self.calls, self.fail = [], {}
        self.rank_order, self.rank_fail, self.rank_flat, self.rank_empty = None, set(), False, False
        self.stores: dict[str, list[str]] = {}
        self.brand = {"entity_id": "SYN-BRAND", "name": "Tidewell", "types": ["urn:entity:brand"], "properties": {},
                      "tags": []}
        self.standins = [("SYN-SI-1", "Vissla", "A surf brand."), ("SYN-SI-2", "Surfrider Foundation", "A nonprofit."),
                         ("SYN-SI-3", "Outerknown", "Apparel."), ("SYN-SI-4", "Twothirds", "Clothing."),
                         ("SYN-SI-5", "OluKai", "Footwear.")]
        self.demographics = None
        self.audience_fail = False
        self.place_kinds: dict[str, list[str]] = {}  # city → genre ids of its 25 places (default: KINDS)
        self.store_fail: set[int] = set()  # own-store search pages that fail
        self.located: dict[str, list[str]] = {}  # stores only a located search finds (beyond the 4 pages)

    def __call__(self, url, params=None, **_):
        params = dict(params or {})
        op = url.rsplit("/", 1)[-1]
        self.calls.append((op, params))
        kind = params.get("filter.type")
        if op == "search" and params.get("types") == "urn:entity:place":
            return self._ok({"results": self._stores(params)})
        if op == "search":
            if self.brand and params["query"] == self.brand["name"]:
                return self._ok({"results": [self.brand]})
            return self._ok({"results": [{"entity_id": i, "name": n, "types": ["urn:entity:brand"], "properties":
                                          {"short_description": d}} for i, n, d in self.standins
                                         if n.casefold().startswith(params["query"].casefold())]})
        if op == "audiences":
            return self._ok({"results": {"audiences": [{"id": "urn:audience:syn:watches", "entity_id": "SYN-AUD-1",
                                                        "name": params["filter.query"]}]}})
        if kind == "urn:entity:destination":
            return self._rank(params)
        if kind in ("urn:audience", "urn:demographics") and self.audience_fail:
            return SimpleNamespace(status_code=500, headers={}, text='{"error":"SYNTHETIC outage"}')
        if kind == "urn:audience":
            return self._ok({"success": True, "results": {"entities": [
                {"name": n, "entity_id": f"SYN-AUD-{i}", "disambiguation": f"{n} (Hobbies And Interests)",
                 "query": {"affinity": 0.9 - i / 100}} for i, n in enumerate(["Watches", "Surfing", "Hiking"], 1)]}})
        if kind == "urn:demographics":
            ids = params["signal.interests.entities"].split(",")
            return self._ok({"success": True, "results": {"demographics": self.demographics or [
                {"entity_id": e, "query": {"age": {"25_to_29": 0.3, "45_to_54": -0.2}, "gender": {"male": 0.2, "female": -0.2}}}
                for e in ids]}})
        if kind == "urn:entity:brand":
            return self._ok({"success": True, "results": {"entities": [
                {"entity_id": i, "name": n, "subtype": "urn:entity:brand", "properties": {"short_description": d}}
                for i, n, d in self.standins]}})
        cid = POINTS[params["filter.location"]]
        if cid in self.fail:
            return SimpleNamespace(status_code=self.fail[cid], headers={}, text='{"error":"SYNTHETIC outage"}')
        c = markets.CITIES[cid]
        kinds = self.place_kinds.get(cid, KINDS)
        return self._ok({"success": True, "results": {"entities": [
            {"entity_id": f"SYN-{cid}-{i}", "name": f"{c['name']} place {i}", "location": {"lat": c["lat"], "lon": c["lon"]},
             "query": {"distance": 800, "affinity": 0.5}, "tags": [{"id": kinds[i % len(kinds)], "type": G,
                                                                    "name": kinds[i % len(kinds)].rsplit(":", 1)[-1]}]}
            for i in range(25)]}})

    def _rank(self, params):
        page = int(params.get("page", 1))
        if page in self.rank_fail:
            return SimpleNamespace(status_code=500, headers={}, text='{"error":"SYNTHETIC outage"}')
        if self.rank_empty:
            return self._ok({"success": True, "results": {"entities": []}})
        order = self.rank_order or markets.ORDER
        ids = sorted(params["filter.results.entities"].split(","),
                     key=lambda d: order.index(ranking.CITY_OF_DEST[d]) if ranking.CITY_OF_DEST[d] in order else 999)
        rows = ids[(page - 1) * 50:page * 50]
        return self._ok({"success": True, "results": {"entities": [
            {"entity_id": d, "name": markets.CITIES[ranking.CITY_OF_DEST[d]]["name"],
             "query": {"affinity": 0.5 if self.rank_flat else 0.99 - ((page - 1) * 50 + i) / 1000}} for i, d in enumerate(rows)]}})

    def _stores(self, params):
        """Own and related-brand stores, named "<brand> <city>", in a retail genre, at the city point."""
        q = params["query"]
        row = lambda i, c: {"entity_id": f"SYN-STORE-{q}-{c}-{i}", "name": f"{q} {markets.CITIES[c]['name']}",  # noqa: E731
                            "location": {"lat": markets.CITIES[c]["lat"], "lon": markets.CITIES[c]["lon"]},
                            "tags": [{"tag_id": f"{G}:clothing_store", "type": G}]}
        rows = [row(i, c) for i, c in enumerate(self.stores.get(q, []))]
        if "filter.location" in params:
            lat, lon = map(float, params["filter.location"].split(","))
            rows += [row(900 + i, c) for i, c in enumerate(self.located.get(q, []))]
            return [r for r in rows if markets.km(lat, lon, r["location"]["lat"], r["location"]["lon"]) < 25]
        page = int(params.get("page", 1))
        if page in self.store_fail:
            return SimpleNamespace(status_code=500, headers={}, text='{"error":"SYNTHETIC outage"}')
        return rows[(page - 1) * 50:page * 50]

    def _ok(self, b):
        return SimpleNamespace(status_code=200, headers={"content-type": "application/json"}, text=json.dumps(b))

    def located_calls(self):
        return [p for p in self.calls if p[0] == "search" and "filter.location" in p[1]]

    def places(self):
        return [p for p in self.calls if p[0] == "insights" and p[1].get("filter.type") == "urn:entity:place"]

    def of(self, kind):
        return [p for p in self.calls if p[1].get("filter.type") == kind or p[1].get("types") == kind]


class Strategist:
    """SYNTHETIC Market strategist @2 output: prose for each decided city, citing its own first place."""

    def __init__(self):
        self.calls, self.fail, self.lead, self.mutate = 0, False, None, None

    def __call__(self, run, inp):
        self.calls += 1
        if self.fail:
            raise RuntimeError("SYNTHETIC strategist failure")
        cs = inp["cities"]
        out = {"lead": self.lead or f"{cs[0]['name']} comes first: Qloo returned shops and cafés there.",
               "cities": [{"city_id": c["city_id"], "why": f"Qloo returned shops and cafés for the brand in {c['name']}.",
                           "different": f"Shops beside cafés in {c['name']}.", "could_change": "Whether a shop answers.",
                           "ways_in": "Retail", "refs": [c["places"][0]["qloo_id"]]} for c in cs if c["places"]]}
        return self.mutate(out) if self.mutate else out


LIVE = ("identity", "scan", "presence", "standins", "rank", "audience", "comparables", "standins_edit")


@pytest.fixture
def markets_env(monkeypatch, tmp_path):
    fake, strat = FakeQloo(), Strategist()
    monkeypatch.setattr(qloo.httpx, "get", fake)
    monkeypatch.setattr(scan, "call_strategist", strat)
    monkeypatch.setattr(config, "REPLAY_CASES", [SYNTHETIC])
    monkeypatch.setattr(config, "LIVE_STAGES", LIVE)
    monkeypatch.setattr(config, "PRIVATE", tmp_path / "private")
    return SimpleNamespace(qloo=fake, strategist=strat)


def idle(pid, timeout=60):
    end = time.time() + timeout
    while db.q("SELECT 1 FROM runs WHERE project_id=? AND status IN ('queued','running')", pid):
        assert time.time() < end, "runs did not finish"
        pause(0.05)


def matched_project(client):
    pid = client.post("/api/projects", json={"url": "http://tidewell.example/"}).json()["project_id"]
    wait_for(client, pid)
    v = wait_for_identity(client, pid)
    assert v["profile"]["confirmed"] and v["markets"]["stage"] == "ready"
    return pid


def scanned(client, env):
    pid = matched_project(client)
    r = client.post(f"/api/projects/{pid}/scan").json()
    assert r["run_id"]
    idle(pid)
    return pid, client.get(f"/api/projects/{pid}/markets").json()


def bedrock_rows(pid):
    return db.q("SELECT COUNT(*) n FROM usage WHERE project_id=? AND provider='bedrock'", pid)[0]["n"]


RECOMMENDED = ["toronto", "vancouver", "montreal", "mexico-city", "calgary", "london"]


def test_scan_hands_off_once_and_coverage_partitions_80(client, markets_env):
    pid, m = scanned(client, markets_env)
    cov = m["coverage"]
    assert cov["returned"] == 80 and sum(cov[s] for s in ("returned", "empty", "failed", "unresolved")) == 80
    assert cov["attempted"] == 80 and len(markets_env.qloo.places()) == 80
    assert markets_env.strategist.calls == 1 and m["generation"]["status"] == "complete"
    # home Portland, Oregon: an international goal leaves the US out; Qloo's (synthetic) order is the catalog's
    assert [c["city_id"] for c in m["assessment"]["cards"]] == RECOMMENDED
    assert m["shortlist"]["version"] == 1 and m["shortlist"]["included"] == 6
    assert m["assessment"]["lead"]["source"] == "MODEL" and m["ranking"]["state"] == "ranked"
    # a completed generation is reused, never rerun
    assert client.post(f"/api/projects/{pid}/scan").json() == {"generation_id": m["generation"]["id"], "run_id": None}
    assert len(markets_env.qloo.places()) == 80
    # the request signal is the confirmed brand, and affinity never reaches the view
    assert m["generation"]["signals"] == [{"kind": "entity", "qloo_id": "SYN-BRAND", "name": "Tidewell", "origin": "QLOO"}]
    assert '"affinity' not in json.dumps(m)
    pv = client.get(f"/api/projects/{pid}/qloo/places/SYN-toronto-0", params={"city": "toronto"}).json()
    assert pv["place"]["name"] == "Toronto place 0" and "affinity_in_request" not in pv["place"]
    assert client.get(f"/api/projects/{pid}/qloo/places/SYN-lisbon-0", params={"city": "toronto"}).status_code == 404


def test_five_failures_retry_update_and_founder_selections_survive(client, markets_env):
    """P3-02 acceptance: 75 + 5, partition 80, partial; untick/add; retry only the 5; update; selections kept."""
    fails = ["lisbon", "lagos", "tokyo", "paris", "berlin"]
    markets_env.qloo.fail = dict.fromkeys(fails, 500)
    pid, m = scanned(client, markets_env)
    cov = m["coverage"]
    assert (cov["returned"], cov["failed"]) == (75, 5) and sum(cov[s] for s in ("returned", "empty", "failed", "unresolved")) == 80
    assert m["run"]["status"] == "partial" and m["generation"]["status"] == "partial"
    assert len(markets_env.qloo.places()) == 75 + 5 * 3  # every attempt dispatched and ledgered
    assert {n["action"] for n in m["notices"]} == {"retry_scan"}
    failed = next(c for c in m["cities"] if c["city_id"] == "lisbon")
    assert failed["attempts"] == 3 and "500" in failed["reason"]
    assert markets_env.strategist.calls == 1 and len(m["assessment"]["cards"]) == 6

    sl = m["shortlist"]
    paris = next(c for c in m["cities"] if c["city_id"] == "paris")
    assert paris["deferred"]["reason"] == "NOT_PRIORITISED"  # ranked, not among the first 6; its failed scan is apart
    r = client.patch(f"/api/projects/{pid}/shortlist", json={"base_version": sl["version"], "exclude": ["vancouver"], "add": ["madrid"]})
    assert r.json() == {"version": 2}
    assert client.patch(f"/api/projects/{pid}/shortlist", json={"base_version": 1, "exclude": ["toronto"]}).status_code == 409
    assert client.patch(f"/api/projects/{pid}/shortlist", json={"base_version": 2, "add": ["atlantis"]}).status_code == 422

    markets_env.qloo.fail = {}
    before = len(markets_env.qloo.places())
    client.post(f"/api/projects/{pid}/scan/retry")
    idle(pid)
    again = [POINTS[p["filter.location"]] for _, p in markets_env.qloo.places()[before:]]
    assert sorted(again) == sorted(fails)  # only those five were requested again
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert m["coverage"]["returned"] == 80 and m["generation"]["status"] == "complete"
    assert markets_env.strategist.calls == 1  # no automatic re-run
    assert not [n for n in m["notices"] if n["action"] == "update"]  # no recommended city's evidence changed
    assert db.get_record(f"cs_{m['generation']['id']}_lisbon")["version"] == 2  # history kept: v1 failed, v2 returned
    assert db.get_record(f"cs_{m['generation']['id']}_lisbon", 1)["status"] == "failed"

    q = len(markets_env.qloo.calls)
    client.post(f"/api/projects/{pid}/shortlist/update")
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert len(markets_env.qloo.calls) == q and markets_env.strategist.calls == 2  # 0 Qloo: the ranking is reused
    picks = {c["city_id"]: c for c in m["shortlist"]["cities"]}
    assert picks["vancouver"]["included"] is False and picks["madrid"]["added_by_founder"] and picks["madrid"]["included"]
    assert m["shortlist"]["version"] == 3 and m["assessment"]["version"] == 2
    assert not [n for n in m["notices"] if n["action"] == "update"]
    m2 = client.get(f"/api/projects/{pid}/markets").json()  # refresh
    assert m2["shortlist"] == m["shortlist"]


def test_a_strategist_failure_leaves_every_text_deterministic_and_update_retries_with_zero_qloo(client, markets_env):
    markets_env.strategist.fail = True
    pid, m = scanned(client, markets_env)
    a = m["assessment"]
    assert m["shortlist_run"]["status"] == "completed" and [c["city_id"] for c in a["cards"]] == RECOMMENDED
    assert a["lead"]["source"] == "DETERMINISTIC" and "SYNTHETIC strategist failure" in a["model_error"]
    assert all(set(c["prose_source"].values()) == {"DETERMINISTIC"} for c in a["cards"])
    assert m["notices"] == []
    q = len(markets_env.qloo.calls)
    markets_env.strategist.fail = False
    assert client.post(f"/api/projects/{pid}/shortlist/update").status_code == 200
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert m["assessment"]["lead"]["source"] == "MODEL" and len(markets_env.qloo.calls) == q
    assert markets_env.strategist.calls == 2


def test_restart_mid_scan_does_not_repeat_completed_cities(client, markets_env):
    pid, m = scanned(client, markets_env)
    gid = m["generation"]["id"]
    run_id = db.q("SELECT id FROM runs WHERE project_id=? AND kind='scan'", pid)[0]["id"]
    later = markets.ORDER[40:]
    # Simulate a stop after 40 cities: the later steps, their CityScans and the handoff never happened.
    db.x("UPDATE runs SET status='running' WHERE id=?", run_id)
    for cid in later:
        db.x("DELETE FROM tasks WHERE run_id=? AND task_key=?", run_id, f"city:{cid}")
        db.x("DELETE FROM records WHERE id=?", f"cs_{gid}_{cid}")
    db.x("DELETE FROM tasks WHERE run_id=? AND task_key IN ('close', 'handoff')", run_id)
    scan._update_generation(gid, lambda g: [g["cities"].pop(c) for c in later])
    db.x("DELETE FROM provider_cache")  # the guarantee rests on task state, never on the 7-day cache
    before = len(markets_env.qloo.places())
    runner.resume_all()
    idle(pid)
    again = [POINTS[p["filter.location"]] for _, p in markets_env.qloo.places()[before:]]
    assert sorted(again) == sorted(later)  # completed cities are never re-dispatched
    assert markets_env.strategist.calls == 1  # the handoff saw the existing assessment: no second strategist


def test_restart_at_the_handoff_gives_exactly_one_strategist_task(client, markets_env):
    pid, m = scanned(client, markets_env)
    srun = db.q("SELECT id FROM runs WHERE project_id=? AND kind='shortlist'", pid)[0]["id"]
    scan_run = db.q("SELECT id FROM runs WHERE project_id=? AND kind='scan'", pid)[0]["id"]
    # A stop while the strategist was running and before the scan's handoff step was marked done.
    db.x("UPDATE runs SET status='running' WHERE id IN (?,?)", srun, scan_run)
    db.x("UPDATE tasks SET status='running' WHERE run_id=? AND task_key='handoff'", scan_run)
    db.x("DELETE FROM tasks WHERE run_id=? AND task_key='assemble'", srun)
    db.x("DELETE FROM records WHERE kind IN ('assessment_set','shortlist') AND project_id=?", pid)
    runner.resume_all()
    idle(pid)
    assert db.q("SELECT COUNT(*) n FROM runs WHERE project_id=? AND kind='shortlist'", pid)[0]["n"] == 1
    assert markets_env.strategist.calls == 1  # the strategist step was memoized
    assert client.get(f"/api/projects/{pid}/markets").json()["shortlist"]["version"] == 1


def _claim(pid, label):
    b = db.latest("brief", pid)
    return next(i for i in b["facts"] + b["interpretation"] if db.get_record(i)["label"] == label), b["version"]


def test_p3_01_home_correction_recomputes_fit_and_asks_for_an_update_with_no_calls(client, markets_env):
    pid, m = scanned(client, markets_env)
    sea = next(c for c in m["cities"] if c["city_id"] == "seattle")
    assert sea["fit"]["chip"] == "SAME_COUNTRY" and m["home"]["note"] == "placed" and sea["excluded"] == "OUTSIDE_GOAL"
    q, b, s = len(markets_env.qloo.calls), bedrock_rows(pid), markets_env.strategist.calls
    home, v = _claim(pid, "Home market")
    r = client.patch(f"/api/projects/{pid}/brief", json={"base_version": v, "claim_id": home, "value": "Lisbon, Portugal"})
    assert "Your shortlist used it too" in r.json()["notice"]
    m = client.get(f"/api/projects/{pid}/markets").json()
    rows = {c["city_id"]: c for c in m["cities"]}
    assert rows["seattle"]["fit"]["chip"] == "IMPORT_NEEDED" and rows["lisbon"]["excluded"] == "ALREADY_HERE"  # the home city
    assert rows["lisbon"]["fit"]["chip"] == "SAME_COUNTRY" and m["home"]["provenance"] == "YOU_SAID"
    notice = next(n for n in m["notices"] if n["action"] == "update")
    assert notice["rerank"] and "your home market" in notice["text"] and "Fit is recomputed" in notice["text"]
    assert m["generation"]["current"] and not [n for n in m["notices"] if n["action"] == "rescan"]  # scan not outdated
    assert (len(markets_env.qloo.calls), bedrock_rows(pid), markets_env.strategist.calls) == (q, b, s)  # 0 Qloo, 0 model
    pages = len(markets_env.qloo.of("urn:entity:destination"))
    client.post(f"/api/projects/{pid}/shortlist/update")
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert len(markets_env.qloo.of("urn:entity:destination")) - pages == -(-m["ranking"]["n_candidates"] // 50)
    assert len(markets_env.qloo.places()) == 80 and m["assessment"]["cards"][0]["city_id"] == "toronto"
    assert "chicago" in [c["city_id"] for c in m["assessment"]["cards"]]  # the US is abroad now
    assert not [n for n in m["notices"] if n["action"] == "update"]


def test_p3_01_a_profile_change_outdates_the_generation(client, markets_env):
    pid, m = scanned(client, markets_env)
    ident = client.get(f"/api/projects/{pid}").json()["identity"]
    client.post(f"/api/projects/{pid}/identity", json={"base_version": ident["version"], "entity_id": "none"})
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert m["generation"]["current"] is False and m["assessment"]["cards"]  # the old scan stays readable
    assert any("profile changed after this scan" in n["text"] for n in m["notices"])
    assert client.post(f"/api/projects/{pid}/scan").status_code == 409  # re-scan needs a confirmed profile


def test_p3_05_strategist_input_order_and_lead_are_outside_model_control(client, markets_env):
    markets_env.strategist.lead = "Calgary is first."  # names a city that is not a top
    # SYNTHETIC: the reply swaps the order, adds an extra city and omits Toronto
    markets_env.strategist.mutate = lambda out: {**out, "cities": [*out["cities"][1:][::-1],
                                                                   {**out["cities"][1], "city_id": "paris"}]}
    pid, m = scanned(client, markets_env)
    a = m["assessment"]
    assert [c["city_id"] for c in a["cards"]] == RECOMMENDED
    assert a["lead"]["source"] == "DETERMINISTIC" and a["lead"]["text"].startswith("Toronto is the top recommendation")
    toronto = a["cards"][0]
    assert set(toronto["prose_source"].values()) == {"DETERMINISTIC"} and "Toronto place 0" in toronto["why"]
    assert a["cards"][1]["prose_source"]["why"] == "MODEL"
    n = m["ranking"]["n_candidates"]
    assert toronto["position_line"] == f"In one Qloo ranking of your {n} candidate cities, Toronto came 1st for people who like Tidewell."
    aset = db.latest("assessment_set", pid)
    assert aset["ignored"] == ["paris"]
    inp = aset["input"]
    assert "affinity" not in json.dumps(inp) and "affinity" not in scan.prompt_of(inp)
    assert "came 1st" not in scan.prompt_of(inp) and "clm_" not in scan.prompt_of(inp)
    assert inp["home"]["value"] == "Portland, Oregon, USA" and len(inp["cities"]) == 6


def test_allowance_running_out_mid_scan_fails_the_rest_undispatched(client, markets_env, monkeypatch):
    pid = matched_project(client)
    idle(pid)  # the store search and audiences finish first
    used = ledger.totals()["qloo"]
    monkeypatch.setattr(config, "CAP_QLOO", used + 10)
    client.post(f"/api/projects/{pid}/scan")
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    # the scan and the ranking share the same 10 attempts: never more, and every city still has a terminal state
    places, ranking_pages = len(markets_env.qloo.places()), len(markets_env.qloo.of("urn:entity:destination"))
    assert places + ranking_pages == 10 == ledger.totals()["qloo"] - used
    assert (m["coverage"]["returned"], m["coverage"]["failed"], m["coverage"]["attempted"]) == (places, 80 - places, places)
    assert m["run"]["status"] == "partial"
    gid = m["generation"]["id"]
    denied = db.get_record(f"cs_{gid}_{markets.ORDER[-1]}")
    assert denied["error"]["dispatched"] is False and denied["attempts"] == 0 and denied["origin"] == "denied"


def test_an_empty_ranking_recommends_nothing_and_the_founder_adds_a_city(client, markets_env):
    markets_env.qloo.rank_empty = True
    pid, m = scanned(client, markets_env)
    assert m["assessment"]["cards"] == [] and m["assessment"]["lead"]["source"] == "NONE" and markets_env.strategist.calls == 0
    assert m["ranking"]["state"] == "empty" and [n["action"] for n in m["notices"]] == ["retry_ranking"]
    assert m["shortlist"]["cities"] == []
    lisbon = next(c for c in m["cities"] if c["city_id"] == "lisbon")
    assert lisbon["deferred"]["reason"] == "NOT_RANKED" and "Qloo ranks" not in json.dumps(m)
    assert client.patch(f"/api/projects/{pid}/shortlist", json={"base_version": 1, "add": ["lisbon"]}).json() == {"version": 2}
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert m["shortlist"]["included"] == 1 and m["shortlist"]["added"] == 1
    markets_env.qloo.rank_empty = False
    assert client.post(f"/api/projects/{pid}/ranking/retry").status_code == 200
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert [c["city_id"] for c in m["assessment"]["cards"]] == RECOMMENDED and m["shortlist"]["added"] == 1
    assert len(markets_env.qloo.places()) == 80  # no scan


# ---- review slice 3 repairs ----
class Malformed(FakeQloo):
    """SYNTHETIC: one city answers 200 with an unreadable body, or the transport raises something unexpected."""

    def __init__(self, city, answer):
        super().__init__()
        self.city, self.answer = city, answer

    def __call__(self, url, params=None, **kw):
        p = dict(params or {})
        if p.get("filter.type") == "urn:entity:place" and POINTS[p["filter.location"]] == self.city:
            self.calls.append(("insights", p))
            if isinstance(self.answer, Exception):
                raise self.answer
            return SimpleNamespace(status_code=200, headers={}, text=json.dumps(self.answer))
        return super().__call__(url, params, **kw)


@pytest.mark.parametrize("answer", [
    {"success": True, "results": {"entities": [{"name": "no id"}]}},
    {"success": True, "results": {"entities": ["not an object"]}},
    {"success": True, "results": {"entities": [{"entity_id": "X", "name": "x", "tags": ["not an object"]}]}},
    RuntimeError("SYNTHETIC unexpected failure"),
])
def test_blocker_1_one_bad_answer_fails_only_its_city(client, markets_env, monkeypatch, answer):
    """Review slice 3, blocker 1: the bad answer goes through qloo.get, not only classify."""
    bad = Malformed("chicago", answer)
    monkeypatch.setattr(qloo.httpx, "get", bad)
    pid, m = scanned(client, markets_env)
    chicago = next(c for c in m["cities"] if c["city_id"] == "chicago")
    if "tags" in str(answer):  # a readable place with one bad tag: the tag is ignored, nothing crashes
        assert chicago["status"] == "returned" and m["coverage"]["returned"] == 80
        return
    assert chicago["status"] == "failed" and m["coverage"]["returned"] == 79  # the other 79 continued
    if not isinstance(answer, Exception):
        assert chicago["reason"] == "unreadable response"
        assert not db.q("SELECT 1 FROM provider_cache WHERE response LIKE '%no id%' OR response LIKE '%not an object%'")
    assert m["run"]["status"] == "partial" and m["generation"]["status"] == "partial"
    assert [n["action"] for n in m["notices"]] == ["retry_scan"] and m["assessment"]["cards"]
    bad.city = None  # Chicago answers properly now
    assert client.post(f"/api/projects/{pid}/scan/retry").status_code == 200
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert m["coverage"]["returned"] == 80 and m["generation"]["status"] == "complete" and m["notices"] == []


def test_blocker_1_a_stopped_scan_is_never_left_running(client, markets_env, monkeypatch):
    """A replay miss on the scan stage fails the run visibly; the generation closes and Retry reaches every city."""
    pid = matched_project(client)
    monkeypatch.setattr(config, "LIVE_STAGES", tuple(x for x in LIVE if x != "scan"))  # the scan may not go live
    client.post(f"/api/projects/{pid}/scan")
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert m["run"]["status"] == "failed" and m["generation"]["status"] == "partial"
    n = m["notices"][0]
    assert n["action"] == "retry_scan" and "The scan stopped" in n["text"] and "80 cities were not searched" in n["text"]
    assert m["coverage"]["queued"] == 80 and markets_env.strategist.calls == 0
    monkeypatch.setattr(config, "LIVE_STAGES", LIVE)
    assert client.post(f"/api/projects/{pid}/scan/retry").status_code == 200
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert m["coverage"]["returned"] == 80 and m["generation"]["status"] == "complete"
    assert markets_env.strategist.calls == 1 and m["assessment"]["cards"]  # the handoff ran once, at the first terminal state


def test_blocker_2_city_names_in_other_spellings():
    assert markets.names_in("Sao Paulo, NYC, Washington, D.C. and Zurich", markets.ORDER) == \
        ["sao-paulo", "new-york", "washington-dc", "zurich"]
    assert markets.names_in("Bogota and Bogotá", markets.ORDER) == ["bogota"]


def test_blocker_3_domestic_only_shipping():
    r = ship("We don't ship outside Germany.")
    assert (r("DE"), r("FR")) == (True, False)
    r = ship("We currently do not ship outside the UK")
    assert (r("GB"), r("US")) == (True, False)
    r = ship("We do not ship to Germany")  # unchanged: a named exclusion
    assert (r("DE"), r("FR")) == (False, None)


# ---- targeted closure repairs (docs/REVIEW-SLICE-03.md, "Targeted closure") ----
def test_closure_unreadable_nested_field_fails_only_its_city(client, markets_env, monkeypatch):
    bad = {"success": True, "results": {"entities": [{"entity_id": "X", "name": "x", "query": "unreadable nested query"}]}}
    monkeypatch.setattr(qloo.httpx, "get", Malformed("chicago", bad))
    pid, m = scanned(client, markets_env)
    chicago = next(c for c in m["cities"] if c["city_id"] == "chicago")
    assert (chicago["status"], chicago["reason"], chicago["attempts"]) == ("failed", "unreadable response", 1)
    assert m["coverage"]["returned"] == 79 and m["coverage"]["queued"] == 0
    sent = db.q("SELECT COUNT(*) n FROM usage WHERE project_id=? AND provider='qloo' AND origin='live'", pid)[0]["n"]
    # identity + store search + 2 ranking pages + audiences and demographics + 80 cities: the attempt is kept (C1)
    assert sent == ledger.totals()["qloo"] == 1 + 1 + 2 + 2 + 80
    assert not db.q("SELECT 1 FROM provider_cache WHERE response LIKE '%unreadable nested%'")


def test_closure_an_unexpected_transport_failure_is_counted_once(client, markets_env, monkeypatch):
    monkeypatch.setattr(qloo.httpx, "get", Malformed("chicago", RuntimeError("SYNTHETIC")))
    pid, m = scanned(client, markets_env)
    c = db.get_record(f"cs_{m['generation']['id']}_chicago")
    sent = db.q("SELECT COUNT(*) n FROM usage WHERE project_id=? AND provider='qloo' AND origin='live'", pid)[0]["n"]
    assert sent == ledger.totals()["qloo"] and c["attempts"] == 1 and c["status"] == "failed"


def test_closure_an_unreadable_answer_cached_earlier_is_never_served(client, markets_env):
    from landfall.cassette import key_of
    pid = matched_project(client)
    prof = db.latest("profile", pid)
    params = {k: str(v) for k, v in sorted(markets.scan_params(markets.CITIES["chicago"], markets.effective_signals(prof)).items())}
    key = key_of("qloo", "/v2/insights", {"path": "/v2/insights", "params": params})
    bad = {"status": 200, "headers": {}, "body": json.dumps({"success": True, "results": {"entities": [{"name": "no id"}]}})}
    db.x("INSERT INTO provider_cache VALUES(?,?,?,?)", key, "qloo", json.dumps(bad), db.now())
    client.post(f"/api/projects/{pid}/scan")
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert next(c for c in m["cities"] if c["city_id"] == "chicago")["status"] == "returned"
    assert [POINTS[p["filter.location"]] for _, p in markets_env.qloo.places()].count("chicago") == 1  # fetched afresh


def test_closure_a_stop_after_the_last_city_still_recovers(client, markets_env, monkeypatch):
    pid = matched_project(client)
    real = scan._scan_city

    def last_then_stop(run, gen, cid):
        out = real(run, gen, cid)
        if cid == markets.ORDER[-1]:
            runner.cancel(run.id)  # the stop lands between the last city and the close step
        return out
    monkeypatch.setattr(scan, "_scan_city", last_then_stop)
    client.post(f"/api/projects/{pid}/scan")
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert m["run"]["status"] == "cancelled" and m["generation"]["status"] == "complete" and m["coverage"]["returned"] == 80
    assert [n["action"] for n in m["notices"]] == ["retry_shortlist"] and markets_env.strategist.calls == 0
    assert client.post(f"/api/projects/{pid}/shortlist/retry").status_code == 200
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert m["assessment"]["cards"] and markets_env.strategist.calls == 1


def test_closure_a_city_retry_never_repeats_the_automatic_handoff(client, markets_env, monkeypatch):
    bad = Malformed("chicago", {"success": False})
    monkeypatch.setattr(qloo.httpx, "get", bad)
    pid, m = scanned(client, markets_env)
    assert markets_env.strategist.calls == 1 and m["shortlist_run"]["status"] == "completed"
    bad.city = None
    client.post(f"/api/projects/{pid}/scan/retry")
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert m["coverage"]["returned"] == 80 and markets_env.strategist.calls == 1  # no second automatic call (C2)
    client.post(f"/api/projects/{pid}/shortlist/update")
    idle(pid)
    assert markets_env.strategist.calls == 2 and client.get(f"/api/projects/{pid}/markets").json()["assessment"]["cards"]


def test_concurrent_scan_keeps_the_rate_under_five_a_second(client, markets_env, monkeypatch):
    """4 cities in flight, one shared pacer: dispatch starts never exceed 4.5 a second (Qloo allows 5)."""
    monkeypatch.setattr(qloo, "PACE_S", 1 / 4.5)
    starts = []
    real = markets_env.qloo.__call__

    def slow(url, params=None, **kw):
        if (params or {}).get("filter.type") == "urn:entity:place":
            starts.append(time.monotonic())
        threading.Event().wait(0.3)  # a real provider latency (time.sleep is stubbed in tests), so requests overlap
        return real(url, params, **kw)
    monkeypatch.setattr(qloo.httpx, "get", slow)
    monkeypatch.setattr(markets, "ORDER", markets.ORDER[:12])  # a short catalog keeps the test quick
    monkeypatch.setattr(scan.markets, "ORDER", markets.ORDER)
    pid = matched_project(client)
    t0 = time.monotonic()
    client.post(f"/api/projects/{pid}/scan")
    idle(pid)
    places = starts
    gaps = [b - a for a, b in zip(places, places[1:])]
    assert len(places) == 12 and min(gaps) >= 0.21
    assert time.monotonic() - t0 < 12 * (0.3 + 1 / 4.5)  # overlapped: faster than one at a time


def test_markets_start_by_themselves_once_the_brief_and_identity_are_settled(client, markets_env, monkeypatch):
    """Owner, 28 Oct 2026: no "Explore markets" click is needed; the scan starts once, and the button then only navigates."""
    from landfall import autoplan
    monkeypatch.setattr(config, "AUTO_PLAN", True)
    monkeypatch.setattr(autoplan, "prewarm", lambda *a: None)  # the deep dives are autoplan's tests
    pid = client.post("/api/projects", json={"url": "http://tidewell.example/"}).json()["project_id"]
    wait_for(client, pid)
    wait_for_identity(client, pid)
    idle(pid)
    m = client.get(f"/api/projects/{pid}/markets").json()
    assert m["auto"] and m["generation"]["status"] == "complete" and m["assessment"]["cards"]
    assert len(scan.generations(pid)) == 1 and len(markets_env.qloo.places()) == 80
    started = [e for e in db.events(pid) if "exploring markets" in (e.get("task") or "")]
    assert [(e["kind"], e["task"]) for e in started] == [("activity", "Started exploring markets once your brief was ready")]
    # the button now reuses the scan, and a second settle never starts another
    assert client.post(f"/api/projects/{pid}/scan").json()["run_id"] is None
    assert scan.auto_start(pid) is None and len(scan.generations(pid)) == 1


def test_markets_wait_for_the_founder_when_switched_off_or_identity_needs_them(client, markets_env, monkeypatch):
    pid = matched_project(client)  # AUTO_PLAN is off in tests
    idle(pid)
    assert scan.generations(pid) == [] and scan.auto_start(pid) is None
    monkeypatch.setattr(config, "AUTO_PLAN", True)
    ident = db.latest("identity", pid)
    db.put_record("identity", pid, {**{k: v for k, v in ident.items() if k not in ("id", "version", "kind", "project_id",
                                                                                    "created_at", "inputs")},
                                    "status": "needs_founder"}, inputs=ident["inputs"], rid=ident["id"])
    assert scan.auto_start(pid) is None and scan.generations(pid) == []


def test_what_we_found_counts_kept_partners_and_contacts_never_qloo_places(monkeypatch):
    """SYNTHETIC dossiers: per way in, KEPT candidates, else PENDING ones as leads; digital is left out."""
    def cand(decision, route="UNKNOWN"):
        return {"decision": decision, "axes": {"operational": {"contact_route": route}}}
    dossiers = {"retail": [cand("KEPT", "OWN_SITE"), cand("KEPT"), cand("PENDING")], "popup": [cand("KEPT", "EMAIL")],
                "creator": [cand("PENDING")], "community": [cand("REJECTED")], "digital": [cand("KEPT", "NA")]}
    monkeypatch.setattr(db, "get_record", lambda rid, v=None: {"status": "complete", "candidates": dossiers[rid.split("_")[-1]]}
                        if rid.startswith("dos_p_x_") else None)
    assert scan.found_of("p", "x", "complete", False, False) == {
        "state": "found", "line": "2 shops, 1 with a contact · 1 pop-up venue with a contact · a creator lead"}
    assert scan.found_of("p", "x", "running", False, True)["line"].endswith(" · still researching")
    assert scan.found_of("p", "y", "running", False, True) == {"state": "researching", "line": "Researching now…"}
    assert scan.found_of("p", "y", None, False, True)["line"] == "Queued for research"
    assert scan.found_of("p", "y", None, False, False)["line"] == "Researched when you build a plan"
