"""Step 0 of BUILD-PLAN §4.5.12: the early access checks for slice 6, run by the owner on their own machine.

    uv run python -m scripts.early_access gmail [--go]   # one gmail.send to the designated test recipient (done 26 Oct)
    uv run python -m scripts.early_access meta  [--go]   # a read on the demo ad account + one PAUSED campaign, verified

Without --go nothing is sent: the exact payload and destination are printed for the owner to approve. With --go the
one action runs once and its provider ids are recorded in live-captures-private/probes/slice45-early/ (never a token).
There is no retry after an ambiguous outcome: it is reported UNCERTAIN, as the shared execution tool will (§4.5.9a).

.env names (values never printed):
  gmail: google_oauth_client_id, google_oauth_client_secret, gmail_test_recipient   (scripts/cloudshell/gcp_setup.sh)
  meta:  meta_access_token (ads_management), meta_ad_account_id, meta_graph_version (default v26.0)
Meta setup (no CLI exists): developers.facebook.com → Create app (Business) → add Marketing API; the owner keeps an
app role; in Graph API Explorer pick the app, add ads_management, generate a user token; the ad account id is in Ads
Manager's URL (act=…). The demo ad account needs no App Review when used by an app role holder.
"""
import base64
import hashlib
import json
import secrets
import sys
import webbrowser
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import getaddresses
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from landfall import config

OUT = config.PRIVATE / "probes" / "slice45-early"
REF = "LF-EARLY-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M")


def need(*names: str) -> list[str]:
    missing = [n for n in names if not config.env(n)]
    if missing:
        sys.exit(f"Missing in .env: {', '.join(missing)}")
    return [config.env(n) for n in names]


def save(name: str, record: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}-{REF}.json").write_text(json.dumps(record, indent=1))
    print(f"Recorded: {OUT / f'{name}-{REF}.json'}")


# ---- Gmail ----
def recipients(msg: EmailMessage) -> list[str]:
    """P45-R1's projection: every To/Cc/Bcc address of the final MIME. Bcc, Cc, a repeated header or a group → refused."""
    if msg.get_all("bcc") or msg.get_all("cc") or len(msg.get_all("to") or []) != 1:
        raise SystemExit("REFUSED: exactly one To header, no Cc or Bcc")
    addrs = getaddresses(msg.get_all("to"))
    if any(not a or "@" not in a for _, a in addrs):
        raise SystemExit("REFUSED: an unparseable or group address")
    return sorted(a.lower() for _, a in addrs)


def _consent(client_id: str, client_secret: str) -> dict:
    """Loopback OAuth with PKCE (Desktop client). Scopes: gmail.send, plus openid email for the sender's identity."""
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(16)
    got: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            got.update({k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Landfall: consent received. You can close this tab.")

        def log_message(self, *a):
            pass

    # A fixed port, so a Web-application client works too once http://127.0.0.1:8765/ is an authorized redirect URI
    # (a Desktop client accepts any loopback port).
    server = HTTPServer(("127.0.0.1", 8765), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}/"
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    link = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "state": state,
        "scope": "openid email https://www.googleapis.com/auth/gmail.send", "code_challenge": challenge,
        "code_challenge_method": "S256", "access_type": "online", "prompt": "consent"})
    webbrowser.open(link)
    print(f"Waiting for consent in the browser. If no tab opened, open this link:\n{link}", flush=True)
    while "code" not in got and "error" not in got:
        server.handle_request()
    if got.get("state") != state or "code" not in got:
        sys.exit(f"Consent failed: {got.get('error', 'state mismatch')}")
    r = httpx.post("https://oauth2.googleapis.com/token", data={
        "code": got["code"], "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect,
        "grant_type": "authorization_code", "code_verifier": verifier}, timeout=20)
    r.raise_for_status()
    tok = r.json()
    # The id_token comes straight from Google's token endpoint over TLS, so its claims are read without re-verifying.
    claims = json.loads(base64.urlsafe_b64decode(tok["id_token"].split(".")[1] + "=="))
    return {"access_token": tok["access_token"], "sender": claims["email"].lower(), "scopes": tok.get("scope", "")}


