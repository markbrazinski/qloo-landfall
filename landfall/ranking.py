"""Slice 3.5 · the goal, current presence, stand-ins, Qloo's single-query ranking, audiences, related-brand stores and
the decision rule (BUILD-PLAN slice 3.5; CONTRACTS B3.5).

Records: CurrentPresence (store search, located checks, founder overrides; city and country chips are recomputed from it
and the current brief), StandIns (no-match only), QlooRanking, AudienceProfile, AudienceUrns, ComparablePresence.
Provider stage strings: `presence`, `standins`, `rank`, `audience`, `comparables` (Qloo).
The ranking is one query and its pages only: positions are never merged with, or compared to, another request.
"""
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import db, markets, qloo, runner
from .cassette import key_of
from .ledger import BudgetExhausted
from .runner import HANDLERS, Run

SCOPES = ("international", "domestic", "both")
PURPOSES = ("presence", "stockists", "test_demand")
SCOPE_WORDS = {"international": "internationally", "domestic": "domestically", "both": "both at home and abroad"}
PURPOSE_WORDS = {"presence": "expand my market presence", "stockists": "find stores and stockists to carry us",
                 "test_demand": "test demand before committing"}
STORE_PAGES = 4
PAGE = 50
STORE_KM = 30
NEAR_MI = 15
NEAR_KM = NEAR_MI * 1.609344
WINDOW = 4
CAPS = {"all": markets.CAP, "home": 2, "abroad": 3}
CHECKS_PER_ROUND = 6  # located checks: the cities about to be recommended, then at most this many replacements

_DEST = json.loads((markets.DATA / "destinations.json").read_text())
DESTINATIONS = _DEST["cities"]
DEST_VERSION = _DEST["version"]
CITY_OF_DEST = {v["qloo_id"]: cid for cid, v in DESTINATIONS.items() if "qloo_id" in v}
_ENTRY = json.loads((markets.DATA / "entry.json").read_text())
_LANG = json.loads((markets.DATA / "languages.json").read_text())
_GENRES = json.loads((markets.DATA / "brand_genres.json").read_text())
REFERENCE = {"destinations": DEST_VERSION, "entry": _ENTRY["version"], "languages": _LANG["version"],
             "brand_genres": _GENRES["version"]}


# ---- intake ----
TRACKING = re.compile(r"^(utm_.*|gclid|gbraid|wbraid|gad_.*|tw_.*|fbclid)$", re.I)


def strip_tracking(url: str) -> str:
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    p = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))


# ---- the brand and its home ----
def brand_entity(pid: str) -> dict | None:
    """The matched Qloo brand candidate (with its raw `brand_tags`), or None on the no-match route."""
    ident = db.latest("identity", pid)
    if not ident or ident["status"] != "decided" or not isinstance(ident["decision"], dict):
        return None
    return next((c for c in ident["candidates"] if c["qloo_id"] == ident["decision"]["entity_id"]), None)


def home_for(pid: str) -> dict:
    """The brief's home market, unless the founder set the home country (YOU SAID)."""
    from .scan import home_of
    home = home_of(db.latest("brief", pid))
    fi = db.latest("founder_input", pid) or {}
    iso = fi.get("home_country")
    if iso and iso != home.get("country"):
        home = {**markets.parse_home(markets.COUNTRY_NAME[iso]), "provenance": "YOU_SAID", "claim_id": None}
    return home


def goal_of(pid: str) -> dict:
    fi = db.latest("founder_input", pid) or {}
    return {"scope": fi.get("scope") or "international", "purpose": fi.get("purpose") or "presence",
            "leave_out_presence": fi.get("leave_out_presence", True), "home_country": fi.get("home_country")}


# ---- current presence (R3) ----
_NOT = re.compile(r"\b(not|no longer|never|n't|coming soon|soon|planned|plan to|will open)\b", re.I)


def _site_facts(brief: dict | None) -> list[dict]:
    """Where-sold facts that count: SUPPORTED or STATED only (TO_CHECK is shown, never used)."""
    facts = [db.get_record(i) for i in (brief or {}).get("facts", [])]
    return [f for f in facts if (f.get("subject") or {}).get("key") == "where_sold"]


def _positive_clauses(text: str) -> list[str]:
    """Clauses of a where-sold fact that don't negate or defer ("not yet in Paris", "opening soon in Oslo")."""
    return [c for c in re.split(r"[.;\n]", text or "") if c.strip() and not _NOT.search(c)]


def site_observations(brief: dict | None) -> tuple[dict, dict, list]:
    """(cities {city_id: claim_id}, countries {iso: claim_id}, shown_not_used [{claim_id, label, value, state}])."""
    cities, countries, unused = {}, {}, []
    for f in _site_facts(brief):
        if f["state"] not in ("SUPPORTED", "STATED"):
            unused.append({"claim_id": f["id"], "label": f["label"], "value": f["value"], "state": f["state"]})
            continue
        for clause in _positive_clauses(f["value"]):
            for cid in markets.names_in(clause, markets.ORDER):
                cities.setdefault(cid, f["id"])
            for iso in markets._countries_in(clause):
                countries.setdefault(iso, f["id"])
    return cities, countries, unused


def nearest_city(lat: float | None, lon: float | None, km: float = STORE_KM) -> str | None:
    """The catalog city within `km` of a point, nearest first."""
    if lat is None or lon is None:
        return None
    best = min(markets.ORDER, key=lambda c: markets.km(lat, lon, markets.CITIES[c]["lat"], markets.CITIES[c]["lon"]))
    c = markets.CITIES[best]
    return best if markets.km(lat, lon, c["lat"], c["lon"]) <= km else None


def store_match(e: dict, brand: str) -> bool:
    """Prefix-plus-retail (P35-8): the name equals the brand or starts with the brand and a space, and the place
    classifies as retail by channels@1 (its genre or category ids)."""
    name, b = " ".join((e.get("name") or "").split()).casefold(), " ".join(brand.split()).casefold()
    if not b or not (name == b or name.startswith(b + " ")):
        return False
    ids = [t.get("tag_id") or t.get("id") or "" for t in e.get("tags") or [] if isinstance(t, dict)]
    kinds = [i.split(":place:", 1)[1] for i in ids if i.startswith(("urn:tag:genre:place:", "urn:tag:category:place:"))]
    return any(markets.genre_class(k) == "retail" for k in kinds)


def _overrides(rec: dict | None) -> dict:
    o = (rec or {}).get("overrides") or {}
    return {k: list(o.get(k, [])) for k in ("add_cities", "remove_cities", "leave_out_countries", "restore_countries")}


