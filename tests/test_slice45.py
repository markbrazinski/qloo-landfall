"""Slice 4.5 offline checks (SYNTHETIC at the fetch and MCP boundaries; no provider is reached)."""
import json
import threading

import pytest

from landfall import config, db, ledger, products
from landfall.cassette import Cassette, key_of
from landfall.runner import Run

SITE = "https://shop.example"
LISTING = f"{SITE}/collections/shirts"


def _page(url: str, body: str, status: int = 200) -> dict:
    return {"url": url, "status": status, "content_type": "text/html", "hops": [], "body": body}


def _product_html(name: str, text: str, price: str = "98.00", ld_extra: dict | None = None) -> str:
    ld = {"@context": "https://schema.org", "@type": "Product", "name": name, "description": text,
          "offers": {"price": price, "priceCurrency": "USD"}, **(ld_extra or {})}
    filler = " ".join(["Free returns within thirty days on every order placed online."] * 4)
    return (f"<html><head><title>{name} – Shop</title><script type=\"application/ld+json\">{json.dumps(ld)}</script>"
            f"</head><body><article><h1>{name}</h1><p>{text}</p><p>Price ${price.split('.')[0]}</p><p>{filler}</p>"
            "</article></body></html>")


PAGES = {
    f"{SITE}/": _page(f"{SITE}/", "<html><body><a href='/products/gift-card'>Gift card</a></body></html>"),
    f"{SITE}/robots.txt": _page(f"{SITE}/robots.txt", "User-agent: *\nDisallow: /cart"),
    LISTING: _page(LISTING, "<html><body><a href='/collections/shirts/products/flannel-shirt'>Flannel</a>"
                            "<a href='/products/linen-shirt?variant=1'>Linen</a><a href='/pages/about'>About</a>"
                            "</body></html>"),
    f"{SITE}/products/flannel-shirt": _page(f"{SITE}/products/flannel-shirt", _product_html(
        "Brushed Flannel Shirt", "A men's shirt in soft organic cotton flannel, cut to be worn untucked.")),
    f"{SITE}/products/linen-shirt": _page(f"{SITE}/products/linen-shirt", _product_html(
        "Linen Shirt", "Breathable linen shirt for summer.", price="88.00")),
    f"{SITE}/products/gift-card": _page(f"{SITE}/products/gift-card", "", status=404),
}


def _cassette(tmp_path, live_stages=()) -> Cassette:
    d = tmp_path / "case"
    d.mkdir()
    with (d / "exchanges.jsonl").open("w") as f:
        for url, resp in PAGES.items():
            f.write(json.dumps({"key": key_of("fetch", "GET", {"url": url}), "provider": "fetch", "op": "GET",
                                "request": {"url": url}, "response": resp}) + "\n")
    return Cassette("replay", tmp_path / "out", replay_dirs=[d], live_stages=live_stages)


def _project(pid="p1") -> str:
    db.x("INSERT INTO projects(id, session, url, mode, created_at) VALUES(?,?,?,?,?)", pid, "s", f"{SITE}/", "live", db.now())
    db.x("INSERT INTO sources(id, project_id, url, final_url, status, fetched_at, title, text, error, replayed) "
         "VALUES(?,?,?,?,?,?,?,?,?,?)", "src_list", pid, LISTING, LISTING, 200, db.now(), "Shirts", "Shirts", None, 0)
    fact = db.put_record("claim", pid, {"subject": {"record_kind": "brief", "key": "products"}, "label": "What you sell",
                                        "value": "Men's shirts designed to be worn untucked", "state": "SUPPORTED",
                                        "source_ids": ["src_list"]})
    db.put_record("brief", pid, {"brand_name": "Shop", "route": "site", "facts": [fact["id"]], "interpretation": [],
                                 "unknowns": [], "corrections": []}, rid=f"brief_{pid}")
    return pid


def _run(pid, cas) -> Run:
    return Run(db.new_id("run"), pid, "products", {}, cas, threading.Event())


