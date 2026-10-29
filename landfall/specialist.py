"""Slice 4.5 · grounded launch-method specialists (BUILD-PLAN §4.5.2–4.5.6; CONTRACTS B6.5).

Built in order (§4.5.12). So far: the deterministic readiness axes (P45-R3), the locality rule (P45-R4), proximity,
event timing (P45-R6) and the retail assortment scan. The specialist runner, the method specs and Develop follow here.
"""
import calendar
import json
import re
from datetime import date, datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt
from zoneinfo import ZoneInfo

from . import markets
from .products import BROAD, PARENT, categories_in

ZONES = json.loads((markets.DATA / "entry.json").read_text())["timezones"]  # entry@1: IANA zone per city

FIT_EVIDENCE = ("QLOO_RELATED", "BRAND_FACT_MATCH")  # QLOO_LISTING and INTERPRETATION are discovery leads only
CONFLICT = ("SOURCES_DIFFER", "CHECK_FAILED")


def locality(window_names_city: bool, own_site_address_in_city: bool, placed_in_city: bool,
             passage_names_lead: bool) -> str:
    """LOCAL from the quote's window, the lead's own-site address, or a PLACED LeadLocation in the city whose passage
    names the lead. Placement establishes locality only, never identity or fit."""
    local = window_names_city or own_site_address_in_city or (placed_in_city and passage_names_lead)
    return "LOCAL" if local else "TO_CHECK"


def axes(fit: str, identity: str, locality: str, method_evidence: str, contact_route: str,
         location_state: str | None = None, digital: bool = False) -> dict:
    """One candidate's three axes and overall readiness, never blended: no axis compensates for another."""
    reasons = []
    if fit not in FIT_EVIDENCE:
        reasons.append({"QLOO_LISTING": "Qloo only listed it; no taste relationship to your brand",
                        "INTERPRETATION": "Fit is our interpretation, not a checked match",
                        "NONE": "No fit basis"}.get(fit, "No fit basis"))
    if digital:
        if method_evidence != "SOURCED":
            reasons.append("No platform option is sourced")
    else:
        if identity in CONFLICT:
            reasons.append("Sources differ about who this is" if identity == "SOURCES_DIFFER"
                           else "The identity check failed")
        elif identity != "VERIFIED":
            reasons.append("Identity not verified by a page")
        if location_state == "SOURCES_DIFFER":
            reasons.append("Sources differ about where it is")
        if locality != "LOCAL":
            reasons.append("Not yet shown to be in this city")
        if method_evidence != "SUPPORTED":
            reasons.append("The way in is not supported by a checked quote")
        if contact_route == "UNKNOWN":
            reasons.append("No known contact route")
    return {"fit": fit,
            "grounding": {"identity": "NA" if digital else identity, "locality": "NA" if digital else locality,
                          "method_evidence": method_evidence},
            "operational": {"contact_route": "NA" if digital else contact_route, "willingness": "UNKNOWN"},
            "overall": "HYPOTHESIS_LED" if reasons else "EVIDENCED", "reasons": reasons}


def readiness(kept: list[dict]) -> dict:
    """A dossier's readiness from its KEPT candidates' axes: NOT_YET with none, else its best candidate's."""
    if not kept:
        return {"overall": "NOT_YET", "reasons": ["No candidate or channel survived the checks"]}
    best = next((a for a in kept if a["overall"] == "EVIDENCED"), kept[0])
    return {**best, "candidates": len(kept)}


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Straight-line (haversine) distance, for logistics labels only: never travel time, never cultural fit."""
    (la1, lo1), (la2, lo2) = [(radians(x), radians(y)) for x, y in (a, b)]
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0088 * asin(sqrt(h))


def distance_label(d: float) -> str:
    return f"≈ {d:.1f} km straight-line"


# ---- event timing (P45-R6): an event's own date, judged at use in the city's timezone; never stored as truth ----
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m} | \
          {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m} | {"sept": 9}
_MON = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DAYS = [d.lower() for d in calendar.day_name]  # monday = 0, as date.weekday()
_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MON})\.?(?:,?\s+(\d{{4}}))?\b", re.I),
     lambda m: (m[3] and int(m[3]), _MONTHS[m[2].lower()], int(m[1]))),
    (re.compile(rf"\b({_MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", re.I),
     lambda m: (m[3] and int(m[3]), _MONTHS[m[1].lower()], int(m[2]))),
]
_NUMERIC = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})\b")


def event_dates(text: str, city_id: str) -> list[dict]:
    """Every date written in a quote: {date (ISO) | None, text, tz, year_known}. A missing year, or a numeric date
    whose day and month could swap (3/4/2026), is kept but stays UNDATED. Dates never come from the fetch time."""
    tz, out, taken = ZONES.get(city_id), [], []
    for rx, parts in _DATE_PATTERNS:
        for m in rx.finditer(text or ""):
            if any(s < m.end() and m.start() < e for s, e in taken):
                continue
            taken.append(m.span())
            y, mo, d = parts(m)
            try:
                iso = date(y or 2000, mo, d).isoformat()
            except ValueError:
                continue
            out.append({"date": iso if y else None, "month_day": iso[5:], "text": m[0], "tz": tz, "year_known": bool(y)})
    for m in _NUMERIC.finditer(text or ""):
        a, b, y = int(m[1]), int(m[2]), int(m[3]) % 100 + 2000 if len(m[3]) == 2 else int(m[3])
        day, mo = (a, b) if a > 12 >= b else (b, a) if b > 12 >= a else (None, None)
        try:
            iso = date(y, mo, day).isoformat() if day else None
        except ValueError:
            iso = None
        out.append({"date": iso, "text": m[0], "tz": tz, "year_known": True, "ambiguous": iso is None})
    return out


def today_in(city_id: str, now: datetime) -> date:
    return now.astimezone(ZoneInfo(ZONES[city_id])).date()


def when(ed: dict, now: datetime) -> str:
    """PAST, UPCOMING or UNDATED, as of `now` in the event's city timezone."""
    if not ed.get("date") or not ed.get("tz"):
        return "UNDATED"
    return "PAST" if date.fromisoformat(ed["date"]) < now.astimezone(ZoneInfo(ed["tz"])).date() else "UPCOMING"


_DAY = "|".join(_DAYS)
SCHEDULE = re.compile(
    rf"\b(?:every|each)\s+(?:other\s+)?(?:{_DAY}|day|morning|evening|week|weekend|month)\b|"
    rf"\b(?:first|second|third|fourth|last)\s+(?:{_DAY})\s+of\s+(?:each|every|the)\s+month\b|"
    rf"\b(?:weekly|fortnightly|monthly)\b|\b(?:{_DAY})s\b", re.I)
_WEEKLY = re.compile(rf"\b(?:every|each)\s+({_DAY})\b|\b({_DAY})s\b", re.I)
_MONTHLY = re.compile(rf"\b(first|second|third|fourth|last)\s+({_DAY})\s+of\s+(?:each|every|the)\s+month\b", re.I)


def schedule_in(quote: str) -> str | None:
    """The schedule phrase a quote states ("every Saturday", "first Thursday of each month"), or None."""
    m = SCHEDULE.search(quote or "")
    return m[0] if m else None


def next_from_schedule(schedule: str, today: date) -> date | None:
    """The next occurrence a weekly or monthly-ordinal schedule states (today counts); other schedules give none."""
    if m := _MONTHLY.search(schedule):
        nth, wd = ["first", "second", "third", "fourth", "last"].index(m[1].lower()), _DAYS.index(m[2].lower())
        for k in range(3):
            y, mo = divmod(today.month - 1 + k, 12)
            days = [d for d in calendar.Calendar().itermonthdates(today.year + y, mo + 1)
                    if d.month == mo + 1 and d.weekday() == wd]
            d = days[-1] if nth == 4 else days[nth]
            if d >= today:
                return d
    if m := _WEEKLY.search(schedule):
        wd = _DAYS.index((m[1] or m[2]).lower())
        return today + timedelta(days=(wd - today.weekday()) % 7)
    return None


def activity_timing(quote: str, dates: list[dict], city_id: str, now: datetime,
                    fetched_at: datetime | None = None) -> dict:
    """Recurrence and timing of one activity, computed at use. RECURRING needs a quoted schedule; dated instances alone
    are REPEATED_BEFORE (history, no next date). `next_date` only from a schedule or a dated future listing."""
    schedule = schedule_in(quote)
    dated = [d for d in dates if d.get("date")]
    whens = [when(d, now) for d in dated]
    recurrence = ("RECURRING" if schedule else "REPEATED_BEFORE" if len(dated) >= 2
                  else "ONE_OFF" if len(dated) == 1 else "UNKNOWN")
    today = today_in(city_id, now)
    upcoming = sorted(d["date"] for d, w in zip(dated, whens) if w == "UPCOMING")
    nxt = next_from_schedule(schedule, today) if schedule else None
    next_date = nxt.isoformat() if nxt else upcoming[0] if upcoming else None
    stale = bool(schedule) and (bool(fetched_at and now - fetched_at > timedelta(days=60))
                                or bool(dated) and all(w == "PAST" for w in whens))
    return {"recurrence": recurrence, "schedule_quote": schedule, "event_dates": dated,
            "when": dict(zip([d["date"] for d in dated], whens)), "next_date": next_date, "possibly_stale": stale}


# ---- retail: assortment_scan (deterministic, own-site snapshots; no provider) ----
_OUTSIDE = re.compile(r"\b(?:brands|labels|designers|makers|we (?:carry|stock)|stockists? of)\b", re.I)
_LEAD_IN = re.compile(r"^(?:\s*(?:we (?:carry|stock)|[:\-–]|like|including|such as|from|by|include)(?!\w))+\s*", re.I)
_NAME = re.compile(r"^[A-Z0-9][\w'’.-]*(?:\s+(?:&\s+)?[A-Z0-9][\w'’.-]*){0,3}$")
_CARRIES = re.compile(r"\b(?:curated|independent|local|designer|luxury|outside|emerging)\s+(?:brands|labels|designers|makers)\b", re.I)
_ROUTE = re.compile(r"\b(?:wholesale|become a stockist|stockist enquir\w+|sell with us|brand submissions?|"
                    r"vendor (?:application|enquir\w+)|submit your (?:brand|products?))\b", re.I)
_SENTENCE = re.compile(r"[^.!?\n]*")


def _sentence_at(text: str, i: int) -> str:
    start = max(text.rfind(c, 0, i) for c in ".!?\n") + 1
    return text[start:i] + _SENTENCE.match(text, i)[0]


def assortment_scan(text: str, own_name: str = "") -> dict:
    """What a shop's own page shows about what it carries. `brands`: outside brand names listed after "brands",
    "labels", "we carry"…; `carries_claim`: a sentence like "curated luxury brands" for the checker to judge (not
    evidence until it is SUPPORTED); `categories` from category_terms@1; `route`: a published wholesale/stockist way in.
    ponytail: a capitalised-list heuristic; misses lowercase brand names and names given only as logos."""
    brands, own = [], markets.fold(own_name)
    for m in _OUTSIDE.finditer(text or ""):
        tail = _LEAD_IN.sub("", _SENTENCE.match(text, m.end())[0])
        for item in re.split(r",\s*(?:and\s+)?|\s+and\s+", tail):
            name = item.strip(" .;:")
            if _NAME.match(name) and markets.fold(name) != own and name not in [b["name"] for b in brands]:
                brands.append({"name": name, "quote": _sentence_at(text, m.start()).strip()})
    carries = _CARRIES.search(text or "")
    route = _ROUTE.search(text or "")
    return {"brands": brands, "carries_claim": carries and _sentence_at(text, carries.start()).strip(),
            "categories": categories_in(text), "route": route and _sentence_at(text, route.start()).strip()}


def category_overlap(brand_categories: list[str], shop_categories: list[str]) -> str | None:
    """MATCH: a shared specific category. PARTIAL: only a broader one (the shop says apparel, the brand sells shirts).
    NONE: the shop's categories are known and unrelated (CATEGORY_MISMATCH). None: the shop's categories aren't shown."""
    if not shop_categories or not brand_categories:  # unknown on either side is unknown, never a mismatch
        return None
    specific = set(brand_categories) - set(BROAD)
    if specific & set(shop_categories):
        return "MATCH"
    broad = {PARENT.get(c, c) for c in brand_categories} | (set(brand_categories) & set(BROAD))
    return "PARTIAL" if broad & set(shop_categories) else "NONE"


# ---- city_check@4 (P45-R4): quote support split from deterministic locality; prose against a labeled bundle ----
CHECK_VERSION = "city_check@4"  # @4: the claim no longer says "in {city}"; prose evidence carries its role
CHECKER = ("You check whether a verbatim quote from a web page supports a claim about its subject, exactly as the "
           "claim is shown to a founder. Each claim reads '{subject}: {text}'. Where the subject is located is checked "
           "separately; do not judge location unless the claim text itself names a place. SUPPORTS: the quote itself "
           "states the whole claim; a paraphrase or translation is fine. PARTIAL: it states only part, or the claim needs "
           "an inference beyond the quote. UNRELATED: the quote does not establish the claim. CONTRADICTS: the quote says "
           "the opposite of another listed claim about the same subject; give that claim's id in `contradicts`. In "
           "unsupported_parts list every part of the claim the quote does not itself state; leave it empty only when the "
           "quote states the whole claim. First restate the full claim in claim_as_read. Each claim also names a way in; "
           "set shows_way_in true only when the quote itself shows it. Judge only from the quote, never from what you "
           "know about the subject.")
PROSE = ("You sort sentences a founder will read about one way into a city. Each comes with its evidence, every item "
         "labeled with its role: CLAIM (a checked quote from a page), BRAND_FACT (a checked fact about the brand), "
         "PRODUCT (one of the brand's products, from its own product page), QLOO (a cultural-relationship result: "
         "never demand, size or behavior), PLATFORM (an ad platform's documented option type), FOUNDER (what the "
         "founder said). PROPOSAL: it proposes something to do or try and states nothing about the world beyond its "
         "evidence. QUESTION: it names something unknown or still to check. SUPPORTED_FACT: every statement of fact in "
         "it is stated by its evidence (a paraphrase is fine). UNSUPPORTED_FACT: it states anything its evidence does "
         "not: what a place, person or group is like, does, offers or cares about, any audience trait, or any number. "
         "A proposal that also states an unsupported fact is UNSUPPORTED_FACT. Judge only from the listed evidence.")
ROLES = ("CLAIM", "BRAND_FACT", "PRODUCT", "QLOO", "PLATFORM", "FOUNDER")
# Every displayed model-written field the prose check judges (BUILD-PLAN §4.5.5 "Checked fields").
CHECKED_FIELDS = {
    "common": ("concept.name", "concept.line", "why_it_fits[].text", "interpretations[]"),
    "retail": ("retail.partnership_hypothesis", "retail.possible_activation"),
    "popup": ("popup.activation_outline.format", "popup.activation_outline.duration", "popup.activation_outline.footprint"),
    "creator": ("creator.collaboration_concepts[]",),
    "community": ("community.concept", "community.participation_assumptions[].text"),
    "digital": ("digital.message.line", "digital.targeting[].element", "digital.creative_requirements[].text"),
    "draft": ("concept.name", "concept.line", "activities[]", "success_measure", "audience_hypothesis"),
}


def claim_listing(claims: list[dict], way_in: dict[str, str]) -> str:
    """The checker's request: "{subject}: {text}", never "in {city}" — a quote is not asked to prove what only a
    placement or an address shows."""
    return "\n".join(f"- id={c['cid']} | {c['subject_name']}: {c['text']} | way in: {way_in.get(c['method'], 'none')} | "
                     f"quote: \"{c['quote']}\"" for c in claims)


def claim_state(verdict: dict | None) -> str:
    """SUPPORTED only for a whole-claim SUPPORTS; a missing or malformed verdict fails closed to TO_CHECK."""
    if not verdict or verdict.get("error"):
        return "TO_CHECK"
    if verdict["verdict"] == "CONTRADICTS":
        return "SOURCES_DIFFER"
    return "SUPPORTED" if verdict["verdict"] == "SUPPORTS" and not verdict.get("unsupported_parts") else "TO_CHECK"


def claim_locality(claim: dict, city_name: str, lead: dict | None, lead_location: dict | None, city_id: str,
                   own_site_address_in_city: bool = False) -> str:
    """Deterministic: the quote's window names the city, the lead's own site gives an address in it, or the lead's own
    LeadLocation is PLACED in this city and the passage names the lead. A placement of another lead never counts."""
    window = claim.get("window") or claim.get("quote") or ""
    placed = bool(lead and lead_location and lead_location.get("lead_key") == lead.get("lead_key")
                  and lead_location.get("state") == "PLACED" and lead_location.get("city_id") == city_id)
    names = bool(lead and markets.fold(lead["name"]) in markets.fold(window))
    return locality(markets.fold(city_name) in markets.fold(window), own_site_address_in_city, placed, names)


def prose_listing(items: list[dict]) -> str:
    """items: {id, field, text, evidence: [{role, text}]}. Each evidence line carries its role."""
    lines = []
    for it in items:
        ev = " / ".join(f"{e['role']}: {e['text']}" for e in it["evidence"] if e["role"] in ROLES) or "none"
        lines.append(f"- id={it['id']} | {it['field']} | sentence: \"{it['text']}\" | evidence: {ev}")
    return "\n".join(lines)


def prose_keep(items: list[dict], verdicts: list[dict], leads: list[dict] = ()) -> dict[str, bool]:
    """Fail closed: an unjudged sentence, or a fact about a named lead its evidence doesn't contain, is dropped."""
    from .research import asserts_unchecked
    kinds = {v["id"]: v["kind"] for v in verdicts}
    out = {}
    for it in items:
        kind = kinds.get(it["id"])
        if kind and asserts_unchecked(it["text"], list(leads), [e["text"] for e in it["evidence"]]):
            kind = "UNSUPPORTED_FACT"
        out[it["id"]] = kind in ("PROPOSAL", "QUESTION", "SUPPORTED_FACT")
    return out


def targeting_basis(t: dict, platform_ids: set[str], consumed_audiences: set[str]) -> dict:
    """Deterministic downgrades: a PLATFORM_OPTION must reference an existing platforms@1 entry, and a QLOO_DERIVED
    element must name an audience this dossier consumed; otherwise TO_CHECK_IN_ADS_MANAGER, with the reason."""
    if t.get("basis") == "PLATFORM_OPTION" and t.get("platforms_ref") not in platform_ids:
        return {**t, "basis": "TO_CHECK_IN_ADS_MANAGER", "downgraded": "no documented platform option matches"}
    if t.get("basis") == "QLOO_DERIVED" and t.get("element") not in consumed_audiences:
        return {**t, "basis": "TO_CHECK_IN_ADS_MANAGER", "downgraded": "not an audience this research used"}
    return t


# ---- the specialist runner: one code path; a method is a spec (prompt · tools · caps · schema · validator) ----
import hashlib  # noqa: E402
from typing import Literal, get_origin  # noqa: E402

from pydantic import BaseModel, Field, model_validator  # noqa: E402
from strands import Agent, tool  # noqa: E402
from strands.hooks import MessageAddedEvent  # noqa: E402
from strands.tools.executors import SequentialToolExecutor  # noqa: E402

from . import creators, db, fetch, products, qloo, ranking, research, runner  # noqa: E402
from .brand import _norm, quote_found  # noqa: E402
from .cassette import NoRecording  # noqa: E402
from .ledger import BudgetExhausted  # noqa: E402
from .nova import make_model  # noqa: E402
from .runner import HANDLERS, Run  # noqa: E402

# model 8 → 10 (27 Oct 2026): LIVE retail used all 8 calls before it could submit, and failed
SPEC_CAPS = {"model": 10, "search": 3, "page": 6, "presence": 3, "qloo": 4, "urn": 0, "check": 2, "prose": 2,
             "locate": 12, "profile": 5}
SPEC_USD, SPEC_EXPECTED = 1.00, 0.20  # owner, 28 Oct 2026: US$0.40 → US$1.00 per investigation
# community and creator @3 (owner, 27 Oct 2026): about 6 searches and 10 page reads, inside the same per-investigation cap
TARGETED_CAPS = {**SPEC_CAPS, "model": 10, "search": 6, "page": 10}
COMMERCIAL = ("wholesale or consignment", "minimum order", "margin", "exclusivity", "payment terms")
TERMINAL_SPEC = ("complete", "partial", "failed", "not_run")


class Refused(research.Refused):
    pass


def _k(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:16]


def spec_row(pid: str, cid: str, method: str, v: int | None = None):
    rows = (db.q("SELECT * FROM specialist_runs WHERE project_id=? AND city_id=? AND method=? AND version=?", pid, cid, method, v)
            if v else db.q("SELECT * FROM specialist_runs WHERE project_id=? AND city_id=? AND method=? "
                           "ORDER BY version DESC LIMIT 1", pid, cid, method))
    return rows[0] if rows else None


def _set_spec(pid: str, cid: str, method: str, v: int, **fields) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    db.x(f"UPDATE specialist_runs SET {cols} WHERE project_id=? AND city_id=? AND method=? AND version=?",
         *[json.dumps(x) if k in ("input", "transcript", "submission") and x is not None else x for k, x in fields.items()],
         pid, cid, method, v)


