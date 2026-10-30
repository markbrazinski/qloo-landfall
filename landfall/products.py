"""Shared product research: what the brand actually sells (BUILD-PLAN §4.5.2a; CONTRACTS B6.5 ProductRecord).

One `products` run per project, before the first specialist. Two routes:
- SHOPIFY_STOREFRONT_MCP: `search_catalog` on the brand's own `/api/ucp/mcp`, only when Landfall serves its own agent
  profile (SHOPIFY_AGENT_PROFILE_URL; deferred to slice 8 by the owner, so off at run time until then). Shopify forbids
  caching results: the MCP body is parsed in memory and never stored, recorded or logged (P45-R7). Only the call's
  metadata is kept. Each product is then read from its own page through Landfall's fetcher.
- BRAND_PAGES: product links on the brand's own pages (the slice-1 reads, which replay), then the product pages.
- SHOPIFY_FEED: the store's own public `/products.json` (owner, 27 Oct 2026), read by the brand read's enrichment track
  when the site is a Shopify store. Each product's photo is saved as a FETCHED artifact for the launch kit.
A record keeps only what its fetched page snapshot shows. Provider stages: `products_mcp`, `products_fetch`.
"""
import base64
import hashlib
import html
import json
import re
from collections import Counter
from urllib.parse import urlsplit

import httpcore

from . import brand, config, db, fetch, ledger, runner
from .cassette import NoRecording
from .runner import HANDLERS, Run

VERSION = "products@1"
RESEARCH_TOOLS = ("search_catalog", "lookup_catalog", "get_product")  # cart, checkout and order tools are refused
MAX_SEARCHES, MAX_PAGES, MAX_LISTINGS = 3, 6, 2
STAGE_MCP, STAGE_FETCH = "products_mcp", "products_fetch"

# category_terms@1: the category words a product, a brand fact or a shop's assortment is matched on (retail fit uses
# the same map). ponytail: English plus the German GOT BAG's pages use; add a language when a live case needs it.
CATEGORIES = {
    "shirts": r"shirts?|button[- ]downs?|polos?|flannels?|tees?|t-shirts?|hemd(?:en)?",
    "trousers": r"pants|trousers|chinos|jeans|shorts|hosen?",
    "outerwear": r"jackets?|coats?|outerwear|vests?|jacken?",
    "knitwear": r"sweaters?|jumpers?|knitwear|cardigans?|pullover",
    "menswear": r"menswear|men'?s",
    "womenswear": r"womenswear|women'?s",
    "bags": r"bags?|backpacks?|daypacks?|totes?|duffels?|rucksacks?|taschen?|rolltop",
    "small accessories": r"wallets?|pouch(?:es)?|scrunchies?|belts?|hats?|caps?|beanies?|socks",
    "footwear": r"shoes|sneakers|boots|footwear",
    "eyewear and watches": r"sunglasses|eyewear|glasses|watch(?:es)?",
    "jewellery": r"jewel(?:le)?ry",
    "home": r"candles?|homewares?|home goods",
    "beauty": r"skin ?care|cosmetics|makeup|beauty",
    "drinks": r"sodas?|teas?|coffee|drinks?|beverages?|juices?",
    "food": r"snacks?|chocolate|food",
    "clothing": r"apparel|clothing|clothes|wardrobe|tailoring|fashion|bekleidung",
    "art supplies": r"art supplies|art and craft supplies|craft supplies|acrylics|oil colou?rs|canvases|easels",
}
# A specific category and the broader one a shop may name instead: shared parent → PARTIAL overlap, never MATCH.
PARENT = {"shirts": "clothing", "trousers": "clothing", "outerwear": "clothing", "knitwear": "clothing",
          "menswear": "clothing", "womenswear": "clothing", "footwear": "clothing"}