def test_page_route_keeps_only_what_each_product_page_shows(tmp_path):
    pid = _project()
    products.products_run(_run(pid, _cassette(tmp_path)))
    v = products.view(pid)
    assert v["route"] == "BRAND_PAGES" and v["mcp"]["state"] == "DISABLED"
    flannel, linen = v["items"]
    assert flannel["title"] == "Brushed Flannel Shirt" and flannel["discovered_via"] == "BRAND_PAGES"
    assert flannel["category_terms"] == ["shirts"]
    assert flannel["materials"] == ["organic cotton", "flannel"]  # "organic cotton" absorbs "cotton"
    assert flannel["price_text"] == "USD 98.00"  # display text, shown on the page
    assert db.q("SELECT text FROM sources WHERE id=?", flannel["snapshot_id"])[0]["text"]  # the chip opens a snapshot
    assert linen["product_url"] == f"{SITE}/products/linen-shirt"  # collection path and variant query canonicalised
    assert {a["outcome"] for a in v["attempts"]} == {"OK", "FAILED"}  # the 404 gift card is an attempt, not a product


def test_refresh_with_identical_pages_writes_no_new_version(tmp_path):
    pid = _project()
    products.products_run(_run(pid, _cassette(tmp_path)))
    first = products.view(pid)["items"]
    products.products_run(_run(pid, Cassette("replay", replay_dirs=[tmp_path / "case"])))
    assert [(i["id"], i["version"]) for i in products.view(pid)["items"]] == [(i["id"], i["version"]) for i in first]


def _mcp_on(monkeypatch, answer):
    monkeypatch.setitem(config._ENV, "SHOPIFY_AGENT_PROFILE_URL", "https://landfall.example/.well-known/ucp-agent.json")
    calls = []

    def fake(url, tool, catalog, profile):
        calls.append((url, tool, catalog))
        return answer(tool, catalog)
    monkeypatch.setattr(products, "mcp", fake)
    return calls


def test_mcp_route_cites_the_fetched_page_and_keeps_no_response_body(tmp_path, monkeypatch):
    pid = _project()
    secret_description = "SYNTHETIC-MCP-BODY-must-never-be-stored"
    calls = _mcp_on(monkeypatch, lambda tool, c: {"products": [
        {"id": "gid://shopify/Product/1", "title": "Brushed Flannel Shirt", "url": f"{SITE}/products/flannel-shirt",
         "description": {"html": secret_description}},
        {"id": "gid://shopify/Product/2", "title": "Elsewhere", "url": "https://other.example/products/x"}]})
    cas = _cassette(tmp_path, live_stages=(products.STAGE_MCP,))
    products.products_run(_run(pid, cas))
    v = products.view(pid)
    assert v["route"] == "SHOPIFY_STOREFRONT_MCP" and v["mcp"]["state"] == "OK"
    assert [c[1] for c in calls] == ["search_catalog"] and calls[0][2] == {"query": "shirts"}
    assert calls[0][0] == f"{SITE}/api/ucp/mcp"
    assert [i["title"] for i in v["items"]] == ["Brushed Flannel Shirt"]  # an off-site product is not the brand's
    rec = db.get_record(v["items"][0]["id"])
    assert rec["mcp"]["tool"] == "search_catalog" and rec["discovered_via"] == "SHOPIFY_STOREFRONT_MCP"
    # P45-R7: no MCP body anywhere: records, tasks, events, captures
    dumped = "".join(json.dumps(r) for t in ("records", "tasks", "events") for r in map(dict, db.q(f"SELECT * FROM {t}")))
    captures = "".join(p.read_text() for p in (tmp_path / "out").rglob("*") if p.is_file())
    assert secret_description not in dumped + captures


@pytest.mark.parametrize("answer", [lambda t, c: (_ for _ in ()).throw(RuntimeError("HTTP 503")),
                                    lambda t, c: {"products": []}])
def test_mcp_failure_or_empty_falls_back_to_pages(tmp_path, monkeypatch, answer):
    pid = _project()
    _mcp_on(monkeypatch, answer)
    products.products_run(_run(pid, _cassette(tmp_path, live_stages=(products.STAGE_MCP,))))
    v = products.view(pid)
    assert v["route"] == "BRAND_PAGES" and v["mcp"]["state"] in ("FAILED", "EMPTY") and len(v["items"]) == 2


