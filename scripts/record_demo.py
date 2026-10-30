"""Record, package and verify Landfall's recorded demo track (owner decision, 28 Oct 2026: Death Wish Coffee and UNTUCKit).

  walk <base> <brand> <out_dir> [--demo] [--from STEP]
      One browser walk: the brand URL with the default goal → See the brief → Explore markets → See recommended markets →
      Build a plan for the top city (auto-plan; the top three prebuild) → Prepare this plan → the Launch page; waits for
      its background preparation (messages, ads, launch kit), picks the first ad and opens "See exactly what's sent"
      once. With --demo it starts from the demo intake instead, checks the recording's choices (fixtures/demo/<brand>/
      case.json) and then presses every sign-off, which a demo runs SIMULATED. Writes walk.json and screenshots to <out_dir>; --from resumes a walk at a step (entry, brief, markets,
      plan, launch). The plan is a creator with Google Ads (strategist.sample_shape) and its creators are samples
      (influencers.sample): a recording picks each so any pick replays. Never searches Influencers.club, connects Gmail
      or Google Ads, or presses Send / Create / Copy.
  package <brand> <out_dir>
      With LANDFALL_DB pointing at the recording database: the walk's recorded exchanges, minus every Influencers.club
      exchange, into fixtures/demo/<brand>/ with a case.json. Refuses secrets.
  package --from-project <pid> --db <path> --out fixtures/demo/<brand>
      The same from any recorded project (e.g. the owner's on .landfall/l67.db): the database is copied through a
      read-only connection first, and the founder's choices are read from the project's records.

Recording (live, under the active authority; ≈ US$1–1.5 Bedrock per brand; one brand at a time):
  cp .landfall/l67.db .landfall/rec2.db (+ -wal, -shm)
  LANDFALL_DB=.landfall/rec2.db LANDFALL_PROVIDER_MODE=record LANDFALL_AUTO_PLAN=1 LANDFALL_SAMPLE_CREATORS=on LANDFALL_SHARED_DELIVERY= \
    LANDFALL_REQUIRE_GRANT= LANDFALL_REPLAY_CASES= LANDFALL_LIVE_STAGES= SHOPIFY_AGENT_PROFILE_URL= \
    influencers_club_api_key= influencer_api_key= uv run uvicorn landfall.app:app --host 127.0.0.1 --port 8114
  (cd web && LANDFALL_API=http://127.0.0.1:8114 npx vite --host 127.0.0.1 --port 5186 --strictPort)
  uv run python -m scripts.record_demo walk http://127.0.0.1:5186 deathwishcoffee .landfall/demo-rec3-dwc
  LANDFALL_DB=.landfall/rec2.db uv run python -m scripts.record_demo package deathwishcoffee .landfall/demo-rec3-dwc
(SHOPIFY_AGENT_PROFILE_URL is emptied so products are read from pages, the route replay takes.)
"""
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from landfall.influencers import BASE
from landfall.ledger import IC  # the Influencers.club provider name

IC_HOST = urlsplit(BASE).hostname  # their API; a web search citing their public site is not their data

BRANDS = {"deathwishcoffee": ("Death Wish Coffee", "https://www.deathwishcoffee.com/"),
          "untuckit": ("UNTUCKit", "https://www.untuckit.com/")}
DEMO = Path(__file__).resolve().parent.parent / "fixtures" / "demo"
STEPS = ["entry", "brief", "markets", "plan", "launch"]


