"""Meta Ads: OUTCOME_TRAFFIC, one campaign, ad set and single-image link ad from a Page, every object PAUSED.

Five writes, each id saved before the next (P45-R2): /adimages → /campaigns → /adsets → /adcreatives → /ads. Read-back
of `status` and `effective_status` on all three delivering objects; ACTIVE anywhere → re-pause, FAILED_VERIFICATION.
Location is `custom_locations` (city point + radius), so no geo-key lookup is needed. Unverified: whether a paused ad
is reviewed before activation, and the Ads Manager link format."""
import json
import time
from urllib.parse import urlencode

from .. import config
from . import Adapter, V, account_blockers, daily, need, reference, timing

TRANSIENT = {1, 2, 4, 17, 613, 80004}  # unknown and throttling codes: never proof that nothing was created


def graph() -> str:
    return f"https://graph.facebook.com/{version()}"


def version() -> str:
    return config.env("meta_graph_version") or "v25.0"  # v24.0 retires 10 Nov 2026


SCOPE = "ads_management,ads_read,pages_show_list,pages_read_engagement,pages_manage_ads"
SANDBOX_LABEL = "Landfall's Meta sandbox"


def sandbox() -> bool:
    return (config.env("LANDFALL_META_ACCESS") or "").upper() == "SANDBOX"


def sandbox_account() -> str:
    a = (config.env("meta_ad_account_id") or "").strip()
    return a if a.startswith("act_") or not a else f"act_{a}"


