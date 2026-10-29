"""Slice 6 · delivery, offline (BUILD-PLAN "Slice 6 · Delivery" §6.3). SYNTHETIC pages, model answers, images and
provider responses: these prove handling, never a live integration (the slice report's status matrix tracks that)."""
import base64
import json
import re
import struct
import zlib

import pytest
from cryptography.fernet import Fernet
from test_markets import idle, markets_env  # noqa: F401
from test_slice4 import env, research_ready  # noqa: F401
from test_slice45_runner import spec_env  # noqa: F401

from landfall import config, db, delivery, fetch, specialist

PRODUCT = "http://tidewell.example/products/hibiscus"
IMAGE = "https://cdn.tidewell.example/hibiscus.png"


def png(seed: int = 0) -> bytes:
    """A real 1×1 PNG; `seed` changes the pixel, so the bytes (and the digest) change."""
    raw = b"\x00" + bytes([seed % 256, 0, 0])
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)  # noqa: E731
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


@pytest.fixture
def deliver_env(spec_env, monkeypatch):
    """A Toronto retail investigation with one product, Develop, SYNTHETIC copy and checks, and one product image.
    It runs on the pre-6.5 route (no beachhead or plan), which only tests and replays may use [P65-I2]."""
    from landfall import plans
    monkeypatch.setattr(plans, "PRE_PLAN_ROUTE", True)
    env = spec_env
    env.images = {IMAGE: png(1)}
    env.web.pages[PRODUCT] = (200, f'<html><head><title>Hibiscus Berry Tea</title><meta property="og:image" content="{IMAGE}">'
                                   '</head><body><p>Hibiscus and berry sparkling tea.</p></body></html>')
    web = env.web

    def live_get(url, binary=False):
        if binary and url in env.images:
            return {"url": url, "status": 200, "content_type": "image/png", "hops": [], "body": "",
                    "b64": base64.b64encode(env.images[url]).decode()}
        return web(url)
    monkeypatch.setattr(fetch, "_live_get", live_get)
    monkeypatch.setattr(config, "LIVE_STAGES", config.LIVE_STAGES + ("develop", "delivery_copy", "delivery_fetch"))
    env.copy = {"subject": "Tidewell canned teas for your makers shelf",
                "paragraphs": [{"text": "Kettle & Crane already carries teas from small Ontario makers.", "cites": ["E1"]},
                               {"text": "Would you try four of our canned teas on that shelf for a month?"},
                               {"text": "Our teas won a national award last year."}]}
    monkeypatch.setattr(delivery, "copy_call", lambda ctx, prompt: env.copy)

    def judge(ctx, listing):
        out = []
        for i, s in re.findall(r"- id=(s\d+) \|.*?sentence: \"(.*?)\" \|", listing):
            out.append({"id": i, "kind": "UNSUPPORTED_FACT" if "award" in s else "SUPPORTED_FACT" if "carries" in s else "PROPOSAL"})
        return {"verdicts": out}
    monkeypatch.setattr(delivery, "judge_copy", judge)
    monkeypatch.setattr(specialist, "draft_call", lambda ctx, prompt: {
        "concept_name": "Makers-shelf trial", "concept_line": "Four canned teas on Kettle & Crane's makers shelf for a month.",
        "activities": ["Send a sample case", "Agree a four-week shelf slot"], "success_measure": "Units sold in four weeks",
        "audience_hypothesis": "Shoppers who already buy small-maker teas", "partners": ["Kettle & Crane"],
        "products": ["Hibiscus Berry Tea"]})
    for k in ("LANDFALL_TOKEN_KEY", "google_oauth_client_id", "google_oauth_client_secret", "LANDFALL_DELIVERY_ALLOWLIST",
              "meta_app_id", "meta_app_secret", "meta_login_config_id", "meta_graph_version", "LANDFALL_GOOGLE_ADS_ACCESS",
              "tiktok_app_id", "tiktok_app_secret", "LANDFALL_TIKTOK_ACCESS", "LANDFALL_GMAIL_ACCESS", "LANDFALL_META_ACCESS",
              "LANDFALL_EMAIL_MODE", "gmail_test_sender", "gmail_test_recipient", "LANDFALL_ENV",
              "google_ads_test_manager_id", "google_ads_test_customer_id", "meta_ad_account_id", "tiktok_sandbox_token",
              "tiktok_sandbox_advertiser_id", "tiktok_sandbox_location_id"):
        monkeypatch.delitem(config._ENV, k, raising=False)
    return env


def with_product(client, env):
    from test_slice4 import scanned
    pid, _ = scanned(client, env)
    p = db.put_record("product", pid, {"title": "Hibiscus Berry Tea", "product_url": PRODUCT, "snapshot_id": None,
                                       "price_text": None, "category_terms": ["tea"], "materials": [], "discovered_via": "BRAND_PAGES",
                                       "mcp": None, "vc": "F"})
    db.put_record("product_research", pid, {"products": [{"id": p["id"], "version": p["version"]}], "attempts": [], "at": db.now()},
                  rid=f"prr_{pid}")
    return pid


def drafted(client, env):
    from test_slice4 import toronto_pages, toronto_script
    pid = with_product(client, env)
    toronto_pages(env.web)
    env.scripts.setdefault("toronto", toronto_script)
    assert client.post(f"/api/projects/{pid}/research", json={"city_ids": ["toronto"]}).status_code == 200
    idle(pid)
    r = client.post(f"/api/projects/{pid}/cities/toronto/methods/retail/investigate", json={}).json()
    idle(pid)
    d = client.get(f"/api/projects/{pid}/dossiers/{r['dossier_id']}").json()
    rid = client.post(f"/api/projects/{pid}/dossiers/{d['id']}/develop", json={}).json()["draft_id"]
    idle(pid)
    return pid, client.get(f"/api/projects/{pid}/drafts/{rid}").json()


def packaged(client, env):
    pid, dr = drafted(client, env)
    r = client.post(f"/api/projects/{pid}/drafts/{dr['id']}/package", json={"base_version": dr["version"]})
    assert r.status_code == 200, r.text
    idle(pid)
    v = client.get(f"/api/projects/{pid}/packages/{r.json()['package_id']}").json()
    assert "preparing" not in v or not v["preparing"], v
    return pid, v


# ---- connections and fake providers (SYNTHETIC: they prove handling only) ----
import hashlib  # noqa: E402
import time  # noqa: E402

USERS = {"EMAIL": ({"email": "founder@tidewell.example"}, None),
         "META_ADS": ({"id": "u1", "name": "Founder", "pages": [{"id": "pg1", "name": "Tidewell"}]},
                      {"id": "act_123", "name": "Tidewell ads", "currency": "CAD", "page_id": "pg1", "page_name": "Tidewell"}),
         "GOOGLE_ADS": ({"email": "founder@tidewell.example"},
                        {"id": "1234567890", "name": "Tidewell", "currency": "CAD", "login_customer_id": None}),
         "TIKTOK_ADS": ({"name": "TikTok advertiser"},
                        {"id": "7001", "name": "Tidewell", "currency": "CAD", "location_ids": {"toronto": ["6167865"]}})}


def configure(monkeypatch, allow="founder@tidewell.example,hello@kettlecrane.example,act_123,1234567890,7001"):
    """A deployment with every adapter configured (names only; the values are SYNTHETIC) and live mode on."""
    monkeypatch.setattr(config, "REPLAY_CASES", [])
    for k, v in {"LANDFALL_TOKEN_KEY": Fernet.generate_key().decode(), "google_oauth_client_id": "cid", "google_oauth_client_secret": "cs",
                 "meta_app_id": "app", "meta_app_secret": "sec", "meta_login_config_id": "cfg", "meta_graph_version": "v24.0",
                 "LANDFALL_GOOGLE_ADS_ACCESS": "TEST", "tiktok_app_id": "tapp", "tiktok_app_secret": "tsec",
                 "LANDFALL_TIKTOK_ACCESS": "APPROVED", "LANDFALL_DELIVERY_ALLOWLIST": allow,
                 "LANDFALL_EMAIL_MODE": "direct", "google_ads_test_manager_id": "9999999999",
                 "google_ads_test_customer_id": "1234567890"}.items():  # slice 6's own send path; 6.7's test routing is test_slice67
        monkeypatch.setitem(config._ENV, k, v)


