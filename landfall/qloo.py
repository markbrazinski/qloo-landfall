"""Qloo adapter: one GET per attempt through the cassette and the ledger. The key never leaves this module.

Every exchange for a project also writes a project usage row and remembers the Qloo ids it returned (`qloo_refs`), so
validators can require that an id came back in a Qloo response recorded for this project (CONTRACTS B3)."""
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import config, db, ledger
from .cassette import Cassette, key_of

CACHE_DAYS = 7  # BUILD-PLAN §3

KEEP = ("content-type", "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining",
        "x-month-ratelimit-limit", "x-month-ratelimit-remaining", "x-second-ratelimit-limit", "x-second-ratelimit-remaining")
RETRYABLE = {429, 500, 502, 503, 504}


PACE_S = 1 / 4.5  # ≥ 222 ms between dispatch starts = 4.5 req/s under Qloo's 5/s limit (owner, 25 Oct 2026)
_pace = threading.Lock()
_last = [0.0]
LOG_BODY = 2048  # bytes of a non-200 body kept in the attempt log


def _wait_turn() -> None:
    """The shared Qloo pacer: one process-wide lock, every caller (scan, identity, profile, founder search)."""
    with _pace:
        wait = _last[0] + PACE_S - time.monotonic()
        if wait > 0:
            threading.Event().wait(wait)  # not time.sleep, which tests stub out for retry backoff
        _last[0] = time.monotonic()


def _attempt(path: str, params: dict) -> dict:
    rid = ledger.reserve("qloo", path)
    out = {"status": 0, "headers": {}, "body": "", "error": "aborted"}
    try:
        _wait_turn()
        r = httpx.get(config.QLOO_BASE + path, params=params, timeout=20,
                      headers={"X-Api-Key": config.QLOO_KEY, "Accept": "application/json"})
        out = {"status": r.status_code, "headers": {k: r.headers[k] for k in KEEP if k in r.headers}, "body": r.text}
    except httpx.HTTPError as e:
        out = {"status": 0, "headers": {}, "body": "", "error": type(e).__name__}
    except Exception as e:  # an unknown failure after dispatch: logged and counted, but not retried (closure C1)
        out = {"status": 0, "headers": {}, "body": "", "error": type(e).__name__, "final": True}
    finally:
        remaining = out["headers"].get("x-month-ratelimit-remaining")
        ledger.finalize(rid, "qloo", path, status=out["status"], cost_known=False,
                        outcome=str(out["status"]) if out["status"] else "transport_error",
                        month_remaining=int(remaining) if remaining and remaining.isdigit() else None)
    return out


def _live(path: str, params: dict) -> dict:
    """Up to 3 attempts on 429/5xx/network errors, honouring Retry-After; every attempt is ledgered and kept in
    `attempt_log`, so 503, 503, 200 leaves all three outcomes inspectable beside the final response (P3-03).
    Allowance denied before the first attempt raises BudgetExhausted (nothing dispatched); denied before a retry,
    the attempts already made are returned with the denial noted."""
    log: list[dict] = []
    out: dict = {}
    for n in range(3):
        try:
            out = _attempt(path, params)
        except ledger.BudgetExhausted as e:
            if not log:
                raise
            out = {**out, "error": f"retry not sent: {e}"}
            break
        log.append({"n": n + 1, "status": out["status"], "headers": out["headers"], "error": out.get("error"),
                    "at": db.now(), **({"body": out["body"][:LOG_BODY]} if out["status"] != 200 else {})})
        if out.pop("final", False) or (out["status"] not in RETRYABLE and out["status"] != 0):
            break
        if n < 2:
            wait = out["headers"].get("retry-after")
            time.sleep(float(wait) if wait and wait.replace(".", "").isdigit() else 0.5 * 2 ** n)
    return {**out, "attempts": len(log), "attempt_log": log}


def attempts_of(response: dict) -> int:
    """Live dispatch attempts behind a recorded response. A cache hit is 0; a legacy exchange (recorded before the
    attempt log existed) reads as its `attempts`, else 1."""
    if response.get("cache"):
        return 0
    if "attempt_log" in response:
        return len(response["attempt_log"])
    return response.get("attempts", 1)


def _readable(out: dict, cache_ok) -> bool:
    try:
        return cache_ok is None or bool(cache_ok(out))
    except Exception:
        return False