def walk(base: str, brand: str, out: Path, demo: bool, start: str = "entry") -> dict:
    from playwright.sync_api import expect, sync_playwright
    out.mkdir(parents=True, exist_ok=True)
    state, wj = out / "state.json", out / "walk.json"
    w = json.loads(wj.read_text()) if start != "entry" and wj.exists() else {"brand": brand, "demo": demo}
    recorded = json.loads((DEMO / brand / "case.json").read_text())["walk"] if demo else None
    shot = lambda page, name: page.screenshot(path=out / f"{name}.png", full_page=True)  # noqa: E731
    save = lambda: wj.write_text(json.dumps(w, indent=1))  # noqa: E731
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True,
                            **({"storage_state": str(state)} if start != "entry" else {}))
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        bar = page.locator(".decision-bar .btn-primary")
        at = STEPS.index(start)

        if at <= 0:  # entry
            page.goto(base + "/")
            if demo:
                page.get_by_role("radio", name=re.compile(BRANDS[brand][0])).check(timeout=30000)
                shot(page, "01-intake")
                page.get_by_role("button", name="Start the demo").click()
            else:
                page.locator("#url").fill(BRANDS[brand][1], timeout=30000)
                page.get_by_role("button", name="Start exploring").click()
            page.wait_for_url(re.compile(r"/p/prj_"), timeout=30000)
            w["pid"] = page.url.split("/p/")[1].split("/")[0].split("?")[0]
            ctx.storage_state(path=str(state))
            save()
        pid = w["pid"]
        api = lambda path: ctx.request.get(f"{base}/api/projects/{pid}{path}").json()  # noqa: E731

        if at <= 1:  # brief
            page.goto(f"{base}/p/{pid}")
            see = page.get_by_role("button", name="See the brief")
            expect(see.or_(page.get_by_text("Here's what we understand", exact=False))).to_be_visible(timeout=900000)
            if see.is_visible():
                see.click()
            expect(page.get_by_text("Here's what we understand", exact=False)).to_be_visible(timeout=60000)
            shot(page, "02-brief")

        if at <= 2:  # markets
            if "/markets" not in page.url:
                page.goto(f"{base}/p/{pid}")
                expect(bar.filter(has_text="Explore markets")).to_be_visible(timeout=600000)
                bar.filter(has_text="Explore markets").click()
            page.wait_for_url(re.compile(r"/markets"), timeout=600000)
            see, build = page.get_by_role("button", name="See recommended markets"), bar.filter(has_text="Build a plan for")
            expect(see.or_(build)).to_be_visible(timeout=1800000)  # the page may move on to the recommendation by itself
            shot(page, "03-scan")
            if see.is_visible():
                see.click()
            expect(build).to_be_visible(timeout=900000)
            shot(page, "04-markets")
            w["city_name"] = bar.inner_text().replace("Build a plan for", "").strip()
            if recorded:
                assert w["city_name"] == recorded["city_name"], (w["city_name"], recorded["city_name"])
            bar.click()
            page.wait_for_url(re.compile(r"/plan/[a-z-]+$"), timeout=60000)
            w["city_id"] = page.url.rstrip("/").rsplit("/", 1)[1]
            for _ in range(60):  # Markets opens the plan first, then records the beachhead; a reload before that loses it
                if (api("/markets").get("beachhead") or {}).get("city_id") == w["city_id"]:
                    break
                page.wait_for_timeout(1000)
            else:
                raise AssertionError("the beachhead was never recorded")
            save()

        if at <= 3:  # plan: auto-plan (prebuilt for the top three) builds it; Prepare drafts what's missing, then signs off
            page.goto(f"{base}/p/{pid}/plan/{w['city_id']}")
            for _ in range(6):
                prep = page.locator(".decision-bar button", has_text=re.compile("Prepare this plan|Preparing"))
                expect(prep).to_have_class("btn-primary", timeout=3600000)
                if not w.get("plan_shot"):
                    shot(page, "05-plan")
                    w["plan_shot"] = True
                prep.click()
                try:
                    page.wait_for_url(re.compile(r"/(prepare|launch)/"), timeout=20000)
                    break
                except Exception:  # noqa: BLE001 — it drafted a missing method first; wait and press again
                    page.wait_for_timeout(5000)
            page.wait_for_url(re.compile(r"/launch/"), timeout=60000)
            plan = api(f"/plans/{w['city_id']}")
            w["methods"] = {m["method"]: m["role"]["role"] for m in plan["methods"] if m.get("role")}
            save()

        if at <= 4:  # launch: wait for the background preparation, pick the first ad, open "See exactly what's sent" once
            cid = w["city_id"]
            page.goto(f"{base}/p/{pid}/launch/{cid}")
            lst = page.get_by_role("list", name="Launch checklist")
            lst.wait_for(timeout=120000)
            for _ in range(360):  # ≤ 30 min; the page started it all (POST …/launch/prewarm)
                if launch_ready(api, cid):
                    break
                page.wait_for_timeout(5000)
            else:
                raise AssertionError("the Launch page's background preparation didn't finish")
            creator = next((it for it in api(f"/plans/{cid}/launch")["items"] if it["method"] == "creator"), None)
            # sample creators (influencers.sample): a recording picks every one, last to first, so whichever a visitor
            # picks replays; a replay picks the first, as the recording ended
            opts = [o for o in (creator or {}).get("options") or [] if o.get("search")]
            for o in (opts[:1] if demo else opts[::-1]):
                ctx.request.post(f"{base}/api/projects/{pid}/plans/{cid}/launch/pick", data={
                    "method": "creator", "lead_id": o["lead_id"], "search_id": o["search"]["id"], "index": o["search"]["index"]})
                page.wait_for_timeout(2000)
                for _ in range(120):
                    if launch_ready(api, cid):
                        break
                    page.wait_for_timeout(3000)
            page.reload()
            lst.wait_for(timeout=120000)
            page.wait_for_timeout(3000)
            shot(page, "06-launch")
            ads = page.locator(".launch-ad")
            if ads.count():
                ads.first.click()
                expect(ads.first).to_have_attribute("aria-checked", "true", timeout=60000)
                page.wait_for_timeout(1500)
                shot(page, "07-ad-picked")
            v = api(f"/plans/{cid}/launch")
            pkgs = [api(f"/packages/{x['id']}") for it in v["items"] for x in it["packages"]]
            w["picks"] = picks_of(v["items"], {x["id"]: x for x in pkgs})
            if recorded:
                assert w["picks"] == recorded["picks"], (w["picks"], recorded["picks"])
            w["items"] = [{"method": it["method"], "status": lst.locator("li.launch-item").nth(i).locator(".launch-status").inner_text(),
                           "sign_off": (lst.locator("li.launch-item").nth(i).locator(".launch-sign button").all_inner_texts() or [None])[0]}
                          for i, it in enumerate(v["items"])]
            w["launch_simulated"] = bool(pkgs) and all(d["state"].get("simulated") for x in pkgs for d in x["destination_states"])
            save()
            lst.get_by_role("link", name=re.compile("See exactly what.s sent")).first.click()  # never Send / Create / Copy
            page.wait_for_url(re.compile(r"/deliver/pkg_"), timeout=60000)
            page.locator("main").get_by_text(re.compile("SIMULATED|Use it yourself|USE IT YOURSELF", re.I)).first.wait_for(timeout=600000)
            page.wait_for_timeout(2000)
            shot(page, "08-review")
            w["review"] = page.url.split("/deliver/")[1].split("?")[0]
            pkg = api(f"/packages/{w['review']}")
            w["review_simulated"] = all(d["state"].get("simulated") for d in pkg["destination_states"])
            save()
            if demo:  # replay only (never a recording): every sign-off on the Launch page runs SIMULATED
                page.goto(f"{base}/p/{pid}/launch/{cid}")
                lst.wait_for(timeout=120000)
                keep = lst.get_by_role("button", name="Keep it as mine")  # a sentence to check: the visitor's own call
                for _ in range(12):
                    if not keep.count():
                        break
                    keep.first.click()
                    page.wait_for_timeout(2500)
                signs = lst.locator(".launch-sign button:not([disabled])")
                signs.first.wait_for(timeout=60000)
                for _ in range(len(v["items"])):
                    if not signs.count():
                        break
                    signs.first.click()
                    page.wait_for_timeout(4000)
                shot(page, "09-signed-off")
                w["signed_off"] = {it["method"]: [(d["summary"], [a["mode"] for a in d.get("actions") or []])
                                                  for x in it["packages"] for d in api(f"/packages/{x['id']}")["destination_states"]]
                                   for it in api(f"/plans/{cid}/launch")["items"]}
                save()
        w["page_errors"] = errors
        save()
        b.close()
    return w


