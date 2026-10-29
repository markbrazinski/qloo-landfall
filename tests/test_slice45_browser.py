"""Slice 4.5 browser check with SYNTHETIC providers (BUILD-PLAN §4.5.11 Browser).

S01 URL → research Toronto (note rules pop-ups out) → S08 card → Investigate retail → stockist comparison → partner drawer
→ back → Develop → draft → edit → reload; the pop-up page not run by the note; creator card → Add a creator profile;
community activities; the digital strip; keyboard and 390 px. Screenshots: .landfall/browser-slice45/ (git-ignored).
"""
import json
import re

import pytest
from test_markets import FakeQloo, Strategist
from test_markets_browser import enter, explore, open_brief, server  # noqa: F401
from test_slice4 import FILLER, KC, STAGES, Checker, Prose, Web, grounding, html, toronto_pages, toronto_script

from landfall import config, db, fetch, products, research, specialist

SHOTS = config.ROOT / ".landfall" / "browser-slice45"
pytestmark = pytest.mark.skipif(not (config.ROOT / "web" / "dist" / "index.html").exists(), reason="frontend not built")
KC_BRANDS = "https://kettlecrane.example/brands"
KC_SITE = "https://kettlecrane.example/"
RUNCLUB = "https://ossingtonrunners.example/"
ARTICLE = "https://torontolife.example/style-creators"
PRODUCT = "http://tidewell.example/products/hibiscus-berry"


def scripts():
    def retail(T):
        code = re.search(r"SOURCE (S\w+)", T["assortment"](url=KC_BRANDS)).group(1)
        T["check_presence"](lead_name="Kettle & Crane", url=KC_SITE)
        return {"concept_name": "Makers-shelf trial", "concept_line": "Four canned teas on a makers shelf.",
                "partnership_hypothesis": "Kettle & Crane already sells teas from small makers.",
                "shops": [{"name": "Kettle & Crane", "own_site": KC_SITE, "claims": [
                    {"text": "Carries teas from small makers", "source": code,
                     "quote": "alongside drinks and teas from small Ontario makers"}]}],
                "rejected": [{"name": "Loblaws", "reason_code": "CHAIN"}]}

    def community(T):
        code = re.search(r"SOURCE (S\w+)", T["fetch_page"](url=RUNCLUB, method="community")).group(1)
        return {"concept": "Offer cold tea after the Saturday run.", "groups": [
            {"name": "Ossington Runners", "own_site": RUNCLUB, "role": "ORGANISER", "activities": [
                {"text": "Runs a weekly social run", "source": code,
                 "quote": "We meet every Saturday 6am at Trinity Bellwoods Park for a 5k loop"}]}],
            "participation_assumptions": ["About 30 runners attend."]}

    def creator(T):
        code = re.search(r"SOURCE (S\w+)", T["fetch_page"](url=ARTICLE, method="creator")).group(1)
        T["creator_profile"](url="https://www.tiktok.com/@ossingtonstyle")
        ev = {"source": code, "quote": "Maya Chen (@ossingtonstyle on TikTok) is a Toronto creator"}
        return {"collaboration_concepts": ["A tasting video featuring the four canned teas."],
                "creators": [{"name": "Maya Chen", "profile_url": "https://www.tiktok.com/@ossingtonstyle", "evidence": [ev],
                              "location_quote": ev, "content": [
                                  {"source": code, "quote": "who reviews tea and sparkling drinks from small makers"}]}]}

    def digital(T):
        return {"channel_primary": "Instagram (Meta)", "objective": "SIGN_UP", "radius_km_hypothesis": 25,
                "targeting": [{"element": "city + radius", "basis": "PLATFORM_OPTION", "platforms_ref": "meta.city_radius"}],
                "message": "Sparkling cold-brewed tea, now on a Toronto waitlist.", "primary_metric": "Waitlist sign-ups",
                "stop_rule": "Stop after two weeks if sign-ups stay flat."}
    return {"retail": retail, "community": community, "creator": creator, "digital": digital}