def test_only_research_tools_are_dispatched():
    for tool in ("get_cart", "create_checkout", "get_order"):
        with pytest.raises(products.Refused):
            products.mcp(f"{SITE}/api/ucp/mcp", tool, {}, "https://landfall.example/p.json")
    assert ledger.totals()["fetch"] == 0  # refused before admission


def test_shopify_mcp_counts_against_fetch_authority(monkeypatch):
    monkeypatch.setattr(config, "CAP_FETCH", 1)
    rid = ledger.reserve("shopify_mcp", "search_catalog")
    ledger.finalize(rid, "shopify_mcp", "search_catalog", outcome="200")
    assert ledger.totals()["fetch"] == 1
    with pytest.raises(ledger.BudgetExhausted):
        ledger.reserve("fetch", "GET")


# ---- readiness truth table (CONTRACTS B6.5, frozen; P45-R3) ----
from landfall import specialist  # noqa: E402

PLACED_LOCAL = specialist.locality(False, False, placed_in_city=True, passage_names_lead=True)


@pytest.mark.parametrize("fit, identity, location, evidence, contact, expected", [
    ("BRAND_FACT_MATCH", "VERIFIED", "PLACED", "SUPPORTED", "OWN_SITE", "EVIDENCED"),
    ("BRAND_FACT_MATCH", "SOURCES_DIFFER", "PLACED", "SUPPORTED", "OWN_SITE", "HYPOTHESIS_LED"),  # conflict + placement
    ("QLOO_RELATED", "CHECK_FAILED", "PLACED", "SUPPORTED", "OWN_SITE", "HYPOTHESIS_LED"),  # failed check + placement
    ("QLOO_LISTING", "VERIFIED", "PLACED", "SUPPORTED", "OWN_SITE", "HYPOTHESIS_LED"),  # a flat listing only
    ("BRAND_FACT_MATCH", "NOT_CHECKED", "PLACED", "SUPPORTED", "OWN_SITE", "HYPOTHESIS_LED"),  # placement ≠ identity
    ("BRAND_FACT_MATCH", "VERIFIED", "SOURCES_DIFFER", "SUPPORTED", "OWN_SITE", "HYPOTHESIS_LED"),  # address conflict
    ("QLOO_RELATED", "VERIFIED", "PLACED", "NONE", "OWN_SITE", "HYPOTHESIS_LED"),
    ("QLOO_RELATED", "VERIFIED", "PLACED", "SUPPORTED", "UNKNOWN", "HYPOTHESIS_LED"),
])
def test_readiness_truth_table(fit, identity, location, evidence, contact, expected):
    a = specialist.axes(fit, identity, PLACED_LOCAL, evidence, contact, location_state=location)
    assert a["overall"] == expected
    assert a["operational"]["willingness"] == "UNKNOWN"
    assert bool(a["reasons"]) == (expected != "EVIDENCED")


def test_placement_without_the_passage_naming_the_lead_is_not_local():
    assert specialist.locality(False, False, placed_in_city=True, passage_names_lead=False) == "TO_CHECK"
    assert specialist.locality(True, False, False, False) == "LOCAL"


def test_dossier_readiness_is_not_yet_without_a_kept_candidate_and_best_otherwise():
    assert specialist.readiness([])["overall"] == "NOT_YET"
    weak = specialist.axes("QLOO_LISTING", "VERIFIED", "LOCAL", "SUPPORTED", "OWN_SITE")
    strong = specialist.axes("BRAND_FACT_MATCH", "VERIFIED", "LOCAL", "SUPPORTED", "EMAIL")
    assert specialist.readiness([weak, strong])["overall"] == "EVIDENCED"
    assert specialist.readiness([weak])["overall"] == "HYPOTHESIS_LED"


def test_digital_needs_a_sourced_platform_option_and_a_fit_basis():
    assert specialist.axes("QLOO_RELATED", "NA", "NA", "SOURCED", "NA", digital=True)["overall"] == "EVIDENCED"
    assert specialist.axes("INTERPRETATION", "NA", "NA", "SOURCED", "NA", digital=True)["overall"] == "HYPOTHESIS_LED"
    assert specialist.axes("QLOO_RELATED", "NA", "NA", "TO_CHECK", "NA", digital=True)["overall"] == "HYPOTHESIS_LED"


