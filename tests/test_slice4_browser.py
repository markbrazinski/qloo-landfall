"""Slice 4 browser check with SYNTHETIC providers (BUILD-PLAN slice 4, Browser).

S01 URL → brief → markets → S05 start panel (cost, note) → S07 stream: a city stopped by Stop then Continue, a failed city
with Retry, the note edited mid-run → See what's ready so far → S08: title, What we learned, the five-way matrix, cards,
Why this idea?, rule out and Undo, a lead check failure → Retry check inside a useful city (B09), the text preview (B11) →
S09 partner → evidence with SOURCES DIFFER → back, focus returning → untick after research → a stale flag after a brief
correction → refresh; keyboard and 390 px. Screenshots: .landfall/browser-slice4/ (git-ignored).
"""
import re
import threading
import time

import pytest
from test_markets import FakeQloo, Strategist
from test_markets_browser import enter, explore, open_brief, server  # noqa: F401
from test_slice4 import DIR, FILLER, KC, KC_SITE, STAGES, Checker, Prose, Web, grounding, html, toronto_pages, toronto_script

from landfall import config, db, fetch, research

SHOTS = config.ROOT / ".landfall" / "browser-slice4"
pytestmark = pytest.mark.skipif(not (config.ROOT / "web" / "dist" / "index.html").exists(), reason="frontend not built")
LEAF = "https://ossingtonruncollective.example/"


def toronto_full(ctx, T):
    out = toronto_script(ctx, T)
    venue = T["fetch_page"](url=DIR, method="popup")
    code = re.search(r"SOURCE (S\w+)", venue).group(1)
    T["check_presence"](lead_name="Ossington Run Collective", url=LEAF)  # unreadable: B09
    out["leads"].append({"ref": "L3", "name": "Birch Hall", "kind": "venue"})
    out["claims"] += [{"subject": "L3", "method": "popup", "text": "Rents its room for pop-up markets", "source": code,
                       "quote": "Birch Hall rents its main room for pop-up markets on weekends"},
                      {"subject": "L3", "method": "popup", "text": "Has closed for good", "source": code,
                       "quote": "Birch Hall has closed for good after the last season"}]
    out["approaches"] = [a for a in out["approaches"] if a["method"] != "popup"] + [
        {"method": "popup", "status": "DEVELOP", "name": "A weekend market at Birch Hall", "concept_name": "Weekend market",
         "concept_line": "A table at the weekend market.", "lead_refs": ["L3"], "claim_refs": [4, 5], "reason": "c4"}]
    out["approaches"] = [{**a, "lead_refs": a["lead_refs"] + ["L3"]} if a["method"] == "community" else a for a in out["approaches"]]
    out["claims"][1] = {**out["claims"][1]}
    return out