def connect(pid, dst, selected=None):
    ad = delivery.adapters()[dst]
    user, sel = USERS[dst]
    sel = selected or sel
    rid = delivery.conn_id(pid, dst)
    delivery.put_token(rid, {"access_token": "tok", "refresh_token": "ref", "expires_at": time.time() + 3600})
    db.put_record("connection", pid, {"provider": ad.PROVIDER, "destination": dst, "state": "ACTIVE", "simulated": False,
                                      "user": user, "accounts": [sel] if sel else [], "selected": sel,
                                      "account_identity": ad.identity(user, sel), "account_label": ad.label_of(user, sel),
                                      "scopes": [], "connected_at": db.now(), "last_ok_at": db.now()}, rid=rid)


RESULT = {"campaignBudgetOperation": ("campaignBudgetResult", "campaignBudgets"), "campaignOperation": ("campaignResult", "campaigns"),
          "campaignCriterionOperation": ("campaignCriterionResult", "campaignCriteria"), "adGroupOperation": ("adGroupResult", "adGroups"),
          "adGroupCriterionOperation": ("adGroupCriterionResult", "adGroupCriteria"), "adGroupAdOperation": ("adGroupAdResult", "adGroupAds")}


class Provider:
    """SYNTHETIC providers for all four adapters. `after_commit[step] = status` commits the write, then answers that
    status (or raises a timeout for "timeout"); `reject[step]` answers a documented definitive rejection."""

    def __init__(self):
        self.calls, self.writes, self.after_commit, self.reject, self.status = [], [], {}, {}, {}

    def step_of(self, req):
        u, m = req["url"], req["method"]
        for key in ("adimages", "campaigns", "adsets", "adcreatives", "ads"):
            if u.endswith(f"/{key}"):
                return {"adimages": "image", "campaigns": "campaign", "adsets": "adset", "adcreatives": "creative", "ads": "ad"}[key]
        for key, step in (("/identity/create/", "identity"), ("/file/image/ad/upload/", "image"), ("/campaign/create/", "campaign"),
                          ("/adgroup/create/", "adgroup"), ("/ad/create/", "ad"), ("messages/send", "message")):
            if u.endswith(key):
                return step
        if u.endswith(":mutate"):
            return "repause" if any("update" in next(iter(o.values())) for o in req["json"]["mutateOperations"]) else "mutate"
        if "/status/update/" in u or (m == "POST" and "graph.facebook" in u):
            return "repause"
        return "read"

    def __call__(self, req, headers):
        assert "tok" in json.dumps(headers), "the token is added only at dispatch"
        step = self.step_of(req)
        self.calls.append((step, req))
        if step == "read":
            return self.answer_read(req)
        if step in self.reject:
            return self.reject[step]
        self.writes.append(step)
        if step == "repause":
            self.status.clear()
            return (200, {"code": 0, "data": {}}) if "tiktok" in req["url"] else (200, {"success": True}) if "facebook" in req["url"] \
                else (200, {"mutateOperationResponses": [{"campaignResult": {"resourceName": "x"}}]})
        ok = self.ok(step, req)
        if step in self.after_commit:
            if self.after_commit[step] == "timeout":
                raise delivery.Transport("ReadTimeout")
            return self.after_commit[step], "<html>Bad Gateway</html>"
        return ok

    def ok(self, step, req):
        u = req["url"]
        if step == "message":
            return 200, {"id": "msg1", "threadId": "thr1", "labelIds": ["SENT"]}
        if "facebook" in u:
            return 200, {"images": {"bytes": {"hash": "imghash"}}} if step == "image" else {"id": f"{step}-1"}
        if "tiktok" in u:
            key = {"identity": "identity_id", "image": "image_id", "campaign": "campaign_id", "adgroup": "adgroup_id"}.get(step)
            return 200, {"code": 0, "message": "OK", "request_id": "r", "data": {"ad_ids": ["ad-1"]} if step == "ad" else {key: f"{step}-1"}}
        out = []
        for i, op in enumerate(req["json"]["mutateOperations"]):
            k = next(iter(op))
            out.append({RESULT[k][0]: {"resourceName": f"customers/1234567890/{RESULT[k][1]}/{100 + i}"}})
        return 200, {"mutateOperationResponses": out}

    def answer_read(self, req):
        u = req["url"]
        if "facebook" in u:
            return 200, {"status": self.status.get("meta", "PAUSED"), "effective_status": self.status.get("meta_eff", "PAUSED")}
        if "tiktok" in u:
            return 200, {"code": 0, "data": {"list": [{"operation_status": self.status.get("tiktok", "DISABLE")}]}}
        if ":search" in u:
            key = "adGroupAd" if "FROM ad_group_ad" in req["json"]["query"] else "adGroup" if "FROM ad_group" in req["json"]["query"] else "campaign"
            return 200, {"results": [{key: {"status": self.status.get("google", "PAUSED")}}]}
        return 404, {}


@pytest.fixture
def provider(monkeypatch):
    p = Provider()
    monkeypatch.setattr(delivery, "SEND", p)
    return p


def outreach_ready(client, env, monkeypatch):
    """The retail package with its recipient (found on the shop's site), a sender name and the flagged sentence removed."""
    pid, v = packaged(client, env)
    edit(client, pid, v, {"from_name": "Maya at Tidewell", "segments": [s["text"] for s in v["copy"]["segments"] if "award" not in s["text"]]})
    configure(monkeypatch)
    connect(pid, "EMAIL")
    return pid, view(client, pid, v["id"])


def digital_packaged(client, env, monkeypatch):
    from test_slice4 import toronto_pages, toronto_script
    pid = with_product(client, env)
    toronto_pages(env.web)
    env.scripts.setdefault("toronto", toronto_script)
    client.post(f"/api/projects/{pid}/research", json={"city_ids": ["toronto"]})
    idle(pid)
    env.spec_script = lambda T: {"concept_name": "City waitlist", "concept_line": "Invite Toronto tea drinkers to a waitlist.",
                                 "channel_primary": "Instagram (Meta)", "channel_alternative": "Google Search", "objective": "SIGN_UP",
                                 "radius_km_hypothesis": 25, "targeting": [
                                     {"element": "city + radius", "basis": "PLATFORM_OPTION", "platforms_ref": "meta.city_radius"}],
                                 "message": "Sparkling cold-brewed tea, now on a Toronto waitlist.", "primary_metric": "Waitlist sign-ups",
                                 "stop_rule": "Stop after two weeks if sign-ups stay flat."}
    r = client.post(f"/api/projects/{pid}/cities/toronto/methods/digital/investigate", json={}).json()
    idle(pid)
    rid = client.post(f"/api/projects/{pid}/dossiers/{r['dossier_id']}/develop", json={}).json()["draft_id"]
    idle(pid)
    env.copy = {"headlines": [{"text": "Sparkling tea in Toronto"}, {"text": "Cold-brewed, lightly fizzy"}, {"text": "Try Tidewell"}],
                "descriptions": [{"text": "Hibiscus and berry sparkling tea in a can."}, {"text": "Join the Toronto waitlist today."}],
                "primary_text": {"text": "Sparkling cold-brewed tea, now on a Toronto waitlist."},
                "ad_text": {"text": "Sparkling cold-brewed tea for Toronto."}, "keywords": ["sparkling tea toronto", "canned tea"],
                "cta": "SIGN_UP"}
    pkg_id = client.post(f"/api/projects/{pid}/drafts/{rid}/package", json={}).json()["package_id"]
    idle(pid)
    return pid, view(client, pid, pkg_id)


