"""Stages 4–5, the deterministic parts (BUILD-PLAN slice 3; CONTRACTS B4/B5).

Everything here is calculated (class C) or checks model output: no provider calls, no model calls. The packaged
reference data lives in landfall/data: catalog@1, countries@1 + eu_customs@2026-10 and gazetteer@1 (GeoNames).
"""
import functools
import json
import math
import re
import unicodedata
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
_CATALOG = json.loads((DATA / "cities.json").read_text())
CATALOG_VERSION = _CATALOG["version"]
RADIUS_M = _CATALOG["radius_m"]  # ponytail: one radius for all cities until results show it is wrong
TAKE = 25
CITIES = {c["city_id"]: c for c in _CATALOG["cities"]}
ORDER = [c["city_id"] for c in _CATALOG["cities"]]
REGIONS = list(dict.fromkeys(c["region"] for c in _CATALOG["cities"]))
CAP = 6
FEATURED = 2

_COUNTRIES = json.loads((DATA / "countries.json").read_text())
COUNTRY_NAME = {iso: names[0] for iso, names in _COUNTRIES["countries"].items()}
EU = set(_COUNTRIES["eu_customs"]["members"])
EU_LABEL = _COUNTRIES["eu_customs"]["label"]
_ALIAS = {n.casefold(): iso for iso, names in _COUNTRIES["countries"].items() for n in names}


def km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine), km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(a)))


# ---- home (P3-04): conservative parsing of the brief's Home market row ----
@functools.cache
def gazetteer() -> dict[str, list[dict]]:
    """gazetteer@1 by country: GeoNames cities15000 subset (CC BY 4.0, attributed in README and LICENSE)."""
    out: dict[str, list[dict]] = {}
    for line in (DATA / "gazetteer.tsv").read_text().split("\n")[2:]:
        if line:
            name, ascii_, admin1, cc, lat, lon, pop = line.split("\t")
            out.setdefault(cc, []).append({"name": name, "ascii": ascii_, "admin1": admin1, "country": cc,
                                           "lat": float(lat), "lon": float(lon), "population": int(pop)})
    return out


def country_of(text: str) -> str | None:
    return _ALIAS.get(" ".join(text.split()).strip(" .").casefold())


def parse_home(text: str | None, claim_id: str | None = None, provenance: str = "INFERRED") -> dict:
    """(1) split on commas; (2) country = the last part equal to a countries@1 name or alias, else unknown; (3) city =
    the first part, looked up in gazetteer@1 within that country, a middle part matching an admin1 name narrows it.
    More than one match, or none, leaves the city unknown. A country alone leaves the city unknown."""
    home = {"text": text, "claim_id": claim_id, "provenance": provenance, "country": None, "city": None,
            "lat": None, "lon": None, "note": "unknown"}
    parts = [p.strip() for p in (text or "").split(",") if p.strip()]
    found = [(i, country_of(p)) for i, p in enumerate(parts) if country_of(p)]
    if not found:
        return home
    at, iso = found[-1]
    home.update(country=iso, note="country_only")
    if at == 0:
        return home
    city = parts[0].casefold()
    matches = [g for g in gazetteer().get(iso, []) if city in (g["name"].casefold(), g["ascii"].casefold())]
    for middle in parts[1:at]:
        narrowed = [g for g in matches if g["admin1"].casefold() == middle.casefold()]
        matches = narrowed or matches
    if len(matches) == 1:
        g = matches[0]
        home.update(city=g["name"], lat=g["lat"], lon=g["lon"], note="placed", admin1=g["admin1"])
    else:
        home["note"] = "ambiguous" if matches else "not_placed"
    return home


# ---- shipping range (P3-04) ----
_GROUPS = {"eu": EU, "european union": EU}
_NEG = re.compile(r"\b(do(es)?\s*n[o']?t|not|never|cannot|can't|no longer)\b(\s+\w+){0,2}?\s+ship", re.I)
_EXCEPT = re.compile(r"\b(except|excluding|other than|but not)\b", re.I)
_WORLD = re.compile(r"\b(worldwide|world-wide|internationally|international|globally|all countries|anywhere)\b", re.I)
_OUTSIDE = re.compile(r"\b(outside|beyond|out of)\b", re.I)
_ONLY = re.compile(r"\b(only|exclusively|solely)\b", re.I)


