"""Owner-connected shared delivery (BUILD-PLAN §6.7.10 G-J; owner instruction 27 Oct), offline: fake providers and
fictional ids and addresses only. The owner connects once; judges never connect, and their sends use the shared
connection inside the existing 6.7 test routing, the allowance, and every existing guard."""
import json
import re
import threading
import time

import pytest
from test_markets import idle, markets_env  # noqa: F401
from test_slice4 import env, research_ready  # noqa: F401
from test_slice6 import ads_ready, approve_and_run, deliver_env, dest, outreach_ready, provider, view  # noqa: F401
from test_slice45_runner import spec_env  # noqa: F401
from test_slice67 import INBOX, SENDER, sent_mime, test_mode

from landfall import config, db, delivery
from landfall.adapters import google_ads as gads

CLIENT, MANAGER, CLIENT_DASHED = "5550001111", "5550002222", "555-000-1111"
S = delivery.SHARED


def shared_on(monkeypatch):
    monkeypatch.setitem(config._ENV, "LANDFALL_SHARED_DELIVERY", "on")


def ga_test(monkeypatch):
    for k, v in {"LANDFALL_GOOGLE_ADS_ACCESS": "TEST", "google_ads_test_customer_id": CLIENT_DASHED,
                 "google_ads_test_manager_id": MANAGER}.items():
        monkeypatch.setitem(config._ENV, k, v)


def connect_shared(dst, expires_in=3600):
    ad, rid = delivery.adapters()[dst], delivery.conn_id(S, dst)
    user = {"email": SENDER}
    sel = {"id": CLIENT, "name": "Owner's private ads name", "currency": "CAD", "test_account": True,
           "login_customer_id": MANAGER} if dst == "GOOGLE_ADS" else None
    delivery.put_token(rid, {"access_token": "tok", "refresh_token": "ref", "expires_at": time.time() + expires_in})
    db.put_record("connection", S, {"provider": ad.PROVIDER, "destination": dst, "state": "ACTIVE", "simulated": False,
                                    "user": user, "accounts": [sel] if sel else [], "selected": sel,
                                    "account_identity": ad.identity(user, sel), "account_label": ad.label_of(user, sel),
                                    "scopes": [], "connected_at": db.now(), "last_ok_at": db.now()}, rid=rid)
    assert rid == f"con__shared_{dst.lower()}"


def drop_own(pid, dst):
    assert delivery.disconnect(pid, dst)  # the founder's own connection, before shared delivery is on


def email_judge(client, env, monkeypatch):
    pid, v = outreach_ready(client, env, monkeypatch)
    test_mode(monkeypatch)
    drop_own(pid, "EMAIL")
    shared_on(monkeypatch)
    connect_shared("EMAIL")
    return pid, view(client, pid, v["id"])


def ads_judge(client, env, monkeypatch):
    pid, v = ads_ready(client, env, monkeypatch)
    ga_test(monkeypatch)
    monkeypatch.setitem(config._ENV, "LANDFALL_DELIVERY_ALLOWLIST", CLIENT)
    drop_own(pid, "GOOGLE_ADS")
    shared_on(monkeypatch)
    connect_shared("GOOGLE_ADS")
    return pid, view(client, pid, v["id"])


def test_resolver_precedence_and_test_environment_gates(client, deliver_env, monkeypatch, provider):
    pid, v = outreach_ready(client, deliver_env, monkeypatch)  # the founder's own Gmail is connected
    test_mode(monkeypatch)
    shared_on(monkeypatch)
    connect_shared("EMAIL")
    assert delivery.conn_for(pid, "EMAIL")["project_id"] == pid  # own > shared: customer support kept
    drop_own(pid, "EMAIL")
    assert delivery.conn_for(pid, "EMAIL")["id"] == "con__shared_email"  # then shared
    monkeypatch.setitem(config._ENV, "LANDFALL_EMAIL_MODE", "direct")  # DIRECT email never uses shared
    assert delivery.conn_for(pid, "EMAIL") is None
    monkeypatch.delitem(config._ENV, "LANDFALL_EMAIL_MODE")
    monkeypatch.delitem(config._ENV, "gmail_test_recipient")  # test addresses missing
    assert delivery.conn_for(pid, "EMAIL") is None
    test_mode(monkeypatch)
    monkeypatch.setitem(config._ENV, "LANDFALL_SHARED_DELIVERY", "off")
    assert delivery.conn_for(pid, "EMAIL") is None
    shared_on(monkeypatch)
    from landfall import grants
    monkeypatch.setattr(grants, "live_project", lambda p: False)  # no judge or owner grant: the recorded demo, never shared
    assert delivery.conn_for(pid, "EMAIL") is None
    monkeypatch.setattr(grants, "live_project", lambda p: True)
    ga_test(monkeypatch)
    connect_shared("GOOGLE_ADS")
    assert delivery.conn_for(pid, "GOOGLE_ADS")["id"] == "con__shared_google_ads"
    monkeypatch.setitem(config._ENV, "LANDFALL_GOOGLE_ADS_ACCESS", "EXPLORER")  # production ad access never uses shared
    assert delivery.conn_for(pid, "GOOGLE_ADS") is None
    ga_test(monkeypatch)
    for dst in ("META_ADS", "TIKTOK_ADS"):
        assert not delivery.shared_enabled(dst)


