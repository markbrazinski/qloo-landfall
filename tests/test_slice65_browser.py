"""Slice 6.5 browser checks (BUILD-PLAN "Slice 6.5" UI.4, UI.9) with SYNTHETIC Qloo and a SYNTHETIC strategist.

Increment A: the intake budget, the four-stage bar with its locks, the context strip and its Edit drawer, the
recommendation with Select boxes and the one primary, Compare, the all-80 browser (totals, search, region, result, pages
in the URL), the city drawer and choosing a beachhead. Screenshots: .landfall/browser-slice65/ (git-ignored).
"""
import re

import pytest
from test_markets_browser import WEB_BUILT, explore, no_overflow, open_brief, server  # noqa: F401  (fixture re-export)

from landfall import config, db

SHOTS = config.ROOT / ".landfall" / "browser-slice65"
pytestmark = pytest.mark.skipif(not WEB_BUILT, reason="frontend not built (make web)")



def research_city(page, base, pid, city="toronto"):
    """Owner, 27 Oct 2026: the Plan page builds the plan by itself (off in tests); these journeys drive each step, so the
    city's research starts through the API, then the page reloads with it done."""
    import time
    assert page.request.post(f"{base}/api/projects/{pid}/research", data={"city_ids": [city]}).ok
    for _ in range(600):
        if page.request.get(f"{base}/api/projects/{pid}/plans/{city}").json()["research"]["status"] in ("complete", "partial"):
            break
        time.sleep(0.1)
    page.reload()


def add_to_plan(page, label, main=False):
    """v4 B04: Add to plan previews the change; nothing is written until "Add {label}"."""
    from playwright.sync_api import expect
    page.get_by_role("article", name=label).get_by_role("button", name="Add to plan").click()
    dialog = page.get_by_role("dialog", name=f"Add {label} to the plan?")
    if main:
        expect(dialog).to_contain_text(f"{label} becomes the main test")
    dialog.get_by_role("button", name=f"Add {label}").click()
    expect(dialog).to_have_count(0, timeout=10000)
    expect(page.get_by_role("article", name=label)).to_contain_text("Main test" if main else "Supports the main test")


def prepare_plan(page):
    """D2: the one primary drafts any method without a current draft first, then (clicked again) signs off and opens Prepare."""
    from playwright.sync_api import expect
    primary = page.locator(".decision-bar .btn-primary", has_text="Prepare this plan")
    primary.click(timeout=60000)
    drafting = page.get_by_text(re.compile("^Landfall is drafting .* before this plan can be prepared"))
    for _ in range(300):  # either it signs off at once (every draft current) or it drafts first
        if "/launch/" in page.url or "/prepare/" in page.url:
            return
        if drafting.count():
            break
        page.wait_for_timeout(100)
    expect(drafting).to_have_count(0, timeout=60000)
    primary.click(timeout=60000)


