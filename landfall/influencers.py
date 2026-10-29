"""Slice 6.6: creator discovery with Influencers.club (BUILD-PLAN "Slice 6.6"; CONTRACTS B11).

Provider facts are from its docs (26 Oct 2026); nothing is live-verified until the owner's key exists. Every request is
dispatched in the request handler, never on the resumable runner [P66-R4], admitted by the ledger's pools before it is
sent, and passes the project's cassette so an accepted case replays at 0 requests. A demo (replay) project reads
SYNTHETIC creators from a fixture and never dispatches; a live project with no key reads "not connected" [P66-R5].
"""
import json
import re
import threading
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import httpx

from . import config, creators, db, ledger, markets, plans, ranking, runner
from .cassette import NoRecording, key_of
from .ledger import IC as PROVIDER, BudgetExhausted

BASE = "https://api-dashboard.influencers.club/public/v1"
LIMIT = 8                  # creators per discovery request (owner, 27 Oct 2026: 8 to save credits)
MAX_PAGE = 2               # "Show 10 more" at most twice [P66-R6]
RATE = 0.03                # credits per creator returned with return_filter_values (DOCS)
ENRICH_CREDITS = 0.2       # profile enrichment (DOCS)
FOLLOWER_MIN = 10_000      # audience location is only filtered for 10k+ creators (DOCS)
MIN_PCT = 20               # ponytail: percent scale assumed from "US>30%" (UNVERIFIED); resolved at the first live call
# v2.5's display and cache rights start 1 Nov 2026. Owner, 27 Oct 2026: live use opens now ("we need to be able to test
# sooner, the hackathon is due oct 30th, we can cache stuff as needed"), the decision §6.6.6 reserved for the owner.
TERMS_FROM = date(2026, 10, 27)
CACHE_DAYS = 30  # terms: caching "as needed", refresh after 30 days recommended
SYNTHETIC = config.ROOT / "fixtures" / "synthetic" / "influencers-club.json"
ATTRIBUTION = "Creator data: Influencers.club"
PHRASE = re.compile(r"[^A-Za-z0-9 &,'-]+")
PROFILE_URL = {"instagram": "https://www.instagram.com/{}/", "tiktok": "https://www.tiktok.com/@{}",
               "youtube": "https://www.youtube.com/@{}"}  # built from the username, not returned by the API

_flight: set[tuple[str, str]] = set()
_flight_lock = threading.Lock()


class Refused(Exception):
    def __init__(self, code: int, detail: str):
        self.code, self.detail = code, detail


class Unknown(Exception):
    """The request may have reached the provider; its outcome (and charge) is not known. Never retried automatically."""


def _key() -> str | None:
    return config.env("influencers_club_api_key") or config.env("influencer_api_key") or None  # either name in .env


def pool_of(session: str) -> str:
    """The allocation a request draws on, from trusted server state only, never a client field [P66 §6.6.2]."""
    # A1: only a session bound to a non-revoked judge link draws on the judges' pool; owner sessions stay personal.
    return "judges" if config.env("LANDFALL_ENV") == "judging" and _judge_grant(session) else "personal"


def _judge_grant(session: str) -> bool:
    from . import grants
    return grants.kind_of(session) == "judge"


def _demo(pid: str) -> bool:
    return db.q("SELECT mode FROM projects WHERE id=?", pid)[0]["mode"] == "replay"


def unavailable(pid: str) -> str | None:
    """Why live discovery can't run for this project now, or None."""
    if _demo(pid):
        if "/fixtures/demo/" in (db.q("SELECT demo_case FROM projects WHERE id=?", pid)[0]["demo_case"] or ""):
            # owner, 28 Oct 2026: a real brand's recorded demo never records or replays Influencers.club data (their terms)
            return "Creator search with Influencers.club isn't part of the recorded demo."
        return None  # SYNTHETIC creators; a recorded exchange skips this check in _dispatch [P66-I1]
    if not _key():
        return "Creator discovery isn't connected in this deployment."
    if date.today() < TERMS_FROM and not config.env("LANDFALL_IC_TERMS_V24_CONFIRMED"):
        return "Creator discovery opens on 1 Nov 2026."
    if config.env("LANDFALL_IC_ADMISSION", "open") != "open":
        return "Live creator discovery is paused while its allowance is reconciled."
    return None