def ruled_by_note(pid: str, cid: str, method: str) -> str | None:
    """The founder's note ruled this method out in the city research (slice 4: NOT_SUITABLE_NOW from YOU SAID)."""
    cr = research.latest_research(pid).get(cid)
    a = next((a for a in (cr or {}).get("approaches", []) if a["method"] == method), None)
    if a and a["status"] == "NOT_SUITABLE_NOW" and a.get("reason_source") == "YOU_SAID":
        return (cr["input"] or {}).get("context") or "your note"
    return None


def workspace(pid: str, cid: str, method: str) -> dict | None:
    return db.get_record(f"ws_{pid}_{cid}_{method}")


def build_spec_input(pid: str, cid: str, method: str, prefs: dict) -> dict:
    """SpecialistInput (CONTRACTS B6.5), pinned at the specialist's start: no other specialist's transcript, ever."""
    cr = research.latest_research(pid).get(cid)
    prior_leads = [db.get_record(i) for i in (cr or {}).get("lead_ids", [])]
    a = next((a for a in (cr or {}).get("approaches", []) if a["method"] == method), None)
    ws = workspace(pid, cid, method)
    saved = (ws or {}).get("founder_prefs") or {}
    # P45-I10: an omitted preference keeps the saved one; an explicit [] or "" clears it
    prefs = {k: prefs.get(k) if prefs.get(k) is not None else saved.get(k) for k in ("rationale", "include", "exclude")}
    pr = db.latest("product_research", pid)
    return {"city_id": cid, "method": method, "research_input": research.build_input(pid, cid),
            "prior": {"city_research": cr and {"id": cr["id"], "version": cr["version"]},
                      "lead_ids": [x["id"] for x in prior_leads if x and (a is None or x["id"] in a.get("lead_ids", []))],
                      "claim_ids": (a or {}).get("claim_ids", []), "lead_locations": []},
            "workspace": ws and {"id": ws["id"], "version": ws["version"]},
            "founder_prefs": {"rationale": prefs.get("rationale"), "include": prefs.get("include") or [],
                              "exclude": prefs.get("exclude") or [],
                              "profiles": [x["creator"]["canonical_url"] for x in db.records("lead", pid)
                                           if method == "creator" and x.get("city_id") == cid
                                           and (x.get("creator") or {}).get("identity") == "YOU_SAID"]},
            "products": [{"id": p["id"], "version": p["version"]} for p in (pr or {}).get("products", [])],
            "spec": {"prompt_version": SPECS[method].prompt_version, "check_version": CHECK_VERSION,
                     "tools": SPECS[method].tool_names, "caps": SPECS[method].caps, "usd_cap": SPECS[method].usd,
                     **({"platforms": PLATFORMS["version"]} if method == "digital" else {})}}


def _products(inp: dict) -> list[dict]:
    return [db.get_record(p["id"], p["version"]) for p in inp["products"]]


def _sells(inp: dict) -> list[dict]:
    return [f for f in inp["research_input"]["facts"] if f["state"] in ("SUPPORTED", "STATED")
            and f["label"] in ("What you sell",)]


def brand_categories(inp: dict) -> list[str]:
    """What the brand sells, as category_terms@1: its checked "what you sell" facts plus its products' own categories
    (each read from the product's page)."""
    found = categories_in(" ".join(f["value"] for f in _sells(inp)))
    return list(dict.fromkeys(found + [c for p in _products(inp) for c in p["category_terms"]]))


def sells_established(inp: dict) -> bool:
    """What the brand sells is established by a SUPPORTED or STATED fact, or by a product read from its own page."""
    return bool(_sells(inp)) or any(p["category_terms"] for p in _products(inp))


def prompt_of(inp: dict, spec) -> str:
    """Deterministic rendering of the SpecialistInput: no ids of ours, dates or affinity."""
    ri = inp["research_input"]
    facts = "\n".join(f"- {f['label']}: {f['value']} ({f['state']})" for f in ri["facts"]) or "- none"
    prods = "\n".join(f"- {p['title']}" + (f" · {', '.join(p['category_terms'])}" if p["category_terms"] else "")
                      + (f" · {', '.join(p['materials'])}" if p["materials"] else "") for p in _products(inp)) or "- none read"
    leads = [db.get_record(i) for i in inp["prior"]["lead_ids"]]
    prior = "\n".join(f"- {x['name']}" + (f" · {x['website']}" if x.get("website") else "") for x in leads if x) or "- none"
    ws = inp["workspace"] and db.get_record(inp["workspace"]["id"], inp["workspace"]["version"])
    tried = "\n".join(f"- {c['ref']}: {c['decision']} ({c['reason_code']})" for c in (ws or {}).get("candidates", [])) or "- none"
    fp = inp["founder_prefs"]
    supplied = fp.get("profiles") or []
    you = "\n".join(filter(None, [fp["rationale"] and f"Why: {fp['rationale']}",
                                  supplied and "Creator profiles the founder named (check them): " + ", ".join(supplied),
                                  fp["include"] and f"Include: {', '.join(fp['include'])}",
                                  fp["exclude"] and f"Exclude: {', '.join(fp['exclude'])}",
                                  ri["context"] and f"Note: {ri['context']}"])) or "- nothing"
    return (f"City: {ri['name']}, {markets.COUNTRY_NAME.get(ri['country'], ri['country'])}\n\nBRAND FACTS:\n{facts}\n\n"
            f"THE BRAND'S PRODUCTS (from its own product pages):\n{prods}\n\nEARLIER LEADS FOR THIS METHOD:\n{prior}\n\n"
            f"ALREADY DECIDED IN AN EARLIER INVESTIGATION:\n{tried}\n\nTHE FOUNDER SAYS:\n{you}\n\n"
            f"{target_prompt(inp)}Investigate, then call {spec.submission.__name__}.")


def _ctx(run: Run, inp: dict, cid: str, method: str, v: int) -> research.Ctx:
    spec = SPECS[method]
    return research.Ctx(run, run.project_id, cid, v, inp["research_input"], caps=spec.caps, usd_cap=spec.usd,
                        expected_usd=min(SPEC_EXPECTED, spec.usd), cr_key=f"{run.project_id}:{cid}:{method}:s{v}",
                        submit=SPECS[method].submission.__name__, nudge_at=spec.caps["model"] - 3,
                        wrap_at=spec.caps["model"] - 2, stage="specialist",
                        forced_usd=research.FORCED_USD if spec.tool_names else 0.0)


def run_loop(ctx: research.Ctx, spec, row, inp: dict) -> dict:
    """The tool loop, rebuilt from its persisted transcript after a restart or a Continue (completed tool calls return
    their stored results). A cap refusal gets one forced submission inside the cap."""
    saved = json.loads(row["transcript"]) if row["transcript"] else None
    model = make_model(ctx.run.cassette, "specialist", research.usage_sink(ctx, "model"), max_tokens=research.MAX_OUT,
                       admit=research.admitter(ctx, "model"))
    agent = Agent(model=model, system_prompt=spec.system, tools=spec.tools(ctx), messages=saved, callback_handler=None,
                  tool_executor=SequentialToolExecutor())

    def persist(event: MessageAddedEvent) -> None:
        if event.message.get("role") == "user":
            _set_spec(ctx.pid, ctx.cid, spec.method, ctx.rv, transcript=json.loads(json.dumps(agent.messages, default=str)))
    agent.hooks.add_callback(MessageAddedEvent, persist)
    out = None
    try:
        res = agent(None if saved else spec.prompt(inp), structured_output_model=spec.submission,
                    cancel_signal=ctx.run.cancel)
        out = res.structured_output
    except Exception as e:
        if not (research._caused_by(e, research.CityCap) or research._no_submission(e)):
            raise
    ctx.run.check()
    if out is None:
        ctx.forced = True
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            out = agent.structured_output(spec.submission, f"A limit is reached. Call {spec.submission.__name__} now "
                                          "with what you have; anything not established stays to check.")
    return out.model_dump()


def _judge(ctx: research.Ctx, system: str, listing: str, model_cls, kind: str, key: str):
    """One memoized checker call (city_check@4); fails closed to {"error"} on anything but a stop or a replay miss."""
    def go():
        try:
            agent = Agent(model=make_model(ctx.run.cassette, "specialist_check", research.usage_sink(ctx, "check"),
                                           max_tokens=3000, admit=research.admitter(ctx, "check")),
                          system_prompt=system, callback_handler=None)
            out = agent(listing, structured_output_model=model_cls, cancel_signal=ctx.run.cancel).structured_output
        except (NoRecording, runner.Cancelled):
            raise
        except Exception as e:
            return {"error": "not allowed" if research._caused_by(e, BudgetExhausted) else type(e).__name__, "verdicts": []}
        return {"verdicts": [v.model_dump() for v in out.verdicts]} if out else {"error": "no answer", "verdicts": []}
    res, refused = research.memo(ctx, kind, key, go)
    return res or {"error": refused, "verdicts": []}


def attempts_of(ctx: research.Ctx) -> list[dict]:
    """MethodWorkspace attempts from this run's checkpoints: FAILED is never shown as absence, EMPTY says by what."""
    out = []
    for r in db.q("SELECT kind, key, status, result, at FROM research_steps WHERE cr=? AND kind IN "
                  "('qloo','search','page','presence','locate','profile') ORDER BY at, rowid", ctx.cr):
        res = json.loads(r["result"] or "{}")
        err, st = str(res.get("error") or ""), res.get("status")
        if r["status"] != "done" or st == "failed" or (err and not err.startswith("not run")):
            outcome = "FAILED"  # never cached, never shown as absence
        elif st == "refused" or err.startswith("not run"):
            outcome = "REFUSED"
        elif st == "empty" or res.get("citations") == [] or (isinstance(st, int) and st != 200):
            outcome = "EMPTY" if st == "empty" or res.get("citations") == [] else "FAILED"
        else:
            outcome = "OK"
        out.append({"tool": r["kind"], "args_hash": r["key"].split(":", 1)[-1][:16], "outcome": outcome,
                    "request_id": res.get("request_id"), "source_id": res.get("id") or res.get("source_id"), "at": r["at"]})
    return out


DIGITAL_CAPS = {"model": 2, "search": 0, "page": 0, "presence": 0, "qloo": 0, "urn": 0, "check": 0, "prose": 1,
                "locate": 0, "profile": 0}


# @2 (LIVE L45-A attempt 1): the model called one tool per turn and ran out of turns after 1 search and 3 pages.
TURNS = """

Budget: about 6 turns, then you must submit. In each turn call SEVERAL tools at once whenever they don't depend on each
other (for example a web search and a Qloo list together, or three pages at once). Submit by your 7th turn with what
you have; anything not established goes in `missing`."""


# community and creator @3: about 6 searches and 10 pages (owner, 27 Oct 2026: "spend is fine"), so more turns.
TURNS_LONG = """

Budget: about 8 turns, then you must submit. In each turn call SEVERAL tools at once whenever they don't depend on each
other (for example two web searches together, or three pages at once). Submit by your 9th turn with what you have;
anything not established goes in `missing`, and every name you couldn't confirm goes in `leads`."""


class Spec:
    def __init__(self, method, prompt_version, system, submission, tool_names, extra_tools, validate, caps=None,
                 usd=None, prompt=None, turns=TURNS):
        system = system + (turns if tool_names else "")
        self.method, self.prompt_version, self.system, self.submission = method, prompt_version, system, submission
        self.tool_names, self._extra, self.validate = tool_names, extra_tools, validate
        self.caps, self.usd = caps or SPEC_CAPS, usd or SPEC_USD
        self.prompt = prompt or (lambda inp: prompt_of(inp, self))

    def tools(self, ctx: research.Ctx) -> list:
        shared = {t.tool_name: t for t in research.make_tools(ctx)}
        return [shared[n] for n in self.tool_names if n in shared] + self._extra(ctx)


# ---- retail_specialist@1 ----
RETAIL_SYSTEM = """You are the Retail specialist on Landfall, an international launch team for small consumer brands. In ONE
city, find up to 3 independent local shops that could be credible first stockists or retail collaborators for this brand,
and show the evidence.

Tools: qloo_places(method="retail") lists shops Qloo relates to the brand; web_search finds independent shops and
"brands we carry" or stockist pages; fetch_page reads a page; check_presence reads a shop's own site for an address in
this city; assortment reads a shop's own page and lists the outside brands, categories and wholesale route it shows (it
also returns the page's text, so don't fetch_page the same address again).

Rules:
- Retail fit needs assortment evidence (the shop's own site names outside brands it carries, or a page states that it
  stocks independent labels) and an overlap with what the brand sells. Big chains are not first stockists.
- A claim needs a page you read and an exact quote (5-40 words) copied from it. Name each shop exactly as its page does.
- Never claim a shop accepts wholesale or consignment, margins, sales, footfall, or that it would carry this brand.
  Those are questions for the founder to ask.
- Shops ruled out, and why, go in `rejected` (CHAIN, CATEGORY_MISMATCH, NOT_LOCAL, CLOSED, NO_ASSORTMENT_EVIDENCE).
- concept, partnership_hypothesis and possible_activation are proposals (a shelf trial, a trunk show) that may name one of
  the brand's products; they state no facts about a shop beyond your claims."""


class ShopClaimIn(research.Lenient):
    text: str = Field(description="One short sentence: what the quote shows about this shop")
    source: str = Field(description="The SOURCE id of a page you read, e.g. S1a2b3c")
    quote: str = Field(description="Copied exactly from that page's text, 5-40 words")


class ShopIn(research.Lenient):
    name: str
    qloo_ref: str | None = Field(None, description="The bracketed code from qloo_places, e.g. R3, if it came from Qloo")
    own_site: str | None = Field(None, description="The shop's own website, only if a page you read gives it")
    claims: list[ShopClaimIn] = Field(default_factory=list)


class RejectedIn(research.Lenient):
    name: str
    reason_code: str = Field(description="CHAIN, CATEGORY_MISMATCH, NOT_LOCAL, CLOSED or NO_ASSORTMENT_EVIDENCE")
    reason: str = ""


class RetailDossierIn(research.Lenient):
    concept_name: str = ""
    concept_line: str = ""
    partnership_hypothesis: str = ""
    possible_activation: str = ""
    shops: list[ShopIn] = Field(default_factory=list)
    rejected: list[RejectedIn] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list, description="Evidence you looked for and did not find")


def retail_tools(ctx: research.Ctx) -> list:
    @tool
    def assortment(url: str) -> str:
        """Read a shop's own page and list the outside brands, categories and wholesale or stockist route it shows."""
        if research.is_social(url):
            return "Not read: social platforms are never read."
        try:
            url = fetch.check_url(url)
        except fetch.FetchBlocked as e:
            return f"Not read: {e}."
        res, refused = research.memo(ctx, "page", url, lambda: {
            **{k: v for k, v in research.load_page(ctx.run, ctx.pid, ctx.cid, url, f"{ctx.cr}:page:{url}",
                                                   stage="specialist_fetch").items()
               if k in ("id", "url", "final_url", "status", "error", "reused")}, "method": "retail"})
        if refused:
            return f"Not read: {refused}."
        s = research.source(res["id"])
        if not research.readable(s):
            return f"Not read: page unavailable ({s.get('error')}). Unknown, not absent: no brands can be inferred."
        scan = assortment_scan(s["text"])
        return (f"SOURCE {research.source_code(s['url'])}\nOUTSIDE BRANDS NAMED: {', '.join(b['name'] for b in scan['brands']) or 'none found'}\n"
                f"CARRIES: {scan['carries_claim'] or 'no statement found'}\nCATEGORIES: {', '.join(scan['categories']) or 'none found'}\n"
                f"STOCKIST OR WHOLESALE ROUTE: {scan['route'] or 'none found'}\nTEXT\n{s['text'][:research.TOOL_CHARS]}")
    return [assortment]


# ---- shared validation helpers (every place-based spec: retail, pop-up, community) ----
LOCATE_KM = 24.14  # 15 miles: the radius Qloo /search placement was verified with (QLOO-CAPABILITIES §10.5)


def _tight(s: str) -> str:
    """Folded with spaces removed: Qloo writes "MartinPatrick3" for the page's "MartinPatrick 3"."""
    return markets.fold(s).replace(" ", "")


def pages_of(ctx: research.Ctx) -> dict[str, dict]:
    """Every page this run read, by the source code the agent saw."""
    out = {}
    for st in research.steps(ctx.cr, "page") + research.steps(ctx.cr, "presence"):
        s = research.source(st.get("id") or st.get("source_id"))
        if s:
            out[research.source_code(s["url"])] = s
    return out


def qloo_codes(ctx: research.Ctx) -> tuple[dict, dict]:
    """(code → place, folded name → count) from this run's Qloo place searches; a name twice is a chain signal."""
    by_code, names = {}, {}
    for r in research.steps(ctx.cr, "qloo"):
        for p in r.get("places", []):
            by_code[p["code"]] = {**p, "listing": r["listing"]}
            names[_norm(p["name"])] = names.get(_norm(p["name"]), 0) + 1
    return by_code, names


def resolve(ctx: research.Ctx, item: dict, pages: dict, by_code: dict, names: dict, faults: list) -> dict | None:
    """A submitted candidate → a lead: from a Qloo code of the same name, or named on a page this run read."""
    q = by_code.get((item.get("qloo_ref") or "").strip("[] "))
    if q and not (research.same_name(q["name"], item["name"]) or _tight(q["name"]) == _tight(item["name"])):
        q = None
    named_on = [s for s in pages.values() if research.readable(s) and (research._in(item["name"], s["text"]) >= 0
                or _tight(item["name"]) in _tight(s["text"]))]  # "Martin Patrick 3" for "MartinPatrick 3" (LIVE L45-A)
    if not q and not named_on:
        faults.append({"field": "candidate", "reason": f"{item['name']}: not in a Qloo result or on a page read; dropped"})
        return None
    own = None
    for u in [item.get("own_site"), q and q.get("website")]:
        if u and any(fetch.same_site(s["final_url"], fetch.site_of(u)) for s in pages.values()):
            own = fetch.site_of(u)
            break
    name = q["name"] if q else item["name"]
    lead = {"name": name, "origin": "QLOO" if q else "PAGE", "qloo_id": q and q["qloo_id"],
            "address": q and q.get("address"), "listing": bool(q and q["listing"]), "own_site": own,
            "first_domain": fetch.site_of(named_on[0]["final_url"]) if named_on else None,
            "chain": names.get(_norm(name), 0) >= 2}
    lead["key"] = lead["lead_key"] = research.lead_key(lead, ctx.cid)
    return lead


def check_claims(ctx: research.Ctx, claims: list[dict], pages: dict, leads: dict, method: str) -> None:
    """In place: exact quote on a page read → city_check@4 (support only) → deterministic locality (placement too)."""
    for c in claims:
        s = pages.get((c.get("source") or "").strip())
        c["quote_found"] = bool(s and research.readable(s) and quote_found(c["quote"], s["text"]))
        c["source_id"] = s and s["id"]
        c["window"] = c["quote_found"] and research.window_of(c["quote"], s["text"])
        c["method"] = method
    todo = [c for c in claims if c["quote_found"]]
    verdicts = {}
    for b in range(0, len(todo), research.CHECK_BATCH):
        res = _judge(ctx, CHECKER, "Judge each claim against its quote:\n" + claim_listing(
            todo[b:b + research.CHECK_BATCH], research.WAY_IN), research.CityVerdicts, "check", str(b))
        verdicts.update({v["claim_id"]: v for v in res["verdicts"]})
    for c in claims:
        lead = leads.get(c["lead_key"])
        v = verdicts.get(c["cid"]) or {}
        c["support"] = v.get("verdict")
        c["reason"] = (v.get("reason") or "Not yet judged.") if c["quote_found"] else "Quote not found on a page Landfall read."
        c["state"] = claim_state(verdicts.get(c["cid"])) if c["quote_found"] else "TO_CHECK"
        c["shows_way_in"] = bool((verdicts.get(c["cid"]) or {}).get("shows_way_in"))
        c["locality"] = (claim_locality(c, ctx.inp["name"], lead, (lead or {}).get("location"), ctx.cid)
                         if c["quote_found"] else "TO_CHECK")