def _countries_in(text: str) -> set[str]:
    """Country names, aliases and the EU group, by whole word. Short aliases (US, UK, EU) must be upper case, so the
    pronoun "us" never reads as a country."""
    found: set[str] = set()
    for name, iso in list(_ALIAS.items()) + [(k, None) for k in _GROUPS]:
        flags = 0 if len(name) <= 3 else re.I
        pat = re.escape(name.upper() if len(name) <= 3 else name)
        if re.search(rf"(?<![\w.]){pat}(?![\w])", text, flags):
            found |= {iso} if iso else _GROUPS[name]
    return found


def shipping_rule(facts: list[dict]):
    """Returns iso → True (inside) | False (outside) | None (not known), from SUPPORTED or STATED shipping facts only.
    Inside: named positively, or worldwide/international without excluding it. Outside: excluded by name, or an
    exhaustive coverage ("only", "exclusively") that doesn't list it. Anything else: None."""
    inside: set[str] = set()
    outside: set[str] = set()
    world = exhaustive = False
    for f in facts:
        if f.get("state") not in ("SUPPORTED", "STATED") or (f.get("subject") or {}).get("key") != "shipping":
            continue
        for clause in re.split(r"[.;\n]", f.get("value") or ""):
            main, *rest = _EXCEPT.split(clause, maxsplit=1)
            if rest:
                outside |= _countries_in(rest[-1])
            named = _countries_in(main)
            if _NEG.search(main) and _OUTSIDE.search(main):  # "we don't ship outside Germany": Germany only
                inside |= named
                exhaustive |= bool(named)
                continue
            if _NEG.search(main):
                outside |= named
                continue
            inside |= named
            world |= bool(_WORLD.search(main))
            exhaustive |= bool(_ONLY.search(main)) and bool(named)

    def rule(iso: str) -> bool | None:
        if iso in outside:
            return False
        if iso in inside or world:
            return True
        return False if exhaustive else None
    return rule


# ---- practical fit (C) ----
CHIPS = {"SAME_COUNTRY": "SAME COUNTRY", "IMPORT_NEEDED": "IMPORT NEEDED", "INSIDE_YOUR_RANGE": "INSIDE YOUR RANGE",
         "NO_IMPORT_STEP_EU": "NO IMPORT STEP (EU)", "FIT_NOT_KNOWN": "FIT NOT KNOWN"}


def practical_fit(city: dict, home: dict, ships) -> dict:
    """Unknown stays null, never false. One chip by precedence: SAME COUNTRY → IMPORT NEEDED → INSIDE YOUR RANGE →
    NO IMPORT STEP (EU) → FIT NOT KNOWN."""
    hc = home.get("country")
    distance = round(km(home["lat"], home["lon"], city["lat"], city["lon"])) if home.get("lat") is not None else None
    same = (hc == city["country"]) if hc else None
    imp = None if not hc else not (same or (hc in EU and city["country"] in EU))
    inside = ships(city["country"])
    chip = ("SAME_COUNTRY" if same else "IMPORT_NEEDED" if imp else "INSIDE_YOUR_RANGE" if inside
            else "NO_IMPORT_STEP_EU" if imp is False else "FIT_NOT_KNOWN")
    return {"distance_km": distance, "same_country": same, "import_needed": imp, "inside_ship_range": inside,
            "chip": chip}


def fit_line(f: dict, home: dict) -> str:
    """The criterion text, from the C fields only."""
    if f["chip"] == "FIT_NOT_KNOWN":
        return "Not known: your brief doesn't place your home market." if not home.get("country") else \
            "Not known from your brief."
    parts = []
    if f["distance_km"] is not None:
        parts.append(f"{f['distance_km']:,} km from {home['city']}")
    parts.append({"SAME_COUNTRY": f"in {COUNTRY_NAME.get(home['country'], home['country'])}, your home market",
                  "IMPORT_NEEDED": f"outside {COUNTRY_NAME.get(home['country'], home['country'])}: an import step",
                  "INSIDE_YOUR_RANGE": "inside the shipping range your brief states",
                  "NO_IMPORT_STEP_EU": f"no import step inside the EU ({EU_LABEL.split('(')[1].rstrip(')')})"}[f["chip"]])
    if f["inside_ship_range"] is False:
        parts.append("outside the shipping range your brief states")
    elif f["inside_ship_range"] is None:
        parts.append("shipping range not known")
    return "; ".join(parts)


# ---- channels@1 and the digest (P3-04) ----
CHANNELS = ["retail", "venues_culture", "fitness_outdoors", "food_drink", "stays", "attractions", "other"]
CHANNEL_LABELS = {"retail": "retail", "venues_culture": "venues & culture", "fitness_outdoors": "fitness & outdoors",
                  "food_drink": "food & drink", "stays": "stays", "attractions": "attractions", "other": "other"}
