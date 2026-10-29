"""Slice 6 browser check with SYNTHETIC providers (BUILD-PLAN §6.3 "Action Center browser checks").

S01 → research Toronto → retail → Develop → Prepare outreach → the Action Center: the unconfigured-deployment card and its
supporting copy, still-needed items, a flagged sentence, sender and recipient edits (v2), the exact preview, .eml and
outreach downloads (bytes checked), mailto, approve, an edit outdating the approval, a SIMULATED connect and send, reload;
then digital → Develop → Prepare campaign → a missing budget → budget, start and radius → the Google Ads plan (digital is
Google Ads only on the product path since 28 Oct 2026) and the campaign package; 390 px. Screenshots: .landfall/browser-slice6/ (git-ignored).
"""
import io
import re
import zipfile

import pytest
from test_markets import FakeQloo, Strategist
from test_markets_browser import enter, explore, open_brief, server  # noqa: F401
from test_slice4 import FILLER, KC, STAGES, Checker, Prose, Web, grounding, html, toronto_script
from test_slice45_browser import KC_BRANDS, PRODUCT, RUNCLUB, scripts

from landfall import config, db, delivery, fetch, products, research, specialist

SHOTS = config.ROOT / ".landfall" / "browser-slice6"
pytestmark = pytest.mark.skipif(not (config.ROOT / "web" / "dist" / "index.html").exists(), reason="frontend not built")
OUTREACH = {"subject": "Tidewell canned teas for your makers shelf",
            "paragraphs": [{"text": "Kettle & Crane already carries teas from small Ontario makers.", "cites": ["E1"]},
                           {"text": "Would you try four of our canned teas on that shelf for a month?"},
                           {"text": "Our teas won a national award last year."}]}
ADS = {"headlines": [{"text": "Sparkling tea in Toronto"}, {"text": "Cold-brewed, lightly fizzy"}, {"text": "Try Tidewell"}],
       "descriptions": [{"text": "Hibiscus and berry sparkling tea in a can."}, {"text": "Join the Toronto waitlist today."}],
       "primary_text": {"text": "Sparkling cold-brewed tea, now on a Toronto waitlist."},
       "ad_text": {"text": "Sparkling cold-brewed tea for Toronto."}, "keywords": ["sparkling tea toronto"], "cta": "SIGN_UP"}


