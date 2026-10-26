"""More resilient brand reading (owner, 25 Oct 2026): a thin homepage's sitemap and its own off-site links, and a
headless render for JavaScript shells. Rich homepages keep the slice-1 request byte for byte, so recorded reads replay.
All inputs are SYNTHETIC; the render test serves a local page."""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from landfall import brand, config, fetch, ledger
from landfall.cassette import Cassette

PEPSI = ["https://contact.pepsico.com/stubborn/about-us", "https://contact.pepsico.com/stubborn/faqs",
         "https://www.instagram.com/stubbornsoda", "https://www.facebook.com/x", "https://apps.apple.com/app/1",
         "https://www.stubbornsoda.com/", "/relative", "mailto:a@b.c", "https://www.pepsicobeveragefacts.com/Home/Product?formula=F1"]


def test_outbound_links_keep_brand_pages_and_drop_social_and_app_links():
    assert fetch.outbound("https://www.stubbornsoda.com/", PEPSI) == [
        "https://contact.pepsico.com/stubborn/about-us", "https://contact.pepsico.com/stubborn/faqs",
        "https://www.pepsicobeveragefacts.com/Home/Product?formula=F1"]


def test_sitemap_urls_read_urlsets_and_indexes():
    xml = ("<urlset><url><loc> https://x.com/about </loc></url><url><loc>https://x.com/a?b=1&amp;c=2</loc></url>"
           "</urlset><sitemapindex><sitemap><loc>https://x.com/products.xml</loc></sitemap></sitemapindex>")
    assert fetch.sitemap_urls(xml) == ["https://x.com/about", "https://x.com/a?b=1&c=2", "https://x.com/products.xml"]


@pytest.mark.parametrize("chars, links, render", [(0, 0, True), (149, 50, True), (399, 2, True), (399, 3, False),
                                                  (277, 46, False), (989, 1, False)])
def test_render_only_javascript_shells(chars, links, render):
    """Komono's 277-character store locator (46 links) and Stubborn Soda's 989-character homepage are read as they are."""
    assert fetch.needs_render({"text": "x" * chars, "links": ["l"] * links}) is render


def test_a_rich_homepage_keeps_the_recorded_request_exactly():
    first = {"url": "https://komono.com/", "title": "KOMONO", "text": "Watches"}
    old = ("Brand website: https://komono.com/\n\nHOMEPAGE https://komono.com/\nTITLE KOMONO\nTEXT\nWatches\n\n"
           "SAME-SITE LINKS ON THE HOMEPAGE:\nhttps://komono.com/a\n\nRead the pages you need, then return the brief.")
    assert brand.reader_prompt("https://komono.com/", first, "https://komono.com/a", [], []) == old
    thin = brand.reader_prompt("https://komono.com/", first, "", ["https://komono.com/about"], PEPSI[:1])
    assert "OTHER PAGES OF THIS SITE (from its sitemap):\nhttps://komono.com/about" in thin
    assert "PAGES ON OTHER SITES THAT THE HOMEPAGE LINKS TO" in thin and PEPSI[0] in thin


def test_recorded_reads_still_replay_unchanged():
    """Every recorded homepage is rich or long enough: no thin-site section and no render is triggered."""
    dirs = [config.ROOT / "fixtures" / "synthetic" / "tidewell-brand-v6"] + [
        config.ROOT / "live-captures-private" / "cases" / c for c in ("L3-A-komono/v1", "L3-B-gotbag/v1")]
    for d in (d for d in dirs if (d / "exchanges.jsonl").exists()):
        for line in filter(None, (d / "exchanges.jsonl").read_text().split("\n")):
            ex = json.loads(line)
            if ex["provider"] == "fetch" and ex["response"].get("status") == 200 and not ex["request"]["url"].endswith(".txt"):
                x = fetch.extract(ex["response"])
                assert not fetch.needs_render(x), ex["request"]["url"]
                if ex["request"]["url"].rstrip("/").count("/") == 2:  # a homepage
                    assert len(x["links"]) >= brand.THIN_LINKS, ex["request"]["url"]


# ---- the headless render: a local JavaScript-only page ----
SHELL = (b"<!doctype html><title>Shell</title><div id=app></div><script>document.getElementById('app').innerHTML="
         b"'<h1>Fizzy Co</h1><p>' + 'We make small-batch sodas from cane sugar and real fruit, sold in Portland grocers '"
         b".repeat(4) + '</p>'</script>")