class Google:
    """SYNTHETIC Google: token exchange, userinfo and the GAQL customer read."""

    def __init__(self, email=SENDER):
        self.email, self.calls = email, []

    def __call__(self, req, headers):
        self.calls.append((req["url"], headers))
        u = req["url"]
        if u.endswith("/token"):
            return 200, {"access_token": "tok", "refresh_token": "ref", "expires_in": 3600,
                         "scope": "openid email https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/adwords"}
        if "userinfo" in u:
            return 200, {"email": self.email}
        if u.endswith("googleAds:search"):
            return 200, {"results": [{"customer": {"id": CLIENT, "testAccount": True, "manager": False, "currencyCode": "CAD",
                                                   "descriptiveName": "t"}}]}
        return 404, {}


def owner_callback(client, dst):
    url = delivery.owner_connect_start(dst)
    state = re.search(r"state=([^&]+)", url).group(1)
    assert db.q("SELECT project_id FROM oauth_states WHERE state=?", state)[0]["project_id"] == S  # flagged shared
    return client.get(f"/api/connections/callback?state={state}&code=c", follow_redirects=False)


def test_owner_flow_saves_the_shared_record_and_locks_the_ads_account(client, deliver_env, monkeypatch):
    outreach_ready(client, deliver_env, monkeypatch)  # a configured deployment
    test_mode(monkeypatch)
    ga_test(monkeypatch)
    shared_on(monkeypatch)
    g = Google()
    monkeypatch.setattr(delivery, "SEND", g)
    assert owner_callback(client, "EMAIL").status_code == 303
    c = db.get_record("con__shared_email")
    assert c["state"] == "ACTIVE" and c["project_id"] == S and c["account_identity"] == {"email": SENDER}
    assert c["account_label"] == "Landfall test sender" and delivery.get_token(c["id"])["refresh_token"] == "ref"
    assert owner_callback(client, "GOOGLE_ADS").status_code == 303
    c = db.get_record("con__shared_google_ads")
    assert c["selected"]["id"] == CLIENT and c["selected"]["login_customer_id"] == MANAGER  # chosen automatically
    assert c["account_identity"]["environment"] == "TEST_ACCOUNT" and c["account_label"] == gads.TEST_LABEL
    # a Gmail that isn't the configured test sender is never saved as the shared connection
    delivery.disconnect(S, "EMAIL")
    g.email = "someone-else@landfall-test.invalid"
    owner_callback(client, "EMAIL")
    assert db.get_record("con__shared_email")["state"] == "DISCONNECTED"
    monkeypatch.setitem(config._ENV, "LANDFALL_EMAIL_MODE", "direct")
    with pytest.raises(delivery.Refused):
        delivery.owner_connect_start("EMAIL")


def test_judges_cannot_connect_choose_disconnect_or_validate_the_shared_connection(client, deliver_env, monkeypatch, provider):
    pid, v = ads_judge(client, deliver_env, monkeypatch)
    base = f"/api/projects/{pid}"
    for r in (client.post(f"{base}/connections/GOOGLE_ADS/start", json={"package_id": v["id"]}),
              client.put(f"{base}/connections/GOOGLE_ADS/account", json={"account_id": CLIENT}),
              client.delete(f"{base}/connections/GOOGLE_ADS"),
              client.post(f"{base}/packages/{v['id']}/destinations/GOOGLE_ADS/validate")):
        assert r.status_code == 403 and "you don't connect" in r.json()["detail"], r.text
    assert provider.calls == [] and db.get_record("con__shared_google_ads")["state"] == "ACTIVE"
    d = dest(view(client, pid, v["id"]), "GOOGLE_ADS")
    assert d["shared"]["available"] and "never serves, never bills" in d["shared"]["disclosure"]
    assert d["state"]["accounts"] == [] and d["state"]["account"] == gads.TEST_LABEL and "connect" not in d["next"]