def test_browser_increment_a_budget_markets_compare_all80_beachhead(server):
    from playwright.sync_api import expect, sync_playwright
    base = server()
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # B1: the budget is part of the sentence; a chip choice is the founder's
        page.goto(base + "/")
        sentence = page.locator(".lfb-sentence")
        assert "with a starting budget of US$10,000" in sentence.inner_text()
        page.get_by_role("button", name=re.compile("^Budget:")).click()
        expect(page.get_by_text("Your ceiling for the first launch test, not a spending commitment.")).to_be_visible()
        page.get_by_role("button", name="US$25,000", exact=True).click()
        assert "US$25,000" in sentence.inner_text()
        page.screenshot(path=SHOTS / "01-intake-budget.png", full_page=True)
        page.get_by_label("Your brand's website").fill("http://tidewell.example/")
        page.keyboard.press("Enter")
        open_brief(page, timeout=30000)
        page.get_by_text("matched automatically").wait_for(timeout=30000)
        pid = page.url.split("/p/")[1].split("/")[0]
        fi = db.latest("founder_input", pid)
        assert (fi["ceiling_usd"], fi["ceiling_by"]) == (25000, "YOU_SAID")

        # SH1: Plan and Launch are locked until a beachhead exists (owner, 28 Oct 2026: Prepare and Act & adapt are one Launch)
        nav = page.get_by_role("navigation", name="Stages")
        assert [t.strip() for t in nav.locator(".stage-label").all_inner_texts()] == ["Brand", "Markets", "Plan", "Launch"]
        nav.get_by_role("button", name=re.compile("Plan")).click()
        expect(nav).to_contain_text("Choose a beachhead city in Markets first")

        explore(page)

        # SH2: the context strip, and a ceiling edit that re-runs nothing
        strip = page.locator(".context-strip")
        expect(strip).to_contain_text("International · Test demand · Up to US$25,000")
        runs = db.q("SELECT COUNT(*) n FROM runs WHERE project_id=?", pid)[0]["n"]
        edit = strip.get_by_role("button", name="Edit")
        edit.click()
        drawer = page.get_by_role("dialog", name="YOUR GOAL")
        drawer.get_by_role("button", name=re.compile("^Budget:")).click()
        drawer.get_by_role("button", name="US$5,000", exact=True).click()
        expect(drawer).to_contain_text("nothing re-runs")
        for _ in range(12):  # SH4: Tab stays inside the drawer
            page.keyboard.press("Tab")
            assert page.evaluate("!!document.activeElement.closest('[role=dialog]')")
        page.keyboard.press("Escape")
        expect(drawer).to_have_count(0)
        page.wait_for_timeout(200)
        assert page.evaluate("document.activeElement.textContent") == "Edit", page.evaluate("document.activeElement.outerHTML.slice(0, 200)")  # focus returns to the opener
        expect(strip).to_contain_text("Up to US$5,000")
        assert db.latest("founder_input", pid)["ceiling_usd"] == 5000
        assert db.q("SELECT COUNT(*) n FROM runs WHERE project_id=?", pid)[0]["n"] == runs

        # M2: one recommended, two alternatives, three more on request; the primary follows the selection
        page.get_by_role("heading", name=re.compile("^Toronto looks like the strongest first city")).wait_for(timeout=10000)
        assert page.locator("article.mk-card").count() == 3
        page.get_by_role("button", name=re.compile("^3 more shortlisted")).click()
        assert page.locator("article.mk-card").count() == 6
        primary = page.locator(".decision-bar .btn-primary")
        expect(primary).to_have_text("Build a plan for Toronto")
        page.get_by_role("checkbox", name="Select Vancouver").check()
        expect(primary).to_have_text("Build a plan for Vancouver")
        page.get_by_role("checkbox", name="Select Montreal").check()
        expect(primary).to_have_text("Compare Vancouver, Montreal")
        expect(page.get_by_role("button", name="or build a plan for Toronto")).to_be_visible()
        page.get_by_role("checkbox", name="Select Calgary").check()
        assert page.get_by_role("checkbox", name="Select London").is_disabled()  # three at most
        page.get_by_role("checkbox", name="Select Calgary").uncheck()
        page.screenshot(path=SHOTS / "02-recommendation-selected.png", full_page=True)
        page.reload()
        expect(page.locator(".decision-bar .btn-primary")).to_have_text("Compare Vancouver, Montreal", timeout=10000)

        # M3: Compare, five criteria, no scores
        page.locator(".decision-bar .btn-primary").click()
        page.get_by_role("heading", name="Compare 2 cities for a first test").wait_for(timeout=10000)
        assert [t.strip() for t in page.locator("table.mk-compare thead th .mk-col-name").all_inner_texts()] == ["Vancouver", "Montreal"]
        assert [t.strip() for t in page.locator("table.mk-compare tbody th").all_inner_texts()] == [
            "Cultural and audience fit", "What we found", "Practical constraints", "Evidence coverage",
            "For your goal: test demand abroad"]
        assert not re.search(r"\bscore\b", page.locator("table.mk-compare").inner_text(), re.I)
        page.screenshot(path=SHOTS / "03-compare.png", full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert no_overflow(page)
        page.set_viewport_size({"width": 1440, "height": 900})
        page.get_by_role("link", name="← Back to Markets").click()
        expect(page.locator(".decision-bar .btn-primary")).to_have_text("Compare Vancouver, Montreal", timeout=10000)

        # M4: all 80, totals that sum to 80, filters and the page in the URL
        page.get_by_role("link", name="Explore all 80 cities").first.click()
        page.get_by_role("heading", name="All 80 cities").wait_for()
        totals = page.locator(".decision-bar .subtitle").inner_text()
        assert totals.startswith("80 cities = ")
        assert sum(int(n) for n in re.findall(r"(\d+) [a-z]", totals.split(" = ", 1)[1])) == 80, totals
        assert page.locator(".city-rows li").count() == 20
        page.get_by_role("button", name="Next ›").click()
        expect(page.locator(".mk-pager")).to_contain_text("21–40 of 80")
        page.reload()
        expect(page.locator(".mk-pager")).to_contain_text("21–40 of 80", timeout=10000)
        page.get_by_label("Search cities").fill("lisb")  # "lis" also matches Minneapolis
        expect(page.locator(".city-rows li")).to_have_count(1)
        page.get_by_label("Search cities").fill("")
        page.get_by_label("Region").select_option("Europe")
        assert all("Europe" in t for t in page.locator(".city-rows li").all_inner_texts())
        page.screenshot(path=SHOTS / "04-all-cities.png", full_page=True)

        # M5: the city drawer labels the city and builds its plan
        page.locator(".city-rows li", has_text="Lisbon").get_by_role("button").click()
        city = page.get_by_role("dialog", name=re.compile("^CITY · "))
        expect(city).to_contain_text("Lisbon")
        city.get_by_role("button", name="Build a plan for Lisbon").click()
        page.wait_for_url(re.compile(r"/plan/lisbon$"), timeout=10000)
        for _ in range(50):  # the plan opens first; the pick saves behind it
            bh = db.latest("shortlist", pid)["beachhead"]
            if bh and bh["city_id"] == "lisbon":
                break
            page.wait_for_timeout(100)
        assert bh["city_id"] == "lisbon" and bh["by"] == "founder"
        assert next(c for c in db.latest("shortlist", pid)["cities"] if c["city_id"] == "lisbon")["included"]

        # the beachhead shows in Markets, and the Plan stage opens
        page.goto(f"{base}/p/{pid}/markets")
        expect(page.get_by_role("article", name="Lisbon")).to_contain_text("ADDED BY YOU", timeout=10000)
        expect(page.locator(".context-strip")).to_contain_text("Beachhead: Lisbon")
        expect(page.get_by_role("navigation", name="Stages")).to_contain_text("Beachhead: Lisbon")
        expect(page.get_by_role("navigation", name="Stages").get_by_role("link", name=re.compile("Plan"))).to_be_visible()
        page.set_viewport_size({"width": 390, "height": 844})
        assert no_overflow(page)
        page.screenshot(path=SHOTS / "05-markets-390.png", full_page=True)
        assert errors == []
        browser.close()


def test_browser_plan_sign_off_act_recipient_template_mockup_and_removal(server, monkeypatch):
    """Increments B–D in the browser (AC1-shaped, SYNTHETIC providers): Build a plan for Toronto → research → Retail as
    the Main test → investigate → develop → sign off → Act: the launch kit, choose Kettle & Crane → the package →
    a template → back to Act; then remove Retail before signing again → the package sits under "Earlier plan"."""
    import base64

    from playwright.sync_api import expect, sync_playwright
    from test_markets import FakeQloo, Strategist
    from test_markets_browser import enter, explore, open_brief
    from test_slice4 import STAGES
    from test_slice6_browser import setup

    from landfall import mockups
    setup(monkeypatch)
    monkeypatch.setattr(mockups, "render_scene", lambda cas, prompt, seed: {
        "b64": base64.b64encode(b"\xff\xd8SYNTHETIC-scene\xff\xd9").decode(), "media_type": "image/jpeg"})
    base = server(FakeQloo(), Strategist())
    monkeypatch.setattr(config, "LIVE_STAGES", STAGES + ("specialist", "specialist_check", "specialist_places", "specialist_search",
                                                         "specialist_fetch", "products_fetch", "develop", "delivery_copy", "delivery_fetch"))
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        enter(page, base)
        open_brief(page)
        pid = page.url.split("/p/")[1].split("/")[0]
        explore(page, timeout=120000)
        page.locator(".decision-bar .btn-primary", has_text="Build a plan for Toronto").click(timeout=30000)
        page.wait_for_url(re.compile(r"/plan/toronto$"))

        # P1 → P2: research the city, add Retail (the first method added is the Main test), investigate it
        research_city(page, base, pid)
        retail = page.get_by_role("article", name="Retail")
        add_to_plan(page, "Retail", main=True)
        expect(page.locator(".decision-bar .btn-disabled, .decision-bar .btn-primary")).to_contain_text("Prepare this plan")
        retail.get_by_role("button", name=re.compile(r"^Investigate retail")).click()
        expect(retail).to_contain_text("Kettle & Crane", timeout=60000)
        page.screenshot(path=SHOTS / "10-plan-main.png", full_page=True)

        # P9 (D2): "Prepare this plan" drafts what is missing, then signs off the versions on screen and opens Prepare
        prepare_plan(page)
        page.wait_for_url(re.compile(r"/launch/toronto$"), timeout=30000)
        plan = db.get_record(f"plan_{pid}_toronto")
        pin = next(m for m in plan["methods"] if m["method"] == "retail")
        assert plan["signed_off"] and pin["draft"] and pin["dossier"]

        # Launch: the top contactable candidate's message is prepared in the background from the standard template;
        # the item shows exactly what goes out and ends in one sign-off (owner, 28 Oct 2026)
        item = page.locator(".launch-item").first
        expect(item).to_contain_text("Kettle & Crane", timeout=60000)
        expect(item.locator(".launch-message")).to_contain_text("Subject", timeout=60000)
        expect(page.locator("#launch-settings")).to_contain_text("Sender name")
        ids = {x["id"] for x in db.records("package", pid)}
        pkg = db.get_record(ids.pop())
        assert not ids and pkg["partner"]["name"] == "Kettle & Crane" and pkg["template_ref"]  # its own package, templated
        assert pkg["from_name"]  # the sender name is a launch setting, never a per-package blocker
        page.screenshot(path=SHOTS / "11-launch-checklist.png", full_page=True)

        # "See exactly what's sent" → the package review, and back
        item.get_by_role("link", name="See exactly what’s sent").click()
        page.wait_for_url(re.compile(r"/deliver/pkg_"))
        page.get_by_role("heading", name="Inquiry to Kettle & Crane").wait_for(timeout=60000)
        page.screenshot(path=SHOTS / "12-package-template.png", full_page=True)
        page.get_by_role("button", name="← Back").click()
        page.wait_for_url(re.compile(r"/launch/toronto$"))

        # AC4 shape: remove Retail before preparing again → its package is fenced at once
        page.goto(f"{base}/p/{pid}/plan/toronto")
        page.get_by_role("article", name="Retail").get_by_role("button", name="Remove").click()
        dialog = page.get_by_role("dialog", name="Remove Retail from the plan?")
        expect(dialog).to_contain_text('Its prepared packages move to "Earlier plan"')
        dialog.get_by_role("button", name="Remove Retail").click()
        expect(page.locator("main")).to_contain_text("changed since you prepared v", timeout=10000)
        page.goto(f"{base}/p/{pid}/prepare/toronto")  # the old address leads to Launch
        page.wait_for_url(re.compile(r"/launch/toronto$"))
        expect(page.locator("main")).to_contain_text("Earlier plan · 1", timeout=10000)
        page.get_by_text("Earlier plan · 1").click()
        page.locator("details a").first.click()
        expect(page.locator("main")).to_contain_text("EARLIER PLAN", timeout=10000)
        page.set_viewport_size({"width": 390, "height": 844})
        for path in (f"/p/{pid}/plan/toronto", f"/p/{pid}/launch/toronto", f"/p/{pid}/act/toronto"):
            page.goto(base + path)
            page.locator("main h1").first.wait_for()
            assert no_overflow(page), path
        page.screenshot(path=SHOTS / "13-launch-390.png", full_page=True)
        assert errors == []
        b.close()


def test_browser_creator_search_choose_find_contact_reaches_the_package(server, monkeypatch):
    """Slice 6.6 in the browser (SYNTHETIC provider and key; P66-I2, I5, I7): Creator as the Main test → sign off →
    Act → search Influencers.club → choose a creator → their own package → Find contact → the package's To is the
    sourced address, and a reload keeps it."""
    from datetime import date

    from playwright.sync_api import expect, sync_playwright
    from test_markets import FakeQloo, Strategist
    from test_markets_browser import enter, explore, open_brief
    from test_slice4 import STAGES
    from test_slice6_browser import setup
    from test_slice66 import KEY, Fake

    from landfall import fetch
    from landfall import influencers as ic
    setup(monkeypatch)
    import json

    from test_slice4 import FILLER, Web, html
    from test_slice45_browser import ARTICLE
    extra, inner = Web(), fetch._live_get  # the creator research pages slice 4.5's browser journey uses
    extra.pages[ARTICLE] = (200, html("Toronto style creators", "Maya Chen (@ossingtonstyle on TikTok) is a Toronto creator "
                                      "who reviews tea and sparkling drinks from small makers.", FILLER))
    extra.pages["https://www.tiktok.com/oembed?url=https%3A%2F%2Fwww.tiktok.com%2F%40ossingtonstyle"] = (200, json.dumps(
        {"author_name": "Maya Chen", "author_url": "https://www.tiktok.com/@ossingtonstyle"}))
    monkeypatch.setattr(fetch, "_live_get", lambda url, binary=False: extra(url) if url in extra.pages else inner(url, binary))
    for k in ("influencers_club_api_key", "influencer_api_key", "LANDFALL_ENV", "LANDFALL_IC_ADMISSION"):
        monkeypatch.delitem(config._ENV, k, raising=False)
    monkeypatch.setitem(config._ENV, "influencers_club_api_key", KEY)
    monkeypatch.setattr(ic, "TERMS_FROM", date(2020, 1, 1))
    fake = Fake()
    monkeypatch.setattr(ic, "transport", fake)
    base = server(FakeQloo(), Strategist())
    monkeypatch.setattr(config, "LIVE_STAGES", STAGES + ("specialist", "specialist_check", "specialist_places", "specialist_search",
                                                         "specialist_fetch", "products_fetch", "develop", "delivery_copy",
                                                         "delivery_fetch", "creator_profile", "creator_discovery"))
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        enter(page, base)
        open_brief(page)
        pid = page.url.split("/p/")[1].split("/")[0]
        explore(page, timeout=120000)
        page.locator(".decision-bar .btn-primary", has_text="Build a plan for Toronto").click(timeout=30000)
        page.wait_for_url(re.compile(r"/plan/toronto$"))
        research_city(page, base, pid)
        add_to_plan(page, "Creator", main=True)
        creator = page.get_by_role("article", name="Creator")
        creator.get_by_role("button", name=re.compile(r"^Investigate creator")).click()
        from test_markets import idle
        expect(creator.get_by_role("button", name=re.compile(r"^Investigate creator"))).to_have_count(0, timeout=10000)
        page.wait_for_timeout(500)
        idle(pid)
        d = db.get_record(f"dos_{pid}_toronto_creator")
        assert d and d["status"] in ("complete", "partial"), d and d["status"]
        page.reload()
        prepare_plan(page)
        page.wait_for_url(re.compile(r"/launch/toronto$"), timeout=30000)

        # Launch → Creator: one Influencers.club search runs in the background; picking a creator looks up their email
        # (once) and prepares their message from the standard template
        item = page.locator(".launch-item").first
        # research-found creators list first; the Influencers.club ones (with follower counts) follow, biggest first
        found = item.locator(".launch-creator", has_text="followers").first
        expect(found).to_contain_text("email looked up when you pick", timeout=60000)
        assert not [x for x in db.records("package", pid) if (x.get("partner") or {}).get("lead_key", "").startswith("creator|")]
        found.get_by_role("button", name="Pick").click()
        expect(item.locator(".launch-creator.on")).to_have_count(1, timeout=30000)
        idle(pid)  # the package is drafted in the background
        page.screenshot(path=SHOTS / "14-launch-creator-contact.png", full_page=True)
        pkgs = [db.get_record(i) for i in {x["id"] for x in db.records("package", pid)}]
        pkg = next((x for x in pkgs if x["recipients"]["to"] == ["creator@synthetic.example"]), None)  # the sourced address is the To
        assert pkg, [(x.get("partner"), x["recipients"]) for x in pkgs]
        assert (pkg.get("partner") or {}).get("lead_key", "").startswith("creator|")
        page.goto(f"{base}/p/{pid}/deliver/{pkg['id']}")
        page.get_by_role("heading", name=re.compile("^Inquiry to ")).wait_for(timeout=60000)
        expect(page.locator("main")).to_contain_text("creator@synthetic.example")
        page.reload()  # a reload keeps it
        expect(page.locator("main")).to_contain_text("creator@synthetic.example", timeout=10000)
        assert sum("/creators/enrich" in c[1] for c in fake.calls) == 1
        assert errors == []
        b.close()
