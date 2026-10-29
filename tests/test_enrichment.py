"""Resilient brand reading and the enrichment track (owner, 27 Oct 2026). All inputs SYNTHETIC; no network.

The reader speaks HTTP/2 first (no-ALPN HTTP/1.1 only when the HTTP/2 handshake fails), keeps a per-run cookie jar,
paces each host and honours one Retry-After ≤ 90 s per host. A Shopify store's own product feed fills products and
photos; web facts count only with a supported quote; Qloo stays first wherever it has the same thing."""
import base64
import json
import threading

import httpcore
import pytest

from landfall import brand, config, db, fetch, ledger, products, ranking
from landfall.cassette import Cassette
from landfall.runner import Run


# ---- fetcher: HTTP/2 first, handshake fallback, cookies, pacing ----
@pytest.fixture
def sends(monkeypatch):
    calls, answers = [], []
    monkeypatch.setattr(fetch, "_pace", lambda host: None)

    def send(url, headers, http2):
        calls.append({"url": url, "http2": http2, "cookie": headers.get("Cookie")})
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(fetch, "_send", send)
    return calls, answers


def _ok(body=b"<title>x</title>", extra=()):
    return 200, [(b"content-type", b"text/html; charset=utf-8"), *extra], body


def test_http2_first_and_no_alpn_only_when_the_handshake_fails(sends):
    calls, answers = sends
    answers += [httpcore.ConnectError("[SSL: TLSV1_ALERT_NO_APPLICATION_PROTOCOL] ALPN handshake failure"), _ok()]
    out = fetch._live_get("https://shop.example/")
    assert out["status"] == 200 and [c["http2"] for c in calls] == [True, False]
    lines = [json.loads(x) for x in config.LEDGER_PATH.read_text().split("\n") if x]
    assert [(x["outcome"], x["http2"]) for x in lines if x["kind"] == "attempt"] == [("transport_error", True), ("200", False)]


def test_a_missing_host_or_a_timeout_is_not_retried_over_http1(sends):
    calls, answers = sends
    answers += [httpcore.ConnectError("[Errno 8] nodename nor servname provided")]
    assert fetch._live_get("https://nowhere.example/")["error"] == "NoSuchHost" and len(calls) == 1


def test_cookies_are_kept_per_host_for_the_run_and_shopify_is_recognised(sends):
    calls, answers = sends
    answers += [_ok(extra=[(b"set-cookie", b"_shopify_y=abc; Path=/; Secure"), (b"x-shopify-stage", b"production")]),
                _ok(), _ok()]
    jar: dict = {}
    first = fetch._live_get("https://shop.example/", jar=jar)
    fetch._live_get("https://shop.example/pages/about", jar=jar)
    fetch._live_get("https://other.example/", jar=jar)
    assert first["shopify"] is True
    assert [c["cookie"] for c in calls] == [None, "_shopify_y=abc", None]


def test_a_cassette_carries_its_own_jar():
    a, b = Cassette("live"), Cassette("live")
    fetch._jar_of(a)["h"] = {"k": "v"}
    assert fetch._jar_of(a) == {"h": {"k": "v"}} and fetch._jar_of(b) == {}