BROAD = ("menswear", "womenswear", "clothing")
_CAT = {k: re.compile(rf"\b(?:{v})\b", re.I) for k, v in CATEGORIES.items()}
MATERIALS = {
    "recycled plastic": r"recycled (?:ocean |marine )?plastic|ocean plastic|recycelte[mn]? (?:\w+ )?plastik",
    "recycled polyester": r"recycled polyester|rpet",
    "organic cotton": r"organic cotton|bio-?baumwolle",
    "cotton": r"cotton|baumwolle", "linen": r"linen|leinen", "wool": r"wool|merino|wolle", "cashmere": r"cashmere",
    "silk": r"silk", "denim": r"denim", "flannel": r"flannel", "leather": r"leather|leder", "nylon": r"nylon",
    "polyester": r"polyester", "hemp": r"hemp|hanf", "canvas": r"canvas",
}
_MAT = {k: re.compile(rf"\b(?:{v})\b", re.I) for k, v in MATERIALS.items()}
PRODUCT_PATH = re.compile(r"/products?/([a-z0-9][a-z0-9-]*)/?$", re.I)
LD_JSON = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)


class Refused(Exception):
    """A tool outside the three research tools; never dispatched."""


def categories_in(text: str) -> list[str]:
    return [k for k, rx in _CAT.items() if rx.search(text or "")]


def materials_in(text: str) -> list[str]:
    found = [k for k, rx in _MAT.items() if rx.search(text or "")]
    return [m for m in found if not any(m != o and m in o for o in found)]  # "organic cotton" absorbs "cotton"


# ---- the brief's "what you sell" ----
def sells(pid: str) -> list[dict]:
    """The brief's products facts in any state, with the brand page each was read from. They only point at where to
    look: a ProductRecord's evidence is its own product page (LIVE L45-A attempt 1: Untuckit's fact was TO_CHECK, so
    requiring SUPPORTED found no products at all)."""
    return [c for c in (db.get_record(i) for i in (db.latest("brief", pid) or {}).get("facts", []))
            if (c.get("subject") or {}).get("key") == "products"]


def queries_for(pid: str) -> list[str]:
    """≤ 3 catalog searches from what you sell: its category words, else the fact itself."""
    facts = sells(pid)
    cats = []
    for c in facts:
        for k in categories_in(c["value"]):
            if k not in cats and k not in BROAD:
                cats.append(k)
    return (cats or [c["value"][:60] for c in facts])[:MAX_SEARCHES]


# ---- MCP route (offline-tested; off until Landfall serves its own profile) ----
def endpoint_of(pid: str) -> str | None:
    """The brand's own store after redirects (gotbag.com → got-bag.com), as the slice-1 read recorded it."""
    p = db.q("SELECT url FROM projects WHERE id=?", pid)
    src = p and db.q("SELECT final_url FROM sources WHERE project_id=? AND url=?", pid, fetch.check_url(p[0]["url"]))
    final = (src and src[0]["final_url"]) or (p and p[0]["url"])
    if not final:
        return None
    parts = urlsplit(final)
    return f"https://{parts.hostname}/api/ucp/mcp"


def mcp(url: str, tool: str, catalog: dict, profile: str) -> dict:
    """One allow-listed MCP call through the fetcher's guarded connection. The parsed result lives only in memory."""
    if tool not in RESEARCH_TOOLS:
        raise Refused(f"{tool} is not a research tool")
    url = fetch.check_url(url)
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": {"meta": {"ucp-agent": {"profile": profile}},
                                                              "catalog": catalog}}}).encode()
    rid = ledger.reserve("shopify_mcp", tool, url=url)
    outcome = "aborted"
    try:
        with httpcore.ConnectionPool(network_backend=fetch._GuardedBackend(), ssl_context=fetch._TLS) as pool:
            r = pool.request("POST", url, content=body, extensions={"timeout": fetch.TIMEOUT},
                             headers={"User-Agent": fetch.UA, "Content-Type": "application/json"})
        outcome = str(r.status)
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        return json.loads(json.loads(r.content)["result"]["content"][0]["text"])
    except (httpcore.TimeoutException, httpcore.NetworkError, httpcore.ProtocolError, OSError) as e:
        outcome = "transport_error"
        raise RuntimeError(type(e).__name__) from None
    finally:
        ledger.finalize(rid, "shopify_mcp", tool, url=url, outcome=outcome)


