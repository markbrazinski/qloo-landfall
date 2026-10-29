"""Slice 6.7 · G4: test delivery routing, offline (BUILD-PLAN §6.7.10). Fictional addresses and a SYNTHETIC Gmail: these
prove the server's routing and wording, never a live send (L67-A is the live proof)."""
import base64
import email
import email.policy
import json
import time

import pytest
from test_markets import idle, markets_env  # noqa: F401
from test_slice4 import env, research_ready  # noqa: F401
from test_slice6 import approve_and_run, deliver_env, dest, edit, outreach_ready, provider, view  # noqa: F401
from test_slice45_runner import spec_env  # noqa: F401

from landfall import config, db, delivery, plans

SENDER, INBOX, PARTNER = "sender@landfall-test.invalid", "inbox@landfall-test.invalid", "hello@kettlecrane.example"


def test_mode(monkeypatch):
    monkeypatch.delitem(config._ENV, "LANDFALL_EMAIL_MODE", raising=False)  # unset = test_redirect
    for k, v in {"gmail_test_sender": SENDER, "gmail_test_recipient": INBOX, "LANDFALL_DELIVERY_ALLOWLIST": f"{SENDER},{INBOX}"}.items():
        monkeypatch.setitem(config._ENV, k, v)


def connect_gmail(pid, address=SENDER):
    ad, user, rid = delivery.adapters()["EMAIL"], {"email": address}, delivery.conn_id(pid, "EMAIL")
    delivery.put_token(rid, {"access_token": "tok", "refresh_token": "ref", "expires_at": time.time() + 3600})
    db.put_record("connection", pid, {"provider": "gmail", "destination": "EMAIL", "state": "ACTIVE", "simulated": False, "user": user,
                                      "accounts": [], "selected": None, "account_identity": ad.identity(user, None),
                                      "account_label": ad.label_of(user, None), "scopes": [], "connected_at": db.now(),
                                      "last_ok_at": db.now()}, rid=rid)


def ready(client, env, monkeypatch, sender=SENDER):
    pid, v = outreach_ready(client, env, monkeypatch)
    test_mode(monkeypatch)
    connect_gmail(pid, sender)
    return pid, view(client, pid, v["id"])


def sent_mime(provider):
    req = next(r for s, r in provider.calls if s == "message")
    return email.message_from_bytes(base64.urlsafe_b64decode(req["json"]["raw"]), policy=email.policy.default)


def execute(client, pid, v, approval_id, **extra):
    return client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/EMAIL/execute", json={"approval_id": approval_id, **extra})


def test_1_2_intended_outreach_is_kept_and_gmail_gets_only_the_test_inbox(client, deliver_env, monkeypatch, provider):
    pid, v = ready(client, deliver_env, monkeypatch)
    d = dest(v, "EMAIL")
    assert v["recipients"]["to"] == [PARTNER] and d["routing"]["intended"]["to"] == [PARTNER]
    assert d["routing"]["intended"]["name"] == "Kettle & Crane" and d["routing"]["recipient_label"] == "Private test inbox"
    vals = {x["field"]: x["value"] for x in d["plan"]["values"]}
    assert vals["To"] == "Private test inbox" and vals["From"] == "Landfall test sender" and PARTNER in vals["Prepared for"]
    before = json.dumps(db.get_record(v["id"], v["version"]), sort_keys=True)
    assert approve_and_run(client, pid, v, "EMAIL").json()["status"] == "SUCCEEDED"
    assert json.dumps(db.get_record(v["id"], v["version"]), sort_keys=True) == before  # the package version is untouched
    m = sent_mime(provider)
    assert m["To"] == INBOX and m["Cc"] is None and m["Bcc"] is None and SENDER in m["From"]
    assert m["Subject"].startswith("[Landfall test] ")
    body = m.get_body(("plain",)).get_content()
    assert body.startswith("Test delivery from Landfall. Prepared for Kettle & Crane; Kettle & Crane has not been contacted.")


