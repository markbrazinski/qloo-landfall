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
    """S01: the URL and, optionally, the goal by mouse (v4: a sentence part opens a row of chips); Enter starts."""
    page.goto(base + "/")
    if scope:
        page.get_by_role("button", name=re.compile("^Where:")).click()
        page.get_by_role("button", name=scope, exact=True).click()
    if purpose:
        page.get_by_role("button", name=re.compile("^Why:")).click()
        page.get_by_role("button", name=purpose, exact=True).click()
    page.get_by_label("Your brand's website").fill("http://tidewell.example/")
    page.keyboard.press("Enter")


BRIEF = re.compile("Here's what we understand")


def open_brief(page, timeout=60000):
    """v4 02 → 03: a founder who watched the reading opens the brief with "See the brief"; a later visit shows it at once."""
    see = page.get_by_role("button", name="See the brief")
    see.or_(page.get_by_role("heading", name=BRIEF)).first.wait_for(timeout=timeout)
    if see.count():
        see.click()
    page.get_by_role("heading", name=BRIEF).wait_for(timeout=10000)


def explore(page, timeout=60000):
    """Brief → Explore markets → S04 scan → its finished banner's "See recommended markets" → S05."""
    page.get_by_role("button", name="Explore markets").click()
    page.get_by_role("button", name="See recommended markets").click(timeout=timeout)