def mcp_enabled(run: Run) -> bool:
    return bool(config.env("SHOPIFY_AGENT_PROFILE_URL")) and (
        run.cassette.mode != "replay" or STAGE_MCP in run.cassette.live_stages)


def mcp_candidates(run: Run, pid: str) -> tuple[list[dict], dict]:
    """Product page URLs from search_catalog, each with its call's metadata. Never raises: a failure falls back."""
    url, profile = endpoint_of(pid), config.env("SHOPIFY_AGENT_PROFILE_URL")
    site = fetch.site_of(url or "")
    calls, found = [], []
    if not url or allowed(run, url, {}):  # the same robots policy before any future live MCP call (P45-I8)
        return [], {"state": "FAILED", "calls": [{"tool": "search_catalog", "outcome": "REFUSED", "at": db.now()}]}
    for q in queries_for(pid):
        run.check()
        at = db.now()
        try:
            res = mcp(url, "search_catalog", {"query": q}, profile)
        except Exception as e:  # noqa: BLE001 — any failure is a fallback, never "no products"
            calls.append({"tool": "search_catalog", "query": q, "at": at, "outcome": "FAILED", "error": str(e)[:120]})
            continue
        items = res.get("products") or []
        calls.append({"tool": "search_catalog", "query": q, "at": at, "outcome": "OK" if items else "EMPTY",
                      "count": len(items)})
        for p in items:
            u = canonical(p.get("url") or "")
            if u and fetch.same_site(u, site) and u not in [f["url"] for f in found]:
                found.append({"url": u, "mcp": {"tool": "search_catalog", "query": q, "at": at}})
    state = "OK" if found else "FAILED" if all(c["outcome"] == "FAILED" for c in calls) else "EMPTY"
    return found[:MAX_PAGES], {"state": state, "calls": calls}


# ---- page route ----
def canonical(url: str) -> str | None:
    """/collections/shirts/products/lockridge → https://host/products/lockridge; anything else is not a product page."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    m = PRODUCT_PATH.search(parts.path or "")
    return f"https://{parts.hostname}/products/{m.group(1).lower()}" if m and parts.hostname else None


def allowed(run: Run, url: str, robots: dict) -> str | None:
    """The shared page policy before any product-path read (P45-I8): never a social platform, and robots.txt honoured.
    Returns None when the read may go ahead, else the attempt outcome to record."""
    from .research import is_social
    if is_social(url):
        return "REFUSED"
    host = urlsplit(url).netloc
    if host not in robots:
        robots[host] = fetch.robots_for(url, run.cassette, STAGE_FETCH)
    return None if fetch.robots_allows(url, robots[host]) else "REFUSED"


def page_candidates(run: Run, pid: str, robots: dict, attempts: list) -> list[dict]:
    """Product links on the brand's own pages: the page each "what you sell" fact came from, then the homepage. Those
    reads are the slice-1 ones, so they replay; ranked by how many what-you-sell words the product's address shares."""
    listings = []
    for c in sells(pid):
        for sid in c.get("source_ids", []):
            row = db.q("SELECT url FROM sources WHERE id=?", sid)
            if row and row[0]["url"] not in listings:
                listings.append(row[0]["url"])
    home = db.q("SELECT url FROM projects WHERE id=?", pid)[0]["url"]
    listings = [*listings, fetch.check_url(home)][:MAX_LISTINGS]
    words = {w for c in sells(pid) for w in re.findall(r"[a-z]{4,}", c["value"].lower())}
    found: list[str] = []
    for u in listings:
        if refused := allowed(run, u, robots):
            attempts.append({"url": u, "outcome": refused, "kind": "listing"})
            continue
        page = fetch.get(u, run.cassette, STAGE_FETCH)
        attempts.append({"url": u, "outcome": "OK" if page.get("status") == 200 else "FAILED", "kind": "listing"})
        if page.get("status") == 200:
            for link in fetch.extract(page)["links"]:
                p = canonical(link)
                if p and p not in found:
                    found.append(p)
    found.sort(key=lambda u: -len(words & set(u.rsplit("/", 1)[-1].split("-"))))  # stable: page order breaks ties
    return [{"url": u, "mcp": None} for u in found[:MAX_PAGES]]