def launch_ready(api, cid: str) -> bool:
    """The Launch page's background preparation has ended: every item's message or ads, and the launch-kit scenes."""
    v = api(f"/plans/{cid}/launch")
    if not v.get("plan"):
        return False
    for it in v["items"]:
        if it["preparing"] or (it.get("creators") or {}).get("searching"):
            return False
        if it["method"] == "digital" and not (it.get("variants") or it["failed"]):
            return False
        if it["method"] != "digital" and not (it["packages"] or it["failed"]) and \
                any(o["lead_id"] and o["route"]["kind"] == "email" for o in it.get("options") or []):
            return False  # delivery.prewarm_launch prepares the top contactable one
        if any(api(f"/packages/{x['id']}").get("preparing") for x in it["packages"]):
            return False  # still tailoring the message
    return not any(m["state"] in ("QUEUED", "GENERATING") for m in api(f"/plans/{cid}/mockups"))


def picks_of(items: list[dict], pkgs: dict) -> dict:
    """Per Launch item: who it goes to (as prepared), and for Google Ads the ad picked."""
    out = {}
    for it in items:
        ps = [pkgs[x["id"]] for x in it["packages"] if x["id"] in pkgs]
        out[it["method"]] = {"ad": it.get("picked")} if it["method"] == "digital" else \
            {"recipients": [(x.get("partner") or {}).get("name") or "Someone you'll name yourself" for x in ps]}
    return out


