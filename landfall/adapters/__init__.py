"""The four delivery adapters (BUILD-PLAN §6.2). Each is an ordinary module: capability, connect, `required(package)`,
`plan(package)` (the exact provider requests), `outcome()` of one write, and read-back. The shared execution path in
`landfall.delivery` owns dispatch, retries (none for writes), uncertainty and approvals.

Nothing here is live-verified unless the slice report's status matrix says so; request shapes come from the providers'
official documentation read on 26 Oct 2026 and are exercised offline against SYNTHETIC fakes only."""
import time
from urllib.parse import urlencode

from .. import config


class Expired(Exception):
    """The provider refused the token or the refresh: the connection needs Reconnect."""


class Unavailable(Exception):
    """The provider couldn't be reached (no answer, 5xx, 429): the connection is kept as it is and nothing was sent."""


def V(field: str, value, label: str, source: str = "") -> dict:
    """One ProviderPlan value: FIXED (documented release setting), FOUNDER, ACCOUNT or DERIVED (formula in source)."""
    return {"field": field, "value": value, "label": label, "source": source}


def daily(pkg: dict, dst: str) -> dict | None:
    """This platform's own share of the package's one daily budget (slice 5, D5): never the whole total."""
    from ..delivery import daily_for
    return daily_for(pkg, dst)


def reference(pkg: dict) -> str:
    """The Landfall reference put in every created object's name: findable later, the same for one package version."""
    import hashlib
    return f"Landfall {hashlib.sha256(pkg['id'].encode()).hexdigest()[:8]} v{pkg['version']}"


def account_blockers(sel: dict, label: str) -> list[dict]:
    """A connection whose account isn't fully set up never yields an executable plan (P6-I2)."""
    if not sel.get("id"):
        return [{"field": "account", "text": f"Connect {label} and choose the account"}]
    return [] if sel.get("currency") else [{"field": "account", "text": "The account's billing currency isn't known: choose the account again"}]


def too_long(out: list, segs: list, limit: int, field: str, label: str) -> None:
    """Structural limits apply to every saved value, whoever wrote it (P6-I5)."""
    for i, s in enumerate(segs or [], 1):
        if s and len(s["text"]) > limit:
            out.append({"field": field, "text": f"{label} {i} is {len(s['text'])} characters; the limit is {limit}"})


def timing(out: list, pkg: dict) -> None:
    from datetime import date
    t = pkg["timing"]
    for k in ("start", "end"):
        if t.get(k):
            try:
                date.fromisoformat(t[k])
            except ValueError:
                out.append({"field": k, "text": f"The {k} date {t[k]} isn't a real date"})
    if t.get("start") and t.get("end") and t["end"] < t["start"]:
        out.append({"field": "end", "text": "The end date is before the start date"})


def need(out: list, ok, field: str, text: str) -> None:
    if not ok:
        out.append({"field": field, "text": text})


class Adapter:
    DEST = PROVIDER = LABEL = KIND = CONFIGURATION = WHERE = ""
    VERB, VERB_NOW, EXECUTING, SUCCESS = "created", "Create, paused", "Creating, paused…", "CREATED_PAUSED"
    MANUAL, ACCOUNT_NOUN = "MARKED_CREATED_BY_YOU", "ad account"
    VALIDATES, PAUSES, FINDS_BY_REFERENCE, NEEDS_ACCOUNT = False, True, False, True
    CONFIG: tuple = ()
    SIMULATED_IDENTITY: dict = {}
    Expired, Unavailable = Expired, Unavailable

    def missing_config(self) -> list[str]:
        return [n for n in self.CONFIG if not config.env(n)]

    def access(self) -> dict:
        raise NotImplementedError

    def identity(self, user: dict, sel: dict | None) -> dict:
        raise NotImplementedError

    def label_of(self, user: dict, sel: dict | None) -> str:
        return (sel or {}).get("name") or user.get("name") or user.get("email") or "connected"

    def select(self, acc: dict, extra: dict, conn: dict, token: dict | None) -> dict:
        return acc

    def fresh_token(self, conn: dict, token: dict | None, read) -> dict | None:
        if token and token.get("expires_at") and token["expires_at"] < time.time():
            raise Expired()
        return token

    def dispatch_ids(self, action: dict) -> dict:
        return {}

    def precheck(self, plan: dict) -> str | None:
        return None

    def find(self, action: dict, conn: dict, headers: dict, read) -> list:
        return []

    def validate_remote(self, plan: dict, conn: dict, headers: dict, read) -> dict:
        return {"result": "NOT_SUPPORTED"}

    def repause(self, action: dict, conn: dict, verification: dict) -> list[dict]:
        return []

    def read_back(self, action: dict, conn: dict, headers: dict, read) -> dict:
        return {"supported": False, "verified": True}

    def success_text(self, action: dict) -> str:
        ids = ", ".join(f"{k} {v}" for k, v in action["ids"].items() if not k.startswith("rid_"))
        env = (action.get("identity") or {}).get("environment")  # 6.7 A1: the label says where it was created
        if env == "TEST_ACCOUNT":
            return f"Campaign created in test account (never serves): {ids}."
        if env == "SANDBOX":
            return f"Campaign created in sandbox (never delivers): {ids}."
        return f"Paused campaign created in {self.WHERE}: {ids}."

    def usable_today(self, pkg: dict) -> list[str]:
        return ["Review the creative, copy, audience rationale, budget and settings", "Download the campaign package",
                "Download the images", "Copy the configuration", f"Follow the {self.LABEL} setup steps"]