class _Shell(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(SHELL)

    def log_message(self, *a):
        pass


@pytest.fixture
def shell_site(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _Shell)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(config, "ALLOW_PRIVATE_FETCH", True)
    monkeypatch.setattr(config, "HOST_MAP", {"fizzy.example": ("127.0.0.1", srv.server_port)})
    yield f"http://fizzy.example:{srv.server_port}/"
    srv.shutdown()


def test_a_javascript_shell_is_rendered_recorded_and_counted(shell_site, tmp_path):
    cas = Cassette("record", tmp_path / "rec")
    plain = fetch.extract(fetch.get(shell_site, cas, "brand"))
    assert fetch.needs_render(plain)
    shown = fetch.render(shell_site, cas, "brand")
    text = fetch.extract(shown)["text"]
    assert shown["status"] == 200 and shown["rendered"] and "small-batch sodas from cane sugar" in text
    ops = [json.loads(x)["op"] for x in (tmp_path / "rec" / "exchanges.jsonl").read_text().split("\n") if x]
    assert ops == ["GET", "RENDER"]  # recorded like any fetch, so a replay needs no browser
    lines = [json.loads(x) for x in config.LEDGER_PATH.read_text().split("\n") if x]
    assert [x["operation"] for x in lines if x["kind"] == "attempt" and x["provider"] == "fetch"] == ["GET", "RENDER"]
    replayed = fetch.render(shell_site, Cassette("replay", replay_dirs=[tmp_path / "rec"]), "brand")
    assert replayed["body"] == shown["body"]


def test_rendered_pages_never_reach_private_addresses(monkeypatch):
    """The route guard blocks a subresource whose host resolves privately, and images/fonts entirely."""
    calls = []

    class Route:
        def __init__(self, url, kind="script"):
            self.request = type("R", (), {"url": url, "resource_type": kind})()

        def continue_(self):
            calls.append(("ok", self.request.url))

        def abort(self):
            calls.append(("blocked", self.request.url))
    monkeypatch.setattr(config, "ALLOW_PRIVATE_FETCH", False)
    monkeypatch.setattr(fetch, "_resolve", lambda host, port: ["10.0.0.1"] if host == "internal.example" else ["45.223.17.132"])
    seen = {}
    for r in (Route("http://internal.example/x"), Route("https://cdn.example/app.js"), Route("https://cdn.example/a.png", "image"),
              Route("file:///etc/passwd"), Route("data:text/plain,hi")):
        fetch._guard_route(r, seen)
    assert calls == [("blocked", "http://internal.example/x"), ("ok", "https://cdn.example/app.js"),
                     ("blocked", "https://cdn.example/a.png"), ("blocked", "file:///etc/passwd"), ("ok", "data:text/plain,hi")]


def test_a_thin_homepage_may_read_its_own_off_site_links_and_nothing_else(monkeypatch, tmp_path):
    """Stubborn Soda: one same-site link; the brand's facts live on pages its homepage links to (PepsiCo)."""
    from landfall import db
    from landfall.runner import Run
    outputs, prompts = [], []

    class FakeAgent:
        def __init__(self, **kw):
            self.tool = kw["tools"][0]

        def __call__(self, prompt, **kw):
            prompts.append(prompt)
            for u in (PEPSI[0], "https://evil.example/", "https://www.stubbornsoda.com/about"):
                outputs.append(self.tool(page_url=u, purpose="about"))
            return type("R", (), {"structured_output": brand.BriefDraft(brand_name="S", facts=[], interpretation=[],
                                                                          unknowns=[])})()
    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", "prj_s", "s", "https://www.stubbornsoda.com/", "live", None, None, db.now())
    monkeypatch.setattr(brand, "Agent", FakeAgent)
    monkeypatch.setattr(brand, "make_model", lambda *a, **k: None)
    monkeypatch.setattr(brand, "robots_allows", lambda run, u: True)
    monkeypatch.setattr(brand, "read_page", lambda run, u: {"id": "src", "url": u, "title": "Page", "status": 200,
                                                            "text": "About Stubborn Soda.", "links": []})
    monkeypatch.setattr(brand, "_sitemap_pages", lambda run, url, sites, known: [])  # no sitemap, as on the live site
    run = Run("run_s", "prj_s", "brand_read", {}, cassette=None)
    brand._run_reader(run, "https://www.stubbornsoda.com/", {"stubbornsoda.com"},
                      {"links": ["https://www.stubbornsoda.com/"], "outbound": fetch.outbound("https://www.stubbornsoda.com/", PEPSI)})
    assert "PAGES ON OTHER SITES THAT THE HOMEPAGE LINKS TO" in prompts[0] and PEPSI[0] in prompts[0]
    assert outputs[0].startswith("PAGE") and outputs[1].startswith("Not read") and outputs[2].startswith("PAGE")
