"""Stage 2 · grounded brand understanding (BUILD-PLAN §1 stage 2, CONTRACTS B2)."""
import hashlib
import re
import threading
import time
import unicodedata
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from strands import Agent, tool

from . import db, fetch, markets
from .cassette import NoRecording
from .ledger import BudgetExhausted
from .nova import make_model
from .runner import HANDLERS, Cancelled, NeedsInput, Run

PROMPT_VERSION = "brand_reader@5"
CHECKER_VERSION = "support_check@4"
CHECKER = ("You check whether a verbatim quote from a web page supports a claim exactly as it is shown to a founder. "
           "Each claim has a field, a label and a value, and together they are the claim: field 'founded', label "
           "'Founded', value '2009' means 'the brand was founded in 2009'. First restate that full claim in "
           "claim_as_read. Then judge it. SUPPORTED: the quote itself states the full claim; a paraphrase or a "
           "translation is fine. PARTIAL: the quote supports only part of it, or the claim needs an inference beyond "
           "the quote (for example 'our cans are sold in Seattle' does not state 'the brand is based in Seattle'). "
           "Dates: a year in which something was done (selling, making, developing, shipping since a year) is not a "
           "founding date; only a quote saying the company was founded, established, started or launched then "
           "supports one. NOT_SUPPORTED: the quote does not support the claim. Also list in unsupported_parts every "
           "part of the displayed claim the quote does not itself state, including qualifiers, hedges and extra "
           "clauses (for example 'with other locations not specified'); leave it empty only when the quote states "
           "the whole claim. Judge only from the quote, never from what you know about the brand.")
MAX_PAGES = 8
PAGE_CHARS = 7000
THIN_LINKS = 5  # a homepage with fewer same-site links than this also offers its sitemap and its own off-site links

ROWS = [("open", "Opening the website"), ("products", "Reading products"),
        ("about", "Reading positioning and about pages"), ("stockists", "Reading stockists and shipping"),
        ("check", "Checking each quote against its page"), ("summary", "Summarizing what we know")]
FACT_LABELS = {"products": "What you sell", "where_sold": "Where you sell", "shipping": "Shipping",
               "community": "Community", "price": "Price points", "founded": "Founded", "other": "Also on the site",
               "home_market": "Based in"}  # home_market: only enrichment facts (a page stating where the brand is based)
INTERP_LABELS = {"positioning": "Positioning", "audience": "Audience", "home_market": "Home market", "other": "Also inferred"}


# ---- model output schemas ----
class Fact(BaseModel):
    field: Literal["products", "where_sold", "shipping", "community", "price", "founded", "other"]
    label: str = Field(description="Short row label, e.g. 'What you sell'")
    value: str = Field(description="The fact in your own concise words (paraphrase is fine)")
    source_url: str = Field(description="URL of the fetched page the quote comes from")
    quote: str = Field(description="An exact excerpt copied character-for-character from that page's TEXT, 4-40 words")


class Interpretation(BaseModel):
    field: Literal["positioning", "audience", "home_market", "other"]
    label: str
    value: str
    basis_facts: list[int] = Field(description="Numbers (1 = first) of the facts in your `facts` list this rests on")
    basis_pages: list[str] = Field(description="URLs of pages you read whose wording this rests on (may be empty)")
    reasoning: str = Field(description="One sentence: how those facts or pages lead to this reading")


class Stated(BaseModel):
    field: Literal["other"] = "other"
    label: str
    value: str


class Unknown(BaseModel):
    label: str = Field(description="e.g. 'Unit cost, wholesale price'")
    why_it_matters: str


class BriefDraft(BaseModel):
    brand_name: str
    facts: list[Fact]
    interpretation: list[Interpretation]
    unknowns: list[Unknown]


class Verdict(BaseModel):
    claim_id: str
    claim_as_read: str = Field(description="The full claim restated from its field, label and value")
    unsupported_parts: list[str] = Field(default_factory=list,
                                         description="Parts of the displayed claim the quote does not state")
    verdict: Literal["SUPPORTED", "PARTIAL", "NOT_SUPPORTED"]
    reason: str


class Verdicts(BaseModel):
    verdicts: list[Verdict]


SYSTEM = """You are the Brand reader on Landfall, an international launch team for small consumer brands.
You read the brand's OWN public website and produce a grounded brief the founder will check.

Output language: English. Translate every value, label, reasoning and unknown into English, whatever language the
site uses. Only `quote` stays exactly as written on the page.

Rules:
- Use fetch_page to read pages on this site only. Besides the homepage, read up to 7 pages that matter most:
  products/shop, about/story, stockists/where to buy, shipping/FAQ, and news/community if present.
- A fact must come from a page you fetched. For each fact copy an exact excerpt from that page's TEXT as `quote`
  (character-for-character, 4-40 words). Your `value` may paraphrase.
- Look for: what they sell (products, formats, flavours or ranges), where they sell (their own shops, stockists,
  retailers, online channels), shipping (where they ship), community (clubs, events, causes or partnerships the brand
  takes part in, not product features), price points, and founding.
- Readings that go beyond what a page literally says are interpretations, never facts: positioning, likely
  audience, and home_market = the city and country where the brand is based (not everywhere it sells).
- Every interpretation must say what it is inferred from: `basis_facts` are the numbers of the facts in your own
  `facts` list it rests on (1 = first fact), `basis_pages` are URLs of pages you read whose wording it rests on, and
  `reasoning` is one sentence linking them to the reading. Cite only the facts whose content actually supports the
  reading, not every fact. If you cannot point to facts or pages, leave it out.
- List as unknowns only things a launch decision would need that the site does not state (for example unit cost,
  wholesale price, production capacity, international shipping). Never fill an unknown with a guess, and never list
  as unknown something a fact already states.
- Never invent a URL, number, place or partner. If something is not on the pages you read, leave it out."""