ROOT_GENRE = "urn:tag:genre:place"
CHANNELS_VERSION = "channels@1"
GAZETTEER_VERSION = "gazetteer@1"
# channels@1: the id → class table, as ordered rules over the id after "urn:tag:genre:place:". Authored against every
# genre id in the captured place responses (tests fail on an unmapped captured id). An id no rule knows is counted
# "other" and listed as unmapped in the digest, never guessed into a channel.
CHANNEL_RULES = [  # after the restaurant… → food_drink and hotel… → stays prefixes
    (r"^(tour_agency|sightseeing_tour_agency|tour_operator|cruise_agency|dj_service|personal_care)$", "other"),
    (r"^(pottery_classes|cooking_class|photography_school)$", "venues_culture"),
    (r"^(brewery|winery|coffee_roasters|pub|cafe|bar|food_court)$", "food_drink"),
    (r"(_store|_shop|_market|_supermarket|^shopping|^market|^supermarket|^boutique)", "retail"),
    (r"(museum|gallery|art_center|theater|theatre|concert_hall|music_venue|event_venue|wedding_venue|night_club|"
     r"dance_club|^club$|amphitheater|convention_center|festival|stadium|cinema|library)", "venues_culture"),
    (r"(gym|fitness|climbing|sports|yoga|wellness_center|^spa$|^park$|^beach$|baseball_field|surf_school|"
     r"swimming|hiking|trail)", "fitness_outdoors"),
    (r"(tourist_attraction|landmark|scenic_spot|observation_deck|castle|monument|plaza|town_square|aquarium|^zoo$|"
     r"^island$|city_hall|miniature_golf)", "attractions"),
]


def genre_class(genre_id: str) -> str | None:
    g = genre_id.removeprefix(ROOT_GENRE + ":")
    if g.startswith("restaurant"):
        return "food_drink"
    if g.startswith("hotel"):
        return "stays"
    for pattern, cls in CHANNEL_RULES:
        if re.search(pattern, g):
            return cls
    return None


def place_class(place: dict) -> str:
    """Exactly one class per place: the highest-precedence class among its genres. No genre beyond the root →
    unknown_category (not other)."""
    genres = [g["qloo_id"] for g in place.get("genres", []) if g["qloo_id"] != ROOT_GENRE]
    if not genres:
        return "unknown_category"
    classes = [genre_class(g) or "other" for g in genres]
    return min(classes, key=CHANNELS.index)


def digest(scan: dict) -> dict:
    places = scan.get("places") or []
    channels = dict.fromkeys(CHANNELS, 0)
    unknown = 0
    unmapped: set[str] = set()
    non_attraction = 0
    for p in places:
        cls = place_class(p)
        unmapped |= {g["qloo_id"] for g in p.get("genres", []) if g["qloo_id"] != ROOT_GENRE and not genre_class(g["qloo_id"])}
        if cls == "unknown_category":
            unknown += 1
            continue
        channels[cls] += 1
        non_attraction += cls != "attractions"
    n = len(places)
    strength = "THIN_SEARCH_RESULT" if n < 10 else "LIMITED" if non_attraction <= 2 else "MODERATE"
    top = sorted(places, key=lambda p: p["rank_in_request"])[:8]
    return {"city_id": scan["city_id"], "returned_count": n, "take": TAKE, "channels": channels, "unknown_category": unknown,
            "top_places": [{"qloo_id": p["qloo_id"], "name": p["name"],
                            "genres": [g["name"] for g in p.get("genres", [])][:3]} for p in top],
            "evidence_strength": strength, "unmapped_genres": sorted(unmapped)}


STRENGTH_LABELS = {"THIN_SEARCH_RESULT": "THIN SEARCH RESULT", "LIMITED": "LIMITED · NOT YET CHECKED",
                   "MODERATE": "MODERATE · NOT YET CHECKED"}


def channel_line(d: dict, n: int = 3) -> str:
    """"10 retail · 6 food & drink": a description of what this one search returned, never a score."""
    ranked = sorted(((c, k) for k, c in d["channels"].items() if c), key=lambda ck: (-ck[0], CHANNELS.index(ck[1])))
    return " · ".join(f"{c} {CHANNEL_LABELS[k]}" for c, k in ranked[:n])