def _plain(v) -> str:
    """Structured-data text without markup ("description.html" on Shopify pages)."""
    return re.sub(r"<[^>]+>", " ", html.unescape(str(v or "")))


def _ld_product(body: str) -> dict:
    for block in LD_JSON.findall(body or ""):
        try:
            data = json.loads(html.unescape(block.strip()))
        except ValueError:
            continue
        for d in (data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []):
            if isinstance(d, dict) and d.get("@type") in ("Product", "ProductGroup"):
                return d
    return {}


def _price_text(ld: dict, text: str) -> str | None:
    """Display text only, never Money, and only when the page's readable text shows that number too."""
    offers = ld.get("offers") or {}
    o = offers[0] if isinstance(offers, list) and offers else offers if isinstance(offers, dict) else {}
    price, cur = str(o.get("price") or o.get("lowPrice") or ""), o.get("priceCurrency") or ""
    whole = price.split(".")[0]
    return f"{cur} {price}".strip() if whole and re.search(rf"(?<!\d){re.escape(whole)}(?!\d)", text or "") else None


def record_from(run: Run, pid: str, cand: dict, robots: dict) -> tuple[dict | None, str]:
    """Read one product page and keep what it shows. Returns (ProductRecord payload or None, attempt outcome)."""
    url = cand["url"]
    if refused := allowed(run, url, robots):
        return None, refused
    page = fetch.get(url, run.cassette, STAGE_FETCH)
    if page.get("status") != 200:
        return None, "FAILED"
    ex = fetch.extract(page)
    ld = _ld_product(page.get("body", ""))
    title = " ".join(str(ld.get("name") or ex["first_title"].split(" – ")[0].split(" | ")[0]).split())
    if not title or not ex["text"]:
        return None, "EMPTY"
    src = brand._store_source(run, url, page, ex)
    # Materials only from fields about this product (P45-I6): its title and its own structured description and
    # material. Page text can hold recommended or neighbouring products, so it never supplies a material.
    described = f"{title} {_plain(ld.get('description'))} {_plain(ld.get('material'))}"
    return {"title": title, "product_url": url, "snapshot_id": src["id"],
            "price_text": _price_text(ld, ex["text"]),
            "category_terms": categories_in(f"{title} {ld.get('category') or ''}"),
            "materials": materials_in(described),
            "discovered_via": "SHOPIFY_STOREFRONT_MCP" if cand["mcp"] else "BRAND_PAGES",
            "mcp": cand["mcp"], "vc": "F"}, "OK"


CONTENT = ("title", "product_url", "price_text", "category_terms", "materials")


def save(pid: str, payload: dict) -> dict:
    """A product keeps its id across refreshes (by page address); v+1 only when what the page shows changed, so a
    refresh that changes nothing flags nothing downstream (P45-R5)."""
    rid = "pro_" + hashlib.sha256(f"{pid}|{payload['product_url']}".encode()).hexdigest()[:16]
    prev = db.get_record(rid)
    if prev and all(prev.get(k) == payload.get(k) for k in CONTENT):
        return prev
    return db.put_record("product", pid, payload, rid=rid)