# A fact row states what a page says. A row that carries its own hedge ("with potential other locations not
# specified") is a compound of a fact and an uncertainty, so it cannot be SOURCE as a whole (review closure, blocker 1).
# ponytail: English phrase list; misses novel hedges. Upgrade path: checker splits rows into atomic parts, each judged.
_HEDGE = re.compile(r"\b(potential(ly)?|possibl[ey]|probabl[ey]|likely|perhaps|presumably|unclear|uncertain|"
                    r"unspecified|not specified|not stated|not known|unknown|seems? to|appears? to|might)\b", re.I)


def _hedge(value: str) -> str | None:
    m = _HEDGE.search(value or "")
    return m.group(0) if m else None


def _label(c: dict) -> str:
    """The row label the founder sees; the checker judges exactly this."""
    return FACT_LABELS.get(c["field"]) if c["field"] != "other" else c.get("label") or FACT_LABELS["other"]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    s = s.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "}))
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def quote_found(quote: str, text: str) -> bool:
    """Deterministic existence check: whitespace, case and punctuation are normalised; wording must match."""
    nq = _norm(quote)
    return len(nq) >= 8 and nq in _norm(text)


def _usage_sink(run: Run):
    def sink(u: dict):
        db.x("INSERT INTO usage(project_id, run_id, at, provider, stage, replayed, input_tokens, output_tokens, est_usd, "
             "status, origin) VALUES(?,?,?,?,?,?,?,?,?,?,?)", run.project_id, run.id, db.now(), "bedrock", u["stage"],
             int(u["replayed"]), u["input_tokens"], u["output_tokens"], u["est_usd"], u["status"],
             "replay" if u["replayed"] else "live")
    return sink


def _row(run: Run, row: str, status: str, detail: str = ""):
    run.emit("reading", {"row": row, "status": status, "detail": detail})


def _activity(run: Run, task: str, tool_name: str, detail: str, status: str = "DONE", role: str = "Brand reader",
              stage: str = "Brand"):
    run.emit("activity", {"stage": stage, "role": role, "tool": tool_name, "task": task, "detail": detail, "status": status})


def _store_source(run: Run, url: str, page: dict, extracted: dict) -> dict:
    """Stored under the requested URL; `final_url` records where redirects led."""
    sid = "src_" + hashlib.sha256(f"{run.project_id}|{url}".encode()).hexdigest()[:16]
    db.x("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?,?,?,?,?,?)", sid, run.project_id, url,
         page.get("url"), page.get("status"), db.now(), extracted.get("title", ""), extracted.get("text", ""),
         page.get("error"), int(run.cassette.mode == "replay"))
    return {"id": sid, "url": url, "final_url": page.get("url") or url, **extracted}


_robots: dict[tuple[str, str], object] = {}


def robots_allows(run: Run, url: str) -> bool:
    """One robots.txt fetch per run and site; the parsed file answers every path."""
    key = (run.id, urlsplit(url).netloc)
    if key not in _robots:
        _robots[key] = fetch.robots_for(url, run.cassette, "brand")
    return fetch.robots_allows(url, _robots[key])


def read_page(run: Run, url: str) -> dict:
    """Fetch through the guarded, recorded path; reuse a page this project already read (restart-safe)."""
    known = db.q("SELECT * FROM sources WHERE project_id=? AND url=? AND status=200", run.project_id, fetch.check_url(url))
    if known:
        s = known[0]
        return {"id": s["id"], "url": s["url"], "final_url": s["final_url"], "title": s["title"], "text": s["text"],
                "links": [], "status": 200}
    page = fetch.get(url, run.cassette, "brand")
    ex = fetch.extract(page) if page.get("status") == 200 else {"title": "", "text": "", "links": []}
    if page.get("status") == 200 and fetch.needs_render(ex):  # a JavaScript shell: read it as a browser would
        shown = fetch.render(url, run.cassette, "brand")
        if shown.get("status") == 200 and len((rx := fetch.extract(shown))["text"]) > len(ex["text"]):
            page, ex = shown, rx
            _activity(run, f"Render {urlsplit(url).path or '/'}", "website reader",
                      "The page needed JavaScript; read it as a browser shows it")
    src = _store_source(run, fetch.check_url(url), page, ex)
    return {**src, "status": page.get("status"), "error": page.get("error"), "retry_after": page.get("retry_after"),
            "shopify": bool(page.get("shopify")), "currency": currency_in(page.get("body") or "")}


_CURRENCY = re.compile(r'Shopify\.currency\s*=\s*\{\s*"active"\s*:\s*"([A-Z]{3})"|"priceCurrency"\s*:\s*"([A-Z]{3})"|'
                       r'og:price:currency"\s+content="([A-Z]{3})"')


def currency_in(body: str) -> str | None:
    """The store's display currency as its own page states it (Shopify's storefront script or structured data)."""
    m = _CURRENCY.search(body or "")
    return next((g for g in m.groups() if g), None) if m else None


MAX_WAIT = 90  # seconds: the longest Retry-After honoured, once per host per run
_waits: dict[tuple[str, str], float] = {}
_wait_lock = threading.Lock()


def read_polite(run: Run, url: str, row: str | None = None) -> dict:
    """read_page, honouring one Retry-After (≤ 90 s) per host per run: the first 429/503 sets the host's wait, every
    page of that host that is turned away meanwhile waits for the same moment and is asked once more. Only this
    caller waits; other tracks keep working. A 403, a robots refusal or a longer wait is never retried."""
    p = read_page(run, url)
    if p.get("status") not in (429, 503):
        return p
    host = urlsplit(p.get("final_url") or url).hostname or ""
    ra = str(p.get("retry_after") or "").strip()
    secs = int(ra) if ra.isdigit() else None
    key = (run.id, host)
    with _wait_lock:
        first = key not in _waits
        if first:
            _waits[key] = time.monotonic() + secs if secs is not None and secs <= MAX_WAIT else 0.0
        left = _waits[key] - time.monotonic()
    if left <= 0:
        return p
    if first:
        msg = f"waiting for {fetch.site_of(url)} (asked us to wait {secs} s)"
        if row:
            _row(run, row, "RUNNING", msg)
        _activity(run, f"Wait for {fetch.site_of(url)}", "website reader",
                  f"HTTP {p.get('status')} · {msg}; the brief keeps filling in from other sources", "WAITING")
    run.cancel.wait(left)
    run.check()
    return {**read_page(run, url), "waited": True}