def presence_of(pid: str) -> dict:
    """The CurrentPresence view: city observations exclude, country observations inform (truth table, CONTRACTS B3.5).
    Recomputed from the stored record, the current brief, the brand's key-market tags and the home; founder removals
    and restores persist by id across recomputation."""
    rec = db.latest("current_presence", pid)
    brief = db.latest("brief", pid)
    home, ent = home_for(pid), brand_entity(pid)
    o = _overrides(rec)
    cities: dict[str, list] = {}
    for s in (rec or {}).get("stores", []):
        if s.get("city_id"):
            cities.setdefault(s["city_id"], []).append({"kind": "QLOO_STORE", "ref": s["qloo_id"], "name": s["name"]})
    for c in (rec or {}).get("located_checks", []):
        if c["status"] == "store_found":
            cities.setdefault(c["city_id"], []).append({"kind": "QLOO_STORE", "ref": c.get("qloo_id"), "name": c.get("name"),
                                                        "located": True})
    site_c, site_n, unused = site_observations(brief)
    for cid, claim in site_c.items():
        cities.setdefault(cid, []).append({"kind": "YOUR_SITE", "ref": claim})
    home_city = nearest_city(home.get("lat"), home.get("lon")) if home.get("note") == "placed" else None
    if home_city:
        cities.setdefault(home_city, []).append({"kind": "HOME", "ref": home.get("claim_id")})
    for cid in o["add_cities"]:
        cities.setdefault(cid, []).append({"kind": "YOU_SAID", "ref": None})
    countries: dict[str, list] = {}
    regions = []
    for t in (ent or {}).get("brand_tags", []):
        if (t.get("type") or "").startswith("urn:tag:key_market"):
            iso = markets.country_of(t.get("name") or "")
            if iso:
                countries.setdefault(iso, []).append({"kind": "QLOO_KEY_MARKET", "ref": t["qloo_id"]})
            else:
                regions.append(t.get("name"))  # "Europe" stays text, never a country
    for iso, claim in site_n.items():
        countries.setdefault(iso, []).append({"kind": "YOUR_SITE", "ref": claim})
    if home.get("country"):
        countries.setdefault(home["country"], []).append({"kind": "HOME_COUNTRY", "ref": home.get("claim_id")})
    for iso in o["leave_out_countries"]:
        countries.setdefault(iso, [])
    store = (rec or {}).get("store_search") or {"status": "not_applicable" if ent is None else "pending", "pages": 0,
                                                 "rows": 0, "request_ids": []}
    return {"id": rec and rec["id"], "version": rec["version"] if rec else 0,
            "cities": [{"city_id": cid, "name": markets.CITIES[cid]["name"], "country": markets.CITIES[cid]["country"],
                        "sources": src, "excluded": cid not in o["remove_cities"]}
                       for cid, src in sorted(cities.items(), key=lambda kv: markets.ORDER.index(kv[0]))],
            "removed_cities": [c for c in o["remove_cities"] if c not in cities],
            "countries": [{"iso": iso, "name": markets.COUNTRY_NAME.get(iso, iso), "sources": src,
                           "leave_out": iso in o["leave_out_countries"] and iso not in o["restore_countries"]}
                          for iso, src in countries.items()],
            "unresolved_regions": regions, "site_not_used": unused, "store_search": store,
            "located_checks": (rec or {}).get("located_checks", []), "overrides": o,
            "home": {"country": home.get("country"), "city": home.get("city"), "note": home.get("note"),
                     "provenance": home.get("provenance")}}


def coverage_line(store: dict, brand: str) -> str:
    n = store.get("rows_matched", 0)
    return {"complete": f"All {n} {brand} store{'s' if n != 1 else ''} Qloo lists" if n else f"Qloo lists no {brand} store",
            "capped": f"Qloo returned {n}+ {brand} stores; more may exist",
            "partial": f"Qloo's store search stopped partway ({n} found); more may exist",
            "failed": "Qloo's store search failed; we don't know where your stores are",
            "not_applicable": "No Qloo store search: Qloo doesn't know your brand",
            "pending": "Qloo's store search hasn't run yet"}[store["status"]]


def _search_rows(out: dict) -> list[dict] | None:
    """`/search` place rows of a readable successful answer; None when the answer can't be read, so an unreadable page
    is never a readable empty page (review slice 3.5, finding 2)."""
    if out.get("status") != 200:
        return None
    try:
        res = json.loads(out.get("body") or "").get("results")
    except (ValueError, AttributeError):
        return None
    if not isinstance(res, list) or not all(isinstance(e, dict) and e.get("entity_id") for e in res):
        return None
    return res


def _store_search(run: Run, pid: str, brand: str) -> dict:
    """Own stores: `/search` pages until a page has fewer than 50 rows, at most 4. Coverage is recorded, never implied."""
    stores, ids, pages, rows, status = [], [], 0, 0, "complete"
    for page in range(1, STORE_PAGES + 1):
        params = {"query": brand, "types": "urn:entity:place", "take": PAGE, **({"page": page} if page > 1 else {})}
        ids.append(request_id("/search", params))
        try:
            out = qloo.get("/search", params, run.cassette, "presence", pid, run.id,
                           cache_ok=lambda o: _search_rows(o) is not None)
        except BudgetExhausted:
            status = "failed" if page == 1 else "partial"
            break
        ents = _search_rows(out)
        if ents is None:
            status = "failed" if page == 1 else "partial"
            break
        pages += 1
        rows += len(ents)
        for e in ents:
            if store_match(e, brand):
                loc = e.get("location") or {}
                stores.append({"qloo_id": e["entity_id"], "name": e.get("name"), "lat": loc.get("lat"), "lon": loc.get("lon"),
                               "city_id": nearest_city(loc.get("lat"), loc.get("lon")), "page": page})
        if len(ents) < PAGE:
            break
    else:
        status = "capped"
    seen = set()
    stores = [s for s in stores if not (s["qloo_id"] in seen or seen.add(s["qloo_id"]))]
    return {"status": status, "pages": pages, "rows": rows, "rows_matched": len(stores), "request_ids": ids,
            "stores": stores}