# ---- Google OAuth (Gmail and Google Ads share Landfall's web client; scopes differ per connection) ----
GOOGLE_AUTH, GOOGLE_TOKEN = "https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"


def google_auth_url(state: str, redirect: str, scopes: str) -> str:
    return GOOGLE_AUTH + "?" + urlencode({"client_id": config.env("google_oauth_client_id"), "redirect_uri": redirect,
                                          "response_type": "code", "scope": scopes, "state": state, "access_type": "offline",
                                          "prompt": "consent", "include_granted_scopes": "false"})


def google_exchange(code: str, redirect: str, read) -> dict:
    st, body = read({"method": "POST", "url": GOOGLE_TOKEN, "form": {
        "code": code, "client_id": config.env("google_oauth_client_id"), "client_secret": config.env("google_oauth_client_secret"),
        "redirect_uri": redirect, "grant_type": "authorization_code"}}, {}, tries=1)
    if st != 200 or not isinstance(body, dict) or "access_token" not in body:
        raise ValueError(f"Google didn't issue a token (HTTP {st})")
    tok = {"access_token": body["access_token"], "refresh_token": body.get("refresh_token"),
           "expires_at": time.time() + int(body.get("expires_in", 3600)), "scope": body.get("scope", "")}
    st, me = read({"method": "GET", "url": GOOGLE_USERINFO}, {"Authorization": f"Bearer {tok['access_token']}"})
    if st != 200 or not isinstance(me, dict) or not me.get("email"):
        raise ValueError("Google didn't say which account signed in")
    return {"token": tok, "user": {"email": me["email"], "sub": me.get("sub")}, "scopes": tok["scope"].split()}


def google_fresh(conn: dict, token: dict | None, read) -> dict | None:
    """Refresh a Google access token a minute before it lapses; invalid_grant → Expired (Reconnect)."""
    if not token or token["expires_at"] > time.time() + 60:
        return token
    if not token.get("refresh_token"):
        raise Expired()
    st, body = read({"method": "POST", "url": GOOGLE_TOKEN, "form": {
        "client_id": config.env("google_oauth_client_id"), "client_secret": config.env("google_oauth_client_secret"),
        "refresh_token": token["refresh_token"], "grant_type": "refresh_token"}}, {})
    if st in (400, 401):  # invalid_grant (a revoked or 7-day Testing-mode refresh token) or a refused client
        raise Expired()
    if st != 200 or not isinstance(body, dict) or "access_token" not in body:  # Google unreachable: never marked EXPIRED
        raise Unavailable()
    from .. import delivery
    new = {**token, "access_token": body["access_token"], "expires_at": time.time() + int(body.get("expires_in", 3600)),
           "refresh_token": body.get("refresh_token") or token["refresh_token"]}  # kept unless Google rotates it
    delivery.put_token(conn["id"], new)
    return new


from .gmail import Gmail  # noqa: E402
from .google_ads import GoogleAds  # noqa: E402
from .meta import Meta  # noqa: E402
from .tiktok import TikTok  # noqa: E402

ADAPTERS = {a.DEST: a for a in (Gmail(), Meta(), GoogleAds(), TikTok())}