# ---- run handlers ----
def brand_read(run: Run):
    url = run.params["url"]
    site = fetch.site_of(url)
    for key, _ in ROWS:
        _row(run, key, "QUEUED")

    def open_site():
        _row(run, "open", "RUNNING", site)
        if not robots_allows(run, url):
            return {"ok": False, "reason": "The site's robots.txt asks automated readers not to read it.", "attempts": 1}
        last = None
        for attempt in (1, 2):
            run.check()
            last = read_polite(run, url, row="open")
            if last.get("status") == 200 and len(last.get("text", "")) >= 150:
                return {"ok": True, "source_id": last["id"], "title": last["title"], "links": last.get("links", []),
                        "site": fetch.site_of(last["final_url"]), "first_title": last.get("first_title"),
                        "outbound": last.get("outbound", []), "shopify": last.get("shopify", False),
                        "currency": last.get("currency"), "final_url": last.get("final_url")}
            if last.get("waited") or last.get("status") == 403:
                break  # the wait was honoured once, or the site refused us: never asked again
        err = last.get("error") or ""
        why = ("We couldn't look up that address: DNS gave no answer. The site may not exist, or its DNS may be "
               "down right now" if err == "NoSuchHost"
               else "The site didn't respond" if "Connect" in err else "Timed out" if "Timeout" in err
               else f"The address can't be read ({err.removeprefix('blocked: ')})" if err.startswith("blocked") else err
               or ("The site is turning away automated readers (HTTP 429, asked us to wait "
                   f"{last.get('retry_after') or 'a while'} s)" if last.get("status") == 429 else None)
               or (f"The site answered with an error (HTTP {last.get('status')})" if last.get("status") != 200
                   else "The page has no readable text; it may need JavaScript"))
        n = 1 if last.get("status") == 403 else 2
        return {"ok": False, "reason": f"{why}, {n} attempt{'s' if n > 1 else ''}", "attempts": n}

    # The enrichment track starts with the read and runs beside it: the store's product feed, grounded web search,
    # Wikipedia and their cited pages. Its facts join the brief before the quote check.
    home_box: dict = {}
    opened = threading.Event()
    track = _start_enrichment(run, url, site, home_box, opened)
    try:
        home = run.step("open", open_site)
    finally:
        opened.set()
    home_box.update(home)
    if not home["ok"]:
        _row(run, "open", "FAILED", home["reason"])
        for key, _ in ROWS[1:4]:
            _row(run, key, "SKIPPED")
        _activity(run, f"Open {site}", "website reader", home["reason"], "FAILED")
        enr = _join(run, track)
        if not enr["facts"] and not enr.get("feed", {}).get("products"):
            for key, _ in ROWS[4:]:
                _row(run, key, "SKIPPED")
            raise NeedsInput(f"unreadable: {home['reason']}")
        # The site turned us away, but the feed and the web gave something: a brief from those, labeled as such.
        draft = {"brand_name": _brand_name(run.project_id, url, home_box) or site, "facts": enr["facts"],
                 "interpretation": [], "unknowns": []}
    else:
        _row(run, "open", "DONE", f"{site} · {len(home['links'])} links on the homepage")
        _activity(run, f"Open {site}", "website reader", f"Homepage read · {len(home['links'])} same-site links")
        # A brand's domain may redirect (gotbag.com → got-bag.com); the site it lands on is the brand's own site.
        draft = run.step("reader", lambda: _run_reader(run, url, {site, home.get("site", site)}, home))
        run.check()
        # the web and store-feed track usually finishes after the site: say so on the row instead of sitting "queued"
        _row(run, "check", "RUNNING", "Adding what your store feed and the web say")
        enr = _join(run, track)
        draft = {**draft, "facts": draft["facts"] + enr["facts"]}  # appended: the reader's fact numbers still hold
    claims = run.step("check", lambda: _check(run, draft))
    # Web leads count only when their quote supports them; the feed's own rows are kept whatever the check says.
    claims = [c for c in claims if c.get("origin") in (None, "FEED") or c["state"] == "SUPPORTED"]
    run.step("summary", lambda: _assemble(run, draft, claims, route="site"))
    _brief_ready(run)


def _brief_ready(run: Run):
    """Outside any step, so a resumed run still hands over: a no-match identity now gets its profile proposed."""
    from . import identity, scan  # both build on this module
    identity.brief_ready(run.project_id)
    scan.auto_start(run.project_id)  # owner, 28 Oct 2026: Markets start by themselves once the brief is done