def test_haversine_minneapolis_to_saint_paul():
    d = specialist.km((44.9778, -93.2650), (44.9537, -93.0900))
    assert 13.5 < d < 14.5 and specialist.distance_label(d).endswith("km straight-line")


# ---- event timing (P45-R6) ----
from datetime import datetime, timezone  # noqa: E402

OCT25 = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
OCT28 = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)


def test_a_reused_snapshot_crossing_the_event_date_turns_past_at_use():
    ed = specialist.event_dates("Night market on 26 October 2026 at the Loft", "brisbane")
    assert [e["date"] for e in ed] == ["2026-10-26"]
    assert specialist.when(ed[0], OCT25) == "UPCOMING" and specialist.when(ed[0], OCT28) == "PAST"


def test_the_city_timezone_decides_the_day():
    ed = specialist.event_dates("Market 2026-10-26", "brisbane")[0]
    late_utc = datetime(2026, 10, 25, 15, tzinfo=timezone.utc)  # already 26 Oct, 01:00, in Brisbane
    assert specialist.when(ed, late_utc) == "UPCOMING"
    assert specialist.when(ed, datetime(2026, 10, 26, 15, tzinfo=timezone.utc)) == "PAST"  # 27 Oct there


def test_two_irregular_past_events_are_repeated_before_not_recurring():
    quote = "We hosted a maker market on 12 September 2026 and a launch night on 3 May 2026."
    t = specialist.activity_timing(quote, specialist.event_dates(quote, "brisbane"), "brisbane", OCT25)
    assert t["recurrence"] == "REPEATED_BEFORE" and t["next_date"] is None and set(t["when"].values()) == {"PAST"}


def test_an_explicit_schedule_is_recurring_with_its_next_date():
    quote = "Brisbane Trail Runners meet every Saturday 6am at Teneriffe Park."
    t = specialist.activity_timing(quote, [], "brisbane", OCT25)  # Sunday 25 Oct in Brisbane
    assert t["recurrence"] == "RECURRING" and t["schedule_quote"] == "every Saturday" and t["next_date"] == "2026-10-31"
    m = specialist.activity_timing("Social ride, first Thursday of each month", [], "minneapolis", OCT25)
    assert m["next_date"] == "2026-11-05"


def test_ambiguous_and_yearless_dates_stay_undated():
    eds = specialist.event_dates("Pop-up 3/4/2026, then Sat 1 Nov", "london")
    assert [specialist.when(e, OCT25) for e in eds] == ["UNDATED", "UNDATED"]
    assert specialist.event_dates("on 25/11/2026", "london")[0]["date"] == "2026-11-25"  # day > 12: unambiguous


def test_a_schedule_whose_dated_instances_are_all_past_is_possibly_stale():
    quote = "Every Sunday; last run 7 June 2026."
    t = specialist.activity_timing(quote, specialist.event_dates(quote, "lisbon"), "lisbon", OCT25)
    assert t["recurrence"] == "RECURRING" and t["possibly_stale"]


# ---- retail assortment (CAPTURED page text from the locked L4 v2 cases) ----
ART_SHED = ("Art Shed Brisbane is Australia's largest online shop for art and craft supplies, featuring a wide range of "
            "acrylics, oil colours, canvases, easels, paper, pencils, pastels, sculpting tools, clay, and kids' supplies. "
            "Explore trusted brands like Mont Marte, Art Spectrum, Matisse, Atelier, and Winsor & Newton.")
MP3 = ("Explore our array of apparel to apothecary treasures, discover modern literature. With over 30,000 square feet "
       "of curated luxury brands and an approach to hospitality that's given us quite the reputation, there's more to "
       "explore in-store. Visit our North Loop Minneapolis boutique for the full MartinPatrick 3 experience.")


def test_art_shed_names_outside_brands_but_is_a_category_mismatch_for_a_bag_brand():
    s = specialist.assortment_scan(ART_SHED, "Art Shed Brisbane")
    assert [b["name"] for b in s["brands"]] == ["Mont Marte", "Art Spectrum", "Matisse", "Atelier", "Winsor & Newton"]
    assert specialist.category_overlap(products.categories_in("Bags and backpacks"), s["categories"]) == "NONE"