def place(ctx: research.Ctx, lead: dict) -> dict:
    """LeadLocation (CONTRACTS B6.5): Qloo's own coordinates, else one /search by name near the city (≤ 12 per run).
    PLACED needs exactly one name match within 15 miles; coordinates are never defaulted to the city point."""
    city = markets.CITIES[ctx.cid]
    base = {"lead_key": lead["key"], "city_id": ctx.cid, "at": db.now(), "options": []}
    q = next((p for r in research.steps(ctx.cr, "qloo") for p in r.get("places", []) if p["qloo_id"] == lead.get("qloo_id")), None)
    if q and q.get("lat") is not None:
        return {**base, "state": "PLACED", "lat": q["lat"], "lon": q["lon"], "address": q.get("address"),
                "source": "QLOO", "request_id": None, "reason": "Qloo's own place record"}
    params = {"query": lead["name"], "types": "urn:entity:place", "filter.location": f"{city['lat']},{city['lon']}",
              "filter.radius": 15, "take": 5}

    def go():
        try:
            out = qloo.get("/search", params, ctx.run.cassette, "specialist_places", ctx.pid, ctx.run.id)
        except BudgetExhausted as e:
            return {"error": f"not run: {e}"}
        if out.get("status") != 200:
            return {"error": f"HTTP {out.get('status')}"}
        ents, _ = qloo.rows(out)
        return {"request_id": ranking.request_id("/search", params), "status": 200,
                "results": [{"qloo_id": e.get("entity_id"), "name": e.get("name"), "lat": (e.get("location") or {}).get("lat"),
                             "lon": (e.get("location") or {}).get("lon"),
                             "address": (e.get("properties") or {}).get("address")} for e in ents]}
    res, refused = research.memo(ctx, "locate", _k(lead["key"]), go)
    if refused or not res or res.get("error"):
        return {**base, "state": "CHECK_FAILED", "lat": None, "lon": None, "address": None, "source": "QLOO_SEARCH",
                "request_id": None, "reason": f"Placement didn't run ({refused or (res or {}).get('error')}); Retry"}
    near = [r for r in res["results"] if r["lat"] is not None and _tight(r["name"]) == _tight(lead["name"])
            and km((city["lat"], city["lon"]), (r["lat"], r["lon"])) <= LOCATE_KM]
    out = {**base, "source": "QLOO_SEARCH", "request_id": res["request_id"], "lat": None, "lon": None, "address": None}
    if len(near) == 1:
        r = near[0]
        key = research.street_key(lead.get("address"))
        if key and r["address"] and not re.search(key, _norm(r["address"])):
            return {**out, "state": "SOURCES_DIFFER", "options": near, "address": r["address"],
                    "reason": "Qloo's address for this name differs from the one the lead's sources give"}
        return {**out, "state": "PLACED", "lat": r["lat"], "lon": r["lon"], "address": r["address"],
                "reason": "One place of this name within 15 miles, from Qloo's search"}
    if len(near) > 1:
        return {**out, "state": "AMBIGUOUS", "options": near, "reason": f"{len(near)} places of this name nearby; you choose"}
    return {**out, "state": "UNPLACED", "reason": "Qloo's search found no place of this name within 15 miles"}


def lead_facts(ctx: research.Ctx, lead: dict, pages: dict, claims: list[dict]) -> dict:
    """Identity (presence on pages), own-site text, contact route and locality for one lead. Placement sets locality
    only (P45-R3); a LeadLocation SOURCES_DIFFER is passed on to the readiness precedence."""
    own_pages = [s for s in pages.values() if lead["own_site"] and fetch.same_site(s["final_url"], lead["own_site"])]
    failed = next((s.get("error") for s in own_pages if not research.readable(s)), None)
    pres = research.presence_of_lead(lead, list(pages.values()), ctx.cid, failed)
    identity = pres["state"] if pres["state"] in ("VERIFIED", "SOURCES_DIFFER", "CHECK_FAILED") else "NOT_CHECKED"
    text = " ".join(s["text"] for s in own_pages if research.readable(s))
    contacts = [x for s in own_pages for x in json.loads(s.get("contacts") or "[]")]
    if not contacts and lead["own_site"] and (cp := read_contact_page(ctx, own_pages, lead["own_site"])):
        own_pages, contacts = [cp, *own_pages], json.loads(cp["contacts"])
    email = next((x for x in contacts if x["kind"] == "email"), None)
    phone = next((x for x in contacts if x["kind"] == "phone"), None)
    mine = [c for c in claims if c["lead_key"] == lead["key"]]
    supported = [c for c in mine if c["state"] == "SUPPORTED"]
    loc = lead.get("location") or {}
    local = ("LOCAL" if any(c["locality"] == "LOCAL" for c in supported) or identity == "VERIFIED" else "TO_CHECK")
    lead["presence"] = identity
    lead["presence_check"] = {"state": identity, "reason": pres["reason"], "source_ids": pres.get("source_ids", [])}
    own_src = next((s for s in own_pages if research.readable(s)), None)
    # an email first, else a phone (owner, 28 Oct 2026: "if we don't get an email address or phone, don't return it")
    reach = email or phone
    lead["contact"] = reach and own_src and {"kind": reach["kind"], "value": reach["value"], "source_id": own_src["id"]}
    return {"identity": identity, "identity_reason": pres["reason"], "own_pages": own_pages, "text": text,
            "email": email, "contact": "OWN_SITE" if email else "UNKNOWN", "local": local, "mine": mine,
            "supported": supported, "closed": bool(text and research.CLOSED.search(text)),
            "location_state": loc.get("state"), "own_src": next((s for s in own_pages if research.readable(s)), None)}


def decide(lead: dict, code: str | None, has_evidence: bool, prefs: dict, pending_code: str) -> tuple[str, str | None, bool]:
    """(decision, reason code, founder override). Exclude wins; include overrides any other rejection, shown."""
    excl = {_norm(x) for x in prefs["exclude"]}
    incl = {_norm(x) for x in prefs["include"]}
    if _norm(lead["name"]) in excl:
        return "REJECTED", "FOUNDER_EXCLUDED", False
    if code and _norm(lead["name"]) in incl:
        return "KEPT", code, True
    if code:
        return "REJECTED", code, False
    return ("KEPT", None, False) if has_evidence else ("PENDING", pending_code, False)


def model_rejections(sub: dict, candidates: list[dict], codes: set[str]) -> list[dict]:
    """The model's own rejections are kept as memory, never upgraded, and never override a checked candidate."""
    out = []
    for r in sub.get("rejected", []):
        code = (r.get("reason_code") or "").upper()
        if code in codes and not any(research.same_name(c["name"], r["name"]) for c in candidates):
            out.append({"ref": _norm(r["name"]), "name": r["name"], "decision": "REJECTED", "reason_code": code,
                        "reason": r.get("reason", ""), "override": False, "claim_ids": [], "by": "specialist",
                        "axes": None, "interpretation": code in ("SCALE_MISMATCH", "INACTIVE")})
    return out


def proximity(cid: str, rows: list[dict], anchor: dict | None = None) -> dict:
    """ProximityView (C): placed points around the city point; distances are straight-line logistics labels only."""
    city = markets.CITIES[cid]
    center = (city["lat"], city["lon"])
    a = anchor and anchor.get("lat") is not None and (anchor["lat"], anchor["lon"])
    placed, unplaced, dist = [], [], []
    for r in rows:
        loc = r.get("location") or {}
        if loc.get("state") == "PLACED":
            placed.append({"lead_key": r["lead_key"], "name": r["name"], "lat": loc["lat"], "lon": loc["lon"],
                           "address": loc.get("address"), "source": loc["source"]})
            d = km(a or center, (loc["lat"], loc["lon"]))
            dist.append({"lead_key": r["lead_key"], "km": round(d, 1), "label": distance_label(d),
                         "from": anchor["name"] if a else f"{city['name']} center"})
        else:
            unplaced.append({"lead_key": r["lead_key"], "name": r["name"], "state": loc.get("state") or "UNPLACED",
                             "reason": loc.get("reason"), "options": loc.get("options", [])})
    return {"city_id": cid, "center": {"lat": center[0], "lon": center[1]}, "placed": placed, "unplaced": unplaced,
            "anchor": anchor if a else None, "distances": dist,
            "caveat": "Being near each other is not evidence that the same people go to both."}


# ---- retail ----
REJECT_CODES = {"CHAIN", "CATEGORY_MISMATCH", "NOT_LOCAL", "CLOSED", "NO_ASSORTMENT_EVIDENCE", "FOUNDER_EXCLUDED"}


def validate_retail(ctx: research.Ctx, sub: dict, inp: dict) -> dict:
    """RetailDossierIn → the retail block, candidates and readiness. Deterministic except the memoized checker."""
    pages, (by_code, names), faults = pages_of(ctx), qloo_codes(ctx), []
    shops = []
    for sh in sub.get("shops", []):
        lead = resolve(ctx, sh, pages, by_code, names, faults)
        if lead and lead["key"] not in [x["key"] for x, _ in shops]:
            shops.append((lead, sh))
    leads = {lead["key"]: lead for lead, _ in shops}
    for lead in leads.values():
        lead["location"] = place(ctx, lead)
    claims = [{"cid": f"c{n}", "lead_key": lead["key"], "subject_name": lead["name"], **c}
              for n, (lead, c) in enumerate(((lead, c) for lead, sh in shops for c in sh.get("claims", [])), 1)]
    check_claims(ctx, claims, pages, leads, "retail")
    brand_cats = brand_categories(inp)
    candidates, rows = [], []
    for lead, sh in shops:
        f = lead_facts(ctx, lead, pages, claims)
        scan = assortment_scan(f["text"], lead["name"]) if f["text"] else {"brands": [], "carries_claim": None,
                                                                            "categories": [], "route": None}
        shop_cats = sorted(set(scan["categories"]) | {k for c in f["supported"] for k in categories_in(c["text"] + " " + c["quote"])})
        overlap = category_overlap(brand_cats, shop_cats)
        evidence = [{"brand_or_category": b["name"], "source_id": f["own_src"]["id"], "quote": b["quote"]} for b in scan["brands"]] \
            + [{"brand_or_category": "outside labels", "source_id": c["source_id"], "quote": c["quote"]}
               for c in f["supported"] if c["shows_way_in"]]
        route = "PUBLISHED_FORM" if scan["route"] else "EMAIL" if f["email"] else "UNKNOWN"
        contact = "OWN_SITE" if (f["email"] or scan["route"]) else "UNKNOWN"
        code = ("CHAIN" if lead["chain"] else "CLOSED" if f["closed"] else "CATEGORY_MISMATCH" if overlap == "NONE" else None)
        decision, code, override = decide(lead, code, bool(evidence), inp["founder_prefs"], "NO_ASSORTMENT_EVIDENCE")
        fit = ("BRAND_FACT_MATCH" if overlap == "MATCH" and f["supported"] and sells_established(inp)
               else "QLOO_LISTING" if lead["listing"] else "QLOO_RELATED" if lead["origin"] == "QLOO" and sells_established(inp)
               else "INTERPRETATION")
        method_ev = "SUPPORTED" if evidence and overlap in ("MATCH", "PARTIAL") else "TO_CHECK" if f["mine"] or evidence else "NONE"
        ax = axes(fit, f["identity"], f["local"], method_ev, contact, f["location_state"])
        if not sells_established(inp):
            ax["reasons"].append("What you sell isn't established")
            ax["overall"] = "HYPOTHESIS_LED"
        candidates.append({"ref": lead["key"], "name": lead["name"], "decision": decision, "reason_code": code,
                           "reason": f["identity_reason"] if decision == "KEPT" else code or "", "override": override,
                           "claim_ids": [c["cid"] for c in f["mine"]], "by": "specialist", "axes": ax})
        rows.append({"lead_key": lead["key"], "name": lead["name"], "axes": ax, "assortment_evidence": evidence,
                     "category_overlap": overlap, "shop_categories": shop_cats, "stockist_route": route,
                     "location": lead["location"], "identity_reason": f["identity_reason"], "decision": decision})
    candidates += model_rejections(sub, candidates, REJECT_CODES)
    prox = proximity(ctx.cid, rows)
    for r in rows:
        r["distance_km"] = next((d["km"] for d in prox["distances"] if d["lead_key"] == r["lead_key"]), None)
    block = {"partnership_hypothesis": sub.get("partnership_hypothesis") or "",
             "shortlist": [r for r in rows if r["decision"] in ("KEPT", "PENDING")][:3],
             "possible_activation": sub.get("possible_activation") or "",
             "commercial_questions": [{"text": q, "state": "UNKNOWN"} for q in COMMERCIAL]}
    return {"claims": claims, "candidates": candidates, "block": block, "faults": faults, "proximity": prox,
            "missing": [{"text": m, "tried": []} for m in sub.get("missing", [])],
            "concept": {"name": sub.get("concept_name") or "", "line": sub.get("concept_line") or ""},
            "leads": list(leads.values()), "questions": list(COMMERCIAL), "dependencies": [],
            "prose": {"retail.partnership_hypothesis": block["partnership_hypothesis"],
                      "retail.possible_activation": block["possible_activation"]},
            "next": ("Ask about wholesale vs consignment", "No shop we could check fits yet")}


# ---- popup_specialist@1 ----
POPUP_SYSTEM = """You are the Pop-up specialist on Landfall, an international launch team for small consumer brands. In ONE
city, find up to 3 venues or hosts where a short physical activation (a weekend pop-up, a market stall, a launch night) is
plausible, and outline what it would take.

Tools: qloo_places(method="popup") lists venues Qloo relates to the brand; web_search finds venue hire, private events
and pop-up spaces; fetch_page reads a venue's hire or events page; check_presence reads a venue's own site for an
address in this city.

Rules:
- Event-use evidence is a quote showing the venue offers space for hire or bookings, hosted a named event (with its
  date if given), or lists markets. A claim needs a page you read and an exact quote (5-40 words).
- Never claim availability, price, permission, footfall or "the right crowd". Capacity only when a page states it.
  A past event is not future availability. Being near other places is logistics, never cultural fit.
- Venues ruled out, and why, go in `rejected` (NO_EVENT_USE_EVIDENCE, NOT_LOCAL, CLOSED, SCALE_MISMATCH: a venue far
  too large or formal for a small brand's first activation, which is your interpretation).
- activation_outline (format, duration, footprint) is a proposal; it states no facts about a venue."""


class EventUseIn(research.Lenient):
    kind: str = Field("HIRE_OFFERED", description="HIRE_OFFERED, HOSTED_EVENT or MARKET_LISTING")
    text: str = Field(description="One short sentence: what the quote shows")
    source: str = Field(description="The SOURCE id of a page you read")
    quote: str = Field(description="Copied exactly from that page's text, 5-40 words")


class VenueIn(research.Lenient):
    name: str
    qloo_ref: str | None = None
    own_site: str | None = None
    event_use: list[EventUseIn] = Field(default_factory=list)


class OutlineIn(research.Lenient):
    format: str = ""
    duration: str = ""
    footprint: str = ""


class PopupDossierIn(research.Lenient):
    concept_name: str = ""
    concept_line: str = ""
    activation_outline: OutlineIn = Field(default_factory=OutlineIn)
    anchor: str | None = Field(None, description="A kept retail shop or a neighborhood the founder named, if any")
    venues: list[VenueIn] = Field(default_factory=list)
    rejected: list[RejectedIn] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


POPUP_CODES = {"NO_EVENT_USE_EVIDENCE", "NOT_LOCAL", "CLOSED", "SCALE_MISMATCH", "FOUNDER_EXCLUDED"}
EVENT_WORDS = re.compile(r"\b(hire|for hire|book(?:ing|ings)?|private (?:events?|functions?|hire)|venue|rent(?:al)?|"
                         r"host(?:s|ed|ing)?|events?|pop-?ups?|markets?|stalls?|launch(?:es)?|exhibitions?)\b", re.I)
CAPACITY = re.compile(r"\b(\d{2,5})\s*(?:guests|people|standing|seated|capacity|pax|persons)\b|"
                      r"\bcapacity\s*(?:of|:)?\s*(\d{2,5})\b", re.I)
REQUIREMENTS = ("AVAILABILITY", "CAPACITY", "PERMITS", "INSURANCE", "STAFFING", "POWER_FIXTURES", "LOAD_IN", "STOCK_IMPORT")
POPUP_COSTING = ("HIRE_FEE", "DEPOSIT", "STAFF", "FIXTURE", "SHIPPING", "PERMIT")


def validate_popup(ctx: research.Ctx, sub: dict, inp: dict) -> dict:
    pages, (by_code, names), faults = pages_of(ctx), qloo_codes(ctx), []
    venues = []
    for v in sub.get("venues", []):
        lead = resolve(ctx, v, pages, by_code, names, faults)
        if lead and lead["key"] not in [x["key"] for x, _ in venues]:
            venues.append((lead, v))
    leads = {lead["key"]: lead for lead, _ in venues}
    for lead in leads.values():
        lead["location"] = place(ctx, lead)
    claims = [{"cid": f"c{n}", "lead_key": lead["key"], "subject_name": lead["name"],
               "kind": (e.get("kind") or "HIRE_OFFERED").upper(), **{k: e[k] for k in ("text", "source", "quote")}}
              for n, (lead, e) in enumerate(((lead, e) for lead, v in venues for e in v.get("event_use", [])), 1)]
    check_claims(ctx, claims, pages, leads, "popup")
    candidates, rows = [], []
    foreign = (ranking.home_for(ctx.pid) or {}).get("country") not in (None, ctx.inp["country"])
    for lead, v in venues:
        f = lead_facts(ctx, lead, pages, claims)
        uses = [{"kind": c["kind"] if c["kind"] in ("HIRE_OFFERED", "HOSTED_EVENT", "MARKET_LISTING") else "HOSTED_EVENT",
                 "event_dates": event_dates(c["quote"], ctx.cid), "source_id": c["source_id"], "quote": c["quote"],
                 "claim_id": c["cid"]}
                for c in f["supported"] if c["shows_way_in"] and EVENT_WORDS.search(c["quote"])]
        cap = next(((m, c) for c in f["supported"] for m in [CAPACITY.search(c["quote"])] if m), None)
        capacity = cap and {"value": int(cap[0].group(1) or cap[0].group(2)), "source_id": cap[1]["source_id"],
                            "quote": cap[1]["quote"]}
        code = "CLOSED" if f["closed"] else None
        decision, code, override = decide(lead, code, bool(uses), inp["founder_prefs"], "NO_EVENT_USE_EVIDENCE")
        fit = ("QLOO_LISTING" if lead["listing"] else "QLOO_RELATED" if lead["origin"] == "QLOO" and sells_established(inp)
               else "INTERPRETATION")
        ax = axes(fit, f["identity"], f["local"], "SUPPORTED" if uses else "TO_CHECK" if f["mine"] else "NONE",
                  f["contact"], f["location_state"])
        candidates.append({"ref": lead["key"], "name": lead["name"], "decision": decision, "reason_code": code,
                           "reason": f["identity_reason"] if decision == "KEPT" else code or "", "override": override,
                           "claim_ids": [c["cid"] for c in f["mine"]], "by": "specialist", "axes": ax})
        rows.append({"lead_key": lead["key"], "name": lead["name"], "axes": ax, "location": lead["location"],
                     "event_use": uses, "capacity": capacity, "inquiry_route": "EMAIL" if f["email"] else "UNKNOWN",
                     "decision": decision, "identity_reason": f["identity_reason"]})
    candidates += model_rejections(sub, candidates, POPUP_CODES)
    anchor_name = (sub.get("anchor") or "").strip()
    anchor = None
    if anchor_name:  # a kept retail shop of this city, placed: never a guessed point
        retail = db.get_record(f"dos_{ctx.pid}_{ctx.cid}_retail")
        hit = next((r for r in ((retail or {}).get("retail") or {}).get("shortlist", [])
                    if research.same_name(r["name"], anchor_name) and (r.get("location") or {}).get("state") == "PLACED"), None)
        anchor = hit and {"name": hit["name"], "lat": hit["location"]["lat"], "lon": hit["location"]["lon"]}
    prox = proximity(ctx.cid, rows, anchor)
    kept = [r for r in rows if r["decision"] == "KEPT"]
    reqs = [{"item": i, "state": "STATED" if i == "CAPACITY" and any(r["capacity"] for r in kept)
             else "TO_CHECK" if i == "PERMITS" or (i == "STOCK_IMPORT" and foreign) else "UNKNOWN"} for i in REQUIREMENTS]
    outline = sub.get("activation_outline") or {}
    block = {"activation_outline": {k: outline.get(k) or "" for k in ("format", "duration", "footprint")},
             "anchor": anchor, "venues": [r for r in rows if r["decision"] in ("KEPT", "PENDING")][:3],
             "requirements": reqs, "costing_inputs_needed": list(POPUP_COSTING)}
    return {"claims": claims, "candidates": candidates, "block": block, "faults": faults, "proximity": prox,
            "missing": [{"text": m, "tried": []} for m in sub.get("missing", [])],
            "concept": {"name": sub.get("concept_name") or "", "line": sub.get("concept_line") or ""},
            "leads": list(leads.values()), "dependencies": [{"text": r["item"].replace("_", " ").lower(), "state": r["state"]}
                                                            for r in reqs],
            "questions": [f"{r['item'].replace('_', ' ').capitalize()}?" for r in reqs if r["state"] != "STATED"],
            "prose": {f"popup.activation_outline.{k}": block["activation_outline"][k] for k in ("format", "duration", "footprint")},
            "next": ("Ask the venue about dates and hire terms", "No venue we could check fits yet")}