def setup(monkeypatch):
    from test_slice4 import toronto_pages

    from landfall import plans
    monkeypatch.setattr(plans, "PRE_PLAN_ROUTE", True)  # these journeys test the slice-6 route [P65-I2]
    web = Web()
    toronto_pages(web)
    web.pages[KC_BRANDS] = (200, html("Brands we carry · Kettle & Crane",
                                      "Brands we carry: Pluck Tea, Sloane and Genuine Tea, alongside drinks and teas from small Ontario makers.",
                                      FILLER, "Wholesale enquiries: makers can write to hello@kettlecrane.example."))
    web.pages[RUNCLUB] = (200, html("Ossington Runners", "We meet every Saturday 6am at Trinity Bellwoods Park for a 5k loop.", FILLER))
    web.pages[PRODUCT] = (200, html("Hibiscus Berry Sparkling Tea – Tidewell", "Hibiscus and Berry sparkling cold-brewed "
                                    "tea in a 12oz can. Brewed slowly, lightly sparkling, no added sugar.", FILLER))
    monkeypatch.setattr(products, "page_candidates", lambda run, pid, robots, attempts: [{"url": PRODUCT, "mcp": None}])
    monkeypatch.setattr(fetch, "_live_get", lambda url, binary=False: web(url))
    monkeypatch.setattr(fetch, "_live_render", lambda url: {"url": url, "status": 0, "error": "not rendered"})
    monkeypatch.setattr(research, "judge", Checker())
    monkeypatch.setattr(research, "judge_prose", Prose())
    monkeypatch.setattr(research, "ground", grounding([KC]))
    monkeypatch.setattr(research, "run_agent", lambda ctx, row: toronto_script(ctx, {t.tool_name: t for t in research.make_tools(ctx)}))
    research._robots.clear()
    plays = scripts()
    monkeypatch.setattr(specialist, "run_loop", lambda ctx, spec, row, inp: plays[spec.method]({t.tool_name: t for t in spec.tools(ctx)}))

    def judge(ctx, system, listing, model_cls, kind, key):
        if kind == "check":
            return {"verdicts": [{"claim_id": i, "verdict": "SUPPORTS", "unsupported_parts": [], "shows_way_in": True}
                                 for i in re.findall(r"- id=(c\d+) \|", listing)]}
        return {"verdicts": [{"id": i, "kind": "PROPOSAL"} for i in re.findall(r"- id=(s\d+) \|", listing)]}
    monkeypatch.setattr(specialist, "_judge", judge)
    monkeypatch.setattr(specialist, "draft_call", lambda ctx, prompt: {
        "concept_name": "Makers-shelf trial", "concept_line": "Four canned teas on Kettle & Crane's makers shelf for a month.",
        "activities": ["Send a sample case", "Agree a four-week shelf slot"], "success_measure": "Units sold in four weeks",
        "audience_hypothesis": "Shoppers who already buy small-maker teas", "partners": ["Kettle & Crane"],
        "products": ["Hibiscus Berry Sparkling Tea"]})
    monkeypatch.setattr(delivery, "copy_call", lambda ctx, prompt: OUTREACH if "inquiry email" in prompt else ADS)
    monkeypatch.setattr(delivery, "judge_copy", lambda ctx, listing: {"verdicts": [
        {"id": i, "kind": "UNSUPPORTED_FACT" if "award" in s else "PROPOSAL"}
        for i, s in re.findall(r"- id=(s\d+) \|.*?sentence: \"(.*?)\" \|", listing)]})
    for k in ("LANDFALL_TOKEN_KEY", "google_oauth_client_id", "google_oauth_client_secret", "meta_app_id", "tiktok_app_id",
              "LANDFALL_GOOGLE_ADS_ACCESS", "LANDFALL_DELIVERY_ALLOWLIST"):
        monkeypatch.delitem(config._ENV, k, raising=False)


def research_toronto(page, base, pid):
    """6.5 removed Markets' research ticks; research for one city starts from its plan, which sets a beachhead and so
    fences packages without a plan. These slice-6 journeys keep the no-plan route: Toronto's research starts through the
    API, then the research page shows it as before."""
    page.get_by_role("heading", name=re.compile("^Toronto looks like the strongest first city")).wait_for(timeout=60000)
    assert page.request.post(f"{base}/api/projects/{pid}/research", data={"city_ids": ["toronto"]}).ok
    page.goto(base + f"/p/{pid}/opportunities/research")


def no_wide(m, name):
    wide = m.evaluate("""[...document.querySelectorAll('main *')].filter(e => e.getBoundingClientRect().right > window.innerWidth + 1)
        .slice(0, 8).map(e => e.tagName + '.' + e.className + ' ' + Math.round(e.getBoundingClientRect().right))""")
    assert not m.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"), (name, wide)