def test_3_4_recipient_edits_cc_bcc_and_request_fields_never_reach_gmail(client, deliver_env, monkeypatch, provider):
    pid, v = ready(client, deliver_env, monkeypatch)
    v = edit(client, pid, v, {"to": ["someone@else.example"], "cc": ["cc@else.example"], "bcc": ["bcc@else.example"]})
    a = client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/EMAIL/approve", json={"base_version": v["version"]}).json()
    r = execute(client, pid, v, a["approval_id"], to=["x@evil.example"], recipients={"to": ["x@evil.example"]}, raw="AAAA")
    assert r.json()["status"] == "SUCCEEDED"
    m = sent_mime(provider)
    assert m["To"] == INBOX and m["Cc"] is None and m["Bcc"] is None
    assert not any(x in m.as_string() for x in ("else.example", "evil.example"))


@pytest.mark.parametrize("case", ["wrong_sender", "no_sender", "no_inbox"])
def test_5_a_wrong_sender_or_missing_setting_blocks_and_never_falls_back(client, deliver_env, monkeypatch, provider, case):
    pid, v = ready(client, deliver_env, monkeypatch, sender="other@landfall-test.invalid" if case == "wrong_sender" else SENDER)
    if case != "wrong_sender":
        monkeypatch.delitem(config._ENV, "gmail_test_sender" if case == "no_sender" else "gmail_test_recipient")
    d = dest(view(client, pid, v["id"]), "EMAIL")
    texts = [b["text"] for b in d["plan"]["blockers"]]
    assert ("isn't this deployment's Landfall test sender" in " ".join(texts)) if case == "wrong_sender" else \
        ("Test delivery isn't set up" in " ".join(texts))
    assert d["summary"] == "NEEDS_INFORMATION" and d["plan"]["requests"] == []
    a = client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/EMAIL/approve", json={"base_version": v["version"]}).json()
    assert a["use"] == "manual"  # nothing approvable for sending
    assert execute(client, pid, v, a["approval_id"]).status_code in (409, 422) and provider.calls == []


@pytest.mark.parametrize("change", [("gmail_test_recipient", "inbox2@landfall-test.invalid"),
                                    ("gmail_test_sender", "sender2@landfall-test.invalid"), ("LANDFALL_EMAIL_MODE", "direct")])
def test_6_changing_routing_after_approval_needs_a_new_approval(client, deliver_env, monkeypatch, provider, change):
    pid, v = ready(client, deliver_env, monkeypatch)
    a = client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/EMAIL/approve", json={"base_version": v["version"]}).json()
    assert a["use"] == "execute"
    monkeypatch.setitem(config._ENV, *change)
    monkeypatch.setitem(config._ENV, "LANDFALL_DELIVERY_ALLOWLIST", f"{SENDER},{INBOX},inbox2@landfall-test.invalid,{PARTNER}")
    r = execute(client, pid, v, a["approval_id"])
    assert r.status_code in (409, 422) and provider.calls == [], r.text


def test_7_one_send_and_an_uncertain_send_is_never_resent(client, deliver_env, monkeypatch, provider):
    pid, v = ready(client, deliver_env, monkeypatch)
    r = approve_and_run(client, pid, v, "EMAIL").json()
    apv = dest(view(client, pid, v["id"]), "EMAIL")["state"]["approval_id"]
    again = execute(client, pid, v, apv)
    assert r["status"] == "SUCCEEDED" and again.status_code == 409 and provider.writes == ["message"]
    v = edit(client, pid, view(client, pid, v["id"]), {"from_name": "Maya"})
    second = approve_and_run(client, pid, v, "EMAIL")
    assert second.status_code == 409 and "A test email was already sent from this package." in second.text and provider.writes == ["message"]


def test_7b_a_timeout_is_uncertain_and_refuses_a_resend(client, deliver_env, monkeypatch, provider):
    pid, v = ready(client, deliver_env, monkeypatch)
    provider.after_commit["message"] = "timeout"
    assert approve_and_run(client, pid, v, "EMAIL").json()["status"] == "UNCERTAIN"
    v = edit(client, pid, view(client, pid, v["id"]), {"from_name": "Maya"})
    assert approve_and_run(client, pid, v, "EMAIL").status_code == 409 and provider.writes == ["message"]


