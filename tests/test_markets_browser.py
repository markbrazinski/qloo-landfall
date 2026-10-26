"""Slice 3 and 3.5 browser checks with SYNTHETIC Qloo and a SYNTHETIC strategist (BUILD-PLAN slice 3.5, Browser).

From URL entry through the replayed synthetic Tidewell brand read: the S01 goal sentence by keyboard → brief (presence
with coverage, audiences) → S04 ranking row → S05 groups, TOP RECOMMENDATION, first lines, a moved-up disclosure → All 80
chips and a founder addition → refresh; a purpose change → Update; a failed ranking with Retry; a small and an empty
`both` group; a stand-in swap → Update; B07, B08, a stopped scan; keyboard and 390 px. Screenshots:
.landfall/browser-slice35/ (git-ignored).
"""
import json
import re
import socket
import threading
import time
from types import SimpleNamespace

import pytest
from test_markets import LIVE, FakeQloo, G, Strategist

from landfall import config, db, identity, markets, qloo, scan
from conftest import SYNTHETIC

WEB_BUILT = (config.ROOT / "web" / "dist" / "index.html").exists()
SHOTS = config.ROOT / ".landfall" / "browser-slice35"
pytestmark = pytest.mark.skipif(not WEB_BUILT, reason="frontend not built (make web)")
US = [c for c in markets.ORDER if markets.CITIES[c]["country"] == "US"]


class Unresolving(FakeQloo):
    """SYNTHETIC: Qloo rejects the location filter for these cities (never observed live; unverified)."""

    def __init__(self, cities):
        super().__init__()
        self.reject = set(cities)

    def __call__(self, url, params=None, **kw):
        loc = (params or {}).get("filter.location")
        if loc and loc in {f"POINT({markets.CITIES[c]['lon']} {markets.CITIES[c]['lat']})" for c in self.reject}:
            self.calls.append(("insights", dict(params)))
            return SimpleNamespace(status_code=400, headers={}, text=json.dumps({"error": {"message": "Unsupported filter.location"}}))
        return super().__call__(url, params, **kw)


@pytest.fixture
def server(monkeypatch, tmp_path):
    def start(fake=None, strategist=None):
        monkeypatch.setattr(qloo.httpx, "get", fake or FakeQloo())
        monkeypatch.setattr(scan, "call_strategist", strategist or Strategist())
        monkeypatch.setattr(config, "REPLAY_CASES", [SYNTHETIC])
        monkeypatch.setattr(config, "LIVE_STAGES", LIVE)
        monkeypatch.setattr(config, "PRIVATE", tmp_path / "private")
        import uvicorn

        from landfall.app import app
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=srv.run, daemon=True).start()
        while not srv.started:
            time.sleep(0.05)
        started.append(srv)
        return f"http://127.0.0.1:{port}"
    started = []
    SHOTS.mkdir(parents=True, exist_ok=True)
    yield start
    for srv in started:
        srv.should_exit = True


def enter(page, base, scope=None, purpose=None):
    """S01: the URL and, optionally, the goal by mouse; Enter starts."""
    page.goto(base + "/")
    if scope:
        page.get_by_role("button", name=re.compile("^Where:")).click()
        page.get_by_role("option", name=scope).click()
    if purpose:
        page.get_by_role("button", name=re.compile("^Why:")).click()
        page.get_by_role("option", name=purpose).click()
    page.get_by_label("Your brand's website").fill("http://tidewell.example/")
    page.keyboard.press("Enter")


def to_recommendations(page, base, block_map=False, **goal):
    """URL entry → brief → Start exploring markets → S04 → banner → S05."""
    if block_map:
        page.route(re.compile(r".*countries-110m.*\.json"), lambda route: route.abort())
    enter(page, base, **goal)
    page.get_by_text("Matched automatically").wait_for(timeout=30000)
    page.get_by_role("button", name="Start exploring markets").click()
    page.wait_for_url(re.compile(r"/markets/scan$"), timeout=10000)
    page.get_by_role("heading", name=re.compile("Scanning 80 cities")).wait_for(timeout=10000)
    try:
        page.get_by_role("button", name="See the recommendations", exact=True).wait_for(timeout=60000)
    except Exception:
        page.screenshot(path=SHOTS / "debug-timeout.png", full_page=True)
        raise AssertionError(page.locator("main").inner_text()[:1500])
    return page.url.split("/p/")[1].split("/")[0]


def no_overflow(page):
    return not page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")


def stockists_fake():
    fake = FakeQloo()
    fake.stores["Tidewell"] = ["amsterdam", "brussels", "madrid", "istanbul", "bogota"]
    fake.rank_order = ["amsterdam", "paris", "berlin", "copenhagen", "lisbon", "oslo", *markets.ORDER]
    boutiques = [f"{G}:boutique", f"{G}:clothing_store", f"{G}:shoe_store", G]
    fake.place_kinds = {"lisbon": boutiques, "oslo": boutiques}
    return fake


