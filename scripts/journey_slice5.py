"""Slice-5 browser journey from S01 (BUILD-PLAN "Slice 5" §5.12 manifests, AC5-13).

usage: python -m scripts.journey_slice5 <app_base> <gotbag|untuckit> <out_dir>

Upstream: scripts.journey_slice65 for the same case (which replays slice 4.5 and everything before it from S01, then
signs off a first plan). Then, on the Plan through the UI:
gotbag (L5-A, Brisbane): Pop-up supports Community → founder amounts in A$ (stock; shipping shared by both) → Propose
  a plan (launch_strategist + strategist_check) → adopt option A → a ceiling change (0 model calls) → sign off → Act:
  the package's signed budget changed → Re-prepare (the recorded copy request) → .eml.
untuckit (L5-B, Minneapolis): Propose a plan → a digital scenario on Meta + Google with labelled assumptions (US$30 a
  day × 14 days) → TikTok added (below its minimum) and removed → sign off → Act → Re-prepare the campaign → the campaign
  package's platform plans carry each platform's own allocation.
Nothing is sent or created. Prints one line per check, writes screenshots and the API views; exits non-zero on a failure.
"""
import io
import json
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

base, case, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
results = []
CID, CITY = {"gotbag": ("brisbane", "Brisbane"), "untuckit": ("minneapolis", "Minneapolis")}[case]
WAIT = 600000


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f" · {detail}" if detail else ""), flush=True)


def ledger_lines():
    p = Path("live-captures-private/usage-ledger.jsonl")
    return sum(1 for _ in p.open()) if p.exists() else 0


def plan(ctx, pid):
    return ctx.request.get(f"{base}/api/projects/{pid}/plans/{CID}").json()


def money_line(page, label):
    return page.get_by_label("Budget", exact=True).locator(".cost-line", has_text=label).first


def enter(page, label, amount, ccy):
    row = money_line(page, label)
    row.get_by_role("button", name=re.compile("Enter amount|Change")).click()
    row.get_by_label(re.compile(f"{re.escape(label)} amount")).fill(amount)
    row.get_by_label("Currency").fill(ccy)
    row.get_by_role("button", name="Save").click()
    expect(money_line(page, label)).to_contain_text("YOU SAID", timeout=15000)


def propose(page, ctx, pid):
    page.get_by_role("button", name=re.compile("^Propose (a plan|again)")).click()
    end = time.monotonic() + 300
    while time.monotonic() < end:
        p = plan(ctx, pid)["proposal"] or {}
        if (p.get("run") or {}).get("status") in ("completed", "failed", "partial"):
            return p
        page.wait_for_timeout(1000)
    return plan(ctx, pid)["proposal"]


def check_proposal(p):
    ok = (p.get("run") or {}).get("status") == "completed" and p.get("options")
    check("the strategist proposed 1–2 options (live or replayed)", ok, (p.get("run") or {}).get("message") or
          f"{len(p.get('options') or [])} option(s), recommended {p.get('recommendation')}")
    if not ok:
        return
    facts = [s for sts in p["rationale"].values() for s in sts if s["kind"] == "FACT"]
    check("AC5-5 every FACT cites evidence; the rest are labelled assumptions",
          all(s["cites"] for s in facts), f"{len(facts)} facts, {sum(len(v) for v in p['rationale'].values()) - len(facts)} assumptions")
    lines = [ln for o in p["options"] for ln in o["cost_lines"]]
    check("AC5-7 assumed amounts carry a basis note; no amount is unlabelled",
          all(ln["basis_note"] for ln in lines if ln["basis"] in ("ASSUMED", "ESTIMATE")) and
          all(ln["amount"] is None for ln in lines if ln["basis"] in ("NOT_KNOWN", "NOT_COSTED")))
    check("AC5-15 each option costs every item of its included methods (never free by omission)", all(
        {f"{m['method']}:" for m in o["methods"] if m["role"] in ("MAIN", "SUPPORTS")} <=
        {ln["item_ref"].split(":")[0] + ":" for ln in o["cost_lines"] if ln["item_ref"]} for o in p["options"]))
    (out / "proposal.json").write_text(json.dumps(p, indent=1))