def transport(method: str, path: str, *, params: dict | None = None, body: dict | None = None) -> tuple[int, dict]:
    """One HTTP exchange. The key goes only in this header; it never reaches a record, a log or a cassette."""
    r = httpx.request(method, BASE + path, params=params, json=body, timeout=30,
                      headers={"Authorization": f"Bearer {_key()}", "Accept": "application/json"})
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"error": r.text[:300]}


def _dispatch(pid: str, pool: str, op: str, request: dict, credits_max: float, charged, *, lookup: bool = False,
              ref: dict | None = None) -> tuple[dict, str]:
    """(response, origin). A recorded exchange replays (REPLAYED, nothing counted); otherwise the ledger admits one
    request at its worst-case credits, and it is finalized with what the response shows."""
    cas = runner.cassette_for(pid)
    replayed = cas.mode == "replay" and cas.recorded(key_of(PROVIDER, op, request)) is not None
    if not replayed and (why := unavailable(pid)):
        raise Refused(503, why)
    ck = f"ic:{key_of(PROVIDER, op, request)}"
    if not replayed and op != "location":  # the same request answered in the last 30 days: reused, 0 requests (owner, 27 Oct)
        hit = db.q("SELECT response, fetched_at FROM provider_cache WHERE key=?", ck)
        if hit and datetime.now(timezone.utc) - datetime.fromisoformat(hit[0]["fetched_at"]) < timedelta(days=CACHE_DAYS):
            return json.loads(hit[0]["response"]), "CACHED"

    def live():
        if lookup and not _affordable(pool, pid, 2, LIMIT * RATE):  # never spend the last request on a lookup [P66-I7]
            raise BudgetExhausted("not enough left for a location lookup and the search after it")
        rid = ledger.reserve(PROVIDER, op, pool=pool, project_id=pid, credits_reserved=credits_max, lookup=lookup, **(ref or {}))
        try:
            status, body = transport(request["method"], request["path"], params=request.get("params"), body=request.get("body"))
        except httpx.TransportError as e:  # sent or not, we can't tell: counted in full, never retried [P66 §6.6.2]
            ledger.finalize(rid, PROVIDER, op, outcome="unknown", pool=pool, project_id=pid, credits_charged=credits_max,
                            unresolved=True, error=type(e).__name__)
            raise Unknown(type(e).__name__) from e
        ledger.finalize(rid, PROVIDER, op, outcome=str(status), pool=pool, project_id=pid,
                        credits_charged=charged(body) if status == 200 else 0.0)
        return {"status": status, "body": body}
    try:
        out = cas.call(PROVIDER, op, request, live, stage="creator_discovery")
        if not replayed and op != "location" and out["status"] == 200:
            db.x("INSERT OR REPLACE INTO provider_cache VALUES(?,?,?,?)", ck, PROVIDER, json.dumps(out), db.now())
        return out, "REPLAYED" if replayed else "LIVE"
    except NoRecording:
        raise Refused(503, "This recorded case has no creator search for these settings.")
    except BudgetExhausted as e:
        raise Refused(423, f"Live creator discovery has reached this {'demo' if pool == 'judges' else 'build'}'s allowance ({e}).")


def _phrase(pid: str) -> tuple[str, dict]:
    """One niche phrase for `ai_search`, 3–150 characters: the brief's "What you sell", else the products' terms; with
    the pinned versions it came from [P66-I6]."""
    brief = db.latest("brief", pid) or {}
    pr = db.latest("product_research", pid) or {}
    products = [{"id": r["id"], "version": r["version"]} for r in pr.get("products", [])]
    claim = next((c for c in (db.get_record(f) for f in brief.get("facts", [])) if c and c.get("label") == "What you sell"), None)
    if claim:
        text = claim["value"]
    else:
        terms = Counter(t for ref in products for t in (db.get_record(ref["id"], ref["version"]) or {}).get("category_terms", []))
        text = ", ".join(t for t, _ in terms.most_common(3))
    phrase = PHRASE.sub(" ", str(text)).strip()[:150].strip()
    if len(phrase) < 3:
        raise Refused(422, "Landfall doesn't know what you sell yet: correct \"What you sell\" on your brief first.")
    d = db.get_record(f"dos_{pid}_{((db.latest('shortlist', pid) or {}).get('beachhead') or {}).get('city_id')}_creator")
    return phrase, {"brief": brief.get("id") and {"id": brief["id"], "version": brief["version"]},
                    "category": {"claim": claim and {"id": claim["id"], "version": claim["version"]},
                                 "from": "WHAT_YOU_SELL" if claim else "PRODUCT_TERMS"},
                    "products": products, "dossier": d and {"id": d["id"], "version": d["version"]}}


