"""Stage 2 · grounded brand understanding (BUILD-PLAN §1 stage 2, CONTRACTS B2)."""
import hashlib
import re
import unicodedata
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from strands import Agent, tool

from . import db, fetch
from .nova import make_model
from .runner import HANDLERS, NeedsInput, Run

PROMPT_VERSION = "brand_reader@5"
CHECKER_VERSION = "support_check@3"
CHECKER = ("You check whether a verbatim quote from a web page supports a claim exactly as it is shown to a founder. "
           "Each claim has a field, a label and a value, and together they are the claim: field 'founded', label "
           "'Founded', value '2009' means 'the brand was founded in 2009'. First restate that full claim in "
           "claim_as_read. Then judge it. SUPPORTED: the quote itself states the full claim; a paraphrase or a "
           "translation is fine. PARTIAL: the quote supports only part of it, or the claim needs an inference beyond "
           "the quote (for example 'our cans are sold in Seattle' does not state 'the brand is based in Seattle'). "
           "Dates: a year in which something was done (selling, making, developing, shipping since a year) is not a "
           "founding date; only a quote saying the company was founded, established, started or launched then "
           "supports one. NOT_SUPPORTED: the quote does not support the claim. Judge only from the quote, never from "
           "what you know about the brand.")
MAX_PAGES = 8
PAGE_CHARS = 7000

ROWS = [("open", "Opening the website"), ("products", "Reading products"),
        ("about", "Reading positioning and about pages"), ("stockists", "Reading stockists and shipping"),
        ("check", "Checking each quote against its page"), ("summary", "Summarising what we know")]
FACT_LABELS = {"products": "What you sell", "where_sold": "Where you sell", "shipping": "Shipping",
               "community": "Community", "price": "Price points", "founded": "Founded", "other": "Also on the site"}
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
        db.x("INSERT INTO usage VALUES(?,?,?,?,?,?,?,?,?,?)", run.project_id, run.id, db.now(), "bedrock", u["stage"],
             int(u["replayed"]), u["input_tokens"], u["output_tokens"], u["est_usd"], u["status"])
    return sink


def _row(run: Run, row: str, status: str, detail: str = ""):
    run.emit("reading", {"row": row, "status": status, "detail": detail})


def _activity(run: Run, task: str, tool_name: str, detail: str, status: str = "DONE", role: str = "Brand reader"):
    run.emit("activity", {"stage": "Brand", "role": role, "tool": tool_name, "task": task, "detail": detail, "status": status})


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
    src = _store_source(run, fetch.check_url(url), page, ex)
    return {**src, "status": page.get("status"), "error": page.get("error")}


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
            last = read_page(run, url)
            if last.get("status") == 200 and len(last.get("text", "")) >= 150:
                return {"ok": True, "source_id": last["id"], "title": last["title"], "links": last.get("links", []),
                        "site": fetch.site_of(last["final_url"])}
            wait = last.get("retry_after")
            if attempt == 1 and last.get("status") in (429, 503) and wait and wait.isdigit() and int(wait) <= 30:
                run.cancel.wait(int(wait))  # honour a short Retry-After; longer waits are reported, not slept through
        err = last.get("error") or ""
        why = ("That address doesn't exist (no DNS record)" if err == "NoSuchHost"
               else "The site didn't respond" if "Connect" in err else "Timed out" if "Timeout" in err
               else f"The address can't be read ({err.removeprefix('blocked: ')})" if err.startswith("blocked") else err
               or ("The site is turning away automated readers (HTTP 429, asked us to wait "
                   f"{last.get('retry_after') or 'a while'} s)" if last.get("status") == 429 else None)
               or (f"The site answered with an error (HTTP {last.get('status')})" if last.get("status") != 200
                   else "The page has no readable text; it may need JavaScript"))
        return {"ok": False, "reason": f"{why}, 2 attempts", "attempts": 2}

    home = run.step("open", open_site)
    if not home["ok"]:
        _row(run, "open", "FAILED", home["reason"])
        for key, _ in ROWS[1:]:
            _row(run, key, "SKIPPED")
        _activity(run, f"Open {site}", "website reader", home["reason"], "FAILED")
        raise NeedsInput(f"unreadable: {home['reason']}")
    _row(run, "open", "DONE", f"{site} · {len(home['links'])} links on the homepage")
    _activity(run, f"Open {site}", "website reader", f"Homepage read · {len(home['links'])} same-site links")

    # A brand's domain may redirect (gotbag.com → got-bag.com); the site it lands on is the brand's own site.
    draft = run.step("reader", lambda: _run_reader(run, url, {site, home.get("site", site)}, home))
    run.check()
    claims = run.step("check", lambda: _check(run, draft))
    run.step("summary", lambda: _assemble(run, draft, claims, route="site"))