def test_8_a_test_send_is_test_sent_never_contact_and_your_own_mark_outranks_it(client, deliver_env, monkeypatch, provider):
    pid, v = ready(client, deliver_env, monkeypatch)
    aid = approve_and_run(client, pid, v, "EMAIL").json()["id"]
    pkg = db.get_record(v["id"])
    d = dest(view(client, pid, v["id"]), "EMAIL")
    assert plans._dst_status(pid, pkg, "EMAIL", []) == "TEST_SENT" and d["summary"] == "TEST_SENT"
    assert "Kettle & Crane has not been contacted" in d["text"] and "delivered" not in d["text"].lower()
    assert not db.records("sent_record", pid) and "delivery_ref" not in db.get_record(aid)
    assert client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/EMAIL/manual", json={}).status_code == 200
    assert plans._dst_status(pid, pkg, "EMAIL", []) == "MARKED_SENT_BY_YOU"
    assert dest(view(client, pid, v["id"]), "EMAIL")["manual"] == "MARKED_SENT_BY_YOU"


def test_9_no_configured_address_leaves_the_server(client, deliver_env, monkeypatch, provider):
    pid, v = ready(client, deliver_env, monkeypatch)
    out = [client.get(f"/api/projects/{pid}/packages/{v['id']}").text]  # before
    monkeypatch.setitem(config._ENV, "LANDFALL_DELIVERY_ALLOWLIST", SENDER)  # a refusal's text
    out.append(approve_and_run(client, pid, v, "EMAIL").text)
    monkeypatch.setitem(config._ENV, "LANDFALL_DELIVERY_ALLOWLIST", f"{SENDER},{INBOX}")
    out.append(approve_and_run(client, pid, view(client, pid, v["id"]), "EMAIL").text)  # success
    out += [client.get(f"/api/projects/{pid}/packages/{v['id']}").text, json.dumps(db.events(pid)),  # Activity (this fixture's project view lacks slice-3.5 fields)
            client.get(f"/api/projects/{pid}/packages/{v['id']}/exports/eml?version={v['version']}").text,
            client.get(f"/api/projects/{pid}/packages/{v['id']}/exports/outreach_zip?version={v['version']}").text]
    blob = "\n".join(out)
    assert "aren't on the delivery allow-list" in blob
    assert SENDER not in blob and INBOX not in blob


def test_10_judging_refuses_direct_mode_at_start(monkeypatch):
    monkeypatch.setitem(config._ENV, "LANDFALL_ENV", "judging")
    monkeypatch.setitem(config._ENV, "LANDFALL_EMAIL_MODE", "direct")
    with pytest.raises(RuntimeError):
        delivery.startup_check()
    monkeypatch.setitem(config._ENV, "LANDFALL_EMAIL_MODE", "test_redirect")
    delivery.startup_check()


def test_11_confirming_test_receipt_is_never_a_real_outreach_record(client, deliver_env, monkeypatch, provider):
    pid, v = ready(client, deliver_env, monkeypatch)
    provider.after_commit["message"] = "timeout"
    aid = approve_and_run(client, pid, v, "EMAIL").json()["id"]
    d = dest(view(client, pid, v["id"]), "EMAIL")
    assert d["summary"] == "UNCERTAIN" and "private test inbox" in d["text"]
    r = client.post(f"/api/projects/{pid}/executions/{aid}/resolve", json={"happened": True})
    assert r.status_code == 200 and r.json()["test"] == "TEST_RECEIPT_CONFIRMED"
    delivery.recover()  # reload / restart
    pkg = db.get_record(v["id"])
    d = dest(view(client, pid, v["id"]), "EMAIL")
    assert not db.records("manual_record", pid) and d["manual"] is None and d["manual_records"] == []
    assert "Marked sent by you" not in json.dumps(db.events(pid))
    assert db.get_record(aid)["status"] == "UNCERTAIN" and "delivery_ref" not in db.get_record(aid)  # Gmail's answer stays
    assert plans._dst_status(pid, pkg, "EMAIL", []) == "TEST_SENT" and d["summary"] == "TEST_SENT"
    assert "you confirmed it in the private test inbox" in d["text"]
    v = edit(client, pid, view(client, pid, v["id"]), {"from_name": "Maya"})
    assert approve_and_run(client, pid, v, "EMAIL").status_code == 409 and provider.writes == ["message"]
    monkeypatch.setitem(config._ENV, "gmail_test_recipient", "elsewhere@landfall-test.invalid")
    assert plans._dst_status(pid, db.get_record(v["id"]), "EMAIL", []) == "TEST_SENT"  # its own routing, not the config
    assert client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/EMAIL/manual", json={}).status_code == 200
    assert plans._dst_status(pid, db.get_record(v["id"]), "EMAIL", []) == "MARKED_SENT_BY_YOU"