def walk_of(pid: str) -> tuple[dict, list[str]]:
    """The founder's choices, read from the project's own records (so any recorded project works, the owner's too):
    the beachhead, the signed plan's methods, and per Launch item who it goes to (and the ad picked)."""
    from landfall import db, delivery, markets, plans
    cid = ((db.latest("shortlist", pid) or {}).get("beachhead") or {}).get("city_id")
    assert cid, "this project never chose a beachhead"
    view = plans.view(pid, cid)
    methods = {m["method"]: m["role"]["role"] for m in view["methods"] if m.get("role")}
    v = delivery.launch_view(pid, cid, db.q("SELECT session FROM projects WHERE id=?", pid)[0]["session"])
    pkgs = {x["id"]: delivery.package_view(pid, x["id"]) for it in v["items"] for x in it["packages"]}
    notes = [f"{x['method']}: {x['partner'].get('name')} was found by Influencers.club, which the demo never replays"
             for x in pkgs.values() if (x.get("partner") or {}).get("lead_id")
             and ((db.get_record(x["partner"]["lead_id"]) or {}).get("found_by") or {}).get("origin") not in (None, "SYNTHETIC")]
    return {"city_id": cid, "city_name": markets.CITIES[cid]["name"], "methods": methods, "picks": picks_of(v["items"], pkgs)}, notes