def ads_ready(client, env, monkeypatch):
    pid, v = digital_packaged(client, env, monkeypatch)
    v = edit(client, pid, v, {"destinations": ["META_ADS", "GOOGLE_ADS", "TIKTOK_ADS"], "budget": {"amount": "25", "ccy": "CAD"},
                              "start": "2026-11-02", "radius_km": 20, "rights_confirmed": True})
    configure(monkeypatch)
    for dst in ("META_ADS", "GOOGLE_ADS", "TIKTOK_ADS"):
        connect(pid, dst)
    return pid, view(client, pid, v["id"])


def view(client, pid, pkg_id):
    return client.get(f"/api/projects/{pid}/packages/{pkg_id}").json()


def edit(client, pid, v, fields):
    r = client.patch(f"/api/projects/{pid}/packages/{v['id']}", json={"base_version": v["version"], "fields": fields})
    assert r.status_code == 200, r.text
    return view(client, pid, v["id"])


def dest(v, dst):
    return next(d for d in v["destination_states"] if d["destination"] == dst)


def approve_and_run(client, pid, v, dst):
    r = client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/{dst}/approve", json={"base_version": v["version"]})
    assert r.status_code == 200, r.text
    assert r.json()["use"] == "execute", r.json()
    return client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/{dst}/execute", json={"approval_id": r.json()["approval_id"]})


# ---- the package, readiness, exports and approval ----
def test_outreach_package_from_the_draft_needs_information_exports_and_outdates(client, deliver_env):
    pid, v = packaged(client, deliver_env)
    assert v["flow"] == "outreach" and v["destinations"] == ["EMAIL"] and v["copy"]["locale"] == "en-CA"
    states = {s["text"]: s["state"] for s in v["copy"]["segments"]}
    assert states["Kettle & Crane already carries teas from small Ontario makers."] == "SUPPORTED"
    assert states["Our teas won a national award last year."] == "TO_CHECK"  # unsupported: blocks approval, never dropped silently
    assert v["recipients"]["to"] == ["hello@kettlecrane.example"] and v["recipients_by"]["to"] == "SOURCE"
    e = dest(v, "EMAIL")
    assert e["state"]["package"] == "NEEDS_INFORMATION" and {m["field"] for m in e["state"]["missing"]} == {"paragraphs[2]", "from_name"}
    assert e["summary"] == "UNAVAILABLE_IN_THIS_DEPLOYMENT" or e["state"]["simulated"]
    assert v["assets"][0]["rights"] == "brand_own_unconfirmed" and v["assets"][0]["source"]["url"] == IMAGE
    assert {i["field"] for i in v["info"]} == {"selection", "budget"}  # pre-slice-5: shown, not invented
    # usable partial materials export while information is still missing; an export is never a SENT record
    r1 = client.get(f"/api/projects/{pid}/packages/{v['id']}/exports/eml")
    r2 = client.get(f"/api/projects/{pid}/packages/{v['id']}/exports/eml")
    assert r1.status_code == 200 and r1.content == r2.content  # deterministic bytes of one version
    assert b"hello@kettlecrane.example" in r1.content and base64.b64encode(png(1))[:20] in r1.content.replace(b"\r\n", b"")
    z = client.get(f"/api/projects/{pid}/packages/{v['id']}/exports/outreach_zip")
    import io
    import zipfile
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert "message.eml" in names and any(n.startswith("attachments/") for n in names)
    att = next(n for n in names if n.startswith("attachments/"))
    assert hashlib.sha256(zipfile.ZipFile(io.BytesIO(z.content)).read(att)).hexdigest() == v["assets"][0]["artifact"]["sha256"]
    assert not db.records("execution", pid) and not db.records("manual_record", pid)
    assert len(db.records("export", pid)) == 3
    assert v["email"]["mailto"].startswith("mailto:hello@kettlecrane.example?subject=")
    # resolve both items → READY; approve (no connection: for your own use); edit the subject → OUTDATED
    v = edit(client, pid, v, {"from_name": "Maya at Tidewell", "segments": [s["text"] for s in v["copy"]["segments"][:2]]})
    assert dest(v, "EMAIL")["state"]["package"] == "VALID"
    assert client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/EMAIL/approve", json={"base_version": v["version"] - 1}).status_code == 409
    r = client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/EMAIL/approve", json={"base_version": v["version"]}).json()
    assert r["use"] == "manual" and dest(view(client, pid, v["id"]), "EMAIL")["state"]["approval"] == "APPROVED"
    v = edit(client, pid, view(client, pid, v["id"]), {"subject": "A shelf trial for Kettle & Crane?"})
    assert dest(v, "EMAIL")["state"]["approval"] == "OUTDATED" and v["copy"]["subject"]["state"] == "YOU_SAID"


def test_artifacts_are_the_saved_bytes_never_a_substitute(client, deliver_env, monkeypatch):
    pid, v = packaged(client, deliver_env)
    a0 = v["assets"][0]
    deliver_env.images[IMAGE] = png(2)  # the remote image changes at the same address after the package was built
    got = client.get(f"/api/projects/{pid}/packages/{v['id']}/exports/asset?artifact={a0['artifact']['id']}").content
    assert hashlib.sha256(got).hexdigest() == a0["artifact"]["sha256"] == hashlib.sha256(png(1)).hexdigest()
    up = client.post(f"/api/projects/{pid}/packages/{v['id']}/assets", json={  # a replacement file under the same name
        "base_version": v["version"], "filename": a0["filename"], "media_type": "image/png",
        "data_b64": base64.b64encode(png(3)).decode(), "role": "ATTACHMENT"}).json()
    v2 = view(client, pid, v["id"])
    assert len({a["artifact"]["sha256"] for a in v2["assets"]}) == 2 and v2["assets"][1]["source"]["kind"] == "FOUNDER_UPLOAD"
    assert db.get_record(up["artifact_id"])["rights"] == "founder_supplied"
    db.x("DELETE FROM artifact_bytes WHERE sha256=?", a0["artifact"]["sha256"])  # the saved source is gone after the fact
    v3 = view(client, pid, v["id"])
    assert any(m["field"] == "assets" for m in dest(v3, "EMAIL")["state"]["missing"]) and not v3["assets"][0]["readable"]
    assert client.get(f"/api/projects/{pid}/packages/{v['id']}/exports/eml").status_code == 422  # blocks; never re-fetched


def test_unconfigured_deployment_never_says_connect_your_account(client, deliver_env, monkeypatch):
    pid, v = packaged(client, deliver_env)
    monkeypatch.setattr(config, "REPLAY_CASES", [])
    e = dest(view(client, pid, v["id"]), "EMAIL")
    assert e["summary"] == "UNAVAILABLE_IN_THIS_DEPLOYMENT" and e["text"] == "Direct delivery unavailable in this deployment."
    assert e["state"]["connection"] == "NOT_AVAILABLE" and "LANDFALL_TOKEN_KEY" in e["state"]["missing_config"]
    assert client.post(f"/api/projects/{pid}/connections/EMAIL/start", json={"package_id": v["id"]}).status_code == 409
    configure(monkeypatch)
    e = dest(view(client, pid, v["id"]), "EMAIL")
    assert e["summary"] == "CONNECT_YOUR_ACCOUNT" and e["state"]["platform_access"] == "REQUIRED"  # Testing mode: test users only
    url = client.post(f"/api/projects/{pid}/connections/EMAIL/start", json={"package_id": v["id"]}).json()["url"]
    assert "gmail.send" in url and "redirect_uri=http" in url
    t = dest(view(client, pid, v["id"]), "TIKTOK_ADS") if "TIKTOK_ADS" in v["destinations"] else None
    assert t is None  # an outreach experiment shows email only, never every provider