def _location(pid: str, pool: str, city: str, platform: str) -> tuple[str | None, dict | None, str | None]:
    """(name, the lookup's response, its origin): the provider's own name for the city, from its classifier
    (0 credits, 1 request), cached for good across projects: classifier values, not Creator Data. A failed lookup
    returns its response so the search records FAILED, never "no location" [P66-I3]."""
    ck = f"ic_location:{platform}:{city}"
    hit = db.q("SELECT response FROM provider_cache WHERE key=?", ck)
    if hit:
        return json.loads(hit[0]["response"])["name"], None, None
    c = markets.CITIES[city]
    path = "/discovery/classifier/audience-locations/" if platform == "instagram" else f"/discovery/classifier/locations/{platform}/"
    out, origin = _dispatch(pid, pool, "location", {"method": "GET", "path": path, "params": {"search": c["name"], "limit": 10}},
                            0.0, lambda b: 0.0, lookup=True, ref={"city_id": city, "platform": platform, "page": 0})
    if out["status"] != 200:
        return None, out, origin
    items = out["body"] if isinstance(out["body"], list) else out["body"].get("results") or out["body"].get("data") or []
    # ponytail: which field names the location is UNVERIFIED (the documented schema looks copy-pasted); first match wins
    names = [str(i.get("name") or i.get("value") or i.get("full_name") or "") if isinstance(i, dict) else str(i) for i in items]
    name = next((n for n in names if c["name"].lower() in n.lower()), None)
    if name:
        db.x("INSERT OR REPLACE INTO provider_cache VALUES(?,?,?,?)", ck, PROVIDER, json.dumps({"name": name}), db.now())
    return name, out, origin


def _affordable(pool: str, pid: str, requests: int, credits: float) -> bool:
    """Whether the pool can admit `requests` more (a lookup is exempt only from a judge project's own cap) and the
    project one more request at `credits`."""
    whole, mine = ledger.ic_left(pool), ledger.ic_left(pool, pid)
    return whole["requests"] >= requests and mine["requests"] >= 1 and mine["credits"] >= credits - 1e-9


def costs(pid: str, pool: str, platform: str = "instagram") -> dict:
    """What each action would send now and whether the allowance admits it [P66-I7]; a demo sends nothing."""
    demo = _demo(pid)
    cached = bool(db.q("SELECT 1 FROM provider_cache WHERE key=?", f"ic_location:{platform}:"
                       f"{((db.latest('shortlist', pid) or {}).get('beachhead') or {}).get('city_id')}"))
    out = {"search": (1 if cached else 2, LIMIT * RATE), "more": (1, LIMIT * RATE), "enrich": (1, ENRICH_CREDITS)}
    return {k: {"requests": 0 if demo else r, "credits": 0.0 if demo else round(c, 2),
                "ok": demo or _affordable(pool, pid, r, c)} for k, (r, c) in out.items()}


def _synthetic(page: int) -> dict:
    data = json.loads(SYNTHETIC.read_text())
    return {"status": 200, "body": {**data, "accounts": data["accounts"][page * LIMIT:(page + 1) * LIMIT]}}