def gmail(go: bool) -> None:
    client_id, client_secret, to = need("google_oauth_client_id", "google_oauth_client_secret", "gmail_test_recipient")
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = f"Landfall early access check ({REF})"
    msg.set_content("This is Landfall's slice-4.5 early access check: one approved gmail.send to the designated test "
                    "recipient. No follow-up will be sent.\n")
    print(f"Destination: {recipients(msg)} (sender: the Gmail account you consent with)\n---\n{msg}---")
    if not go:
        print("Dry run: nothing sent. Re-run with --go to send exactly this once.")
        return
    session = _consent(client_id, client_secret)
    msg["From"] = session["sender"]
    if recipients(msg) != [to.lower()]:  # checked again on the final MIME, immediately before dispatch
        sys.exit("REFUSED: the final MIME's recipients differ from the approved destination")
    raw = msg.as_bytes()
    record = {"ref": REF, "at": datetime.now(timezone.utc).isoformat(), "sender_identity": session["sender"],
              "recipients": recipients(msg), "payload_sha256": hashlib.sha256(raw).hexdigest(),
              "scopes": session["scopes"]}
    try:
        r = httpx.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                       headers={"Authorization": f"Bearer {session['access_token']}"},
                       json={"raw": base64.urlsafe_b64encode(raw).decode()}, timeout=30)
    except httpx.TransportError as e:
        save("gmail", {**record, "status": "UNCERTAIN", "error": type(e).__name__})
        sys.exit("UNCERTAIN: no answer from Gmail. Check Sent before trying again; nothing is retried.")
    body = r.json()
    if r.status_code != 200:
        save("gmail", {**record, "status": "FAILED", "http": r.status_code, "error": body.get("error")})
        sys.exit(f"FAILED: HTTP {r.status_code} {body.get('error', {}).get('message')}")
    save("gmail", {**record, "status": "SENT", "message_id": body["id"], "thread_id": body["threadId"]})
    print(f"SENT · message {body['id']} · thread {body['threadId']}")


# ---- Meta ----
def meta(go: bool) -> None:
    token, account = need("meta_access_token", "meta_ad_account_id")
    act = account if account.startswith("act_") else f"act_{account}"
    graph = f"https://graph.facebook.com/{config.env('meta_graph_version', 'v26.0')}"
    auth = {"Authorization": f"Bearer {token}"}
    campaign = {"name": f"{REF} Landfall paused check (safe to delete)", "objective": "OUTCOME_TRAFFIC",
                "status": "PAUSED", "special_ad_categories": "[]"}
    print(f"Read: GET /me, GET /{act}\nThen create once: POST /{act}/campaigns {campaign}\nNo ad set, ad, budget or "
          "activation. Verified ⇔ configured status PAUSED and effective_status not ACTIVE.")
    if not go:
        print("Dry run: nothing created. Re-run with --go.")
        return
    with httpx.Client(headers=auth, timeout=30) as c:
        me = c.get(f"{graph}/me", params={"fields": "id,name"}).json()
        acct = c.get(f"{graph}/{act}", params={"fields": "name,account_status,currency,timezone_name"}).json()
        record = {"ref": REF, "at": datetime.now(timezone.utc).isoformat(), "user_identity": me.get("id"),
                  "ad_account_id": act, "account": acct, "payload": campaign}
        if "error" in me or "error" in acct:
            save("meta", {**record, "status": "FAILED", "error": me.get("error") or acct.get("error")})
            sys.exit(f"FAILED on the read: {(me.get('error') or acct.get('error')).get('message')}")
        print(f"Account: {acct.get('name')} · status {acct.get('account_status')} · {acct.get('currency')}")
        try:
            r = c.post(f"{graph}/{act}/campaigns", data=campaign)
        except httpx.TransportError as e:
            save("meta", {**record, "status": "UNCERTAIN", "error": type(e).__name__})
            sys.exit(f"UNCERTAIN: no answer. Search Ads Manager for '{REF}'; nothing is retried.")
        created = r.json()
        if "id" not in created:
            save("meta", {**record, "status": "FAILED", "http": r.status_code, "error": created.get("error")})
            sys.exit(f"FAILED: {created.get('error', {}).get('message')}")
        cid = created["id"]
        v = c.get(f"{graph}/{cid}", params={"fields": "status,configured_status,effective_status"}).json()
        ok = v.get("status") == "PAUSED" and v.get("effective_status") != "ACTIVE"
        if not ok:
            c.post(f"{graph}/{cid}", data={"status": "PAUSED"})
        link = f"https://adsmanager.facebook.com/adsmanager/manage/campaigns?act={act[4:]}&selected_campaign_ids={cid}"
        save("meta", {**record, "status": "CREATED (PAUSED)" if ok else "FAILED_VERIFICATION", "campaign_id": cid,
                      "verification": v, "link": link})
        print(f"{'CREATED (PAUSED)' if ok else 'FAILED_VERIFICATION (paused again)'} · campaign {cid} · {v}\n{link}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("gmail", "meta"):
        sys.exit(__doc__)
    {"gmail": gmail, "meta": meta}[sys.argv[1]]("--go" in sys.argv[2:])