def presence_step(run: Run, pid: str) -> dict:
    """Writes CurrentPresence for the current identity (founder overrides carried over)."""
    ident = db.latest("identity", pid)
    prev = db.latest("current_presence", pid)
    if prev and prev.get("identity") == {"id": ident["id"], "version": ident["version"]}:
        return {"version": prev["version"]}
    ent = brand_entity(pid)
    if ent:
        found = _store_search(run, pid, ent["name"])
        stores = found.pop("stores")
        from .brand import _activity
        _activity(run, f"Find {ent['name']} stores in Qloo", "Qloo place search",
                  f"{found['pages']} page{'s' if found['pages'] != 1 else ''} · {found['rows_matched']} stores · "
                  f"{len({s['city_id'] for s in stores if s['city_id']})} catalog cities · {found['status']}",
                  "FAILED" if found["status"] == "failed" else "DONE", role="Market scout", stage="Markets")
    else:
        found, stores = {"status": "not_applicable", "pages": 0, "rows": 0, "rows_matched": 0, "request_ids": []}, []
    rec = put("current_presence", pid, {"identity": {"id": ident["id"], "version": ident["version"]},
                                        "store_search": found, "stores": stores, "located_checks": [],
                                        "overrides": _overrides(prev)},
              [{"id": ident["id"], "version": ident["version"], "keys": ["decision"]}], f"cp_{pid}")
    return {"version": rec["version"]}


def put(kind: str, pid: str, payload: dict, inputs: list, rid: str) -> dict:
    return db.put_record(kind, pid, payload, inputs=inputs, rid=rid)


def request_id(path: str, params: dict) -> str:
    """The exchange key of a Qloo request (as the cassette and provider cache compute it)."""
    return key_of("qloo", path, {"path": path, "params": {k: str(v) for k, v in sorted(params.items())}})


# ---- stand-ins (R4) ----
_CHARITY = re.compile(r"\b(foundation|charity|charitable|non-?profit)\b", re.I)


def genre_for(brief: dict | None) -> str | None:
    """brand_genres@1: the first word group found in "What you sell"; nothing found → None ("category not matched")."""
    text = " ".join(f["value"] for f in (db.get_record(i) for i in (brief or {}).get("facts", []))
                    if (f.get("subject") or {}).get("key") == "products").casefold()
    for words, genre in _GENRES["words"]:
        if any(re.search(rf"\b{re.escape(w)}\b", text) for w in words):
            return genre
    return None


def is_charity(e: dict) -> bool:
    # ponytail: "trust" only in the name; in a description it is usually marketing ("a brand you can trust")
    desc = ((e.get("properties") or {}).get("short_description") or "")
    return bool(_CHARITY.search(e.get("name") or "") or _CHARITY.search(desc) or re.search(r"\btrust\b", e.get("name") or "", re.I))


def standins_step(run: Run, pid: str) -> dict:
    prof = db.latest("profile", pid)
    prev = db.latest("standins", pid)
    if prev and prev["status"] != "failed":
        return {"version": prev["version"]}  # founder edits (even removing all) are kept; never re-picked silently
    tags = [s["qloo_id"] for s in prof.get("signals", []) if s.get("included") and s["kind"] == "tag"]
    genre = genre_for(db.latest("brief", pid))
    params = {"filter.type": "urn:entity:brand", "signal.interests.tags": ",".join(tags), "take": 10,
              **({"filter.tags": genre} if genre else {})}
    status, brands, excluded = "none", [], []
    if tags:
        try:
            out = qloo.get("/v2/insights", params, run.cassette, "standins", pid, run.id,
                           cache_ok=lambda o: _rank_rows(o) is not None)
        except BudgetExhausted:
            out = {"status": 0}
        ents = _rank_rows(out)
        if ents is None:  # unreadable is failed (Retry), never "no brand with a similar following"
            status = "failed"
        else:
            for i, e in enumerate(ents, 1):
                (excluded if is_charity(e) else brands).append(
                    {"qloo_id": e["entity_id"], "name": e.get("name"), "rank_in_request": i, "origin": "QLOO",
                     **({"reason": "charity"} if is_charity(e) else {})})
            brands = brands[:3]
            status = "ok" if brands else "none"
    from .brand import _activity
    _activity(run, "Pick brands with a similar following", "Qloo brand insights",
              f"{', '.join(b['name'] for b in brands) or 'none'}" + (f" · {len(excluded)} charities left out" if excluded else "")
              + ("" if genre else " · category not matched"), "FAILED" if status == "failed" else "DONE",
              role="Market scout", stage="Markets")
    rec = put("standins", pid, {"brands": brands, "basis_tags": tags, "genre": genre, "excluded": excluded,
                                "status": status, "request_id": request_id("/v2/insights", params) if tags else None},
              [{"id": prof["id"], "version": prof["version"], "keys": ["signals"]}], f"si_{pid}")
    return {"version": rec["version"], "status": status}


# ---- the ranking signal ----
def ranking_signal(pid: str) -> dict | None:
    """The brand entity (matched), or the stand-ins (no match). None when there is nothing to rank with."""
    ent = brand_entity(pid)
    if ent:
        return {"kind": "brand", "entity_ids": [ent["qloo_id"]], "names": [ent["name"]],
                "label": f"people who like {ent['name']}", "standins": None}
    si = db.latest("standins", pid)
    if not si or not si["brands"]:
        return None
    names = [b["name"] for b in si["brands"]]
    return {"kind": "standins", "entity_ids": [b["qloo_id"] for b in si["brands"]], "names": names,
            "label": f"people who like {join(names)} (stand-ins)", "standins": {"id": si["id"], "version": si["version"]}}


def signal_inputs(pid: str, sig: dict | None) -> list[dict]:
    """What a ranking or audience record consumed as its signal: the StandIns version, or the matched identity."""
    if not sig:
        return []
    if sig["standins"]:
        return [sig["standins"] | {"keys": ["brands"]}]
    ident = db.latest("identity", pid)
    return [{"id": ident["id"], "version": ident["version"], "keys": ["decision"]}]


def join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def signal_key(sig: dict | None) -> list | None:
    return sorted(sig["entity_ids"]) if sig else None


# ---- candidates (the truth table) ----
def candidates(pid: str) -> dict:
    """Mapped catalog cities that are not presence, not outside the goal and not left out. Located-check stores count
    as presence. `needs_home`: a domestic or both goal with no home country never ranks (the scope is not broadened)."""
    g = goal_of(pid)
    pres = presence_of(pid)
    fi = db.latest("founder_input", pid) or {}
    home = pres["home"]["country"]
    here = {c["city_id"] for c in pres["cities"] if c["excluded"]}
    left = {c["iso"] for c in pres["countries"] if c["leave_out"]}
    typed_out = markets.left_out(fi.get("exclude"))
    reasons, ids = {}, []
    for cid in markets.ORDER:
        country = markets.CITIES[cid]["country"]
        if "unmapped" in DESTINATIONS[cid]:
            reasons[cid] = "NOT_RANKED"
        elif cid in here and g["leave_out_presence"]:
            reasons[cid] = "ALREADY_HERE"
        elif country in left or cid in typed_out:
            reasons[cid] = "LEFT_OUT_BY_YOU"
        elif home and g["scope"] == "international" and country == home:
            reasons[cid] = "OUTSIDE_GOAL"
        elif home and g["scope"] == "domestic" and country != home:
            reasons[cid] = "OUTSIDE_GOAL"
        else:
            ids.append(cid)
    needs_home = not home and g["scope"] in ("domestic", "both")
    ids = [] if needs_home else sorted(ids)
    return {"city_ids": ids, "hash": hashlib.sha256(",".join(ids).encode()).hexdigest()[:16], "excluded": reasons,
            "needs_home": needs_home, "home_country": home, "scope": g["scope"],
            "home_unknown_international": not home and g["scope"] == "international"}