def package(pid: str, dest: Path) -> Path:
    """A recorded project's exchanges, minus every Influencers.club exchange, and a case.json with its founder's walk."""
    from landfall import config, db
    src = config.PRIVATE / "runs" / pid / "exchanges.jsonl"
    lines = [ln for ln in src.read_text().split("\n") if ln]
    kept = [ln for ln in lines if json.loads(ln)["provider"] != IC]  # their terms: never recorded into the demo
    assert not any(json.loads(ln)["provider"] == IC or IC_HOST in ln for ln in kept)
    text = "\n".join(kept) + "\n"
    for secret in secrets():
        assert secret not in text, "a configured secret or private address is in the recorded exchanges"
    p = db.q("SELECT * FROM projects WHERE id=?", pid)[0]
    fi, brief = db.latest("founder_input", pid), db.latest("brief", pid) or {}
    walk_, notes = walk_of(pid)
    name = next((n for n, url in BRANDS.values() if url == p["url"]), brief.get("brand_name") or p["url"])
    usage = db.q("SELECT provider, COUNT(*) n, COALESCE(SUM(est_usd),0) usd FROM usage WHERE project_id=? GROUP BY provider", pid)
    providers = {}
    for ln in kept:
        pr = json.loads(ln)["provider"]
        providers[pr] = providers.get(pr, 0) + 1
    case = {
        "case_id": f"demo-{dest.name}", "label": f"RECORDED · {name}, a real brand; replayed, nothing live",
        "name": name, "status": "recorded demo (owner decision, 28 Oct 2026)",
        "input": {"url": p["url"], "goal": {k: fi.get(k) for k in ("objective", "ceiling_usd", "ceiling_by", "include", "exclude", "scope",
                                                                     "purpose", "leave_out_presence", "home_country")}},
        "walk": walk_, **({"notes": notes} if notes else {}),
        # the only runs a demo project may start (runner.demo_allows): an unrecorded choice ends before any work. A run
        # stopped partway (the other prebuilt plans, cancelled once the walk ended) never recorded enough to replay.
        "runs": sorted({f"{r['kind']}:{r['input_key'].replace(pid, '{pid}')}"
                        for r in db.q("SELECT kind, input_key FROM runs WHERE project_id=? AND status != 'cancelled'", pid)}),
        "recorded_from_project": pid, "recorded_at": p["created_at"], "model": config.NOVA_MODEL,
        "exchanges": {"kept": len(kept), "dropped_influencers_club": len(lines) - len(kept), "by_provider": providers},
        "usage": {r["provider"]: {"calls": r["n"], "est_usd": round(r["usd"], 4)} for r in usage},
        "sha256": hashlib.sha256(text.encode()).hexdigest()}
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "exchanges.jsonl.gz").write_bytes(gzip.compress(text.encode(), mtime=0))  # read by cassette.read_exchanges
    (dest / "case.json").write_text(json.dumps(case, indent=1, ensure_ascii=False) + "\n")
    for n in notes:
        print("NOTE", n)
    return dest


def snapshot(db_path: Path) -> Path:
    """A private copy of a live database, read through a read-only connection (the original is never written)."""
    import sqlite3
    import tempfile
    out = Path(tempfile.mkdtemp(prefix="landfall-demo-")) / "snapshot.db"
    src = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    with sqlite3.connect(out) as dst:
        src.backup(dst)
    src.close()
    return out


def secrets() -> list[str]:
    """Values that must never reach a demo case: provider keys, OAuth client secrets and the configured addresses.
    Read in memory from the environment and .env; never printed."""
    from landfall import config
    names = ("api_key", "gemini_api_key", "influencer_api_key", "youtube_api_key", "google_oauth_client_secret",
             "google_oauth_client_id", "LANDFALL_TOKEN_KEY", "gmail_test_recipient", "gmail_test_sender",
             "google_ads_test_customer_id", "google_ads_test_manager_id", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")
    vals = [config.env(n) for n in names]
    vals += [v.strip() for v in (config.env("LANDFALL_DELIVERY_ALLOWLIST") or "").split(",")]
    try:  # the AWS profile's own keys, read in memory where a profile exists (never copied)
        import boto3
        c = boto3.Session(profile_name=config.AWS_PROFILE).get_credentials()
        vals += [c.access_key, c.secret_key, c.token] if c else []
    except Exception:  # noqa: BLE001 — no profile on this machine: nothing of it can be in a case
        pass
    return [v for v in vals if v and len(v) >= 6]


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "walk":
        base, brand, out = sys.argv[2], sys.argv[3], Path(sys.argv[4])
        frm = sys.argv[sys.argv.index("--from") + 1] if "--from" in sys.argv else "entry"
        w = walk(base, brand, out, "--demo" in sys.argv, frm)
        print(json.dumps(w, indent=1))
        sys.exit(1 if w.get("page_errors") else 0)
    elif cmd == "package":  # package <brand> <walk_dir> | package --from-project <pid> --db <path> --out <dir>
        from landfall import config
        opt = lambda n: sys.argv[sys.argv.index(n) + 1] if n in sys.argv else None  # noqa: E731
        if opt("--db"):
            config.DB_PATH = snapshot(Path(opt("--db")))  # before the first query opens a connection
        if opt("--from-project"):
            print(package(opt("--from-project"), Path(opt("--out"))))
        else:
            brand, rec = sys.argv[2], Path(sys.argv[3])
            print(package(json.loads((rec / "walk.json").read_text())["pid"], DEMO / brand))