# ---- the Qloo city target (community and creator; owner, 27 Oct 2026: "show Qloo has impact to each recommendation") ----
# Qloo decides who (the brand's audiences), where (the heatmap's cells, named by the neighborhoods Qloo files its own
# places under) and which venues host them (places signaled by each audience's URN); web search only fills in names
# inside that target. Every value shown is Qloo's, ordered only inside the request it came from (QLOO-CAPABILITIES §2).
QLOO_TARGETED = ("community", "creator")
TARGET_VERSION = "qloo_target@1"
TARGET_TYPES = ("Communities", "Hobbies And Interests")  # `filter.parents.types` is ignored on urn:audience (CQ-A)
TARGET_AUDIENCES, MAP_CELLS, NAME_CELLS, NAME_KM, SHOW_VENUES = 3, 40, 12, 1.5, 5
NOT_DEMAND = "a cultural relationship, not demand"
THEME_SKIP = ("urn:tag:genre:media", "urn:tag:genre:person")
# not searchable as a community or a creator niche ("Clean Lines", "Comfort", "Classic", "Bar"): shown, never searched
QUERY_SKIP = ("urn:tag:genre:place", "urn:tag:aesthetic_property", "urn:tag:emotional_tone", "urn:tag:personal_style",
              "urn:tag:industry")
MAX_LEADS = 8


def city_target(run: Run, pid: str, cid: str) -> dict:
    """QlooTarget for (project, city), shared by the community and creator investigations: reused while its signal and
    audiences are unchanged and it is under a week old, else about 8 requests (audience URNs, 1 heatmap, 1 place search
    per audience, 1 taste analysis), each through `qloo.get` (ledger, pacer, cache). A failed part is failed, never
    absent. Returns the record's {id, version}."""
    rid, city = f"qt_{pid}_{cid}", markets.CITIES[cid]
    sig = ranking.ranking_signal(pid)
    ap = db.latest("audience_profile", pid)
    same = bool(ap and sig) and ranking.signal_key(ap["signal"]) == ranking.signal_key(sig)
    live = [a for a in ap["audiences"] if a["uuid"] not in ap["removed"]] if same else []
    picked = ([a for a in live if a.get("type") in TARGET_TYPES] or live)[:TARGET_AUDIENCES]
    key = {"version": TARGET_VERSION, "signal": ranking.signal_key(sig), "audiences": [a["uuid"] for a in picked]}
    old = db.get_record(rid)
    week = (datetime.now(timezone.utc) - timedelta(days=qloo.CACHE_DAYS)).isoformat(timespec="seconds")
    if old and old.get("key") == key and old.get("state") == "ok" and old["created_at"] > week:
        return {"id": old["id"], "version": old["version"]}
    failed: list[str] = []
    point = {"lat": city["lat"], "lon": city["lon"]}

    def ask(what: str, params: dict) -> dict:
        try:
            out = qloo.get("/v2/insights", params, run.cassette, "specialist_qloo", pid, run.id)
        except (BudgetExhausted, NoRecording) as e:
            out = {"status": 0, "error": str(e)}
        t["requests"].append({"what": what, "request_id": ranking.request_id("/v2/insights", params), "status": out.get("status")})
        if out.get("status") != 200:
            failed.append(f"{what}: {out.get('error') or 'HTTP ' + str(out.get('status'))}")
        return out

    t = {"key": key, "city_id": cid, "city": city["name"], "label": (sig or {}).get("label"),
         "brand": ((sig or {}).get("names") or [None])[0], "center": point, "caveat": NOT_DEMAND, "audiences": [],
         "cells": [], "cell_count": 0, "neighbourhoods": [], "themes": [], "requests": [], "failed": failed}
    if not sig:
        return _put_target(run, t, "no_signal")
    ents = ",".join(sig["entity_ids"])
    cells = qloo.heatmap_cells(ask("heatmap", {"filter.type": "urn:heatmap", "filter.location.query": city["name"],
                                               "signal.interests.entities": ents}))
    try:
        urns = ranking.resolve_urns(pid, run.cassette, run.id, [a["uuid"] for a in picked], stage="specialist_qloo") if picked else {}
    except NoRecording as e:
        urns = {}
        failed.append(f"audience ids: {e}")
    for k, a in enumerate(picked, 1):
        urn = (urns.get(a["uuid"]) or {}).get("urn")
        row = {"uuid": a["uuid"], "name": a["name"], "type": a.get("type"), "rank": a["rank_in_request"],
               "of": len(ap["audiences"]), "urn": urn, "venues": [], "venue_state": "failed" if urn else "no_urn", "flat": False}
        if urn:  # R5: the URN, never the UUID (a UUID is silently ignored)
            rows = ranking._ranking_rows(ask(f"places for {a['name']}", research.place_params(point, None, urn)))
            if rows is not None:
                row["venues"] = [{**qloo.place_row(e, i), "code": f"Q{k}-{i}", "of": len(rows)} for i, e in enumerate(rows, 1)]
                row["venue_state"] = "returned" if rows else "empty"
                row["flat"] = len(rows) >= 3 and len({e["query"]["affinity"] for e in rows}) < 2
        t["audiences"].append(row)
    out = ask("themes", {"filter.type": "urn:tag", "signal.interests.entities": ents,
                         "signal.location": f"POINT({city['lon']} {city['lat']})", "take": 20})
    seen: set = set()
    for i, tg in enumerate(qloo.rows(out)[1], 1):
        name, sub = (tg.get("name") or "").strip(), tg.get("subtype") or tg.get("type") or ""
        if name and name.casefold() not in seen and sub not in THEME_SKIP:
            seen.add(name.casefold())
            t["themes"].append({"name": name, "rank": i, "subtype": sub})
    t["themes"] = t["themes"][:8]
    if cells:  # each top cell takes the neighborhood of the nearest Qloo place within 1.5 km; no other geocoder
        named = [v for a in t["audiences"] for v in a["venues"] if v["lat"] is not None and v["hood"]]
        hoods: dict = {}
        for r, c in enumerate(cells[:NAME_CELLS], 1):
            near = min(named, key=lambda v: km((c["lat"], c["lon"]), (v["lat"], v["lon"])), default=None)
            if near and km((c["lat"], c["lon"]), (near["lat"], near["lon"])) <= NAME_KM:
                c["hood"] = near["hood"]
                hoods.setdefault(c["hood"], {"name": c["hood"], "best_rank": r, "cells": 0})["cells"] += 1
        t["neighbourhoods"] = list(hoods.values())[:3]
        t["cells"] = [{**c, "rank": r} for r, c in enumerate(cells[:MAP_CELLS], 1)]
        t["cell_count"] = len(cells)
    usable = cells or any(a["venues"] for a in t["audiences"]) or t["themes"]
    return _put_target(run, t, "ok" if not failed else "partial" if usable else "failed")


def _put_target(run: Run, t: dict, state: str) -> dict:
    rec = db.put_record("qloo_target", run.project_id, {**t, "state": state}, rid=f"qt_{run.project_id}_{t['city_id']}")
    research._activity(run, f"Ask Qloo who and where in {t['city']}", "Qloo audiences, heatmap, places, taste",
                       f"{len(t['audiences'])} audiences · {t['cell_count']} heatmap cells · "
                       f"{sum(len(a['venues']) for a in t['audiences'])} venues · {len(t['themes'])} themes"
                       + (f" · {len(t['failed'])} failed" if t["failed"] else ""),
                       {"ok": "DONE", "partial": "PARTIAL"}.get(state, "FAILED"))
    return {"id": rec["id"], "version": rec["version"]}


def target_of(inp: dict) -> dict | None:
    ref = inp.get("qloo_target")
    t = ref and db.get_record(ref["id"], ref["version"])
    return t if t and t.get("state") in ("ok", "partial") else None


def target_queries(t: dict, method: str, cats: list[str], city: str) -> list[str]:
    """Searches anchored to the Qloo target: each audience × a neighborhood, Qloo's local themes, a venue Qloo relates
    to one audience only (a stadium related to every audience says little), and what the brand sells when known."""
    hoods = [n["name"] for n in t["neighbourhoods"]] or [city]
    themes = [x["name"] for x in t["themes"] if not x["subtype"].startswith(QUERY_SKIP)]
    out = []
    for i, a in enumerate(t["audiences"]):
        an = a["name"].lower()
        out.append(f"{an} club, group or meetup in {hoods[i % len(hoods)]}" if method == "community"
                   else f"{an} content creator or blogger")
    if method == "community":
        out += [f"{themes[0]} event organizer in {hoods[0]}"] if themes else []
        out += [f"{themes[1]} community or collective in {hoods[1 % len(hoods)]}"] if len(themes) > 1 else []
        names = [v["name"] for a in t["audiences"] if not a["flat"] for v in a["venues"][:SHOW_VENUES]]
        venue = next((n for n in names if names.count(n) == 1), names[0] if names else None)
        out += [f"groups or recurring events at {venue}"] if venue else []
    else:
        out += [f"{themes[0]} content creator"] if themes else []
        out += [f"{cats[0]} creator or blogger"] if cats else []
        out.append(f"creators based in {hoods[0]}")
    return list(dict.fromkeys(" ".join(q.split()) for q in out))[:6]


def target_prompt(inp: dict) -> str:
    """The QLOO TARGET section of a community or creator prompt; "" for every other method (their prompts unchanged)."""
    t = target_of(inp)
    if not t:
        return ""
    city = inp["research_input"]["name"]
    out = [f"QLOO TARGET for {city} (Qloo's cultural relationships for {t['label']}; not demand, not audience size):"]
    if t["audiences"]:
        out.append("- Audiences Qloo relates to the brand, most related first: " + ", ".join(a["name"] for a in t["audiences"]))
    if t["neighbourhoods"]:
        out.append(f"- Where in {city} the brand's taste concentrates (Qloo heatmap): " + ", ".join(n["name"] for n in t["neighbourhoods"]))
    for a in t["audiences"]:
        if a["venues"]:
            out.append(f"- Places Qloo relates to the {a['name']} audience near {city}" + (" (a listing, no taste order)" if a["flat"] else "")
                       + ":\n" + "\n".join(f"  [{v['code']}] {v['name']}" + "".join(f" · {x}" for x in (v["hood"], v["address"], v["website"]) if x)
                                           for v in a["venues"][:SHOW_VENUES]))
    if t["themes"]:
        out.append("- Local themes Qloo relates to the brand here: " + ", ".join(x["name"] for x in t["themes"]))
    qs = target_queries(t, inp["method"], brand_categories(inp), city)
    if qs:
        out.append("SEARCHES TO RUN FIRST (built from the Qloo target; reword if you like, but keep the audience or theme "
                   "and the place):\n" + "\n".join(f"- {q}" for q in qs))
    return "\n".join(out) + "\n\n"


def target_view(t: dict | None) -> dict | None:
    """What the detail page draws: audiences with their top venues, named neighborhoods, the map cells and themes."""
    if not t:
        return None
    return {**{k: t[k] for k in ("id", "version", "state", "city", "brand", "label", "center", "caveat", "cells",
                                 "cell_count", "neighbourhoods", "themes", "failed")},
            "audiences": [{**{k: a[k] for k in ("name", "type", "rank", "of", "urn", "venue_state", "flat")},
                           "venues": a["venues"][:SHOW_VENUES]} for a in t["audiences"]]}


def target_venue(t: dict | None, qloo_id: str | None) -> tuple[dict, dict] | tuple[None, None]:
    for a in (t or {}).get("audiences", []):
        for v in a["venues"]:
            if qloo_id and v["qloo_id"] == qloo_id:
                return a, v
    return None, None


def place_or_target(ctx: research.Ctx, lead: dict, t: dict | None) -> dict:
    """A lead from a Qloo target venue is placed by Qloo's own record; any other goes through `place`."""
    _, v = target_venue(t, lead.get("qloo_id"))
    if v and v["lat"] is not None:
        return {"lead_key": lead["key"], "city_id": ctx.cid, "at": db.now(), "options": [], "state": "PLACED",
                "lat": v["lat"], "lon": v["lon"], "address": v["address"], "source": "QLOO", "request_id": None,
                "reason": "Qloo's own place record"}
    return place(ctx, lead)


# ---- leads: named by a search or a page this run read, not yet confirmed; shown, never evidence ----
UNREAD = re.compile(r"unavailable|could ?n[o'’]?t (?:be )?(?:read|load|reach|access|open|fetch)|not (?:readable|reachable|"
                    r"accessible|read|loaded)|blocked|timed? ?out|robots|\b40[34]\b|\b5\d\d\b|no readable text|"
                    r"social (?:platform|profile)|never read", re.I)


_json_list = research._json_list  # moved to research.Lenient (every submission gets it)


class JsonLists(research.Lenient):
    """A list sent as a JSON string is read as the list (LIVE creator @3, 27 Oct 2026: `creators: "[{...}]"` failed
    validation until the model limit ended the run)."""
    @model_validator(mode="before")
    @classmethod
    def _json_lists(cls, data):
        if isinstance(data, dict):
            data = {k: _json_list(v, cls.model_fields[k].annotation) if k in cls.model_fields else v for k, v in data.items()}
        return data


class LeadNameIn(research.Lenient):
    name: str
    url: str | None = Field(None, description="The page or profile address that named it, if any")


CONTACT_PATHS = ("contact", "contacto", "contact-us", "contactanos")


def read_contact_page(ctx: research.Ctx, own_pages: list[dict], site: str) -> dict | None:
    """A venue's home page often has no email or phone; its contact page usually does. The first readable one with a
    contact wins. ponytail: guessed paths, not links read off the home page; add link-following if these miss often."""
    from urllib.parse import urljoin
    base = next((s["final_url"] for s in own_pages if s.get("final_url")), None) or (site if "://" in site else f"https://{site}")
    for path in CONTACT_PATHS:
        try:
            url = fetch.check_url(urljoin(base, f"/{path}"))
        except fetch.FetchBlocked:
            return None
        res, refused = research.memo(ctx, "page", url, lambda url=url: {
            **{k: v for k, v in research.load_page(ctx.run, ctx.pid, ctx.cid, url, f"{ctx.cr}:page:{url}",
                                                   stage=f"{ctx.stage}_fetch").items()
               if k in ("id", "url", "final_url", "status", "error", "reused")}, "method": "contact_page"})
        if refused:
            return None
        s = res and research.source(res["id"])
        if s and research.readable(s) and json.loads(s.get("contacts") or "[]"):
            return s
    return None


def read_before_drop(ctx: research.Ctx, url: str | None) -> bool:
    """P1 (owner, 27 Oct 2026): a named group's own page is read before it can be dropped (counted as a page read;
    social platforms are never read, so a group known only there stays a LEAD)."""
    if not url or research.is_social(url):
        return False
    try:
        url = fetch.check_url(url)
    except fetch.FetchBlocked:
        return False
    res, refused = research.memo(ctx, "page", url, lambda: {
        **{k: v for k, v in research.load_page(ctx.run, ctx.pid, ctx.cid, url, f"{ctx.cr}:page:{url}",
                                               stage=f"{ctx.stage}_fetch").items()
           if k in ("id", "url", "final_url", "status", "error", "reused")}, "method": "read_before_drop"})
    return bool(res and not refused and research.readable(research.source(res["id"]) or {}))


def _named(name: str, text: str) -> bool:
    return research._in(name, text) >= 0 or (len(_tight(name)) >= 4 and _tight(name) in _tight(text))


def named_in_run(ctx: research.Ctx, name: str, pages: dict) -> str | None:
    """Where this run saw a name: on a page it read, else in a search answer or citation; None when nowhere."""
    for s in pages.values():
        if research.readable(s) and _named(name, s["text"]):
            return f"named on {fetch.site_of(s['final_url'])}"
    for st in research.steps(ctx.cr, "search"):
        if _named(name, (st.get("answer") or "") + " " + " ".join(c["url"] for c in st.get("citations", []))):
            return "named in search"
    return None


def seen_urls(ctx: research.Ctx, pages: dict) -> set[str]:
    return ({c["url"] for st in research.steps(ctx.cr, "search") for c in st.get("citations", [])}
            | {u for s in pages.values() for u in (s["url"], s["final_url"])})


def anchors_of(ctx: research.Ctx, t: dict | None, name: str, pages: dict) -> tuple[str | None, str | None]:
    """(Qloo anchor, neighborhood) of the anchored search that surfaced a name, by provenance, never by the model's
    say-so: the first search whose answer names it, or that cited a page naming it, and whose query carries a target
    audience ("Racing audience") or theme ("rock theme") and/or a target neighborhood."""
    if not t:
        return None, None
    on = {u for s in pages.values() if research.readable(s) and _named(name, s["text"]) for u in (s["url"], s["final_url"])}
    words = lambda x, q: re.search(rf"(?<!\w){re.escape(x.casefold())}(?!\w)", q) is not None  # noqa: E731
    for st in research.steps(ctx.cr, "search"):
        q = (st.get("query") or "").split(" — in ")[0].casefold()
        if not (_named(name, st.get("answer") or "") or on & {c["url"] for c in st.get("citations", [])}):
            continue
        a = next((f"{x['name']} audience" for x in t["audiences"] if words(x["name"], q)), None) or \
            next((f"{x['name']} theme" for x in t["themes"] if words(x["name"], q)), None)
        n = next((x["name"] for x in t["neighbourhoods"] if words(x["name"], q)), None)
        if a or n:
            return a, n
    return None, None


def why_lead(a: str | None, n: str | None, name: str) -> str | None:
    return f"{' × '.join(x for x in (a, n) if x)} → {name}" if a or n else None


GENERIC = set("group groups club clubs meetup meetups community communities collective lovers fans enthusiasts events event "
              "organizer organizers organizer organizers local scene and of the in creators creator bloggers blogger".split())


def generic(name: str, t: dict | None, city: str) -> bool:
    """"Fishing groups", "Toronto watches club": a category made of the target's own words, not a named group or person."""
    words = GENERIC | set(re.findall(r"\w+", markets.fold(" ".join(
        [city] + [x["name"] for k in ("audiences", "themes", "neighbourhoods") for x in (t or {}).get(k, [])]))))
    return all(w in words for w in re.findall(r"\w+", markets.fold(name)))


def lead_rows(ctx: research.Ctx, t: dict | None, raw: list[dict], pages: dict, taken: list[str], faults: list) -> list[dict]:
    """LEADs, each traced to the search or page in this run that named it; a name seen nowhere is left out (never
    fabricated). Its Qloo anchor is the anchored search that surfaced it (`anchors_of`)."""
    urls, out = seen_urls(ctx, pages), []
    for x in raw:
        name = (x.get("name") or "").strip()
        if not name or any(research.same_name(name, n) for n in taken + [o["name"] for o in out]):
            continue
        if generic(name, t, ctx.inp["name"]):
            faults.append({"field": "lead", "reason": f"{name}: a category, not a named group or person; left out"})
            continue
        found = named_in_run(ctx, name, pages)
        if not found:
            faults.append({"field": "lead", "reason": f"{name}: not named by a search or a page this run read; left out"})
            continue
        au, v = next(((au, v) for au in (t or {}).get("audiences", []) for v in au["venues"] if research.same_name(v["name"], name)),
                     (None, None))  # a Qloo venue named as a lead keeps Qloo's own audience and neighborhood
        a, n = (f"{au['name']} audience", v["hood"]) if v else anchors_of(ctx, t, name, pages)
        out.append({"name": name, "state": "LEAD", "found": found, "url": x.get("url") if x.get("url") in urls else None,
                    "reason": x.get("reason") or "not yet confirmed", "anchor": a, "neighbourhood": n,
                    "why_qloo": why_lead(a, n, name)})
    # ponytail: the first MAX_LEADS, audience-anchored first; a listing page can name dozens
    return sorted(out, key=lambda x: (not (x["anchor"] or "").endswith(" audience"), x["why_qloo"] is None))[:MAX_LEADS]


def no_qloo_name(sub: dict) -> dict:
    """A concept is the founder's, not a data source's (LIVE community @3: "Qloo Coffee Community Engagement")."""
    return {**sub, **{k: re.sub(r"\s{2,}", " ", re.sub(r"\bQloo(?:'s)?\b", "", sub.get(k) or "")).strip()
                      for k in ("concept_name", "concept_line")}}


def split_rejections(sub: dict, codes: set[str]) -> tuple[dict, list[dict]]:
    """A rejection for an evidence reason stays a rejection; one because a page couldn't be read (unknown, not absent)
    or with no recognized code becomes a LEAD to confirm."""
    keep, leads = [], []
    for r in sub.get("rejected", []):
        if (r.get("reason_code") or "").upper() in codes and not UNREAD.search(r.get("reason") or ""):
            keep.append(r)
        else:
            leads.append({"name": r.get("name"), "reason": "its page couldn't be read: unknown, not absent"})
    return {**sub, "rejected": keep}, leads