def test_browser_goal_presence_ranking_recommendations_all80_update(server):
    from playwright.sync_api import expect, sync_playwright
    base = server(fake=stockists_fake())
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # S01 by keyboard: the goal sentence is pre-filled; Enter on a menu opens it, arrows move, Escape closes
        page.goto(base + "/")
        sentence = page.locator(".goal-sentence")
        assert sentence.inner_text().startswith("I would like to expand internationally ▾ to expand my market presence ▾")
        why = page.get_by_role("button", name=re.compile("^Why:"))
        why.focus()
        page.keyboard.press("Enter")
        expect(page.get_by_role("listbox", name="Why")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.get_by_role("listbox", name="Why")).to_have_count(0)
        assert page.evaluate("document.activeElement.getAttribute('aria-label')").startswith("Why:")
        page.keyboard.press("Enter")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        assert "find stores and stockists to carry us" in sentence.inner_text()
        page.screenshot(path=SHOTS / "s01-goal.png", full_page=True)
        page.get_by_label("Your brand's website").fill("http://tidewell.example/")
        page.keyboard.press("Enter")

        # the brief: where you already are (with coverage) and who likes you (with the caveat)
        page.get_by_text("Matched automatically").wait_for(timeout=30000)
        page.get_by_role("heading", name="Where you already are").wait_for(timeout=10000)
        page.get_by_text("All 5 Tidewell stores Qloo lists").wait_for(timeout=10000)
        pres = page.locator(".presence").first.inner_text()
        assert "Amsterdam" in pres and "QLOO STORES" in pres and "left out of the ranking" in pres
        aud = page.locator(".audience").first
        expect(aud).to_contain_text("a cultural relationship, not your customer data or audience size")
        page.get_by_role("button", name="Remove the audience Surfing").click()
        expect(page.locator(".audience").first).to_contain_text("You removed: Surfing")
        page.screenshot(path=SHOTS / "brief-presence-audience.png", full_page=True)

        page.get_by_role("button", name="Start exploring markets").click()
        page.wait_for_url(re.compile(r"/markets/scan$"), timeout=10000)
        page.get_by_text(re.compile(r"Qloo ranked your \d+ candidate cities in one query for people who like Tidewell")).wait_for(timeout=60000)
        page.get_by_role("button", name="See the recommendations", exact=True).wait_for(timeout=60000)
        assert page.locator(".map-dot").count() == 80
        page.screenshot(path=SHOTS / "s04-scan-ranking.png", full_page=True)
        page.get_by_role("button", name="See the recommendations", exact=True).click()

        # S05
        page.get_by_role("heading", name="6 cities recommended for research").wait_for(timeout=10000)
        main = page.locator("main").inner_text()
        for text in ("RESEARCH PRIORITY", "SIGNALS WE USED", "WHY THIS ORDER?", "WHAT SETS THEM APART", "STILL TO ESTABLISH",
                     "TOP RECOMMENDATION", "INVESTIGATE FIRST", "your purpose (stockists) breaks near-ties", "COULD CHANGE IT"):
            assert text in main, text
        lisbon = page.get_by_role("article", name="Lisbon")
        assert "In one Qloo ranking of your" in lisbon.inner_text() and "came 4th for people who like Tidewell" in lisbon.inner_text()
        assert "Moved up for your goal (Qloo's position: 4th)" in lisbon.inner_text() and "TOP RECOMMENDATION" in lisbon.inner_text()
        assert "Surfing" not in page.locator(".panels").inner_text()  # the removed audience isn't used
        assert page.get_by_role("button", name="Research all 6 recommended cities").is_disabled()
        page.screenshot(path=SHOTS / "s05-recommendations.png", full_page=True)
        trail = lisbon.get_by_role("button", name="Why this city?")
        trail.click()
        assert trail.get_attribute("aria-expanded") == "true" and "1 · QLOO'S RANKING" in lisbon.locator(".trail").inner_text()
        lisbon.locator(".trail").get_by_role("button", name=re.compile("QLOO")).first.click()
        drawer = page.get_by_role("dialog", name="QLOO PLACE")
        expect(drawer).to_contain_text("A cultural relationship, not demand")
        page.keyboard.press("Escape")
        expect(drawer).to_have_count(0)

        page.get_by_label("Research Paris").uncheck()
        page.get_by_text("Paris removed from research").wait_for(timeout=10000)
        page.get_by_role("button", name="Research 5 of 6 recommended cities").wait_for(timeout=10000)

        # All 80: the new chips and a founder addition that never becomes a recommendation
        page.get_by_role("link", name="See all 80 cities").first.click()
        page.get_by_role("heading", name="Every city we scanned").wait_for()
        rows = {n: page.locator(".city-rows li", has_text=n).first.inner_text() for n in ("Amsterdam", "Seattle", "Wellington")}
        assert "YOU'RE ALREADY HERE" in rows["Amsterdam"] and "OUTSIDE YOUR GOAL" in rows["Seattle"]
        assert "NOT RANKED BY QLOO" in rows["Wellington"] and "has no destination" in rows["Wellington"]
        page.get_by_role("button", name="Already yours").click()
        assert page.locator(".city-rows li").count() == 5
        page.get_by_role("button", name="All 80").click()
        page.get_by_label("Search 80 cities").fill("welling")
        page.get_by_role("button", name="Research Wellington too").click()
        page.get_by_text("Wellington added to the research").wait_for(timeout=10000)
        page.get_by_label("Search 80 cities").fill("")
        page.screenshot(path=SHOTS / "s06-all80.png", full_page=True)
        page.reload()
        page.get_by_role("heading", name="Every city we scanned").wait_for()
        page.get_by_role("link", name="Back to the recommendations").click()
        page.get_by_role("heading", name="6 cities recommended for research").wait_for()
        assert page.get_by_label("Research Paris").is_checked() is False  # refresh keeps the untick
        assert "ADDED BY YOU · 1" in page.locator("main").inner_text()

        # a purpose change: a notice first, then an explicit Update with no new Qloo searches
        page.locator(".goal-bar").get_by_role("button", name=re.compile("^Why:")).click()
        page.get_by_role("option", name="expand my market presence").click()
        update = page.get_by_role("button", name="Update the recommendations · no new Qloo searches")
        update.wait_for(timeout=10000)
        assert "your purpose changed" in page.locator(".notice-changed").first.inner_text()
        update.click()
        expect(page.locator("article.city-card").first).to_have_attribute("aria-label", "Paris", timeout=20000)
        assert page.get_by_label("Research Paris").is_checked() is False  # the founder's untick is carried over

        for path, name in (("/", "s01"), (f"/p/{page.url.split('/p/')[1].split('/')[0]}", "brief"),
                           (page.url.split(base)[1], "s05"), (page.url.split(base)[1] + "/all", "s06")):
            m = ctx.new_page()
            m.set_viewport_size({"width": 390, "height": 844})
            m.goto(base + path)
            m.locator("main h1, h1").first.wait_for()
            m.wait_for_timeout(600)
            m.screenshot(path=SHOTS / f"{name}-390.png", full_page=True)
            assert no_overflow(m), name
            if name == "s01":  # a native select replaces the menu at 390 px
                assert m.locator("select.goal-select").first.is_visible() and not m.locator("button.goal-menu").first.is_visible()
            m.close()
        assert not errors, errors
        browser.close()