def _run_reader(run: Run, url: str, sites: set[str], home: dict) -> dict:
    pages: dict[str, dict] = {}
    purposes = {"products": [], "about": [], "stockists": []}
    failed = {k: 0 for k in purposes}  # pages of each kind found but not readable: unknown, never "not found"
    for key in purposes:
        _row(run, key, "RUNNING")

    thin = len(home["links"]) < THIN_LINKS
    more = _sitemap_pages(run, url, sites, set(home["links"])) if thin else []
    linked = set(home.get("outbound") or []) if thin else set()  # the brand's own links to pages on other sites

    @tool
    def fetch_page(page_url: str, purpose: Literal["products", "about", "stockists", "shipping", "community", "other"]) -> str:
        """Read one page of the brand's own website. purpose says why you are reading it."""
        run.check()
        if not any(fetch.same_site(page_url, s) for s in sites) and _norm_url(page_url) not in linked:
            return "Not read: only pages on the brand's own site can be read here."
        if len(pages) >= MAX_PAGES and page_url not in pages:
            return f"Not read: the {MAX_PAGES}-page limit is reached. Work with the pages you have."
        try:
            if not robots_allows(run, page_url):
                return "Not read: the site's robots.txt asks automated readers not to read this page."
            row = {"products": "products", "about": "about", "stockists": "stockists", "shipping": "stockists",
                   "community": "about"}.get(purpose)
            p = read_polite(run, page_url, row)
        except fetch.FetchBlocked as e:
            return f"Not read: {e}"
        path = urlsplit(p["url"]).path or "/"
        if p.get("status") != 200 or not p.get("text"):
            if row:
                failed[row] += 1
            _activity(run, f"Read {path}", "website reader", p.get("error") or f"HTTP {p.get('status')}", "UNAVAILABLE")
            return f"Not read: page unavailable ({p.get('error') or p.get('status')}). Treat its contents as unknown."
        pages[p["url"]] = p
        if row:
            purposes[row].append(path)
            _row(run, row, "RUNNING", ", ".join(purposes[row]))
        _activity(run, f"Read {path}", "website reader", p["title"][:80] or path)
        return f"PAGE {p['url']}\nTITLE {p['title']}\nTEXT\n{p['text'][:PAGE_CHARS]}"

    first = read_page(run, url)
    pages[first["url"]] = first
    links = "\n".join(home["links"][:60])
    prompt = reader_prompt(url, first, links, more, sorted(linked))
    agent = Agent(model=make_model(run.cassette, "brand_reader", _usage_sink(run), max_tokens=6000),
                  system_prompt=SYSTEM, tools=[fetch_page], callback_handler=None)
    result = agent(prompt, structured_output_model=BriefDraft, cancel_signal=run.cancel)
    run.check()
    draft = result.structured_output
    if draft is None:
        raise RuntimeError("the Brand reader returned no brief")
    for key, paths in purposes.items():
        _row(run, key, *row_outcome(paths, failed[key]))
    return draft.model_dump()


def row_outcome(paths: list[str], failed: int) -> tuple[str, str]:
    if paths:
        return "DONE", ", ".join(paths)
    if failed:
        return "FAILED", f"{failed} page{'s' if failed > 1 else ''} found but couldn't be read"
    return "SKIPPED", "No page of this kind found"


def read_summary(b: dict) -> dict:
    """v4 B02: what the brand read reached, one entry per page (a page read on any attempt counts as read). Most pages
    failed and nothing on the site supports a fact → `unreadable`: the brief is not trusted as a brief."""
    pid = b["project_id"]
    enr = db.latest("enrichment", pid) or {}
    extra = set(enr.get("sources") or [])  # the feed and web leads: reported as what filled in, not as site pages
    rows = db.q("SELECT url, status, error FROM sources WHERE project_id=? AND fetched_at<=? ORDER BY fetched_at",
                pid, b.get("read_at") or db.now())
    pages: dict[str, dict] = {}
    for r in (r for r in rows if r["url"] not in extra):
        ok = r["status"] == 200
        if ok or not pages.get(r["url"], {}).get("ok"):
            pages[r["url"]] = {"url": r["url"], "ok": ok, "why": None if ok else r["error"] or f"HTTP {r['status']}"}
    failed = [p for p in pages.values() if not p["ok"]]
    read = len(pages) - len(failed)
    whys = [p["why"] for p in failed]
    facts = [f for f in (db.get_record(c) for c in b["facts"]) if f]
    supported = any(f.get("state") == "SUPPORTED" for f in facts)
    fill = filled_in(pid, b, enr, facts, failed, len(pages))
    return {"pages_read": read, "pages_tried": len(pages), "pages_failed": len(failed),
            "why": max(set(whys), key=whys.count) if whys else None, "pages": list(pages.values()),
            "unreadable": b["route"] == "site" and len(failed) > read and not supported and not fill["feed_products"],
            "enrichment": fill}


def filled_in(pid: str, b: dict, enr: dict, facts: list[dict], failed: list[dict], tried: int) -> dict:
    """What limited the reader and what filled in, said frankly, and each fact's origin (SOURCE · FEED · WEB)."""
    site = fetch.site_of((db.q("SELECT url FROM projects WHERE id=?", pid) or [{"url": ""}])[0]["url"])
    limited = [p for p in failed if p["why"] in ("HTTP 429", "HTTP 503")]
    pr = db.latest("product_research", pid) or {}
    feed_n = len(pr.get("products") or []) if pr.get("route") == "SHOPIFY_FEED" else 0
    web = {f["source_ids"][0] for f in facts if f.get("origin") == "WEB" and f.get("source_ids")}
    from .ranking import brand_entity  # ranking builds on this module
    parts = ([f"your store's product feed ({feed_n} product{'s' if feed_n != 1 else ''})"] if feed_n else []) + \
        ([f"{len(web)} web source{'s' if len(web) != 1 else ''}"] if web else []) + (["Qloo"] if brand_entity(pid) else [])
    line = (f"{site} limited our reader (HTTP {limited[0]['why'][5:]} on {len(limited)} of {tried} pages). "
            if limited else "")
    line += (f"Filled in from: {', '.join(parts)}." if limited else f"Also from: {', '.join(parts)}.") if parts else ""
    return {"frank": line.strip() or None, "limited": len(limited), "filled": parts, "feed_products": feed_n,
            "web_sources": len(web), "origins": {f["id"]: f.get("origin") or "SOURCE" for f in facts},
            "stopped": enr.get("stopped"), "searches": len(enr.get("searches") or []),
            "leads": len(enr.get("leads") or [])}