def qloo_why(inp: dict, t: dict | None, rows: list[dict], leads: list[dict]) -> list[dict]:
    """The "Why: Qloo" lines, most specific first (a partner, a venue, a lead, then where, who, themes and the city),
    each from Qloo's own values and phrased as a relationship, never demand."""
    if not t:
        return []
    city, brand = t["city"], t["brand"] or "your brand"
    Q = lambda about, text, subject=None: {"about": about, "subject": subject, "text": text, "refs": ["QLOO"]}  # noqa: E731
    out = [Q(r.get("kind", "partner"), r["why_qloo"], r["name"]) for r in rows if r.get("why_qloo")]
    for r in rows:
        a, v = target_venue(t, r.get("qloo_id"))
        if v and not a["flat"]:
            out.append(Q("venue", f"{v['name']}: #{v['rank']} of {v['of']} places Qloo relates to the {a['name']} audience "
                                  f"near {city}", v["name"]))
    out += [Q("lead", f"{x['why_qloo']} (a lead to confirm)", x["name"]) for x in leads if x["why_qloo"]]
    if t["neighbourhoods"]:
        out.append(Q("neighbourhood", f"{brand}'s taste concentrates in {ranking.join([n['name'] for n in t['neighbourhoods']])} "
                                      f"(Qloo heatmap, the top {min(NAME_CELLS, t['cell_count'])} of {t['cell_count']} cells)"))
    if t["audiences"]:
        out.append(Q("audiences", f"Qloo relates people who like {brand} to " + ranking.join(
            [f"{a['name']} (#{a['rank']} of {a['of']})" for a in t["audiences"]])))
    if t["themes"]:
        out.append(Q("themes", f"Local themes Qloo relates to {brand} in {city}: " + ", ".join(x["name"] for x in t["themes"][:4])))
    if pos := ((inp["research_input"].get("recommendation") or {}).get("position_line")):
        out.append(Q("city", pos))
    return out


# ---- community_specialist@3 (@3: Qloo-anchored, read before dropping, unconfirmed names kept as LEADs) ----
COMMUNITY_SYSTEM = """You are the Community specialist on Landfall, an international launch team for small consumer brands. In
ONE city, find up to 3 local groups or organizers with recurring activities relevant to the brand (clubs, meetups, cause
groups), and propose how the brand could take part.

Qloo decides the target; you find the names. The brief's QLOO TARGET lists the audiences Qloo relates to the brand,
where in the city its taste concentrates, the venues Qloo relates to each audience, and local themes. Run its searches
first, and look for groups that serve those audiences, in those neighborhoods or at those venues.

Tools: web_search finds organizers; fetch_page reads an organizer's own site or a public page about it (a Meetup or
event listing, a news article); check_presence reads a group's own site for an address in this city;
qloo_places(method="community") lists more venues (gyms, parks, clubs). A venue is a host, not an organizer.

Rules:
- A group must be shown to exist, to operate in this city, and to run a relevant activity, each by a quote (5-40 words)
  from a page you read. Copy schedules exactly ("every Saturday 6am", "first Thursday of each month") and dates with
  their year.
- Read before you drop: when a search names a group, fetch its own site or a public page about it before deciding. A
  page you couldn't read is unknown, not a reason to reject; social platforms are never read.
- Every group a search or page named that you couldn't confirm goes in `leads` (its name and the address that named
  it). Never invent a name.
- An organizer and the venue it meets at are separate: give each group's `role` (ORGANISER, VENUE or BOTH) and name its
  venue in `venue` when known; a QLOO TARGET venue is cited by its code in `qloo_ref`.
- Never claim willingness to promote, member counts (unless a dated quote states them) or audience traits a quote
  does not state ("environmentally aware members" is not allowed unless quoted).
- `rejected` is for evidence reasons only (VENUE_ONLY, NO_RECURRING_ACTIVITY, INACTIVE, NOT_LOCAL).
- concept and participation_assumptions are proposals and assumptions; they state no facts about a group."""


class ActivityIn(research.Lenient):
    text: str = Field(description="One short sentence: the activity the quote shows")
    source: str
    quote: str = Field(description="Copied exactly, 5-40 words; include the schedule or date wording")


class GroupIn(research.Lenient):
    name: str
    qloo_ref: str | None = None
    own_site: str | None = None
    role: str = Field("ORGANISER", description="ORGANISER, VENUE or BOTH")
    venue: str | None = Field(None, description="The venue it meets at, if a page says")
    activities: list[ActivityIn] = Field(default_factory=list)


class CommunityDossierIn(JsonLists):
    concept_name: str = ""
    concept_line: str = ""
    concept: str = Field("", description="How the brand could take part (a proposal)")
    groups: list[GroupIn] = Field(default_factory=list)
    leads: list[LeadNameIn] = Field(default_factory=list, description="Named by a search or page, not yet confirmed")
    participation_assumptions: list[str] = Field(default_factory=list)
    inquiry_questions: list[str] = Field(default_factory=list)
    rejected: list[RejectedIn] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


COMMUNITY_CODES = {"VENUE_ONLY", "NO_RECURRING_ACTIVITY", "INACTIVE", "NOT_LOCAL", "CLOSED", "FOUNDER_EXCLUDED"}


def target_tags(ctx: research.Ctx, t: dict | None, lead: dict, pages: dict) -> tuple[str | None, str | None]:
    """(Qloo anchor, neighborhood) for a candidate: a Qloo venue carries its own audience and neighborhood; any
    other takes the anchored search that surfaced it."""
    a, v = target_venue(t, lead.get("qloo_id"))
    return (f"{a['name']} audience", v["hood"]) if v else anchors_of(ctx, t, lead["name"], pages)


def validate_community(ctx: research.Ctx, sub: dict, inp: dict) -> dict:
    pages, (by_code, names), faults = pages_of(ctx), qloo_codes(ctx), []
    t = target_of(inp)
    by_code.update({v["code"]: {**v, "listing": a["flat"]} for a in (t or {}).get("audiences", []) for v in a["venues"]})
    sub, unread = split_rejections(sub, COMMUNITY_CODES)
    groups, dropped = [], []
    for g in sub.get("groups", []):
        lead = resolve(ctx, g, pages, by_code, names, faults)
        if not lead and read_before_drop(ctx, g.get("own_site")):  # read it before it can be dropped
            faults.pop()
            pages = pages_of(ctx)
            lead = resolve(ctx, g, pages, by_code, names, faults)
        if not lead:
            faults.pop()  # not dropped: kept as a LEAD below when this run saw its name
            dropped.append({**g, "url": g.get("own_site")})
        elif lead["key"] not in [x["key"] for x, _ in groups]:
            role = (g.get("role") or "ORGANISER").upper()
            lead["role"] = role if role in ("ORGANISER", "VENUE", "BOTH") else "ORGANISER"
            groups.append((lead, g))
    leads = {lead["key"]: lead for lead, _ in groups}
    for lead in leads.values():
        lead["location"] = place_or_target(ctx, lead, t)
    claims = [{"cid": f"c{n}", "lead_key": lead["key"], "subject_name": lead["name"], **{k: a[k] for k in ("text", "source", "quote")}}
              for n, (lead, a) in enumerate(((lead, a) for lead, g in groups for a in g.get("activities", [])), 1)]
    check_claims(ctx, claims, pages, leads, "community")
    now = datetime.now(timezone.utc)
    candidates, rows = [], []
    for lead, g in groups:
        f = lead_facts(ctx, lead, pages, claims)
        acts = []
        for c in f["supported"]:
            if not c["shows_way_in"]:
                continue
            src = research.source(c["source_id"])
            dates = event_dates(c["quote"], ctx.cid)
            tm = activity_timing(c["quote"], dates, ctx.cid, now)
            acts.append({"text": c["text"], "recurrence": tm["recurrence"], "schedule_quote": tm["schedule_quote"],
                         "event_dates": dates, "source_id": c["source_id"], "quote": c["quote"], "claim_id": c["cid"],
                         "fetched_at": src and src["fetched_at"], "recurrence_inputs": True})
        venue = next((x for x, _ in groups if g.get("venue") and research.same_name(x["name"], g["venue"])), None)
        regular = [a for a in acts if a["recurrence"] in ("RECURRING", "REPEATED_BEFORE")]
        code = ("CLOSED" if f["closed"] else "VENUE_ONLY" if lead["role"] == "VENUE" else None)
        decision, code, override = decide(lead, code, bool(regular), inp["founder_prefs"], "NO_RECURRING_ACTIVITY")
        if decision == "REJECTED" and code == "VENUE_ONLY":
            decision = "PENDING"  # a venue stays a lead until an organizer page exists
        fit = ("QLOO_LISTING" if lead["listing"] else "QLOO_RELATED" if lead["origin"] == "QLOO" and sells_established(inp)
               else "INTERPRETATION")
        ax = axes(fit, f["identity"], f["local"], "SUPPORTED" if regular else "TO_CHECK" if acts else "NONE",
                  f["contact"], f["location_state"])
        aud, hood = target_tags(ctx, t, lead, pages)
        candidates.append({"ref": lead["key"], "name": lead["name"], "decision": decision, "reason_code": code,
                           "reason": f["identity_reason"] if decision == "KEPT" else code or "", "override": override,
                           "claim_ids": [c["cid"] for c in f["mine"]], "by": "specialist", "axes": ax})
        rows.append({"lead_key": lead["key"], "name": lead["name"], "role": lead["role"], "axes": ax,
                     "venue_lead_key": venue and venue["key"], "activities": acts, "location": lead["location"],
                     "contact_route": "EMAIL" if f["email"] else "UNKNOWN", "decision": decision,
                     "qloo_id": lead.get("qloo_id"), "anchor": aud, "neighbourhood": hood,
                     "why_qloo": why_lead(aud, hood, lead["name"]), "kind": "group"})
    candidates += model_rejections(sub, candidates, COMMUNITY_CODES)
    shown = [r for r in rows if r["decision"] in ("KEPT", "PENDING")][:3]
    unconfirmed = lead_rows(ctx, t, dropped + unread + sub.get("leads", []), pages,
                            [r["name"] for r in rows] + [c["name"] for c in candidates], faults)
    sub = no_qloo_name(sub)
    block = {"concept": sub.get("concept") or "", "groups": shown, "leads": unconfirmed,
             "participation_assumptions": [{"text": t_, "state": "ASSUMED"} for t_ in sub.get("participation_assumptions", [])],
             "inquiry_questions": sub.get("inquiry_questions", [])}
    why = qloo_why(inp, t, shown, unconfirmed)
    return {"claims": claims, "candidates": candidates, "block": block, "faults": faults, "proximity": proximity(ctx.cid, rows),
            "missing": [{"text": m, "tried": []} for m in sub.get("missing", [])],
            "concept": {"name": sub.get("concept_name") or "", "line": sub.get("concept_line") or ""},
            "leads": list(leads.values()), "dependencies": [],
            "questions": sub.get("inquiry_questions", []) or ["Would the organizer welcome a brand partner?"],
            "assumptions": [a["text"] for a in block["participation_assumptions"]],
            "prose": {"community.concept": block["concept"],
                      **{f"community.participation_assumptions[{i}].text": a["text"]
                         for i, a in enumerate(block["participation_assumptions"])}},
            "qloo": target_view(t), "qloo_why": why, "why_it_fits": why,
            "audiences_used": [a["uuid"] for a in (t or {}).get("audiences", [])],
            "next": ("Ask the organizer about taking part",
                     f"Confirm {unconfirmed[0]['name']}: {unconfirmed[0]['found']}, not yet confirmed" if unconfirmed
                     else "No group we could check runs a relevant activity yet")}


# ---- creator_specialist@3 (@3: Qloo-anchored searches, read before dropping, unconfirmed names kept as LEADs) ----
CREATOR_SYSTEM = """You are the Creator specialist on Landfall, an international launch team for small consumer brands. In ONE
city, find up to 5 creators whose content is relevant to the brand and this city, confirm who they are, and propose a
collaboration.

Qloo decides the target; you find the names. The brief's QLOO TARGET lists the audiences Qloo relates to the brand,
where in the city its taste concentrates and local themes. Run its searches first, and look for creators whose content
serves those audiences or themes in this city.

Tools: web_search finds readable sources (articles, creator websites, link pages; social platforms are never read);
fetch_page reads one of them; creator_profile canonicalises a TikTok, YouTube or Instagram profile URL and, for TikTok
and YouTube, asks the platform's public oEmbed for the profile's own name (Instagram is never fetched).

Rules:
- Keep three things apart: where the creator is (a quote saying they are based in or from the city), what their content
  is about (pages you read), and where their audience is (unknown: nothing available states it).
- Identity needs evidence: a page you read that names the handle or links the profile, and the platform's own answer.
- A follower or subscriber figure needs its source and a date; an undated third-party figure is not a metric.
- Never claim fees, availability, engagement or audience demographics.
- Read before you drop: when a search names a creator, read a page about them (their website, a link page, an article)
  before deciding. A page you couldn't read is unknown, not a reason to reject.
- Every creator a search or page named that you couldn't confirm goes in `leads` (their name and the address that
  named them). Never invent a name.
- `rejected` is for evidence reasons only (NOT_LOCAL, OFF_TOPIC).
- collaboration_concepts are proposals that may name one of the brand's products."""


class CreatorEvidenceIn(research.Lenient):
    source: str = Field(description="The SOURCE id of a page you read")
    quote: str = Field(description="Copied exactly, 5-40 words; it names the creator or their handle")


class CreatorIn(research.Lenient):
    name: str
    profile_url: str
    evidence: list[CreatorEvidenceIn] = Field(default_factory=list, description="Pages naming the handle or linking the profile")
    location_quote: CreatorEvidenceIn | None = Field(None, description="A quote saying where the creator is based")
    content: list[CreatorEvidenceIn] = Field(default_factory=list, description="Quotes showing what their content is about")
    figures: list[CreatorEvidenceIn] = Field(default_factory=list, description="Quotes stating follower or subscriber counts")


class CreatorDossierIn(JsonLists):
    concept_name: str = ""
    concept_line: str = ""
    collaboration_concepts: list[str] = Field(default_factory=list)
    creators: list[CreatorIn] = Field(default_factory=list)
    leads: list[LeadNameIn] = Field(default_factory=list, description="Named by a search or page, not yet confirmed")
    rejected: list[RejectedIn] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


CREATOR_CODES = {"IDENTITY_UNCONFIRMED", "NOT_LOCAL", "OFF_TOPIC", "FOUNDER_EXCLUDED"}
HOSTS_OF = {"tiktok": "tiktok.com", "youtube": "youtube.com", "instagram": "instagram.com"}
FIGURE = re.compile(r"\b(\d[\d,.]*\s*(?:k|m|million|thousand)?)\s*(?:followers|subscribers|fans)\b", re.I)
OUTSTANDING = ("audience geography", "fee", "usage rights", "availability", "ad-disclosure duty")


def creator_tools(ctx: research.Ctx) -> list:
    @tool
    def creator_profile(url: str) -> str:
        """Canonicalise a TikTok, YouTube or Instagram profile URL and confirm its own name where the platform allows."""
        p = creators.canonical(url)
        if not p:
            return "Not a profile URL (a post, a video or another site). Give the profile's own URL."
        if p["platform"] == "instagram":
            return f"PROFILE {p['canonical_url']} ({p['handle']}): Instagram is never fetched; identity stays unconfirmed by the platform."
        res, refused = research.memo(ctx, "profile", _k(p["canonical_url"]), lambda: {
            "profile": p, "oembed": creators.oembed(p, ctx.run.cassette),
            "youtube": creators.youtube_channel(p, ctx.run.cassette)})
        if refused:
            return f"Not checked: {refused}."
        o = res["oembed"]
        yt = res.get("youtube")
        return (f"PROFILE {p['canonical_url']} ({p['handle']})\n"
                + (f"PLATFORM NAME: {o['author_name']} ({o['author_url']})" if o else "PLATFORM NAME: unavailable (unknown, not absent)")
                + (f"\nYOUTUBE: {yt['title']}; country set by the channel: {yt.get('country') or 'none'}" if yt else ""))
    return [creator_profile]


def validate_creator(ctx: research.Ctx, sub: dict, inp: dict) -> dict:
    pages, faults = pages_of(ctx), []
    t = target_of(inp)
    sub, unread = split_rejections(sub, CREATOR_CODES)
    dropped = []
    profiles = {r["profile"]["canonical_url"]: r for r in research.steps(ctx.cr, "profile")}
    brand_cats = set(brand_categories(inp))
    candidates, rows, leads, claims = [], [], [], []
    excl = {_norm(x) for x in inp["founder_prefs"]["exclude"]}

    def quoted(e):
        s = pages.get((e.get("source") or "").strip()) if e else None
        return s if s and research.readable(s) and quote_found(e["quote"], s["text"]) else None

    for cr in sub.get("creators", []):
        p = creators.canonical(cr["profile_url"])
        if not p:  # not a profile URL: kept as a LEAD when this run saw the name (never dropped unread)
            dropped.append({**cr, "url": cr.get("profile_url")})
            continue
        handle = p["handle"].lstrip("@").lower()
        named = lambda text: research._in(cr["name"], text) >= 0 or _tight(cr["name"]) in _tight(text)  # noqa: E731
        ev = []
        for e in cr.get("evidence", []):  # P45-I4: the quote ties THIS person to THIS handle or profile; a name alone doesn't
            s, q = quoted(e), (e or {}).get("quote", "")
            if not (s and named(q)):
                continue
            if f"{HOSTS_OF[p['platform']]}/{'@' if p['platform'] != 'instagram' else ''}{handle}" in q.lower().replace("www.", ""):
                ev.append({"kind": "PAGE_LINKS_PROFILE", "source_id": s["id"]})
            elif re.search(rf"(?<![\w.]){re.escape(handle)}(?![\w])", q.lower()):
                ev.append({"kind": "PAGE_NAMES_HANDLE", "source_id": s["id"]})
        prof = profiles.get(p["canonical_url"]) or {}
        o = prof.get("oembed")
        if o:  # the platform's own answer must be the same profile AND the same person
            same_url = (creators.canonical(o["author_url"]) or {}).get("canonical_url", "").lower() == p["canonical_url"].lower()
            same_person = bool(o.get("author_name")) and (named(o["author_name"]) or research.same_name(o["author_name"], cr["name"]))
            ev.append({"kind": "OEMBED", "source_id": None, "agrees": same_url and same_person})
        yt = prof.get("youtube")
        if yt:
            ev.append({"kind": "PLATFORM_API", "source_id": None})
        identity = creators.identity(ev)
        lead = {"name": cr["name"], "origin": "PAGE", "listing": False, "chain": False, "own_site": None,
                "first_domain": p["platform"], "qloo_id": None}
        lead["key"] = lead["lead_key"] = f"creator|{p['canonical_url'].lower()}"
        loc_claim = None
        if cr.get("location_quote"):
            loc_claim = {"cid": f"c{len(claims) + 1}", "lead_key": lead["key"], "subject_name": cr["name"],
                         "text": f"is based in {ctx.inp['name']}", **{k: cr["location_quote"][k] for k in ("source", "quote")}}
            claims.append(loc_claim)
        content = [{"cid": f"c{len(claims) + 1 + i}", "lead_key": lead["key"], "subject_name": cr["name"],
                    "text": "publishes content about this", **{k: e[k] for k in ("source", "quote")}}
                   for i, e in enumerate(cr.get("content", []))]
        claims += content
        figures = []
        for e in cr.get("figures", []):
            s = quoted(e)
            m = FIGURE.search(e["quote"]) if s else None
            dated = [d for d in event_dates(e["quote"], ctx.cid) if d.get("date")]
            if m:
                figures.append({"name": "followers", "value": m.group(1), "source_id": s["id"],
                                "observed_at": dated[0]["date"] if dated else None, "quote": e["quote"]})
        if yt and yt.get("metric"):
            figures.append({**yt["metric"], "source_id": None, "retention": yt["retention"]})
        lead["creator"] = {"platform": p["platform"], "handle": p["handle"], "canonical_url": p["canonical_url"],
                           "display_name": (o or {}).get("author_name") or cr["name"], "identity": identity,
                           "identity_evidence": ev, "metrics": [x for x in figures if creators.keep_metric(x)],
                           "unused_figures": [x for x in figures if not creators.keep_metric(x)],
                           "youtube": yt and {k: yt[k] for k in ("channel_id", "country", "retention")}}
        leads.append(lead)
        rows.append((lead, loc_claim, content))
    leads_by = {x["key"]: x for x in leads}
    check_claims(ctx, claims, pages, leads_by, "creator")
    for lead, loc_claim, content in rows:
        c = lead["creator"]
        located = bool(loc_claim and loc_claim["state"] == "SUPPORTED" and loc_claim["locality"] == "LOCAL")
        own = loc_claim and loc_claim["source_id"] and fetch.site_of((research.source(loc_claim["source_id"]) or {}).get("final_url") or "")
        c["location"] = {"state": ("SELF_REPORTED" if located and own and own not in ("instagram.com", "tiktok.com", "youtube.com")
                                   and research._in(lead["name"], own.replace(".", " ")) >= 0 else "REPORTED" if located
                                   else "PLATFORM_DECLARED" if (c.get("youtube") or {}).get("country") else "UNKNOWN"),
                         "text": ctx.inp["name"] if located else (c.get("youtube") or {}).get("country"),
                         "source_id": loc_claim and loc_claim["source_id"]}
        observed = [x for x in content if x["state"] == "SUPPORTED"]
        c["themes"] = [{"text": x["quote"][:120], "state": "OBSERVED", "source_id": x["source_id"]} for x in observed]
        on_topic = any(set(categories_in(x["quote"])) & brand_cats for x in observed)
        code = ("FOUNDER_EXCLUDED" if _norm(lead["name"]) in excl else None)
        if code:
            decision = "REJECTED"
        elif c["identity"] == "POSSIBLE":
            decision, code = "PENDING", "IDENTITY_UNCONFIRMED"
        elif not located:
            decision, code = "PENDING", "NOT_LOCAL"
        elif not observed:
            decision, code = "PENDING", "OFF_TOPIC"
        else:
            decision = "KEPT"
        ax = axes("BRAND_FACT_MATCH" if on_topic and sells_established(inp) else "INTERPRETATION",
                  "VERIFIED" if c["identity"] == "IDENTIFIED" else "NOT_CHECKED", "LOCAL" if located else "TO_CHECK",
                  "SUPPORTED" if observed else "NONE", "UNKNOWN")
        candidates.append({"ref": lead["key"], "name": lead["name"], "decision": decision, "reason_code": code,
                           "reason": code or "", "override": False, "claim_ids": [x["cid"] for x in [loc_claim, *content] if x],
                           "by": "specialist", "axes": ax})
        aud, hood = anchors_of(ctx, t, lead["name"], pages)
        lead["row"] = {"lead_key": lead["key"], "name": lead["name"], "axes": ax, "creator": c, "anchor": aud,
                       "neighbourhood": hood, "why_qloo": why_lead(aud, hood, lead["name"]), "kind": "creator",
                       "content_evidence": [{"url": (research.source(x["source_id"]) or {}).get("final_url"),
                                             "title": (research.source(x["source_id"]) or {}).get("title"),
                                             "observed_at": (research.source(x["source_id"]) or {}).get("fetched_at"),
                                             "theme": x["quote"][:120], "state": "OBSERVED"} for x in observed],
                       "contact_route": "UNKNOWN", "audience_location": {"state": "UNKNOWN"}, "fee": "UNKNOWN",
                       "decision": decision}
    candidates += model_rejections(sub, candidates, CREATOR_CODES)
    sub = no_qloo_name(sub)
    concepts = sub.get("collaboration_concepts", [])
    shown = [x["row"] for x in leads if x["row"]["decision"] in ("KEPT", "PENDING")][:5]
    unconfirmed = lead_rows(ctx, t, dropped + unread + sub.get("leads", []), pages,
                            [x["name"] for x in leads] + [c["name"] for c in candidates], faults)
    block = {"collaboration_concepts": concepts, "creators": shown, "leads": unconfirmed, "outstanding": list(OUTSTANDING)}
    for x in leads:
        x.pop("row", None)
    why = qloo_why(inp, t, shown, unconfirmed)
    first = next((r for r in shown if r["decision"] == "PENDING"), None) or (unconfirmed and unconfirmed[0])
    return {"claims": claims, "candidates": candidates, "block": block, "faults": faults, "proximity": None,
            "missing": [{"text": m, "tried": []} for m in sub.get("missing", [])],
            "concept": {"name": sub.get("concept_name") or "", "line": sub.get("concept_line") or ""},
            "leads": leads, "dependencies": [], "questions": [f"{o.capitalize()}?" for o in OUTSTANDING],
            "prose": {f"creator.collaboration_concepts[{i}]": x for i, x in enumerate(concepts)},
            "qloo": target_view(t), "qloo_why": why, "why_it_fits": why,
            "audiences_used": [a["uuid"] for a in (t or {}).get("audiences", [])],
            "next": ("Ask about a collaboration and its fee",
                     f"Confirm {first['name']}: " + (first.get("found") or "found, identity or location not yet confirmed")
                     + (", not yet confirmed" if first.get("found") else "") if first
                     else "No creator we could confirm fits yet")}