# ---- A · M · T: locked test destinations, environment in the identity, allow-list (offline; fictional ids) ----
from landfall.adapters import google_ads as gads, meta as metamod, tiktok as tt  # noqa: E402

CLIENT, MANAGER, ACT = "5550001111", "5550002222", "act_777"


def ad_config(monkeypatch, **extra):
    for k, v in {"google_oauth_client_id": "cid", "google_oauth_client_secret": "cs", "LANDFALL_GOOGLE_ADS_ACCESS": "TEST",
                 "google_ads_test_customer_id": "555-000-1111", "google_ads_test_manager_id": MANAGER,
                 "meta_app_id": "app", "meta_app_secret": "sec", "LANDFALL_META_ACCESS": "SANDBOX", "meta_ad_account_id": ACT,
                 "LANDFALL_DELIVERY_ALLOWLIST": f"{SENDER},{INBOX},{CLIENT},{ACT}", **extra}.items():
        monkeypatch.setitem(config._ENV, k, v)
    for k in ("meta_login_config_id", "meta_graph_version"):
        monkeypatch.delitem(config._ENV, k, raising=False)


def test_a2_the_configured_test_client_is_reached_through_the_configured_manager(monkeypatch):
    ad_config(monkeypatch)
    calls = []

    def google(req, headers):
        calls.append((req["url"], headers))
        if "oauth2.googleapis.com/token" in req["url"]:
            return 200, {"access_token": "tok", "expires_in": 3600, "scope": "openid email https://www.googleapis.com/auth/adwords"}
        if "userinfo" in req["url"]:
            return 200, {"email": "owner@landfall-test.invalid"}
        return 200, {"results": [{"customer": answer}]}
    monkeypatch.setattr(delivery, "SEND", google)
    ga = gads.GoogleAds()
    got = ga.connect({"code": "c"}, "http://x/cb")
    assert got["accounts"] == [{"id": CLIENT, "name": gads.TEST_LABEL}]  # manager-only access still reaches the client
    assert not any("listAccessibleCustomers" in u for u, _ in calls)
    answer = {"id": CLIENT, "testAccount": True, "manager": False, "currencyCode": "USD", "descriptiveName": "t"}
    sel = ga.select(got["accounts"][0], {"login_customer_id": "123"}, {}, {"access_token": "tok"})
    assert sel["login_customer_id"] == MANAGER and calls[-1][1]["login-customer-id"] == MANAGER  # the form's value is ignored
    assert ga.identity({"email": "o"}, sel)["environment"] == "TEST_ACCOUNT"
    for bad in ({**answer, "testAccount": False}, {**answer, "manager": True}, {**answer, "id": MANAGER}, {**answer, "id": "999"}):
        answer = bad
        with pytest.raises(delivery.Refused, match="isn't a test client account"):
            ga.select({"id": CLIENT}, {}, {}, {"access_token": "tok"})
    monkeypatch.delitem(config._ENV, "google_ads_test_customer_id")
    assert "google_ads_test_customer_id" in ga.missing_config()