def search(pid: str, city: str, session: str, platform: str = "instagram", follower_min: int | None = None,
           more_of: str | None = None, dry_run: bool = False) -> dict:
    """POST …/plans/{city}/creators/search: one discovery request (plus a location lookup the first time per city)."""
    bh = ((db.latest("shortlist", pid) or {}).get("beachhead") or {}).get("city_id")
    if city != bh:
        raise Refused(409, "Creator discovery runs for your beachhead city.")
    if platform not in PROFILE_URL:
        raise Refused(422, "Choose Instagram, TikTok or YouTube.")
    page = 0
    if more_of:
        prev = db.get_record(more_of)
        if not prev or prev["project_id"] != pid or prev["kind"] != "creator_search":
            raise Refused(404, "No such search.")
        page, platform, follower_min = prev["request"]["page"] + 1, prev["platform"], prev["inputs"]["follower_min"]
        if page > MAX_PAGE:
            raise Refused(422, "That's every page Landfall shows for this search.")
    pool = pool_of(session)
    c = costs(pid, pool, platform)["more" if more_of else "search"]
    est = {"requests": c["requests"], "credits": c["credits"]}
    if dry_run:
        return {"est": est, "left": ledger.ic_left(pool, pid), "unavailable": unavailable(pid)}
    with _flight_lock:
        if (pid, city) in _flight:
            raise Refused(409, "A creator search for this city is already running.")
        _flight.add((pid, city))
    ctx: dict = {"follower_min": follower_min, "page": page}
    try:
        ctx["phrase"], ctx["pins"] = _phrase(pid)
        fmin = max(FOLLOWER_MIN, follower_min or 0)
        if _demo(pid):
            if why := unavailable(pid):
                raise Refused(503, why)
            loc, origin, out = markets.CITIES[city]["name"], "SYNTHETIC", _synthetic(page)
            ctx["sent"] = _request(platform, loc, ctx["phrase"], fmin, page)
        else:
            loc, lk, lk_origin = _location(pid, pool, city, platform)
            if not loc:
                if lk and lk["status"] != 200:
                    return _store(pid, city, platform, pool, ctx, "FAILED", lk, lk_origin, note="The location lookup "
                                  "failed, so no creators were searched.")
                return _store(pid, city, platform, pool, ctx, "EMPTY", None, lk_origin or "LIVE",
                              note=f"Influencers.club has no location matching {markets.CITIES[city]['name']}.")
            ctx["location"], ctx["sent"] = loc, _request(platform, loc, ctx["phrase"], fmin, page)
            out, origin = _dispatch(pid, pool, "discovery", ctx["sent"], LIMIT * RATE,
                                    lambda b: round(len(b.get("accounts") or []) * RATE, 4),
                                    ref={"city_id": city, "platform": platform, "page": page})
            ctx["credits"] = {"reserved": LIMIT * RATE, "charged": round(len(out["body"].get("accounts") or []) * RATE, 4)
                              if out["status"] == 200 and origin == "LIVE" else 0.0}
        ctx["location"] = loc
        status = "COMPLETED" if out["status"] == 200 and out["body"].get("accounts") else "EMPTY" if out["status"] == 200 else "FAILED"
        return _store(pid, city, platform, pool, ctx, status, out, origin)
    except Unknown as e:
        return _store(pid, city, platform, pool, ctx, "UNKNOWN", None, "LIVE", note=f"Outcome unknown ({e}); it may have been charged.")
    finally:
        with _flight_lock:
            _flight.discard((pid, city))


def _request(platform: str, loc: str, phrase: str, fmin: int, page: int) -> dict:
    filters: dict = {"ai_search": phrase}
    if platform == "instagram":  # the only platform with an audience-location filter (DOCS)
        filters["audience"] = {"location": [{"name": loc, "type": "city", "min_pct": MIN_PCT}]}
    else:
        filters["location"] = [loc]
    filters["number_of_subscribers" if platform == "youtube" else "number_of_followers"] = {"min": fmin}
    return {"method": "POST", "path": "/discovery/", "params": {"return_filter_values": "true"},  # nlp_search: never sent
            "body": {"platform": platform, "filters": filters, "sort": {"sort_by": "relevancy", "sort_order": "desc"},
                     "paging": {"limit": LIMIT, "page": page}}}