def to_recommendations(page, base, block_map=False, **goal):
    """URL entry → brief → Explore markets → S04 → banner (not clicked)."""
    if block_map:
        page.route(re.compile(r".*countries-110m.*\.json"), lambda route: route.abort())
    enter(page, base, **goal)
    open_brief(page, timeout=30000)
    page.get_by_text("matched automatically").wait_for(timeout=30000)
    page.get_by_role("button", name="Explore markets").click()
    page.wait_for_url(re.compile(r"/markets/scan$"), timeout=10000)
    page.get_by_role("heading", name=re.compile("Looking at 80 cities")).wait_for(timeout=10000)
    try:
        page.get_by_role("button", name="See recommended markets").wait_for(timeout=60000)
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

        # S01 by keyboard (v4 LF-IntakeSentence): the sentence is pre-filled; Enter on a part opens its chips, Enter again
        # closes them, and Tab reaches the chips in order
        page.goto(base + "/")
        sentence = page.locator(".lfb-sentence")
        assert sentence.inner_text().startswith("I'd like to expand internationally ▾ to test demand in a new market ▾")
        why = page.get_by_role("button", name=re.compile("^Why:"))
        why.focus()
        page.keyboard.press("Enter")
        expect(page.get_by_role("group", name="What you want to learn")).to_be_visible()
        assert why.get_attribute("aria-expanded") == "true"
        page.keyboard.press("Enter")
        expect(page.get_by_role("group", name="What you want to learn")).to_have_count(0)
        assert page.evaluate("document.activeElement.getAttribute('aria-label')").startswith("Why:")
        page.keyboard.press("Enter")
        for _ in range(3):  # Budget, then the chips: Test demand, Find retail partners
            page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.textContent") == "Find retail partners"
        page.keyboard.press("Enter")
        assert "find retail partners" in sentence.inner_text()
        page.screenshot(path=SHOTS / "s01-goal.png", full_page=True)
        page.get_by_label("Your brand's website").fill("http://tidewell.example/")
        page.keyboard.press("Enter")

        # the brief: where you already sell (with coverage) and who likes brands like yours (with the caveat)
        open_brief(page, timeout=30000)
        page.get_by_text("matched automatically").wait_for(timeout=30000)
        page.get_by_role("button", name=re.compile("^Where you already sell")).click()
        page.get_by_text("All 5 Tidewell stores Qloo lists").wait_for(timeout=10000)
        pres = page.locator(".presence").first.inner_text()
        assert "Amsterdam" in pres and "QLOO STORES" in pres and "left out of the ranking" in pres
        aud = page.get_by_role("region", name="Who likes brands like yours")
        expect(aud).to_contain_text("Surfing")
        expect(aud).to_contain_text("not your customer data")
        expect(aud).to_contain_text("It isn’t an audience size.")
        # v4 03 dropped the per-audience Remove control (and so the check that a removed audience isn't used downstream)
        page.screenshot(path=SHOTS / "brief-presence-audience.png", full_page=True)

        page.get_by_role("button", name="Explore markets").click()
        page.wait_for_url(re.compile(r"/markets/scan$"), timeout=10000)
        page.get_by_role("button", name="See recommended markets").wait_for(timeout=60000)
        assert page.locator(".map-dot").count() == 80
        counts = page.get_by_role("list", name="Scan outcomes").inner_text()
        assert sum(int(n) for n in re.findall(r"\d+", counts)) == 80, counts  # v4 04: five outcomes that add to 80
        page.screenshot(path=SHOTS / "s04-scan-ranking.png", full_page=True)
        page.get_by_role("button", name="See recommended markets").click()

        # S05 (v4 05: one recommended, two alternatives, the rest on request; Select boxes and one primary)
        pid = page.url.split("/p/")[1].split("/")[0]
        page.get_by_role("heading", name="Lisbon looks like the strongest first city for Tidewell").wait_for(timeout=10000)
        assert page.locator("article.mk-card").count() == 3
        page.get_by_role("button", name=re.compile(r"^\d+ more shortlisted")).click()
        assert page.locator("article.mk-card").count() == 6
        main = page.locator("main").inner_text()
        for text in ("RECOMMENDED", "ALTERNATIVE", "Fits because", "What we found", "What informed this",
                     "how many shops Qloo returned in each city to break near-ties"):  # your purpose (stockists) breaks near-ties
            assert text in main, text
        assert re.search(r"Qloo’s ranking of your \d+ candidate cities for the audience who would like Tidewell", main)
        lisbon = page.get_by_role("article", name="Lisbon")
        assert "RECOMMENDED" in lisbon.inner_text()
        assert re.search(r"Qloo ranks it #4 of \d+ candidate cities for the audience who would like Tidewell; moved for your goal", lisbon.inner_text())
        assert "Local evidence" not in main and "Main uncertainty" not in main  # owner, 28 Oct 2026: Qloo's places are not partners
        assert re.fullmatch(r"Qloo ranks it #4 of \d+ candidate cities for the audience who would like Tidewell; moved for your goal\.",
                            page.locator(".decision-bar .subtitle").inner_text())
        assert page.locator(".decision-bar .btn-primary").inner_text() == "Build a plan for Lisbon"  # the plan, not N-city research
        page.screenshot(path=SHOTS / "s05-recommendations.png", full_page=True)
        lisbon.get_by_role("button", name="Why Lisbon").click()
        why = page.get_by_role("dialog", name=re.compile("^CITY"))
        expect(why).to_contain_text("WHAT WE FOUND")
        expect(why).to_contain_text("COVERAGE")
        page.keyboard.press("Escape")
        expect(why).to_have_count(0)

        page.get_by_role("checkbox", name="Select Paris").check()
        expect(page.locator(".decision-bar .btn-primary")).to_have_text("Build a plan for Paris")
        page.reload()  # the selection is in the URL, so a refresh keeps it
        expect(page.get_by_role("checkbox", name="Select Paris")).to_be_checked(timeout=10000)

        # All 80: the result labels, the result filter, and a founder's city that never becomes a recommendation
        page.get_by_role("link", name="Explore all 80 cities").first.click()
        page.get_by_role("heading", name="All 80 cities").wait_for()
        search = page.get_by_label("Search cities")

        def row(name):
            search.fill(name)
            return page.locator(".city-rows li", has_text=name).first.inner_text()
        assert "Already sell here" in row("Amsterdam") and "Outside your goal" in row("Seattle")
        assert "has no destination" in row("Wellington")  # NOT_RANKED's line; its row label is its result bucket
        search.fill("")
        page.get_by_label("Result").select_option("here")
        assert page.locator(".city-rows li").count() == 5
        page.get_by_label("Result").select_option("")
        page.screenshot(path=SHOTS / "s06-all80.png", full_page=True)
        page.reload()
        page.get_by_role("heading", name="All 80 cities").wait_for()
        search.fill("welling")
        page.locator(".city-rows li", has_text="Wellington").get_by_role("button").click()
        with page.expect_response(lambda r: r.url.endswith("/shortlist") and r.request.method == "PATCH"):  # saves behind the plan page
            page.get_by_role("dialog", name=re.compile("^CITY")).get_by_role("button", name="Build a plan for Wellington").click()
        page.wait_for_url(re.compile(r"/plan/wellington$"), timeout=10000)
        page.goto(f"{base}/p/{pid}/markets")
        page.get_by_role("heading", name="Lisbon looks like the strongest first city for Tidewell").wait_for()
        expect(page.get_by_role("article", name="Wellington")).to_contain_text("ADDED BY YOU")  # never a recommendation

        # a purpose change (the context strip's Edit): a notice first, then an explicit Update with no new Qloo searches
        page.locator(".context-strip").get_by_role("button", name="Edit").click()
        goal = page.get_by_role("dialog", name="YOUR GOAL")
        goal.get_by_role("button", name=re.compile("^Why:")).click()
        goal.get_by_role("button", name="Build awareness", exact=True).click()
        expect(goal.get_by_role("status")).to_contain_text("Goal v2. Your recommendations are unchanged until you update", timeout=10000)
        page.keyboard.press("Escape")
        update = page.get_by_role("button", name="Update the recommendations · no new Qloo searches")
        update.wait_for(timeout=10000)
        assert "your purpose changed" in page.locator(".notice-changed").first.inner_text()
        update.click()
        expect(page.locator("article.mk-card").first).to_have_attribute("aria-label", "Paris", timeout=20000)
        expect(page.get_by_role("article", name="Wellington")).to_contain_text("ADDED BY YOU")  # the founder's city is carried over
        assert db.latest("shortlist", pid)["beachhead"]["city_id"] == "wellington"

        for path, name in (("/", "s01"), (f"/p/{pid}", "brief"), (f"/p/{pid}/markets", "s05"), (f"/p/{pid}/markets/all", "s06")):
            m = ctx.new_page()
            m.set_viewport_size({"width": 390, "height": 844})
            m.goto(base + path)
            m.locator("main h1, h1").first.wait_for()
            m.wait_for_timeout(600)
            m.screenshot(path=SHOTS / f"{name}-390.png", full_page=True)
            assert no_overflow(m), name
            m.close()  # v4's sentence chips work at every width: no native select at 390 px any more
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
        page.get_by_role("button", name="See recommended markets").click()
        page.get_by_role("heading", name="No order to recommend from").wait_for(timeout=10000)
        assert "Qloo's ranking failed" in page.locator("main").inner_text()
        page.screenshot(path=SHOTS / "s05-ranking-failed.png", full_page=True)
        fake.rank_fail = set()
        page.get_by_role("button", name="Retry ranking · 1–2 Qloo requests").click()
        page.get_by_role("heading", name=re.compile("^Toronto looks like the strongest first city")).wait_for(timeout=20000)
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
        to_recommendations(page, base, scope="Both")
        page.get_by_role("button", name="See recommended markets").click()
        page.get_by_role("heading", name="AT HOME", exact=True).wait_for(timeout=10000)
        main = page.locator("main").inner_text()
        assert "ABROAD" in main and main.count("RECOMMENDED") >= 1
        assert page.locator("article.mk-card").count() == home + 3  # each group shows up to 3; nothing padded
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
        open_brief(page)
        page.get_by_text("How we’ll find your audience").wait_for(timeout=40000)  # v4 03: no Qloo match; Explore confirms the profile
        page.get_by_role("button", name="Explore markets").click()
        page.get_by_role("button", name="See recommended markets").click(timeout=60000)
        page.get_by_role("heading", name=re.compile("looks like the strongest first city")).wait_for(timeout=10000)
        assert "for the audience who would like Vissla, Outerknown and Twothirds (brands like yours)" in page.locator("article.mk-card").first.inner_text()
        # v4 moved the stand-ins editor off Markets: the brief's Qloo card → Adjust → Search Qloo for more
        page.goto(page.url.split("/markets")[0])
        page.get_by_text("a following like Vissla and Outerknown").wait_for(timeout=20000)
        page.get_by_role("button", name="Adjust").click()
        page.get_by_role("button", name="Search Qloo for more").click()
        si = page.get_by_role("dialog", name="DISCOVERY PROFILE").locator(".standins")
        si.locator("li", has_text="Twothirds").get_by_role("button", name="Remove").click()
        si.get_by_label("Add a brand with a similar following").fill("OluK")
        si.get_by_role("button", name="Search Qloo").click()
        si.get_by_role("button", name="Add", exact=True).click()
        page.keyboard.press("Escape")
        page.get_by_role("button", name="Explore markets").click()  # an outdated recommendation opens Markets, not a new scan
        update = page.get_by_role("button", name="Update the recommendations · re-ranks with 1–2 Qloo requests, no place scan")
        update.wait_for(timeout=10000)
        update.click()
        expect(page.locator("article.mk-card").first).to_contain_text("Vissla, Outerknown and OluKai (brands like yours)", timeout=20000)
        page.screenshot(path=SHOTS / "s05-standins-updated.png", full_page=True)
        browser.close()