# ---- digital_specialist@3 (one structured call; no tool loop; @3: Google Search ads only, owner 28 Oct 2026) ----
PLATFORMS = json.loads((markets.DATA / "platforms.json").read_text())
PLATFORM_IDS = {e["id"] for e in PLATFORMS["entries"]}
DIGITAL_SYSTEM = """You are the Digital specialist on Landfall, an international launch team for small consumer brands. For ONE
city, turn the brand and its cultural evidence into one testable Google Search ads outline: channel, targeting
hypothesis, message, creative requirements and measurement. Return DigitalOutlineIn now; you have no tools.

Rules:
- The channel is Google Search ads, always: people searching on Google for what the brand sells see a text ad and click
  through to the brand's site. Never propose TikTok, Meta, Instagram, Facebook, YouTube or any other platform, and the
  concept, message and measurement are about search ads (searches, clicks to the site, sign-ups or purchases from them).
- Every targeting element has a basis: QLOO_DERIVED (one of the listed audiences, by its exact name: an interest
  hypothesis, never "exportable" or "targetable"), PLATFORM_OPTION (an option type in the PLATFORM OPTIONS list, with its
  id in platforms_ref), or TO_CHECK_IN_ADS_MANAGER (a specific interest name nobody has verified on the platform).
- If the brand is not shown to ship to this city's country, the objective is SIGN_UP (a waitlist), not PURCHASE.
- A demographic skew stays a skew ("leans 45-54"), never "the customers". Keep the stand-in caveat when one is given.
- No reach, impressions, CPM, cost, budget or forecast figures: those come later, with costs.
- The message is one line built from the brand's facts or products.
- Every PLATFORM_OPTION gives platforms_ref, exactly one of the ids in PLATFORM OPTIONS; an option with no listed id is
  TO_CHECK_IN_ADS_MANAGER. Where a platform documents city targeting with a radius, the geography is itself a
  PLATFORM_OPTION."""


class TargetIn(research.Lenient):
    element: str
    basis: str = Field(description="QLOO_DERIVED, PLATFORM_OPTION or TO_CHECK_IN_ADS_MANAGER")
    platforms_ref: str | None = None


class CreativeIn(research.Lenient):
    text: str
    platforms_ref: str | None = None


class DigitalOutlineIn(research.Lenient):
    concept_name: str = ""
    concept_line: str = ""
    channel_primary: str = Field("Google Search", description="Always Google Search")
    channel_alternative: str = ""
    objective: str = Field("SIGN_UP", description="PURCHASE, SIGN_UP or AWARENESS")
    radius_km_hypothesis: int | None = None
    targeting: list[TargetIn] = Field(default_factory=list)
    message: str = ""
    creative_requirements: list[CreativeIn] = Field(default_factory=list)
    primary_metric: str = ""
    stop_rule: str = Field("", description="A hypothesis, e.g. 'stop after two weeks if sign-ups stay flat'")
    horizon_days: int | None = None


MONEY_REACH = re.compile(r"\b(reach|impressions?|cpm|cpc|cpa|ctr|cost|spend|budget|forecast|roas|revenue)\b|[$€£¥]", re.I)
COUNTRY_ALIASES = {"US": ["US", "U.S.", "USA", "United States", "America"], "GB": ["UK", "United Kingdom", "Britain"],
                   "AU": ["Australia"], "CA": ["Canada"], "DE": ["Germany"], "PT": ["Portugal"], "DK": ["Denmark"]}


def ships_to(inp: dict) -> bool:
    """Shipping to the city's country is SUPPORTED by a brief fact naming it (or worldwide); otherwise not established."""
    ri = inp["research_input"]
    names = [markets.COUNTRY_NAME.get(ri["country"], ri["country"]), *COUNTRY_ALIASES.get(ri["country"], [])]
    for f in ri["facts"]:
        if f["label"] == "Shipping" and f["state"] == "SUPPORTED":
            if re.search(r"\b(worldwide|international(ly)?|globally)\b", f["value"], re.I) or \
                    any(re.search(rf"(?<!\w){re.escape(n)}(?!\w)", f["value"]) for n in names):
                return True
    return False


def digital_prompt(inp: dict) -> str:
    ri = inp["research_input"]
    aud = ri["audience"] or {}
    lines = [prompt_of(inp, SPECS["digital"]).rsplit("\n\nInvestigate", 1)[0]]
    lines.append("AUDIENCES (Qloo; interest hypotheses):\n" + ("\n".join(f"- {a['name']}" for a in aud.get("offered", []))
                                                              or "- none: build the targeting from brand facts alone"))
    if aud.get("demographics_lines"):
        lines.append("DEMOGRAPHIC SKEWS:\n" + "\n".join(f"- {x}" for x in aud["demographics_lines"]))
    if aud.get("caveat"):
        lines.append(f"CAVEAT: {aud['caveat']}")
    lines.append(f"SHIPS TO {markets.COUNTRY_NAME.get(ri['country'], ri['country']).upper()}: "
                 + ("yes (a checked fact says so)" if ships_to(inp) else "not established"))
    lines.append("PLATFORM OPTIONS (official, dated; Google only):\n" + "\n".join(
        f"- {e['id']}: {e['platform']} · {e['option_type']} (\"{e['quote'][:140]}\")" for e in PLATFORMS["entries"]
        if e["platform"] == "Google"))
    return "\n\n".join(lines) + "\n\nReturn DigitalOutlineIn."


# An option id badges an element only when the element is the kind of thing that option targets (P45-I1).
OPTION_CONTENT = {
    "meta.city_radius": r"\b(city|cities|radius|km|kilomet\w*|miles?|around)\b",
    "google.location_radius": r"\b(city|cities|radius|km|kilomet\w*|miles?|location|around)\b",
    "tiktok.location": r"\b(city|cities|location|region|metro)\b",
    "meta.age": r"\b\d{2}\s*(?:-|–|to)\s*\d{2}\b|\bage", "meta.gender": r"\b(men|women|male|female|gender)\b",
    "google.demographics": r"\b\d{2}\s*(?:-|–|to)\s*\d{2}\b|\b(age|men|women|male|female|gender)\b",
}
PLATFORM_OF = {"Meta": r"\b(meta|instagram|facebook)\b", "Google": r"\b(google|youtube)\b", "TikTok": r"\btiktok\b"}
# @3: a digital line naming another platform is dropped (LIVE 28 Oct 2026: "a bold, high-energy TikTok campaign")
NOT_SEARCH = re.compile(r"\b(tiktok|meta|instagram|facebook|reels?|snapchat|pinterest|youtube|influencers?)\b", re.I)
SEARCH_CHANNEL = "Google Search"
ENTRY = {e["id"]: e for e in PLATFORMS["entries"]}


def channel_platform(channel: str) -> str | None:
    return next((p for p, rx in PLATFORM_OF.items() if re.search(rx, channel or "", re.I)), None)


def bind_option(t: dict, channel: str) -> dict:
    """A PLATFORM_OPTION must be a listed entry of the channel's own platform, and the element must be what that option
    type targets; otherwise TO_CHECK_IN_ADS_MANAGER with the reason. Interest-type options accept any interest name."""
    if t["basis"] != "PLATFORM_OPTION":
        return t
    e = ENTRY.get(t.get("platforms_ref"))
    why = ("no documented platform option matches" if not e
           else f"a {e['platform']} option, but the channel is {channel or 'not named'}" if e["platform"] != channel_platform(channel)
           else "the element isn't what this option targets" if not re.search(OPTION_CONTENT.get(e["id"], r"."), t["element"], re.I)
           else None)
    return {**t, "basis": "TO_CHECK_IN_ADS_MANAGER", "downgraded": why} if why else t


def money_or_reach(text: str) -> bool:
    return bool(text) and (bool(re.search(r"[$€£¥]", text)) or bool(research.NUM.search(text) and MONEY_REACH.search(text)))


def validate_digital(ctx: research.Ctx, sub: dict, inp: dict) -> dict:
    ri, faults = inp["research_input"], []
    offered = {a["name"]: a for a in ((ri["audience"] or {}).get("offered") or [])}
    channel = SEARCH_CHANNEL  # @3: Google Search ads only, whatever the model named

    def clean(text: str, field: str) -> str:
        if money_or_reach(text or ""):
            faults.append({"field": field, "reason": f"\"{text[:80]}\": a reach, cost or forecast figure; left out (slice 5)"})
            return ""
        if NOT_SEARCH.search(text or ""):
            faults.append({"field": field, "reason": f"\"{text[:80]}\": names a platform other than Google Search; left out"})
            return ""
        return text or ""
    targeting = []
    for t in sub.get("targeting", []):
        t = {"element": clean(t["element"], "digital.targeting"), "basis": (t.get("basis") or "TO_CHECK_IN_ADS_MANAGER").upper(),
             "platforms_ref": t.get("platforms_ref")}
        if not t["element"]:
            continue
        t = bind_option(targeting_basis(t, PLATFORM_IDS, set(offered)), channel)
        if t.get("downgraded"):
            faults.append({"field": "digital.targeting", "reason": f"\"{t['element']}\": {t['downgraded']}"})
        if t["basis"] == "QLOO_DERIVED":
            t["audience_uuid"] = offered[t["element"]]["uuid"]
        targeting.append(t)
    objective = (sub.get("objective") or "SIGN_UP").upper()
    if objective not in ("PURCHASE", "SIGN_UP", "AWARENESS"):
        objective = "SIGN_UP"
    if objective == "PURCHASE" and not ships_to(inp):
        objective = "SIGN_UP"
        faults.append({"field": "digital.objective", "reason": "Shipping to this country isn't established: sign-ups, not purchases"})
    radius = sub.get("radius_km_hypothesis")
    block = {"channel": {"primary": clean(channel, "digital.channel"),
                         "alternative": None},
             "objective": objective,
             "geography": {"city_id": ctx.cid, "radius_km_hypothesis": radius if isinstance(radius, int) and 0 < radius <= 80 else None},
             "targeting": targeting, "message": {"line": clean(sub.get("message") or "", "digital.message.line"),
                                                 "claim_ids": [f["claim_id"] for f in ri["facts"] if f["state"] == "SUPPORTED"]},
             "creative_requirements": [{"text": clean(c["text"], "digital.creative_requirements"),
                                        "platforms_ref": c.get("platforms_ref") if c.get("platforms_ref") in PLATFORM_IDS else None}
                                       for c in sub.get("creative_requirements", [])],
             "measurement": {"primary_metric": clean(sub.get("primary_metric") or "", "digital.measurement"),
                             "stop_rule": clean(sub.get("stop_rule") or "", "digital.measurement"), "stop_rule_label": "HYPOTHESIS"},
             "scenario_inputs_required": ["budget (you say)", "horizon", "currency", "objective", "rates per platform (sourced or assumed)"],
             "horizon_days": {"value": sub.get("horizon_days") or 14, "state": "ASSUMED"},
             "caveat": (ri["audience"] or {}).get("caveat"),
             "brand_facts_only": not offered}
    # Every displayed model-written field is judged here, before readiness (P45-I1): an unjudged or unsupported
    # element is dropped and can't lend the dossier a sourced badge.
    fields = {"digital.message.line": block["message"]["line"], "digital.channel.primary": block["channel"]["primary"],
              "digital.measurement.primary_metric": block["measurement"]["primary_metric"],
              "digital.measurement.stop_rule": block["measurement"]["stop_rule"],
              **{f"digital.targeting[{i}].element": t["element"] for i, t in enumerate(targeting)},
              **{f"digital.creative_requirements[{i}].text": c["text"] for i, c in enumerate(block["creative_requirements"])}}
    checked = check_prose(ctx, inp, fields, [], [], faults)
    v = {"method": "digital", "block": block}
    for path, text in checked.items():
        _set_path(v, path, text)
    block["targeting"] = [t for t in block["targeting"] if t["element"]]
    block["creative_requirements"] = [c for c in block["creative_requirements"] if c["text"]]
    channel = block["channel"]["primary"]
    sourced = [t for t in block["targeting"] if t["basis"] == "PLATFORM_OPTION"]
    qloo_used = [t for t in block["targeting"] if t["basis"] == "QLOO_DERIVED"]
    ax = axes("QLOO_RELATED" if qloo_used and sells_established(inp) else "INTERPRETATION", "NA", "NA",
              "SOURCED" if sourced else "TO_CHECK", "NA", digital=True)
    candidates = [{"ref": f"channel:{channel}", "name": channel, "decision": "KEPT", "reason_code": None, "reason": "",
                   "override": False, "claim_ids": [], "by": "specialist", "axes": ax}] if channel else []
    return {"claims": [], "candidates": candidates, "block": block, "faults": faults, "proximity": None, "missing": [],
            "concept": {"name": clean(sub.get("concept_name") or "", "concept.name"),
                        "line": clean(sub.get("concept_line") or "", "concept.line")},
            "leads": [], "dependencies": [], "questions": ["Exact keywords and audience segments, checked in Google Ads"],
            "audiences_used": [t["audience_uuid"] for t in qloo_used], "prose": {}, "next": ("", "")}


SPECS = {
    "retail": Spec("retail", "retail_specialist@2", RETAIL_SYSTEM, RetailDossierIn,
                   ["qloo_places", "web_search", "fetch_page", "check_presence"], retail_tools, validate_retail),
    "popup": Spec("popup", "popup_specialist@2", POPUP_SYSTEM, PopupDossierIn,
                  ["qloo_places", "web_search", "fetch_page", "check_presence"], lambda ctx: [], validate_popup),
    "community": Spec("community", "community_specialist@3", COMMUNITY_SYSTEM, CommunityDossierIn,
                      ["qloo_places", "web_search", "fetch_page", "check_presence"], lambda ctx: [], validate_community,
                      caps=TARGETED_CAPS, turns=TURNS_LONG),
    "creator": Spec("creator", "creator_specialist@3", CREATOR_SYSTEM, CreatorDossierIn,
                    ["web_search", "fetch_page"], creator_tools, validate_creator, caps=TARGETED_CAPS, turns=TURNS_LONG),
    "digital": Spec("digital", "digital_specialist@3", DIGITAL_SYSTEM, DigitalOutlineIn, [], lambda ctx: [],
                    validate_digital, caps=DIGITAL_CAPS, usd=0.20, prompt=digital_prompt),
}
METHOD_FALLBACK = {"retail": ("A shelf trial in an independent shop", "Ask one evidenced shop about a small shelf trial."),
                   "popup": ("A weekend pop-up", "Ask one venue with event-use evidence about a short activation."),
                   "community": ("A collaboration with a local group", "Ask one organizer how a brand could take part."),
                   "creator": ("A creator collaboration", "Ask one confirmed local creator about a collaboration."),
                   "digital": ("A local Google Search ads test", "Search ads for what you sell, shown to people searching in the city, "
                               "measured against a stop rule.")}
# P1-3: a retail concept must describe the shop trial, never the brand's own product copy ("Casual Shirt Brand /
# Stylish, wrinkle-free casual shirts…"). ponytail: a word test; a concept that names a trial passes.
RETAIL_TRIAL = re.compile(r"\b(shelf|shelves|trial|stock\w*|consign\w*|wholesale|trunk.show|pop-in|concession|"
                          r"shop-in-shop|in-store|carry|carried|placement|display|sell-through|stockist\w*)\b", re.I)


def retail_concept(concept: dict, shop: str | None = None) -> dict:
    """The retail concept as shown and stored: the model's when it names a shop trial, else one built from the first kept
    shop ("A shelf trial at {shop}"), else the method's fallback. Never the brand description."""
    name, line = concept.get("name") or "", concept.get("line") or ""
    if name and line and RETAIL_TRIAL.search(f"{name} {line}"):
        return concept
    if shop:
        return {"name": f"A shelf trial at {shop}", "line": f"Ask {shop} about a small shelf trial: a few of your products, "
                f"for a few weeks, to see whether they sell there.", "source": "DETERMINISTIC"}
    return {"name": METHOD_FALLBACK["retail"][0], "line": METHOD_FALLBACK["retail"][1], "source": "DETERMINISTIC"}


def concept_of(d: dict) -> dict:
    """A dossier's concept for display: retail goes through `retail_concept`, so records stored before P1-3 read right."""
    if d.get("method") != "retail":
        return d.get("concept") or {"name": "", "line": ""}
    kept = next((c.get("name") for c in d.get("candidates") or [] if c.get("decision") == "KEPT"), None)
    return retail_concept(d.get("concept") or {}, kept)


def bundle(inp: dict, claims: list[dict]) -> list[dict]:
    """The role-labeled evidence the prose check judges against (P45-R4)."""
    ev = [{"role": "CLAIM", "text": f"{c['subject_name']}: {c['text']} (\"{c['quote']}\")"} for c in claims
          if c["state"] == "SUPPORTED"]
    brief = db.get_record((inp["research_input"].get("brief") or {}).get("id") or "")
    ev += [{"role": "BRAND_FACT", "text": f"The brand is {brief['brand_name']}"}] if brief else []
    ev += [{"role": "BRAND_FACT", "text": f"{f['label']}: {f['value']}"} for f in inp["research_input"]["facts"]
           if f["state"] in ("SUPPORTED", "STATED")]
    ev += [{"role": "PRODUCT", "text": f"{p['title']}" + (f": {', '.join(p['category_terms'] + p['materials'])}"
                                                          if p["category_terms"] or p["materials"] else "")}
           for p in _products(inp)]
    if t := target_of(inp):  # community and creator: the Qloo target the specialist was given
        ev += [{"role": "QLOO", "text": f"Audience Qloo relates to the brand: {a['name']}"} for a in t["audiences"]]
        ev += [{"role": "QLOO", "text": f"Where in the city Qloo relates the brand's taste: {n['name']}"} for n in t["neighbourhoods"]]
        ev += [{"role": "QLOO", "text": f"Local theme Qloo relates to the brand: {x['name']}"} for x in t["themes"]]
    if inp["method"] == "digital":
        ev += [{"role": "QLOO", "text": f"Audience: {a['name']}"} for a in ((inp["research_input"]["audience"] or {}).get("offered") or [])]
        ev += [{"role": "PLATFORM", "text": f"{e['platform']} · {e['option_type']}: \"{e['quote']}\""} for e in PLATFORMS["entries"]]
    if inp["research_input"]["context"]:
        ev.append({"role": "FOUNDER", "text": inp["research_input"]["context"]})
    if inp["founder_prefs"]["rationale"]:
        ev.append({"role": "FOUNDER", "text": inp["founder_prefs"]["rationale"]})
    return ev


