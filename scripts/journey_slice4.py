"""Slice-4 browser journey through the real UI, from S01 (BUILD-PLAN slice 4, live cases L4-A / L4-B).

usage: python -m scripts.journey_slice4 <app_base> <untuckit|gotbag> <out_dir>

Explicit action manifests (no imported records, no acceptance-walkthrough state):
untuckit (L4-A): S01 "both" · presence → untuckit.com → brief → Start exploring markets → recommendations → untick Helsinki
  and Dublin, add Copenhagen (the shortlist endpoint: 6.5 removed those Markets controls) → remove the audience "Baseball"
  → research with the note "We won't do pop-ups this year." (the research endpoint) → S07 → S08 → S09. Research set: minneapolis, montreal, copenhagen.
gotbag (L4-B): the L2-B founder steps (scripts.journey_identity: Neither, remove Travel, add Ocean, confirm) → purpose
  "test demand before committing" on the brief → Start → recommendations → untick Sydney, Melbourne, Auckland, Perth →
  Research. Research set: brisbane, lisbon.
Prints one line per check, writes screenshots and opportunities.json; exits non-zero on a failure.
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
RECOMMENDED = re.compile(r"^Start in |No city recommended yet")  # 6.5 v4 05
EXPECT = {"untuckit": {"minneapolis", "montreal", "copenhagen"}, "gotbag": {"brisbane", "lisbon"}}
UNTICK = {"untuckit": ["Helsinki", "Dublin"], "gotbag": ["Sydney", "Melbourne", "Auckland", "Perth"]}


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
        page.get_by_label("Your brand's website").fill("untuckit.com")
        page.keyboard.press("Enter")
        page.wait_for_url(re.compile(r"/p/prj_"), timeout=15000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    chooser = page.get_by_role("button", name="This is us")
    page.get_by_role("heading", name=BRIEF).or_(chooser).first.wait_for(timeout=300000)
    if chooser.count():
        page.get_by_role("radio", name=re.compile("untuckit", re.I)).first.check()
        chooser.click()
        page.get_by_role("heading", name=BRIEF).wait_for(timeout=300000)
    pid = page.url.split("/p/")[1].split("/")[0]
    (out / "project_url.txt").write_text(base + f"/p/{pid}")
    if case == "gotbag":
        pick(page, "Why", "test demand before committing")
        page.locator(".notice").first.wait_for(timeout=10000)
        page.get_by_text("Brands with a similar following:").wait_for(timeout=120000)
    page.locator(".audience").first.get_by_text(re.compile("cultural relationship")).wait_for(timeout=120000)
    page.get_by_role("button", name="Start exploring markets").click()
    page.wait_for_url(re.compile(r"/markets/scan$"), timeout=15000)
    page.get_by_role("button", name="See the recommendations", exact=True).wait_for(timeout=900000)
    page.get_by_role("button", name="See the recommendations", exact=True).click()
    page.get_by_role("heading", name=RECOMMENDED).wait_for(timeout=600000)
    page.wait_for_timeout(800)
    # 6.5 removed Markets' research ticks and the multi-city start panel. The locked L4 manifest still needs the same
    # shortlist and research run, so its edits go through the same endpoints the old controls called.
    mk = ctx.request.get(base + f"/api/projects/{pid}/markets").json()
    ids = {c["name"]: c["city_id"] for c in mk["cities"]}
    r = ctx.request.patch(base + f"/api/projects/{pid}/shortlist", data={
        "base_version": mk["shortlist_version"], "exclude": [ids[c] for c in UNTICK[case]],
        "add": ["copenhagen"] if case == "untuckit" else []})
    check("untick " + ", ".join(UNTICK[case]) + (" · add Copenhagen (ADDED BY YOU)" if case == "untuckit" else ""), r.ok, r.text()[:200])
    page.reload()
    page.get_by_role("heading", name=RECOMMENDED).wait_for()
    if case == "untuckit":
        rm = page.get_by_role("button", name="Remove the audience Baseball")
        check("the audience Baseball is offered before its removal", rm.count() > 0)
        if rm.count():
            rm.first.click()
            page.get_by_text(re.compile("You removed: .*Baseball")).first.wait_for(timeout=10000)
            check("remove the audience Baseball (after the recommendations)", True)
    page.screenshot(path=out / "01-recommendations.png", full_page=True)

    start = ctx.request.get(base + f"/api/projects/{pid}/opportunities").json()["start"]
    check("research states expected and maximum cost per city", (start["expected_usd"], start["max_usd"]) == (0.36, 0.80),
          f"{start['expected_usd']} / {start['max_usd']}")
    r = ctx.request.post(base + f"/api/projects/{pid}/research", data={
        "city_ids": start["new"], "context": "We won't do pop-ups this year." if case == "untuckit" else None})
    check("research starts for the shortlisted cities", r.ok, r.text()[:200])
    page.goto(base + f"/p/{pid}/opportunities/research")
    page.get_by_role("heading", name=re.compile("Checking leads and drafting")).wait_for(timeout=60000)
    page.wait_for_timeout(20000)
    page.screenshot(path=out / "03-research-underway.png", full_page=True)
    page.get_by_role("button", name="Review approaches").wait_for(timeout=1800000)
    page.screenshot(path=out / "04-research-done.png", full_page=True)
    page.get_by_role("button", name="Review approaches").click()
    page.get_by_role("heading", name=re.compile(r"approach(es)? across \d+ cit")).wait_for(timeout=30000)
    page.wait_for_timeout(800)
    page.screenshot(path=out / "05-approaches.png", full_page=True)

    o = ctx.request.get(base + f"/api/projects/{pid}/opportunities").json()
    (out / "opportunities.json").write_text(json.dumps(o, indent=1))
    got = {c["city_id"] for c in o["cities"] if c["version"]}
    check("the research set matches the manifest", got == EXPECT[case], ", ".join(sorted(got)))
    for c in o["cities"]:
        if c["version"]:
            check(f"{c['name']}: all five methods assessed", set(c["matrix"]) == {"retail", "creator", "popup", "community", "digital"},
                  " · ".join(f"{k} {v['status']}" for k, v in c["matrix"].items()))
    if case == "untuckit":
        cph = next((c for c in o["cities"] if c["city_id"] == "copenhagen"), None)
        check("Copenhagen researched as ADDED_BY_YOU with no recommendation", cph and cph["selection"] == "ADDED_BY_YOU"
              and not (cph.get("input_summary") or {}).get("position_line"))
        pops = [c["matrix"].get("popup", {}).get("status") for c in o["cities"] if c["version"]]
        check("pop-up is NOT SUITABLE NOW where the note was used", all(s == "NOT_SUITABLE_NOW" for s in pops), ", ".join(map(str, pops)))
    check("affinity never reaches the view", "affinity" not in json.dumps(o))
    print(f"INFO READY approaches in this case: {json.dumps(o['title'])} (the success criterion is judged across both cases, "
          "in human review)", flush=True)
    cards = page.locator("article.approach")
    if cards.count():
        first = cards.first
        first.get_by_role("button", name="Why this idea?").click()
        ev = first.get_by_role("button", name=re.compile("SUPPORTED"))
        if ev.count():
            ev.first.click()
            drawer = page.get_by_role("dialog", name="EVIDENCE")
            expect(drawer).to_contain_text("Quote on the page")
            page.screenshot(path=out / "06-evidence.png", full_page=True)
            page.keyboard.press("Escape")
        partner = first.locator(".partner-row .link")
        if partner.count():
            partner.first.click()
            expect(page.get_by_role("dialog", name="PARTNER")).to_contain_text("unknown until you ask")
            page.screenshot(path=out / "07-partner.png", full_page=True)
            page.keyboard.press("Escape")
    page.reload()
    page.get_by_role("heading", name=re.compile(r"across \d+ cit")).wait_for(timeout=30000)
    check("refresh keeps the approaches", True)
    m = ctx.new_page()
    m.set_viewport_size({"width": 390, "height": 844})
    m.goto(base + f"/p/{pid}/opportunities")
    m.locator("main h1").first.wait_for()
    m.wait_for_timeout(600)
    m.screenshot(path=out / "08-mobile-approaches.png", full_page=True)
    check("390px: no horizontal page scroll", not m.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"))
    check("no JavaScript errors", not errors, "; ".join(errors[:2]))
    if "--state" in sys.argv:  # slice 4.5's journey continues in this browser session
        ctx.storage_state(path=sys.argv[sys.argv.index("--state") + 1])
    browser.close()
print(f"{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
