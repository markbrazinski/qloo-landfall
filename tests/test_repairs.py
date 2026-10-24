"""Regressions for slice-1 review findings 1, 2, 3 (checker inputs) and 5 (docs/REVIEW-SLICE-01.md)."""
import pytest
from fastapi.testclient import TestClient

from landfall import app as app_module
from landfall import brand, db
from landfall.runner import Run

CANARY = "OUTSIDE-CANARY-7f3a"


# ---- finding 1: static serving stays inside the public build ----
@pytest.fixture
def public_root(tmp_path, monkeypatch):
    pub = tmp_path / "public"
    pub.mkdir()
    (pub / "index.html").write_text("<!doctype html><title>Landfall</title>")
    (pub / "lockup.svg").write_text("<svg/>")
    outside = tmp_path / "secret.txt"
    outside.write_text(CANARY)
    (pub / "leak.txt").symlink_to(outside)          # symlink escaping the public root
    (pub / "nested").mkdir()
    (pub / "nested" / "up").symlink_to(tmp_path)    # directory symlink escaping it
    monkeypatch.setattr(app_module, "WEB", pub)
    return pub


@pytest.mark.parametrize("path", ["/..%2f..%2fREADME.md", "/%2e%2e/%2e%2e/README.md", "/..%2fsecret.txt",
                                  "//etc/passwd", "/leak.txt", "/nested/up/secret.txt"])
def test_static_paths_cannot_escape_the_public_root(public_root, path):
    r = TestClient(app_module.app).get(path)
    assert CANARY not in r.text and "Landfall" in r.text or r.status_code == 404


def test_static_assets_and_app_routes_still_work(public_root):
    c = TestClient(app_module.app)
    assert c.get("/lockup.svg").text == "<svg/>"
    for route in ("/", "/p/prj_abc123"):
        r = c.get(route)
        assert r.status_code == 200 and "<title>Landfall</title>" in r.text


# ---- finding 2: evidence requires the claim to belong to the source's project and cite that source ----
def _project(client, pid):
    client.get("/api/projects/none")  # obtain a session cookie
    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", pid, client.cookies["lf_session"], "https://example.com/", "live",
         None, None, db.now())


def _source(pid, sid):
    db.x("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?)", sid, pid, "https://example.com/", "https://example.com/", 200,
         db.now(), "T", "public text", None, 0)


def test_source_drawer_rejects_foreign_and_unrelated_claims():
    a, b = TestClient(app_module.app), TestClient(app_module.app)
    _project(a, "prj_a")
    _project(b, "prj_b")
    _source("prj_a", "src_a")
    _source("prj_a", "src_a2")
    own = db.put_record("claim", "prj_a", {"label": "Founded", "value": "2009", "quote": "Founded in 2009",
                                           "source_ids": ["src_a"], "subject": {"key": "founded"}})
    unrelated = db.put_record("claim", "prj_a", {"label": "Shipping", "value": "US", "quote": "ships",
                                                 "source_ids": ["src_a2"]})
    foreign = db.put_record("claim", "prj_b", {"quote": "PRIVATE CLAIM CANARY", "source_ids": ["src_a"]})
    assert a.get("/api/sources/src_a", params={"claim": foreign["id"]}).status_code == 404
    assert a.get("/api/sources/src_a", params={"claim": unrelated["id"]}).status_code == 404
    assert a.get("/api/sources/src_a").status_code == 422           # a claim is required
    assert b.get("/api/sources/src_a", params={"claim": own["id"]}).status_code == 404  # other session
    ok = a.get("/api/sources/src_a", params={"claim": own["id"]}).json()
    assert ok["excerpt"] == "Founded in 2009" and ok["claim"]["label"] == "Founded" and ok["claim"]["value"] == "2009"