def check_prose(ctx: research.Ctx, inp: dict, fields: dict[str, str], claims: list[dict], leads: list[dict],
                faults: list[dict]) -> dict[str, str]:
    """Every model-written field is judged against the bundle; an unjudged or unsupported sentence is dropped."""
    ev = bundle(inp, claims)
    items = [{"id": f"s{n}", "field": f, "text": t, "evidence": ev} for n, (f, t) in enumerate(fields.items(), 1) if t]
    if not items:
        return fields
    res = _judge(ctx, PROSE, "Sort each sentence:\n" + prose_listing(items), research.ProseVerdicts, "prose", "0")
    keep = prose_keep(items, res["verdicts"], leads)
    out = dict(fields)
    for it in items:
        if not keep[it["id"]]:
            out[it["field"]] = ""
            faults.append({"field": it["field"], "reason": f"\"{it['text'][:80]}\": not supported by its evidence; left out"})
    return out


# The brief facts each method uses (BUILD-PLAN §4.5.3); a change to any other fact flags nothing.
FACTS_USED = {"retail": ("What you sell", "Price points", "Where you sell", "Shipping"), "popup": ("What you sell",),
              "creator": ("What you sell",), "community": ("What you sell", "Community"),
              "digital": ("What you sell", "Shipping", "Where you sell")}
PRODUCT_FIELDS = ("title", "category_terms", "materials")  # what prompts, validators and the prose bundle read


def consumed_of(inp: dict, claims: list[dict], products_used: list[dict]) -> dict:
    """The values this dossier used (P45-R5, P45-I2): a change to one of these flags it; anything else flags nothing."""
    ri, method = inp["research_input"], inp["method"]
    return {"city_id": ri["city_id"], "goal": ri["goal"], "context": ri["context"], "prefs": inp["founder_prefs"],
            "facts": {f["claim_id"]: {"value": f["value"], "state": f["state"]} for f in ri["facts"]
                      if f["label"] in FACTS_USED.get(method, ())},
            "claims": {c["cid"]: c["state"] for c in claims},
            "products": {p["id"]: {"version": p["version"], "fields_used": list(PRODUCT_FIELDS),
                                   **{k: p[k] for k in PRODUCT_FIELDS}} for p in products_used},
            "ranking_signal": sorted((ri.get("ranking_signal") or {}).get("entity_ids") or []),
            "platforms": PLATFORMS["version"] if method == "digital" else None,
            "lead_locations": {}}


def stale_of(pid: str, d: dict) -> list[str]:
    """One selective comparison of the consumed values with today's, for the dossier, its drafts and handoff views."""
    c, out = d["consumed"], []
    for fid, used in c["facts"].items():
        now = db.get_record(fid) or {}
        used = used if isinstance(used, dict) else {"value": used, "state": now.get("state")}  # pre-I2 dossiers
        if now.get("value") != used["value"] or now.get("state") != used["state"]:
            out.append("A brand fact this used changed")
            break
    if any((db.get_record(cid) or {}).get("state") != st for cid, st in (c.get("claims") or {}).items()):
        out.append("A checked claim this used changed")
    for prid, p in c["products"].items():
        now = db.get_record(prid)
        if not now or any(now.get(k) != p[k] for k in p["fields_used"]):
            out.append("A product this used changed")
            break
    if ((db.latest("founder_input", pid) or {}).get("research_context") or None) != c["context"]:
        out.append("Your note changed")
    g = ranking.goal_of(pid)
    if c.get("goal") and {"scope": g["scope"], "purpose": g["purpose"]} != c["goal"]:
        out.append("Your goal changed")
    ws = workspace(pid, d["city_id"], d["method"])
    if c.get("prefs") is not None and ws and {k: ws["founder_prefs"].get(k) for k in ("include", "exclude")} != \
            {k: c["prefs"].get(k) for k in ("include", "exclude")}:
        out.append("Your include or exclude list changed")
    if "ranking_signal" in c and sorted((ranking.ranking_signal(pid) or {}).get("entity_ids") or []) != c["ranking_signal"]:
        out.append("Qloo's signal for your brand changed")
    ap = db.latest("audience_profile", pid)
    if ap and set(ap["removed"]) & set(c.get("audiences") or []):
        out.append("You removed an audience this used")
    if c.get("platforms") and c["platforms"] != PLATFORMS["version"]:
        out.append("The platform options reference changed")
    for key, version in (c.get("lead_locations") or {}).items():
        loc = db.get_record(f"loc_{_k(pid, d['city_id'], key)}")
        if loc and loc["version"] != version:
            out.append("A location changed")
            break
    return out


def _set_path(v: dict, path: str, value: str) -> None:
    """Write a checked prose field back: "retail.possible_activation", "creator.collaboration_concepts[1]"."""
    head, _, rest = path.partition(".")
    node = v["block"] if head == v["method"] else v
    keys = [int(k) if k.isdigit() else k for k in re.findall(r"[^.\[\]]+", rest if head == v["method"] else path)]
    for k in keys[:-1]:
        node = node[k]
    node[keys[-1]] = value


EMPTY = {"claims": [], "candidates": [], "block": None, "faults": [], "missing": [], "concept": {"name": "", "line": ""},
         "leads": [], "questions": [], "dependencies": [], "prose": {}, "proximity": None, "next": ("", "")}


def publish(ctx: research.Ctx, spec, inp: dict, sub: dict | None, status: str, error: str | None = None) -> dict:
    pid, cid, method = ctx.pid, ctx.cid, spec.method
    v = {**EMPTY, **(spec.validate(ctx, sub, inp) if sub else {}), "method": method}
    faults = v["faults"]
    if v["block"]:
        fields = {"concept.name": v["concept"]["name"], "concept.line": v["concept"]["line"], **v["prose"]}
        prose = check_prose(ctx, inp, fields, v["claims"], v["leads"], faults)
        for path, text in prose.items():
            if not path.startswith("concept."):
                _set_path(v, path, text)
        for k, val in v["block"].items():  # a dropped list sentence leaves the list, not a blank entry
            if isinstance(val, list):
                v["block"][k] = [x for x in val if x != "" and not (isinstance(x, dict) and x.get("text") == "")]
        name, line = prose["concept.name"], prose["concept.line"]
        model = bool(name and line)
        v["concept"] = {"name": name if model else METHOD_FALLBACK[method][0],
                        "line": line if model else METHOD_FALLBACK[method][1], "source": "MODEL" if model else "DETERMINISTIC"}
        if method == "retail":
            kept = next((c.get("name") for c in v["candidates"] if c.get("decision") == "KEPT"), None)
            v["concept"] = retail_concept(v["concept"], kept)
    ids = {}
    for c in v["claims"]:
        rec = db.put_record("city_claim", pid, {
            **{k: c.get(k) for k in ("subject_name", "method", "text", "quote", "source_id", "window", "quote_found",
                                     "locality", "state", "shows_way_in", "support", "reason")},
            "subject": c["lead_key"], "city_id": cid, "vc": "F", "checker": CHECK_VERSION,
            "specialist": {"method": method, "version": ctx.rv}}, rid=f"ccl_{_k(ctx.cr, c['cid'])}")
        ids[c["cid"]] = rec["id"]
    lead_ids, locations = {}, {}
    for lead in v["leads"]:
        rec = db.put_record("lead", pid, {**{k: lead.get(k) for k in ("key", "name", "origin", "qloo_id", "address",
                                                                       "own_site", "listing", "chain", "role", "creator",
                                                                       "contact")},
                                          "type": {"retail": "shop", "popup": "venue", "community": "group",
                                                   "creator": "creator"}.get(method, "other"),
                                          "presence": lead.get("presence") or "NOT_CHECKED",
                                          "presence_check": lead.get("presence_check") or
                                          {"state": "NOT_CHECKED", "reason": "Not checked by this investigation.", "source_ids": []},
                                          "city_id": cid, "claim_ids": [ids[c["cid"]] for c in v["claims"]
                                                                        if c["lead_key"] == lead["key"]],
                                          "willingness": "UNKNOWN"}, rid=f"lead_{_k(ctx.cr, lead['key'])}")
        lead_ids[lead["key"]] = rec["id"]
        if lead.get("location"):
            loc = db.put_record("lead_location", pid, {**lead["location"], "lead_id": rec["id"]},
                                rid=f"loc_{_k(pid, cid, lead['key'])}")
            locations[lead["key"]] = loc["version"]
    for c in v["candidates"]:
        c["claim_ids"] = [ids[x] for x in c["claim_ids"] if x in ids]
        c["lead_id"] = lead_ids.get(c["ref"])
    for rows in (v["block"] or {}).values():
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict) and "lead_key" in r:
                r["lead_id"] = lead_ids.get(r["lead_key"])
                for e in r.get("event_use", []) + r.get("activities", []):
                    e["claim_id"] = ids.get(e.get("claim_id"), e.get("claim_id"))
    kept = [c["axes"] for c in v["candidates"] if c["decision"] == "KEPT" and c["axes"]]
    readiness_ = readiness(kept)
    products_used = _products(inp) if v["block"] else []
    consumed = consumed_of(inp, [{**c, "cid": ids[c["cid"]]} for c in v["claims"]], products_used)
    consumed["lead_locations"] = locations
    consumed["audiences"] = v.get("audiences_used", [])
    best = next((c for c in v["candidates"] if c["decision"] == "KEPT" and c["axes"]
                 and c["axes"]["overall"] == readiness_["overall"]), None)
    next_action = ({"kind": "INQUIRE", "text": v["next"][0], "target": best["lead_id"]}
                   if readiness_["overall"] == "EVIDENCED" and best else
                   {"kind": "VERIFY", "text": best["axes"]["reasons"][0], "target": best["lead_id"]} if best else
                   {"kind": "INVESTIGATE_FURTHER", "text": v["next"][1] or "Nothing we could check fits yet"})
    if method == "digital" and v["block"]:
        next_action = {"kind": "DEVELOP", "text": "Develop the test outline, then add costs in your plan"}
    ws_prev = workspace(pid, cid, method)
    ws = db.put_record("workspace", pid, {
        "city_id": cid, "method": method,
        "candidates": [{k: c[k] for k in ("ref", "name", "decision", "reason_code", "reason", "claim_ids", "by", "override")}
                       for c in v["candidates"]],
        "attempts": attempts_of(ctx) + ((ws_prev or {}).get("attempts") or [])[:50],
        "open_questions": [{"text": q, "state": "UNKNOWN"} for q in v["questions"]],
        "founder_prefs": inp["founder_prefs"],
        "dossier_versions": [*((ws_prev or {}).get("dossier_versions") or []), ctx.rv]}, rid=f"ws_{pid}_{cid}_{method}")
    payload = {"city_id": cid, "method": method, "input": inp, "consumed": consumed, "concept": v["concept"],
               "products": [{"id": p["id"], "version": p["version"]} for p in products_used],
               "why_it_fits": v.get("why_it_fits", []), "facts": [ids[c["cid"]] for c in v["claims"] if c["state"] == "SUPPORTED"],
               "interpretations": v.get("interpretations", []), "assumptions": v.get("assumptions", []),
               "missing": v["missing"], "dependencies": v["dependencies"], "next_action": next_action,
               "readiness": readiness_, method: v["block"], "candidates": v["candidates"], "proximity": v["proximity"],
               "workspace": {"id": ws["id"], "version": ws["version"]}, "counters": research.counts(ctx.cr),
               "faults": faults, "prompt_version": spec.prompt_version, "check_version": CHECK_VERSION,
               "status": status, "error": error, "spec_version": ctx.rv,
               "qloo": v.get("qloo") or target_view(target_of(inp)), "qloo_why": v.get("qloo_why", [])}
    rec = db.put_record("dossier", pid, payload, rid=f"dos_{pid}_{cid}_{method}")
    _set_spec(pid, cid, method, ctx.rv, status=status, error=error, ended_at=db.now())
    research._activity(ctx.run, f"Investigate {METHOD_LABELS[method].lower()} in {ctx.inp['name']}",
                       spec.prompt_version.split("@")[0].replace("_", " "),
                       f"{status} · {readiness_['overall'].replace('_', ' ').lower()} · "
                       f"{sum(c['decision'] == 'KEPT' for c in v['candidates'])} kept · "
                       f"{sum(c['decision'] == 'REJECTED' for c in v['candidates'])} rejected",
                       "DONE" if status == "complete" else "FAILED" if status == "failed" else "PARTIAL")
    return {"id": rec["id"], "version": rec["version"], "status": status}


METHOD_LABELS = research.METHOD_LABELS


def specialist_run(run: Run):
    pid, cid, method, v = run.project_id, run.params["city_id"], run.params["method"], run.params["version"]
    spec = SPECS[method]
    if not db.latest("product_research", pid):  # shared product research runs once, before the first specialist
        run.step("products", lambda: products.products_run(run) or {"ok": True})
    row = spec_row(pid, cid, method, v)
    if row["status"] in TERMINAL_SPEC and row["run_id"] == run.id:
        return None
    inp = json.loads(row["input"]) if row["input"] else build_spec_input(pid, cid, method, run.params.get("prefs") or {})
    if method in QLOO_TARGETED and "qloo_target" not in inp:  # pinned with the input; shared with the other method
        inp["qloo_target"] = run.step(f"qloo_target:{cid}", lambda: city_target(run, pid, cid))
    _set_spec(pid, cid, method, v, status="running", run_id=run.id, input=inp, started_at=row["started_at"] or db.now(),
              error=None)
    ctx = _ctx(run, inp, cid, method, v)

    def go():
        try:
            sub = json.loads(row["submission"]) if row["submission"] else None
            if sub is None:
                sub = run_loop(ctx, spec, row, inp)
                _set_spec(pid, cid, method, v, submission=sub)
            return publish(ctx, spec, inp, sub, "complete")
        except runner.Cancelled:
            _set_spec(pid, cid, method, v, status="stopped")
            raise
        except NoRecording:
            _set_spec(pid, cid, method, v, status="failed", error="recorded data does not cover this step")
            raise
        except Exception as e:
            reason = (f"operating allowance reached ({e})" if research._caused_by(e, BudgetExhausted)
                      else f"{type(e).__name__}: {str(e)[:160]}")
            return publish(ctx, spec, inp, None, "failed", reason)
    out = run.step(f"specialist:{cid}:{method}:{v}", go)
    return ("partial", out.get("error") or "The investigation failed; Retry.") if out["status"] == "failed" else None


HANDLERS["specialist"] = specialist_run


def investigate(pid: str, cid: str, method: str, prefs: dict, dry_run: bool = False) -> dict:
    """POST …/cities/{city_id}/methods/{method}/investigate (CONTRACTS B6.5)."""
    if method not in SPECS:
        raise Refused(422, f"The {method} specialist isn't built yet.")
    if cid not in research.latest_research(pid):
        raise Refused(409, "Research this city first.")
    if note := ruled_by_note(pid, cid, method):
        raise Refused(422, f"Your note rules this out ({note}). Edit your note to investigate it.")
    cost = {"cap_usd": SPECS[method].usd, "expected_usd": min(SPEC_EXPECTED, SPECS[method].usd)}
    if dry_run:
        return {"cost": cost}
    with db._lock:
        row = spec_row(pid, cid, method)
        if row and row["status"] in ("queued", "running"):
            raise Refused(409, "This investigation is already running.")
        v = (row["version"] if row else 0) + 1
        db.x("INSERT INTO specialist_runs(project_id, city_id, method, version, status, created_at) VALUES(?,?,?,?,?,?)",
             pid, cid, method, v, "queued", db.now())
    rid = runner.start(pid, "specialist", f"{cid}:{method}:{v}",
                       {"city_id": cid, "method": method, "version": v, "prefs": prefs})
    return {"run_id": rid, "dossier_id": f"dos_{pid}_{cid}_{method}", "cost": cost}


def timed(block, city_id: str, now: datetime):
    """P45-R6: PAST, UPCOMING or UNDATED is computed now, in the city's timezone, never read from storage."""
    if isinstance(block, dict):
        out = {k: timed(v, city_id, now) for k, v in block.items()}
        if block.get("recurrence_inputs"):  # a community activity: recurrence, next date and staleness as of now
            out.update(activity_timing(block.get("quote") or "", block.get("event_dates") or [], city_id, now,
                                       block.get("fetched_at") and datetime.fromisoformat(block["fetched_at"])))
        if isinstance(out.get("event_dates"), list):
            out["event_dates"] = [{**e, "when": when(e, now)} for e in out["event_dates"]]
        return out
    if isinstance(block, list):
        return [timed(x, city_id, now) for x in block]
    return block


def dossier_view(pid: str, did: str) -> dict | None:
    d = db.get_record(did)
    if not d or d["project_id"] != pid:
        return None
    ws = db.get_record(d["workspace"]["id"], d["workspace"]["version"])
    view = {k: d.get(k) for k in ("id", "version", "city_id", "method", "concept", "products", "facts", "missing",
                                  "dependencies", "next_action", "readiness", "candidates", "counters", "faults",
                                  "status", "error", "prompt_version", "check_version", "proximity", "why_it_fits",
                                  "interpretations", "assumptions", "qloo", "qloo_why")}
    view[d["method"]] = timed(d[d["method"]], d["city_id"], datetime.now(timezone.utc))
    view["workspace"] = ws and {k: ws[k] for k in ("id", "version", "candidates", "attempts", "open_questions", "founder_prefs")}
    block = view.get(d["method"]) or {}
    rows = block.get("shortlist", []) + block.get("venues", []) + block.get("groups", [])
    for r in rows:  # P45-I9: the current location record, so a founder's choice shows everywhere at once
        latest = r.get("location") and db.get_record(f"loc_{_k(pid, d['city_id'], r['lead_key'])}")
        if latest:
            r["location"] = {**r["location"], **{k: latest.get(k) for k in ("state", "lat", "lon", "address", "source",
                                                                              "reason", "options")}, "version": latest["version"]}
    if rows and d.get("proximity") is not None:
        view["proximity"] = proximity(d["city_id"], rows, block.get("anchor"))
        for r in block.get("shortlist", []):
            r["distance_km"] = next((x["km"] for x in view["proximity"]["distances"] if x["lead_key"] == r["lead_key"]), None)

    view["concept"] = concept_of(d)
    view["stale"] = stale_of(pid, d)
    view["products"] = [{k: p[k] for k in ("id", "version", "title", "product_url", "snapshot_id")}
                        for p in (db.get_record(x["id"], x["version"]) for x in d["products"])]
    # The latest active outreach message for this city and method, so the detail can say which questions it already asks.
    from .delivery import email_text  # delivery imports this module
    latest = {k["id"]: k for k in db.records("package", pid)
              if k.get("city_id") == d["city_id"] and k.get("method") == d["method"] and k.get("flow") == "outreach"}
    pkg = next((k for k in reversed(latest.values()) if k.get("status", "active") == "active"), None)
    view["message"] = pkg and {"partner": (pkg.get("partner") or {}).get("name"), "body": email_text(pkg)[1],
                               "start": (pkg.get("timing") or {}).get("start")}
    return view


# ---- Develop this approach (approach_drafter@1, BUILD-PLAN §4.5.6): one structured call over a saved dossier ----
DRAFTER_VERSION = "approach_drafter@2"  # @2: a digital draft is a Google Search ads test (owner, 28 Oct 2026)
DRAFT_CAPS = {"model": 2, "search": 0, "page": 0, "presence": 0, "qloo": 0, "urn": 0, "check": 0, "prose": 1,
              "locate": 0, "profile": 0}
COSTING = {"retail": [("Stock for the trial", "STOCK"), ("Shipping to the shop", "SHIPPING"), ("Margin or consignment terms", "OTHER")],
           "popup": [("Venue hire", "HIRE_FEE"), ("Deposit", "DEPOSIT"), ("Staff hours", "STAFF"), ("Fixtures", "FIXTURE"),
                     ("Shipping stock", "SHIPPING"), ("Permits", "PERMIT")],
           "creator": [("Creator fee", "CREATOR_FEE"), ("Product for the creator", "STOCK")],
           "community": [("Sponsorship or contribution", "OTHER"), ("Product for the event", "STOCK")],
           "digital": [("Media spend", "MEDIA_SPEND"), ("Creative production", "OTHER")]}
DRAFTER = """You turn one saved launch-method investigation into an experiment draft the founder can cost next. You do no
research. Use only the investigation's kept candidates as partners, only the brand's listed products, and only facts
listed as checked. The concept, activities, success measure and audience hypothesis are proposals. Never state prices,
budgets, costs, reach or forecasts (they come next, with costs), and never claim a partner agreed to anything."""