def reader_prompt(url: str, first: dict, links: str, more: list[str], linked: list[str]) -> str:
    """The Brand reader's first message. For a rich homepage it is exactly the slice-1 text, so recorded reads still
    replay; a thin homepage adds its sitemap pages and the pages on other sites it links to."""
    extra = ""
    if more:
        extra += "OTHER PAGES OF THIS SITE (from its sitemap):\n" + "\n".join(more) + "\n\n"
    if linked:
        extra += ("PAGES ON OTHER SITES THAT THE HOMEPAGE LINKS TO (the brand's own links, for example its parent "
                  "company's about or product pages; you may read these with fetch_page too, within the page limit):\n"
                  + "\n".join(linked) + "\n\n")
    return (f"Brand website: {url}\n\nHOMEPAGE {first['url']}\nTITLE {first['title']}\nTEXT\n{first['text'][:PAGE_CHARS]}\n\n"
            f"SAME-SITE LINKS ON THE HOMEPAGE:\n{links}\n\n{extra}Read the pages you need, then return the brief.")


def _norm_url(u: str) -> str:
    try:
        return fetch.check_url(u)
    except fetch.FetchBlocked:
        return u


def _sitemap_pages(run: Run, url: str, sites: set[str], known: set[str], limit: int = 40) -> list[str]:
    """Pages listed in the site's sitemap (robots.txt's Sitemap lines, else /sitemap.xml; one level of index), so a
    thin homepage never leads to guessed addresses. At most 3 fetches."""
    p = urlsplit(url)
    rp = _robots.get((run.id, p.netloc))
    maps = list((rp.site_maps() if rp else None) or [f"{p.scheme}://{p.netloc}/sitemap.xml"])[:1]
    pages, fetched = [], 0
    while maps and fetched < 3:
        try:
            r = fetch.get(maps.pop(0), run.cassette, "brand")
        except fetch.FetchBlocked:
            continue
        fetched += 1
        if r.get("status") != 200:
            continue
        for loc in fetch.sitemap_urls(r.get("body", "")):
            if loc.lower().split("?")[0].endswith(".xml"):
                maps.append(loc)
            elif any(fetch.same_site(loc, s) for s in sites) and loc not in known and loc not in pages:
                pages.append(loc)
    if pages:
        _activity(run, "Read the sitemap", "website reader", f"{len(pages)} more pages listed")
    return pages[:limit]


def _check(run: Run, draft: dict) -> list[dict]:
    """Quote existence (deterministic), then one support judgment for all found quotes (model)."""
    _row(run, "check", "RUNNING")
    texts = {}
    for s in db.q("SELECT url, final_url, text FROM sources WHERE project_id=?", run.project_id):
        texts[s["url"]] = texts[fetch.check_url(s["final_url"] or s["url"])] = s["text"]
    claims = []
    for i, f in enumerate(draft["facts"]):
        try:  # the citation is model output: a malformed or unread URL makes this one fact unestablished, never the run
            url = fetch.check_url(f["source_url"])
        except fetch.FetchBlocked:
            url = f["source_url"]
        text = texts.get(url, "")
        claims.append({"key": f"f{i}", **f, "source_url": url, "page_read": bool(text),
                       "quote_found": bool(text) and quote_found(f["quote"], text)})
    to_judge = [c for c in claims if c["quote_found"]]
    verdicts = {}
    if to_judge:
        listing = "\n".join(f"- id={c['key']} | field: {c['field']} | label: {_label(c)} | value: {c['value']} | "
                            f"quote: \"{c['quote']}\"" for c in to_judge)
        agent = Agent(model=make_model(run.cassette, "support_check", _usage_sink(run), max_tokens=3000),
                      system_prompt=CHECKER,
                      callback_handler=None)
        res = agent(f"Judge each claim against its quote:\n{listing}", structured_output_model=Verdicts,
                    cancel_signal=run.cancel)
        verdicts = {v.claim_id: v.model_dump() for v in (res.structured_output.verdicts if res.structured_output else [])}
    out = []
    for c in claims:
        v = verdicts.get(c["key"])
        if not c["page_read"]:
            state, reason = "TO_CHECK", "The cited page is not one Landfall read, so the claim is not established."
        elif not c["quote_found"]:
            state, reason = "TO_CHECK", "Quote not found on the page, so the claim is not established."
        elif v is None:
            state, reason = "TO_CHECK", "Not yet judged."
        elif v["verdict"] == "SUPPORTED" and v.get("unsupported_parts"):
            # The whole displayed row is the claim: support for part of it does not establish the row.
            state, reason = "TO_CHECK", ("The quote supports part of this row but not: "
                                         + "; ".join(v["unsupported_parts"]) + ".")
        elif v["verdict"] == "SUPPORTED" and _hedge(c["value"]):
            state, reason = "TO_CHECK", (f"The quote supports part of this row, but the row also says \"{_hedge(c['value'])}\", "
                                         "which no page states. Only the confirmed part is a fact.")
        elif v["verdict"] == "SUPPORTED":
            state, reason = "SUPPORTED", v["reason"]
        else:
            state, reason = "TO_CHECK", f"The quote is on the page but does not establish this claim: {v['reason']}"
        partial = v and v["verdict"] == "SUPPORTED" and (v.get("unsupported_parts") or _hedge(c["value"]))
        support = "PARTIAL" if partial else v["verdict"] if v else None
        out.append({**c, "state": state, "support": support, "checker_verdict": v["verdict"] if v else None,
                    "unsupported_parts": (v or {}).get("unsupported_parts", []),
                    "reason": reason,
                    "claim_as_read": v.get("claim_as_read") if v else None, "checker": CHECKER_VERSION})
    supported = sum(1 for c in out if c["state"] == "SUPPORTED")
    _row(run, "check", "DONE", f"{supported} of {len(out)} quotes support their facts")
    _activity(run, "Check quotes against pages", "quote check + support check",
              f"{supported} supported · {len(out) - supported} to check", role="Researcher")
    return out