# ---- finding 3: the checker judges the claim as displayed (field + label + value) ----
def test_checker_receives_the_full_displayed_claim(monkeypatch):
    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", "prj_c", "s", "https://got-bag.com/", "live", None, None, db.now())
    page = "Seit 2018 entwickeln wir Taschen und Rucksäcke aus recycelten Hauptmaterialien."
    db.x("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?)", "src_c", "prj_c", "https://got-bag.com/pages/about-us",
         "https://got-bag.com/pages/about-us", 200, db.now(), "About", page, None, 0)
    seen = {}

    class FakeAgent:
        def __init__(self, **kw):
            seen["system"] = kw["system_prompt"]

        def __call__(self, prompt, **kw):
            seen["prompt"] = prompt
            v = brand.Verdict(claim_id="f0", claim_as_read="The brand was founded in 2018", verdict="PARTIAL",
                              reason="The quote dates product development, not founding.")
            return type("R", (), {"structured_output": brand.Verdicts(verdicts=[v])})()

    monkeypatch.setattr(brand, "Agent", FakeAgent)
    monkeypatch.setattr(brand, "make_model", lambda *a, **k: None)
    run = Run("run_c", "prj_c", "brand_read", {}, cassette=None)
    draft = {"facts": [{"field": "founded", "label": "Founded", "value": "2018",
                        "source_url": "https://got-bag.com/pages/about-us", "quote": page}]}
    out = brand._check(run, draft)
    assert "field: founded | label: Founded | value: 2018" in seen["prompt"]
    assert "founding date" in seen["system"] and brand.CHECKER_VERSION == "support_check@3"
    c = out[0]
    assert c["quote_found"] is True and c["support"] == "PARTIAL" and c["state"] == "TO_CHECK"
    assert c["reason"].startswith("The quote is on the page but does not establish this claim")


# ---- finding 5: incomplete reading never becomes a site-wide absence claim ----
def test_unknowns_after_a_failed_page_are_not_absence_claims():
    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", "prj_u", "s", "https://komono.com/", "live", None, None, db.now())
    db.event("prj_u", "activity", {"task": "Read /pages/shipping-policy", "status": "UNAVAILABLE", "detail": "HTTP 404"})
    b = db.put_record("brief", "prj_u", {"brand_name": "K", "route": "site", "facts": [], "interpretation": [],
                                         "unknowns": [{"label": "International shipping", "why_it_matters": "x"}],
                                         "corrections": []})
    basis = app_module._brief_view(db.get_record(b["id"]))["unknown_basis"]
    assert basis == "Not established from the pages read (1 page unavailable)"
    assert "not on the site" not in basis.lower()


def test_a_malformed_citation_fails_one_fact_not_the_run(monkeypatch):
    """stubbornsoda.com, 24 Oct: the reader cited 'httpshttps://…' and the whole run failed in the quote check."""
    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", "prj_m", "s", "https://www.stubbornsoda.com/", "live", None, None, db.now())
    page = "Stubborn Soda is craft soda made with cane sugar, sold in stores across the US."
    db.x("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?)", "src_m", "prj_m", "https://www.stubbornsoda.com/",
         "https://www.stubbornsoda.com/", 200, db.now(), "Home", page, None, 0)
    seen = {}

    class FakeAgent:
        def __init__(self, **kw):
            pass

        def __call__(self, prompt, **kw):
            seen["prompt"] = prompt
            v = brand.Verdict(claim_id="f0", claim_as_read="Craft soda made with cane sugar", verdict="SUPPORTED", reason="ok")
            return type("R", (), {"structured_output": brand.Verdicts(verdicts=[v])})()

    monkeypatch.setattr(brand, "Agent", FakeAgent)
    monkeypatch.setattr(brand, "make_model", lambda *a, **k: None)
    run = Run("run_m", "prj_m", "brand_read", {}, cassette=None)
    draft = {"facts": [
        {"field": "products", "label": "What you sell", "value": "Craft soda with cane sugar",
         "source_url": "https://www.stubbornsoda.com/", "quote": "craft soda made with cane sugar"},
        {"field": "where_sold", "label": "Where you sell", "value": "Stores across the US",
         "source_url": "httpshttps://www.stubbornsoda.com/", "quote": "sold in stores across the US"},
        {"field": "shipping", "label": "Shipping", "value": "US", "source_url": "https://www.stubbornsoda.com/shipping",
         "quote": "we ship to the US"}]}
    out = brand._check(run, draft)
    assert [c["state"] for c in out] == ["SUPPORTED", "TO_CHECK", "TO_CHECK"]
    assert "not one Landfall read" in out[1]["reason"] and "not one Landfall read" in out[2]["reason"]
    assert "httpshttps" not in seen["prompt"]  # an unread citation is never sent for a support judgment