class DraftIn(research.Lenient):
    concept_name: str = ""
    concept_line: str = ""
    activities: list[str] = Field(default_factory=list, description="3-6 concrete steps of the experiment")
    success_measure: str = ""
    audience_hypothesis: str = ""
    partners: list[str] = Field(default_factory=list, description="Names of kept candidates this draft uses")
    products: list[str] = Field(default_factory=list, description="Titles of the brand's products this draft uses")


def draft_prompt(d: dict, ws: dict | None, rationale: str | None) -> str:
    inp = d["input"]
    kept = [c for c in d["candidates"] if c["decision"] == "KEPT"]
    facts = [db.get_record(i) for i in d["facts"]]
    prods = [db.get_record(p["id"], p["version"]) for p in d["products"]]
    return (f"Method: {METHOD_LABELS[d['method']]} · City: {inp['research_input']['name']}\n"
            f"Concept so far: {d['concept']['name']}: {d['concept']['line']}\n"
            f"Kept candidates:\n" + ("\n".join(f"- {c['name']}" for c in kept) or "- none") + "\n"
            f"Checked facts:\n" + ("\n".join(f"- {f['subject_name']}: {f['text']}" for f in facts if f) or "- none") + "\n"
            f"Brand facts:\n" + "\n".join(f"- {f['label']}: {f['value']}" for f in inp["research_input"]["facts"]
                                          if f["state"] in ("SUPPORTED", "STATED")) + "\n"
            f"Products:\n" + ("\n".join(f"- {p['title']}" for p in prods) or "- none") + "\n"
            f"Still unknown:\n" + "\n".join(f"- {x}" for x in [m["text"] for m in d["missing"]]
                                            + [q["text"] for q in (ws or {}).get("open_questions", [])]) + "\n"
            + ("This is a Google Search ads test: the concept, activities and success measure are about search ads "
               "(keywords, ads, clicks to the site, sign-ups or purchases from them). Never TikTok, Meta, Instagram or "
               "any other platform.\n" if d["method"] == "digital" else "")
            + (f"The founder says: {rationale}\n" if rationale else "") + "Return DraftIn.")


def draft_call(ctx: research.Ctx, prompt: str) -> dict:
    agent = Agent(model=make_model(ctx.run.cassette, "develop", research.usage_sink(ctx, "model"), max_tokens=2000,
                                   admit=research.admitter(ctx, "model")), system_prompt=DRAFTER, callback_handler=None)
    out = agent(prompt, structured_output_model=DraftIn, cancel_signal=ctx.run.cancel).structured_output
    return out.model_dump() if out else {}


def ruled_out(pid: str, d: dict) -> str | None:
    if ruled_by_note(pid, d["city_id"], d["method"]):
        return "Your note rules this method out. Edit your note to develop it."
    cr = research.latest_research(pid).get(d["city_id"])
    a = next((a for a in (cr or {}).get("approaches", []) if a["method"] == d["method"]), None)
    if a and research._decided(pid, a).get("status") == "RULED_OUT_BY_YOU":
        return "You ruled this approach out. Undo that to develop it."
    return None


def develop_run(run: Run):
    pid, did, dv = run.project_id, run.params["dossier_id"], run.params["dossier_version"]
    d = db.get_record(did, dv)
    ws = db.get_record(d["workspace"]["id"], d["workspace"]["version"])
    prev = db.get_record(f"drf_{did[4:]}")
    ctx = research.Ctx(run, pid, d["city_id"], dv, d["input"]["research_input"], caps=DRAFT_CAPS, usd_cap=0.03,
                       expected_usd=0.02, forced_usd=0.0, stage="develop", cr_key=f"{pid}:{d['city_id']}:{d['method']}:develop:{(prev or {}).get('version', 0) + 1}")
    sub = run.step("draft", lambda: draft_call(ctx, draft_prompt(d, ws, run.params.get("rationale"))))
    run.step("publish", lambda: publish_draft(ctx, d, ws, sub, run.params.get("rationale")))


HANDLERS["develop"] = develop_run


def publish_draft(ctx: research.Ctx, d: dict, ws: dict | None, sub: dict, rationale: str | None) -> dict:
    """Validator (§4.5.6): partners only from KEPT candidates; products only from the dossier; no money words; every
    unknown carried as a validation task; HYPOTHESIS-LED unless the dossier is EVIDENCED."""
    pid, inp, method, faults = ctx.pid, d["input"], d["method"], []
    kept = {c["name"]: c for c in d["candidates"] if c["decision"] == "KEPT"}
    partners = []
    for name in sub.get("partners", []):
        hit = next((c for n, c in kept.items() if research.same_name(n, name)), None)
        if hit:
            partners.append({"lead_id": hit.get("lead_id"), "name": hit["name"], "participation": "NOT_CONFIRMED"})
        else:
            faults.append({"field": "partners", "reason": f"{name}: not a kept candidate of this investigation; left out"})
    prods = {p["title"]: p for p in (db.get_record(x["id"], x["version"]) for x in d["products"])}
    used = [{"id": prods[t]["id"], "version": prods[t]["version"], "title": t, "snapshot_id": prods[t]["snapshot_id"]}
            for t in sub.get("products", []) if t in prods]
    fields = {"concept.name": sub.get("concept_name") or "", "concept.line": sub.get("concept_line") or "",
              "success_measure": sub.get("success_measure") or "", "audience_hypothesis": sub.get("audience_hypothesis") or "",
              **{f"activities[{i}]": a for i, a in enumerate(sub.get("activities", []))}}
    for k, t in list(fields.items()):
        if t and (MONEY_REACH.search(t) or re.search(r"\b(price|fee|cost)s?\b", t, re.I)):
            faults.append({"field": k, "reason": f"\"{t[:80]}\": money belongs to costing (slice 5); left out"})
            fields[k] = ""
        elif t and method == "digital" and NOT_SEARCH.search(t):
            faults.append({"field": k, "reason": f"\"{t[:80]}\": the digital test is Google Search ads only; left out"})
            fields[k] = ""
    claims = [{**db.get_record(i), "cid": i} for i in d["facts"]]
    checked = check_prose(ctx, inp, fields, [{**c, "subject_name": c["subject_name"]} for c in claims],
                          [{"name": c["name"]} for c in kept.values()], faults)
    name, line = checked["concept.name"], checked["concept.line"]
    if not (name and line):
        name, line = d["concept"]["name"], d["concept"]["line"]
    if method == "retail":
        c = retail_concept({"name": name, "line": line}, next((p["name"] for p in partners), None) or
                           next((c["name"] for c in kept.values()), None))
        name, line = c["name"], c["line"]
    origin = "EVIDENCED" if d["readiness"]["overall"] == "EVIDENCED" else "HYPOTHESIS_LED"
    tasks = validation_tasks(d, ws)
    caveat = (inp["research_input"]["audience"] or {}).get("caveat")
    city = inp["research_input"]
    payload = {
        "source": {"dossier_id": d["id"], "dossier_version": d["version"], "city_id": d["city_id"], "method": method},
        "origin": origin, "label": "EVIDENCED DRAFT" if origin == "EVIDENCED" else "HYPOTHESIS-LED DRAFT",
        "concept": {"name": name, "line": line}, "activities": [checked[f"activities[{i}]"] for i in range(len(sub.get("activities", [])))
                                                               if checked.get(f"activities[{i}]")],
        "success_measure": checked["success_measure"],
        "audience_hypothesis": {"text": checked["audience_hypothesis"], "label": "HYPOTHESIS", "caveat": caveat},
        "partners": partners, "products": used, "dependencies": d.get("dependencies", []),
        "validation_tasks": tasks,
        "costing_inputs": [{"item": i, "kind": k, "state": "UNKNOWN"} for i, k in COSTING[method]],
        "claim_ids": d["facts"], "founder_rationale": rationale, "faults": faults, "status": "DRAFT",
        "prompt_version": DRAFTER_VERSION, "vc": "I"}
    if method == "digital":
        dg = d["digital"]
        payload["digital_outline"] = {
            "channel": dg["channel"], "objective": dg["objective"],
            "geography": {**dg["geography"], "city": city["name"], "country": city["country"]},
            "targeting_description": [{"element": t["element"], "basis": t["basis"]} for t in dg["targeting"]],
            "currency": markets.CURRENCY.get(city["country"]), "horizon_days": dg["horizon_days"],
            "scenario_inputs_required": dg["scenario_inputs_required"]}
    rec = db.put_record("draft", pid, payload, rid=f"drf_{d['id'][4:]}")
    research._activity(ctx.run, f"Develop {METHOD_LABELS[method].lower()} in {city['name']}", "approach drafter",
                       f"{payload['label'].lower()} · {len(payload['validation_tasks'])} validation tasks")
    return {"id": rec["id"], "version": rec["version"]}


PENDING_WHY = {"NO_RECURRING_ACTIVITY": "no recurring schedule quoted", "VENUE_ONLY": "a venue; no organizer page found",
               "NO_EVENT_USE_EVIDENCE": "no quote shows it hires space or hosts events",
               "NO_ASSORTMENT_EVIDENCE": "no outside brands or stocking statement found",
               "IDENTITY_UNCONFIRMED": "identity not confirmed by two agreeing signals", "NOT_LOCAL": "not shown to be in the city",
               "OFF_TOPIC": "no observed content about what you sell"}


def validation_tasks(d: dict, ws: dict | None) -> list[dict]:
    """Every unknown in the validated dossier (P45-I5, O8): each kept or pending candidate's shortfalls by name, the
    method's open questions and dependencies, then the model's own "missing" items unless they name a candidate the
    validator dropped. Deduplicated by wording; each task keeps its subject."""
    dropped = [f["reason"].split(":")[0] for f in d.get("faults", []) if f.get("field") in ("candidate", "lead")]
    tasks = []
    for c in d["candidates"]:
        if c["decision"] not in ("KEPT", "PENDING") or not c.get("axes"):
            continue
        if c["decision"] == "PENDING" and c.get("reason_code") in PENDING_WHY:
            tasks.append({"text": f"{c['name']}: {PENDING_WHY[c['reason_code']]}", "from": "candidate", "subject": c["name"]})
        tasks += [{"text": f"{c['name']}: {r}", "from": "candidate", "subject": c["name"]} for r in c["axes"]["reasons"]]
    tasks += [{"text": x["text"], "from": "dependency"} for x in d.get("dependencies", []) if x.get("state") != "STATED"]
    tasks += [{"text": q["text"], "from": "question"} for q in (ws or {}).get("open_questions", [])]
    tasks += [{"text": m["text"], "from": "missing"} for m in d["missing"]
              if not any(n and research._in(n, m["text"]) >= 0 for n in dropped)]
    seen, out = set(), []
    for t in tasks:
        if _norm(t["text"]) not in seen:
            seen.add(_norm(t["text"]))
            out.append(t)
    return out


def develop(pid: str, did: str, base_version: int | None, rationale: str | None) -> dict:
    d = db.get_record(did)
    if not d or d["project_id"] != pid:
        raise Refused(404, "No such investigation.")
    if base_version is not None and base_version != d["version"]:
        raise Refused(409, "The investigation changed since you opened it.")
    if d["status"] not in ("complete", "partial"):
        raise Refused(422, "Only a finished investigation can be developed.")
    if why := ruled_out(pid, d):
        raise Refused(422, why)
    rid = runner.start(pid, "develop", f"{did}:{d['version']}:{_k(rationale)}",
                       {"dossier_id": did, "dossier_version": d["version"], "rationale": rationale})
    return {"run_id": rid, "draft_id": f"drf_{did[4:]}"}


EDITABLE = ("concept", "activities", "success_measure", "audience_hypothesis")


def edit_draft(pid: str, rid: str, base_version: int, fields: dict) -> dict:
    """A founder edit makes v+1; edited fields are YOU SAID."""
    cur = db.get_record(rid)
    if not cur or cur["project_id"] != pid:
        raise Refused(404, "No such draft.")
    if base_version != cur["version"]:
        raise Refused(409, "The draft changed since you opened it.")
    bad = set(fields) - set(EDITABLE)
    if bad:
        raise Refused(422, f"Not editable: {', '.join(sorted(bad))}")
    payload = {k: v for k, v in cur.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")}
    if "audience_hypothesis" in fields:
        fields = {**fields, "audience_hypothesis": {**payload["audience_hypothesis"], "text": fields["audience_hypothesis"]}}
    payload.update(fields)
    payload["you_said"] = sorted(set(payload.get("you_said", [])) | set(fields))
    rec = db.put_record("draft", pid, payload, rid=rid)
    return {"version": rec["version"]}


def draft_view(pid: str, rid: str) -> dict | None:
    """The draft with its handoff views (§4.5.7) and "Built on research that changed" (P45-R5), computed now."""
    from . import delivery  # delivery builds on drafts; imported here to keep the dependency one-way at import time
    dr = db.get_record(rid)
    if not dr or dr["project_id"] != pid:
        return None
    src = db.get_record(dr["source"]["dossier_id"], dr["source"]["dossier_version"])
    stale = stale_of(pid, src) + [f"Product changed: {p['title']}" for p in dr["products"]
                                  if (db.get_record(p["id"]) or {}).get("version") != p["version"]]
    latest = db.get_record(dr["source"]["dossier_id"])
    if latest["version"] != src["version"]:
        stale.append("A newer investigation of this method exists")
    kept_leads = {p["lead_id"]: db.get_record(p["lead_id"]) for p in dr["partners"] if p.get("lead_id")}
    return {**{k: dr[k] for k in dr if k not in ("inputs", "kind", "project_id")},
            "concept": concept_of({**src, "concept": dr["concept"]}),
            "stale": stale,
            "concept_preview_input": {"draft_id": dr["id"], "treatment": dr["source"]["method"],
                                      "headline": dr["concept"]["name"], "audience_message": dr["audience_hypothesis"]["text"],
                                      "execution": dr["activities"], "products": dr["products"],
                                      "partner_names": [p["name"] for p in dr["partners"]],
                                      "participation": "not confirmed", "image_rights": "none"},
            "package": delivery.package_for_draft(pid, rid),
            "packages": delivery.packages_for_draft(pid, rid),
            "outreach_targets": [{"lead_id": lid, "name": (lead or {}).get("name"), "contact": None,
                                  "inquiry_questions": [t["text"] for t in dr["validation_tasks"] if t["from"] == "question"]}
                                 for lid, lead in kept_leads.items()]}


# ---- views and founder inputs for the interface (CONTRACTS B6.5 interfaces) ----
def summaries(pid: str) -> dict:
    """{city_id: {method: {...}}} for the approach cards and the matrix: investigation state, readiness, draft."""
    out: dict = {}
    for r in db.q("SELECT city_id, method, MAX(version) v FROM specialist_runs WHERE project_id=? GROUP BY city_id, method", pid):
        row = spec_row(pid, r["city_id"], r["method"], r["v"])
        d = db.get_record(f"dos_{pid}_{r['city_id']}_{r['method']}")
        dr = d and db.get_record(f"drf_{d['id'][4:]}")
        out.setdefault(r["city_id"], {})[r["method"]] = {
            "status": row["status"], "dossier_id": d and d["id"], "readiness": d and d["readiness"]["overall"],
            "axes": d and {k: d["readiness"].get(k) for k in ("fit", "grounding", "operational", "reasons")},
            "next_action": d and d["next_action"], "draft_id": dr and dr["id"], "draft_label": dr and dr["label"]}
    return out


def investigation_view(pid: str, cid: str, method: str) -> dict:
    """Everything the detail page needs, in one read: the dossier (if any), the run state, the cost and why not."""
    row = spec_row(pid, cid, method)
    d = db.get_record(f"dos_{pid}_{cid}_{method}")
    run = row and row["run_id"] and db.q("SELECT id, status, detail FROM runs WHERE id=?", row["run_id"])
    note = ruled_by_note(pid, cid, method)
    dr = d and db.get_record(f"drf_{d['id'][4:]}")
    cost = {"cap_usd": SPECS[method].usd, "expected_usd": min(SPEC_EXPECTED, SPECS[method].usd)} if method in SPECS else None
    return {"city_id": cid, "city": markets.CITIES[cid]["name"], "method": method, "label": METHOD_LABELS.get(method, method),
            "researched": cid in research.latest_research(pid), "ruled_by_note": note, "cost": cost,
            "status": row["status"] if row else None, "version": row["version"] if row else None,
            "run": run and {"id": run[0]["id"], "status": run[0]["status"],
                            "message": json.loads(run[0]["detail"] or "{}").get("message")},
            "can_continue": bool(row and row["status"] in ("stopped", "failed") and row["input"]),
            "preferences": (lambda w: w and {"workspace_id": w["id"], "version": w["version"], **w["founder_prefs"]})(
                workspace(pid, cid, method)),
            "dossier": d and dossier_view(pid, d["id"]), "draft": dr and draft_view(pid, dr["id"]),
            "develop_run": d and next(({"status": r["status"], "message": json.loads(r["detail"] or "{}").get("message")}
                                       for r in db.q("SELECT status, detail FROM runs WHERE project_id=? AND kind='develop' "
                                                     "AND input_key LIKE ? ORDER BY started_at DESC, rowid DESC LIMIT 1",
                                                     pid, f"{d['id']}:%")), None),
            "founder_creators": [{"id": x["id"], **x["creator"]} for x in db.records("lead", pid)
                                 if x.get("city_id") == cid and (x.get("creator") or {}).get("identity") == "YOU_SAID"]
            if method == "creator" else []}


def resume(pid: str, cid: str, method: str) -> dict:
    """Continue a stopped or failed investigation: same version and pinned input; completed tool calls aren't repeated."""
    row = spec_row(pid, cid, method)
    if not row or row["status"] not in ("stopped", "failed") or not row["input"]:
        raise Refused(409, "Nothing to continue here.")
    _set_spec(pid, cid, method, row["version"], status="queued")
    rid = runner.start(pid, "specialist", f"{cid}:{method}:{row['version']}:resume:{db.now()}",
                       {"city_id": cid, "method": method, "version": row["version"]})
    return {"run_id": rid, "dossier_id": f"dos_{pid}_{cid}_{method}"}


def edit_workspace(pid: str, wid: str, base_version: int, include: list[str] | None, exclude: list[str] | None,
                   rationale: str | None) -> dict:
    """PATCH …/workspaces/{id} (YOU SAID): your include and exclude lists, kept by name for the next investigation of
    this method in this city. Nothing re-runs; the current dossier is flagged as changed."""
    ws = db.get_record(wid)
    if not ws or ws["project_id"] != pid:
        raise Refused(404, "No such workspace.")
    if ws["version"] != base_version:
        raise Refused(409, "Your preferences changed since you opened them.")
    prefs = dict(ws["founder_prefs"])
    for k, v in (("include", include), ("exclude", exclude), ("rationale", rationale)):
        if v is not None:
            prefs[k] = [x.strip() for x in v if x.strip()] if isinstance(v, list) else (v.strip() or None)
    payload = {k: v for k, v in ws.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")}
    rec = db.put_record("workspace", pid, {**payload, "founder_prefs": prefs, "prefs_by": "founder"}, rid=wid)
    return {"version": rec["version"]}


def add_creator(pid: str, cid: str, url: str, note: str | None) -> dict:
    """POST …/leads: a founder-supplied creator profile (YOU SAID). Canonicalised, never fetched here; the next creator
    investigation receives it as an include."""
    p = creators.canonical(url)
    if not p:
        raise Refused(422, "That isn't a TikTok, YouTube or Instagram profile address.")
    key = f"creator|{p['canonical_url'].lower()}"
    rec = db.put_record("lead", pid, {"key": key, "name": p["handle"], "origin": "YOU_SAID", "city_id": cid,
                                      "creator": {**p, "display_name": p["handle"], "identity": "YOU_SAID",
                                                  "identity_evidence": [{"kind": "YOU_SAID", "source_id": None}],
                                                  "location": {"state": "YOU_SAID", "text": markets.CITIES[cid]["name"]},
                                                  "metrics": [], "unused_figures": [], "note": note},
                                      "willingness": "UNKNOWN"}, rid=f"lead_{_k(pid, cid, key)}")
    return {"lead_id": rec["id"]}


def choose_location(pid: str, lead_id: str, base_version: int, option: int) -> dict:
    """PUT …/leads/{id}/location: the founder chooses among AMBIGUOUS options (YOU_CHOSE)."""
    lead = db.get_record(lead_id)
    if not lead or lead["project_id"] != pid:
        raise Refused(404, "No such lead.")
    rid = f"loc_{_k(pid, lead['city_id'], lead['key'])}"
    cur = db.get_record(rid)
    if not cur:
        raise Refused(404, "This lead has no location to choose.")
    if cur["version"] != base_version:
        raise Refused(409, "The location changed since you opened it.")
    if not 0 <= option < len(cur.get("options") or []):
        raise Refused(422, "Choose one of the listed places.")
    o = cur["options"][option]
    rec = db.put_record("lead_location", pid, {**{k: v for k, v in cur.items() if k not in ("id", "version", "kind", "project_id",
                                                                                          "created_at", "inputs")},
                                               "state": "PLACED", "lat": o["lat"], "lon": o["lon"], "address": o["address"],
                                               "source": "YOU_CHOSE", "reason": "You chose this place", "at": db.now()}, rid=rid)
    return {"version": rec["version"]}