def test_a_judge_gmail_send_uses_the_shared_sender_and_reaches_only_the_test_inbox(client, deliver_env, monkeypatch, provider):
    pid, v = email_judge(client, deliver_env, monkeypatch)
    d = dest(v, "EMAIL")
    assert d["shared"] == {"available": True, "why": None, "disclosure": delivery.SHARED_DISCLOSURE["EMAIL"]}
    assert d["routing"]["recipient_label"] == "Private test inbox" and d["state"]["account"] == "Landfall test sender"
    a = approve_and_run(client, pid, v, "EMAIL").json()
    assert a["status"] == "SUCCEEDED" and db.get_record(a["id"])["connection_id"] == "con__shared_email"
    m = sent_mime(provider)
    assert m["To"] == INBOX and m["Cc"] is None and m["Bcc"] is None and SENDER in m["From"]
    assert dest(view(client, pid, v["id"]), "EMAIL")["summary"] == "TEST_SENT"


def test_a_judge_ads_create_uses_only_the_configured_test_client(client, deliver_env, monkeypatch, provider):
    pid, v = ads_judge(client, deliver_env, monkeypatch)
    heads = []
    real = delivery.SEND
    monkeypatch.setattr(delivery, "SEND", lambda req, h: (heads.append((req["url"], h)), real(req, h))[1])
    a = approve_and_run(client, pid, v, "GOOGLE_ADS").json()
    assert a["status"] == "SUCCEEDED", a
    assert heads and all(f"/customers/{CLIENT}/" in u and h["login-customer-id"] == MANAGER for u, h in heads)


def fake_actions(dst, pid, n):
    for _ in range(n):
        db.put_record("execution", pid, {"connection_id": delivery.conn_id(S, dst), "status": "SUCCEEDED", "mode": "LIVE",
                                         "steps": [{"step": "x", "status": "CONFIRMED"}], "package": {"id": "pkg_x"},
                                         "destination": dst, "at": db.now()}, rid=db.new_id("exe"))
    db.put_record("execution", pid, {"connection_id": delivery.conn_id(S, dst), "status": "FAILED", "mode": "LIVE",  # never sent
                                     "steps": [{"step": "x", "status": "PENDING"}], "package": {"id": "pkg_x"},
                                     "destination": dst, "at": db.now()}, rid=db.new_id("exe"))


@pytest.mark.parametrize("scope", ["per_project", "total"])
def test_the_allowance_refuses_before_any_provider_call(client, deliver_env, monkeypatch, provider, scope):
    pid, v = email_judge(client, deliver_env, monkeypatch)
    fake_actions("EMAIL", pid, 2) if scope == "per_project" else fake_actions("EMAIL", "prj_other", 20)
    d = dest(view(client, pid, v["id"]), "EMAIL")
    assert d["summary"] == "UNAVAILABLE_IN_THIS_DEPLOYMENT" and d["text"] == delivery.USED_UP and d["next"] == ["review"]
    r = approve_and_run(client, pid, v, "EMAIL")
    assert r.status_code == 409 and r.json()["detail"] == delivery.USED_UP and provider.calls == []
    assert not db.q("SELECT 1 FROM execution_slots WHERE package_id=?", v["id"])  # no slot claimed