def test_browser_a_failed_ranking_shows_the_no_order_state_and_retry_recovers(server):
    from playwright.sync_api import sync_playwright
    fake = FakeQloo()
    fake.rank_fail = {1}
    base = server(fake=fake)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        to_recommendations(page, base)
        page.get_by_role("button", name="See the recommendations", exact=True).click()
        page.get_by_role("heading", name="No order to recommend from").wait_for(timeout=10000)
        assert "Qloo's ranking failed" in page.locator("main").inner_text()
        page.screenshot(path=SHOTS / "s05-ranking-failed.png", full_page=True)
        fake.rank_fail = set()
        page.get_by_role("button", name="Retry ranking · 1–2 Qloo requests").click()
        page.get_by_role("heading", name="6 cities recommended for research").wait_for(timeout=20000)
        browser.close()


@pytest.mark.parametrize("stores, home", [(US[:10], 1), (US, 0)])
def test_browser_both_with_a_small_and_an_empty_home_group(server, stores, home):
    from playwright.sync_api import sync_playwright
    fake = FakeQloo()
    fake.stores["Tidewell"] = stores
    base = server(fake=fake)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        to_recommendations(page, base, scope="both at home and abroad")
        page.get_by_role("button", name="See the recommendations", exact=True).click()
        page.get_by_text(f"AT HOME · {home} OF UP TO 2").wait_for(timeout=10000)
        main = page.locator("main").inner_text()
        assert "ABROAD · 3 OF UP TO 3" in main and main.count("TOP RECOMMENDATION") >= (2 if home else 1)
        if home:
            assert "Only 1 city was ranked here" in main and "isn't padded to 2" in main
        else:
            assert "No city at home came back ranked" in main and "Nothing is moved over from the other group" in main
        page.screenshot(path=SHOTS / f"s05-both-home-{home}.png", full_page=True)
        browser.close()


