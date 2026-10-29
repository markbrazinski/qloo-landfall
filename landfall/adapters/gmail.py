"""Gmail: one founder-approved message sent from the connected account (`users.messages.send`, raw RFC 2822).

Scopes `openid email gmail.send` only: no inbox permission is added for sending, so there is no read-back. The send has
no idempotency key: anything but a 200 with ids, a 400 or a 401 is UNCERTAIN and never resent (§6.2a).

Slice 6.7 (G1): unless `LANDFALL_EMAIL_MODE=direct`, every live send is routed by the server from the configured test
sender to the private test inbox. The package keeps its intended recipient; only the MIME handed to Gmail changes."""
import base64
import email
import email.policy
import hashlib
import re
from email.utils import getaddresses

from .. import config
from . import Adapter, V, google_auth_url, google_exchange, google_fresh, need

SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
ADDR = re.compile(r"^[^@\s<>,;\"]+@[^@\s<>,;\"]+\.[^@\s<>,;\"]+$")
SENDER_LABEL, INBOX_LABEL = "Landfall test sender", "Private test inbox"
NOT_SET_UP = "Test delivery isn't set up in this deployment (private test addresses missing)"
WRONG_SENDER = "The connected Gmail isn't this deployment's Landfall test sender. Connect that account."


def email_mode() -> str:
    """G1: TEST_REDIRECT unless the deployment says `direct` (refused at start in judging, see `startup_check`)."""
    return "DIRECT" if (config.env("LANDFALL_EMAIL_MODE") or "").strip().lower() == "direct" else "TEST_REDIRECT"


def email_routing() -> dict:
    """The only source of the actual sender and destination: `{mode, sender, to}` or `{mode, blocker}`. Read from live
    config every time; nothing is cached."""
    if email_mode() == "DIRECT":
        return {"mode": "DIRECT"}
    s, t = (config.env("gmail_test_sender") or "").strip(), (config.env("gmail_test_recipient") or "").strip()
    if not (ADDR.match(s) and ADDR.match(t)):
        return {"mode": "TEST_REDIRECT", "blocker": NOT_SET_UP}
    return {"mode": "TEST_REDIRECT", "sender": s, "to": t}


def digest(addr: str) -> str:
    return hashlib.sha256(addr.lower().encode()).hexdigest()[:16]


def partner_name(pkg: dict) -> str:
    return (pkg.get("partner") or {}).get("name") or "the intended recipient"


def routed(pkg: dict, to: str) -> dict:
    """What Gmail receives in a test send: the test inbox only (external Cc/Bcc dropped by construction), a marked
    subject and one first line; the prepared body follows unchanged. The package itself is never changed."""
    who = partner_name(pkg)
    c = pkg["copy"]
    subj = c.get("subject") or {}
    return {**pkg, "recipients": {"to": [to], "cc": [], "bcc": []},
            "copy": {**c, "subject": {**subj, "text": f"[Landfall test] {subj.get('text') or ''}"},
                     "segments": [{"text": f"Test delivery from Landfall. Prepared for {who}; {who} has not been contacted."},
                                  *(c.get("segments") or [])]}}


def is_test(action: dict | None) -> bool:
    """Classification reads the action's own stored routing, never the current config (P67-R1)."""
    return ((action or {}).get("routing") or {}).get("mode") == "TEST_REDIRECT"