# ---- Shopify product feed (owner, 27 Oct 2026) ----
FEED_LIMIT, FEED_KEEP, FEED_FACTS = 250, 8, 3  # every kept product gets its photo (one paced fetch each); 250 is Shopify's max
# page, so the core types outnumber the newest merch (28 Oct 2026: 30 newest gave Death Wish stir sticks, not coffee)
FEED_TEXT = 60  # feed lines kept as the cited source text, core products first
STAGE_FEED = "enrich_feed"


def feed_url_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/products.json?limit={FEED_LIMIT}"


def shopify_items(body: str) -> list[dict] | None:
    """The `products` of a Shopify feed, or None when the body isn't one (a 200 HTML page, another platform's JSON)."""
    try:
        data = json.loads(body or "")
    except ValueError:
        return None
    items = data.get("products") if isinstance(data, dict) else None
    if not isinstance(items, list) or not all(isinstance(p, dict) and p.get("handle") and isinstance(p.get("variants"), list)
                                              for p in items):
        return None
    return items


def _feed_line(p: dict) -> str:
    """One product as the feed text shows it: the feed's own title, type and price, nothing added."""
    v = (p.get("variants") or [{}])[0]
    return " — ".join(str(x).strip() for x in (p.get("title"), p.get("product_type"), v.get("price")) if str(x or "").strip())


def _store_currency(run: Run, origin: str, home: dict) -> str | None:
    """The store's currency: what its homepage states, else the store's public /meta.json (robots permitting)."""
    if home.get("currency"):
        return home["currency"]
    u = f"{origin}/meta.json"
    if not brand.robots_allows(run, u):
        return None
    r = fetch.get(u, run.cassette, STAGE_FEED)
    try:
        cur = json.loads(r.get("body") or "").get("currency") if r.get("status") == 200 else None
    except (ValueError, AttributeError):
        return None
    return cur if isinstance(cur, str) and re.fullmatch(r"[A-Z]{3}", cur) else None