def test_two_projects_racing_for_the_last_allowed_action_dispatch_exactly_once(client, deliver_env, monkeypatch, provider):
    from test_slice6 import configure, edit, packaged
    made = [packaged(client, deliver_env) for _ in range(2)]  # both before live configuration, as outreach_ready does
    configure(monkeypatch)
    test_mode(monkeypatch)
    shared_on(monkeypatch)
    connect_shared("EMAIL")
    (pid, v), (pid2, v2) = [(p, edit(client, p, x, {"from_name": "Maya at Tidewell", "segments": [
        s["text"] for s in x["copy"]["segments"] if "award" not in s["text"]]})) for p, x in made]
    monkeypatch.setitem(config._ENV, "LANDFALL_SHARED_EMAIL_TOTAL", "1")
    aps = [client.post(f"/api/projects/{p}/packages/{x['id']}/destinations/EMAIL/approve", json={"base_version": x["version"]}).json()
           for p, x in ((pid, v), (pid2, v2))]
    go, out = threading.Barrier(2), []

    def run(p, x, a):
        go.wait()
        try:
            out.append(delivery.execute(p, x["id"], "EMAIL", a["approval_id"])["status"])
        except delivery.Refused as e:
            out.append(e.detail)
    ts = [threading.Thread(target=run, args=(p, x, a)) for (p, x), a in zip(((pid, v), (pid2, v2)), aps)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(out) == sorted(["SUCCEEDED", delivery.USED_UP]) and provider.writes == ["message"], out


@pytest.mark.parametrize("answer", ["invalid_grant", "outage"])
def test_expired_or_unreachable_refresh_sends_nothing_and_offers_exports(client, deliver_env, monkeypatch, provider, answer):
    pid, v = email_judge(client, deliver_env, monkeypatch)
    connect_shared("EMAIL", expires_in=-10)  # the access token lapsed: the refresh token is used

    def google(req, headers):
        if req["url"].endswith("/token"):
            assert req["form"]["grant_type"] == "refresh_token" and req["form"]["refresh_token"] == "ref"
            return (400, {"error": "invalid_grant"}) if answer == "invalid_grant" else (503, "unavailable")
        return provider(req, headers)
    monkeypatch.setattr(delivery, "SEND", google)
    monkeypatch.setattr(delivery.time, "sleep", lambda s: None)
    a = approve_and_run(client, pid, v, "EMAIL").json()
    assert a["status"] == "FAILED" and provider.writes == [] and "nothing was sent" in a["error"]
    c = db.get_record("con__shared_email")
    assert c["state"] == ("EXPIRED" if answer == "invalid_grant" else "ACTIVE")  # an outage never expires it
    v2 = view(client, pid, v["id"])
    d = dest(v2, "EMAIL")
    assert "Reconnect" not in json.dumps(d) and "connect" not in d["next"]
    if answer == "invalid_grant":
        assert d["summary"] == "UNAVAILABLE_IN_THIS_DEPLOYMENT" and d["text"].startswith("Sending is unavailable right now")
        assert d["next"] == ["review"] and d["shared"]["available"] is False
    assert v2["version"] == v["version"] and delivery.approvals(pid, v["id"], "EMAIL")  # package and approval kept
    assert client.get(f"/api/projects/{pid}/packages/{v['id']}/exports/eml?version={v['version']}").status_code == 200


def test_a_lapsed_access_token_is_refreshed_and_stored(client, deliver_env, monkeypatch, provider):
    pid, v = email_judge(client, deliver_env, monkeypatch)
    connect_shared("EMAIL", expires_in=-10)

    def google(req, headers):
        if req["url"].endswith("/token"):
            return 200, {"access_token": "tok2", "expires_in": 3600}
        return provider(req, headers)
    monkeypatch.setattr(delivery, "SEND", google)
    assert approve_and_run(client, pid, v, "EMAIL").json()["status"] == "SUCCEEDED"
    tok = delivery.get_token("con__shared_email")
    assert tok["access_token"] == "tok2" and tok["refresh_token"] == "ref" and tok["expires_at"] > time.time()


def test_no_configured_id_or_address_in_judge_visible_responses(client, deliver_env, monkeypatch, provider):
    pid, v = ads_judge(client, deliver_env, monkeypatch)
    out = [client.get(f"/api/projects/{pid}/packages/{v['id']}").text]
    provider.after_commit["mutate"] = "timeout"  # UNCERTAIN
    out.append(approve_and_run(client, pid, v, "GOOGLE_ADS").text)
    out += [client.get(f"/api/projects/{pid}/packages/{v['id']}").text, json.dumps(db.events(pid))]  # Activity (this fixture's project view lacks slice-3.5 fields)
    act = client.get(f"/api/projects/{pid}/plans/{v['city_id']}/act")
    out.append(act.text if act.status_code == 200 else "")
    blob = "\n".join(out)
    assert gads.TEST_LABEL in blob
    for secret in (CLIENT, CLIENT_DASHED, MANAGER, "555-000-2222", SENDER, INBOX, "Owner's private ads name"):
        assert secret not in blob, secret