def _resolve_basis(it: dict, fact_ids: list[str], states: dict, read: dict) -> dict:
    """Turn the reader's references into checked links. Only facts in this brief and pages Landfall actually read
    count; anything else is dropped and recorded. The basis is established when at least one cited fact is SUPPORTED
    or STATED, or a cited page was read."""
    claim_ids, dropped = [], []
    for n in it.get("basis_facts") or []:
        if isinstance(n, int) and 1 <= n <= len(fact_ids):
            if fact_ids[n - 1] not in claim_ids:
                claim_ids.append(fact_ids[n - 1])
        else:
            dropped.append(f"fact {n}")
    source_ids = []
    for u in it.get("basis_pages") or []:
        try:
            sid = read.get(fetch.check_url(u))
        except fetch.FetchBlocked:
            sid = None
        if sid and sid not in source_ids:
            source_ids.append(sid)
        elif not sid:
            dropped.append(u)
    established = bool(source_ids) or any(states.get(c) in ("SUPPORTED", "STATED") for c in claim_ids)
    return {"claim_ids": claim_ids, "source_ids": source_ids, "established": established, "dropped": dropped}


PRECEDENCE = {"QLOO": 0, "SOURCE": 1, None: 1, "FEED": 2, "WEB": 3}  # Qloo > the brand's own site > store feed > web


def merge_targets(claims: list[dict]) -> list[int]:
    """Facts that state the same thing under the same label are one row (owner, 28 Oct 2026: "Founded: 2012" three
    times). Per claim, the index of the claim it merges into (itself when kept). Within a label, the kept fact is the
    established one first, then the most specific value, then the origin's precedence; a fact whose words (less the
    label's) are all in a kept fact's value merges into it. A more specific fact that isn't established stays its own row."""
    words = lambda c: set(_norm(c["value"]).split()) - set(_norm(_label(c)).split())  # noqa: E731
    rank = lambda i: (claims[i].get("state") not in ("SUPPORTED", "STATED"), -len(words(claims[i])),  # noqa: E731
                      PRECEDENCE.get(claims[i].get("origin"), 4), i)
    out, kept = list(range(len(claims))), []
    for i in sorted(range(len(claims)), key=rank):
        into = next((k for k in kept if _label(claims[k]) == _label(claims[i]) and words(claims[i]) <= words(claims[k])), None)
        if into is None:
            kept.append(i)
        else:
            out[i] = into
    return out


def _assemble(run: Run, draft: dict, claims: list[dict], route: str) -> dict:
    sources = {}
    for s in db.q("SELECT id, url, final_url FROM sources WHERE project_id=?", run.project_id):
        sources[s["url"]] = sources[fetch.check_url(s["final_url"] or s["url"])] = s["id"]
    src = lambda c: [sources[c["source_url"]]] if c.get("source_url") in sources else []  # noqa: E731
    origin = lambda c: c.get("origin") or ("SOURCE" if route == "site" else None)  # noqa: E731
    into, ids = merge_targets(claims), {}
    for i, c in enumerate(claims):
        if into[i] != i:
            continue
        same = [claims[j] for j in range(len(claims)) if into[j] == i and j != i]  # dropped, their sources kept as corroboration
        rec = db.put_record("claim", run.project_id, {
            "subject": {"record_kind": "brief", "key": c["field"]}, "label": _label(c),
            "value": c["value"], "state": c["state"], "vc": "F" if route == "site" else "U",
            "source_ids": list(dict.fromkeys(src(c) + [s for o in same for s in src(o)])),
            "corroboration": [{"value": o["value"], "origin": origin(o), "source_ids": src(o)} for o in same],
            "quote": c.get("quote"), "quote_found": c.get("quote_found"), "support": c.get("support"),
            "reason": c.get("reason"), "claim_as_read": c.get("claim_as_read"), "checker": c.get("checker"),
            "checker_verdict": c.get("checker_verdict"), "unsupported_parts": c.get("unsupported_parts") or [],
            "origin": origin(c), "produced_by": f"{run.id}/check"})
        ids[i] = rec["id"]
    fact_ids = [ids[into[i]] for i in range(len(claims))]  # per claim, so the reader's basis_facts numbers still hold
    read = {s["url"]: s["id"] for s in db.q("SELECT id, url FROM sources WHERE project_id=? AND status=200", run.project_id)}
    for s in db.q("SELECT id, final_url FROM sources WHERE project_id=? AND status=200", run.project_id):
        read.setdefault(s["final_url"], s["id"])
    states = {ids[i]: claims[i]["state"] for i in ids}
    interp_ids = []
    for it in draft["interpretation"]:
        basis = _resolve_basis(it, fact_ids, states, read)
        label = (INTERP_LABELS[it["field"]] if it["field"] in ("home_market", "positioning", "audience") and route == "site"
                 else it["label"] or INTERP_LABELS[it["field"]])
        rec = db.put_record("claim", run.project_id, {
            "subject": {"record_kind": "brief", "key": it["field"]}, "label": label, "value": it["value"],
            "state": "INFERRED", "vc": "I", "source_ids": basis["source_ids"], "basis_claim_ids": basis["claim_ids"],
            "reasoning": it.get("reasoning", ""), "basis_established": basis["established"],
            "basis_dropped": basis["dropped"], "produced_by": f"{run.id}/reader"},
            inputs=[{"id": f"brief_{run.project_id}", "version": 1, "keys": basis["claim_ids"]}])
        interp_ids.append(rec["id"])
    pages = db.q("SELECT COUNT(*) n FROM sources WHERE project_id=? AND status=200", run.project_id)[0]["n"]
    brief = db.put_record("brief", run.project_id, {
        "brand_name": qloo_name(run.project_id) or draft["brand_name"], "route": route, "facts": list(ids.values()), "interpretation": interp_ids,
        "unknowns": draft["unknowns"], "corrections": [], "pages_read": pages, "read_at": db.now(),
        "prompt_version": PROMPT_VERSION}, inputs=[{"id": run.project_id, "version": 1, "keys": ["url"]}],
        rid=f"brief_{run.project_id}")
    _row(run, "summary", "DONE", f"{len(ids)} facts · {len(interp_ids)} interpretations · {len(draft['unknowns'])} unknowns")
    _activity(run, "Summarize what we know", "brief", f"{len(ids)} facts · {len(interp_ids)} inferences · "
              f"{len(draft['unknowns'])} unknowns")
    run.emit("brief", {"version": brief["version"]})
    return {"brief_id": brief["id"], "version": brief["version"]}