def _store(pid, city, platform, pool, ctx, status, out, origin, note=None, rid=None) -> dict:
    """A CreatorSearch: the exact request sent (never the key), the credits, and the pinned inputs [P66-I6]."""
    body = (out or {}).get("body") or {}
    body = body if isinstance(body, dict) else {}
    accounts = [{"user_id": a.get("user_id"), "username": (a.get("profile") or {}).get("username"),
                 "full_name": (a.get("profile") or {}).get("full_name"), "followers": (a.get("profile") or {}).get("followers"),
                 "engagement_percent": (a.get("profile") or {}).get("engagement_percent"),
                 "matched_filters": a.get("matched_filters") or {}} for a in body.get("accounts") or []]
    accounts.sort(key=lambda a: -(a["followers"] or 0))  # owner, 27 Oct 2026: the biggest creator first (choose() indexes this order)
    for a in accounts:  # no picture: its URL expires in 24 h
        a["profile_url"] = {"value": PROFILE_URL[platform].format(a["username"]), "built": True} if a["username"] else None
    n = db.q("SELECT COUNT(DISTINCT id) n FROM records WHERE kind='creator_search' AND project_id=?", pid)[0]["n"]
    sl = db.latest("shortlist", pid)
    rec = db.put_record("creator_search", pid, {
        "city_id": city, "platform": platform, "pool": pool, "origin": origin, "retrieved_at": db.now(),
        "inputs": {"beachhead": {"shortlist": sl["version"]}, "phrase": ctx.get("phrase"), "follower_min": ctx.get("follower_min"),
                   **(ctx.get("pins") or {}),
                   "audience_profile": (db.latest("audience_profile", pid) or {}).get("version"),
                   "creator_budget": plans.creator_fee(pid, city)},  # S5-12: pinned, never a filter (6.6 R7)
        "request": {"page": ctx.get("page", 0), "limit": LIMIT, "location": ctx.get("location"),
                    "location_means": None if not ctx.get("location") else "AUDIENCE" if platform == "instagram" else "CREATOR",
                    "follower_min_sent": ((ctx.get("sent") or {}).get("body") or {}).get("filters", {}).get(
                        "number_of_subscribers" if platform == "youtube" else "number_of_followers", {}).get("min"),
                    "sent": ctx.get("sent")},
        "credits": ctx.get("credits"),
        "status": status, "note": note, "total": body.get("total"), "credits_left_reported": body.get("credits_left"),
        "error": {"status": out["status"], "error_code": body.get("error_code"),
                  "detail": body.get("detail") or body.get("error")} if status == "FAILED" else None,
        "accounts": accounts,
        "retention": {"provider": PROVIDER, "acquired_at": db.now(), "on_termination": "delete within 30 days"}},
        rid=rid or f"cs_ic_{pid}_{city}_{n + 1}")
    db.event(pid, "founder", {"stage": "Plan", "task": "Searched for creators", "detail": f"{markets.CITIES[city]['name']} · "
                              f"{platform} · {status.lower()} · {len(accounts)} found", "search": rec["id"]})
    return {"search_id": rec["id"], "version": rec["version"], "status": status}


def _audience_location(a: dict) -> str | None:
    """The per-creator audience match, only from what the provider says it matched (PROVIDER_ESTIMATE)."""
    v = (a.get("matched_filters") or {}).get("audience_location")
    return ", ".join(map(str, v)) if isinstance(v, list) and v else str(v) if v else None