def test_pacing_spaces_requests_to_one_host_two_seconds_apart(monkeypatch):
    clock, slept = [100.0], []
    monkeypatch.setattr(fetch.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(fetch.time, "sleep", lambda s: slept.append(round(s, 3)))
    monkeypatch.setattr(fetch, "_next_at", {})
    for host in ("a.example", "a.example", "b.example", "a.example"):
        fetch._pace(host)
    assert slept == [2.0, 4.0]  # the second and third request to a.example; b.example goes at once


# ---- one Retry-After per host per run ----
class _Cancel:
    def __init__(self):
        self.waited = []

    def wait(self, s):
        self.waited.append(round(s))
        return False

    def is_set(self):
        return False


def _run(pid="prj_e"):
    return Run("run_e", pid, "brand_read", {}, cassette=None, cancel=_Cancel())


def _pages(monkeypatch, answers):
    seen = []

    def read_page(run, url):
        seen.append(url)
        return {"id": "s", "url": url, "final_url": url, "title": "", "text": "x" * 200, "links": [], **answers.pop(0)}
    monkeypatch.setattr(brand, "read_page", read_page)
    return seen


def test_a_429_waits_once_up_to_90_seconds_and_says_so(monkeypatch):
    monkeypatch.setattr(brand, "_waits", {})
    seen = _pages(monkeypatch, [{"status": 429, "retry_after": "60"}, {"status": 200},
                                {"status": 429, "retry_after": "60"}])
    run = _run()
    assert brand.read_polite(run, "https://shop.example/", row="open")["status"] == 200
    assert run.cancel.waited and 59 <= run.cancel.waited[0] <= 60
    # later, the wait has been used: a new 429 from that host is final
    monkeypatch.setattr(brand.time, "monotonic", lambda: 10 ** 9)
    assert brand.read_polite(run, "https://shop.example/b")["status"] == 429 and len(seen) == 3
    shown = [e for e in db.events("prj_e", 0, "reading")]
    assert shown[-1]["detail"] == "waiting for shop.example (asked us to wait 60 s)"


@pytest.mark.parametrize("answer", [{"status": 403}, {"status": 429, "retry_after": "120"}, {"status": 429}])
def test_a_refusal_a_long_wait_or_no_retry_after_is_never_retried(monkeypatch, answer):
    monkeypatch.setattr(brand, "_waits", {})
    seen = _pages(monkeypatch, [answer])
    run = _run()
    assert brand.read_polite(run, "https://shop.example/")["status"] == answer["status"]
    assert len(seen) == 1 and run.cancel.waited == []


# ---- the Shopify product feed ----
FEED = {"products": [
    {"title": "Lockridge Shirt", "handle": "lockridge", "product_type": "Shirts", "body_html": "<p>Wrinkle-free <b>cotton</b></p>",
     "variants": [{"price": "98.00"}], "images": [{"src": "https://cdn.shopify.com/s/files/1/lockridge.jpg?v=1"}]},
    {"title": "Gift Card", "handle": "gift-card", "product_type": "Gift Card", "variants": [{"price": "50.00"}], "images": []},
    {"title": "Merino Polo", "handle": "merino-polo", "product_type": "Polos", "body_html": "",
     "variants": [{"price": "88.00"}], "images": [{"src": "//cdn.shopify.com/s/files/1/polo.png"}]},
]}
JPEG = b"\xff\xd8\xff" + b"0" * 64


@pytest.fixture
def store(monkeypatch):
    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", "prj_f", "s", "https://shop.example/", "live", None, None, db.now())
    asked = []
    monkeypatch.setattr(brand, "robots_allows", lambda run, u: True)

    def get(url, cassette, stage=""):
        asked.append(url)
        if url.endswith("/products.json?limit=250"):
            return {"url": url, "status": 200, "content_type": "application/json", "body": json.dumps(FEED)}
        return {"url": url, "status": 404, "body": ""}

    def get_bytes(url, cassette, stage=""):
        asked.append(url)
        ctype = "image/png" if "polo" in url else "image/jpeg"
        return {"url": url, "status": 200, "content_type": ctype, "b64": base64.b64encode(JPEG).decode()}
    monkeypatch.setattr(fetch, "get", get)
    monkeypatch.setattr(fetch, "get_bytes", get_bytes)
    return asked


def test_shopify_feed_detection():
    assert products.shopify_items(json.dumps(FEED)) and products.shopify_items("<html>") is None
    assert products.shopify_items(json.dumps({"products": [{"title": "x"}]})) is None


def test_the_feed_gives_products_prices_photos_and_quoted_facts(store):
    run = Run("run_f", "prj_f", "brand_read", {}, cassette=Cassette("live"))
    out = products.read_feed(run, "prj_f", "https://shop.example/", {"currency": "USD"})
    assert (out["products"], out["images"], out["shopify"]) == (2, 2, True)
    assert "https://cdn.shopify.com/s/files/1/lockridge.jpg?v=1&width=800" in store
    view = products.view("prj_f")
    assert [(i["title"], i["price_text"], i["discovered_via"]) for i in view["items"]] == [
        ("Lockridge Shirt", "USD 98.00", "SHOPIFY_FEED"), ("Merino Polo", "USD 88.00", "SHOPIFY_FEED")]
    assert all(i["image"] and i["image_why"] is None for i in view["items"])
    # the launch kit matches saved photos by product page (mockups.kit_of)
    arts = {a["source"]["page"]: a for a in db.records("artifact", "prj_f")}
    assert set(arts) == {"https://shop.example/products/lockridge", "https://shop.example/products/merino-polo"}
    assert all(a["source"]["kind"] == "FETCHED" and a["rights"] == "brand_own_unconfirmed" for a in arts.values())
    text = db.q("SELECT text FROM sources WHERE url=?", out["url"])[0]["text"]
    assert [f["origin"] for f in out["facts"]] == ["FEED", "FEED"]
    assert all(f["source_url"] == out["url"] and brand.quote_found(f["quote"], text) for f in out["facts"])
    # a second read never saves the same photo twice
    products.read_feed(run, "prj_f", "https://shop.example/", {"currency": "USD"})
    assert len(db.records("artifact", "prj_f")) == 2


def test_a_read_homepage_that_isnt_shopify_asks_for_no_feed(store):
    run = Run("run_f", "prj_f", "brand_read", {}, cassette=Cassette("live"))
    assert products.read_feed(run, "prj_f", "https://shop.example/", {"ok": True, "shopify": False})["status"] == "NOT_SHOPIFY"
    assert store == []


# ---- Qloo first in presence; the frank line ----
def _identity(pid, name="UNTUCKit"):
    cand = {"qloo_id": "Q1", "name": name, "brand_tags": [], "tags": []}
    db.put_record("identity", pid, {"status": "decided", "decision": {"entity_id": "Q1"}, "candidates": [cand],
                                    "decided_by": "landfall"}, rid=f"identity_{pid}")


def _fact(pid, value, origin, state="SUPPORTED", quote="sold at Nordstrom"):
    return db.put_record("claim", pid, {"subject": {"record_kind": "brief", "key": "where_sold"}, "label": "Where you sell",
                                        "value": value, "state": state, "origin": origin, "quote": quote})["id"]


def test_presence_keeps_qloo_first_and_adds_web_stockists_only_where_qloo_has_none():
    pid = "prj_p"
    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", pid, "s", "https://www.untuckit.com/", "live", None, None, db.now())
    _identity(pid)
    db.put_record("current_presence", pid, {"stores": [{"qloo_id": "S1", "name": "UNTUCKit SoHo", "city_id": "new-york"}],
                                            "located_checks": [], "overrides": {}}, rid=f"cp_{pid}")
    facts = [_fact(pid, "Sold at Nordstrom in New York and Chicago", "WEB"),
             _fact(pid, "Sold at Holt Renfrew in Toronto", "WEB", state="TO_CHECK")]
    db.put_record("brief", pid, {"route": "site", "facts": facts, "interpretation": []}, rid=f"brief_{pid}")
    cities = {c["city_id"]: c["sources"] for c in ranking.presence_of(pid)["cities"]}
    assert [s["kind"] for s in cities["new-york"]] == ["QLOO_STORE"]
    assert cities["chicago"] == [{"kind": "WEB", "ref": facts[0], "quote": "sold at Nordstrom"}]
    assert "toronto" not in cities  # a web fact whose quote isn't supported is never used
    assert brand.qloo_name(pid) == "UNTUCKit"


def test_the_brief_says_frankly_what_limited_the_reader_and_what_filled_in():
    pid = "prj_r"
    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", pid, "s", "https://www.deathwishcoffee.com/", "live", None, None, db.now())
    at = "2026-10-27T11:00:00+00:00"
    for i, st in enumerate([200, 429, 429, 429, 429, 200]):
        db.x("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?)", f"s{i}", pid, f"https://www.deathwishcoffee.com/{i}",
             None, st, at, "", "", None, 0)
    db.x("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?)", "w1", pid, "https://news.example/a", None, 200, at, "", "", None, 0)
    db.put_record("enrichment", pid, {"sources": ["https://news.example/a"], "searches": [{}] * 3, "leads": [{}]},
                  rid=f"enr_{pid}")
    db.put_record("product_research", pid, {"route": "SHOPIFY_FEED", "products": [{"id": "p", "version": 1}] * 4},
                  rid=f"prr_{pid}")
    _identity(pid, "Death Wish Coffee")
    web = db.put_record("claim", pid, {"state": "SUPPORTED", "origin": "WEB", "source_ids": ["w1"]})["id"]
    feed = db.put_record("claim", pid, {"state": "SUPPORTED", "origin": "FEED", "source_ids": ["f"]})["id"]
    b = db.put_record("brief", pid, {"route": "site", "facts": [web, feed], "read_at": "2026-10-27T12:00:00+00:00"}) | {
        "project_id": pid}
    r = brand.read_summary(b)
    assert (r["pages_tried"], r["pages_failed"], r["unreadable"]) == (6, 4, False)  # the web lead isn't a site page
    assert r["enrichment"]["frank"] == ("deathwishcoffee.com limited our reader (HTTP 429 on 4 of 6 pages). Filled in "
                                        "from: your store's product feed (4 products), 1 web source, Qloo.")
    assert r["enrichment"]["origins"] == {web: "WEB", feed: "FEED"}


# ---- the read carries on when the site turns it away ----
def test_a_blocked_homepage_still_ends_with_a_brief_from_the_feed_and_the_web(monkeypatch):
    pid = "prj_b"
    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", pid, "s", "https://shop.example/", "live", None, None, db.now())
    monkeypatch.setattr(brand, "robots_allows", lambda run, u: True)
    monkeypatch.setattr(brand, "read_polite", lambda run, u, row=None: {"status": 429, "retry_after": "600", "text": ""})
    monkeypatch.setattr(brand, "_brief_ready", lambda run: None)
    feed_fact = {"field": "products", "label": "What you sell", "value": "Lockridge Shirt (Shirts)",
                 "source_url": "https://shop.example/products.json?limit=250", "quote": "Lockridge Shirt — Shirts — 98.00",
                 "origin": "FEED"}
    web_ok = {**feed_fact, "field": "where_sold", "value": "Nordstrom, Chicago", "origin": "WEB",
              "source_url": "https://news.example/a"}
    web_bad = {**web_ok, "value": "Harrods, London"}

    def enrich(run, url, site, home, opened):
        opened.wait(5)
        return {"facts": [feed_fact, web_ok, web_bad], "feed": {"products": 1}}
    monkeypatch.setattr(brand, "_enrich", enrich)
    monkeypatch.setattr(brand, "_check", lambda run, draft: [
        {**f, "key": f"f{i}", "state": "TO_CHECK" if f is web_bad or f["value"] == "Harrods, London" else "SUPPORTED"}
        for i, f in enumerate(draft["facts"])])
    run = Run("run_b", pid, "brand_read", {"url": "https://shop.example/"}, cassette=None, cancel=threading.Event())
    brand.brand_read(run)
    b = db.latest("brief", pid)
    values = [db.get_record(i)["value"] for i in b["facts"]]
    assert values == ["Lockridge Shirt (Shirts)", "Nordstrom, Chicago"]  # the unsupported web lead is dropped
    assert [db.get_record(i)["origin"] for i in b["facts"]] == ["FEED", "WEB"]
    assert b["brand_name"] == "shop"  # address-derived: Qloo had no match


def test_fetch_get_hands_the_run_jar_to_the_live_call(monkeypatch):
    seen = []
    monkeypatch.setattr(fetch, "_live_get", lambda url, binary=False: seen.append(fetch._ctx.jar) or {"url": url, "status": 404})
    cas = Cassette("live")
    fetch.get("https://shop.example/", cas)
    assert seen[0] is fetch._jar_of(cas) and fetch._ctx.jar is None


def test_lead_facts_keep_only_read_pages_and_a_home_needs_a_country(monkeypatch):
    facts = [brand.LeadFact(field="home_market", label="Based in", value="Saratoga Springs, New York",
                            source_url="https://news.example/a", quote="based in Saratoga Springs, New York"),
             brand.LeadFact(field="home_market", label="Based in", value="New York City, United States",
                            source_url="https://news.example/a", quote="headquartered in New York City"),
             brand.LeadFact(field="where_sold", label="x", value="Nordstrom, Chicago",
                            source_url="https://invented.example/", quote="sold at Nordstrom in Chicago")]

    class FakeAgent:
        def __init__(self, **kw):
            pass

        def __call__(self, prompt, **kw):
            return type("R", (), {"structured_output": brand.LeadFacts(facts=facts)})()
    monkeypatch.setattr(brand, "Agent", FakeAgent)
    monkeypatch.setattr(brand, "make_model", lambda *a, **k: None)
    page = {"url": "https://news.example/a", "final_url": "https://news.example/a", "title": "A", "text": "..."}
    out = brand._lead_facts(_run(), "Shop", "shop.example", [page])
    assert [(f["field"], f["label"], f["origin"]) for f in out] == [("other", "Based in", "WEB"), ("home_market", "Based in", "WEB")]


def test_a_colourway_listed_twice_is_one_product(store):
    FEED["products"].append({**FEED["products"][0], "handle": "lockridge-blue"})
    try:
        run = Run("run_f", "prj_f", "brand_read", {}, cassette=Cassette("live"))
        assert products.read_feed(run, "prj_f", "https://shop.example/", {"currency": "USD"})["products"] == 2
    finally:
        FEED["products"].pop()