# ---- inferred from: every interpretation carries a checked basis ----
def test_inferred_from_keeps_only_references_landfall_can_resolve():
    read = {"https://k.example/pages/about": "src_about"}
    states = {"clm_1": "SUPPORTED", "clm_2": "TO_CHECK"}
    b = brand._resolve_basis({"basis_facts": [1, 2, 9, 0], "basis_pages": ["https://k.example/pages/about",
                                                                         "https://k.example/never-read", "httpshttps://x"]},
                             ["clm_1", "clm_2"], states, read)
    assert b["claim_ids"] == ["clm_1", "clm_2"] and b["source_ids"] == ["src_about"]
    assert b["dropped"] == ["fact 9", "fact 0", "https://k.example/never-read", "httpshttps://x"]
    assert b["established"] is True


def test_reading_resting_only_on_unestablished_facts_is_not_established():
    b = brand._resolve_basis({"basis_facts": [2], "basis_pages": []}, ["clm_1", "clm_2"],
                             {"clm_1": "SUPPORTED", "clm_2": "TO_CHECK"}, {})
    assert b["established"] is False
    assert brand._resolve_basis({"basis_facts": [], "basis_pages": []}, ["clm_1"], {"clm_1": "SUPPORTED"}, {})["established"] is False


def test_correcting_a_cited_fact_flags_the_reading_for_review():
    client = TestClient(app_module.app)
    _project(client, "prj_i")
    _source("prj_i", "src_i")
    fact = db.put_record("claim", "prj_i", {"label": "Where you sell", "value": "41 shops in Portland", "state": "SUPPORTED",
                                            "source_ids": ["src_i"], "quote": "41 shops", "subject": {"key": "where_sold"}})
    reading = db.put_record("claim", "prj_i", {"label": "Home market", "value": "Portland", "state": "INFERRED", "vc": "I",
                                               "source_ids": [], "basis_claim_ids": [fact["id"]], "basis_established": True,
                                               "reasoning": "Stockists are in Portland", "subject": {"key": "home_market"}})
    db.put_record("brief", "prj_i", {"brand_name": "T", "route": "site", "facts": [fact["id"]],
                                     "interpretation": [reading["id"]], "unknowns": [], "corrections": []}, rid="brief_prj_i")
    v = client.get("/api/projects/prj_i").json()["brief"]["interpretation"][0]["inferred_from"]
    assert v["established"] and v["facts"][0]["label"] == "Where you sell" and v["changed"] == []
    r = client.patch("/api/projects/prj_i/brief", json={"base_version": 1, "claim_id": fact["id"], "value": "Seattle shops"})
    assert "Our reading of Home market used this fact; review it" in r.json()["notice"]
    after = client.get("/api/projects/prj_i").json()["brief"]["interpretation"][0]["inferred_from"]
    assert after["changed"] == ["Where you sell"]


def test_reader_accepts_both_the_entered_domain_and_its_redirect_target(monkeypatch):
    """GOT BAG v4: gotbag.com redirects to got-bag.com; the reader asked for gotbag.com/… pages and every one was
    refused as off-site, leaving an empty brief."""
    from landfall import fetch
    tool_outputs = []

    class FakeAgent:
        def __init__(self, **kw):
            self.tool = kw["tools"][0]

        def __call__(self, prompt, **kw):
            for u in ("https://gotbag.com/pages/about-us", "https://got-bag.com/pages/about-us", "https://evil.example/"):
                tool_outputs.append(self.tool(page_url=u, purpose="about"))
            return type("R", (), {"structured_output": brand.BriefDraft(brand_name="G", facts=[], interpretation=[],
                                                                          unknowns=[])})()

    db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", "prj_g", "s", "https://gotbag.com/", "live", None, None, db.now())
    monkeypatch.setattr(brand, "Agent", FakeAgent)
    monkeypatch.setattr(brand, "make_model", lambda *a, **k: None)
    monkeypatch.setattr(brand, "robots_allows", lambda run, u: True)
    monkeypatch.setattr(brand, "read_page", lambda run, u: {"id": "src", "url": u, "title": "About", "status": 200,
                                                            "text": "Seit 2018 entwickeln wir Taschen.", "links": []})
    run = Run("run_g", "prj_g", "brand_read", {}, cassette=None)
    brand._run_reader(run, "https://gotbag.com/", {"gotbag.com", fetch.site_of("https://got-bag.com/")},
                      {"links": []})
    assert tool_outputs[0].startswith("PAGE") and tool_outputs[1].startswith("PAGE")
    assert tool_outputs[2].startswith("Not read")
