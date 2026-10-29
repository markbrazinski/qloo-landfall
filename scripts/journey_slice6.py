"""Slice-6 browser journey through the real UI, from S01 (BUILD-PLAN §6.3, cumulative preparation cases L6-A / L6-B).

usage: python -m scripts.journey_slice6 <app_base> <gotbag|untuckit> <out_dir>

Upstream: scripts.journey_slice45 replays the 4.5 case from S01 in the same browser session (which replays slice 4 first).
Then the pre-slice-5 draft route, each step through the UI:
gotbag (L6-A, outreach): Brisbane community draft → Prepare outreach → the designated test recipient and a sender (YOU SAID)
  → flagged sentences removed → approve (for your own use) → .eml and outreach package downloaded and their bytes inspected
  → subject edited → the approval is outdated → approve again → reload.
untuckit (L6-B, digital): Minneapolis digital → Develop → Prepare campaign → budget needed → a daily budget and start (YOU SAID)
  → the request plans per ad platform (account values unavailable without a connection) → campaign package and assets
  downloaded and inspected → setup steps.
Nothing is sent or created: this deployment replays the upstream, so direct delivery is SIMULATED and is never used here.
Writes screenshots, the package views and the downloaded files' digests; exits non-zero on a failure.
"""
import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from landfall import config

base, case, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
results = []
WAIT = 600000


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f" · {detail}" if detail else ""), flush=True)


def package(ctx, pid, pkg_id):
    return ctx.request.get(f"{base}/api/projects/{pid}/packages/{pkg_id}").json()


def download(page, name, n):
    with page.expect_download(timeout=60000) as d:
        page.get_by_role("link", name=name).click()
    data = Path(d.value.path()).read_bytes()
    (out / f"{n:02d}-{d.value.suggested_filename}").write_bytes(data)
    return data


def prepare(page, ctx, pid, city, method, n):
    page.goto(f"{base}/p/{pid}/opportunities/{city}/{method}")
    draft = page.get_by_role("region", name="Developed draft")
    if method == "digital" and draft.count() == 0:  # L6-B: the digital outline is developed here (one live call)
        page.get_by_role("button", name=re.compile("^Develop (this approach|again)")).click(timeout=30000)
        draft.wait_for(timeout=WAIT)
        check(f"{city} {method}: Develop wrote a DigitalTestOutline draft", True)
    draft.wait_for(timeout=60000)
    label = "Prepare campaign" if method == "digital" else "Prepare outreach"
    draft.get_by_role("button", name=label).click()
    page.wait_for_url(re.compile(r"/deliver/pkg_"), timeout=30000)
    page.get_by_role("heading", name="Where this can go").wait_for(timeout=WAIT)
    page.wait_for_timeout(800)
    pkg_id = page.url.rsplit("/", 1)[1]
    v = package(ctx, pid, pkg_id)
    page.screenshot(path=out / f"{n:02d}-{city}-{method}-package-v{v['version']}.png", full_page=True)
    (out / f"package-v{v['version']}.json").write_text(json.dumps(v, indent=1))
    segs = [s for k in ("segments", "headlines", "descriptions") for s in v["copy"].get(k) or []] + \
        [v["copy"][k] for k in ("subject", "primary_text", "ad_text") if v["copy"].get(k)]
    check(f"{label}: package v{v['version']} saved from {v['draft']['label']} v{v['draft']['version']}", v.get("version") == 1,
          f"locale {v['copy']['locale']} · {len(segs)} segments · {len(v['assets'])} images · faults {len(v['faults'])}")
    states = {s["state"] for s in segs}
    check("every segment is judged (SUPPORTED, PROPOSAL or TO CHECK; none unchecked)", segs and "UNCHECKED" not in states,
          ", ".join(f"{s['state']}: {s['text'][:50]}" for s in segs[:8]))
    check("a SUPPORTED segment cites a checked claim or product", all(s["claim_ids"] for s in segs if s["state"] == "SUPPORTED"))
    check("Qloo interests are labelled and never in a request plan", all("qloo" not in json.dumps(d["plan"]["requests"]).lower()
                                                                         for d in v["destination_states"]))
    check("the brand's own images keep their source and an unconfirmed-rights label",
          all(a["rights"] == "brand_own_unconfirmed" and a["source"].get("url") for a in v["assets"]),
          "; ".join(f"{a['filename']} {a['artifact']['sha256'][:12]}" for a in v["assets"]) or
          "; ".join(f"{x['product']}: {x['outcome']} {x.get('reason', '')}" for x in v["asset_attempts"]))
    return v