# ---- QlooRanking (R1) ----
def _rank_rows(out: dict) -> list[dict] | None:
    """Well-formed destination rows of a successful answer, or None when the answer can't be read."""
    if out.get("status") != 200:
        return None
    try:
        body = json.loads(out.get("body") or "")
        ents = body["results"]["entities"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(ents, list):
        return None
    return [e for e in ents if isinstance(e, dict) and e.get("entity_id")]


def _ranking_rows(out: dict) -> list[dict] | None:
    """Destination rows only when every row carries a finite numeric affinity; anything else is unreadable, never an
    order (review slice 3.5, finding 1)."""
    rows = _rank_rows(out)
    if rows is None:
        return None
    for e in rows:
        a = (e.get("query") or {}).get("affinity") if isinstance(e.get("query"), dict) else None
        if isinstance(a, bool) or not isinstance(a, (int, float)) or not math.isfinite(a):
            return None
    return rows


def _flat(affinity: dict) -> bool:
    return len(affinity) >= 3 and len(set(affinity.values())) < 2


def _cacheable_rank(out: dict) -> bool:
    """Only a readable answer with at least two distinct values is reused; Retry must really ask again."""
    rows = _ranking_rows(out)
    return bool(rows) and (len(rows) < 3 or len({e["query"]["affinity"] for e in rows}) >= 2)


def rank_query(run: Run, pid: str, sig: dict, cand: dict) -> dict:
    """One query and its pages only (identical parameters except `page`); never chunked, merged or compared."""
    dest_ids = [DESTINATIONS[c]["qloo_id"] for c in cand["city_ids"]]
    base = {"filter.type": "urn:entity:destination", "signal.interests.entities": ",".join(sig["entity_ids"]),
            "filter.results.entities": ",".join(dest_ids), "take": PAGE}
    positions, affinity, ignored, ids = {}, {}, [], []
    state, failed_page = None, None
    max_pages = max(1, -(-len(dest_ids) // PAGE))
    for page in range(1, max_pages + 1):
        params = {**base, "page": page}
        ids.append(request_id("/v2/insights", params))
        try:
            out = qloo.get("/v2/insights", params, run.cassette, "rank", pid, run.id, cache_ok=_cacheable_rank)
        except BudgetExhausted:
            if page == 1:
                state = "denied"
            failed_page = page
            break
        rows = _ranking_rows(out)
        if rows is None:
            failed_page = page
            if page == 1:
                state = "failed"
            break
        for e in rows:
            cid = CITY_OF_DEST.get(e["entity_id"])
            if cid not in cand["city_ids"] or cid in positions:
                ignored.append({"entity_id": e["entity_id"], "name": e.get("name"),
                                "why": "repeated" if cid in positions else "not a candidate"})
                continue
            positions[cid] = len(positions) + 1
            affinity[cid] = (e.get("query") or {}).get("affinity")
        if len(rows) < PAGE:
            break
    if state is None:  # flat first: pages that came back without a signal never become a partial order
        state = ("flat" if _flat(affinity) else "ranked_partial" if failed_page else "empty" if not positions
                 else "ranked")
    if state in ("flat", "empty", "failed", "denied"):
        positions = {} if state != "flat" else positions
    unranked = {cid: ("beyond_failed_page" if failed_page else "omitted") for cid in cand["city_ids"] if cid not in positions}
    unranked.update({cid: "unmapped" for cid, r in cand["excluded"].items() if r == "NOT_RANKED"})
    return {"state": state, "positions": positions if state in ("ranked", "ranked_partial") else {},
            "unranked": unranked, "request_ids": ids, "ignored": ignored, "affinity": affinity,
            "pages": len(ids) - (1 if failed_page else 0)}


def rank_step(run: Run, pid: str) -> dict:
    """Reuses the latest QlooRanking when its signal and candidate set are current; otherwise ranks once."""
    sig, cand = ranking_signal(pid), candidates(pid)
    prev = db.latest("qloo_ranking", pid)
    if prev and ranking_current(prev, sig, cand) and not run.params.get("rerank"):  # Retry ranking is explicit
        return {"id": prev["id"], "version": prev["version"], "reused": True}
    if cand["needs_home"]:
        res = {"state": "needs_home", "positions": {}, "unranked": {}, "request_ids": [], "ignored": [], "affinity": {}}
    elif sig is None:
        res = {"state": "no_signal", "positions": {}, "unranked": {}, "request_ids": [], "ignored": [], "affinity": {}}
    elif not cand["city_ids"]:
        res = {"state": "no_candidates", "positions": {}, "unranked": {}, "request_ids": [], "ignored": [], "affinity": {}}
    else:
        res = rank_query(run, pid, sig, cand)
    pres = db.latest("current_presence", pid)
    fi = db.latest("founder_input", pid)
    inputs = [*signal_inputs(pid, sig),
              *([{"id": pres["id"], "version": pres["version"], "keys": ["cities"]}] if pres else []),
              {"id": fi["id"], "version": fi["version"], "keys": ["scope", "leave_out_presence", "home_country"]}]
    rec = put("qloo_ranking", pid, {
        "signal": sig and {k: sig[k] for k in ("kind", "entity_ids", "names", "label")},
        "candidates": {"city_ids": cand["city_ids"], "hash": cand["hash"]}, "scope": cand["scope"],
        "home_country": cand["home_country"], "reference": DEST_VERSION, "run_id": run.id, **res}, inputs, f"qr_{pid}")
    from .brand import _activity
    n = len(res["positions"])
    _activity(run, "Rank your candidate cities in Qloo", "Qloo destination insights",
              f"{len(res['request_ids'])} request{'s' if len(res['request_ids']) != 1 else ''} · {n} of "
              f"{len(cand['city_ids'])} cities positioned · {res['state']}",
              "DONE" if res["state"] in ("ranked", "no_candidates") else "FAILED" if res["state"] in ("failed", "denied")
              else "PARTIAL", role="Market scout", stage="Markets")
    run.emit("ranking", {"state": res["state"], "version": rec["version"]})
    return {"id": rec["id"], "version": rec["version"], "reused": False}


def located_out(pid: str, cand: dict | None = None) -> set[str]:
    """Cities a located check found a store in that the effective presence rule still leaves out. A founder restore or
    `leave_out_presence: false` puts them back (review slice 3.5, finding 3): this is the one rule for skipping,
    currentness and labels."""
    cand = cand or candidates(pid)
    found = {c["city_id"] for c in (db.latest("current_presence", pid) or {}).get("located_checks", [])
             if c["status"] == "store_found"}
    return found - set(cand["city_ids"])


def ranking_current(rk: dict, sig: dict | None, cand: dict) -> bool:
    """A ranking is current when its signal is today's and its candidate set equals today's, apart from cities a
    located check has since left out (those leave the set without a new ranking call)."""
    if signal_key(rk.get("signal")) != signal_key(sig) or rk.get("scope") != cand["scope"]:
        return False
    out = located_out(rk["project_id"], cand)
    return set(cand["city_ids"]) == set(rk["candidates"]["city_ids"]) - out and \
        (rk["state"] != "needs_home" or cand["needs_home"])


RANK_STATES = {
    "ranked": "Qloo ranked your candidate cities in one query.",
    "ranked_partial": "Qloo's ranking stopped partway: the cities on the pages that came back keep their positions; the rest are unranked.",
    "empty": "Qloo's ranking came back empty: it returned no relationship to any candidate city.",
    "flat": "Qloo's ranking carried no signal: every city came back with the same value, so there is no order to follow.",
    "failed": "Qloo's ranking failed.",
    "denied": "Qloo's ranking wasn't sent: Landfall's operating allowance is reached.",
    "no_signal": "There is nothing to rank with yet: Qloo doesn't know your brand and no brand with a similar following is chosen.",
    "no_candidates": "No city is left to rank: every catalog city is outside your goal, already yours or left out.",
    "needs_home": "Tell us your home country to rank cities at home.",
}
RETRYABLE = ("ranked_partial", "empty", "flat", "failed", "denied")


# ---- AudienceProfile (R5) ----
def words_for(index: float | None) -> str:
    """Index → words: |i| < 0.15 neutral; 0.15–0.5 "leans"; ≥ 0.5 "strongly"; missing "not known". Skews, not shares."""
    if index is None:
        return "not known"
    a = abs(index)
    return "neutral" if a < 0.15 else "leans" if a < 0.5 else "strongly"


AGE_LABELS = {"24_and_younger": "under 25", "25_to_29": "25–29", "30_to_34": "30–34", "35_to_44": "35–44",
              "45_to_54": "45–54", "55_and_older": "55+"}


def describe_row(row: dict) -> str:
    """One signal's demographics in words: the strongest positive age skew and the gender skew."""
    age = {k: v for k, v in (row.get("age") or {}).items() if isinstance(v, (int, float))}
    parts = []
    if age:
        band, v = max(age.items(), key=lambda kv: kv[1])
        w = words_for(v)
        parts.append(f"{w} {AGE_LABELS.get(band, band)}" if v > 0 and w != "neutral" else "no clear age lean")
    else:
        parts.append("age not known")
    male = (row.get("gender") or {}).get("male")
    w = words_for(male)
    parts.append("gender not known" if male is None else "no clear gender lean" if w == "neutral"
                 else f"{w} {'men' if male > 0 else 'women'}")
    return ", ".join(parts)


def demographics_words(rows: list[dict]) -> dict:
    """Attributed lines, one per signal; across signals, "mixed" when two rows lean in opposite directions on gender or
    on their strongest age band. Never blended into one combined skew."""
    lines = [{"signal_id": r["signal_id"], "signal_name": r["signal_name"], "text": describe_row(r)} for r in rows]
    males = [r["gender"].get("male") for r in rows if (r.get("gender") or {}).get("male") is not None]
    strong = [m for m in males if abs(m) >= 0.15]
    tops = {max(r["age"].items(), key=lambda kv: kv[1])[0] for r in rows if r.get("age")}
    mixed = len(rows) > 1 and ((any(m > 0 for m in strong) and any(m < 0 for m in strong)) or len(tops) > 1)
    return {"lines": lines, "summary": "mixed" if mixed else (lines[0]["text"] if len(lines) == 1 else
                                                            "consistent across the brands" if lines else "not known")}


def _audience_type(disambiguation: str | None) -> str | None:
    m = re.search(r"\(([^)]+)\)\s*$", disambiguation or "")
    return m.group(1) if m else None


def audience_step(run: Run, pid: str) -> dict:
    """Audiences (take 10) and demographics (one row per signal entity) for the ranking signal; 2 Qloo. For the same
    signal only a failed part is asked again (Retry: 1 Qloo); a new signal (stand-ins changed) asks both."""
    sig = ranking_signal(pid)
    prev = db.latest("audience_profile", pid)
    same = bool(prev) and signal_key(prev.get("signal")) == signal_key(sig)
    keep = {k for k in ("audiences", "demographics") if same and prev["status"][k] != "failed"}
    if len(keep) == 2:
        return {"version": prev["version"], "reused": True}
    if sig is None:
        return {"version": prev and prev["version"], "skipped": "no signal"}
    ents = ",".join(sig["entity_ids"])
    status, audiences, rows = {}, [], []
    if "audiences" in keep:
        status["audiences"], audiences = prev["status"]["audiences"], prev["audiences"]
    else:
        _audiences(run, pid, ents, status, audiences)
    if "demographics" in keep:
        status["demographics"], rows = prev["status"]["demographics"], prev["demographics"]
    else:
        rows = _demographics(run, pid, sig, status)
    with db._lock:  # a removal is kept on every later version, including one made while Qloo was answering
        latest = db.latest("audience_profile", pid) or {}
        removed = list(dict.fromkeys((prev or {}).get("removed", []) + latest.get("removed", [])))
        rec = put("audience_profile", pid, {"signal": {k: sig[k] for k in ("kind", "entity_ids", "names", "label")},
                                            "audiences": audiences, "removed": removed, "demographics": rows,
                                            "status": status, "run_id": run.id}, signal_inputs(pid, sig), f"ap_{pid}")
    from .brand import _activity
    _activity(run, "Ask Qloo who likes " + join(sig["names"]), "Qloo audience insights",
              f"{len(audiences)} audiences · {len(rows)} demographic row{'s' if len(rows) != 1 else ''}",
              "FAILED" if "failed" in status.values() else "DONE", role="Market scout", stage="Markets")
    return {"version": rec["version"], "reused": False}


def _audiences(run: Run, pid: str, ents: str, status: dict, audiences: list) -> None:
    try:
        out = qloo.get("/v2/insights", {"filter.type": "urn:audience", "signal.interests.entities": ents, "take": 10},
                       run.cassette, "audience", pid, run.id)
    except BudgetExhausted:
        out = {"status": 0}
    found = _rank_rows(out)
    if found is None:
        status["audiences"] = "failed"
    else:
        audiences += [{"uuid": e["entity_id"], "name": e.get("name"), "disambiguation": e.get("disambiguation"),
                       "type": _audience_type(e.get("disambiguation")), "rank_in_request": i,
                       "affinity": (e.get("query") or {}).get("affinity")} for i, e in enumerate(found, 1)]
        status["audiences"] = "ok" if audiences else "empty"


def _demographics(run: Run, pid: str, sig: dict, status: dict) -> list[dict]:
    rows = []
    try:
        out = qloo.get("/v2/insights", {"filter.type": "urn:demographics", "signal.interests.entities": ",".join(sig["entity_ids"])},
                       run.cassette, "audience", pid, run.id)
    except BudgetExhausted:
        out = {"status": 0}
    try:
        demo = json.loads(out.get("body") or "")["results"]["demographics"] if out.get("status") == 200 else None
    except (ValueError, KeyError, TypeError):
        demo = None
    names = dict(zip(sig["entity_ids"], sig["names"]))
    if not isinstance(demo, list):
        status["demographics"] = "failed"
    else:
        rows = [{"signal_id": d.get("entity_id"), "signal_name": names.get(d.get("entity_id"), d.get("entity_id")),
                 "age": (d.get("query") or {}).get("age") or {}, "gender": (d.get("query") or {}).get("gender") or {}}
                for d in demo if isinstance(d, dict)]
        rows.sort(key=lambda r: sig["entity_ids"].index(r["signal_id"]) if r["signal_id"] in sig["entity_ids"] else 99)
        status["demographics"] = "ok" if rows else "empty"
    return rows


def remove_audiences(pid: str, base_version: int, remove: list[str]) -> dict:
    with db._lock:
        ap = db.latest("audience_profile", pid)
        if not ap:
            raise LookupError("There are no audiences yet.")
        if ap["version"] != base_version:
            raise PermissionError(f"Your audiences changed (now v{ap['version']}). Reload and try again.")
        known = {a["uuid"] for a in ap["audiences"]}
        if set(remove) - known:
            raise LookupError("That isn't one of your audiences.")
        payload = {k: v for k, v in ap.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")}
        payload["removed"] = list(dict.fromkeys(ap["removed"] + remove))
        rec = put("audience_profile", pid, payload, ap["inputs"], ap["id"])
    names = [a["name"] for a in ap["audiences"] if a["uuid"] in remove]
    db.event(pid, "founder", {"stage": "Markets", "task": "Removed an audience", "detail": ", ".join(names),
                              "audience_version": rec["version"]})
    return rec


def resolve_urns(pid: str, cassette, run_id: str | None, uuids: list[str]) -> dict:
    """AudienceUrns, lazily at a consumer's first use: a URN only when `/v2/audiences?filter.query=<name>` returns a
    row whose `entity_id` is the stored UUID. Ambiguous, missing or failed → unresolved, never guessed. Never versions
    AudienceProfile; a removed audience is never resolved."""
    ap = db.latest("audience_profile", pid)
    rid = f"au_{ap['id']}"
    have = (db.get_record(rid) or {}).get("urns", {})
    out = dict(have)
    for a in ap["audiences"]:
        if a["uuid"] not in uuids or a["uuid"] in ap["removed"] or (have.get(a["uuid"]) or {}).get("state") == "resolved":
            continue
        params = {"filter.query": a["name"], "take": 5}
        try:
            r = qloo.get("/v2/audiences", params, cassette, "audience", pid, run_id)
            rows = json.loads(r.get("body") or "{}")["results"]["audiences"] if r.get("status") == 200 else None
        except (BudgetExhausted, ValueError, KeyError, TypeError):
            rows = None
        match = [x for x in rows or [] if isinstance(x, dict) and x.get("entity_id") == a["uuid"]]
        out[a["uuid"]] = {"uuid": a["uuid"], "urn": match[0].get("id") if len(match) == 1 else None,
                          "state": "failed" if rows is None else "resolved" if len(match) == 1 else "unresolved",
                          "request_id": request_id("/v2/audiences", params)}
    with db._lock:
        latest = db.latest("audience_profile", pid)  # a removal that raced this resolution wins
        for u in latest["removed"]:
            out.pop(u, None)
        db.put_record("audience_urns", pid, {"audience_profile": ap["id"], "urns": out}, rid=rid)
    return out


# ---- related-brand stores and located checks (R3) ----
def related_brands(pid: str) -> list[dict]:
    """Matched: the brand's similar_brand / competitor_brand tags; no match: the stand-ins. Top 3, as {qloo_id, name}."""
    ent = brand_entity(pid)
    if ent:
        tags = [t for t in ent.get("brand_tags", []) if t.get("name")
                and (t.get("type") or "").startswith(("urn:tag:similar_brand", "urn:tag:competitor_brand"))]
        first: dict = {}
        for t in tags:
            first.setdefault(t["name"], {"qloo_id": t["qloo_id"], "name": t["name"]})
        return list(first.values())[:3]
    si = db.latest("standins", pid)
    return [{"qloo_id": b["qloo_id"], "name": b["name"]} for b in (si or {}).get("brands", [])][:3]


def near_search(run: Run, pid: str, stage: str, brand: str, cid: str) -> dict:
    """R10: a brand's stores within 15 mi of the city; prefix-plus-retail matches located within 15 mi only."""
    c = markets.CITIES[cid]
    params = {"query": brand, "types": "urn:entity:place", "filter.location": f"{c['lat']},{c['lon']}",
              "filter.radius": NEAR_MI, "take": PAGE}
    try:
        out = qloo.get("/search", params, run.cassette, stage, pid, run.id, cache_ok=lambda o: _search_rows(o) is not None)
    except BudgetExhausted:
        return {"state": "failed", "stores": None, "capped": False, "request_id": None, "matches": []}
    ents = _search_rows(out)
    if ents is None:
        return {"state": "failed", "stores": None, "capped": False, "request_id": request_id("/search", params), "matches": []}
    matches = []
    for e in ents:
        loc = e.get("location") or {}
        if store_match(e, brand) and loc.get("lat") is not None and \
                markets.km(c["lat"], c["lon"], loc["lat"], loc["lon"]) <= NEAR_KM:
            matches.append({"qloo_id": e["entity_id"], "name": e.get("name")})
    return {"state": "ok", "stores": len(matches), "capped": len(ents) >= PAGE, "request_id": request_id("/search", params),
            "matches": matches}


def located_checks(run: Run, pid: str, city_ids: list[str], brand: str) -> list[dict]:
    """For uncertain store coverage: own-brand stores near each city about to be recommended. A found store makes the
    city presence (CurrentPresence v+1); a failed check is disclosed on the card."""
    found = []
    for cid in city_ids:
        r = near_search(run, pid, "presence", brand, cid)
        found.append({"city_id": cid, "status": "failed" if r["state"] == "failed" else "store_found" if r["stores"]
                      else "none_found", "request_id": r["request_id"],
                      **({"qloo_id": r["matches"][0]["qloo_id"], "name": r["matches"][0]["name"]} if r["stores"] else {})})
    with db._lock:
        cp = db.latest("current_presence", pid)
        payload = {k: v for k, v in cp.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")}
        done = {c["city_id"]: c for c in payload["located_checks"]}
        done.update({c["city_id"]: c for c in found})
        payload["located_checks"] = list(done.values())
        put("current_presence", pid, payload, cp["inputs"], cp["id"])
    return found


def comparables_step(run: Run, pid: str, city_ids: list[str]) -> dict:
    """ComparablePresence for the recommended cities: ≤ 3 related brands × ≤ 6 cities. A failed search is "not
    checked", never 0; a full page is "at least 50"."""
    brands = related_brands(pid)
    cities = []
    for cid in city_ids:
        rows = []
        for b in brands:
            r = near_search(run, pid, "comparables", b["name"], cid)
            rows.append({"qloo_id": b["qloo_id"], "name": b["name"], "stores": r["stores"], "capped": r["capped"],
                         "state": r["state"], "request_id": r["request_id"]})
        cities.append({"city_id": cid, "brands": rows})
    rec = put("comparable_presence", pid, {"brands": brands, "cities": cities, "radius_mi": NEAR_MI, "run_id": run.id},
              signal_inputs(pid, ranking_signal(pid)), f"cmp_{pid}")
    if brands and city_ids:
        from .brand import _activity
        n = sum(1 for c in cities for b in c["brands"] if b["state"] == "failed")
        _activity(run, f"Look for {join([b['name'] for b in brands])} stores near {len(city_ids)} cities", "Qloo place search",
                  f"{len(brands) * len(city_ids)} searches" + (f" · {n} failed" if n else ""),
                  "PARTIAL" if n else "DONE", role="Market scout", stage="Markets")
    return {"id": rec["id"], "version": rec["version"]}


# ---- purpose keys and the decision rule (R2) ----
STOCKIST_END = ("_store", "boutique", "department_store", "outlet_mall", "shopping_mall")
NOT_STOCKIST = ("restaurant", "cafe", "bar", "bakery", "coffee", "brewery")


def is_stockist(place: dict) -> bool:
    """A store or boutique genre and no food or drink genre; the generic `shopping` genre alone never counts."""
    gs = [g["qloo_id"].removeprefix(markets.ROOT_GENRE + ":") for g in place.get("genres", [])
          if g["qloo_id"] != markets.ROOT_GENRE]
    words = {w for g in gs for w in re.split(r"[:_]", g)}  # "wine_bar" and "restaurant:cafe" are food; "barber_shop" isn't
    if words & set(NOT_STOCKIST):
        return False
    return any(g.split(":")[-1].endswith(STOCKIST_END) for g in gs)


def stockist_key(scan: dict | None) -> int | None:
    """1 when the city's scan returned ≥ 3 stockist places, else 0; None (counted 0, labelled) without a scan."""
    if not scan or scan.get("status") not in ("returned", "empty"):
        return None
    return int(sum(is_stockist(p) for p in scan.get("places", [])) >= 3)


_REF_DAY = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)


def utc_offset(cid: str) -> float:
    return ZoneInfo(_ENTRY["timezones"][cid]).utcoffset(_REF_DAY).total_seconds() / 3600


def home_offset(home: dict) -> float | None:
    """The home's standard UTC offset: the nearest catalog city in the home country (a placed home), or the one
    offset all that country's catalog cities share (a country-only home). Otherwise not known."""
    iso = home.get("country")
    same = [c for c in markets.ORDER if markets.CITIES[c]["country"] == iso]
    if not same:
        return None
    if home.get("lat") is not None:
        c = min(same, key=lambda x: markets.km(home["lat"], home["lon"], markets.CITIES[x]["lat"], markets.CITIES[x]["lon"]))
        return utc_offset(c)
    offs = {utc_offset(c) for c in same}
    return offs.pop() if len(offs) == 1 else None


def bloc_of(iso: str | None) -> str | None:
    return next((k for k, b in _ENTRY["blocs"].items() if iso in b["members"]), None)


def entry_facts(cid: str, home: dict) -> dict:
    """Shared official language, same trade bloc, ≥ 5 working hours overlap; each None when not known."""
    city = markets.CITIES[cid]
    hc = home.get("country")
    langs_h, langs_c = _LANG["countries"].get(hc), _LANG["countries"].get(city["country"])
    off = home_offset(home)
    hours = None if off is None else max(0.0, 8 - abs(utc_offset(cid) - off))
    return {"same_country": None if not hc else hc == city["country"],
            "shared_language": None if not (langs_h and langs_c) else bool(set(langs_h) & set(langs_c)),
            "same_bloc": None if not hc else (bloc_of(hc) is not None and bloc_of(hc) == bloc_of(city["country"])),
            "overlap_hours": hours}


def test_demand_key(facts: dict) -> int:
    return int(bool(facts["shared_language"])) + int(bool(facts["same_bloc"])) + \
        int(facts["overlap_hours"] is not None and facts["overlap_hours"] >= 5)


ENTRY_CHIPS = {"SAME_COUNTRY": "SAME COUNTRY", "SAME_TRADE_BLOC": "SAME TRADE BLOC", "SHARED_LANGUAGE": "SHARED LANGUAGE",
               "IMPORT_NEW_LANGUAGE": "IMPORT, NEW LANGUAGE", "FIT_NOT_KNOWN": "FIT NOT KNOWN"}


def entry_chip(f: dict) -> str:
    if f["same_country"]:
        return "SAME_COUNTRY"
    if f["same_bloc"]:
        return "SAME_TRADE_BLOC"
    if f["shared_language"]:
        return "SHARED_LANGUAGE"
    if f["same_country"] is False and f["shared_language"] is False:
        return "IMPORT_NEW_LANGUAGE"
    return "FIT_NOT_KNOWN"


ROLE_BY_RANK = {1: "TOP_RECOMMENDATION", 2: "INVESTIGATE_FIRST", 3: "STRONG_ALTERNATIVE", 4: "STRONG_ALTERNATIVE",
                5: "ALSO_WORTH", 6: "ALSO_WORTH"}
ROLE_LABELS = {"TOP_RECOMMENDATION": "TOP RECOMMENDATION", "INVESTIGATE_FIRST": "INVESTIGATE FIRST",
               "STRONG_ALTERNATIVE": "STRONG ALTERNATIVE", "ALSO_WORTH": "ALSO WORTH RESEARCHING"}
GROUP_LABELS = {"all": "Recommended", "home": "At home", "abroad": "Abroad"}


def decide(positions: dict[str, int], scope: str, home_country: str | None, keys: dict[str, int],
           skip: set[str] = frozenset()) -> list[dict]:
    """The total, stable decision rule. Per group (one, or At home / Abroad for `both`): the group's ranked cities by
    Qloo position (group-local rank 1…n); fixed windows of 4; inside a window by (purpose key desc, position asc); the
    cap, with no slot transfer and no padding. Any input order gives the same output. Cities in `skip` (a located
    check found a store) leave before the windows are cut, so the next ranked city takes the slot."""
    groups = [("home", lambda c: markets.CITIES[c]["country"] == home_country),
              ("abroad", lambda c: markets.CITIES[c]["country"] != home_country)] if scope == "both" else [("all", lambda c: True)]
    out = []
    for key, member in groups:
        ranked = sorted((c for c in positions if member(c) and c not in skip), key=lambda c: (positions[c], c))
        final = []
        for i in range(0, len(ranked), WINDOW):
            final += sorted(ranked[i:i + WINDOW], key=lambda c: (-keys.get(c, 0), positions[c], c))
        picked = final[:CAPS[key]]
        local = {c: i + 1 for i, c in enumerate(ranked)}
        out.append({"key": key, "label": GROUP_LABELS[key], "cap": CAPS[key], "available": len(ranked),
                    "cities": [{"city_id": c, "qloo_position": positions[c], "group_rank": local[c], "final_position": i + 1,
                                "moved_by_purpose": local[c] != i + 1, "role": ROLE_BY_RANK[i + 1], "purpose_key": keys.get(c, 0)}
                               for i, c in enumerate(picked)]})
    return out


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def position_line(n_candidates: int, city: str, position: int, label: str) -> str:
    """The deterministic first line, from QlooRanking only; never a score, never compared to another request."""
    return f"In one Qloo ranking of your {n_candidates} candidate cities, {city} came {ordinal(position)} for {label}."


# ---- the prepare run: presence, stand-ins and audiences before the scan ----
def prepare_run(run: Run):
    pid = run.project_id
    run.step("presence", lambda: presence_step(run, pid))
    if brand_entity(pid) is None:
        run.step("standins", lambda: standins_step(run, pid))
    run.step("audience", lambda: audience_step(run, pid))


def prepare(pid: str) -> str | None:
    """Started when the confirmed profile follows the current identity (and again after a stand-in edit is
    re-ranked). Memoized per identity and profile version; never repeats completed steps."""
    from .scan import current_profile
    prof, ident = current_profile(pid), db.latest("identity", pid)
    if not prof or not ident:
        return None
    key = f"i{ident['version']}-p{prof['id']}-{prof['version']}"
    done = db.q("SELECT id FROM runs WHERE project_id=? AND kind='prepare' AND input_key=? AND status='completed'", pid, key)
    return done[0]["id"] if done else runner.start(pid, "prepare", key)


HANDLERS["prepare"] = prepare_run
HANDLERS["audience"] = lambda run: run.step("audience", lambda: audience_step(run, run.project_id))


# ---- views (records → screen views; never stored) ----
CAVEAT = "a cultural relationship, not your customer data or audience size"


def presence_view(pid: str) -> dict:
    p = presence_of(pid)
    ent = brand_entity(pid)
    return {**p, "coverage_line": coverage_line(p["store_search"], ent["name"] if ent else "your"),
            "leave_out_presence": goal_of(pid)["leave_out_presence"]}


def standins_view(pid: str) -> dict | None:
    if brand_entity(pid) is not None:
        return None
    si = db.latest("standins", pid)
    run = db.q("SELECT status FROM runs WHERE project_id=? AND kind='prepare' ORDER BY started_at DESC, rowid DESC LIMIT 1", pid)
    if not si:
        return {"state": "pending" if run and run[0]["status"] in ("queued", "running") else "not_started", "brands": []}
    genre = _GENRES["genres"].get(si["genre"]) if si["genre"] else None
    return {"state": si["status"], "id": si["id"], "version": si["version"], "brands": si["brands"],
            "excluded": si["excluded"], "genre": genre, "genre_line": f"Brand category: {genre}" if genre
            else "Category not matched: Qloo picked from your tags alone",
            "line": "Brands with a similar following: " + " · ".join(b["name"] for b in si["brands"]) if si["brands"]
            else "No brand with a similar following yet. Add one to let Qloo order cities for you."}


def audience_view(pid: str) -> dict:
    ap = db.latest("audience_profile", pid)
    if not ap:
        sig = ranking_signal(pid)
        return {"state": "pending" if sig else "no_signal", "audiences": [], "removed": []}
    standins = ap["signal"]["kind"] == "standins"
    who = (f"Qloo's view of people who like {join(ap['signal']['names'])}, used as a stand-in for you" if standins
           else f"Qloo's view of the people who like {ap['signal']['names'][0]}")
    demo = demographics_words(ap["demographics"])
    stale = signal_key(ap["signal"]) != signal_key(ranking_signal(pid))
    return {"state": "ready", "id": ap["id"], "version": ap["version"], "kind": ap["signal"]["kind"],
            "label": f"{who}: {CAVEAT}", "status": ap["status"],
            "audiences": [{k: a[k] for k in ("uuid", "name", "type")} for a in ap["audiences"] if a["uuid"] not in ap["removed"]],
            "removed": [{k: a[k] for k in ("uuid", "name")} for a in ap["audiences"] if a["uuid"] in ap["removed"]],
            "demographics": demo, "from_previous_standins": stale}