def _run_reader(run: Run, url: str, sites: set[str], home: dict) -> dict:
    pages: dict[str, dict] = {}
    purposes = {"products": [], "about": [], "stockists": []}
    for key in purposes:
        _row(run, key, "RUNNING")

    @tool
    def fetch_page(page_url: str, purpose: Literal["products", "about", "stockists", "shipping", "community", "other"]) -> str:
        """Read one page of the brand's own website. purpose says why you are reading it."""
        run.check()
        if not any(fetch.same_site(page_url, s) for s in sites):  # the entered domain and where it redirects
            return "Not read: only pages on the brand's own site can be read here."
        if len(pages) >= MAX_PAGES and page_url not in pages:
            return f"Not read: the {MAX_PAGES}-page limit is reached. Work with the pages you have."
        try:
            if not robots_allows(run, page_url):
                return "Not read: the site's robots.txt asks automated readers not to read this page."
            p = read_page(run, page_url)
        except fetch.FetchBlocked as e:
            return f"Not read: {e}"
        row = {"products": "products", "about": "about", "stockists": "stockists", "shipping": "stockists",
               "community": "about"}.get(purpose)
        path = urlsplit(p["url"]).path or "/"
        if p.get("status") != 200 or not p.get("text"):
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
    prompt = (f"Brand website: {url}\n\nHOMEPAGE {first['url']}\nTITLE {first['title']}\nTEXT\n{first['text'][:PAGE_CHARS]}\n\n"
              f"SAME-SITE LINKS ON THE HOMEPAGE:\n{links}\n\nRead the pages you need, then return the brief.")
    agent = Agent(model=make_model(run.cassette, "brand_reader", _usage_sink(run), max_tokens=6000),
                  system_prompt=SYSTEM, tools=[fetch_page], callback_handler=None)
    result = agent(prompt, structured_output_model=BriefDraft, cancel_signal=run.cancel)
    run.check()
    draft = result.structured_output
    if draft is None:
        raise RuntimeError("the Brand reader returned no brief")
    for key, paths in purposes.items():
        _row(run, key, "DONE" if paths else "SKIPPED", ", ".join(paths) if paths else "No page of this kind found")
    return draft.model_dump()


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
        elif v["verdict"] == "SUPPORTED":
            state, reason = "SUPPORTED", v["reason"]
        else:
            state, reason = "TO_CHECK", f"The quote is on the page but does not establish this claim: {v['reason']}"
        out.append({**c, "state": state, "support": v["verdict"] if v else None, "reason": reason,
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


def _assemble(run: Run, draft: dict, claims: list[dict], route: str) -> dict:
    sources = {}
    for s in db.q("SELECT id, url, final_url FROM sources WHERE project_id=?", run.project_id):
        sources[s["url"]] = sources[fetch.check_url(s["final_url"] or s["url"])] = s["id"]
    fact_ids = []
    for c in claims:
        rec = db.put_record("claim", run.project_id, {
            "subject": {"record_kind": "brief", "key": c["field"]}, "label": _label(c),
            "value": c["value"], "state": c["state"], "vc": "F" if route == "site" else "U",
            "source_ids": [sources[c["source_url"]]] if c.get("source_url") in sources else [],
            "quote": c.get("quote"), "quote_found": c.get("quote_found"), "support": c.get("support"),
            "reason": c.get("reason"), "claim_as_read": c.get("claim_as_read"), "checker": c.get("checker"),
            "produced_by": f"{run.id}/check"})
        fact_ids.append(rec["id"])
    read = {s["url"]: s["id"] for s in db.q("SELECT id, url FROM sources WHERE project_id=? AND status=200", run.project_id)}
    for s in db.q("SELECT id, final_url FROM sources WHERE project_id=? AND status=200", run.project_id):
        read.setdefault(s["final_url"], s["id"])
    states = {fid: c["state"] for fid, c in zip(fact_ids, claims)}
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
        "brand_name": draft["brand_name"], "route": route, "facts": fact_ids, "interpretation": interp_ids,
        "unknowns": draft["unknowns"], "corrections": [], "pages_read": pages, "read_at": db.now(),
        "prompt_version": PROMPT_VERSION}, inputs=[{"id": run.project_id, "version": 1, "keys": ["url"]}],
        rid=f"brief_{run.project_id}")
    _row(run, "summary", "DONE", f"{len(fact_ids)} facts · {len(interp_ids)} interpretations · {len(draft['unknowns'])} unknowns")
    _activity(run, "Summarise what we know", "brief", f"{len(fact_ids)} facts · {len(interp_ids)} inferences · "
              f"{len(draft['unknowns'])} unknowns")
    run.emit("brief", {"version": brief["version"]})
    return {"brief_id": brief["id"], "version": brief["version"]}


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


HANDLERS["brand_read"] = brand_read
HANDLERS["brand_describe"] = brand_describe