def outreach(page, ctx, pid):
    v = prepare(page, ctx, pid, "brisbane", "community", 30)
    gmail = page.get_by_role("article", name="Gmail")
    missing = {m["field"] for d in v["destination_states"] for m in d["state"]["missing"]}
    check("the sender is needed before approval (NEEDS INFORMATION, never defaulted)", "from_name" in missing)
    check("the recipient: from the partner's own site, or needed", bool(v["recipients"]["to"]) == ("to" not in missing),
          f"to {v['recipients_by'].get('to') or 'missing'}")
    check("budget and selection are shown as not known (pre-slice 5)", {i["field"] for i in v["info"]} == {"selection", "budget"})
    flagged = [s["text"] for s in v["copy"].get("segments", []) if s["state"] == "TO_CHECK"]
    keep = [s["text"] for s in v["copy"].get("segments", []) if s["state"] != "TO_CHECK"]
    if (v["copy"].get("subject") or {}).get("state") == "TO_CHECK":  # first, before typing: a keep saves at once
        page.get_by_role("region", name="The message").get_by_role("button", name="Keep the subject as mine").click()
        expect(page.locator("main")).to_contain_text("V2", timeout=15000)
        flagged.append("(subject kept as yours)")
    page.get_by_label(re.compile(r"^To\b")).fill(config.env("gmail_test_recipient") or "")
    page.get_by_label("Your name, as the sender").fill("Landfall test sender")
    for i in reversed([i for i, s in enumerate(v["copy"].get("segments", [])) if s["state"] == "TO_CHECK"]):
        page.get_by_role("button", name=f"Remove paragraph {i + 1}", exact=True).click()
    if not keep:
        page.get_by_role("button", name="Add a paragraph").click()
        page.get_by_label(re.compile("^Paragraph 1")).fill("Would you be open to a short conversation about a collaboration?")
    before = package(ctx, pid, v["id"])["version"]
    page.get_by_role("region", name="The message").get_by_role("button", name="Save changes").click()
    expect(page.locator("main")).to_contain_text(f"V{before + 1}", timeout=15000)
    v2 = package(ctx, pid, v["id"])
    n = v2["version"]
    check("your edits make a new version (YOU SAID); flagged sentences removed or kept as yours", n > 1 and not any(
        s["state"] == "TO_CHECK" for s in v2["copy"]["segments"]) and v2["copy"]["subject"]["state"] != "TO_CHECK",
          f"v{n} · {[t[:60] for t in flagged]}")
    d = next(x for x in v2["destination_states"] if x["destination"] == "EMAIL")
    check(f"v{n} is valid for email", d["state"]["package"] == "VALID", "; ".join(m["text"] for m in d["state"]["missing"]))
    gmail.get_by_role("button", name=re.compile(f"^Approve v{n}")).click()
    expect(gmail).to_contain_text("APPROVED", timeout=15000)
    page.get_by_text("Preview the exact message").click()
    page.screenshot(path=out / f"31-approved-v{n}.png", full_page=True)
    eml = download(page, "Download .eml", 32)
    z = download(page, "Download the outreach package", 33)
    zf = zipfile.ZipFile(io.BytesIO(z))
    v2 = package(ctx, pid, v["id"])
    subject = v2["email"]["subject"]
    check(f"the .eml holds exactly v{n}: subject, recipient, body, no removed sentence",
          subject.encode() in eml.replace(b"\r\n ", b" ") and all(t[:40].encode() not in eml for t in flagged)
          and (config.env("gmail_test_recipient") or "").encode() in eml, f"sha256 {hashlib.sha256(eml).hexdigest()[:16]}")
    atts = [n for n in zf.namelist() if n.startswith("attachments/")]
    check("the outreach package carries the same .eml and the approved attachment bytes",
          zf.read("message.eml") == eml and sorted(hashlib.sha256(zf.read(n)).hexdigest() for n in atts)
          == sorted(a["artifact"]["sha256"] for a in v2["assets"] if a["role"] == "ATTACHMENT"), f"{len(atts)} attachments")
    page.get_by_label("Subject").fill(subject + " (Brisbane)")
    page.get_by_role("region", name="The message").get_by_role("button", name="Save changes").click()
    expect(gmail).to_contain_text(f"Approve v{n + 1}", timeout=15000)
    v3 = package(ctx, pid, v["id"])
    d = next(x for x in v3["destination_states"] if x["destination"] == "EMAIL")
    check("an edit after approval outdates it", d["state"]["approval"] == "OUTDATED")
    gmail.get_by_role("button", name=re.compile(f"^Approve v{n + 1}")).click()
    expect(gmail).to_contain_text("APPROVED", timeout=15000)
    page.reload()
    expect(page.get_by_role("article", name="Gmail")).to_contain_text("APPROVED", timeout=30000)
    page.screenshot(path=out / "34-after-reload.png", full_page=True)
    v3 = package(ctx, pid, v["id"])
    check(f"reload: v{n + 1} and its approval persist; nothing was sent", v3["version"] == n + 1 and all(
        not x["actions"] and x["state"]["execution"] == "NONE" for x in v3["destination_states"]))
    (out / f"package-v{n + 1}.json").write_text(json.dumps(v3, indent=1))
    return v3