# ---- enrichment track (owner, 27 Oct 2026): always on, beside the site read ----
# Qloo first: identity, stores and presence stay Qloo's wherever Qloo has them (identity.py, ranking.presence_of);
# these sources only fill gaps, and every fact keeps its origin (SOURCE · FEED · WEB).
MAX_LEADS = 6
LEAD_CHARS = 5000
SEARCHES = ("Where can you buy {name} ({site})? Name the stockists and retailers that carry {name}, with their cities.",
            "Where is {name} ({site}) headquartered or based, and when was it founded?",
            "What products does {name} ({site}) sell?")
ENRICH_SYSTEM = """You read web pages that a search found about one brand, and pull out facts about THAT brand only.
Output language: English; only `quote` stays exactly as written on the page.
- Each fact comes from one listed page: copy an exact excerpt from that page's TEXT as `quote` (character-for-character,
  4-40 words). `value` may paraphrase but must say no more than the quote.
- field where_sold: one retailer, stockist or own store that sells the brand, naming the city when the page does
  (one fact per retailer or city). home_market: where the brand is headquartered or based, ending with the country
  (e.g. 'Saratoga Springs, New York, United States').
  founded: the year it was founded. products: what it sells. other: anything else a launch team needs.
- Ignore pages about a different company with a similar name, and anything a page only hints at.
- Never invent a URL, place, retailer or number. Return no facts rather than a guess."""


class LeadFact(BaseModel):
    field: Literal["where_sold", "home_market", "founded", "products", "other"]
    label: str = Field(description="Short row label, e.g. 'Where you sell'")
    value: str = Field(description="The fact in a few words; home_market as 'City, Country'")
    source_url: str = Field(description="URL of the listed page the quote comes from")
    quote: str = Field(description="An exact excerpt copied character-for-character from that page's TEXT, 4-40 words")


class LeadFacts(BaseModel):
    facts: list[LeadFact]


def qloo_name(pid: str) -> str | None:
    """Qloo's name for the brand once its match is settled (confident, or chosen by the founder): it wins over any
    name derived from the address, the page title or the web."""
    from .ranking import brand_entity  # ranking builds on this module
    ent = brand_entity(pid)
    return ent["name"] if ent else None


def _brand_name(pid: str, url: str, home: dict) -> str:
    from .identity import derive_names  # identity builds on this module
    # Qloo's name wins once settled, so wait briefly for the identity run beside this read: otherwise which name the web
    # searches use is a race, and a replay can ask a question its recording never did (demo cases, 28 Oct 2026)
    end = time.monotonic() + 20
    while time.monotonic() < end and db.q("SELECT 1 FROM runs WHERE project_id=? AND kind='identity' AND "
                                          "status IN ('queued','running')", pid):
        time.sleep(0.2)
    return qloo_name(pid) or derive_names(url, home.get("final_url"), home.get("first_title"))["site_name"]


def _start_enrichment(run: Run, url: str, site: str, home: dict, opened: threading.Event):
    box: dict = {}

    def go():
        try:
            box["result"] = run.step("enrich", lambda: _enrich(run, url, site, home, opened))
        except Exception as e:  # noqa: BLE001 — the track never stops the read; the brief continues without it
            box["result"] = {"facts": [], "feed": {}, "error": type(e).__name__}
    t = threading.Thread(target=go, daemon=True, name=f"enrich-{run.id}")
    t.start()
    return t, box


def _join(run: Run, track) -> dict:
    t, box = track
    while t.is_alive():
        run.check()
        t.join(0.5)
    return box.get("result") or {"facts": [], "feed": {}}


def _ground(run: Run, question: str) -> list[str]:
    """One Converse call with Nova web grounding (the research tool's call; metered and capped by the ledger)."""
    from . import config
    model = make_model(run.cassette, "enrich_search", _usage_sink(run), max_tokens=800)
    resp = model.client.converse(modelId=config.NOVA_MODEL, messages=[{"role": "user", "content": [{"text": question}]}],
                                 toolConfig={"tools": [{"systemTool": {"name": "nova_grounding"}}]},
                                 inferenceConfig={"maxTokens": 800})
    blocks = resp["output"]["message"]["content"]
    cites = [c["location"]["web"] for b in blocks for c in (b.get("citationsContent") or {}).get("citations", [])
             if "web" in c.get("location", {})]
    return list(dict.fromkeys(c["url"] for c in cites if c.get("url")))