def evidence_line(d: dict) -> str:
    n = d["returned_count"]
    if d["evidence_strength"] == "THIN_SEARCH_RESULT":
        return f"Qloo returned {n} place{'s' if n != 1 else ''} here, too few to judge from. No lead checked yet."
    na = n - d["channels"]["attractions"] - d["unknown_category"]
    return f"{na} of the {n} places returned are not tourist attractions. No lead checked yet."


# ---- the scan request and response classification (P3-04) ----
def effective_signals(profile: dict) -> list[dict]:
    """What is actually sent to Qloo: included signals only. An unticked comparable is never sent or shown as used."""
    return [{"kind": s["kind"], "qloo_id": s["qloo_id"], "name": s["name"], "origin": s["origin"]}
            for s in profile.get("signals", []) if s.get("included")]


def scan_params(city: dict, signals: list[dict]) -> dict:
    params = {"filter.type": "urn:entity:place", "filter.location": f"POINT({city['lon']} {city['lat']})",
              "filter.location.radius": RADIUS_M, "take": TAKE}
    ents = [s["qloo_id"] for s in signals if s["kind"] == "entity"]
    tags = [s["qloo_id"] for s in signals if s["kind"] == "tag"]
    if ents:
        params["signal.interests.entities"] = ",".join(ents)
    if tags:
        params["signal.interests.tags"] = ",".join(tags)
    return params


def _message(body: str) -> str:
    """Only the error *message* fields of an error body, never echoed request params."""
    try:
        b = json.loads(body or "")
    except ValueError:
        return ""
    if not isinstance(b, dict):
        return ""
    msgs = [b.get("message"), b.get("detail")]
    err = b.get("error")
    msgs += [err] if isinstance(err, str) else [err.get("message")] if isinstance(err, dict) else []
    msgs += [e.get("message") for e in b.get("errors") or [] if isinstance(e, dict)]
    return " ".join(m for m in msgs if isinstance(m, str))


def parse_place(e: dict, rank: int) -> dict:
    tags = [t for t in e.get("tags") or [] if isinstance(t, dict)]
    tid = lambda t: t.get("id") or t.get("tag_id")  # noqa: E731
    genres = [{"qloo_id": tid(t), "name": t.get("name")} for t in tags
              if (tid(t) or "").startswith(ROOT_GENRE + ":")]
    other = [{"qloo_id": tid(t), "name": t.get("name"), "type": t.get("type")} for t in tags
             if not (tid(t) or "").startswith(ROOT_GENRE)][:12]
    props, q, loc = e.get("properties") or {}, e.get("query") or {}, e.get("location") or {}
    return {"qloo_id": e.get("entity_id"), "name": e.get("name"), "genres": genres, "tags_kept": other,
            "rank_in_request": rank, "affinity_in_request": q.get("affinity"), "distance_m": q.get("distance"),
            "lat": loc.get("lat"), "lon": loc.get("lon"), "address": props.get("address"),
            "website": props.get("website"), "phone": props.get("phone")}


def classify(out: dict, city: dict) -> dict:
    """Never raises: an answer this code can't read is that city's failed "unreadable response" (closure, blocker 1)."""
    try:
        return _classify(out, city)
    except Exception:
        return {"status": "failed", "places": [], "error": {"http_status": out.get("status") or None,
                                                             "reason": "unreadable response"}}


def _classify(out: dict, city: dict) -> dict:
    """returned / empty only for HTTP 200 whose body parses to success: true with results.entities a list; a
    malformed or unsuccessful 200 is failed. unresolved only for a 4xx whose message names the location filter, or
    when more than half the places lie over 2 × radius from the city point (an ignored filter). All unresolved classes
    are unverified live."""
    status = out.get("status")
    if status == 200:
        try:
            body = json.loads(out.get("body") or "")
            ents = body["results"]["entities"] if body.get("success") is True else None
        except (ValueError, KeyError, TypeError, AttributeError):
            ents = None
        if not isinstance(ents, list) or not all(isinstance(e, dict) and e.get("entity_id") for e in ents):
            return {"status": "failed", "places": [], "error": {"http_status": 200, "reason": "unreadable response"}}
        places = [parse_place(e, i + 1) for i, e in enumerate(ents)]
        far = 0
        for p in places:
            d = (km(city["lat"], city["lon"], p["lat"], p["lon"]) * 1000 if p["lat"] is not None and p["lon"] is not None
                 else p["distance_m"])
            far += d is not None and d > 2 * RADIUS_M
        if places and far * 2 > len(places):
            return {"status": "unresolved", "places": places,
                    "error": {"http_status": 200, "reason": "results are not in this city"}}
        return {"status": "returned" if places else "empty", "places": places, "error": None}
    if status and 400 <= status < 500 and re.search(r"filter\.location|\blocation\b", _message(out.get("body", "")), re.I):
        return {"status": "unresolved", "places": [],
                "error": {"http_status": status, "reason": "Qloo could not use this city's location"}}
    reason = out.get("error") or (f"Qloo answered HTTP {status}" if status else "no response")
    return {"status": "failed", "places": [], "error": {"http_status": status or None, "reason": reason}}


