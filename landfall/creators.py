"""Creator identity tools for the creator specialist (BUILD-PLAN §4.5.5; CONTRACTS B6.5 Lead.creator, Retention).

- `canonical`: a profile URL → {platform, handle, canonical_url}. Instagram is canonicalised and never fetched.
- `oembed`: TikTok's (VERIFIED LIVE) and YouTube's (UNVERIFIED) public oEmbed through Landfall's fetcher: the
  canonical author URL and display name only; no bio, location or counts.
- `youtube_channel`: YouTube Data API `channels.list` (1 quota unit), only when the owner's key is set. Every field
  it returns carries `retention` (30 days, P45-R7) and `sweep` redacts it everywhere it was copied.
- `identity`: IDENTIFIED needs two agreeing signals of different kinds; one signal is POSSIBLE.
Provider stages: `creator_profile`, `youtube_channel`.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

import httpx

from . import config, db, fetch, ledger
from .cassette import Cassette

YOUTUBE_API = "https://www.googleapis.com/youtube/v3/channels"
RETAIN_DAYS = 30  # YouTube Developer Policies III.E.4: non-authorized data is refreshed or deleted within 30 days
_HANDLE = r"[A-Za-z0-9._-]{2,64}"
PROFILE = [
    ("tiktok", re.compile(rf"^/@({_HANDLE})/?$"), "https://www.tiktok.com/@{}"),
    ("youtube", re.compile(rf"^/@({_HANDLE})/?$"), "https://www.youtube.com/@{}"),
    ("youtube", re.compile(r"^/channel/(UC[A-Za-z0-9_-]{22})/?$"), "https://www.youtube.com/channel/{}"),
    ("instagram", re.compile(rf"^/({_HANDLE})/?$"), "https://www.instagram.com/{}/"),
]
HOSTS = {"tiktok": "tiktok.com", "youtube": "youtube.com", "instagram": "instagram.com"}
NOT_PROFILES = {"p", "reel", "reels", "explore", "stories", "accounts", "watch", "shorts", "results", "feed"}


def canonical(url: str) -> dict | None:
    """The profile a URL points at, or None for a post, a video, a search or another site."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").removeprefix("www.").removeprefix("m.")
    for platform, rx, fmt in PROFILE:
        if host == HOSTS[platform] and (m := rx.match(parts.path or "")) and m[1].lower() not in NOT_PROFILES:
            return {"platform": platform, "handle": m[1] if m[1].startswith("UC") else "@" + m[1].lstrip("@"),
                    "canonical_url": fmt.format(m[1])}
    return None


def oembed(profile: dict, cassette: Cassette) -> dict | None:
    """{author_name, author_url} from the platform's public oEmbed, or None (unavailable is not a mismatch)."""
    if profile["platform"] not in ("tiktok", "youtube"):
        return None  # Instagram is never fetched
    endpoint = f"https://www.{HOSTS[profile['platform']]}/oembed?url={quote(profile['canonical_url'], safe='')}"
    if not fetch.robots_allows(endpoint, fetch.robots_for(endpoint, cassette, "creator_profile")):
        return None
    r = fetch.get(endpoint, cassette, "creator_profile")
    try:
        body = json.loads(r.get("body") or "") if r.get("status") == 200 else {}
    except ValueError:
        body = {}
    if not body.get("author_url"):
        return None
    return {"author_name": body.get("author_name"), "author_url": body["author_url"]}


def youtube_enabled() -> bool:
    """Off by default even with a key (P45-I3): its 30-day deletion must reach checkpoints, task results, specialist
    transcripts and the model exchanges that quote them, and that sweep isn't built. LANDFALL_ENABLE_YOUTUBE=1 is for
    offline tests and a future slice that completes it; never set it for live runs until then."""
    return config.env("LANDFALL_ENABLE_YOUTUBE") == "1" and bool(config.env("youtube_api_key"))


def _live_channels(params: dict) -> dict:
    rid = ledger.reserve("youtube", "channels.list")
    outcome = "aborted"
    try:  # the key is added only here: never in the canonical request, a capture, a log or a prompt
        r = httpx.get(YOUTUBE_API, params={**params, "key": config.env("youtube_api_key")}, timeout=15)
        outcome = str(r.status_code)
        return {"status": r.status_code, "body": r.text}
    except httpx.TransportError as e:
        outcome = "transport_error"
        return {"status": 0, "error": type(e).__name__}
    finally:
        ledger.finalize(rid, "youtube", "channels.list", outcome=outcome)


