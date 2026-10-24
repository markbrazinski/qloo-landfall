"""Slice-1 browser journey through the real UI: URL → reading → brief → evidence → activity → correction → refresh.

usage: python -m scripts.journey <app_base> <brand_url|demo> <out_dir> [correction value]
Prints one line per check; exits non-zero if any check fails.
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

base, target, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
fix = sys.argv[4] if len(sys.argv) > 4 else None
out.mkdir(parents=True, exist_ok=True)
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f" · {detail}" if detail else ""))


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})  # one context: the session cookie owns the project
    page = ctx.new_page()
    page.goto(base + "/")
    page.screenshot(path=out / "01-entry.png", full_page=True)
    if target == "demo":
        page.get_by_role("button", name=re.compile("Tidewell · sparkling")).click()
    else:
        page.get_by_label("Your brand's website").fill(target)
        page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/p/prj_"), timeout=15000)
    project_url = page.url
    check("URL submit opens the project", "/p/prj_" in project_url, project_url)
    page.wait_for_timeout(2500)
    page.screenshot(path=out / "02-reading.png", full_page=True)
    reading_title = page.locator("h1").first.inner_text()
    check("reading screen shows progress", "Reading" in reading_title or "understand" in reading_title, reading_title)

    try:
        page.get_by_role("heading", name=re.compile("Here's what we understand")).wait_for(timeout=240000)
        check("brief opens automatically", True)
    except Exception:
        page.screenshot(path=out / "03-stopped.png", full_page=True)
        check("brief opens automatically", False, page.locator("h1").first.inner_text())
        browser.close()
        sys.exit(1)
    page.screenshot(path=out / "03-brief.png", full_page=True)
    body = page.locator("main").inner_text()
    check("unknowns never claim site-wide absence", "Not on the site" not in body)
    if page.get_by_text("Not known yet").count() and page.locator(".group", has_text="Not known yet").locator(".row").count():
        check("unknowns say what was read", "Not established from the pages read" in body or "Not in your description" in body)
    sources = page.get_by_role("button", name="View source ↗")
    check("sourced rows open evidence", sources.count() > 0, f"{sources.count()} View source links")

    if sources.count():
        sources.first.focus()
        sources.first.click()
        drawer = page.get_by_role("dialog", name="EVIDENCE")
        expect(drawer).to_be_visible()
        page.wait_for_timeout(600)
        page.screenshot(path=out / "04-evidence.png")
        has_quote = drawer.locator("blockquote").count() > 0
        text = drawer.inner_text()
        check("evidence drawer shows source and excerpt", has_quote and "SOURCE" in text)
        heading = drawer.locator("h2").first.inner_text()
        check("evidence drawer shows the full displayed claim", ": " in heading and "Quote on the page" in text, heading)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        focused = page.evaluate("document.activeElement && document.activeElement.innerText")
        check("Escape closes and returns focus to the opener", "view source" in (focused or "").lower(), repr(focused))

    inferred = page.get_by_role("button", name="INFERRED ↗")
    check("interpretations say what they are inferred from", inferred.count() > 0 and page.get_by_text(re.compile("^Inferred from:")).count() > 0,
          f"{inferred.count()} inferred rows")
    if inferred.count():
        inferred.first.click()
        inf = page.get_by_role("dialog", name="INFERENCE")
        expect(inf).to_be_visible()
        page.screenshot(path=out / "04b-inference.png")
        itext = inf.inner_text()
        check("inference drawer lists its basis and reasoning", "INFERRED FROM" in itext and "WHY WE READ IT THIS WAY" in itext)
        nested = inf.get_by_role("button", name="View source ↗")
        if nested.count():
            nested.first.click()
            ev = page.get_by_role("dialog", name="EVIDENCE")
            expect(ev).to_be_visible()
            check("a cited fact opens its own source, with a way back", ev.get_by_role("button", name="← Back").count() == 1)
            page.keyboard.press("Escape")
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)

    page.get_by_role("button", name=re.compile("^Activity")).click()
    act = page.get_by_role("dialog", name="ACTIVITY")
    expect(act).to_be_visible()
    page.screenshot(path=out / "05-activity.png")
    check("activity lists real steps", act.locator(".act-list li").count() >= 3, f"{act.locator('.act-list li').count()} entries")
    page.keyboard.press("Escape")

    if fix:
        row = page.locator(".row-wrap", has_text="Home market")
        row = row if row.count() else page.locator(".row-wrap").filter(has=page.get_by_role("button", name="Correct")).first
        row.get_by_role("button", name="Correct").first.click()
        inp = row.locator("input")
        inp.fill(fix)
        row.get_by_role("button", name="Save correction").click()
        page.get_by_text(re.compile("Brief v2. Nothing has used it yet")).wait_for(timeout=10000)
        check("correction creates brief v2 with notice", True)
        page.screenshot(path=out / "06-corrected.png", full_page=True)
        page.reload()
        page.get_by_role("heading", name=re.compile("Here's what we understand")).wait_for(timeout=15000)
        check("v2 and the correction survive refresh",
              page.get_by_text("BRAND · YOUR BRIEF V2").count() == 1 and page.get_by_text(fix).count() >= 1)

    page = ctx.new_page()  # same context, so this is the founder's own brief, not an error page
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(project_url)
    page.get_by_role("heading", name=re.compile("Here's what we understand")).wait_for(timeout=15000)
    page.screenshot(path=out / "07-mobile.png", full_page=True)
    overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
    check("390px brief has no horizontal page scroll", not overflow)
    browser.close()
print(f"{sum(results)}/{len(results)} checks passed · {project_url}")
sys.exit(0 if all(results) else 1)