# ---- the prose guard (P3-05): every displayed strategist text ----
PROHIBITED = [
    (r"\bmore (?!than\b)[^.]{0,40}?\bthan\b", "compares counts across cities"),
    (r"\b(fewer|less) [^.]{0,40}?\bthan\b", "compares counts across cities"),
    (r"\b(stronger|weaker|better|worse|higher|lower|larger|bigger|richer)\b[^.]{0,60}?\bthan\b", "compares cities"),
    (r"\bthe (most|fewest|highest|lowest|largest|biggest|strongest|best)\b", "ranks across cities"),
    (r"\b(strongest|weakest|best|biggest|largest)\b", "ranks across cities"),
    (r"\branks?(ed|ing)?\b|\branking\b|\btop[- ](city|cities|market|markets|choice|spot)\b", "ranks across cities"),
    (r"\b(score|scores|scored|scoring|affinity|popularity|popular)\b", "uses scores or popularity"),
    (r"\bdemand\b|\bsales\b|\bsell(s|ing)? (well|out)\b|\bwill sell\b|\bbest[- ]?sell", "claims demand or sales"),
    (r"\breceptive market\b|\bmarket (potential|access)\b|\bpotential (for|customers|buyers|market)\b",
     "claims demand or sales"),
    (r"\bmarket[- ]size\b|\bmarket share\b|\baudience size\b|\bproven\b|\bguarantee", "claims size or proof"),
    # slice 3.5 (R6): reputations and comparisons the order must not borrow
    (r"\bmature market\b|\breputation\b|\boutrank", "claims a reputation or compares cities"),
    (r"\b(twice|three times|double|half) as (many|much)\b", "compares counts across cities"),
    (r"\bthan (any|all|every|the) (other|others|rest)\b|\bunlike (the |any )?other|\bany other cit", "compares cities"),
]


def guard(text: str | None) -> str | None:
    """The reason a text fails, or None. A pattern list: the human review judges what it cannot."""
    for pattern, why in PROHIBITED:
        if text and re.search(pattern, text, re.I):
            return why
    return None


# ---- city names (review slice 3, blocker 2) ----
def fold(s: str) -> str:
    """Accents and case removed, punctuation as spaces: "Bogotá" and "Bogota", "Washington, D.C." and "washington dc"."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().casefold().replace(".", "")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


# Other spellings the model may use for catalog cities (review slice 3, blocker 2).
ALIASES = {"new-york": ["NYC", "New York City"], "washington-dc": ["Washington", "Washington D.C.", "D.C."],
           "bengaluru": ["Bangalore"], "mexico-city": ["CDMX", "Ciudad de Mexico"], "ho-chi-minh-city": ["Saigon", "HCMC"],
           "sao-paulo": ["Sao Paulo"], "zurich": ["Zuerich"], "lisbon": ["Lisboa"], "munich": ["Muenchen", "München"],
           "copenhagen": ["København"], "prague": ["Praha"], "vienna": ["Wien"], "milan": ["Milano"], "brussels": ["Bruxelles"],
           "mumbai": ["Bombay"], "tel-aviv": ["Tel Aviv-Yafo"], "rio-de-janeiro": ["Rio"]}


def names_in(text: str, ids) -> list[str]:
    """Catalog cities named in a text, in order of first mention, whatever the accents, case or a common alias."""
    t = f" {fold(text)} "
    hits = []
    for cid in ids:
        spots = [t.find(f" {fold(n)} ") for n in [CITIES[cid]["name"], *ALIASES.get(cid, [])]]
        spots = [i for i in spots if i >= 0]
        if spots:
            hits.append((min(spots), cid))
    return [cid for _, cid in sorted(hits)]


def left_out(exclude: str | None) -> set[str]:
    """Cities the founder asked to leave out at the start (by city or country name)."""
    out = set()
    for part in (exclude or "").split(","):
        part = part.strip().casefold()
        if not part:
            continue
        iso = country_of(part)
        out |= {cid for cid, c in CITIES.items() if c["name"].casefold() == part or (iso and c["country"] == iso)}
    return out