def digital(page, ctx, pid):
    v = prepare(page, ctx, pid, "minneapolis", "digital", 30)
    for d in v["destination_states"]:
        check(f"{d['label']}: a daily budget is needed (never invented)", any(m["field"] == "budget" for m in d["state"]["missing"]))
    page.get_by_label("Daily budget").fill("20")
    page.get_by_label("Currency").fill("USD")
    page.get_by_label("Start", exact=True).fill("2026-11-09")
    page.get_by_role("region", name="Budget, timing and settings").get_by_role("button", name="Save").click()
    expect(page.locator("main")).to_contain_text("US$20 a day in total", timeout=15000)  # slice 5: one total, split
    if v["assets"]:
        page.get_by_label(re.compile("I hold the rights")).click()
        expect(page.get_by_label(re.compile("I hold the rights"))).to_be_checked(timeout=15000)
    v = package(ctx, pid, v["id"])
    for d in v["destination_states"]:
        page.get_by_text(f"{d['label']}: what Landfall would send").click()
        acct = [x for x in d["plan"]["values"] if x["label"] == "ACCOUNT"]
        check(f"{d['label']}: the request plan is shown, account values unavailable without a connection",
              d["plan"]["values"] and all(x["value"] in (None, [], "") for x in acct),
              f"{len(d['plan']['values'])} values · still needed: {'; '.join(m['text'] for m in d['state']['missing']) or 'nothing'}")
    page.screenshot(path=out / "31-campaign-plans.png", full_page=True)
    z = download(page, "Download the campaign package", 32)
    zf = zipfile.ZipFile(io.BytesIO(z))
    names = zf.namelist()
    check("the campaign package: README, package.json, copy.csv, a plan and setup steps per platform",
          {"README.txt", "package.json", "copy.csv"} <= set(names) and all(
              f"plans/{d['destination'].lower()}.json" in names and f"setup-{d['destination'].lower()}.txt" in names
              for d in v["destination_states"]), ", ".join(names))
    check("it calls itself a manual setup and reference package", b"not a direct-import file" in zf.read("README.txt"))
    pj = json.loads(zf.read("package.json"))
    check("its JSON is exactly this version", pj["landfall_package"] == {"id": v["id"], "version": v["version"]})
    check("asset bytes in the package equal the saved artifacts", sorted(hashlib.sha256(zf.read(n)).hexdigest() for n in names
                                                                         if n.startswith("assets/")) == sorted(a["artifact"]["sha256"] for a in v["assets"]))
    v = package(ctx, pid, v["id"])  # after the downloads: their Export records
    (out / f"package-v{v['version']}.json").write_text(json.dumps(v, indent=1))
    return v


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    up = out / "upstream45"
    rc = subprocess.run([sys.executable, "-m", "scripts.journey_slice45", base, case, str(up)])
    check("upstream L45 journey from S01 (replayed)", rc.returncode == 0)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, storage_state=str(up / "state.json"), accept_downloads=True)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    pid = (up / "upstream" / "project_url.txt").read_text().strip().rsplit("/p/", 1)[1]
    v = outreach(page, ctx, pid) if case == "gotbag" else digital(page, ctx, pid)
    exports = [e for e in v["exports"]]
    check("downloads are Export records, never SENT; no execution exists", exports and all(
        not d["actions"] and d["manual"] is None for d in v["destination_states"]), f"{len(exports)} exports")
    m = ctx.new_page()
    m.set_viewport_size({"width": 390, "height": 844})
    m.goto(f"{base}/p/{pid}/deliver/{v['id']}")
    m.get_by_role("heading", name="Where this can go").wait_for(timeout=30000)
    m.wait_for_timeout(600)
    m.screenshot(path=out / "40-mobile.png", full_page=True)
    check("390px: no horizontal page scroll", not m.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"))
    p = ctx.request.get(f"{base}/api/projects/{pid}").json()
    (out / "project.json").write_text(json.dumps({"id": pid, "package_id": v["id"], "usage": p.get("usage")}, indent=1))
    check("no JavaScript errors", not errors, "; ".join(errors[:2]))
    browser.close()
print(f"{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