def choose(pid: str, sid: str, i: int) -> dict:
    """POST …/creators/searches/{id}/accounts/{i}/choose [P66-R2]: a standalone project Lead, keyed by the built profile
    URL so a creator the founder also added by URL is one recipient, not two."""
    s = db.get_record(sid)
    if not s or s["project_id"] != pid or s["kind"] != "creator_search" or not 0 <= i < len(s["accounts"]):
        raise Refused(404, "No such creator in this search.")
    a = s["accounts"][i]
    p = a["profile_url"] and creators.canonical(a["profile_url"]["value"])
    if not p:
        raise Refused(422, "This creator has no profile address Landfall can build.")
    from .specialist import _k
    key = f"creator|{p['canonical_url'].lower()}"
    rid = f"lead_{_k(pid, s['city_id'], key)}"
    if (cur := db.get_record(rid)) and cur.get("found_by"):
        return {"lead_id": rid}
    found = {"search": {"id": s["id"], "version": s["version"]}, "account_index": i, "user_id": a["user_id"],
             "platform": s["platform"], "origin": s["origin"]}
    if cur:  # the founder added this profile already: keep their lead, note where else it was found
        db.put_record("lead", pid, {**_fields(cur), "found_by": found}, rid=rid)
        return {"lead_id": rid}
    db.put_record("lead", pid, {
        "key": key, "name": a["full_name"] or a["username"], "origin": "INFLUENCERS_CLUB", "source": PROVIDER,
        "city_id": s["city_id"], "basis": "FOUND_BY_PROVIDER", "found_by": found,
        "creator": {**p, "display_name": a["full_name"] or p["handle"], "identity": "PROVIDER",
                    "location": {"state": "SEARCH_FILTER", "text": s["request"]["location"]}  # TikTok, YouTube [P66-I5]
                    if s["request"].get("location_means") == "CREATOR" else {"state": "UNKNOWN", "text": None},
                    "metrics": [{"name": "followers", "value": a["followers"], "as_of": s["retrieved_at"]},
                                {"name": "engagement_percent", "value": a["engagement_percent"], "as_of": s["retrieved_at"]}]},
        "audience_location": s["platform"] == "instagram" and _audience_location(a) and   # Instagram only [P66-I5]
                             {"text": _audience_location(a), "state": "PROVIDER_ESTIMATE", "by": ATTRIBUTION} or None,
        "contact": None, "willingness": "UNKNOWN", "retention": s["retention"]}, rid=rid)
    return {"lead_id": rid}


def _current(kind: str, pid: str) -> list[dict]:
    """The latest version of each record of a kind, in creation order."""
    return list({r["id"]: r for r in db.records(kind, pid)}.values())