def test_browser_stand_in_swap_then_update(server, monkeypatch):
    from playwright.sync_api import expect, sync_playwright
    fake = FakeQloo()
    fake.brand = None

    def propose(run, brief, facts):
        db.x("INSERT OR IGNORE INTO qloo_refs VALUES(?,?,?,?,?)", run.project_id, "urn:tag:keyword:qloo:ocean", "tag", "Ocean",
             "urn:tag:keyword:qloo")
        return {"signals": [{"qloo_id": "urn:tag:keyword:qloo:ocean", "kind": "tag", "name": "Ocean", "basis_facts": [1],
                             "reasoning": "SYNTHETIC"}], "searches": {"tags": 1, "brands": 0}}
    monkeypatch.setattr(identity, "_propose", propose)
    base = server(fake=fake)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        enter(page, base)
        page.get_by_role("button", name=re.compile("Confirm")).first.click(timeout=40000)
        page.get_by_text("Brands with a similar following: Vissla · Outerknown · Twothirds").wait_for(timeout=20000)
        assert "used as a stand-in for you" in page.locator(".audience").first.inner_text()
        page.get_by_role("button", name="Start exploring markets").click()
        page.get_by_role("button", name="See the recommendations", exact=True).wait_for(timeout=60000)
        page.get_by_role("button", name="See the recommendations", exact=True).click()
        page.get_by_role("heading", name=re.compile("cities recommended for research")).wait_for(timeout=10000)
        assert "for people who like Vissla, Outerknown and Twothirds (stand-ins)" in page.locator("article.city-card").first.inner_text()
        si = page.locator(".standins").first
        si.locator("li", has_text="Twothirds").get_by_role("button", name="Remove").click()
        si.get_by_label("Add a brand with a similar following").fill("OluK")
        si.get_by_role("button", name="Search Qloo").click()
        si.get_by_role("button", name="Add", exact=True).click()
        update = page.get_by_role("button", name="Update the recommendations · re-ranks with 1–2 Qloo requests, no place scan")
        update.wait_for(timeout=10000)
        update.click()
        expect(page.locator("article.city-card").first).to_contain_text("Vissla, Outerknown and OluKai (stand-ins)", timeout=20000)
        page.screenshot(path=SHOTS / "s05-standins-updated.png", full_page=True)
        browser.close()


def test_browser_b07_unresolved_add_and_refresh(server):
    from playwright.sync_api import sync_playwright
    base = server(fake=Unresolving(["lagos", "riyadh", "accra"]))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        to_recommendations(page, base)
        assert "3 unresolved" in page.locator(".coverage").inner_text()
        page.get_by_role("button", name="See the recommendations", exact=True).click()
        page.get_by_role("link", name="See all 80 cities").first.click()
        page.get_by_role("heading", name="Every city we scanned").wait_for()
        page.get_by_role("button", name="Unresolved", exact=True).click()
        assert "3 cities Qloo couldn't search" in page.locator(".unresolved-panel").inner_text()
        assert page.locator(".city-rows li").count() == 3
        page.screenshot(path=SHOTS / "b07-unresolved.png", full_page=True)
        page.get_by_role("button", name="Research Lagos too").click()
        page.get_by_text("Lagos added to the research").wait_for(timeout=10000)
        page.reload()
        page.get_by_role("heading", name="Every city we scanned").wait_for()
        lagos = page.locator(".city-rows li", has_text="Lagos")
        assert "ADDED BY YOU" in lagos.inner_text() and "in research" in lagos.inner_text()
        browser.close()


def test_browser_b08_map_geometry_blocked(server):
    from playwright.sync_api import sync_playwright
    base = server()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        to_recommendations(page, base, block_map=True)
        assert "Map outline unavailable · positions approximate" in page.locator("main").inner_text()
        assert page.locator("svg .map-land").count() == 0 and page.locator(".map-dot").count() == 80
        browser.close()


def test_browser_a_stopped_scan_shows_why_and_retry_recovers(server, monkeypatch):
    """Review slice 3, blocker 1: S04 shows the stopped scan and a way forward."""
    from playwright.sync_api import sync_playwright
    base = server()
    monkeypatch.setattr(config, "LIVE_STAGES", tuple(x for x in LIVE if x != "scan"))  # SYNTHETIC: no scan recording
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        enter(page, base)
        page.get_by_text("Matched automatically").wait_for(timeout=30000)
        page.get_by_role("button", name="Start exploring markets").click()
        page.get_by_text("The scan stopped").wait_for(timeout=15000)
        assert page.get_by_role("button", name=re.compile("See the recommendations")).count() == 0
        monkeypatch.setattr(config, "LIVE_STAGES", LIVE)
        page.get_by_role("button", name="Retry failed cities").click()
        page.get_by_role("button", name="See the recommendations", exact=True).wait_for(timeout=60000)
        assert "80 returned" in page.locator(".coverage").inner_text()
        browser.close()