def youtube_channel(profile: dict, cassette: Cassette, now: datetime | None = None) -> dict | None:
    """Self-declared country, a subscriber count observed today (rounded by YouTube to 3 significant figures) and the
    description, each with retention. None without a key, for a non-YouTube profile, or when unavailable."""
    if not youtube_enabled() or profile["platform"] != "youtube":
        return None
    h = profile["handle"]
    params = {"part": "snippet,statistics", **({"id": h} if h.startswith("UC") else {"forHandle": h})}
    r = cassette.call("youtube", "channels.list", params, lambda: _live_channels(params), "youtube_channel")
    items = []
    if r.get("status") == 200:
        try:
            items = json.loads(r.get("body") or "{}").get("items") or []
        except ValueError:
            pass
    if not items:
        return None
    it, now = items[0], now or datetime.now(timezone.utc)
    stats, snip = it.get("statistics") or {}, it.get("snippet") or {}
    retention = {"provider": "youtube", "acquired_at": now.isoformat(timespec="seconds"),
                 "delete_by": (now + timedelta(days=RETAIN_DAYS)).isoformat(timespec="seconds")}
    out = {"channel_id": it.get("id"), "title": snip.get("title"), "description": (snip.get("description") or "")[:500],
           "country": snip.get("country"), "retention": retention}
    if stats.get("subscriberCount") and not stats.get("hiddenSubscriberCount"):
        out["metric"] = {"name": "subscribers", "value": int(stats["subscriberCount"]), "source": "YouTube Data API",
                         "observed_at": now.date().isoformat(), "note": "rounded by YouTube to 3 significant figures"}
    return out


def identity(evidence: list[dict], by_founder: bool = False) -> str:
    """IDENTIFIED: two agreeing signals of different kinds (oEmbed, a page naming the handle, a page linking the
    profile, the platform API). One signal, or a similar handle alone, is POSSIBLE. A founder's URL is YOU_SAID."""
    if by_founder:
        return "YOU_SAID"
    if any(e.get("agrees") is False for e in evidence):  # a conflicting platform identity never upgrades (P45-I4)
        return "POSSIBLE"
    return "IDENTIFIED" if len({e["kind"] for e in evidence}) >= 2 else "POSSIBLE"


def keep_metric(metric: dict) -> bool:
    """A metric is stored only with a source and an observation date; anything else is an unused figure."""
    return bool(metric.get("source") and metric.get("observed_at"))


# ---- retention sweep (P45-R7): every copy of a provider-derived field, redacted once its delete_by passes ----
def _expired(node, now: datetime) -> bool:
    r = isinstance(node, dict) and node.get("retention")
    return bool(r and r.get("delete_by") and not node.get("redacted")
                and datetime.fromisoformat(r["delete_by"]) <= now)


def _redact(node, now: datetime):
    """Replace every expired provider-derived block with its retention stamp and a checksum of what was removed."""
    if _expired(node, now):
        digest = hashlib.sha256(json.dumps(node, sort_keys=True).encode()).hexdigest()
        return {"retention": node["retention"], "redacted": True, "sha256": digest}
    if isinstance(node, dict):
        return {k: _redact(v, now) for k, v in node.items()}
    if isinstance(node, list):
        return [_redact(v, now) for v in node]
    return node


def sweep(now: datetime | None = None) -> dict:
    """Records, provider_cache and captured exchanges. A YouTube exchange is replaced by a checksummed marker, so a
    replay answers "unavailable" instead of the deleted data. ponytail: specialist transcripts join this sweep when
    the specialist runner stores them."""
    now = now or datetime.now(timezone.utc)
    n = {"records": 0, "cache": 0, "exchanges": 0}
    for r in db.q("SELECT id, version, payload FROM records WHERE payload LIKE '%\"retention\"%'"):
        payload = json.loads(r["payload"])
        clean = _redact(payload, now)
        if clean != payload:
            db.x("UPDATE records SET payload=? WHERE id=? AND version=?", json.dumps(clean), r["id"], r["version"])
            n["records"] += 1
    cutoff = (now - timedelta(days=RETAIN_DAYS)).isoformat(timespec="seconds")
    n["cache"] = db.x("DELETE FROM provider_cache WHERE provider='youtube' AND fetched_at <= ?", cutoff)
    for path in config.PRIVATE.rglob("exchanges.jsonl") if config.PRIVATE.exists() else []:
        lines, changed = path.read_text().split("\n"), False
        for i, line in enumerate(lines):
            if '"provider": "youtube"' not in line:
                continue
            ex = json.loads(line)
            at = (ex.get("provenance") or {}).get("at")
            if ex["response"].get("redacted") or not at or at > cutoff:
                continue
            ex["response"] = {"status": 0, "redacted": True, "error": "deleted under the YouTube 30-day rule",
                              "sha256": hashlib.sha256(json.dumps(ex["response"], sort_keys=True).encode()).hexdigest()}
            lines[i], changed = json.dumps(ex, ensure_ascii=False), True
            n["exchanges"] += 1
        if changed:
            path.write_text("\n".join(lines))
    return n
