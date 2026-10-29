"""Slice-3.5 browser journey through the real UI, from S01 (BUILD-PLAN slice 3.5, live cases L35-A / L35-B).

usage: python -m scripts.journey_slice35 <app_base> <untuckit|gotbag> <out_dir>

untuckit (L35-A, matched, both · presence): S01 goal sentence → untuckit.com → brief (where you already are, with store
  coverage; who likes you, attributed) → Start exploring markets → S04 ranking row → S05 At home ≤ 2 and Abroad ≤ 3, a top
  per non-empty group, deterministic first lines, home and store cities absent → untick → All 80 (YOU'RE ALREADY HERE,
  OUTSIDE YOUR GOAL, NOT RANKED BY QLOO) → add → refresh → 390 px.
gotbag (L35-B, no match, international · test demand): the L2-B founder steps through the UI (scripts.journey_identity:
  Neither, remove Travel, add Ocean, confirm) → the purpose set on the brief → stand-ins and their audiences → scan →
  S05 ordered through the stand-ins, test-demand windows disclosed → refresh → 390 px.
Prints one line per check, writes screenshots and markets.json (the final MarketsView); exits non-zero on a failure.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

base, case, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
results = []
BRIEF = re.compile("Here's what we understand")
RECOMMENDED = re.compile(r"\d+ cit(y|ies) recommended for research|No city recommended yet")
DONE = dict(name="See the recommendations", exact=True)


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f" · {detail}" if detail else ""), flush=True)


def pick(page, menu, option):
    page.get_by_role("button", name=re.compile(f"^{menu}:")).first.click()
    page.get_by_role("option", name=option).click()


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    state = out / "state.json"
    if case == "gotbag":
        up = out / "upstream"
        rc = subprocess.run([sys.executable, "-m", "scripts.journey_identity", base, "ask", "gotbag.com", str(up),
                             "--remove", "Travel", "--search", "ocean", "--add", "Ocean", "--state", str(state)])
        check("upstream L2-B founder steps through the UI (journey_identity)", rc.returncode == 0)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900}, storage_state=str(state))
        page = ctx.new_page()
        page.goto((up / "project_url.txt").read_text().strip())
    else:
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.goto(base + "/")
        pick(page, "Where", "both at home and abroad")
        check("S01: the goal sentence reads as chosen", "expand both at home and abroad ▾ to expand my market presence"
              in page.locator(".goal-sentence").inner_text())
        page.screenshot(path=out / "00-s01-goal.png")
        page.get_by_label("Your brand's website").fill("untuckit.com")
        page.keyboard.press("Enter")
        page.wait_for_url(re.compile(r"/p/prj_"), timeout=15000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    chooser = page.get_by_role("button", name="This is us")
    page.get_by_role("heading", name=BRIEF).or_(chooser).first.wait_for(timeout=300000)
    if chooser.count():  # a founder answer when Qloo's match isn't confident
        page.get_by_role("radio", name=re.compile("untuckit", re.I)).first.check()
        chooser.click()
        page.get_by_role("heading", name=BRIEF).wait_for(timeout=300000)
    pid = page.url.split("/p/")[1].split("/")[0]
    (out / "project_url.txt").write_text(base + f"/p/{pid}")

    if case == "gotbag":
        pick(page, "Why", "test demand before committing")
        page.locator(".notice").first.wait_for(timeout=10000)
        check("the purpose changes on the brief, as a founder decision", "test demand before committing" in page.locator(".goal-sentence").first.inner_text())
        page.get_by_text("Brands with a similar following:").wait_for(timeout=120000)
        si = page.locator(".standins").first.inner_text()
        check("stand-ins chosen by Qloo, never called competitors", "Brands with a similar following:" in si and "Never treated as competitors" in si, si.split("\n")[0])
    page.locator(".audience").first.get_by_text(re.compile("cultural relationship")).wait_for(timeout=120000)
    aud = page.locator(".audience").first.inner_text()
    pres = page.locator(".presence").first.inner_text()
    if case == "gotbag":
        check("audiences are labelled as the stand-ins', not GOT BAG's", "used as a stand-in for you" in aud)
        check("demographics stay one attributed row per stand-in", aud.count("People who like") == 3, str(aud.count("People who like")))
        check("no Qloo store search without a match (coverage not 'no stores')", "doesn't know your brand" in pres)
    else:
        check("who likes you: attributed to Untuckit, with the caveat", "people who like untuckit" in aud.lower()
              and "not your customer data or audience size" in aud)
        check("where you already are: store coverage is stated", any(w in pres for w in ("Qloo lists", "Qloo returned", "stopped partway", "failed")),
              pres.split("\n")[0][:120])
        check("Qloo store cities are left out", "QLOO STORES" in pres)
    page.screenshot(path=out / "01-brief.png", full_page=True)

    start = page.get_by_role("button", name="Start exploring markets")
    check("Start exploring markets is enabled", start.is_enabled())
    start.click()
    page.wait_for_url(re.compile(r"/markets/scan$"), timeout=15000)
    page.get_by_role("heading", name=re.compile("Scanning 80 cities")).wait_for()
    page.wait_for_timeout(3000)
    check("S04: the ranking row", "candidate cities" in page.locator(".ranking-row").inner_text(), page.locator(".ranking-row").inner_text())
    page.screenshot(path=out / "02-scan-running.png", full_page=True)
    page.get_by_role("button", **DONE).wait_for(timeout=900000)
    page.wait_for_timeout(800)
    cov = page.locator(".coverage").inner_text()
    nums = dict((k, int(v)) for v, k in re.findall(r"(\d+) (attempted|returned|empty|failed|unresolved)", cov))
    check("coverage partitions 80 at completion", sum(nums.get(k, 0) for k in ("returned", "empty", "failed", "unresolved")) == 80, cov)
    row = page.locator(".ranking-row").inner_text()
    check("Qloo ranked in one query", "in one query" in row, row)
    page.screenshot(path=out / "03-scan-done.png", full_page=True)

    page.get_by_role("button", **DONE).click()
    page.get_by_role("heading", name=RECOMMENDED).wait_for(timeout=600000)
    page.wait_for_timeout(800)
    main = page.locator("main").inner_text()
    cards = page.locator("article.city-card")
    names = [cards.nth(i).get_attribute("aria-label") for i in range(cards.count())]
    lines = [cards.nth(i).locator(".position-line").inner_text() for i in range(cards.count())]
    check("each card's first line is the deterministic ranking line", lines and all("In one Qloo ranking of your" in x for x in lines), lines[0] if lines else "")
    check("the signals and the order are explained", "SIGNALS WE USED" in main and "WHY THIS ORDER?" in main)
    if case == "untuckit":
        home = re.search(r"AT HOME · (\d) OF UP TO 2", main)
        abroad = re.search(r"ABROAD · (\d) OF UP TO 3", main)
        check("both: At home ≤ 2 and Abroad ≤ 3, never padded", bool(home and abroad), f"{home and home.group(0)} · {abroad and abroad.group(0)}")
        tops = main.count("TOP RECOMMENDATION")
        check("a top per non-empty group", tops == (int(home.group(1)) > 0) + (int(abroad.group(1)) > 0) if home and abroad else False, str(tops))
        check("New York (home) is not recommended", "New York" not in names, ", ".join(names))
    else:
        check("ordered through the stand-ins", all("(stand-ins)" in x for x in lines))
        check("the test-demand windows are disclosed", "testing demand" in main)
    page.screenshot(path=out / "04-recommendations.png", full_page=True)
    if names:
        first = cards.first
        first.get_by_role("button", name="Why this city?").click()
        check("Why this city? opens the evidence trail", "1 · QLOO'S RANKING" in first.locator(".trail").inner_text())
        q = first.locator(".trail").get_by_role("button", name=re.compile("QLOO"))
        if q.count():
            q.first.click()
            drawer = page.get_by_role("dialog", name="QLOO PLACE")
            expect(drawer).to_contain_text("A cultural relationship, not demand")
            page.screenshot(path=out / "05-place-drawer.png")
            page.keyboard.press("Escape")
        last = names[-1]
        page.get_by_label(f"Research {last}").uncheck()
        page.get_by_text(f"{last} removed from research").wait_for(timeout=10000)
        check(f"untick {last}: the founder's change is noted", True)
    page.get_by_role("link", name="See all 80 cities").first.click()
    page.get_by_role("heading", name="Every city we scanned").wait_for()
    allv = page.locator("main").inner_text()
    check("All 80 shows the new reasons", "NOT RANKED BY QLOO" in allv and ("OUTSIDE YOUR GOAL" in allv or case == "untuckit"),
          " · ".join(r for r in ("YOU'RE ALREADY HERE", "OUTSIDE YOUR GOAL", "NOT RANKED BY QLOO") if r in allv))
    if case == "untuckit":
        ny = page.locator(".city-rows li", has_text="New York").first.inner_text()
        check("New York reads YOU'RE ALREADY HERE", "YOU'RE ALREADY HERE" in ny, ny.replace("\n", " · "))
    page.get_by_role("button", name="Not recommended").click()
    row = page.locator(".city-rows li", has=page.get_by_text("NOT PRIORITISED", exact=True)).first
    if row.count():
        added = row.locator(".city-name").inner_text()
        row.get_by_role("button", name=f"Research {added} too").click()
        page.get_by_text(f"{added} added to the research").wait_for(timeout=10000)
        check(f"All 80 → add {added} (ADDED BY YOU), never a recommendation", True)
    page.screenshot(path=out / "06-all80.png", full_page=True)
    page.get_by_role("link", name="Back to the recommendations").click()
    page.get_by_role("heading", name=RECOMMENDED).wait_for()
    h1 = page.locator("main h1").inner_text()
    page.reload()
    page.get_by_role("heading", name=RECOMMENDED).wait_for(timeout=30000)
    check("refresh keeps the recommendations and the founder's changes", page.locator("main h1").inner_text() == h1)
    page.screenshot(path=out / "07-after-refresh.png", full_page=True)

    m = ctx.new_page()
    m.set_viewport_size({"width": 390, "height": 844})
    for path, name in ((f"/p/{pid}", "08-mobile-brief"), (f"/p/{pid}/markets", "09-mobile-recommendations"),
                       (f"/p/{pid}/markets/all", "10-mobile-all80")):
        m.goto(base + path)
        m.locator("main h1").first.wait_for()
        m.wait_for_timeout(600)
        m.screenshot(path=out / f"{name}.png", full_page=True)
        check(f"390px: no horizontal page scroll ({name})", not m.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"))
    view = ctx.request.get(base + f"/api/projects/{pid}/markets").json()
    (out / "markets.json").write_text(json.dumps(view, indent=1))
    check("no JavaScript errors", not errors, "; ".join(errors[:2]))
    browser.close()
print(f"{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