def test_martinpatrick_3_carries_a_claim_to_check_and_partially_overlaps_shirts():
    s = specialist.assortment_scan(MP3, "MartinPatrick 3")
    assert s["brands"] == [] and "curated luxury brands" in s["carries_claim"]  # judged by the checker, not assumed
    assert specialist.category_overlap(products.categories_in("Men's shirts"), s["categories"]) == "PARTIAL"
    assert specialist.category_overlap(["shirts"], []) is None  # not shown ≠ mismatch


def test_a_published_wholesale_route_is_found():
    s = specialist.assortment_scan("Brands we carry: Barbour, Filson and Taylor Stitch. Wholesale enquiries: see our form.")
    assert [b["name"] for b in s["brands"]] == ["Barbour", "Filson", "Taylor Stitch"]
    assert s["route"].startswith("Wholesale enquiries")
    assert [b["name"] for b in specialist.assortment_scan("Our brands: Byredo, Le Labo.")["brands"]] == ["Byredo", "Le Labo"]


# ---- creator tools (SYNTHETIC provider answers) ----
from landfall import creators  # noqa: E402


@pytest.mark.parametrize("url, expected", [
    ("https://www.tiktok.com/@pl.menswear?lang=en", ("tiktok", "@pl.menswear", "https://www.tiktok.com/@pl.menswear")),
    ("https://youtube.com/@StyleMpls/", ("youtube", "@StyleMpls", "https://www.youtube.com/@StyleMpls")),
    ("https://www.youtube.com/channel/UC0123456789abcdefghijkl",
     ("youtube", "UC0123456789abcdefghijkl", "https://www.youtube.com/channel/UC0123456789abcdefghijkl")),
    ("https://instagram.com/pernilleteisbaek", ("instagram", "@pernilleteisbaek", "https://www.instagram.com/pernilleteisbaek/")),
    ("https://www.instagram.com/p/Cx123/", None), ("https://www.youtube.com/watch?v=abc", None),
    ("https://example.com/@someone", None),
])
def test_profile_urls_are_canonicalised(url, expected):
    c = creators.canonical(url)
    assert (c and (c["platform"], c["handle"], c["canonical_url"])) == expected


def _replay(tmp_path, exchanges: list[tuple[str, str, dict, dict]]) -> Cassette:
    d = tmp_path / "creator-case"
    d.mkdir(exist_ok=True)
    with (d / "exchanges.jsonl").open("w") as f:
        for provider, op, req, resp in exchanges:
            f.write(json.dumps({"key": key_of(provider, op, req), "provider": provider, "op": op, "request": req,
                                "response": resp}) + "\n")
    return Cassette("replay", replay_dirs=[d])


def test_tiktok_oembed_gives_the_canonical_author_and_instagram_is_never_fetched(tmp_path):
    p = creators.canonical("https://www.tiktok.com/@pl.menswear")
    endpoint = "https://www.tiktok.com/oembed?url=https%3A%2F%2Fwww.tiktok.com%2F%40pl.menswear"
    cas = _replay(tmp_path, [
        ("fetch", "GET", {"url": "https://www.tiktok.com/robots.txt"}, _page("https://www.tiktok.com/robots.txt", "User-agent: *\nAllow: /")),
        ("fetch", "GET", {"url": endpoint}, _page(endpoint, json.dumps({"author_name": "PL Menswear",
                                                                       "author_url": "https://www.tiktok.com/@pl.menswear"})))])
    assert creators.oembed(p, cas) == {"author_name": "PL Menswear", "author_url": "https://www.tiktok.com/@pl.menswear"}
    assert creators.oembed(creators.canonical("https://instagram.com/pernilleteisbaek"), Cassette("replay")) is None


YT_BODY = json.dumps({"items": [{"id": "UC0123456789abcdefghijkl", "snippet": {"title": "Style MPLS", "country": "US",
                                 "description": "Menswear in Minneapolis"}, "statistics": {"subscriberCount": "12300"}}]})