def test_slice6_action_center_in_the_browser(server, monkeypatch):
    from playwright.sync_api import expect, sync_playwright
    setup(monkeypatch)
    base = server(FakeQloo(), Strategist())
    monkeypatch.setattr(config, "LIVE_STAGES", STAGES + ("specialist", "specialist_check", "specialist_places", "specialist_search",
                                                         "specialist_fetch", "products_fetch", "develop", "delivery_copy", "delivery_fetch"))
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        enter(page, base)
        open_brief(page)
        pid = page.url.split("/p/")[1].split("/")[0]
        explore(page, timeout=120000)
        research_toronto(page, base, pid)
        page.get_by_role("region", name="Toronto").get_by_text("DONE", exact=True).wait_for(timeout=60000)

        # retail → Develop → Prepare outreach
        page.goto(base + f"/p/{pid}/opportunities/toronto/retail")
        page.get_by_role("button", name="Investigate retail").click()
        page.get_by_role("button", name="Develop this approach").click(timeout=30000)
        draft = page.get_by_role("region", name="Developed draft")
        expect(draft).to_contain_text("EVIDENCED DRAFT", timeout=30000)
        draft.get_by_role("button", name="Prepare outreach").click()
        page.wait_for_url(re.compile(r"/deliver/pkg_"))
        pkg_id = page.url.rsplit("/", 1)[1]
        gmail = page.get_by_role("article", name="Gmail")
        msg = page.get_by_role("region", name="Message")
        checks = page.locator(".aa-checks")
        # SIMULATED here: the replayed upstream. v4 13: the reason sentence, then what prevents direct delivery under "Before you send"
        expect(page.locator(".aa-review-head")).to_contain_text("Connect account", timeout=30000)
        expect(msg).to_contain_text("Connect your Gmail to send messages you approve from your own address.")
        expect(checks).to_contain_text("Add your name as the sender")
        expect(checks).to_contain_text("award")  # the flagged sentence, whole, with Keep it as mine
        page.screenshot(path=SHOTS / "01-outreach-v1.png", full_page=True)

        # the same package in a deployment without delivery configured: truthful wording, never "Connect"
        monkeypatch.setattr(config, "REPLAY_CASES", [])
        page.reload()
        expect(page.locator(".aa-review-head")).to_contain_text("Direct delivery unavailable", timeout=10000)
        expect(gmail).not_to_contain_text("Connect")
        expect(msg).to_contain_text("Gmail sending isn’t available in this deployment. Copy, download or open messages in your mail app.")
        page.screenshot(path=SHOTS / "02-unavailable.png", full_page=True)
        from test_markets_browser import SYNTHETIC
        monkeypatch.setattr(config, "REPLAY_CASES", [SYNTHETIC])
        page.reload()

        # fix both items → v2 (Edit opens the review); preview, downloads, mailto
        msg.get_by_role("button", name="Edit").click()
        page.get_by_label("Your name, as the sender").fill("Maya at Tidewell")
        page.get_by_role("button", name="Remove paragraph 3").click()
        page.get_by_role("region", name="The message").get_by_role("button", name="Save changes").click()
        expect(page.locator("main")).to_contain_text("Saved as a new version", timeout=10000)
        assert db.get_record(pkg_id)["version"] == 2
        expect(gmail.get_by_role("button", name="Connect Gmail to send from here")).to_be_visible(timeout=10000)
        page.get_by_text("Preview the exact message").click()
        expect(page.locator("pre")).to_contain_text("Maya at Tidewell")
        with page.expect_download() as d:
            page.get_by_role("link", name="Download .eml draft").click()
        eml = open(d.value.path(), "rb").read()
        assert b"Subject: Tidewell canned teas for your makers shelf" in eml and b"award" not in eml and b"hello@kettlecrane.example" in eml
        # v4 13 offers the .eml, the attachments and the mail app; the outreach zip has no button of its own any more
        z = zipfile.ZipFile(io.BytesIO(page.request.get(f"{base}/api/projects/{pid}/packages/{pkg_id}/exports/outreach_zip?version=2").body()))
        assert z.read("message.eml") == eml and "manifest.json" in z.namelist()
        assert page.get_by_role("link", name="Open in your mail app").get_attribute("href").startswith("mailto:hello@kettlecrane.example?subject=")
        expect(page.get_by_role("region", name="Use it yourself")).to_contain_text("None of these send anything")

        # SIMULATED connect → edit (v3) → the one approve-and-send action → reload. v4 has no separate Approve step to outdate
        # (its in-page action approves this exact version and runs it), so the edit is checked by the version that's sent.
        gmail.get_by_role("button", name="Connect Gmail to send from here").click()
        send = gmail.get_by_role("button", name="Approve and send with Gmail")
        expect(send).to_be_visible(timeout=10000)
        page.get_by_label("Subject").fill("A shelf trial at Kettle & Crane?")
        page.get_by_role("region", name="The message").get_by_role("button", name="Save changes").click()
        expect(page.get_by_label("Subject")).to_have_value("A shelf trial at Kettle & Crane?", timeout=10000)
        assert db.get_record(pkg_id)["version"] == 3
        send.click()
        expect(gmail).to_contain_text("Simulated · nothing was sent", timeout=10000)
        expect(gmail).to_contain_text("Simulated: no provider was called.")
        page.reload()
        expect(gmail).to_contain_text("Simulated: no provider was called.", timeout=10000)
        page.screenshot(path=SHOTS / "03-sent-simulated.png", full_page=True)

        # digital → Develop → Prepare campaign
        page.goto(base + f"/p/{pid}/opportunities/toronto/digital")
        page.get_by_role("button", name="Investigate digital").click()
        page.get_by_role("button", name="Develop this approach").click(timeout=30000)
        page.get_by_role("region", name="Developed draft").get_by_role("button", name="Prepare campaign").click(timeout=30000)
        page.wait_for_url(re.compile(r"/deliver/pkg_"))
        gads = page.get_by_role("article", name="Google Ads")
        ad = page.get_by_role("region", name="Ad package")
        expect(ad).to_contain_text("Enter a daily budget", timeout=30000)
        expect(gads).to_be_visible()
        assert page.get_by_role("article", name="Meta Ads").count() == 0  # digital is Google Ads only on the product path
        assert page.get_by_role("article", name="Gmail").count() == 0  # only the destinations the experiment names
        ad.get_by_role("button", name="Edit").click()
        page.get_by_label("Daily budget").fill("25")
        page.get_by_label("Start", exact=True).fill("2026-11-02")
        page.get_by_role("region", name="Budget, timing and settings").get_by_role("button", name="Save").click()
        # each save re-keys the edit sections to the new version, so the next field waits for this one to land
        expect(page.get_by_role("region", name="Budget, timing and settings")).to_contain_text("a day in total", timeout=10000)
        page.get_by_label("Radius (km)").fill("20")
        page.get_by_role("region", name="Audience rationale and targeting").get_by_role("button", name="Save").click()
        expect(page.get_by_role("region", name="Audience rationale and targeting")).to_contain_text("+ 12.4 mi", timeout=10000)  # 20 km, shown in miles
        ad.get_by_role("button", name="Done editing").click()
        expect(ad).not_to_contain_text("Enter a daily budget", timeout=10000)
        page.get_by_text("Google Ads: what Landfall would send").click()
        details = page.get_by_role("group", name="Google Ads details")
        expect(details).to_contain_text("Maximize Clicks (target_spend)")
        expect(details).to_contain_text("Google Search only")
        expect(details).to_contain_text("25000000 micros of")  # the founder's 25 a day, in micros
        expect(details).to_contain_text("Toronto + 12.4 mi (proximity)")  # the 20 km radius
        expect(details).to_contain_text("sparkling tea toronto [PHRASE]")
        expect(page.locator("main")).to_contain_text("SYNTHETIC REQUEST PLAN · not provider validated")
        with page.expect_download() as d:
            page.get_by_role("link", name="Download creative list").click()
        names = zipfile.ZipFile(io.BytesIO(open(d.value.path(), "rb").read())).namelist()
        assert "plans/google_ads.json" in names and "setup-google_ads.txt" in names, names
        page.screenshot(path=SHOTS / "04-campaign.png", full_page=True)
        pkg_url = page.url

        m = page.context.new_page()
        m.set_viewport_size({"width": 390, "height": 844})
        m.goto(pkg_url)
        m.get_by_role("article", name="Google Ads").wait_for(timeout=10000)
        m.wait_for_timeout(400)
        m.screenshot(path=SHOTS / "05-mobile-campaign.png", full_page=True)
        no_wide(m, "mobile-campaign")
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement !== document.body")
        assert not errors, errors
        b.close()