def _fields(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")}


def enrich(pid: str, lead_id: str, session: str) -> dict:
    """POST …/leads/{id}/enrich: the founder's "Find contact" for a creator they chose; 1 request, ≤ 0.2 credits."""
    lead = db.get_record(lead_id)
    if not lead or lead["project_id"] != pid or not lead.get("found_by"):
        raise Refused(404, "Only a creator found by Influencers.club can be looked up there.")
    if lead.get("contact"):
        return {"lead_id": lead_id, "contact": lead["contact"]}
    pool = pool_of(session)
    request = {"method": "POST", "path": "/creators/enrich/handle/profile/",
               "body": {"handle": lead["creator"]["handle"].lstrip("@"), "platform": lead["found_by"]["platform"],
                        "email_required": "preferred"}}
    base = {"lead_id": lead_id, "lead_key": lead["key"], "pool": pool, "request": request}
    if _demo(pid):
        out, origin = {"status": 200, "body": {"result": {"email": None, "email_status": "no_email"}}}, "SYNTHETIC"
    else:
        try:
            out, origin = _dispatch(pid, pool, "enrich", request, ENRICH_CREDITS,
                                    lambda b: ENRICH_CREDITS if b.get("result") else 0.0, ref={"lead_id": lead_id})
        except Unknown as e:
            db.put_record("creator_enrichment", pid, {**base, "status": "UNKNOWN", "credits": {"reserved": ENRICH_CREDITS},
                                                      "note": f"Outcome unknown ({e}); it may have been charged."})
            raise Refused(502, "Influencers.club didn't answer; the request may have been charged. Nothing is retried.")
    body = out["body"] if isinstance(out["body"], dict) else {}
    res = body.get("result") or {}
    ok = out["status"] == 200
    db.put_record("creator_enrichment", pid, {**base, "status": "COMPLETED" if ok else "FAILED", "origin": origin,
                                              "retrieved_at": db.now(), "email_status": res.get("email_status"),
                                              "location": res.get("location"), "retention": lead.get("retention"),
                                              "credits": {"reserved": ENRICH_CREDITS, "charged": ENRICH_CREDITS if ok and res and origin == "LIVE" else 0.0}})
    if not ok:
        raise Refused(502, f"Influencers.club refused the lookup ({out['status']}).")
    # the delivery contract's contact (kind email) with its provenance [P66-I2]
    contact = {"kind": "email", "value": res["email"], "email_status": res.get("email_status"),
               "source": "influencers_club_enrichment", "acquired_at": db.now()} \
        if res.get("email") and res.get("email_status") != "invalid" else None
    loc = res.get("location")
    new = db.put_record("lead", pid, {**_fields(lead), "contact": contact,
                                      "creator": {**lead["creator"], "location": {"state": "PROVIDER", "text": loc} if loc
                                                  else lead["creator"]["location"]}}, rid=lead_id)
    from . import delivery
    updated = delivery.refresh_contact(pid, new) if contact else []
    return {"lead_id": lead_id, "contact": contact, "packages": updated}


def view(pid: str, city: str, session: str) -> dict:
    """GET …/plans/{city}/creators: saved searches and chosen creators only; never dispatches."""
    pool = pool_of(session)
    searches = [s for s in _current("creator_search", pid) if s["city_id"] == city]
    chosen = [lead for lead in _current("lead", pid) if lead.get("found_by") and lead["city_id"] == city]
    return {"available": unavailable(pid) is None, "reason": unavailable(pid), "pool": pool,
            "left": ledger.ic_left(pool, pid), "costs": costs(pid, pool), "attribution": ATTRIBUTION,
            "demo": _demo(pid), "creator_budget": (fee := plans.creator_fee(pid, city)),
            "creator_budget_line": fee["text"] if fee else "Not costed in your signed plan yet",
            "qloo_context": [a["name"] for a in (ranking.audience_view(pid) or {}).get("audiences", [])],
            "searches": [{**{k: s[k] for k in ("id", "version", "platform", "origin", "retrieved_at", "status", "note",
                                               "total", "request", "error")},
                          "accounts": [{**a, "audience_location": _audience_location(a) if s["platform"] == "instagram" else None,
                                        "creator_location": s["request"]["location"]
                                        if s["request"].get("location_means") == "CREATOR" else None} for a in s["accounts"]],
                          "next_page": s["request"]["page"] < MAX_PAGE and len(s["accounts"]) == LIMIT}
                         for s in searches],
            "chosen": [{"lead_id": lead["id"], "key": lead["key"], "name": lead["name"], "contact": lead.get("contact"),
                        "creator_location": lead["creator"].get("location"), "audience_location": lead.get("audience_location"),
                        "enrichment": next((e["status"] for e in reversed(db.records("creator_enrichment", pid))
                                            if e.get("lead_key") == lead["key"]), None),
                        "found_by": lead["found_by"]} for lead in chosen]}


def recover() -> None:
    """At startup [P66-R4]: a reservation the process never finalized becomes UNKNOWN, counted in full, never resent."""
    entries = ledger._entries()
    done = {e.get("reservation") for e in entries if e.get("kind") == "attempt"}
    for e in entries:
        if e.get("kind") != "reserve" or e.get("provider") != PROVIDER or e["id"] in done:
            continue
        _materialize(e)  # first, under an id from the reservation, so an interrupted recovery neither loses nor repeats it
        ledger.finalize(e["id"], PROVIDER, e["operation"], outcome="unknown", pool=e["pool"], project_id=e.get("project_id"),
                        credits_charged=e["credits_reserved"], unresolved=True, error="process stopped mid-request")


def _materialize(e: dict) -> None:
    """The founder-visible UNKNOWN record of an interrupted request [P66-I4]: a search (lookup or discovery) or a
    contact lookup, with the platform, page or lead it was sent for."""
    pid = e.get("project_id")
    if not pid or not db.q("SELECT 1 FROM projects WHERE id=?", pid):
        return
    note = "Landfall stopped while this request was in flight; it may have been charged. Nothing is resent."
    if e["operation"] == "enrich":
        rid = f"ce_rec_{e['id']}"
        lead = db.get_record(e.get("lead_id") or "") or {}
        if not db.get_record(rid):
            db.put_record("creator_enrichment", pid, {"lead_id": e.get("lead_id"), "lead_key": lead.get("key"), "pool": e["pool"],
                                                      "status": "UNKNOWN", "credits": {"reserved": e["credits_reserved"]},
                                                      "note": note}, rid=rid)
    elif e.get("city_id") and not db.get_record(f"cs_rec_{e['id']}"):
        _store(pid, e["city_id"], e.get("platform", "instagram"), e["pool"], {"page": e.get("page", 0),
               "credits": {"reserved": e["credits_reserved"]}}, "UNKNOWN", None, "LIVE",
               note=("The location lookup: " if e["operation"] == "location" else "") + note, rid=f"cs_rec_{e['id']}")