def test_oauth_state_is_single_use_and_bound_to_this_browser(client, deliver_env, monkeypatch, provider):
    pid, v = packaged(client, deliver_env)
    configure(monkeypatch)
    url = client.post(f"/api/projects/{pid}/connections/EMAIL/start", json={"package_id": v["id"]}).json()["url"]
    state = re.search(r"state=([^&]+)", url).group(1)

    def google(req, headers):
        if req["url"].endswith("/token"):
            return 200, {"access_token": "tok", "refresh_token": "ref", "expires_in": 3600,
                         "scope": "openid email https://www.googleapis.com/auth/gmail.send"}
        return 200, {"email": "founder@tidewell.example", "sub": "1"}
    monkeypatch.setattr(delivery, "SEND", lambda req, headers: google(req, headers))
    r = client.get(f"/api/connections/callback?state={state}&code=abc", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/p/{pid}/deliver/{v['id']}"
    c = delivery.conn_for(pid, "EMAIL")
    assert c["account_identity"] == {"email": "founder@tidewell.example"} and "tok" not in json.dumps(c)
    assert delivery.get_token(c["id"])["access_token"] == "tok"  # encrypted outside records
    assert client.get(f"/api/connections/callback?state={state}&code=abc", follow_redirects=False).status_code == 400


# ---- ProviderPlans: one complete SYNTHETIC golden sequence per platform (P6-R3) ----
GOLDEN = config.ROOT / "fixtures" / "synthetic" / "slice6-plans.json"


def normalized(pid, plan):
    """Ids that differ per test run are replaced, so the golden file pins the request shapes and values."""
    return json.loads(re.sub(r"Landfall [0-9a-f]{8} ", "Landfall {ref} ", json.dumps(plan["requests"]).replace(pid, "{project}")))


def test_provider_plans_match_the_synthetic_goldens(client, deliver_env, monkeypatch):
    o_pid, o = packaged(client, deliver_env)  # both packages are built while the upstream still replays
    o = edit(client, o_pid, o, {"from_name": "Maya at Tidewell", "segments": [s["text"] for s in o["copy"]["segments"][:2]]})
    pid, v = ads_ready(client, deliver_env, monkeypatch)
    connect(o_pid, "EMAIL")
    pkg = db.get_record(v["id"])
    got = {}
    for dst in ("META_ADS", "GOOGLE_ADS", "TIKTOK_ADS"):
        plan = delivery.provider_plan(pid, pkg, dst)
        assert plan["blockers"] == [], (dst, plan["blockers"])
        assert {x["label"] for x in plan["values"]} <= {"FIXED", "FOUNDER", "ACCOUNT", "DERIVED"}
        got[dst] = delivery.strip_bulk(normalized(pid, plan))
    got["EMAIL"] = [{**r, "json": {"raw": "<mime>"}} for r in normalized(o_pid, delivery.provider_plan(o_pid, db.get_record(o["id"]), "EMAIL"))]
    if not GOLDEN.exists() or config.env("LANDFALL_REGENERATE_GOLDEN"):
        GOLDEN.write_text(json.dumps({"label": "SYNTHETIC · not provider validated", "plans": got}, indent=1) + "\n")
    assert json.loads(GOLDEN.read_text())["plans"] == got
    m = got["META_ADS"]
    assert [r["step"] for r in m] == ["image", "campaign", "adset", "creative", "ad"]
    assert {m[1]["form"]["status"], m[2]["form"]["status"], m[4]["form"]["status"]} == {"PAUSED"}
    # slice 5 D5: CA$25 a day is one total split across the three platforms (8.34 / 8.33 / 8.33), never 25 each
    assert m[2]["form"]["daily_budget"] == "834" and json.loads(m[2]["form"]["targeting"])["geo_locations"]["custom_locations"][0]["radius"] == 20
    g = got["GOOGLE_ADS"][0]["json"]
    assert g["partialFailure"] is False and g["mutateOperations"][0]["campaignBudgetOperation"]["create"]["amountMicros"] == "8330000"
    statuses = [next(iter(o.values()))["create"].get("status") for o in g["mutateOperations"]]
    assert statuses.count("PAUSED") == 3
    t = got["TIKTOK_ADS"]
    assert [r["step"] for r in t] == ["identity", "image", "campaign", "adgroup", "ad"]
    assert t[3]["json"]["placements"] == ["PLACEMENT_PANGLE"] and t[3]["json"]["budget"] == 8.33
    assert t[2]["json"]["operation_status"] == t[3]["json"]["operation_status"] == t[4]["json"]["creatives"][0]["operation_status"] == "DISABLE"
    assert "qloo" not in json.dumps(got).lower()  # Qloo rationale is never sent as targeting


def test_missing_fields_and_blockers_are_named_never_defaulted(client, deliver_env, monkeypatch):
    pid, v = digital_packaged(client, deliver_env, monkeypatch)
    v = edit(client, pid, v, {"destinations": ["META_ADS", "GOOGLE_ADS", "TIKTOK_ADS"]})
    for dst in ("META_ADS", "GOOGLE_ADS", "TIKTOK_ADS"):
        fields = {m["field"] for m in dest(v, dst)["state"]["missing"]}
        assert "budget" in fields, dst  # never invented
    assert {"rights", "start"} <= {m["field"] for m in dest(v, "META_ADS")["state"]["missing"]}
    v = edit(client, pid, v, {"budget": {"amount": "10", "ccy": "USD"}, "start": "2026-11-02", "rights_confirmed": True})
    assert any("USD 20" in m["text"] for m in dest(v, "TIKTOK_ADS")["state"]["missing"])
    configure(monkeypatch)
    connect(pid, "META_ADS")  # the ad account bills in CAD
    blockers = dest(view(client, pid, v["id"]), "META_ADS")["plan"]["blockers"]
    assert any(b["text"].startswith("Budget in CAD") for b in blockers)  # never auto-converted
    assert client.patch(f"/api/projects/{pid}/packages/{v['id']}", json={"base_version": v["version"], "fields": {
        "budget": {"amount": "12.345", "ccy": "USD"}}}).status_code == 422


# ---- the §6.2a matrix: a write is never retried (P6-R1) ----
WRITES = {"EMAIL": ["message"], "META_ADS": ["image", "campaign", "adset", "creative", "ad"], "GOOGLE_ADS": ["mutate"],
          "TIKTOK_ADS": ["identity", "image", "campaign", "adgroup", "ad"]}
CASES = [(dst, step, after) for dst, steps in WRITES.items() for step in steps for after in (502, 504, "timeout")]


@pytest.mark.parametrize("dst,step,after", CASES[::3] + [("META_ADS", "adset", 504), ("TIKTOK_ADS", "ad", 502), ("EMAIL", "message", 502)])
def test_a_write_that_commits_then_fails_is_uncertain_once_and_never_resent(client, deliver_env, monkeypatch, provider, dst, step, after):
    pid, v = outreach_ready(client, deliver_env, monkeypatch) if dst == "EMAIL" else ads_ready(client, deliver_env, monkeypatch)
    provider.after_commit[step] = after
    r = approve_and_run(client, pid, v, dst)
    assert r.status_code == 200 and r.json()["status"] == "UNCERTAIN", r.json()
    assert provider.writes.count(step) == 1  # exactly one write
    later = WRITES[dst][WRITES[dst].index(step) + 1:]
    assert not set(later) & set(provider.writes)  # no later child write
    a = db.get_record(r.json()["id"])
    assert [s["status"] for s in a["steps"]][WRITES[dst].index(step)] == "UNKNOWN"
    assert set(a["ids"]) >= set(WRITES[dst][:WRITES[dst].index(step)])  # known ids kept
    d = dest(view(client, pid, v["id"]), dst)
    assert d["summary"] == "UNCERTAIN" and d["next"] == ["resolve_happened", "resolve_not_happened"]
    assert d["text"].startswith("We can't tell whether this was") and "Check" in d["text"]
    delivery.recover()  # a restart never redispatches
    assert provider.writes.count(step) == 1
    apv = d["state"]["approval_id"]
    again = client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/{dst}/execute", json={"approval_id": apv})
    assert again.status_code == 409 and provider.writes.count(step) == 1  # no execute or retry path


def test_the_write_client_has_retries_off():
    assert delivery._CLIENT._transport._pool._retries == 0


def test_definitive_rejection_is_failed_and_keeps_partial_resources(client, deliver_env, monkeypatch, provider):
    pid, v = ads_ready(client, deliver_env, monkeypatch)
    provider.reject["adset"] = (400, {"error": {"message": "Invalid parameter", "code": 100, "error_user_msg": "Budget too low"}})
    a = db.get_record(approve_and_run(client, pid, v, "META_ADS").json()["id"])
    assert a["status"] == "FAILED" and "Budget too low" in a["error"] and a["partial"] == ["image", "campaign"]
    assert a["ids"]["campaign"] == "campaign-1" and a["verification"]["objects"]["campaign"]["status"] == "PAUSED"
    assert "creative" not in provider.writes
    d = dest(view(client, pid, v["id"]), "META_ADS")
    assert d["summary"] == "PARTLY_CREATED" and d["text"].startswith("Partly created · Campaign ✓")  # v4 B13 (was FAILED, P1-4)
    provider.reject["mutate"] = (400, {"error": {"code": 400, "details": [{"@type": "type.googleapis.com/google.ads.googleads.v26.errors.GoogleAdsFailure",
                                                                           "errors": [{"message": "Keyword text is too long"}]}]}})
    assert db.get_record(approve_and_run(client, pid, v, "GOOGLE_ADS").json()["id"])["status"] == "FAILED"
    provider.reject["campaign"] = (200, {"code": 40100, "message": "Too many requests", "request_id": "r"})  # throttling: UNCERTAIN
    assert db.get_record(approve_and_run(client, pid, v, "TIKTOK_ADS").json()["id"])["status"] == "UNCERTAIN"


@pytest.mark.parametrize("dst,key,bad", [("META_ADS", "meta", "ACTIVE"), ("GOOGLE_ADS", "google", "ENABLED"), ("TIKTOK_ADS", "tiktok", "ENABLE")])
def test_a_read_back_that_isnt_paused_is_repaused_and_failed_verification(client, deliver_env, monkeypatch, provider, dst, key, bad):
    pid, v = ads_ready(client, deliver_env, monkeypatch)
    provider.status[key] = bad
    a = db.get_record(approve_and_run(client, pid, v, dst).json()["id"])
    assert a["status"] == "FAILED_VERIFICATION" and "repause" in provider.writes and a["verification"]["repaused"]
    d = dest(view(client, pid, v["id"]), dst)
    assert d["summary"] == "FAILED_VERIFICATION" and d["text"].startswith("Created, but not confirmed paused: Landfall paused it again.")


def test_success_sends_once_reads_back_and_never_twice(client, deliver_env, monkeypatch, provider):
    pid, v = outreach_ready(client, deliver_env, monkeypatch)
    a = db.get_record(approve_and_run(client, pid, v, "EMAIL").json()["id"])
    assert a["status"] == "SUCCEEDED" and a["result"]["message_id"] == "msg1" and a["result"]["thread_id"] == "thr1"
    assert dest(view(client, pid, v["id"]), "EMAIL")["summary"] == "SENT"
    assert approve_and_run(client, pid, view(client, pid, v["id"]), "EMAIL").status_code == 409 and provider.writes == ["message"]


def test_ad_platforms_create_paused_and_read_back(client, deliver_env, monkeypatch, provider):
    pid, v = ads_ready(client, deliver_env, monkeypatch)
    for dst in ("META_ADS", "GOOGLE_ADS", "TIKTOK_ADS"):
        a = db.get_record(approve_and_run(client, pid, v, dst).json()["id"])
        assert a["status"] == "SUCCEEDED" and a["verification"]["verified"], (dst, a)
        assert dest(view(client, pid, v["id"]), dst)["summary"] == "CREATED_PAUSED"
    rids = [r["json"]["request_id"] for s, r in provider.calls if s in ("campaign", "adgroup") and "tiktok" in r["url"]]
    assert len(set(rids)) == 2 and all(isinstance(x, int) and x < 2 ** 63 for x in rids)


def test_duplicate_execute_is_refused_by_the_atomic_claim(client, deliver_env, monkeypatch, provider):
    pid, v = outreach_ready(client, deliver_env, monkeypatch)
    apv = client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/EMAIL/approve", json={"base_version": v["version"]}).json()
    db.x("INSERT INTO approval_claims VALUES(?,?,?)", apv["approval_id"], "exe_other", db.now())  # a concurrent click won
    r = client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/EMAIL/execute", json={"approval_id": apv["approval_id"]})
    assert r.status_code == 409 and provider.writes == []


def test_bcc_is_shown_approved_and_projected_from_the_final_mime(client, deliver_env, monkeypatch, provider):
    pid, v = outreach_ready(client, deliver_env, monkeypatch)
    v = edit(client, pid, v, {"bcc": ["founder@tidewell.example"]})
    vals = {x["field"]: x["value"] for x in dest(v, "EMAIL")["plan"]["values"]}
    assert vals["Bcc"] == ["founder@tidewell.example"]
    assert db.get_record(approve_and_run(client, pid, v, "EMAIL").json()["id"])["status"] == "SUCCEEDED"
    pkg = db.get_record(v["id"])
    plan = delivery.provider_plan(pid, pkg, "EMAIL")
    plan["recipients"] = ["someone@else.example"]
    assert delivery.adapters()["EMAIL"].precheck(plan) == "The recipients in the message differ from the ones you approved"


def test_off_the_allow_list_is_refused_before_any_write(client, deliver_env, monkeypatch, provider):
    pid, v = outreach_ready(client, deliver_env, monkeypatch)
    monkeypatch.setitem(config._ENV, "LANDFALL_DELIVERY_ALLOWLIST", "founder@tidewell.example")
    r = approve_and_run(client, pid, v, "EMAIL").json()
    assert r["status"] == "REFUSED" and "hello@kettlecrane.example" in r["error"] and provider.calls == []


def test_an_expired_token_asks_to_reconnect_and_keeps_the_package_and_approval(client, deliver_env, monkeypatch, provider):
    pid, v = outreach_ready(client, deliver_env, monkeypatch)
    delivery.put_token(delivery.conn_id(pid, "EMAIL"), {"access_token": "tok", "refresh_token": "ref", "expires_at": time.time() - 10})
    monkeypatch.setattr(delivery, "SEND", lambda req, headers: (400, {"error": "invalid_grant"}))
    a = db.get_record(approve_and_run(client, pid, v, "EMAIL").json()["id"])
    assert a["status"] == "FAILED" and "Reconnect" in a["error"]
    d = dest(view(client, pid, v["id"]), "EMAIL")
    assert d["state"]["connection"] == "EXPIRED"
    # Reconnect as the same identity: a new approval of the unchanged package executes; another identity doesn't match
    connect(pid, "EMAIL")
    monkeypatch.setattr(delivery, "SEND", provider)
    assert approve_and_run(client, pid, view(client, pid, v["id"]), "EMAIL").json()["status"] == "SUCCEEDED"


def test_a_new_identity_after_approval_is_refused(client, deliver_env, monkeypatch, provider):
    pid, v = ads_ready(client, deliver_env, monkeypatch)
    apv = client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/META_ADS/approve", json={"base_version": v["version"]}).json()
    connect(pid, "META_ADS", {**USERS["META_ADS"][1], "id": "act_999"})
    r = client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/META_ADS/execute", json={"approval_id": apv["approval_id"]})
    assert r.status_code == 409 and "Approve it again" in r.json()["detail"] and provider.writes == []


def test_demo_mode_is_simulated_and_never_calls_a_provider(client, deliver_env, monkeypatch, provider):
    pid, v = packaged(client, deliver_env)  # REPLAY_CASES is set: replayed upstream
    v = edit(client, pid, v, {"from_name": "Maya", "segments": [s["text"] for s in v["copy"]["segments"][:2]]})
    assert client.post(f"/api/projects/{pid}/connections/EMAIL/start", json={"package_id": v["id"]}).json()["simulated"]
    a = db.get_record(approve_and_run(client, pid, view(client, pid, v["id"]), "EMAIL").json()["id"])
    assert a["mode"] == "SIMULATED" and a["status"] == "SUCCEEDED" and provider.calls == []
    assert "SIMULATED" in dest(view(client, pid, v["id"]), "EMAIL")["text"]


# ---- summary precedence (P6-R2) ----
def test_uncertain_outranks_capability_survives_restart_and_disconnect(client, deliver_env, monkeypatch, provider):
    pid, v = outreach_ready(client, deliver_env, monkeypatch)
    provider.after_commit["message"] = "timeout"
    aid = approve_and_run(client, pid, v, "EMAIL").json()["id"]
    assert dest(view(client, pid, v["id"]), "EMAIL")["summary"] == "UNCERTAIN"  # refresh
    delivery.recover()  # restart
    client.delete(f"/api/projects/{pid}/connections/EMAIL")  # a later disconnect is a capability line, not the result
    d = dest(view(client, pid, v["id"]), "EMAIL")
    assert d["summary"] == "UNCERTAIN" and d["state"]["connection"] == "NOT_CONNECTED"
    client.post(f"/api/projects/{pid}/executions/{aid}/resolve", json={"happened": True, "provider_id": "msg-seen"})
    d = dest(view(client, pid, v["id"]), "EMAIL")
    assert d["summary"] == "UNCERTAIN" and d["manual"] == "MARKED_SENT_BY_YOU"  # a ManualRecord never converts it


def test_it_didnt_happen_needs_a_new_approval(client, deliver_env, monkeypatch, provider):
    pid, v = outreach_ready(client, deliver_env, monkeypatch)
    provider.after_commit["message"] = 502
    r = approve_and_run(client, pid, v, "EMAIL").json()
    old = dest(view(client, pid, v["id"]), "EMAIL")["state"]["approval_id"]
    assert client.post(f"/api/projects/{pid}/executions/{r['id']}/resolve", json={"happened": False}).json()["status"] == "NOT_EXECUTED_BY_FOUNDER"
    assert client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/EMAIL/execute", json={"approval_id": old}).status_code == 409
    provider.after_commit.clear()
    assert approve_and_run(client, pid, view(client, pid, v["id"]), "EMAIL").json()["status"] == "SUCCEEDED"


def test_an_interrupted_write_is_uncertain_on_restart(client, deliver_env, monkeypatch, provider):
    pid, v = outreach_ready(client, deliver_env, monkeypatch)
    apv = client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/EMAIL/approve", json={"base_version": v["version"]}).json()

    def die(req, headers):
        provider.writes.append("message")
        raise SystemExit("the process stopped mid-send")
    monkeypatch.setattr(delivery, "SEND", die)
    with pytest.raises(SystemExit):
        delivery.execute(pid, v["id"], "EMAIL", apv["approval_id"])
    assert delivery.recover() == 1
    a = next(x for x in db.records("execution", pid) if x["version"] == max(y["version"] for y in db.records("execution", pid)))
    assert a["status"] == "UNCERTAIN" and a["steps"][0]["status"] == "UNKNOWN" and provider.writes == ["message"]


def test_manual_records_are_never_provider_confirmed(client, deliver_env, monkeypatch):
    pid, v = packaged(client, deliver_env)
    monkeypatch.setattr(config, "REPLAY_CASES", [])
    client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/EMAIL/manual", json={"date": "2026-10-27"})
    d = dest(view(client, pid, v["id"]), "EMAIL")
    assert d["manual"] == "MARKED_SENT_BY_YOU" and d["summary"] == "UNAVAILABLE_IN_THIS_DEPLOYMENT" and d["state"]["execution"] == "NONE"


def test_an_uncited_fact_is_flagged_a_save_never_launders_it_and_keeping_it_is_yours(client, deliver_env):
    deliver_env.copy = {**deliver_env.copy, "subject": "Kettle & Crane carries small-maker teas",
                        "paragraphs": [{"text": "Kettle & Crane carries teas from small Ontario makers."},  # judged a fact, no cites
                                       {"text": "Would you try four of our canned teas on that shelf for a month?"}]}
    pid, v = packaged(client, deliver_env)
    seg = v["copy"]["segments"][0]
    assert seg["state"] == "TO_CHECK" and seg["why"] == "states a fact without naming its evidence"
    assert v["copy"]["subject"]["state"] == "TO_CHECK"
    v = edit(client, pid, v, {"subject": v["copy"]["subject"]["text"], "from_name": "Maya"})  # resent unchanged
    assert v["copy"]["subject"]["state"] == "TO_CHECK"  # a save never clears a flag
    v = edit(client, pid, v, {"confirm": ["subject", "paragraphs[0]"]})
    assert v["copy"]["subject"]["state"] == v["copy"]["segments"][0]["state"] == "YOU_SAID"
    assert dest(v, "EMAIL")["state"]["package"] == "VALID"


# ---- REVIEW-SLICE-06 repairs (P6-I1 to I7): the reviewer's counterexamples, now asserting the repaired behaviour ----
import threading  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402


def test_i1_two_approvals_racing_send_exactly_once_and_restart_keeps_it(client, deliver_env, monkeypatch, provider):
    pid, v = outreach_ready(client, deliver_env, monkeypatch)
    a1 = delivery.approve(pid, v["id"], "EMAIL", v["version"])["approval_id"]
    a2 = delivery.approve(pid, v["id"], "EMAIL", v["version"])["approval_id"]
    barrier, old = threading.Barrier(2), delivery.approval_state

    def checked(*args):
        r = old(*args)
        barrier.wait(timeout=10)  # both requests pass every check before either claims anything
        return r
    monkeypatch.setattr(delivery, "approval_state", checked)

    def go(a):
        try:
            return delivery.execute(pid, v["id"], "EMAIL", a)["status"]
        except delivery.Refused as e:
            return f"REFUSED {e.code}"
    with ThreadPoolExecutor(2) as pool:
        got = sorted(pool.map(go, [a1, a2]))
    assert got == ["REFUSED 409", "SUCCEEDED"] and provider.writes == ["message"]
    monkeypatch.setattr(delivery, "approval_state", old)
    delivery.recover()
    a3 = delivery.approve(pid, v["id"], "EMAIL", v["version"])["approval_id"]
    with pytest.raises(delivery.Refused):
        delivery.execute(pid, v["id"], "EMAIL", a3)
    assert provider.writes == ["message"]


class OAuth:
    """SYNTHETIC answers for each ad platform's real connect and select code (token exchange, account reads)."""

    def __init__(self, currency="CAD", pages=({"id": "pg1", "name": "Tidewell"},), region="Toronto"):
        self.currency, self.pages, self.region = currency, list(pages), region

    def __call__(self, req, headers):
        u = req["url"]
        if "oauth/access_token" in u:
            return 200, {"access_token": "tok", "expires_in": 5_000_000}
        if u.endswith("/me"):
            return 200, {"id": "u1", "name": "Founder"}
        if u.endswith("/me/adaccounts"):
            return 200, {"data": [{"account_id": "123", "name": "Tidewell ads", "currency": self.currency, "timezone_name": "America/Toronto"}]}
        if u.endswith("/me/accounts"):
            return 200, {"data": self.pages}
        if u.endswith("/token"):
            return 200, {"access_token": "tok", "refresh_token": "r", "expires_in": 3600, "scope": "openid email https://www.googleapis.com/auth/adwords https://www.googleapis.com/auth/gmail.send"}
        if "userinfo" in u:
            return 200, {"email": "founder@tidewell.example"}
        if u.endswith("customers:listAccessibleCustomers"):
            return 200, {"resourceNames": ["customers/1234567890"]}
        if u.endswith("googleAds:search"):
            return 200, {"results": [{"customer": {"id": "1234567890", "descriptiveName": "Tidewell", **({"currencyCode": self.currency} if self.currency else {})}}]}
        if u.endswith("/oauth2/access_token/"):
            return 200, {"code": 0, "data": {"access_token": "tok", "advertiser_ids": ["7001"]}}
        if u.endswith("/oauth2/advertiser/get/"):
            return 200, {"code": 0, "data": {"list": [{"advertiser_id": "7001", "advertiser_name": "Tidewell"}]}}
        if u.endswith("/advertiser/info/"):
            return 200, {"code": 0, "data": {"list": [{"currency": self.currency, "timezone": "America/Toronto"}]}}
        if u.endswith("/tool/region/"):
            return 200, {"code": 0, "data": {"region_info": [{"location_id": 6167865, "name": self.region, "region_level": "CITY"}]}}
        return 404, {}


def callback(client, pid, pkg_id, dst):
    url = client.post(f"/api/projects/{pid}/connections/{dst}/start", json={"package_id": pkg_id}).json()["url"]
    state = re.search(r"state=([^&]+)", url).group(1)
    r = client.get(f"/api/connections/callback?state={state}&code=c&auth_code=c", follow_redirects=False)
    assert r.status_code == 303, r.text


def test_i2_authorization_only_discovers_accounts_and_selection_reads_what_execution_needs(client, deliver_env, monkeypatch):
    pid, v = digital_packaged(client, deliver_env, monkeypatch)
    v = edit(client, pid, v, {"destinations": ["META_ADS", "GOOGLE_ADS", "TIKTOK_ADS"], "budget": {"amount": "25", "ccy": "CAD"},
                              "start": "2026-11-02", "radius_km": 20, "rights_confirmed": True})
    configure(monkeypatch)
    fake = OAuth()
    monkeypatch.setattr(delivery, "SEND", fake)
    monkeypatch.setitem(config._ENV, "LANDFALL_GOOGLE_ADS_ACCESS", "EXPLORER")  # discovery and choice; 6.7's TEST lock is test_slice67
    for dst in ("META_ADS", "GOOGLE_ADS", "TIKTOK_ADS"):
        callback(client, pid, v["id"], dst)  # a single account: still not executable until chosen
        d = dest(view(client, pid, v["id"]), dst)
        assert d["next"] == ["choose_account"] and delivery.conn_for(pid, dst)["account_identity"] is None, dst
    put = lambda dst, **b: client.put(f"/api/projects/{pid}/connections/{dst}/account", json={"city_id": "toronto", **b})  # noqa: E731
    assert put("META_ADS", account_id="act_123").status_code == 422  # no Page chosen
    assert put("META_ADS", account_id="act_123", page_id="pg1").status_code == 200
    fake.currency = None
    assert put("GOOGLE_ADS", account_id="1234567890").status_code == 422  # currency not read: never assumed
    fake.currency = "CAD"
    assert put("GOOGLE_ADS", account_id="1234567890").status_code == 200
    fake.region = "Toronto Islands"
    assert put("TIKTOK_ADS", account_id="7001").status_code == 200
    v = view(client, pid, v["id"])
    assert any("no location named Toronto" in b["text"] for b in dest(v, "TIKTOK_ADS")["plan"]["blockers"])
    fake.region = "Toronto"
    assert put("TIKTOK_ADS", account_id="7001").status_code == 200  # the account can be chosen again from the card
    v = view(client, pid, v["id"])
    for dst in ("META_ADS", "GOOGLE_ADS", "TIKTOK_ADS"):
        d = dest(v, dst)
        assert d["plan"]["blockers"] == [] and d["next"] == ["approve"], (dst, d["plan"]["blockers"])
    meta = delivery.provider_plan(pid, db.get_record(v["id"]), "META_ADS")
    assert json.loads(meta["requests"][3]["form"]["object_story_spec"])["page_id"] == "pg1"


def test_i3_expiry_offers_reconnect_then_a_new_approval_then_one_send(client, deliver_env, monkeypatch, provider):
    pid, v = outreach_ready(client, deliver_env, monkeypatch)
    provider.reject["message"] = (401, {"error": {"message": "expired"}})
    approve_and_run(client, pid, v, "EMAIL")
    d = dest(view(client, pid, v["id"]), "EMAIL")
    assert d["summary"] == "FAILED" and d["next"] == ["connect"] and d["state"]["connection"] == "EXPIRED"
    assert d["state"]["approval"] == "CONSUMED"
    connect(pid, "EMAIL")  # Reconnect as the same identity
    d = dest(view(client, pid, v["id"]), "EMAIL")
    assert d["summary"] == "FAILED" and d["next"] == ["approve"]  # the result stays; its recovery is offered
    provider.reject.clear()
    assert approve_and_run(client, pid, view(client, pid, v["id"]), "EMAIL").json()["status"] == "SUCCEEDED"
    assert provider.writes == ["message"]


def test_i3_it_didnt_happen_offers_a_new_approval_not_the_used_one(client, deliver_env, monkeypatch, provider):
    pid, v = outreach_ready(client, deliver_env, monkeypatch)
    provider.after_commit["message"] = "timeout"
    aid = approve_and_run(client, pid, v, "EMAIL").json()["id"]
    client.post(f"/api/projects/{pid}/executions/{aid}/resolve", json={"happened": False})
    d = dest(view(client, pid, v["id"]), "EMAIL")
    assert d["summary"] == "READY_TO_APPROVE" and d["next"] == ["approve"] and d["state"]["approval"] == "CONSUMED"
    provider.after_commit.clear()
    assert approve_and_run(client, pid, view(client, pid, v["id"]), "EMAIL").json()["status"] == "SUCCEEDED"
    assert provider.writes == ["message", "message"]  # the founder said the first didn't happen: one new attempt


@pytest.mark.parametrize("dst,key,stop_at", [("GOOGLE_ADS", "google", "repause"), ("META_ADS", "meta", "repause"),
                                             ("TIKTOK_ADS", "tiktok", "repause"), ("GOOGLE_ADS", "google", "read")])
def test_i4_ids_survive_a_restart_during_read_back_or_re_pause(client, deliver_env, monkeypatch, dst, key, stop_at):
    pid, v = ads_ready(client, deliver_env, monkeypatch)
    p = Provider()
    p.status[key] = "ENABLED" if key == "google" else "ACTIVE" if key == "meta" else "ENABLE"

    def die(req, headers):
        if p.step_of(req) == stop_at:
            p.writes.append(stop_at)
            raise SystemExit(f"the process stopped during {stop_at}")
        return p(req, headers)
    monkeypatch.setattr(delivery, "SEND", die)
    apv = delivery.approve(pid, v["id"], dst, v["version"])["approval_id"]
    with pytest.raises(SystemExit):
        delivery.execute(pid, v["id"], dst, apv)
    delivery.recover()
    a = delivery.latest_actions(pid, v["id"], dst)[-1]
    assert a["status"] == "UNCERTAIN" and "Created" in a["error"]
    assert set(a["ids"]) >= set(WRITES[dst]) and all(s["status"] == "CONFIRMED" for s in a["steps"])
    if stop_at == "repause":
        assert a["safety"][0]["status"] == "UNKNOWN"
    writes = list(p.writes)
    with pytest.raises(delivery.Refused):  # never redispatched: neither the create nor the safety write
        delivery.execute(pid, v["id"], dst, delivery.approve(pid, v["id"], dst, v["version"])["approval_id"])
    assert p.writes == writes


def test_i5_edited_copy_and_dates_are_held_to_the_destination_limits(client, deliver_env, monkeypatch):
    pid, v = ads_ready(client, deliver_env, monkeypatch)
    r = client.patch(f"/api/projects/{pid}/packages/{v['id']}", json={"base_version": v["version"], "fields": {"start": "2026-99-99"}})
    assert r.status_code == 422
    r = client.patch(f"/api/projects/{pid}/packages/{v['id']}", json={"base_version": v["version"], "fields": {"end": "2026-11-01"}})
    assert r.status_code == 422 and "before the start" in r.json()["detail"]
    v = edit(client, pid, v, {"headlines": ["A" * 31, "Second headline", "Third headline"],
                              "descriptions": ["B" * 91, "Second description"], "ad_text": "C" * 101})
    g, t = (dest(v, x)["state"]["missing"] for x in ("GOOGLE_ADS", "TIKTOK_ADS"))
    assert any("31 characters" in m["text"] for m in g) and any("91 characters" in m["text"] for m in g)
    assert any("101 characters" in m["text"] for m in t)
    for dst in ("GOOGLE_ADS", "TIKTOK_ADS"):
        assert client.post(f"/api/projects/{pid}/packages/{v['id']}/destinations/{dst}/approve",
                           json={"base_version": v["version"]}).status_code == 422
    assert client.get(f"/api/projects/{pid}/packages/{v['id']}/exports/campaign_zip").status_code == 200  # still usable


def test_i6_an_exported_version_keeps_its_bytes_whatever_the_connection_or_configuration(client, deliver_env, monkeypatch):
    o_pid, o = packaged(client, deliver_env)
    o = edit(client, o_pid, o, {"from_name": "Maya at Tidewell", "segments": [s["text"] for s in o["copy"]["segments"][:2]]})
    pid, v = ads_ready(client, deliver_env, monkeypatch)
    connect(o_pid, "EMAIL")
    grab = lambda p, pkg, fmt: delivery.export_bytes(p, db.get_record(pkg), fmt)[0]  # noqa: E731
    before = {f: grab(o_pid, o["id"], f) for f in ("eml", "outreach_zip")} | {f: grab(pid, v["id"], f) for f in ("campaign_zip", "json")}
    for dst in ("EMAIL",):
        delivery.disconnect(o_pid, dst)
    for dst in ("META_ADS", "GOOGLE_ADS"):
        delivery.disconnect(pid, dst)
    USERS_EMAIL = USERS["EMAIL"]
    monkeypatch.setitem(USERS, "EMAIL", ({"email": "someone.else@tidewell.example"}, None))
    connect(o_pid, "EMAIL")
    connect(pid, "TIKTOK_ADS", {**USERS["TIKTOK_ADS"][1], "id": "7002", "currency": "USD"})
    monkeypatch.setitem(config._ENV, "meta_graph_version", "v99.0")
    after = {f: grab(o_pid, o["id"], f) for f in ("eml", "outreach_zip")} | {f: grab(pid, v["id"], f) for f in ("campaign_zip", "json")}
    assert before == after and b"From:" not in before["eml"]
    monkeypatch.setitem(USERS, "EMAIL", USERS_EMAIL)


def test_i7_each_sentence_is_judged_by_its_own_citations_and_the_guards(monkeypatch):
    ev = [{"id": "E1", "role": "BRAND_FACT", "text": "The brand is Tidewell", "claim_id": "brand"},
          {"id": "E2", "role": "PRODUCT", "text": "Hibiscus tea made from hibiscus", "claim_id": "product-1"},
          {"id": "E3", "role": "PRODUCT", "text": "Cotton shirts", "claim_id": "product-2"}]
    sub = {"headlines": [{"text": "Hibiscus tea", "cites": ["E1"]}, {"text": "Hibiscus tea", "cites": ["E2"]},
                         {"text": "Premium Cotton Shirts", "cites": ["E3"]}, {"text": "Tidewell Toronto", "cites": ["E1"]}]}
    c = delivery.segments_from(sub, ev, "digital", [])
    seen = []

    def judge(ctx, listing):  # a judge that believes everything: only the plumbing and the guards can hold the line
        seen.append(listing)
        return {"verdicts": [{"id": i, "kind": "SUPPORTED_FACT" if "Hibiscus tea made" in line or "Cotton" in line else "UNSUPPORTED_FACT"}
                             for i, line in re.findall(r"- id=(s\d+) \|(.*)", listing)]}
    monkeypatch.setattr(delivery, "judge_copy", judge)
    delivery.check_segments(None, c, ev, [], city="Toronto")
    first = re.search(r"- id=s1 \|.*", seen[0]).group(0)
    assert "Hibiscus tea made" not in first  # the wrongly cited sentence never sees the uncited product
    states = [(h["state"], h.get("why")) for h in c["headlines"]]
    assert states[0][0] == "TO_CHECK" and states[1] == ("SUPPORTED", None)
    assert states[2] == ("TO_CHECK", "\"Premium\" is a quality claim its evidence doesn't make")
    assert states[3] == ("TO_CHECK", "reads as a local presence its evidence doesn't show")


def test_a_failed_sign_in_returns_to_the_package_with_the_reason(client, deliver_env, monkeypatch):
    pid, v = packaged(client, deliver_env)
    configure(monkeypatch)
    monkeypatch.setattr(delivery, "SEND", lambda req, headers: (400, {"error": "invalid_grant"}))
    url = client.post(f"/api/projects/{pid}/connections/EMAIL/start", json={"package_id": v["id"]}).json()["url"]
    r = client.get(f"/api/connections/callback?state={re.search(r'state=([^&]+)', url).group(1)}&code=c", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(f"/p/{pid}/deliver/{v['id']}?connect_error=")
    assert delivery.conn_for(pid, "EMAIL") is None


def test_i7_idioms_are_not_quality_claims():
    g = lambda t: delivery.guard(t, [], "Tidewell", "Toronto")  # noqa: E731
    assert g("We'd love your thoughts on the best way to approach this collaboration.") is None
    assert g("Tell us how best to work with your group.") is None and g("Which dates work best for your group?") is None
    assert g("Best canned tea in town.") == "\"Best\" is a quality claim its evidence doesn't make"
    assert g("Our best seller, now here.") == "\"best\" is a quality claim its evidence doesn't make"
    assert g("The best canned tea in Canada.") == "\"best\" is a quality claim its evidence doesn't make"
    assert g("Our Toronto store opens soon.") == "reads as a local presence its evidence doesn't show"
    for t in ("Elevate your wardrobe with Toronto-inspired designs.", "Shirts designed for the modern Toronto man.", "Made in Toronto."):
        assert g(t) == "reads as a local presence its evidence doesn't show", t
    assert g("Join the Toronto waitlist today.") is None  # an invitation, not a claim of origin or presence
    assert g("Which dates suit you? Are you based in Toronto? Is our Toronto store near you?") is None  # questions claim nothing
    assert g("Are you free in May? Our Toronto store opens soon.") == "reads as a local presence its evidence doesn't show"


def test_gift_cards_are_not_attached_as_product_images(client, deliver_env):
    pid, dr = drafted(client, deliver_env)
    gift = db.put_record("product", pid, {"title": "E-GIFT CARD", "product_url": PRODUCT, "snapshot_id": None, "price_text": None,
                                          "category_terms": [], "materials": [], "discovered_via": "BRAND_PAGES", "mcp": None, "vc": "F"})
    draft = db.get_record(dr["id"])
    db.put_record("draft", pid, {**{k: v for k, v in draft.items() if k not in ("id", "version", "kind", "project_id", "created_at", "inputs")},
                                 "products": [{"id": gift["id"], "version": gift["version"], "title": "E-GIFT CARD"}, *draft["products"]]},
                  rid=dr["id"])
    dr = client.get(f"/api/projects/{pid}/drafts/{dr['id']}").json()
    client.post(f"/api/projects/{pid}/drafts/{dr['id']}/package", json={"base_version": dr["version"]})
    idle(pid)
    v = view(client, pid, f"pkg_{dr['id'][4:]}")
    assert [a["outcome"] for a in v["asset_attempts"]] == ["SKIPPED", "OK"] and len(v["assets"]) == 1