def test_slice6_recovery_and_account_paths_in_the_browser(server, monkeypatch):
    """REVIEW-SLICE-06 P6-I2/I3, in a live-mode deployment with SYNTHETIC providers: OAuth is intercepted and sent back to
    Landfall's real callback. Gmail: connect → send → expired token → Reconnect → approve → send once. Google Ads (digital
    is Google Ads only on the product path since 28 Oct 2026): connect → choose the account → a definitive rejection →
    fix → approve → an uncertain attempt, never retried → "It isn't there" → approve → one new attempt, created paused."""
    from playwright.sync_api import expect, sync_playwright
    from test_slice6 import OAuth, Provider, configure
    setup(monkeypatch)
    base = server(FakeQloo(), Strategist())
    monkeypatch.setattr(config, "LIVE_STAGES", STAGES + ("specialist", "specialist_check", "specialist_places", "specialist_search",
                                                         "specialist_fetch", "products_fetch", "develop", "delivery_copy", "delivery_fetch"))
    prov, oauth = Provider(), OAuth()
    oauth_paths = ("oauth/access_token", "/me", "/me/adaccounts", "/me/accounts", "/token", "userinfo")

    def send(req, headers):  # Google Ads' account discovery and its customer read are the connect step, not the ad write
        if req["url"].endswith(oauth_paths + ("customers:listAccessibleCustomers",)) or "FROM customer" in str(req.get("json")):
            return oauth(req, headers)
        return prov(req, headers)
    monkeypatch.setattr(delivery, "SEND", send)

    def to_callback(route):  # the provider's consent screen, answered at once: back to Landfall with a code
        state = re.search(r"state=([^&]+)", route.request.url).group(1)
        route.fulfill(status=302, headers={"Location": f"{base}/api/connections/callback?state={state}&code=c"})
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.route(re.compile(r"https://(accounts\.google\.com|www\.facebook\.com)/.*"), to_callback)
        enter(page, base)
        open_brief(page)
        pid = page.url.split("/p/")[1].split("/")[0]
        explore(page, timeout=120000)
        research_toronto(page, base, pid)
        page.get_by_role("region", name="Toronto").get_by_text("DONE", exact=True).wait_for(timeout=60000)
        for method, button in (("retail", "Prepare outreach"), ("digital", "Prepare campaign")):
            page.goto(base + f"/p/{pid}/opportunities/toronto/{method}")
            page.get_by_role("button", name=re.compile(f"^Investigate {method}$")).click()
            page.get_by_role("button", name="Develop this approach").click(timeout=30000)
            page.get_by_role("region", name="Developed draft").get_by_role("button", name=button).click(timeout=30000)
            page.wait_for_url(re.compile(r"/deliver/pkg_"))
            page.locator(".aa-review-head h1").wait_for(timeout=30000)
            if method == "retail":
                outreach_url = page.url
        digital_url = page.url
        configure(monkeypatch)  # a configured, live deployment from here on
        monkeypatch.setitem(config._ENV, "LANDFALL_GOOGLE_ADS_ACCESS", "EXPLORER")  # account discovery; 6.7's TEST lock is test_slice67

        # Gmail: connect, fix, approve and send → 401 → Reconnect → approve and send once more
        page.goto(outreach_url)
        gmail = page.get_by_role("article", name="Gmail")
        gmail.get_by_role("button", name="Connect Gmail to send from here").click()
        expect(gmail.get_by_role("button", name="Connect Gmail to send from here")).to_have_count(0, timeout=15000)
        page.get_by_role("region", name="Message").get_by_role("button", name="Edit").click()
        page.get_by_label("Your name, as the sender").fill("Maya at Tidewell")
        page.get_by_role("button", name="Remove paragraph 3", exact=True).click()
        page.get_by_role("region", name="The message").get_by_role("button", name="Save changes").click()
        send = gmail.get_by_role("button", name="Approve and send with Gmail")
        expect(send).to_be_enabled(timeout=10000)
        prov.reject["message"] = (401, {"error": {"message": "Invalid Credentials"}})
        send.click()
        expect(gmail).to_contain_text("Gmail refused it", timeout=10000)
        page.screenshot(path=SHOTS / "10-expired.png", full_page=True)
        prov.reject.clear()
        gmail.get_by_role("button", name="Reconnect Gmail").click()
        expect(gmail.get_by_role("button", name="Reconnect Gmail")).to_have_count(0, timeout=15000)
        gmail.get_by_role("button", name="Approve and send with Gmail").click(timeout=10000)
        expect(gmail).to_contain_text("Gmail accepted it", timeout=10000)  # live mode: the SYNTHETIC provider answered, not the demo
        assert prov.writes == ["message"]

        # Google Ads: connect → choose the account → rejection → fix → approve → uncertain → it isn't there → approve → once
        page.goto(digital_url)
        gads = page.get_by_role("article", name="Google Ads")
        page.get_by_role("region", name="Ad package").get_by_role("button", name="Edit").click()
        budget = page.get_by_role("region", name="Budget, timing and settings")
        page.get_by_label("Daily budget").fill("25")
        page.get_by_label("Currency").fill("CAD")
        page.get_by_label("Start", exact=True).fill("2026-11-02")
        budget.get_by_role("button", name="Save").click()
        expect(budget).to_contain_text("a day in total", timeout=10000)  # each save re-keys the edit sections
        page.get_by_label("Radius (km)").fill("20")
        page.get_by_role("region", name="Audience rationale and targeting").get_by_role("button", name="Save").click()
        expect(page.get_by_role("region", name="Audience rationale and targeting")).to_contain_text("+ 12.4 mi", timeout=10000)  # 20 km, shown in miles
        gads.get_by_role("button", name="Connect Google Ads to create it paused").click(timeout=10000)
        expect(gads).to_contain_text("Choose the account", timeout=10000)
        gads.get_by_role("button", name="Use this account").click()  # the one accessible account, pre-chosen
        create = gads.get_by_role("button", name="Approve and create paused in Google Ads")
        expect(create).to_be_enabled(timeout=10000)
        expect(gads).to_contain_text("Tidewell (1234567890)")
        prov.reject["mutate"] = (400, {"error": {"code": 400, "details": [{
            "@type": "type.googleapis.com/google.ads.googleads.v26.errors.GoogleAdsFailure",
            "errors": [{"message": "Budget amount is too small"}]}]}})
        create.click()
        expect(gads).to_contain_text("Google Ads refused it", timeout=10000)
        expect(gads).to_contain_text("Budget amount is too small")
        assert prov.writes == ["message"]  # one atomic mutate (partialFailure false): a rejection created nothing
        prov.reject.clear()
        page.get_by_role("region", name="Ad package").get_by_role("button", name="Edit").click()  # connecting reloaded the page
        page.get_by_label("Daily budget").fill("40")  # the fix: a new version
        budget.get_by_role("button", name="Save").click()
        expect(budget).to_contain_text("40", timeout=10000)
        # v4 B13 "Partly created … create only the missing" can't arise here: Google Ads applies all of the mutate or none of
        # it. B13 stays covered offline (tests/test_v4_delivery.py); this checks an uncertain attempt is never re-created.
        expect(gads).not_to_contain_text("Partly created")
        prov.after_commit["mutate"] = "timeout"
        create.click(timeout=10000)
        expect(gads).to_contain_text("Not sure it was created", timeout=10000)
        expect(gads).to_contain_text("Landfall won’t retry on its own")
        expect(gads.get_by_role("button", name=re.compile("^Approve and create"))).to_have_count(0)
        page.screenshot(path=SHOTS / "11-uncertain.png", full_page=True)
        prov.after_commit.clear()
        page.reload()  # a reload, too, never re-sends
        expect(gads).to_contain_text("Not sure it was created", timeout=10000)
        assert prov.writes == ["message", "mutate"], prov.writes
        gads.get_by_role("button", name="It isn’t there").click()
        expect(page.locator("main")).to_contain_text("Closed. Approve again to try once more.", timeout=10000)
        gads.get_by_role("button", name=re.compile("^Approve and create")).click(timeout=10000)
        expect(gads).to_contain_text("Created, paused", timeout=10000)
        assert prov.writes == ["message", "mutate", "mutate"], prov.writes  # the uncertain one, then the one approved after it
        page.screenshot(path=SHOTS / "12-created.png", full_page=True)
        assert not errors, errors
        b.close()