def read_feed(run: Run, pid: str, url: str, home: dict) -> dict:
    """Products, prices and photos from the store's own feed. Returns what it did, the feed's brief facts (SOURCE
    facts citing the feed URL, each quoting one feed line) and the addresses it read."""
    from .delivery import GIFT, IMAGE_TYPES, save_artifact
    furl = feed_url_of(home.get("final_url") or url)
    out = {"url": furl, "status": None, "shopify": bool(home.get("shopify")), "products": 0, "images": 0, "facts": [],
           "sources": [], "currency": None}
    if home.get("ok") and not home.get("shopify"):
        out["status"] = "NOT_SHOPIFY"  # the homepage was read and isn't a Shopify store: no feed to ask for
        return out
    if not brand.robots_allows(run, furl):
        out["status"] = "REFUSED"
        return out
    try:
        page = fetch.get(furl, run.cassette, STAGE_FEED)
    except NoRecording:  # cases recorded before the limit rose to 250 (fixtures/demo/) asked for the 30 newest
        out["url"] = furl = furl.replace(f"limit={FEED_LIMIT}", "limit=30")
        page = fetch.get(furl, run.cassette, STAGE_FEED)
    out["sources"].append(fetch.check_url(furl))
    out["status"] = page.get("status") or page.get("error")
    items = shopify_items(page.get("body", "")) if page.get("status") == 200 else None
    if items is None:
        brand._activity(run, "Read your store's product feed", "Shopify product feed",
                        f"No product feed ({out['status']})", "UNAVAILABLE", role="Researcher")
        return out
    out["shopify"] = True
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(page.get("url") or furl))
    cur = out["currency"] = _store_currency(run, origin, home)
    # the store's core products first: the product categories it lists most (coffee before one silicone cup, 27 Oct 2026),
    # "Coffee" and "Coffee Bundle" counted as one; a stable sort, so the store's own order holds within a category
    def kind(p: dict) -> str:
        t = (p.get("product_type") or "").strip()
        return (categories_in(t) or [t.casefold()])[0] if t else ""
    freq = Counter(kind(p) for p in items)
    items = sorted(items, key=lambda p: -freq[kind(p)] if kind(p) else 0)
    text = (f"Currency: {cur} (as your store states it)\n" if cur else "") + "\n".join(
        _feed_line(p) + (f"\n{' '.join(_plain(p.get('body_html')).split())[:240]}" if p.get("body_html") else "")
        for p in items[:FEED_TEXT])
    src = brand._store_source(run, fetch.check_url(furl), page, {"title": "Your store's product feed", "text": text})
    kept, refs = [], []
    for p in items:
        if len(kept) >= FEED_KEEP:
            break
        title = " ".join(str(p.get("title") or "").split())
        if not title or GIFT.search(title) or any(k["title"] == title for k, _ in kept):
            continue  # a colorway listed as its own product is the same product to a founder
        price = str((p.get("variants") or [{}])[0].get("price") or "").strip()
        desc = " ".join(_plain(p.get("body_html")).split())
        payload = {"title": title, "product_url": f"{origin}/products/{p['handle']}", "snapshot_id": src["id"],
                   "price_text": (f"{cur} {price}" if cur else f"{price} (currency not shown)") if price else None,
                   "category_terms": categories_in(f"{title} {p.get('product_type') or ''}"),
                   "materials": materials_in(f"{title} {desc}"), "discovered_via": "SHOPIFY_FEED", "mcp": None, "vc": "F"}
        rec = save(pid, payload)
        kept.append((p, rec))
        refs.append({"id": rec["id"], "version": rec["version"]})
    if refs:
        db.put_record("product_research", pid, {
            "version_tag": VERSION, "route": "SHOPIFY_FEED", "mcp": {"state": "DISABLED", "reason": "read from the store's product feed"},
            "queries": [], "products": refs, "attempts": [{"url": furl, "outcome": "OK", "kind": "feed"}], "at": db.now()},
            rid=f"prr_{pid}")
    out["products"] = len(refs)
    have = {a["source"].get("page") for a in db.records("artifact", pid) if a["source"].get("kind") == "FETCHED"}
    for p, rec in kept:
        imgs = [i for i in p.get("images") or [] if isinstance(i, dict) and i.get("src")]
        if rec["product_url"] in have or not imgs:
            continue
        src_url = imgs[0]["src"].strip()
        src_url = ("https:" + src_url if src_url.startswith("//") else src_url) + ("&" if "?" in src_url else "?") + "width=800"
        if not brand.robots_allows(run, src_url):
            continue
        got = fetch.get_bytes(src_url, run.cassette, STAGE_FEED)
        ctype = (got.get("content_type") or "").split(";")[0].strip()
        data = base64.b64decode(got.get("b64") or "")
        if got.get("status") != 200 or ctype not in IMAGE_TYPES or not data:
            continue
        save_artifact(pid, data, f"{re.sub(r'[^a-z0-9]+', '-', rec['title'].lower()).strip('-')[:50]}.{'png' if ctype == 'image/png' else 'jpg'}",
                      ctype, {"kind": "FETCHED", "url": got.get("url") or src_url, "fetched_at": db.now(),
                              "page": rec["product_url"]},
                      "brand_own_unconfirmed",
                      ["Your own product image from your site; confirm you hold the rights before any public use"])
        out["images"] += 1
    types: dict[str, dict] = {}
    for p, rec in kept:
        types.setdefault((p.get("product_type") or "").strip().casefold(), p)
    for p in list(types.values())[:FEED_FACTS]:
        kind = (p.get("product_type") or "").strip()
        out["facts"].append({"field": "products", "label": "What you sell",
                             "value": f"{' '.join(p['title'].split())}{f' ({kind})' if kind else ''}",
                             "source_url": fetch.check_url(furl), "quote": _feed_line(p), "origin": "FEED"})
    brand._activity(run, "Read your store's product feed", "Shopify product feed",
                    f"{out['products']} products · {out['images']} photos saved" + (f" · prices in {cur}" if cur else ""),
                    role="Researcher")
    return out