class Meta(Adapter):
    DEST, PROVIDER, LABEL, KIND = "META_ADS", "meta_ads", "Meta Ads", "META_CREATE_PAUSED_CAMPAIGN"
    CONFIGURATION = "Traffic: one campaign, ad set (city + radius, daily budget) and single-image link ad, all paused"
    WHERE, VALIDATES = "Meta Ads Manager", True
    CONFIG = ("meta_app_id", "meta_app_secret")
    SIMULATED_IDENTITY = {"user_id": "demo", "ad_account_id": "act_demo", "page_id": "demo-page"}

    def missing_config(self) -> list[str]:
        return [n for n in self.CONFIG + (("meta_ad_account_id",) if sandbox() else ()) if not config.env(n)]

    def access(self) -> dict:
        s = (config.env("LANDFALL_META_ACCESS") or "APP_ROLES").upper()
        if s == "SANDBOX":
            return {"state": "SANDBOX", "connectable": True,
                    "note": "Sandbox ad account: never delivers and has no payment method; only app roles can connect"}
        if s == "ADVANCED":
            return {"state": "APPROVED", "connectable": True, "note": "Advanced Access granted"}
        return {"state": "PENDING" if s == "PENDING" else "REQUIRED", "connectable": True,
                "note": "Only people with a role on Landfall's Meta app can connect; others need Business Verification and Advanced Access"}

    def auth_url(self, state: str, redirect: str) -> str:
        q = {"client_id": config.env("meta_app_id"), "redirect_uri": redirect, "state": state, "response_type": "code"}
        cfg = config.env("meta_login_config_id")  # Facebook Login for Business is optional for the owner's own account
        q.update({"config_id": cfg, "override_default_response_type": "true"} if cfg else {"scope": SCOPE})
        return f"https://www.facebook.com/{version()}/dialog/oauth?" + urlencode(q)

    def connect(self, params: dict, redirect: str) -> dict:
        from ..delivery import read
        app = {"client_id": config.env("meta_app_id"), "client_secret": config.env("meta_app_secret")}
        st, short = read({"method": "GET", "url": f"{graph()}/oauth/access_token",
                          "params": {**app, "redirect_uri": redirect, "code": params.get("code", "")}}, {}, tries=1)
        if st != 200 or "access_token" not in short:
            raise ValueError(f"Meta didn't issue a token (HTTP {st})")
        st, long = read({"method": "GET", "url": f"{graph()}/oauth/access_token", "params": {
            **app, "grant_type": "fb_exchange_token", "fb_exchange_token": short["access_token"]}}, {})
        tok = long if st == 200 and "access_token" in long else short
        token = {"access_token": tok["access_token"], "expires_at": time.time() + int(tok.get("expires_in") or 3600)}
        h = {"Authorization": f"Bearer {token['access_token']}"}
        _, me = read({"method": "GET", "url": f"{graph()}/me", "params": {"fields": "id,name"}}, h)
        _, acts = read({"method": "GET", "url": f"{graph()}/me/adaccounts",
                        "params": {"fields": "account_id,name,account_status,currency,timezone_name"}}, h)
        _, pages = read({"method": "GET", "url": f"{graph()}/me/accounts", "params": {"fields": "id,name"}}, h)
        accounts = [{"id": f"act_{a['account_id']}", "name": a.get("name") or a["account_id"], "currency": a.get("currency"),
                     "timezone": a.get("timezone_name"), "account_status": a.get("account_status")}
                    for a in (acts or {}).get("data", [])]
        if sandbox():  # M2: the configured sandbox account is the only choice, read directly if the list omits it
            accounts = [a for a in accounts if a["id"] == sandbox_account()]
            if not accounts:
                st, a = read({"method": "GET", "url": f"{graph()}/{sandbox_account()}",
                              "params": {"fields": "account_id,name,account_status,currency,timezone_name"}}, h)
                if st == 200 and isinstance(a, dict) and a.get("account_id"):
                    accounts = [{"id": f"act_{a['account_id']}", "name": a.get("name") or a["account_id"], "currency": a.get("currency"),
                                 "timezone": a.get("timezone_name"), "account_status": a.get("account_status")}]
            accounts = [{**a, "name": SANDBOX_LABEL} for a in accounts]
        return {"token": token, "user": {"id": me.get("id"), "name": me.get("name"),
                                         "pages": [{"id": p["id"], "name": p.get("name")} for p in (pages or {}).get("data", [])]},
                "accounts": accounts, "scopes": ["ads_management", "pages_show_list", "pages_read_engagement", "pages_manage_ads",
                                                 "business_management"]}

    def select(self, acc: dict, extra: dict, conn: dict, token: dict | None) -> dict:
        page = next((p for p in conn["user"].get("pages", []) if p["id"] == extra.get("page_id")), None)
        if not page:
            from ..delivery import Refused
            raise Refused(422, "Choose the Facebook Page the ad runs from.")
        if sandbox() and acc["id"] != sandbox_account():
            from ..delivery import Refused
            raise Refused(422, "Only this deployment's sandbox ad account can be used.")
        return {**acc, "page_id": page["id"], "page_name": page["name"], "sandbox": sandbox()}

    def identity(self, user: dict, sel: dict | None) -> dict:
        return {"user_id": user["id"], "ad_account_id": (sel or {}).get("id"), "page_id": (sel or {}).get("page_id"),
                "environment": "SANDBOX" if (sel or {}).get("sandbox") else "PRODUCTION"}

    def label_of(self, user, sel) -> str:
        if sandbox():
            return SANDBOX_LABEL
        return f"{sel['name']} · Page {sel.get('page_name')}" if sel and sel.get("page_id") else user.get("name") or "Meta"

    def auth_headers(self, token: dict, conn: dict) -> dict:
        return {"Authorization": f"Bearer {token['access_token']}"}

    def required(self, pkg: dict) -> list[dict]:
        out: list = []
        c, cr = pkg["copy"], pkg["creative"]
        creatives = [a for a in pkg["assets"] if a["role"] == "CREATIVE"]
        need(out, creatives, "assets", "Add an image for the ad (upload one if your site's couldn't be saved)")
        need(out, not creatives or creatives[0]["artifact"]["sha256"] in pkg.get("rights_confirmed", []), "rights",
             "Confirm you hold the rights to the ad image")
        need(out, (c.get("primary_text") or {}).get("text"), "primary_text", "Write the primary text")
        need(out, c.get("headlines"), "headlines", "Write a headline")
        need(out, cr.get("cta"), "cta", "Choose a call to action")
        need(out, cr.get("landing_url"), "landing_url", "Give the landing page")
        need(out, daily(pkg, self.DEST), "budget", "Enter a daily budget, or set one in your plan's digital scenario and re-prepare")
        need(out, pkg["timing"]["start"], "start", "Choose a start date")
        need(out, pkg["targeting"]["geo"].get("radius_km"), "radius_km", "Choose a radius around the city (1–80 km)")
        timing(out, pkg)
        return out

    def plan(self, pkg: dict, conn: dict | None) -> dict:
        sel = (conn or {}).get("selected") or {}
        blockers = account_blockers(sel, "Meta")
        if sel.get("id") and not sel.get("page_id"):
            blockers.append({"field": "account", "text": "Choose the Facebook Page the ad runs from"})
        b = daily(pkg, self.DEST)
        if sel.get("currency") and b and b["ccy"] != sel["currency"]:
            blockers.append({"field": "budget", "text": f"Budget in {sel['currency']}: the ad account bills in {sel['currency']}, "
                                                        f"the package says {b['ccy']} (never converted for you)"})
        act, geo, c, cr = sel.get("id") or "act_{ad_account}", pkg["targeting"]["geo"], pkg["copy"], pkg["creative"]
        ref = reference(pkg)
        image = next((a for a in pkg["assets"] if a["role"] == "CREATIVE"), None)
        start = pkg["timing"]["start"] and f"{pkg['timing']['start']}T00:00:00+0000"
        targeting = {"geo_locations": {"custom_locations": [{"latitude": geo["lat"], "longitude": geo["lon"],
                                                             "radius": geo.get("radius_km"), "distance_unit": "kilometer"}]},
                     "targeting_automation": {"advantage_audience": 0}}
        story = {"page_id": sel.get("page_id"), "link_data": {
            "link": cr.get("landing_url"), "message": (c.get("primary_text") or {}).get("text"),
            "name": (c.get("headlines") or [{}])[0].get("text"), "image_hash": "{{image}}",
            "call_to_action": {"type": cr.get("cta"), "value": {"link": cr.get("landing_url")}}}}
        adset = {"name": f"{ref} · {geo['name']}", "campaign_id": "{{campaign}}", "daily_budget": str(b["minor"]) if b else None,
                 "billing_event": "IMPRESSIONS", "optimization_goal": "LINK_CLICKS", "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
                 "targeting": json.dumps(targeting), "start_time": start, "status": "PAUSED"}
        if pkg["timing"].get("end"):
            adset["end_time"] = f"{pkg['timing']['end']}T23:59:00+0000"
        requests = [
            {"step": "image", "method": "POST", "url": f"{graph()}/{act}/adimages",
             "form": {"bytes": "", "bytes_sha256": image and image["artifact"]["sha256"]}},
            {"step": "campaign", "method": "POST", "url": f"{graph()}/{act}/campaigns",
             "form": {"name": ref, "objective": "OUTCOME_TRAFFIC", "status": "PAUSED", "special_ad_categories": "[]",
                      "buying_type": "AUCTION", "is_adset_budget_sharing_enabled": "false"}},
            {"step": "adset", "method": "POST", "url": f"{graph()}/{act}/adsets", "form": adset},
            {"step": "creative", "method": "POST", "url": f"{graph()}/{act}/adcreatives",
             "form": {"name": ref, "object_story_spec": json.dumps(story)}},
            {"step": "ad", "method": "POST", "url": f"{graph()}/{act}/ads",
             "form": {"name": ref, "adset_id": "{{adset}}", "creative": json.dumps({"creative_id": "{{creative}}"}), "status": "PAUSED"}}]
        values = [V("Objective", "OUTCOME_TRAFFIC", "FIXED", "release configuration"),
                  V("Status (campaign, ad set, ad)", "PAUSED", "FIXED", "never activated"),
                  V("Special ad categories", [], "FIXED"), V("Ad-set budget sharing", False, "FIXED", "required since v24"),
                  V("Optimization · billing", "LINK_CLICKS · IMPRESSIONS", "FIXED", "a documented valid pair"),
                  V("Bid strategy", "LOWEST_COST_WITHOUT_CAP", "FIXED", "no bid amount"),
                  V("Advantage audience", 0, "FIXED", "only the location below is targeted"),
                  V("Ad account", sel.get("name"), "ACCOUNT"), V("Currency", sel.get("currency"), "ACCOUNT"),
                  V("Page", sel.get("page_name"), "ACCOUNT"),
                  V("Daily budget", b and f"{b['minor']} minor units of {b['ccy']}", "DERIVED", "your budget in minor units"),
                  V("Start", pkg["timing"]["start"], "FOUNDER"),
                  V("Location", f"{geo['name']} + {geo.get('radius_km')} km", "FOUNDER" if geo.get("radius_by") == "YOU_SAID" else "DERIVED",
                    "city point from the catalog; radius " + ("you said" if geo.get("radius_by") == "YOU_SAID" else "the draft's hypothesis")),
                  V("Image", image and image["artifact"]["sha256"][:12], "DERIVED", "the saved file, by digest"),
                  V("Primary text · headline · CTA", f"{(c.get('primary_text') or {}).get('text')} · "
                    f"{(c.get('headlines') or [{}])[0].get('text')} · {cr.get('cta')}", "FOUNDER", "the reviewed copy"),
                  V("Landing page", cr.get("landing_url"), "FOUNDER" if cr.get("landing_by") == "YOU_SAID" else "DERIVED",
                    "your website" if cr.get("landing_by") != "YOU_SAID" else "")]
        return {"requests": requests, "values": values, "blockers": blockers}

    def allow_targets(self, ident: dict, plan: dict) -> list[str]:
        return [ident["ad_account_id"]]

    def outcome(self, step: str, status: int, body) -> tuple[str, object]:
        if isinstance(body, dict) and "error" in body:
            e = body["error"] or {}
            if e.get("code") == 190:
                return "expired", ""
            if status < 500 and e.get("code") not in TRANSIENT and status >= 400:
                return "rejected", f"Meta rejected the {step}: {e.get('error_user_msg') or e.get('message')} (code {e.get('code')})"
            return "unknown", f"HTTP {status} · code {e.get('code')}"
        if status == 200 and isinstance(body, dict):
            if step == "image":
                img = next(iter((body.get("images") or {}).values()), {})
                if img.get("hash"):
                    return "ok", {"id": img["hash"]}
            elif step == "repause" and body.get("success"):
                return "ok", {"id": "ok"}
            elif body.get("id"):
                return "ok", {"id": body["id"]}
        return "unknown", f"HTTP {status}" if status else "no response"

    def read_back(self, action: dict, conn: dict, headers: dict, read) -> dict:
        objs = {}
        for k in ("campaign", "adset", "ad"):
            if k in action["ids"]:
                st, body = read({"method": "GET", "url": f"{graph()}/{action['ids'][k]}",
                                 "params": {"fields": "status,effective_status"}}, headers)
                objs[k] = {"status": body.get("status"), "effective_status": body.get("effective_status")} if st == 200 else {"status": None}
        ok = len(objs) == 3 and all(o["status"] == "PAUSED" and o.get("effective_status") != "ACTIVE" for o in objs.values())
        return {"supported": True, "verified": ok, "objects": objs}

    def repause(self, action: dict, conn: dict, v: dict) -> list[dict]:
        return [{"step": "repause", "method": "POST", "url": f"{graph()}/{action['ids'][k]}", "form": {"status": "PAUSED"}}
                for k, o in v["objects"].items() if o.get("status") != "PAUSED" or o.get("effective_status") == "ACTIVE"]

    def validate_remote(self, plan: dict, conn: dict, headers: dict, read) -> dict:
        """`validate_only` on the campaign; the later objects need its id, so they're checked only when created."""
        req = next(r for r in plan["requests"] if r["step"] == "campaign")
        st, body = read({**req, "form": {**req["form"], "execution_options": json.dumps(["validate_only"])}}, headers)
        if st == 200 and isinstance(body, dict) and body.get("success"):
            return {"result": "PASSED", "message": "Campaign accepted by validate_only (ad set, creative and ad are checked when created)"}
        return {"result": "FAILED", "message": self.outcome("campaign", st, body)[1]}

    def result(self, action: dict, conn: dict) -> dict:
        acct = action["identity"]["ad_account_id"].removeprefix("act_")
        return {**{k: action["ids"].get(k) for k in ("campaign", "adset", "creative", "ad")}, "links": {
            "ads_manager": f"https://adsmanager.facebook.com/adsmanager/manage/campaigns?act={acct}&selected_campaign_ids={action['ids'].get('campaign')}",
            "link_format": "unverified"}}

    def prepared(self, pkg: dict) -> list[str]:
        c = pkg["copy"]
        return [f"Primary text, {len(c.get('headlines', []))} headlines, call to action", "A single image from your site",
                f"{pkg['targeting']['geo']['name']} + radius, daily budget, all paused"]

    def setup_steps(self, pkg: dict) -> list[str]:
        g = pkg["targeting"]["geo"]
        return ["In Meta Ads Manager, create a campaign with the Traffic objective; leave it off (paused).",
                f"Ad set: location {g['name']} + {g.get('radius_km') or '?'} km, daily budget {_money(pkg)}, optimize for link clicks.",
                "Ad: single image from the package's assets, the primary text, the first headline, the call to action and the landing page.",
                "Interests from the audience rationale are Qloo's; look up Meta's own interests yourself if you want them.",
                "Leave everything paused until you have checked it, then record it here: Marked created by you."]


def _money(pkg: dict) -> str:
    from ..delivery import money_text
    return money_text(daily(pkg, "META_ADS"))
