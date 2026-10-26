"""Slice-2 browser journey through the real UI, from URL entry (BUILD-PLAN slice 2 browser tests).

usage: python -m scripts.journey_identity <app_base> <matched|ask> <brand_url> <out_dir>
         [--phase propose|edit|all] [--state file.json] [--remove NAME] [--search TERM --add NAME]

matched (L2-A Komono): no question → brief auto-opens with "Matched automatically · Not you?" → Qloo drawer →
  "Not you?" → Neither (keyboard) → profile proposed → "Not you?" → the brand again → refresh keeps it.
ask (L2-B GOT BAG): the question appears above the fold while reading → keyboard radio check → Neither → brief
  auto-opens → profile proposed [phase propose ends here] → remove one, add one via Qloo search, confirm → refresh.
Prints one line per check; exits non-zero if any check fails. The browser session is saved with --state so a
second phase continues as the same founder.
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

base, mode, target, out = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4])
arg = lambda name, default=None: next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == name), default)  # noqa: E731
phase, state, remove, term, add = arg("--phase", "all"), arg("--state"), arg("--remove"), arg("--search"), arg("--add")
out.mkdir(parents=True, exist_ok=True)
results = []
BRIEF = re.compile("Here's what we understand")


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f" · {detail}" if detail else ""), flush=True)


def wait_profile(page, timeout=300000):
    page.locator(".profile").wait_for(timeout=60000)
    page.wait_for_function("!document.querySelector('.profile')?.innerText.includes('is searching Qloo')", timeout=timeout)


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, **({"storage_state": state} if state and phase == "edit" else {}))
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    if phase in ("all", "propose"):
        page.goto(base + "/")
        page.get_by_label("Your brand's website").fill(target)
        page.keyboard.press("Enter")
        page.wait_for_url(re.compile(r"/p/prj_"), timeout=15000)
        (out / "project_url.txt").write_text(page.url)
        check("URL entry opens the project", True, page.url)

        if mode == "matched":
            asked = False
            for _ in range(600):  # until the brief opens, watch that no question ever appears
                if page.get_by_role("heading", name=re.compile("Which of these is you|Is this you")).count():
                    asked = True
                if page.get_by_role("heading", name=BRIEF).count():
                    break
                page.wait_for_timeout(400)
            check("confident single match: no question asked", not asked)
            check("brief opens automatically", page.get_by_role("heading", name=BRIEF).count() == 1)
            page.wait_for_timeout(800)
            page.screenshot(path=out / "01-brief-matched.png", full_page=True)
            disc = page.locator("section", has=page.get_by_role("heading", name="Discovery"))
            dtext = disc.inner_text()
            check("Discovery says Matched automatically · Not you?", "Matched automatically" in dtext and "Not you?" in dtext,
                  dtext.split("\n")[2] if dtext.count("\n") > 2 else dtext)
            check("What Qloo associates with you shows tags", "What Qloo associates with you" in dtext and "a cultural relationship, not demand" in dtext)
            check("Start exploring markets is enabled (slice 3)",
                  page.get_by_role("button", name="Start exploring markets").is_enabled())
            disc.get_by_role("button", name="QLOO ↗").click()
            qd = page.get_by_role("dialog", name="QLOO")
            expect(qd).to_be_visible()
            page.screenshot(path=out / "02-qloo-drawer.png")
            check("Qloo drawer shows entity facts and tags", "Qloo id" in qd.inner_text() and "not demand" in qd.inner_text())
            page.keyboard.press("Escape")

            disc.get_by_role("button", name="Not you?").click()
            dlg = page.get_by_role("dialog", name="QLOO IDENTITY")
            expect(dlg).to_be_visible()
            radios = dlg.get_by_role("radio")
            check("Not you? reopens the chooser with the candidate plus Neither", radios.count() == 2, f"{radios.count()} options")
            radios.first.focus()
            page.keyboard.press("ArrowDown")  # native radio group: arrows move and select
            check("keyboard: arrow key selects Neither", dlg.get_by_role("radio", name=re.compile("Neither")).is_checked())
            page.screenshot(path=out / "03-not-you.png")
            dlg.get_by_role("button", name="Continue without a match").click()
            wait_profile(page)
            page.wait_for_timeout(600)
            page.screenshot(path=out / "04-neither-profile.png", full_page=True)
            prof = page.locator(".profile").inner_text()
            check("Neither → profile editor with proposed signals", "Your discovery profile" in prof and "NOT CONFIRMED" in prof)
            check("Markets waits for the profile", "Confirm your discovery profile" in page.locator("#primary-reason").inner_text())

            disc.get_by_role("button", name="Change").click()
            dlg = page.get_by_role("dialog", name="QLOO IDENTITY")
            dlg.get_by_role("radio").first.check()
            dlg.get_by_role("button", name="This is us").click()
            page.get_by_text("Chosen by you").wait_for(timeout=10000)
            page.reload()
            page.get_by_role("heading", name=BRIEF).wait_for(timeout=15000)
            check("choosing the brand again → refresh keeps 'Chosen by you'", page.get_by_text("Chosen by you").count() == 1
                  and page.locator(".profile").count() == 0)
        else:
            q = page.get_by_role("heading", name=re.compile("One question while we read|one question first"))
            q.wait_for(timeout=120000)
            reading_open = page.get_by_role("heading", name=re.compile("One question while we read")).count() == 1
            rows = page.locator(".task-rows li").all_inner_texts()
            still = [r.split("\n")[0] for r in rows if r.startswith(("RUNNING", "QUEUED"))]
            check("the question appears before reading finishes", reading_open and still, f"{len(still)} reading rows unfinished")
            box = page.locator(".chooser").bounding_box()
            check("the question is above the fold", box and box["y"] < 900, f"top at {box and round(box['y'])}px")
            page.screenshot(path=out / "01-question.png")
            radios = page.locator(".chooser").get_by_role("radio")
            radios.first.focus()
            page.keyboard.press("ArrowDown")
            check("keyboard: arrow key moves the selection", radios.nth(1).is_checked(), f"{radios.count()} options incl. Neither")
            page.locator(".chooser").get_by_role("radio", name=re.compile("Neither")).check()
            page.get_by_role("button", name="Continue without a match").click()
            page.get_by_role("heading", name=BRIEF).wait_for(timeout=240000)
            check("after Neither, the brief opens automatically", True)
            wait_profile(page)
            page.wait_for_timeout(600)
            page.screenshot(path=out / "02-proposed-profile.png", full_page=True)
            prof = page.locator(".profile")
            check("profile proposed with origins and inferred-from", prof.get_by_text("QLOO · CATEGORY").count() > 0
                  and prof.get_by_role("button", name=re.compile("^Inferred from")).count() > 0)
            comps = prof.get_by_text("COMPARABLE · NOT YOU")
            if comps.count():
                boxes = prof.get_by_role("checkbox")
                check("comparables are unticked and carry the fixed note", all(not boxes.nth(i).is_checked() for i in range(boxes.count()))
                      and "Off by default: Qloo finds comparable brands by matching words" in prof.inner_text())
            first = prof.get_by_role("button", name=re.compile("^Inferred from")).first
            first.click()
            inf = page.get_by_role("dialog", name="INFERENCE")
            expect(inf).to_be_visible()
            page.screenshot(path=out / "03-signal-inference.png")
            check("a signal's inference drawer shows its facts and reasoning", "INFERRED FROM" in inf.inner_text())
            page.keyboard.press("Escape")
        if state:
            ctx.storage_state(path=state)

    if mode == "ask" and phase in ("all", "edit"):
        if phase == "edit":
            page.goto((out / "project_url.txt").read_text().strip())
            page.get_by_role("heading", name=BRIEF).wait_for(timeout=30000)
            wait_profile(page)
        prof = page.locator(".profile")
        before = prof.locator(".row-wrap").count()
        if remove:
            prof.get_by_role("button", name=f"Remove {remove}").click()
        if term and add:
            prof.get_by_label("Add a signal").fill(term)
            prof.get_by_role("button", name="Search Qloo").click()
            found = prof.locator(".found li", has_text=add)
            found.first.wait_for(timeout=30000)
            found.first.get_by_role("button", name="Add").click()
        page.screenshot(path=out / "04-edited.png", full_page=True)
        prof.get_by_role("button", name="Confirm profile").click()
        prof.get_by_text("Profile confirmed").wait_for(timeout=15000)
        after = [r.split("\n")[0] for r in prof.locator(".row-wrap").all_inner_texts()]
        check("remove one, add one, confirm", (not remove or remove not in after) and (not add or add in after),
              f"{before} → {len(after)} signals")
        check("after confirming, Start exploring markets is enabled (slice 3)",
              page.get_by_role("button", name="Start exploring markets").is_enabled())
        page.reload()
        page.get_by_role("heading", name=BRIEF).wait_for(timeout=15000)
        prof = page.locator(".profile")
        check("refresh keeps the confirmed profile", prof.get_by_text("CONFIRMED", exact=True).count() == 1
              and (not add or prof.get_by_text(add).count() >= 1))
        page.screenshot(path=out / "05-confirmed.png", full_page=True)
        page.get_by_role("button", name=re.compile("^Activity")).click()
        act = page.get_by_role("dialog", name="ACTIVITY")
        expect(act).to_be_visible()
        page.screenshot(path=out / "06-activity.png")
        check("Activity shows Qloo request counts", "QLOO REQUESTS" in act.inner_text() and "Qloo tag search" in act.inner_text())
        page.keyboard.press("Escape")

    if phase in ("all", "edit") or mode == "matched":
        m = ctx.new_page()
        m.set_viewport_size({"width": 390, "height": 844})
        m.goto(page.url)
        m.get_by_role("heading", name=BRIEF).wait_for(timeout=15000)
        m.screenshot(path=out / "07-mobile.png", full_page=True)
        check("390px: no horizontal page scroll", not m.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"))
    check("no JavaScript errors", not errors, "; ".join(errors[:2]))
    browser.close()
print(f"{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