def products_run(run: Run):
    pid = run.project_id
    prr = db.latest("product_research", pid)
    if prr and prr["route"] == "SHOPIFY_FEED" and prr["products"]:
        # ponytail: the feed read during the brand read stands; a refresh re-reads it with the next brand read
        run.emit("activity", {"stage": "Brand", "role": "Brand reader", "tool": "products", "task": "Find your products",
                              "detail": f"{len(prr['products'])} products from your store's product feed", "status": "DONE"})
        run.emit("products", {"count": len(prr["products"])})
        return
    run.emit("activity", {"stage": "Brand", "role": "Brand reader", "tool": "products", "task": "Find your products",
                          "detail": "Reading your own product pages", "status": "RUNNING"})
    mcp_state = {"state": "DISABLED", "reason": "Landfall's own agent profile is served from slice 8"}
    cands: list[dict] = []
    if mcp_enabled(run):
        cands, mcp_state = run.step("mcp", lambda: mcp_candidates(run, pid))
    route = "SHOPIFY_STOREFRONT_MCP" if cands else "BRAND_PAGES"
    listing_attempts: list = []
    if not cands:  # MCP off, failed or empty: the brand's own pages, never "no products"
        def pages():
            return {"cands": page_candidates(run, pid, {}, listing_attempts), "attempts": listing_attempts}
        got_pages = run.step("pages", pages)
        cands, listing_attempts = got_pages["cands"], got_pages["attempts"]

    def read():
        robots, kept, attempts = {}, [], list(listing_attempts)
        for c in cands:
            run.check()
            payload, outcome = record_from(run, pid, c, robots)
            attempts.append({"url": c["url"], "outcome": outcome})
            if payload:
                rec = save(pid, payload)
                kept.append({"id": rec["id"], "version": rec["version"]})
        return {"kept": kept, "attempts": attempts}

    got = run.step("read", read)
    db.put_record("product_research", pid, {
        "version_tag": VERSION, "route": route, "mcp": mcp_state, "queries": queries_for(pid),
        "products": got["kept"], "attempts": got["attempts"], "at": db.now()}, rid=f"prr_{pid}")
    detail = (f"{len(got['kept'])} products from your own pages" if got["kept"]
              else f"No product page found among {len(got['attempts'])} links read")
    run.emit("activity", {"stage": "Brand", "role": "Brand reader", "tool": "products", "task": "Find your products",
                          "detail": detail, "status": "DONE"})
    run.emit("products", {"count": len(got["kept"])})


HANDLERS["products"] = products_run


def start(pid: str) -> str:
    brief = db.latest("brief", pid)
    return runner.start(pid, "products", f"brief:{brief['version'] if brief else 0}")


def view(pid: str) -> dict | None:
    """The brief's Products group: the current ProductRecords, each with its page snapshot, and how they were found."""
    pr = db.latest("product_research", pid)
    if not pr:
        return None
    arts = {a["source"].get("page"): a for a in db.records("artifact", pid) if a["source"].get("kind") == "FETCHED"}
    limited = bool(db.q("SELECT 1 FROM sources WHERE project_id=? AND status IN (429, 503) LIMIT 1", pid))
    items = []
    for ref in pr["products"]:
        p = db.get_record(ref["id"], ref["version"])
        a = arts.get(p["product_url"])
        items.append({**{k: p[k] for k in ("id", "version", "title", "product_url", "snapshot_id", "price_text",
                                           "category_terms", "materials", "discovered_via")},
                      "image": a and {"sha256": a["sha256"], "media_type": a["media_type"]},
                      "image_why": None if a else ("Your site limited our reader; no product photo yet" if limited
                                                    else "No product photo read yet")})
    return {"route": pr["route"], "mcp": pr["mcp"], "items": items, "attempts": pr["attempts"], "at": pr["at"]}