def test_youtube_channel_needs_the_key_keeps_it_out_of_the_request_and_stamps_retention(tmp_path, monkeypatch):
    p = creators.canonical("https://www.youtube.com/@StyleMpls")
    req = {"part": "snippet,statistics", "forHandle": "@StyleMpls"}
    cas = _replay(tmp_path, [("youtube", "channels.list", req, {"status": 200, "body": YT_BODY})])
    monkeypatch.setitem(config._ENV, "youtube_api_key", "")
    assert creators.youtube_channel(p, cas) is None  # flag off without a key
    monkeypatch.setitem(config._ENV, "youtube_api_key", "SYNTHETIC-KEY")
    assert creators.youtube_channel(p, cas) is None  # P45-I3: a key alone doesn't enable it
    monkeypatch.setitem(config._ENV, "LANDFALL_ENABLE_YOUTUBE", "1")
    ch = creators.youtube_channel(p, cas, now=OCT25)  # replayed: the canonical request has no key, so it matched
    assert ch["country"] == "US" and ch["metric"]["value"] == 12300 and ch["metric"]["observed_at"] == "2026-10-25"
    assert ch["retention"]["delete_by"].startswith("2026-11-24")


def test_identified_needs_two_agreeing_signals_of_different_kinds():
    assert creators.identity([{"kind": "PAGE_NAMES_HANDLE"}]) == "POSSIBLE"  # Pernille Teisbaek: Instagram-only page
    assert creators.identity([{"kind": "PAGE_NAMES_HANDLE"}, {"kind": "PAGE_NAMES_HANDLE"}]) == "POSSIBLE"
    assert creators.identity([{"kind": "PAGE_NAMES_HANDLE"}, {"kind": "OEMBED"}]) == "IDENTIFIED"
    assert creators.identity([{"kind": "PAGE_NAMES_HANDLE"}, {"kind": "OEMBED", "agrees": False}]) == "POSSIBLE"
    assert creators.identity([], by_founder=True) == "YOU_SAID"
    assert not creators.keep_metric({"name": "followers", "value": 1_400_000, "source": "vogue.com"})  # undated