def test_browser_b07_unresolved_add_and_refresh(server):
    from playwright.sync_api import sync_playwright
    base = server(fake=Unresolving(["lagos", "riyadh", "accra"]))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        to_recommendations(page, base)
        assert "3Couldn’t match" in page.get_by_role("list", name="Scan outcomes").inner_text().replace("\n", "")
        page.get_by_role("button", name="See recommended markets").click()
        page.get_by_role("link", name="Explore all 80 cities").first.click()
        page.get_by_role("heading", name="All 80 cities").wait_for()
        assert "3 couldn’t match" in page.locator(".decision-bar .subtitle").inner_text()
        page.get_by_label("Result").select_option("unresolved")
        assert page.locator(".city-rows li").count() == 3
        page.screenshot(path=SHOTS / "b07-unresolved.png", full_page=True)
        page.reload()  # the filter is in the URL
        page.get_by_role("heading", name="All 80 cities").wait_for()
        assert page.locator(".city-rows li").count() == 3
        page.locator(".city-rows li", has_text="Lagos").get_by_role("button").click()
        city = page.get_by_role("dialog", name=re.compile("^CITY"))
        assert "Qloo couldn't match this city area" in city.inner_text()  # unknown, not rejected: no Build action
        assert city.get_by_role("button", name=re.compile("Build a plan")).count() == 0
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
        open_brief(page, timeout=30000)
        page.get_by_text("matched automatically").wait_for(timeout=30000)
        page.get_by_role("button", name="Explore markets").click()
        page.get_by_text("The scan stopped").wait_for(timeout=15000)
        assert page.get_by_role("button", name="See recommended markets").count() == 0
        monkeypatch.setattr(config, "LIVE_STAGES", LIVE)
        page.get_by_role("button", name="Retry failed cities").click()
        page.get_by_role("button", name="See recommended markets").wait_for(timeout=60000)
        assert "80Cities with leads" in page.get_by_role("list", name="Scan outcomes").inner_text().replace("\n", "")
        browser.close()


