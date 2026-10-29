"""Access grants (DEPLOYMENT-PLAN §5, A1; owner, 27 Oct 2026: "judges get live stuff, and everyone else gets mocked
stuff"). A judge or owner link binds a fresh browser session to a grant; a session without one gets the recorded demo
when LANDFALL_REQUIRE_GRANT=on. Only the SHA-256 of a link id is stored; the raw id is shown once, at creation."""
import hashlib
import secrets

from . import config, db

KINDS = ("judge", "owner")


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create(kind: str, label: str | None = None) -> str:
    """A new link id (128 bits). Returned once; never stored or logged."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    raw = secrets.token_urlsafe(16)
    db.x("INSERT INTO grants VALUES(?,?,?,?,NULL)", _hash(raw), kind, label or kind, db.now())
    return raw


def revoke(label_or_hash: str) -> int:
    """Revokes every live grant with this label (or this hash); bound sessions lose it on their next request."""
    return db.x("UPDATE grants SET revoked_at=? WHERE revoked_at IS NULL AND (label=? OR hash=?)",
                db.now(), label_or_hash, label_or_hash)


def list() -> list[dict]:  # noqa: A001 (the owner CLI's name)
    return [dict(r) for r in db.q("SELECT hash, kind, label, created_at, revoked_at FROM grants ORDER BY created_at")]


def bind(raw: str) -> str | None:
    """A FRESH session id bound to the grant `raw` names, or None if it is unknown or revoked. Never upgrades an
    existing session id, so a session id planted beforehand never gains a grant (fixation)."""
    h = _hash(raw or "")
    if not db.q("SELECT 1 FROM grants WHERE hash=? AND revoked_at IS NULL", h):
        return None
    sid = secrets.token_urlsafe(18)
    db.x("INSERT INTO grant_sessions VALUES(?,?,?)", sid, h, db.now())
    return sid


def kind_of(session: str | None) -> str | None:
    """"judge" | "owner" | None, read on every call, so a revocation applies on the session's next request."""
    rows = db.q("SELECT g.kind FROM grant_sessions s JOIN grants g ON g.hash=s.grant_hash "
                "WHERE s.session=? AND g.revoked_at IS NULL", session or "")
    return rows[0]["kind"] if rows else None


def required() -> bool:
    return config.env("LANDFALL_REQUIRE_GRANT") == "on"


def track(session: str | None) -> str:
    """"live" or "demo". Without LANDFALL_REQUIRE_GRANT=on (local development) everything stays live."""
    return "live" if not required() or kind_of(session) else "demo"


def live_project(pid: str) -> bool:
    """True if the project's session holds a non-revoked grant, or grants aren't required (shared-delivery code calls this)."""
    rows = db.q("SELECT session FROM projects WHERE id=?", pid)
    return not required() or bool(rows and kind_of(rows[0]["session"]))