def _enrich(run: Run, url: str, site: str, home: dict, opened: threading.Event) -> dict:
    """(a) the store's product feed, (b) grounded web searches and Wikipedia, (c) Qloo as the identity run does it.
    Every cited page is a lead: read with the same polite fetcher, and a fact is proposed only with a quote from it
    (the brief's quote check then decides). An authority cap stops the track with what it has."""
    from . import products
    from .identity import name_key, norm
    from .research import is_social
    out = {"facts": [], "feed": {}, "searches": [], "wikipedia": None, "leads": [], "sources": [], "stopped": None,
           "name": None}

    def guarded(fn, what: str):
        if out["stopped"]:
            return None
        try:
            return fn()
        except BudgetExhausted as e:
            out["stopped"] = f"operating allowance reached ({e})"
        except NoRecording:
            out["stopped"] = "not in this recording"
        except (Cancelled, fetch.FetchBlocked):
            raise
        except Exception as e:  # noqa: BLE001 — one failed source is unknown, never the end of the track
            _activity(run, what, "enrichment", f"{type(e).__name__}", "FAILED", role="Researcher")
        return None

    end = time.monotonic() + 10  # the feed reads the homepage's Shopify flag and currency: wait for it (briefly), so a
    while not opened.wait(0.5) and time.monotonic() < end:  # replay asks what the recording asked (meta.json or not)
        run.check()
    feed = guarded(lambda: products.read_feed(run, run.project_id, url, home), "Read your store's product feed") or {}
    out["feed"] = {k: feed.get(k) for k in ("url", "status", "shopify", "products", "images", "currency")}
    out["facts"] += feed.get("facts") or []
    out["sources"] += [u for u in feed.get("sources") or []]

    end = time.monotonic() + 10  # the homepage title names the brand better than its address; don't wait long for it
    while not opened.wait(0.5) and time.monotonic() < end:
        run.check()
    name = out["name"] = _brand_name(run.project_id, url, home)

    cited: list[str] = []
    for q in SEARCHES:
        question = q.format(name=name, site=site)
        got = guarded(lambda: _ground(run, question), "Search the web")
        out["searches"].append({"query": question, "citations": len(got or []), "ran": got is not None})
        if got is not None:
            _activity(run, "Search the web", "Nova web grounding", f"'{question[:70]}…' · {len(got)} pages cited",
                      role="Researcher")
        cited += [u for u in got or [] if u not in cited]
    wiki = "https://en.wikipedia.org/wiki/" + "_".join(name.split())
    home_url = fetch.check_url(url)
    leads = list({_norm_url(u): u for u in [wiki, *cited] if not is_social(u) and _norm_url(u) != home_url}.values())[:MAX_LEADS]

    read: list[dict] = []
    for u in leads:
        def one(u=u):
            if not robots_allows(run, u):
                return {"url": u, "outcome": "REFUSED"}
            pg = read_polite(run, u)
            ok = pg.get("status") == 200 and bool(pg.get("text"))
            if ok and u == wiki and name_key(name) not in norm(pg.get("title")):
                ok = False  # a Wikipedia page about something else
            if ok:
                read.append(pg)
            return {"url": u, "outcome": "OK" if ok else pg.get("error") or f"HTTP {pg.get('status')}"}
        try:
            r = guarded(one, f"Read {fetch.site_of(u)}")
        except fetch.FetchBlocked as e:
            r = {"url": u, "outcome": f"blocked: {e}"}
        if r:
            out["leads"].append(r)
            out["sources"].append(_norm_url(u))
            if u == wiki:
                out["wikipedia"] = r["outcome"]
    if read:
        _activity(run, "Read the pages the web search cited", "website reader",
                  f"{len(read)} of {len(leads)} pages read", role="Researcher")
        facts = guarded(lambda: _lead_facts(run, name, site, read), "Read facts from web pages") or []
        out["facts"] += facts
    rec = {k: v for k, v in out.items() if k != "facts"} | {"proposed": len(out["facts"]), "at": db.now()}
    db.put_record("enrichment", run.project_id, rec, rid=f"enr_{run.project_id}")
    if out["stopped"]:
        _activity(run, "Fill in from other sources", "enrichment", f"Stopped: {out['stopped']}", "PARTIAL", role="Researcher")
    return out


def _lead_facts(run: Run, name: str, site: str, pages: list[dict]) -> list[dict]:
    listing = "\n\n".join(f"PAGE {p['url']}\nTITLE {p['title']}\nTEXT\n{p['text'][:LEAD_CHARS]}" for p in pages)
    agent = Agent(model=make_model(run.cassette, "enrich_reader", _usage_sink(run), max_tokens=3000),
                  system_prompt=ENRICH_SYSTEM, callback_handler=None)
    res = agent(f"Brand: {name} ({site})\n\n{listing}\n\nReturn the facts.", structured_output_model=LeadFacts,
                cancel_signal=run.cancel)
    run.check()
    read = {_norm_url(p["url"]) for p in pages} | {_norm_url(p["final_url"]) for p in pages}
    out = []
    for f in (res.structured_output.facts if res.structured_output else [])[:12]:
        src = _norm_url(f.source_url)
        if src not in read:
            continue  # a page the track didn't read is never a source
        field = f.field
        if field == "home_market" and not markets.parse_home(f.value)["country"]:
            field = "other"  # shown as "Based in", never a home without a country (it would hide the reader's reading)
        out.append({"field": field, "label": FACT_LABELS.get(f.field, f.label) if f.field != "other" else f.label,
                    "value": f.value, "source_url": src, "quote": f.quote,
                    "origin": "SOURCE" if fetch.same_site(src, site) else "WEB"})
    return out


class Described(BaseModel):
    brand_name: str
    stated: list[Stated] = Field(description="What the founder's description states, split into rows "
                                              "with labels like 'What you sell'")
    interpretation: list[Interpretation]
    unknowns: list[Unknown]


def brand_describe(run: Run):
    """Unreadable-site route: build the brief from the founder's one-line description (site facts not read)."""
    text = run.params["description"]
    _row(run, "summary", "RUNNING", "From your description")

    def describe():
        agent = Agent(model=make_model(run.cassette, "brand_describe", _usage_sink(run), max_tokens=2000),
                      system_prompt="You turn a founder's one-line description of their brand into brief rows. "
                                    "'stated' rows restate only what the founder said. Interpretations are your "
                                    "readings, clearly separate; each cites basis_facts = the numbers of the stated "
                                    "rows it rests on (1 = first), basis_pages = [], and one sentence of reasoning. "
                                    "Unknowns are what a launch decision would need that the description does not "
                                    "say. Never add facts the founder did not state.",
                      callback_handler=None)
        res = agent(f"Website (could not be read): {run.params['url']}\nFounder's description: {text}",
                    structured_output_model=Described, cancel_signal=run.cancel)
        return res.structured_output.model_dump()

    d = run.step("describe", describe)
    claims = [{"field": "other", "label": s["label"], "value": s["value"], "state": "STATED", "quote": text,
               "quote_found": True, "reason": "Your description"} for s in d["stated"]]
    run.step("summary", lambda: _assemble(run, {**d, "facts": claims}, claims, route="description"))
    run.emit("founder", {"task": "Description", "detail": text})
    _brief_ready(run)


HANDLERS["brand_read"] = brand_read
HANDLERS["brand_describe"] = brand_describe