def test_retention_sweep_redacts_records_cache_and_captures_after_30_days(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PRIVATE", tmp_path / "private")
    run_dir = tmp_path / "private" / "runs" / "p1"
    run_dir.mkdir(parents=True)
    old = "2026-09-20T10:00:00+00:00"
    (run_dir / "exchanges.jsonl").write_text(
        json.dumps({"key": "k", "provider": "youtube", "op": "channels.list", "provenance": {"at": old},
                    "request": {}, "response": {"status": 200, "body": YT_BODY}}) + "\n" +
        json.dumps({"key": "k2", "provider": "fetch", "op": "GET", "provenance": {"at": old},
                    "request": {}, "response": {"status": 200, "body": "a page"}}) + "\n")
    stamp = {"provider": "youtube", "acquired_at": old, "delete_by": "2026-10-20T10:00:00+00:00"}
    rec = db.put_record("lead", "p1", {"name": "Style MPLS", "creator": {"youtube": {"country": "US", "retention": stamp},
                                                                      "location": {"state": "REPORTED"}}})
    db.x("INSERT INTO provider_cache VALUES(?,?,?,?)", "yt1", "youtube", YT_BODY, old)
    assert creators.sweep(OCT25) == {"records": 1, "cache": 1, "exchanges": 1}
    kept = db.get_record(rec["id"])
    assert kept["creator"]["youtube"]["redacted"] and "country" not in kept["creator"]["youtube"]
    assert kept["creator"]["location"] == {"state": "REPORTED"}  # page-derived provenance is untouched
    text = (run_dir / "exchanges.jsonl").read_text()
    assert "Style MPLS" not in text and "a page" in text
    assert creators.sweep(OCT25) == {"records": 0, "cache": 0, "exchanges": 0}  # idempotent


# ---- city_check@4 on the constructed request and the validator (P45-R4) ----
MP3_LEAD = {"lead_key": "minneapolis:martinpatrick3.com", "name": "MartinPatrick 3"}
MP3_CLAIM = {"cid": "c1", "subject_name": "MartinPatrick 3", "method": "retail",
             "text": "carries curated luxury menswear brands",
             "quote": "With over 30,000 square feet of curated luxury brands",
             "window": "Visit our North Loop boutique for the full MartinPatrick 3 experience. With over 30,000 square feet "
                       "of curated luxury brands"}
PLACED = {"lead_key": "minneapolis:martinpatrick3.com", "city_id": "minneapolis", "state": "PLACED",
          "address": "212 N 3rd Ave", "source": "QLOO_SEARCH"}


def test_north_loop_quote_plus_placement_is_supported_and_local():
    listing = specialist.claim_listing([MP3_CLAIM], {"retail": "the shop stocks other brands"})
    assert "Minneapolis" not in listing and listing.startswith("- id=c1 | MartinPatrick 3: carries")
    verdict = {"claim_id": "c1", "verdict": "SUPPORTS", "unsupported_parts": [], "shows_way_in": True}  # SYNTHETIC
    assert specialist.claim_state(verdict) == "SUPPORTED"
    assert specialist.claim_locality(MP3_CLAIM, "Minneapolis", MP3_LEAD, PLACED, "minneapolis") == "LOCAL"
    assert specialist.claim_locality(MP3_CLAIM, "Minneapolis", MP3_LEAD, None, "minneapolis") == "TO_CHECK"


def test_a_placement_of_a_different_business_with_the_same_name_is_not_local():
    twin = {**PLACED, "lead_key": "minneapolis:martinpatrick3-outlet.example"}
    assert specialist.claim_locality(MP3_CLAIM, "Minneapolis", MP3_LEAD, twin, "minneapolis") == "TO_CHECK"
    assert specialist.claim_locality(MP3_CLAIM, "Minneapolis", MP3_LEAD, {**PLACED, "city_id": "chicago"},
                                     "minneapolis") == "TO_CHECK"


def test_partial_contradicting_and_missing_verdicts_fail_closed():
    assert specialist.claim_state({"verdict": "SUPPORTS", "unsupported_parts": ["menswear"]}) == "TO_CHECK"
    assert specialist.claim_state({"verdict": "CONTRADICTS"}) == "SOURCES_DIFFER"
    assert specialist.claim_state(None) == specialist.claim_state({"error": "x"}) == "TO_CHECK"


def test_prose_bundle_keeps_a_supported_product_paraphrase_and_drops_invented_nested_prose():
    items = [
        {"id": "s1", "field": "retail.possible_activation", "text": "Show the Brushed Flannel Shirt on a trial shelf.",
         "evidence": [{"role": "PRODUCT", "text": "Brushed Flannel Shirt: a men's shirt in organic cotton flannel"}]},
        {"id": "s2", "field": "community.participation_assumptions[].text",
         "text": "Function Well hosts environmentally aware members every week.",
         "evidence": [{"role": "CLAIM", "text": "Function Well: a gym in Toombul"}]},
        {"id": "s3", "field": "popup.activation_outline.format", "text": "A two-day market stall.", "evidence": []},
    ]
    listing = specialist.prose_listing(items)
    assert "evidence: PRODUCT: Brushed Flannel Shirt" in listing and "evidence: none" in listing
    verdicts = [{"id": "s1", "kind": "PROPOSAL"}, {"id": "s2", "kind": "PROPOSAL"}]  # SYNTHETIC; s3 left unjudged
    keep = specialist.prose_keep(items, verdicts, leads=[{"name": "Function Well"}])
    assert keep == {"s1": True, "s2": False, "s3": False}


def test_a_falsely_labelled_platform_option_is_downgraded():
    platforms, audiences = {"meta.city_radius", "tiktok.interests"}, {"Sustainability"}
    ok = {"element": "city + radius", "basis": "PLATFORM_OPTION", "platforms_ref": "meta.city_radius"}
    assert specialist.targeting_basis(ok, platforms, audiences) == ok
    fake = {"element": "people who recently moved", "basis": "PLATFORM_OPTION", "platforms_ref": "meta.life_events"}
    assert specialist.targeting_basis(fake, platforms, audiences)["basis"] == "TO_CHECK_IN_ADS_MANAGER"
    invented = {"element": "Luxury travellers", "basis": "QLOO_DERIVED"}
    assert specialist.targeting_basis(invented, platforms, audiences)["basis"] == "TO_CHECK_IN_ADS_MANAGER"


# ---- regressions from LIVE L45-A attempt 1 (26 Oct 2026) ----
def test_unknown_brand_categories_are_not_known_never_a_mismatch():
    assert specialist.category_overlap([], ["clothing"]) is None  # every Minneapolis shop was wrongly rejected
    assert specialist.category_overlap(["shirts"], ["art supplies"]) == "NONE"


def test_a_to_check_what_you_sell_fact_still_leads_to_product_pages(tmp_path):
    pid = _project()
    fact = db.get_record(db.latest("brief", pid)["facts"][0])
    db.put_record("claim", pid, {**{k: fact[k] for k in ("subject", "label", "value", "source_ids")}, "state": "TO_CHECK"},
                  rid=fact["id"])
    assert products.queries_for(pid) == ["shirts"]
    products.products_run(_run(pid, _cassette(tmp_path)))
    assert len(products.view(pid)["items"]) == 2  # each kept product is backed by its own page


def test_one_call_steps_are_not_refused_by_the_forced_submission_reserve():
    """LIVE L45-A attempt 2: Develop's $0.03 cap minus a $0.05 forced-submission reserve refused every call."""
    from types import SimpleNamespace
    from landfall import research
    run = SimpleNamespace(params={})
    for cap, forced in ((0.03, 0.0), (0.05, 0.0)):
        ctx = research.Ctx(run, "p", "toronto", 1, {}, caps=specialist.DRAFT_CAPS, usd_cap=cap, expected_usd=0.02,
                           cr_key=f"p:toronto:develop:{cap}", forced_usd=forced)
        research.admitter(ctx, "model")(0.01)  # admitted: no exception
    tight = research.Ctx(run, "p", "toronto", 1, {}, caps=specialist.DRAFT_CAPS, usd_cap=0.03, expected_usd=0.02,
                         cr_key="p:toronto:develop:tight")
    with pytest.raises(research.CityCap):
        research.admitter(tight, "model")(0.01)  # the slice-4 default reserve still applies where it is meant to



# ---- P45-I6 / I8 (implementation review) ----
def test_materials_come_from_the_product_not_neighbouring_recommendations(tmp_path):
    url = f"{SITE}/products/nylon-shirt"
    body = _product_html("Travel Shirt", "A lightweight nylon travel shirt.") .replace(
        "</article>", "</article><section><h2>You may also like</h2><p>Merino wool sweater</p><p>Leather belt</p>"
                      "<p>Silk scarf</p></section>")
    PAGES[url] = _page(url, body)
    try:
        cas = _cassette(tmp_path)
        rec, outcome = products.record_from(_run(_project(), cas), "p1", {"url": url, "mcp": None}, {})
    finally:
        PAGES.pop(url)
    assert outcome == "OK" and rec["materials"] == ["nylon"]


def test_a_disallowed_listing_or_a_social_original_url_is_never_read(tmp_path, monkeypatch):
    pid = _project()
    PAGES[f"{SITE}/robots.txt"] = _page(f"{SITE}/robots.txt", "User-agent: *\nDisallow: /collections\nDisallow: /")
    try:
        cas = _cassette(tmp_path)
        calls = []
        real = products.fetch.get
        monkeypatch.setattr(products.fetch, "get", lambda u, c, st="": calls.append(u) or real(u, c, st))
        attempts = []
        assert products.page_candidates(_run(pid, cas), pid, {}, attempts) == []
        assert [u for u in calls if not u.endswith("/robots.txt")] == []  # zero page dispatches
        assert {a["outcome"] for a in attempts} == {"REFUSED"}
    finally:
        PAGES[f"{SITE}/robots.txt"] = _page(f"{SITE}/robots.txt", "User-agent: *\nDisallow: /cart")
    db.x("UPDATE projects SET url=? WHERE id=?", "https://www.instagram.com/someshop/", pid)
    db.x("DELETE FROM sources WHERE project_id=?", pid)
    attempts = []
    assert products.page_candidates(_run(pid, Cassette("replay")), pid, {}, attempts) == []
    assert attempts == [{"url": "https://www.instagram.com/someshop/", "outcome": "REFUSED", "kind": "listing"}]
