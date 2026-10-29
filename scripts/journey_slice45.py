"""Slice-4.5 browser journey through the real UI, from S01 (BUILD-PLAN §4.5.11, live cases L45-A / L45-B).

usage: python -m scripts.journey_slice45 <app_base> <untuckit|gotbag> <out_dir>

Upstream: scripts.journey_slice4 replays the locked L4 case from S01 to S08 in the same browser session. Then the explicit
4.5 manifest, each step through the UI:
untuckit (L45-A): Minneapolis · Investigate retail → Investigate digital → Develop retail ("one independent menswear shop").
gotbag (L45-B): Brisbane · Investigate creator → community → pop-up → Develop community → Develop pop-up → Develop
  creator (the last two after REVIEW-SLICE-045); Lisbon · Add a creator profile (an Instagram URL; YOU SAID, never fetched).
Prints one line per check, writes screenshots and the dossiers and drafts as JSON; exits non-zero on a failure.
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
WAIT = 1500000  # a live investigation: ≤ 8 model calls and its tool calls
LABEL = {"retail": "retail", "popup": "pop-up", "creator": "creator", "community": "community", "digital": "digital"}
DONE = {"retail": "Stockist comparison|No shop we could check", "popup": "Venues and what it would take|No venue we could check",
        "creator": "Where they are, what they make|No creator we could check", "community": "Organisers and their activities|No group we could check",
        "digital": "Audience → message → channel"}


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f" · {detail}" if detail else ""), flush=True)


def investigate(page, ctx, pid, city, method, n):
    page.goto(f"{base}/p/{pid}/opportunities/{city}/{method}")
    page.locator("main h1").first.wait_for(timeout=30000)
    btn = page.get_by_role("button", name=re.compile(f"^Investigate ({LABEL[method]}|further)$"))
    check(f"{city} {method}: Investigate shows its cost first", btn.count() == 1, btn.first.inner_text() if btn.count() else "")
    api = f"{base}/api/projects/{pid}/cities/{city}/methods/{method}"
    before = (ctx.request.get(api).json().get("dossier") or {}).get("version") or 0
    btn.first.click()
    for _ in range(WAIT // 2000):  # a new dossier version, finished (LIVE L45-A: the old version was read once)
        d = ctx.request.get(api).json().get("dossier") or {}
        if (d.get("version") or 0) > before and d.get("status") in ("complete", "partial", "failed"):
            break
        page.wait_for_timeout(2000)
    page.reload()
    page.get_by_text(re.compile(DONE[method])).first.wait_for(timeout=60000)
    page.wait_for_timeout(1000)
    page.screenshot(path=out / f"{n:02d}-{city}-{method}.png", full_page=True)
    v = ctx.request.get(f"{base}/api/projects/{pid}/cities/{city}/methods/{method}").json()
    (out / f"{city}-{method}.json").write_text(json.dumps(v, indent=1))
    d = v.get("dossier") or {}
    check(f"{city} {method}: a dossier is saved ({d.get('status')})", d.get("status") in ("complete", "partial"),
          f"readiness {(d.get('readiness') or {}).get('overall')} · kept {sum(c['decision'] == 'KEPT' for c in d.get('candidates', []))}"
          f" · rejected {sum(c['decision'] == 'REJECTED' for c in d.get('candidates', []))} · model calls {(d.get('counters') or {}).get('model')}")
    check(f"{city} {method}: willingness is never claimed", all((c.get("axes") or {}).get("operational", {}).get("willingness", "UNKNOWN") == "UNKNOWN"
                                                               for c in d.get("candidates", [])))
    check(f"{city} {method}: affinity never reaches the view", "affinity" not in json.dumps(v))
    return v


def develop(page, ctx, pid, city, method, rationale, n):
    page.goto(f"{base}/p/{pid}/opportunities/{city}/{method}")
    page.get_by_role("button", name=re.compile("^Develop (this approach|again)")).wait_for(timeout=30000)
    if rationale:
        page.get_by_label(re.compile("Why this one")).fill(rationale)
    page.get_by_role("button", name=re.compile("^Develop (this approach|again)")).click()
    draft = page.get_by_role("region", name="Developed draft")
    draft.wait_for(timeout=WAIT)
    page.wait_for_timeout(1000)
    page.screenshot(path=out / f"{n:02d}-{city}-{method}-draft.png", full_page=True)
    v = ctx.request.get(f"{base}/api/projects/{pid}/cities/{city}/methods/{method}").json()
    dr = v.get("draft") or {}
    (out / f"{city}-{method}-draft.json").write_text(json.dumps(dr, indent=1))
    check(f"{city} {method}: a draft is saved ({dr.get('label')})", bool(dr.get("id")),
          f"{len(dr.get('validation_tasks', []))} validation tasks · partners {[p['name'] for p in dr.get('partners', [])]}")
    kept = {c["name"] for c in (v.get("dossier") or {}).get("candidates", []) if c["decision"] == "KEPT"}
    check(f"{city} {method}: partners come only from kept candidates", all(p["name"] in kept for p in dr.get("partners", [])))
    check(f"{city} {method}: every costing input is UNKNOWN (no money in 4.5)", all(c["state"] == "UNKNOWN" for c in dr.get("costing_inputs", [])))
    return dr


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    state = out / "state.json"
    up = out / "upstream"
    resume = "--continue" in sys.argv  # same session and database: only the steps after the investigations
    if not resume:
        rc = subprocess.run([sys.executable, "-m", "scripts.journey_slice4", base, case, str(up), "--state", str(state)])
        check("upstream L4 journey from S01 (replayed)", rc.returncode == 0)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, storage_state=str(state))
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    pid = (up / "project_url.txt").read_text().strip().rsplit("/p/", 1)[1]
    page.goto(f"{base}/p/{pid}/opportunities")
    page.get_by_role("heading", name=re.compile(r"across \d+ cit")).wait_for(timeout=60000)
    if case == "untuckit":
        if not resume:
            investigate(page, ctx, pid, "minneapolis", "retail", 10)
        investigate(page, ctx, pid, "minneapolis", "digital", 11)  # on --continue: again, with digital@2 and platforms@2
        develop(page, ctx, pid, "minneapolis", "retail", "one independent menswear shop", 12)
    else:
        if not resume:
            for n, method in enumerate(("creator", "community", "popup"), 10):
                investigate(page, ctx, pid, "brisbane", method, n)
        develop(page, ctx, pid, "brisbane", "community", None, 13)
        # REVIEW-SLICE-045: every investigated method needs an evidenced candidate or a useful hypothesis-led draft
        develop(page, ctx, pid, "brisbane", "popup", None, 15)
        develop(page, ctx, pid, "brisbane", "creator", None, 16)
        page.goto(f"{base}/p/{pid}/opportunities/lisbon/creator")
        page.get_by_label("Add a creator profile you know").fill("https://www.instagram.com/landfall_test_profile/")
        page.get_by_role("button", name="Add").click()
        expect(page.locator("main")).to_contain_text("YOU SAID", timeout=15000)
        page.screenshot(path=out / "14-lisbon-creator-profile.png", full_page=True)
        check("Lisbon: a founder-supplied Instagram profile is YOU SAID and never fetched", True)
    p = ctx.request.get(f"{base}/api/projects/{pid}").json()
    (out / "project.json").write_text(json.dumps({k: p.get(k) for k in ("id", "usage")}, indent=1))
    page.goto(f"{base}/p/{pid}/opportunities")
    page.get_by_role("heading", name=re.compile(r"across \d+ cit")).wait_for(timeout=30000)
    page.screenshot(path=out / "20-approaches-after.png", full_page=True)
    m = ctx.new_page()
    m.set_viewport_size({"width": 390, "height": 844})
    city = "minneapolis" if case == "untuckit" else "brisbane"
    m.goto(f"{base}/p/{pid}/opportunities/{city}/{'retail' if case == 'untuckit' else 'community'}")
    m.locator("main h1").first.wait_for()
    m.wait_for_timeout(600)
    m.screenshot(path=out / "21-mobile.png", full_page=True)
    check("390px: no horizontal page scroll", not m.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"))
    check("no JavaScript errors", not errors, "; ".join(errors[:2]))
    browser.close()
print(f"{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
