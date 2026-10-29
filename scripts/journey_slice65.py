"""Slice-6.5 browser journey through the v4 stages, from S01 (BUILD-PLAN 6.5 UI.9 AC1, AC3, AC6, AC7).

usage: python -m scripts.journey_slice65 <app_base> <gotbag|untuckit> <out_dir>

untuckit (slice 5, new): L45-A replayed from S01 → Markets → Build a plan for Minneapolis → Digital as the Main test →
develop it (the recorded L6-B develop) → sign off → Act → Prepare the campaign (the recorded L6-B copy) → the package.

Upstream: scripts.journey_slice45 replays L45-B (and through it L4-B, L35-B, L2-B, L1-B) from S01 in one browser session.
Then, through the new UI: Markets → Build a plan for Brisbane → Plan with Community as the Main test → sign off →
Act → a message to someone you'll name yourself (the recorded L6-A request, so it replays) → the package review with
"Direct delivery unavailable" and a .eml download; and a ceiling edit that starts nothing. Choosing Club KT, a PENDING
candidate, needs a new copy request, so on replay it is shown, not prepared (AC5 is SYNTHETIC, UI.9).
Prints one line per check, writes screenshots; exits non-zero on a failure.
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
CITY = {"gotbag": ("brisbane", "Brisbane", "community", "Community"),
        "untuckit": ("minneapolis", "Minneapolis", "digital", "Digital")}[case]


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f" · {detail}" if detail else ""), flush=True)


def ledger_lines():
    p = Path("live-captures-private/usage-ledger.jsonl")
    return sum(1 for _ in p.open()) if p.exists() else 0


def outreach(page, ctx, pid, cid):
    # Act: the PENDING candidate is labelled; the recorded no-partner package replays
    body = page.locator("main")
    expect(body).to_contain_text("Choose organisers", timeout=30000)
    expect(body).to_contain_text(re.compile("couldn't confirm this fit|No candidate was confirmed"), timeout=30000)
    pending = "couldn't confirm this fit" in body.inner_text()
    check("recipients: a PENDING candidate is labelled, or no candidate is offered", True,
          "PENDING shown as 'Landfall couldn't confirm this fit'" if pending else "no kept or pending candidate in this replay")
    page.get_by_role("button", name="Prepare a message to someone you'll name yourself").click()
    expect(body).to_contain_text("Outreach", timeout=120000)
    page.screenshot(path=out / "03-act.png", full_page=True)
    page.get_by_role("link", name="Review").first.click()
    page.wait_for_url(re.compile(r"/deliver/pkg_"), timeout=30000)
    gmail = page.get_by_role("article", name="Gmail")
    expect(gmail).to_be_visible(timeout=60000)
    check("AC7 Gmail is SIMULATED in replay, never a live send", "SIMULATED" in gmail.inner_text(),
          gmail.inner_text()[:120].replace("\n", " "))
    with page.expect_download() as d:
        page.get_by_role("link", name="Download .eml").click()
    eml = open(d.value.path(), "rb").read()
    pkg = ctx.request.get(f"{base}/api/projects/{pid}/packages/{page.url.rsplit('/', 1)[1]}").json()
    acts = [a for d in pkg["destination_states"] for a in d["actions"]]
    check("AC7 the .eml downloads and no send is recorded", b"Subject:" in eml and not acts, f"{len(eml)} bytes")
    page.screenshot(path=out / "04-package.png", full_page=True)
    page.get_by_role("button", name="← Back to Act").click()
    page.wait_for_url(re.compile(f"/act/{cid}$"))


def campaign(page, ctx, pid):
    """Act → Prepare the campaign (the recorded L6-B copy) → the package: SIMULATED platforms, a campaign package."""
    body = page.locator("main")
    expect(body).to_contain_text("Platforms and settings", timeout=30000)
    page.get_by_role("button", name="Prepare the campaign").click()
    page.wait_for_url(re.compile(r"/deliver/pkg_"), timeout=30000)
    meta = page.get_by_role("article", name="Meta Ads")
    expect(meta).to_be_visible(timeout=300000)
    check("the ad platforms are SIMULATED in replay, never a live write", "SIMULATED" in meta.inner_text())
    page.screenshot(path=out / "04-package.png", full_page=True)
    page.get_by_role("button", name="← Back to Act").click()
    page.wait_for_url(re.compile(r"/act/minneapolis$"))


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    up = out / "upstream45"
    rc = subprocess.run([sys.executable, "-m", "scripts.journey_slice45", base, case, str(up)])
    check("upstream L45 journey from S01 (replayed)", rc.returncode == 0)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, storage_state=str(up / "state.json"), accept_downloads=True)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    pid = (up / "upstream" / "project_url.txt").read_text().strip().rsplit("/p/", 1)[1]
    cid, city, method, label = CITY

    # Markets: the stage bar locks Plan until a beachhead; the city becomes the beachhead
    page.goto(f"{base}/p/{pid}/markets?sel={cid}")
    nav = page.get_by_role("navigation", name="Stages")
    nav.locator(".stage-label").first.wait_for(timeout=60000)
    check("four stages: Brand · Markets · Plan · Act", nav.locator(".stage-label").all_inner_texts() == ["Brand", "Markets", "Plan", "Act"])
    primary = page.locator(".decision-bar .btn-primary")
    expect(primary).to_have_text(f"Build a plan for {city}", timeout=30000)
    page.screenshot(path=out / "01-markets.png", full_page=True)
    before = ledger_lines()
    primary.click()
    page.wait_for_url(re.compile(f"/plan/{cid}$"), timeout=30000)
    v = ctx.request.get(f"{base}/api/projects/{pid}").json()
    check(f"{city} is the beachhead", (v.get("beachhead") or {}).get("city_id") == cid, json.dumps(v.get("beachhead"))[:160])

    # AC3: a ceiling edit writes FounderInput v+1 and starts nothing
    fi = v["intent"]["version"]
    page.locator(".context-strip").get_by_role("button", name="Edit").click()
    page.get_by_role("dialog", name="YOUR GOAL").get_by_role("button", name=re.compile("^Budget:")).click()
    page.get_by_role("option", name="US$5,000").click()
    expect(page.get_by_role("dialog", name="YOUR GOAL")).to_contain_text("nothing re-runs", timeout=10000)
    page.keyboard.press("Escape")
    v = ctx.request.get(f"{base}/api/projects/{pid}").json()
    check("AC3 ceiling → US$5,000, YOU SAID, no ledger dispatch",
          (v["intent"]["version"], v["intent"]["ceiling_usd"], v["intent"]["ceiling_by"]) == (fi + 1, 5000, "YOU_SAID") and ledger_lines() == before)

    # Plan: AC6 honest unknowns, then the Main test and sign-off
    plan = ctx.request.get(f"{base}/api/projects/{pid}/plans/{cid}").json()
    not_inv = [m["label"] for m in plan["methods"] if m["investigation"]["status"] == "not_investigated"]
    if case == "gotbag":
        check("AC6 retail and digital are 'Not investigated'", {"Retail", "Digital"} <= set(not_inv), ", ".join(not_inv))
    page.get_by_label(f"Role for {label}").select_option("MAIN")
    budget = page.get_by_label("Budget", exact=True)
    expect(budget).to_contain_text("not known", timeout=10000)
    main = page.locator("main").inner_text()
    zero = re.search(r"\$0(?:\.00)?(?![\d.])", main)  # "≤ $0.40" is a cost cap, not a total
    check("AC6 (S5-29) money states: '+ N not known', the fit shown, never $0",
          re.search(r"\d+ not known", budget.inner_text()) and "FIT UNRESOLVED" in budget.inner_text() and not zero, zero and zero.group(0))
    expect(page.get_by_role("article", name=label)).to_contain_text("SELECTED BY YOU", timeout=10000)
    page.screenshot(path=out / "02-plan.png", full_page=True)
    dev = page.locator(".decision-bar .btn-primary", has_text="develop")
    if dev.count():  # Develop first (P9): the plan stays unsigned while the draft is written
        dev.click()
    page.locator(".decision-bar .btn-primary", has_text="Sign off plan v").click(timeout=300000)
    page.wait_for_url(re.compile(f"/act/{cid}$"), timeout=30000)
    plan = ctx.request.get(f"{base}/api/projects/{pid}/plans/{cid}").json()
    pin = next(m for m in plan["methods"] if m["method"] == method)["role"]
    check("sign-off pins the dossier and draft versions shown", bool(plan["plan"]["signed_off"] and pin["draft"] and pin["dossier"]),
          json.dumps({"dossier": pin["dossier"], "draft": pin["draft"]}))

    if case == "gotbag":
        outreach(page, ctx, pid, cid)
    else:
        campaign(page, ctx, pid)
    check("no page errors", errors == [], "; ".join(errors)[:200])
    browser.close()

print(f"{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