def _cached_or_live(key: str, path: str, params: dict, origin: list, cache_ok=None) -> dict:
    """A successful answer is reused for 7 days. A cache hit is reused evidence: it carries its original fetch time and
    0 attempts, never the attempts of the request that first fetched it."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=CACHE_DAYS)).isoformat(timespec="seconds")
    hit = db.q("SELECT response, fetched_at FROM provider_cache WHERE key=? AND fetched_at>?", key, cutoff)
    if hit and _readable(json.loads(hit[0]["response"]), cache_ok):
        origin.append("cache")
        return {**json.loads(hit[0]["response"]), "attempts": 0, "attempt_log": [],
                "cache": {"fetched_at": hit[0]["fetched_at"]}}
    if hit:  # an unreadable answer cached before the check existed is never served (closure, blocker 1)
        db.x("DELETE FROM provider_cache WHERE key=?", key)
    origin.append("live")
    out = _live(path, params)
    if out["status"] == 200 and _readable(out, cache_ok):  # never cache an answer the caller can't read
        db.x("INSERT OR REPLACE INTO provider_cache VALUES(?,?,?,?)", key, "qloo", json.dumps(out), db.now())
    return out


def get(path: str, params: dict, cassette: Cassette, stage: str = "", project_id: str | None = None,
        run_id: str | None = None, cache_ok=None) -> dict:
    """One logical lookup. Project usage gets one `live` row per dispatch attempt, or one `cache` / `replay` row
    (0 dispatches), so "Qloo N sent" equals the project's ledger delta (P3-03)."""
    params = {k: str(v) for k, v in sorted(params.items())}
    request = {"path": path, "params": params}
    origin: list[str] = []
    out = cassette.call("qloo", path, request, lambda: _cached_or_live(key_of("qloo", path, request), path, params, origin, cache_ok),
                        stage)
    if project_id:
        how = origin[0] if origin else "replay"
        rows = ([(a["status"], a["n"]) for a in out.get("attempt_log", [])] if how == "live"
                else [(out.get("status"), None)])
        for status, n in rows:
            db.x("INSERT INTO usage(project_id, run_id, at, provider, stage, replayed, est_usd, status, origin, attempt) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?)", project_id, run_id, db.now(), "qloo", stage, int(how == "replay"), 0.0,
                 status, how, n)
        remember(project_id, out)
    return {**out, "origin": origin[0] if origin else "replay"}


def tag_ref(t: dict) -> dict:
    """QlooTag: `/search` embeds `tag_id`, `/v2/tags` returns `id`; both become `qloo_id`."""
    return {"qloo_id": t.get("tag_id") or t.get("id"), "name": t.get("name"), "type": t.get("type")}


def rows(out: dict) -> tuple[list[dict], list[dict]]:
    """(entities, tags) from a `/search` or `/v2/tags` body; empty when the body is not a successful answer."""
    try:
        res = json.loads(out.get("body") or "{}").get("results", [])
    except (ValueError, AttributeError):
        return [], []
    if isinstance(res, list):
        return [e for e in res if isinstance(e, dict) and e.get("entity_id")], []
    if isinstance(res, dict):  # only well-formed rows: classifiers judge the rest (review slice 3, blocker 1)
        ents = res.get("entities") if isinstance(res.get("entities"), list) else []
        tags = res.get("tags") if isinstance(res.get("tags"), list) else []
        return [e for e in ents if isinstance(e, dict) and e.get("entity_id")], [t for t in tags if isinstance(t, dict)]
    return [], []


def remember(project_id: str, out: dict) -> None:
    if out.get("status") != 200:
        return
    entities, tags = rows(out)
    refs = [(e["entity_id"], "entity", e.get("name"), _entity_type(e)) for e in entities]
    refs += [(r["qloo_id"], "tag", r["name"], r["type"]) for t in tags + [x for e in entities for x in e.get("tags") or []
                                                                           if isinstance(x, dict)]
             if (r := tag_ref(t))["qloo_id"]]
    for qid, kind, name, typ in refs:
        db.x("INSERT OR IGNORE INTO qloo_refs VALUES(?,?,?,?,?)", project_id, qid, kind, name, typ)


def _entity_type(e: dict) -> str:
    t = e.get("subtype") or e.get("types") or "urn:entity:brand"
    return t[0] if isinstance(t, list) and t else t if isinstance(t, str) else "urn:entity:brand"


def recorded(project_id: str, qloo_id: str) -> dict | None:
    r = db.q("SELECT * FROM qloo_refs WHERE project_id=? AND qloo_id=?", project_id, qloo_id)
    return dict(r[0]) if r else None


# ---- the city target for community and creator investigations (QLOO-CAPABILITIES R5, R11, R13 and §10.6) ----
def results_of(out: dict) -> dict | None:
    """`results` of a successful `/v2/insights` answer, else None: an unreadable answer is failed, never empty."""
    if out.get("status") != 200:
        return None
    try:
        res = json.loads(out.get("body") or "{}").get("results")
    except (ValueError, AttributeError):
        return None
    return res if isinstance(res, dict) else None


def heatmap_cells(out: dict) -> list[dict] | None:
    """`urn:heatmap` geohash cells, most related first; only cells with coordinates and a numeric affinity. Affinity
    orders cells inside this one request only (rule 2), and tracks popularity closely (§5e)."""
    res = results_of(out)
    if res is None or not isinstance(res.get("heatmap"), list):
        return None
    cells = []
    for c in res["heatmap"]:
        loc, q = (c.get("location") or {}) if isinstance(c, dict) else {}, (c.get("query") or {}) if isinstance(c, dict) else {}
        vals = (loc.get("latitude"), loc.get("longitude"), q.get("affinity"))
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            cells.append({"lat": vals[0], "lon": vals[1], "geohash": loc.get("geohash"), "affinity": vals[2],
                          "popularity": q.get("popularity")})
    return sorted(cells, key=lambda c: -c["affinity"])


def place_row(e: dict, rank: int) -> dict:
    """A place row with the neighborhood Qloo files it under (`properties.geocode.name`, VERIFIED LIVE CQ-E)."""
    props, loc = e.get("properties") or {}, e.get("location") or {}
    return {"qloo_id": e.get("entity_id"), "name": e.get("name"), "rank": rank, "affinity": (e.get("query") or {}).get("affinity"),
            "lat": loc.get("lat"), "lon": loc.get("lon"), "address": props.get("address"), "website": props.get("website"),
            "hood": (props.get("geocode") or {}).get("name") or props.get("neighborhood")}