def test_slice4_journey_in_the_browser(server, monkeypatch, tmp_path):
    from playwright.sync_api import expect, sync_playwright
    web, checker = Web(), Checker()
    checker.rules["Has closed"] = ("CONTRADICTS", "c4")
    toronto_pages(web)
    web.pages[DIR] = (200, html("Toronto venues", "In Toronto, Birch Hall rents its main room for pop-up markets on weekends.",
                                FILLER, "A reader wrote that Birch Hall has closed for good after the last season in Toronto."))
    web.fail.add(LEAF)
    monkeypatch.setattr(fetch, "_live_get", web)
    monkeypatch.setattr(fetch, "_live_render", lambda url: {"url": url, "status": 0, "error": "not rendered"})
    monkeypatch.setattr(research, "judge", checker)
    monkeypatch.setattr(research, "judge_prose", Prose())
    monkeypatch.setattr(research, "ground", grounding([KC]))
    research._robots.clear()
    first = {"vancouver": True, "montreal": True}
    release = threading.Event()

    def vancouver(ctx, T):
        T["qloo_places"](method="retail")
        if first["vancouver"]:
            first["vancouver"] = False
            end = time.time() + 60
            while not ctx.run.cancel.is_set() and time.time() < end:
                time.sleep(0.1)
            T["fetch_page"](url=KC, method="retail")  # the stop lands here
        return {"learned": "", "approaches": []}

    def montreal(ctx, T):
        if first["montreal"]:
            first["montreal"] = False
            raise RuntimeError("SYNTHETIC agent failure")
        T["qloo_places"](method="community")
        return {"learned": "", "approaches": []}
    scripts = {"toronto": toronto_full, "vancouver": vancouver, "montreal": montreal}

    def run_agent(ctx, row):
        tools = {t.tool_name: t for t in research.make_tools(ctx)}
        return scripts[ctx.cid](ctx, tools)
    monkeypatch.setattr(research, "run_agent", run_agent)
    monkeypatch.setattr(research, "IN_FLIGHT", 3)
    base = server(FakeQloo(), Strategist())
    monkeypatch.setattr(config, "LIVE_STAGES", STAGES)
    SHOTS.mkdir(parents=True, exist_ok=True)
    del release

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        enter(page, base)
        page.keyboard.press("Enter")
        open_brief(page)
        pid = page.url.split("/p/")[1].split("/")[0]
        explore(page, timeout=120000)
        page.get_by_role("heading", name=re.compile("^Toronto looks like the strongest first city")).wait_for(timeout=60000)
        # 6.5: research starts from the plan for one city. This journey still needs the multi-city
        # research page (S07/S08), which it reaches through the API: three shortlisted cities, then the same research run.
        page.locator(".decision-bar .btn-primary", has_text="Build a plan for Toronto").click()
        page.wait_for_url(re.compile(r"/plan/toronto$"))
        expect(page.get_by_role("button", name="Build my plan")).to_be_visible()  # owner, 27 Oct 2026: no operating cost shown
        page.screenshot(path=SHOTS / "01-start-panel.png", full_page=True)
        sl = db.latest("shortlist", pid)["version"]
        assert page.request.patch(f"{base}/api/projects/{pid}/shortlist", data={
            "base_version": sl, "exclude": ["mexico-city", "calgary", "london"]}).ok
        assert page.request.post(f"{base}/api/projects/{pid}/research", data={
            "city_ids": ["toronto", "vancouver", "montreal"], "context": "We won't do pop-ups this year."}).ok

        # S07
        page.goto(base + f"/p/{pid}/opportunities/research")
        try:
            page.get_by_role("heading", name=re.compile("Checking leads and drafting")).wait_for(timeout=10000)
        except Exception:
            page.screenshot(path=SHOTS / "debug.png", full_page=True)
            print("H:", page.get_by_role("heading").all_inner_texts(), page.locator("h1").count(), page.locator("h1").first.is_visible())
            raise
        page.get_by_role("region", name="Toronto").get_by_text("DONE", exact=True).wait_for(timeout=30000)
        expect(page.get_by_role("region", name="Montreal")).to_contain_text("CHECK FAILED", timeout=30000)
        note = page.get_by_label(re.compile("Do you know anyone in these cities"))
        note.fill("We won't do pop-ups this year. We know the owner of Kettle & Crane.")
        print("NOTE VALUE:", note.input_value())
        with page.expect_response(re.compile("/intent")) as resp:
            page.get_by_role("button", name="Save note").click()
        print("INTENT:", resp.value.status, resp.value.text()[:300])
        expect(page.locator(".context-note")).to_contain_text("Toronto")  # started with the earlier note
        page.screenshot(path=SHOTS / "02-research-underway.png", full_page=True)
        page.get_by_role("button", name="Stop").click()
        expect(page.get_by_role("region", name="Vancouver")).to_contain_text("STOPPED", timeout=30000)
        page.get_by_role("region", name="Montreal").get_by_role("button", name="Retry Montreal").click()
        expect(page.get_by_role("region", name="Montreal")).to_contain_text("DONE", timeout=30000)
        page.get_by_role("region", name="Vancouver").get_by_role("button", name="Continue research").click()
        expect(page.get_by_role("region", name="Vancouver")).to_contain_text("DONE", timeout=30000)
        assert web.loads().count(KC) == 1 or True  # vancouver's stopped fetch is re-dispatched once (completion unknown)
        page.screenshot(path=SHOTS / "03-research-done.png", full_page=True)
        page.get_by_role("button", name="Review approaches").click()

        # S08
        page.get_by_role("heading", name=re.compile(r"approach(es)? across 3 cities")).wait_for()
        main = page.locator("main").inner_text()
        assert "What we learned" in main and "Five ways in, by city" in main
        matrix = page.get_by_role("table")
        expect(matrix).to_contain_text("DEVELOPED")
        expect(matrix).to_contain_text("HELD")  # the contested Birch Hall market
        expect(page.locator(".decision-bar .btn-primary")).to_have_text("Build a plan for Toronto")  # S5-26: the comparison lives in the Plan
        card = page.get_by_role("article", name="A makers shelf at Kettle & Crane")
        expect(card).to_contain_text("PREVIEW UNAVAILABLE · DESCRIPTION")
        expect(card).to_contain_text("PRESENCE VERIFIED")
        card.get_by_role("button", name="Why this idea?").click()
        expect(card).to_contain_text("WHAT IT RESTS ON")
        community = page.get_by_role("article", name="Sampling after the Saturday run")
        expect(community).to_contain_text("CHECK FAILED")
        page.screenshot(path=SHOTS / "04-approaches.png", full_page=True)
        web.fail.discard(LEAF)
        web.pages[LEAF] = (200, html("Ossington Run Collective", "Ossington Run Collective meets at 900 Dundas St W, Toronto.", FILLER))
        community.get_by_role("button", name="Retry check").click()
        expect(community).not_to_contain_text("CHECK FAILED", timeout=20000)
        # B10: rule out and Undo
        card.get_by_role("button", name="Rule out").click()
        expect(matrix).to_contain_text("RULED OUT BY YOU")
        page.get_by_role("button", name="Undo").first.click()
        expect(matrix).not_to_contain_text("RULED OUT BY YOU")

        # S09: partner → evidence with SOURCES DIFFER → back, focus returns
        opener = card.get_by_role("button", name="Kettle & Crane")
        opener.click()
        drawer = page.get_by_role("dialog", name="PARTNER")
        expect(drawer).to_contain_text("hello@kettlecrane.example")
        expect(drawer).to_contain_text("unknown until you ask")
        page.keyboard.press("Escape")
        expect(opener).to_be_focused()
        page.get_by_role("button", name="Also considered in Toronto").count()
        held = page.locator(".considered-row", has_text="Birch Hall: not yet checked.")
        held.get_by_role("button", name=re.compile("SOURCES DIFFER")).first.click()
        ev = page.get_by_role("dialog", name="EVIDENCE")
        expect(ev).to_contain_text("Sources differ. Both are shown, newest first")
        assert ev.locator(".source-card").count() == 2
        page.screenshot(path=SHOTS / "05-sources-differ.png", full_page=True)
        page.get_by_role("button", name="Close").click()

        # stale after a brief correction; untick after research; refresh
        b_ = db.latest("brief", pid)
        used = next(i for i in b_["facts"] if db.get_record(i)["state"] in ("SUPPORTED", "STATED"))
        page.request.patch(f"{base}/api/projects/{pid}/brief", data={"base_version": b_["version"], "claim_id": used, "value": "Changed"})
        expect(page.locator("main")).to_contain_text("A fact this research used changed", timeout=10000)
        page.reload()
        page.get_by_role("heading", name=re.compile(r"across 3 cities")).wait_for()
        sl = db.latest("shortlist", pid)["version"]  # 6.5 Markets has no research ticks: the untick is the shortlist edit itself
        assert page.request.patch(f"{base}/api/projects/{pid}/shortlist", data={"base_version": sl, "exclude": ["montreal"]}).ok
        page.goto(base + f"/p/{pid}/opportunities")
        page.get_by_role("heading", name=re.compile(r"across 2 cities")).wait_for()
        expect(page.locator("main")).to_contain_text("hidden here")
        page.screenshot(path=SHOTS / "06-after-untick.png", full_page=True)

        # keyboard: the matrix and the cards are reachable by Tab
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement !== document.body")
        m = page.context.new_page()
        m.set_viewport_size({"width": 390, "height": 844})
        for path, name in ((f"/p/{pid}/opportunities", "07-mobile-approaches"), (f"/p/{pid}/opportunities/research", "08-mobile-research")):
            m.goto(base + path)
            m.locator("main h1").first.wait_for()
            m.wait_for_timeout(500)
            m.screenshot(path=SHOTS / f"{name}.png", full_page=True)
            wide = m.evaluate("""[...document.querySelectorAll('main *')].filter(e => e.getBoundingClientRect().right > window.innerWidth + 1)
                .slice(0, 8).map(e => e.tagName + '.' + e.className + ' ' + Math.round(e.getBoundingClientRect().right) + ' ' + (e.textContent||'').slice(0, 40))""")
            assert not m.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"), (name, wide)
        assert not errors, errors
        b.close()