def test_slice45_journey_in_the_browser(server, monkeypatch):
    from playwright.sync_api import expect, sync_playwright
    web = Web()
    toronto_pages(web)
    web.pages[KC_BRANDS] = (200, html("Brands we carry · Kettle & Crane",
                                      "Brands we carry: Pluck Tea, Sloane and Genuine Tea, alongside drinks and teas from small Ontario makers.",
                                      FILLER, "Wholesale enquiries: makers can write to hello@kettlecrane.example."))
    web.pages[RUNCLUB] = (200, html("Ossington Runners", "Ossington Runners is a free social run club in Toronto. We meet every "
                                    "Saturday 6am at Trinity Bellwoods Park for a 5k loop.", FILLER))
    web.pages[ARTICLE] = (200, html("Toronto style creators", "Maya Chen (@ossingtonstyle on TikTok) is a Toronto creator who "
                                    "reviews tea and sparkling drinks from small makers.", FILLER))
    web.pages[PRODUCT] = (200, html("Hibiscus Berry Sparkling Tea – Tidewell", "Hibiscus and Berry sparkling cold-brewed "
                                    "tea in a 12oz can. Brewed slowly, lightly sparkling, no added sugar.", FILLER))
    monkeypatch.setattr(products, "page_candidates", lambda run, pid, robots, attempts: [{"url": PRODUCT, "mcp": None}])
    oembed = "https://www.tiktok.com/oembed?url=https%3A%2F%2Fwww.tiktok.com%2F%40ossingtonstyle"
    web.pages[oembed] = (200, json.dumps({"author_name": "Maya Chen", "author_url": "https://www.tiktok.com/@ossingtonstyle"}))
    monkeypatch.setattr(fetch, "_live_get", web)
    monkeypatch.setattr(fetch, "_live_render", lambda url: {"url": url, "status": 0, "error": "not rendered"})
    monkeypatch.setattr(research, "judge", Checker())
    monkeypatch.setattr(research, "judge_prose", Prose())
    monkeypatch.setattr(research, "ground", grounding([KC]))
    monkeypatch.setattr(research, "run_agent", lambda ctx, row: toronto_script(ctx, {t.tool_name: t for t in research.make_tools(ctx)}))
    research._robots.clear()
    plays = scripts()
    monkeypatch.setattr(specialist, "run_loop", lambda ctx, spec, row, inp: plays[spec.method](
        {t.tool_name: t for t in spec.tools(ctx)}))

    def judge(ctx, system, listing, model_cls, kind, key):
        if kind == "check":
            return {"verdicts": [{"claim_id": i, "verdict": "SUPPORTS", "unsupported_parts": [], "shows_way_in": True}
                                 for i in re.findall(r"- id=(c\d+) \|", listing)]}
        return {"verdicts": [{"id": i, "kind": "PROPOSAL"} for i in re.findall(r"- id=(s\d+) \|", listing)]}
    monkeypatch.setattr(specialist, "_judge", judge)
    monkeypatch.setattr(specialist, "draft_call", lambda ctx, prompt: {
        "concept_name": "Makers-shelf trial", "concept_line": "Four canned teas on Kettle & Crane's makers shelf for a month.",
        "activities": ["Send a sample case", "Agree a four-week shelf slot"], "success_measure": "Units sold in four weeks",
        "audience_hypothesis": "Shoppers who already buy small-maker teas", "partners": ["Kettle & Crane"],
        "products": ["Hibiscus Berry Sparkling Tea"]})
    base = server(FakeQloo(), Strategist())
    monkeypatch.setattr(config, "LIVE_STAGES", STAGES + ("specialist", "specialist_check", "specialist_places", "specialist_search", "specialist_fetch",
                                                         "products_fetch", "creator_profile"))
    SHOTS.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        enter(page, base)
        open_brief(page)
        pid = page.url.split("/p/")[1].split("/")[0]
        explore(page, timeout=120000)
        page.get_by_role("heading", name=re.compile("^Toronto looks like the strongest first city")).wait_for(timeout=60000)
        # 6.5: one city's research starts from its plan; the research note (no field on the Plan page) is set first
        fi = db.latest("founder_input", pid)
        assert page.request.patch(f"{base}/api/projects/{pid}/intent", data={
            "base_version": fi["version"], "research_context": "We won't do pop-ups this year."}).ok
        page.locator(".decision-bar .btn-primary", has_text="Build a plan for Toronto").click()
        page.wait_for_url(re.compile(r"/plan/toronto$"))
        from test_slice65_browser import research_city
        research_city(page, base, pid)
        page.goto(base + f"/p/{pid}/opportunities/research")
        page.get_by_role("region", name="Toronto").get_by_text("DONE", exact=True).wait_for(timeout=60000)
        page.get_by_role("button", name="Review approaches").click()

        # S08 card → Investigate retail (cost shown first)
        card = page.get_by_role("article", name="A makers shelf at Kettle & Crane")
        card.get_by_role("button", name="Investigate retail").click()
        page.wait_for_url(re.compile("/opportunities/toronto/retail$"))
        expect(page.locator("main")).to_contain_text("Not investigated yet")
        page.get_by_role("button", name="Investigate retail").click()
        table = page.get_by_role("region", name="Stockist comparison")
        expect(table).to_contain_text("Pluck Tea", timeout=30000)
        expect(table).to_contain_text("MATCH")
        expect(page.get_by_role("region", name="Summary")).to_contain_text("EVIDENCED")
        expect(page.get_by_role("region", name="Summary")).to_contain_text("WILLINGNESS UNKNOWN")
        expect(page.get_by_role("region", name="Rejected")).to_contain_text("Loblaws")
        page.screenshot(path=SHOTS / "01-retail.png", full_page=True)
        # P45-I7: the product read from its own page opens its saved snapshot from the dossier
        page.get_by_role("region", name="Summary").get_by_role("button", name="Hibiscus Berry Sparkling Tea").click()
        expect(page.get_by_role("dialog", name="PRODUCT")).to_contain_text("lightly sparkling")
        page.keyboard.press("Escape")
        opener = table.get_by_role("button", name="Kettle & Crane")
        opener.click()
        expect(page.get_by_role("dialog", name="PARTNER")).to_contain_text("Kettle & Crane")
        page.keyboard.press("Escape")
        expect(opener).to_be_focused()

        # Develop → draft → edit → reload
        page.get_by_role("button", name="Develop this approach").click()
        draft = page.get_by_role("region", name="Developed draft")
        expect(draft).to_contain_text("EVIDENCED DRAFT", timeout=30000)
        expect(draft).to_contain_text("NOT CONFIRMED")
        expect(draft).to_contain_text("wholesale or consignment")
        page.get_by_label(re.compile("Success measure")).fill("A re-order within six weeks")
        draft.get_by_role("button", name="Save").click()
        expect(draft).to_contain_text("YOU SAID", timeout=10000)
        page.reload()
        expect(page.get_by_label(re.compile("Success measure"))).to_have_value("A re-order within six weeks", timeout=10000)
        page.screenshot(path=SHOTS / "02-draft.png", full_page=True)
        draft.get_by_role("button", name="Hibiscus Berry Sparkling Tea").click()
        expect(page.get_by_role("dialog", name="PRODUCT")).to_contain_text("FROM YOUR PRODUCT PAGE")
        page.keyboard.press("Escape")
        page.goto(base + f"/p/{pid}")  # the brief's Products group
        page.get_by_role("button", name=re.compile("^Products · ")).click(timeout=15000)  # v4 03: an accordion row
        page.get_by_role("button", name="Source for Hibiscus Berry Sparkling Tea").click()
        expect(page.get_by_role("dialog", name="PRODUCT")).to_contain_text("lightly sparkling")
        page.keyboard.press("Escape")

        # the note rules pop-ups out: not run, with the reason and Edit your note
        page.goto(base + f"/p/{pid}/opportunities/toronto/popup")
        expect(page.locator("main")).to_contain_text("your note rules this out", timeout=10000)
        expect(page.get_by_role("button", name="Edit your note")).to_be_visible()

        # creator → Add a creator profile (YOU SAID)
        page.goto(base + f"/p/{pid}/opportunities/toronto/creator")
        page.get_by_role("button", name=re.compile("Investigate creator")).click()
        creators = page.get_by_role("region", name="Creators")
        expect(creators).to_contain_text("IDENTIFIED", timeout=30000)
        page.get_by_label("Add a creator profile you know").fill("https://www.instagram.com/pernille.style/")
        creators.get_by_role("button", name="Add").click()
        expect(creators).to_contain_text("YOU SAID", timeout=10000)
        page.screenshot(path=SHOTS / "03-creator.png", full_page=True)

        # community activities and the digital strip
        page.goto(base + f"/p/{pid}/opportunities/toronto/community")
        page.get_by_role("button", name=re.compile("Investigate community")).click()
        expect(page.get_by_role("region", name="Organizers and activities")).to_contain_text("RECURRING", timeout=30000)
        page.goto(base + f"/p/{pid}/opportunities/toronto/digital")
        page.get_by_role("button", name="Investigate digital").click()
        strip = page.get_by_role("region", name="Audience, message, channel, measurement")
        expect(strip).to_contain_text("Google Search", timeout=30000)  # Google Ads only (owner, 28 Oct 2026)
        expect(strip).to_contain_text("No scenario yet: add digital to your plan")  # S5-21: from the plan, never figures of its own
        page.screenshot(path=SHOTS / "04-digital.png", full_page=True)

        # back on S08 the card shows the investigation; keyboard and 390 px
        page.goto(base + f"/p/{pid}/opportunities")
        expect(page.get_by_role("article", name="A makers shelf at Kettle & Crane")).to_contain_text("DRAFTED", timeout=10000)
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement !== document.body")
        m = page.context.new_page()
        m.set_viewport_size({"width": 390, "height": 844})
        for path, name in ((f"/p/{pid}/opportunities/toronto/retail", "05-mobile-retail"),
                           (f"/p/{pid}/opportunities/toronto/digital", "06-mobile-digital")):
            m.goto(base + path)
            m.locator("main h1").first.wait_for()
            m.wait_for_timeout(500)
            m.screenshot(path=SHOTS / f"{name}.png", full_page=True)
            wide = m.evaluate("""[...document.querySelectorAll('main *')].filter(e => e.getBoundingClientRect().right > window.innerWidth + 1)
                .slice(0, 8).map(e => e.tagName + '.' + e.className + ' ' + Math.round(e.getBoundingClientRect().right))""")
            assert not m.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"), (name, wide)
        assert not errors, errors
        b.close()