def test_r3_configured_ad_accounts_pass_the_allow_list_and_others_are_refused(monkeypatch):
    ad_config(monkeypatch)
    assert delivery.allow_listed("GOOGLE_ADS", {"customer_id": CLIENT}, {}) is None
    assert delivery.allow_listed("GOOGLE_ADS", {"customer_id": "5550009999"}, {})
    assert delivery.allow_listed("META_ADS", {"ad_account_id": ACT}, {}) is None
    assert delivery.allow_listed("META_ADS", {"ad_account_id": "act_888"}, {})


def test_m1_m2_meta_scope_dialog_v25_and_the_sandbox_account_only(monkeypatch):
    ad_config(monkeypatch)
    m = metamod.Meta()
    url = m.auth_url("st", "http://x/cb")
    assert "/v25.0/dialog/oauth" in url and "scope=ads_management" in url and "config_id" not in url
    assert m.missing_config() == [] and m.access()["state"] == "SANDBOX"
    conn = {"user": {"pages": [{"id": "pg", "name": "P"}]}}
    sel = m.select({"id": ACT, "name": "x", "currency": "USD"}, {"page_id": "pg"}, conn, None)
    assert m.identity({"id": "u"}, sel)["environment"] == "SANDBOX" and m.label_of({"id": "u"}, sel) == metamod.SANDBOX_LABEL
    with pytest.raises(delivery.Refused):
        m.select({"id": "act_888", "name": "x", "currency": "USD"}, {"page_id": "pg"}, conn, None)


def test_a1_outcome_wording_names_the_environment():
    ga = gads.GoogleAds()
    a = {"ids": {"mutate": "customers/1/campaigns/2"}}
    assert ga.success_text({**a, "identity": {"environment": "TEST_ACCOUNT"}}).startswith("Campaign created in test account (never serves)")
    assert ga.success_text({**a, "identity": {"environment": "SANDBOX"}}).startswith("Campaign created in sandbox")
    assert ga.success_text({**a, "identity": {"environment": "PRODUCTION"}}).startswith("Paused campaign created in Google Ads")


def test_t1_tiktok_sandbox_mode_is_configured_not_oauth_and_without_access_says_approval_required(monkeypatch):
    for k in ("tiktok_sandbox_token", "tiktok_sandbox_advertiser_id", "tiktok_sandbox_location_id", "LANDFALL_TIKTOK_ACCESS"):
        monkeypatch.delitem(config._ENV, k, raising=False)
    monkeypatch.setitem(config._ENV, "LANDFALL_TOKEN_KEY", __import__("cryptography.fernet").fernet.Fernet.generate_key().decode())
    t = tt.TikTok()
    assert delivery.capability("TIKTOK_ADS")["why_not"] in ("Platform approval required", "Direct delivery unavailable in this deployment")
    monkeypatch.setitem(config._ENV, "LANDFALL_TIKTOK_ACCESS", "SANDBOX")
    assert set(t.missing_config()) == set(tt.SANDBOX_CONFIG)
    for k, v in {"tiktok_sandbox_token": "portal-tok", "tiktok_sandbox_advertiser_id": "7007", "tiktok_sandbox_location_id": "6167865"}.items():
        monkeypatch.setitem(config._ENV, k, v)
    assert tt.base().startswith("https://sandbox-ads.tiktok.com/") and delivery.capability("TIKTOK_ADS")["can_connect"]
    got = t.direct_connect()
    assert got["token"]["access_token"] == "portal-tok" and got["accounts"][0]["id"] == "7007"
    monkeypatch.setattr(delivery, "SEND", lambda req, headers: (200, {"code": 0, "data": {"list": [{"currency": "USD"}]}}) if "/advertiser/info/"
                        in req["url"] else pytest.fail(f"unexpected call {req['url']}"))  # never /tool/region/
    sel = t.select({"id": "7007", "name": "s"}, {"city_id": "toronto"}, {}, {"access_token": "portal-tok"})
    assert sel["location_ids"] == {"toronto": ["6167865"]} and t.identity({}, sel)["environment"] == "SANDBOX"
    with pytest.raises(delivery.Refused):
        t.select({"id": "8008", "name": "s"}, {"city_id": "toronto"}, {}, {"access_token": "portal-tok"})
