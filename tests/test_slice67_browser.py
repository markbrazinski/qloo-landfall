"""Slice 6.7 browser check (BUILD-PLAN §6.7.10 G4): the test-delivery disclosure, the button and confirm text, and the
after-send wording, at 1280 px and 390 px. S01 → research Toronto → retail → Develop → Prepare outreach (the slice-6
route, SYNTHETIC upstream), then the deployment switches to live test delivery against a SYNTHETIC Gmail with fictional
addresses. Screenshots: .landfall/browser-slice67/ (git-ignored)."""
import re

import pytest
from cryptography.fernet import Fernet
from test_markets import FakeQloo, Strategist
from test_markets_browser import enter, explore, open_brief, server  # noqa: F401
from test_slice4 import STAGES
from test_slice6 import Provider
from test_slice6_browser import no_wide, research_toronto, setup
from test_slice67 import INBOX, SENDER, connect_gmail, test_mode

from landfall import config, delivery

SHOTS = config.ROOT / ".landfall" / "browser-slice67"
pytestmark = pytest.mark.skipif(not (config.ROOT / "web" / "dist" / "index.html").exists(), reason="frontend not built")


def test_slice67_test_delivery_in_the_browser(server, monkeypatch):
    from playwright.sync_api import expect, sync_playwright
    setup(monkeypatch)
    base = server(FakeQloo(), Strategist())
    monkeypatch.setattr(config, "LIVE_STAGES", STAGES + ("specialist", "specialist_check", "specialist_places", "specialist_search",
                                                         "specialist_fetch", "products_fetch", "develop", "delivery_copy", "delivery_fetch"))
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
        errors, dialogs = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
        enter(page, base)
        open_brief(page)
        pid = page.url.split("/p/")[1].split("/")[0]
        explore(page, timeout=120000)
        research_toronto(page, base, pid)
        page.get_by_role("region", name="Toronto").get_by_text("DONE", exact=True).wait_for(timeout=60000)
        page.goto(base + f"/p/{pid}/opportunities/toronto/retail")
        page.get_by_role("button", name="Investigate retail").click()
        page.get_by_role("button", name="Develop this approach").click(timeout=30000)
        page.get_by_role("region", name="Developed draft").get_by_role("button", name="Prepare outreach").click(timeout=30000)
        page.wait_for_url(re.compile(r"/deliver/pkg_"))
        page.get_by_role("region", name="Message").get_by_role("button", name="Edit").click(timeout=30000)
        page.get_by_label("Your name, as the sender").fill("Maya at Tidewell", timeout=30000)
        page.get_by_role("button", name="Remove paragraph 3").click()
        page.get_by_role("region", name="The message").get_by_role("button", name="Save changes").click()
        expect(page.locator("main")).to_contain_text("Saved as a new version", timeout=10000)

        # the deployment goes live in test delivery: a SYNTHETIC Gmail, fictional addresses, the test sender connected
        monkeypatch.setattr(config, "REPLAY_CASES", [])
        for k, v in {"LANDFALL_TOKEN_KEY": Fernet.generate_key().decode(), "google_oauth_client_id": "cid",
                     "google_oauth_client_secret": "cs"}.items():
            monkeypatch.setitem(config._ENV, k, v)
        test_mode(monkeypatch)
        gmail_fake = Provider()
        monkeypatch.setattr(delivery, "SEND", gmail_fake)
        connect_gmail(pid)
        page.reload()
        gmail = page.get_by_role("article", name="Gmail")
        expect(gmail).to_contain_text("TEST DELIVERY", timeout=10000)
        expect(gmail).to_contain_text("This outreach is prepared for Kettle & Crane")
        expect(gmail).to_contain_text("Kettle & Crane will not be contacted.")
        gmail.get_by_text("What Gmail will receive").click()
        expect(gmail.locator("pre")).to_contain_text("To: Private test inbox")
        expect(gmail.locator("pre")).to_contain_text("[Landfall test] ")
        page.screenshot(path=SHOTS / "01-test-delivery-disclosure.png", full_page=True)

        # v4: the confirm text is on the page beside the one action (no browser dialog)
        expect(gmail).to_contain_text("Approves this exact version and submits it to Gmail for the private test inbox only. "
                                      "Kettle & Crane won’t be contacted.")
        gmail.get_by_role("button", name="Send test email").click(timeout=10000)
        expect(gmail).to_contain_text("Test email sent · Kettle & Crane not contacted", timeout=10000)
        assert dialogs == []
        assert gmail_fake.writes == ["message"]
        page.reload()
        expect(gmail).to_contain_text("Kettle & Crane has not been contacted.", timeout=10000)
        expect(gmail).to_contain_text("message msg1")
        expect(gmail).not_to_contain_text("TEST DELIVERY")  # the before-send notice gives way to the outcome
        page.screenshot(path=SHOTS / "02-test-email-sent-1280.png", full_page=True)
        html = page.content()
        assert SENDER not in html and INBOX not in html

        page.set_viewport_size({"width": 390, "height": 844})  # the same browser: projects belong to its session
        page.reload()
        expect(gmail).to_contain_text("Test email sent", timeout=10000)
        no_wide(page, "deliver-390")
        page.screenshot(path=SHOTS / "03-test-email-sent-390.png", full_page=True)
        assert not errors, errors
        b.close()
