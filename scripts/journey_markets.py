"""Slice-3 browser journey through the real UI, from URL entry (BUILD-PLAN slice 3, live cases L3-A / L3-B).

usage: python -m scripts.journey_markets <app_base> <komono|gotbag> <out_dir> [--home "Antwerp, Belgium"]

komono (L3-A): URL → automatic match, brief opens → Start exploring markets → S04 → S05 → untick the last shortlisted
  city → All 80 → add the first returned city not proposed → correct Home market (fit changes, notice, no calls) → refresh.
gotbag (L3-B): the L2-B founder steps through the UI (scripts.journey_identity: Neither, remove Travel, add Ocean,
  confirm) → Start exploring markets → S04 → S05 → refresh.
Prints one line per check and writes screenshots and markets.json (the final MarketsView); exits non-zero on a failure.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

base, case, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
arg = lambda name, default=None: next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == name), default)  # noqa: E731
home = arg("--home")
out.mkdir(parents=True, exist_ok=True)
results = []
BRIEF = re.compile("Here's what we understand")
SHORTLISTED = re.compile(r"\d+ cit(y|ies) shortlisted for research|The shortlist isn't ready")


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f" · {detail}" if detail else ""), flush=True)


def header(page):
    return page.get_by_role("button", name=re.compile("^Activity")).inner_text()


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
        page.get_by_label("Your brand's website").fill("komono.com")
        page.keyboard.press("Enter")
        page.wait_for_url(re.compile(r"/p/prj_"), timeout=15000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.get_by_role("heading", name=BRIEF).wait_for(timeout=300000)
    pid = page.url.split("/p/")[1].split("/")[0]
    (out / "project_url.txt").write_text(base + f"/p/{pid}")
    if case == "komono":
        check("confident match: the brief opens with 'Matched automatically'", page.get_by_text("Matched automatically").count() >= 1)
    start = page.get_by_role("button", name="Start exploring markets")
    check("Start exploring markets is enabled once the profile is confirmed", start.is_enabled())
    start.click()
    page.wait_for_url(re.compile(r"/markets/scan$"), timeout=15000)
    page.get_by_role("heading", name=re.compile("Scanning 80 cities")).wait_for()
    page.wait_for_timeout(4000)
    page.screenshot(path=out / "01-scan-running.png", full_page=True)
    mid = page.locator(".coverage").inner_text()
    check("S04 while running: the coverage line and map dots", "80 in catalog" in mid and page.locator(".map-dot").count() == 80, mid)
    page.get_by_role("button", name=re.compile("^See the shortlist")).wait_for(timeout=600000)
    page.wait_for_timeout(800)
    cov = page.locator(".coverage").inner_text()
    nums = dict((k, int(v)) for v, k in re.findall(r"(\d+) (attempted|returned|empty|failed|unresolved)", cov))
    check("coverage partitions 80 at completion", sum(nums.get(k, 0) for k in ("returned", "empty", "failed", "unresolved")) == 80, cov)
    check("emerging findings describe each search", page.locator(".findings li").count() > 0)
    page.locator(".map-dot").first.focus()
    page.keyboard.press("Enter")
    check("keyboard: a map dot selects its city", page.locator(".city-detail").count() == 1)
    page.screenshot(path=out / "02-scan-done.png", full_page=True)

    page.get_by_role("button", name=re.compile("^See the shortlist")).click()
    page.get_by_role("heading", name=SHORTLISTED).wait_for(timeout=600000)
    page.wait_for_timeout(800)
    h1 = page.locator("main h1").inner_text()
    n = int(re.match(r"\d+", h1).group(0)) if h1[0].isdigit() else 0
    main = page.locator("main").inner_text()
    check("S05: the shortlist opens with its research-priority brief", n > 0 and "RESEARCH PRIORITY" in main and
          "WHAT WE UNDERSTOOD" in main and "STILL TO ESTABLISH" in main, h1)
    check("never padded: at most 6, cap note when fewer", n <= 6 and (n == 6 or "isn't padded to 6" in main))
    research = page.locator("#primary-reason")
    check("the research CTA is a labelled boundary", "City research arrives in the next build" in research.inner_text())
    page.screenshot(path=out / "03-shortlist.png", full_page=True)
    first = page.locator("article.city-card").first
    why = first.get_by_role("button", name="Why this priority?")
    why.click()
    check("Why this priority? opens the four-step evidence trail", why.get_attribute("aria-expanded") == "true"
          and "2 · WHAT QLOO RETURNED" in first.locator(".trail").inner_text())
    first.locator(".trail").get_by_role("button", name=re.compile("QLOO")).first.click()
    drawer = page.get_by_role("dialog", name="QLOO PLACE")
    expect(drawer).to_contain_text("A cultural relationship, not demand")
    page.screenshot(path=out / "04-place-drawer.png")
    page.keyboard.press("Escape")
    expect(drawer).to_have_count(0)
    page.locator("details.deferred summary").click()
    page.screenshot(path=out / "05-trail-deferred.png", full_page=True)

    if case == "komono":
        cards = page.locator("article.city-card")
        last = cards.nth(cards.count() - 1).get_attribute("aria-label")
        before = header(page)
        page.get_by_label(f"Research {last}").uncheck()
        page.get_by_role("button", name=f"Research {n - 1} of {n} shortlisted cit{'y' if n == 1 else 'ies'}").wait_for(timeout=10000)
        check(f"untick {last}: YOU CHANGED notice and the CTA count follows",
              page.get_by_text(f"{last} removed from research").count() == 1, last)
        page.get_by_role("link", name="See all 80 cities").first.click()
        page.get_by_role("heading", name="Every city we scanned").wait_for()
        page.get_by_role("button", name="Not proposed").click()
        row = page.locator(".city-rows li", has=page.get_by_text("RETURNED", exact=True)).first
        added = row.locator(".city-name").inner_text()
        row.get_by_role("button", name=f"Research {added} too").click()
        page.get_by_text(f"{added} added to the research").wait_for(timeout=10000)
        check(f"All 80 → add {added} (ADDED BY YOU), no new research", header(page) == before, header(page))
        page.screenshot(path=out / "06-all80-added.png", full_page=True)
        if home:
            page.goto(base + f"/p/{pid}")
            page.get_by_role("heading", name=BRIEF).wait_for()
            row = page.locator(".row-wrap", has=page.get_by_text("Home market", exact=True))
            row.get_by_role("button", name="Correct").click()
            page.get_by_label("Home market").fill(home)
            page.get_by_role("button", name="Save correction").click()
            note = page.locator(".notice").first
            note.wait_for(timeout=10000)
            check("brief correction says the shortlist used it: fit now, reasoning not", "Your shortlist used it too" in note.inner_text(),
                  note.inner_text())
            page.goto(base + f"/p/{pid}/markets")
            page.get_by_role("heading", name=SHORTLISTED).wait_for()
            page.wait_for_timeout(600)
            txt = page.locator("main").inner_text()
            check("shortlist-wide notice names Home market; Update offered", "(Home market)" in txt and
                  page.get_by_role("button", name=re.compile("Update the shortlist")).count() == 1)
            check("fit recomputed: distances from the corrected home appear", "km from home" in txt)
            check("0 model and 0 Qloo calls for the correction", header(page) == before, f"{before} → {header(page)}")
            page.screenshot(path=out / "07-home-corrected.png", full_page=True)

    page.reload()
    page.get_by_role("heading", name=SHORTLISTED).wait_for(timeout=30000)
    check("refresh keeps the shortlist and the founder's changes", page.locator("main h1").inner_text() == h1)
    page.screenshot(path=out / "08-after-refresh.png", full_page=True)
    page.get_by_role("button", name=re.compile("^Activity")).click()
    act = page.get_by_role("dialog", name="ACTIVITY")
    expect(act).to_contain_text("02 · Markets")
    page.screenshot(path=out / "09-activity.png")
    page.keyboard.press("Escape")

    m = ctx.new_page()
    m.set_viewport_size({"width": 390, "height": 844})
    for path, name in ((f"/p/{pid}/markets", "10-mobile-shortlist"), (f"/p/{pid}/markets/all", "11-mobile-all80")):
        m.goto(base + path)
        m.locator("main h1").wait_for()
        m.wait_for_timeout(600)
        m.screenshot(path=out / f"{name}.png", full_page=True)
        check(f"390px: no horizontal page scroll ({name})", not m.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"))
    view = ctx.request.get(base + f"/api/projects/{pid}/markets").json()
    (out / "markets.json").write_text(json.dumps(view, indent=1))
    check("no JavaScript errors", not errors, "; ".join(errors[:2]))
    browser.close()
print(f"{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