class Gmail(Adapter):
    DEST, PROVIDER, LABEL, KIND = "EMAIL", "gmail", "Gmail", "GMAIL_SEND"
    CONFIGURATION = "One message you approve (To, Cc, Bcc, subject, body, attachments), sent from your connected Gmail"
    WHERE, VERB, VERB_NOW, EXECUTING, SUCCESS = "your Gmail Sent folder", "sent", "Send", "Sending…", "SENT"
    MANUAL, ACCOUNT_NOUN, PAUSES, NEEDS_ACCOUNT = "MARKED_SENT_BY_YOU", "Gmail account", False, False
    CONFIG = ("google_oauth_client_id", "google_oauth_client_secret")
    SCOPES = "openid email https://www.googleapis.com/auth/gmail.send"
    SIMULATED_IDENTITY = {"email": "demo-sender@example.com"}

    def access(self) -> dict:
        s = (config.env("LANDFALL_GMAIL_ACCESS") or "TESTING").upper()
        if s == "VERIFIED":
            return {"state": "APPROVED", "connectable": True, "note": "Google verification granted"}
        return {"state": "PENDING" if s == "PENDING" else "REQUIRED", "connectable": True,
                "note": "Testing mode: only listed test users can connect; anyone else needs Google's sensitive-scope verification"}

    def auth_url(self, state: str, redirect: str) -> str:
        return google_auth_url(state, redirect, self.SCOPES)

    def connect(self, params: dict, redirect: str) -> dict:
        from ..delivery import read
        got = google_exchange(params.get("code", ""), redirect, read)
        if "https://www.googleapis.com/auth/gmail.send" not in got["scopes"]:
            raise ValueError("Google didn't grant permission to send")
        return {**got, "accounts": []}

    def identity(self, user: dict, sel: dict | None) -> dict:
        return {"email": user["email"]}

    def label_of(self, user: dict, sel: dict | None) -> str:
        """G2: in test delivery the address never leaves the server; the label says whether it is the test sender."""
        rt = email_routing()
        if rt["mode"] == "DIRECT":
            return user["email"]
        return SENDER_LABEL if (rt.get("sender") or "").lower() == user["email"].lower() else "A Gmail account that isn't the test sender"

    def fresh_token(self, conn, token, read):
        return google_fresh(conn, token, read)

    def auth_headers(self, token: dict, conn: dict) -> dict:
        return {"Authorization": f"Bearer {token['access_token']}"}

    def required(self, pkg: dict) -> list[dict]:
        out: list = []
        r = pkg["recipients"]
        if email_mode() == "DIRECT":  # a test send doesn't need (or establish) the partner's address: see `contact_missing`
            need(out, r["to"], "to", "Add the recipient's email address")
        need(out, pkg.get("from_name"), "from_name", "Add your name as the sender")
        need(out, (pkg["copy"].get("subject") or {}).get("text"), "subject", "Add a subject")
        need(out, pkg["copy"].get("segments"), "segments", "Write the message")
        dup = [a for a in r["to"] + r["cc"] + r["bcc"] if (r["to"] + r["cc"] + r["bcc"]).count(a) > 1]
        need(out, not dup, "recipients", f"A recipient appears twice: {', '.join(sorted(set(dup)))}")
        return out

    def plan(self, pkg: dict, conn: dict | None, sim: bool = False) -> dict:
        from ..delivery import manifest, mime
        sender = (conn or {}).get("account_identity", {}).get("email") if conn else None
        blockers = [] if sender else [{"field": "sender", "text": "Connect the Gmail account to send from"}]
        rt = None if sim or (conn or {}).get("simulated") else email_routing()  # a SIMULATED send reaches no one
        if rt and rt["mode"] == "TEST_REDIRECT":
            return self._test_plan(pkg, sender, rt, blockers)
        r = pkg["recipients"]
        values = [V("From", sender, "ACCOUNT", "the signed-in Gmail address (openid email)"),
                  V("Sender name", pkg.get("from_name"), "FOUNDER"),
                  V("To", r["to"], "FOUNDER" if (pkg.get("recipients_by") or {}).get("to") == "YOU_SAID" else "DERIVED",
                    "the partner's own-site contact" if (pkg.get("recipients_by") or {}).get("to") == "SOURCE" else ""),
                  V("Cc", r["cc"], "FOUNDER"), V("Bcc", r["bcc"], "FOUNDER", "shown here and bound to your approval"),
                  V("Subject", (pkg["copy"].get("subject") or {}).get("text"), "FOUNDER", "the reviewed copy"),
                  V("Attachments", [f"{m['filename']} · {m['sha256'][:12]}" for m in manifest(pkg) if m["role"] == "ATTACHMENT"],
                    "DERIVED", "the saved files, by digest"),
                  V("Method", "users.messages.send (raw RFC 2822)", "FIXED")]
        requests = []
        try:
            raw = base64.urlsafe_b64encode(mime(pkg, sender)).decode()
            requests = [{"step": "message", "method": "POST", "url": SEND_URL, "json": {"raw": raw}}]
        except Exception as e:  # a missing artifact is reported by the shared check
            blockers.append({"field": "message", "text": str(e)})
        out = {"requests": requests, "values": values, "blockers": blockers,
               "recipients": sorted(a.lower() for a in r["to"] + r["cc"] + r["bcc"])}
        if rt:
            out["routing"] = {"mode": "DIRECT"}
        return out

    def _test_plan(self, pkg: dict, sender: str | None, rt: dict, blockers: list) -> dict:
        """G1/G2: the actual sender and inbox come from config only and are shown by label; the intended outreach is
        shown as "Prepared for". Any blocker stops here: nothing ever falls back to the intended address."""
        from ..delivery import manifest, mime
        r, by = pkg["recipients"], (pkg.get("recipients_by") or {}).get("to")
        if rt.get("blocker"):
            blockers.append({"field": "routing", "text": rt["blocker"]})
        elif sender and sender.lower() != rt["sender"].lower():
            blockers.append({"field": "routing", "text": WRONG_SENDER})
        send = routed(pkg, rt.get("to") or "")
        intended = ", ".join([*r["to"], *(f"Cc {a}" for a in r["cc"]), *(f"Bcc {a}" for a in r["bcc"])])
        values = [V("From", SENDER_LABEL if sender else None, "ACCOUNT", "the connected Gmail, which must be this deployment's test sender"),
                  V("Sender name", pkg.get("from_name"), "FOUNDER"),
                  V("To", INBOX_LABEL, "FIXED", "server configuration; never the partner"),
                  V("Prepared for", f"{partner_name(pkg)}" + (f" · {intended}" if intended else
                                                              " · Contact address: not found. A test send doesn't establish one."),
                    "FOUNDER" if by == "YOU_SAID" else "DERIVED", "the intended outreach, unchanged and not contacted"),
                  V("Delivery mode", "Test delivery: private test inbox only", "FIXED"),
                  V("Subject", send["copy"]["subject"]["text"], "FOUNDER", "the reviewed copy, marked as a test"),
                  V("Attachments", [f"{m['filename']} · {m['sha256'][:12]}" for m in manifest(pkg) if m["role"] == "ATTACHMENT"],
                    "DERIVED", "the saved files, by digest"),
                  V("Method", "users.messages.send (raw RFC 2822)", "FIXED")]
        requests = []
        if not any(b["field"] == "routing" for b in blockers):  # no request exists that could reach a wrong destination
            try:
                raw = base64.urlsafe_b64encode(mime(send, sender)).decode()
                requests = [{"step": "message", "method": "POST", "url": SEND_URL, "json": {"raw": raw}}]
            except Exception as e:
                blockers.append({"field": "message", "text": str(e)})
        routing = {"mode": "TEST_REDIRECT", "destination": rt.get("to") and digest(rt["to"]), "sender": rt.get("sender") and digest(rt["sender"])}
        return {"requests": requests, "values": values, "blockers": blockers, "routing": routing,
                "recipients": [rt["to"].lower()] if rt.get("to") else []}

    def precheck(self, plan: dict) -> str | None:
        """P45-R1, amended: recipients projected deterministically from the final MIME (To, Cc, Bcc) must equal the
        approved set; a duplicate header, a group or an unparseable address refuses."""
        msg = email.message_from_bytes(base64.urlsafe_b64decode(plan["requests"][0]["json"]["raw"]), policy=email.policy.compat32)
        found = []
        for hdr in ("To", "Cc", "Bcc"):
            vals = msg.get_all(hdr) or []
            if len(vals) > 1:
                return f"The message has more than one {hdr} header"
            for v in vals:
                if ":" in v and ";" in v:
                    return f"{hdr} holds a group address"
                pairs = getaddresses([v])
                if any("@" not in a or not a for _, a in pairs):
                    return f"{hdr} holds an address that can't be read"
                found += [a.lower() for _, a in pairs]
        if sorted(found) != plan["recipients"]:
            return "The recipients in the message differ from the ones you approved"
        if (plan.get("routing") or {}).get("mode") == "TEST_REDIRECT":  # G1: re-read from config at execute
            to = email_routing().get("to")
            if not to or found != [to.lower()]:
                return "The message isn't addressed only to the private test inbox"
        return None

    def allow_targets(self, ident: dict, plan: dict) -> list[str]:
        return [ident["email"], *plan["recipients"]]

    def outcome(self, step: str, status: int, body) -> tuple[str, object]:
        if status == 200 and isinstance(body, dict) and body.get("id") and body.get("threadId"):
            return "ok", {"id": body["id"], "extra": {"thread_id": body["threadId"]}}
        if status == 400:
            msg = (body.get("error") or {}).get("message") if isinstance(body, dict) else None
            return "rejected", f"Gmail rejected the message: {msg or 'invalid request'}"
        if status == 401:
            return "expired", ""
        return "unknown", f"HTTP {status}" if status else "no response"

    def result(self, action: dict, conn: dict) -> dict:
        return {"message_id": action["ids"].get("message"), "thread_id": (action.get("responses") or {}).get("message", {}).get("thread_id"),
                "sent_at": action["at"]}

    def success_text(self, action: dict) -> str:
        if is_test(action):
            who = action.get("partner_name") or "The intended recipient"
            if (action.get("resolution") or {}).get("kind") == "TEST_RECEIPT_CONFIRMED":
                return (f"Test email sent · you confirmed it in the private test inbox; Gmail's own answer was unclear. "
                        f"{who} has not been contacted.")
            return f"Test email sent — Submitted to Gmail for the private test inbox (message {action['ids'].get('message')}). {who} has not been contacted."
        return f"Sent from {action['identity']['email']} · message {action['ids'].get('message')}."

    def prepared(self, pkg: dict) -> list[str]:
        n = sum(a["role"] == "ATTACHMENT" for a in pkg["assets"])
        who = (pkg.get("partner") or {}).get("name") or "your partner"
        return [f"An inquiry to {who} ({pkg['copy']['locale']})", f"{n} attachment{'s' * (n != 1)} from your own site"]

    def usable_today(self, pkg: dict) -> list[str]:
        return ["Preview the message", "Copy the subject and body", "Download the .eml and the outreach package",
                "Open in your mail app (add attachments by hand)"]

    def setup_steps(self, pkg: dict) -> list[str]:
        return ["Open the .eml in your mail app, or use Open in your mail app.",
                "Attach the files from the outreach package if your mail app didn't keep them.",
                "Check the recipients, then send it yourself.", "Come back and record it: Marked sent by you."]