def sign(page, ctx, pid):
    dev = page.locator(".decision-bar .btn-primary", has_text="develop")
    if dev.count():
        dev.click()
    page.locator(".decision-bar .btn-primary", has_text="Sign off plan v").click(timeout=WAIT)
    page.wait_for_url(re.compile(f"/act/{CID}$"), timeout=60000)
    return plan(ctx, pid)["plan"]["version"]


def reprepare(page, ctx, pid, v1, v2):
    body = page.locator("main")
    expect(body).to_contain_text(f"Your signed plan's budget changed (v{v1} → v{v2})", timeout=30000)
    check("Act flags the package prepared from the earlier signed plan", True, f"v{v1} → v{v2}")
    page.screenshot(path=out / "20-act-stale.png", full_page=True)
    page.get_by_role("button", name=re.compile("^Re-prepare from your signed plan")).first.click()
    expect(body).not_to_contain_text("budget changed", timeout=WAIT)
    page.get_by_role("link", name="Review").first.click()
    page.wait_for_url(re.compile(r"/deliver/pkg_"), timeout=30000)
    return ctx.request.get(f"{base}/api/projects/{pid}/packages/{page.url.rsplit('/', 1)[1]}").json()


def mobile(ctx, pid, path, name):
    m = ctx.new_page()
    m.set_viewport_size({"width": 390, "height": 844})
    m.goto(base + path)
    m.locator("main h1").first.wait_for(timeout=120000)
    m.wait_for_timeout(600)
    m.screenshot(path=out / name, full_page=True)
    check(f"390px {path.rsplit('/', 2)[-2]}: no horizontal page scroll", not m.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"))
    m.close()


def gotbag(page, ctx, pid, v1):
    page.get_by_label("Role for Pop-up").select_option("SUPPORTS")
    expect(page.get_by_role("article", name="Pop-up")).to_contain_text("SUPPORTS THE MAIN TEST", timeout=15000)
    enter(page, "Product for the event", "300", "AUD")
    enter(page, "Shipping stock", "120", "AUD")
    row = money_line(page, "Shipping stock")
    row.get_by_role("button", name="Change").click()
    row.get_by_label("Community").click()  # saved at once; the box follows the saved plan
    expect(money_line(page, "Shipping stock")).to_contain_text("SHARED", timeout=15000)
    v = plan(ctx, pid)
    ship = next(ln for ln in v["money"]["lines"] if ln["label"] == "Shipping stock")
    check("AC5-3 the shared line exists once, used by Community and Pop-up", sorted(ship["used_by"]) == ["community", "popup"] and
          sum(ln["label"] == "Shipping stock" for ln in v["money"]["lines"]) == 1)
    check("AC5-2a A$ amounts are converted at a dated ECB rate (or shown not converted)",
          (v["money"]["fx"] and "ECB rates of" in v["money"]["fx"]["text"]) or v["money"]["needs_fx"], (v["money"]["fx"] or {}).get("text", "no rate"))
    check("AC5-2 never $0: known plus the count not known", re.search(r"\d+ not known", v["money"]["text"]) and "$0" not in v["money"]["text"], v["money"]["text"])
    page.screenshot(path=out / "10-plan-amounts.png", full_page=True)
    p = propose(page, ctx, pid)
    check_proposal(p)
    page.screenshot(path=out / "11-proposal.png", full_page=True)
    if (p.get("options") or []):
        pre = plan(ctx, pid)["plan"]["version"]
        page.get_by_label("Landfall's proposal").get_by_role("button", name="Adopt option A").click()
        expect(page.get_by_label("Landfall's proposal")).to_contain_text("ADOPTED", timeout=15000)
        v = plan(ctx, pid)
        mine = next(ln for ln in v["money"]["lines"] if ln["label"] == "Shipping stock")
        check("AC5-4 adopting writes Plan v+1 (source chosen); founder amounts win per item",
              v["plan"]["version"] == pre + 1 and mine["by"] == "founder" and mine["amount"] == {"minor": 12000, "ccy": "AUD"})
    before, lines = plan(ctx, pid), ledger_lines()
    page.locator(".context-strip").get_by_role("button", name="Edit").click()
    page.get_by_role("dialog", name="YOUR GOAL").get_by_role("button", name=re.compile("^Budget:")).click()
    page.get_by_role("option", name="US$10,000").click()
    page.keyboard.press("Escape")
    after = plan(ctx, pid)
    check("AC5-1 a ceiling change recalculates fit with 0 model calls", after["money"]["ceiling"]["minor"] == 1000000
          and ledger_lines() == lines and after["plan"]["version"] == before["plan"]["version"], f"{before['money']['fit']} → {after['money']['fit']}")
    page.screenshot(path=out / "12-plan-after-ceiling.png", full_page=True)
    mobile(ctx, pid, f"/p/{pid}/plan/{CID}", "13-plan-390.png")
    v2 = sign(page, ctx, pid)
    signed = plan(ctx, pid)["signed"]
    check("the signed snapshot holds the plan's money", signed["version"] == v2 and signed["money"]["text"], signed["money"]["text"])
    pkg = reprepare(page, ctx, pid, v1, v2)
    check("re-prepare writes a new version from the signed plan (a new approval is needed)",
          pkg["plan_ref"]["version"] == v2 and pkg["selection"]["state"] == "IN_SIGNED_PLAN", f"package v{pkg['version']}")
    with page.expect_download() as d:
        page.get_by_role("link", name="Download .eml").click()
    eml = open(d.value.path(), "rb").read()
    check("the .eml downloads; nothing is sent", b"Subject:" in eml and not [a for x in pkg["destination_states"] for a in x["actions"]])
    page.screenshot(path=out / "21-package.png", full_page=True)
    return pkg


RATES = {"META_ADS": ("8", "10", "12", "0.008", "0.01", "0.012", "0.02", "0.03", "0.04"),
         "GOOGLE_ADS": ("20", "30", "40", "0.02", "0.03", "0.04", "0.03", "0.04", "0.05")}


def untuckit(page, ctx, pid, v1):
    p = propose(page, ctx, pid)
    check_proposal(p)
    page.screenshot(path=out / "11-proposal.png", full_page=True)
    card = page.get_by_label("Digital scenario")
    card.get_by_label(re.compile("Total daily budget")).fill("30")
    card.get_by_label("Days").fill("14")
    for pl, label in (("META_ADS", "Meta"), ("GOOGLE_ADS", "Google")):
        if not card.get_by_label(label, exact=True).is_checked():
            card.get_by_label(label, exact=True).check()
    if card.get_by_label("TikTok", exact=True).is_checked():
        card.get_by_label("TikTok", exact=True).uncheck()
    for pl, label in (("META_ADS", "Meta"), ("GOOGLE_ADS", "Google")):
        vals = RATES[pl]
        for i, (k, j) in enumerate((k, j) for k in ("cpm", "ctr", "cvr") for j in ("low", "mid", "high")):
            card.get_by_label(f"{label} {k} {j}").fill(vals[i])
    card.get_by_role("button", name="Save scenario").click()
    expect(page.get_by_label("Digital scenario")).to_contain_text("Media spend US$420", timeout=15000)
    s = plan(ctx, pid)["scenario"]
    rows = {r["platform"]: r for r in s["results"]["platforms"]}
    check("AC5-8 US$30 × 14 → US$420 media spend, US$15 a day each on Meta and Google",
          s["media_spend"] == {"minor": 42000, "ccy": "USD"} and [r["allocation_text"] for r in rows.values()] == ["US$15", "US$15"])
    check("AC5-8 rates stay on their platform; each input labelled ASSUMED; reach null without frequency",
          rows["META_ADS"]["outputs"]["impressions"]["mid"] == 21000 and rows["GOOGLE_ADS"]["outputs"]["impressions"]["mid"] == 7000
          and all(r["outputs"]["unique_reach"] is None for r in rows.values())
          and {st for x in s["platforms"] for k, st in x["input_states"].items() if k != "frequency"} == {"ASSUMED"})
    check("AC5-8 a sourced or benchmark scenario: not demonstrated (D2 open; P5-1 had no Meta connection)", True,
          "recorded as a gap in the report, not a pass")
    page.screenshot(path=out / "14-scenario.png", full_page=True)
    card = page.get_by_label("Digital scenario")
    card.get_by_label("TikTok", exact=True).check()
    card.get_by_role("button", name="Save scenario").click()
    expect(page.get_by_label("Digital scenario")).to_contain_text("BLOCKS EXPORT", timeout=15000)
    s = plan(ctx, pid)["scenario"]
    check("destination change: TikTok added → US$10 each, TikTok below its US$20 minimum",
          [r["allocation_text"] for r in s["results"]["platforms"]] == ["US$10", "US$10", "US$10"] and
          s["results"]["platforms"][2]["below_minimum"] and s["results"]["note"] == "2 of 3 platforms have rates")
    page.screenshot(path=out / "15-scenario-tiktok.png", full_page=True)
    card = page.get_by_label("Digital scenario")
    card.get_by_label("TikTok", exact=True).uncheck()
    card.get_by_role("button", name="Save scenario").click()
    expect(page.get_by_label("Digital scenario")).not_to_contain_text("BLOCKS EXPORT", timeout=15000)
    mobile(ctx, pid, f"/p/{pid}/plan/{CID}", "16-scenario-390.png")
    v2 = sign(page, ctx, pid)
    pkg = reprepare(page, ctx, pid, v1, v2)
    check("AC5-9 the package is prefilled from the signed plan's allocations",
          pkg["budget"]["source"] == "PLAN" and pkg["budget_view"]["split"] == {"META_ADS": "US$15", "GOOGLE_ADS": "US$15"}, json.dumps(pkg["budget_view"]))
    expect(page.locator("main")).to_contain_text("From your signed plan", timeout=15000)
    with page.expect_download(timeout=60000) as d:
        page.get_by_role("link", name="Download the campaign package").click()
    zf = zipfile.ZipFile(io.BytesIO(Path(d.value.path()).read_bytes()))
    budgets = {d: next((x["value"] for x in json.loads(zf.read(f"plans/{d}.json"))["values"] if x["field"] == "Daily budget"), None)
               for d in ("meta_ads", "google_ads")}
    check("each export's budget is its own allocation (Meta 1500 minor units, Google 15,000,000 micros)",
          budgets == {"meta_ads": "1500 minor units of USD", "google_ads": "15000000 micros of USD"}, json.dumps(budgets))
    page.screenshot(path=out / "21-package.png", full_page=True)
    return pkg


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    up = out / "upstream65"
    rc = subprocess.run([sys.executable, "-m", "scripts.journey_slice65", base, case, str(up)])
    check("upstream 6.5 journey from S01 (replayed)", rc.returncode == 0)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, storage_state=str(up / "upstream45" / "state.json"), accept_downloads=True)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    pid = (up / "upstream45" / "upstream" / "project_url.txt").read_text().strip().rsplit("/p/", 1)[1]
    page.goto(f"{base}/p/{pid}/plan/{CID}")
    page.get_by_label("Budget", exact=True).wait_for(timeout=60000)
    v1 = plan(ctx, pid)["plan"]["signed_version"]
    pkg = gotbag(page, ctx, pid, v1) if case == "gotbag" else untuckit(page, ctx, pid, v1)
    (out / "plan.json").write_text(json.dumps(plan(ctx, pid), indent=1))
    (out / "package.json").write_text(json.dumps(pkg, indent=1))
    mobile(ctx, pid, f"/p/{pid}/deliver/{pkg['id']}", "22-package-390.png")
    (out / "project.json").write_text(json.dumps({"id": pid, "package_id": pkg["id"]}, indent=1))
    check("no JavaScript errors", not errors, "; ".join(errors[:2]))
    browser.close()
print(f"{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