def test_browser_markets_start_and_move_on_by_themselves(server, monkeypatch):
    """Owner, 28 Oct 2026: the scan starts when the brief is done; Explore only navigates; the scan view moves on to the
    recommendation by itself once its 8 s have played and the recommendation is written."""
    from playwright.sync_api import sync_playwright

    from landfall import autoplan
    monkeypatch.setattr(config, "AUTO_PLAN", True)
    monkeypatch.setattr(autoplan, "prewarm", lambda *a: None)  # the deep dives are autoplan's tests
    base = server()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        enter(page, base)
        open_brief(page, timeout=30000)
        pid = page.url.split("/p/")[1].split("/")[0]
        end = time.time() + 30
        while not scan.generations(pid):  # nobody clicked anything
            assert time.time() < end, "the scan didn't start by itself"
            time.sleep(0.1)
        page.get_by_role("button", name="Explore markets").click()
        page.wait_for_url(re.compile(r"/markets/scan$"), timeout=10000)
        page.get_by_role("heading", name=re.compile("Looking at 80 cities")).wait_for(timeout=10000)
        page.wait_for_url(re.compile(r"/markets$"), timeout=60000)  # no click on "See recommended markets"
        page.get_by_role("heading", name=re.compile("looks like the strongest first city for Tidewell")).wait_for(timeout=10000)
        page.screenshot(path=SHOTS / "auto-s05.png", full_page=True)
        page.goto(f"{base}/p/{pid}/markets/scan")  # coming back to the scan stays there: it moved on once
        page.get_by_role("button", name="See recommended markets").wait_for(timeout=20000)
        time.sleep(1)
        assert page.url.endswith("/markets/scan")
        browser.close()
