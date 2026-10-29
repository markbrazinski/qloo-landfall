"""TikTok Ads (API for Business v1.3): Traffic to a website, single image on PLACEMENT_PANGLE (owner, 26 Oct 2026: it
runs in TikTok's partner-app network, not the TikTok app), CUSTOMIZED_USER identity; campaign, ad group and ad DISABLE.

Five writes: /identity/create/ → /file/image/ad/upload/ → /campaign/create/ → /adgroup/create/ → /ad/create/. Every
response is HTTP 200 with `{code, message, request_id}`: code 0 is success, 40001 and 40002 are definitive rejections,
anything else is UNCERTAIN. Campaign and ad-group creates carry a stable `request_id` from (action id, step); TikTok
documents collapsing identical requests within 10 s, which only absorbs an accidental duplicate, never a resend.
Unverified until a sandbox or live call: BUDGET_MODE_INFINITE on the campaign with a daily ad-group budget, and the
region-lookup response shape."""
import hashlib
import json
from decimal import Decimal

from .. import config, markets
from . import Adapter, V, account_blockers, daily, need, reference, timing, too_long

API = "https://business-api.tiktok.com/open_api/v1.3"
SANDBOX_API = "https://sandbox-ads.tiktok.com/open_api/v1.3"  # portal doc 1738855331457026: 1 QPS, 30 QPM, 1,000 QPD
SANDBOX_CONFIG = ("tiktok_sandbox_token", "tiktok_sandbox_advertiser_id", "tiktok_sandbox_location_id")
SANDBOX_LABEL = "Landfall's TikTok sandbox advertiser"


def sandbox() -> bool:
    """6.7 T1: only once TikTok has granted a sandbox (owner-recorded); the portal issues the token, no OAuth."""
    return (config.env("LANDFALL_TIKTOK_ACCESS") or "").upper() == "SANDBOX"


def base() -> str:
    return SANDBOX_API if sandbox() else API
MIN_DAILY = {"USD": 2000}  # minor units: the documented ad-group daily minimum; other currencies aren't checked here


class TikTok(Adapter):
    DEST, PROVIDER, LABEL, KIND = "TIKTOK_ADS", "tiktok_ads", "TikTok Ads", "TIKTOK_CREATE_DISABLED_CAMPAIGN"
    CONFIGURATION = ("Traffic: single image on Pangle (TikTok's partner-app network, not the TikTok app); campaign, "
                     "ad group and ad all disabled")
    WHERE, ACCOUNT_NOUN = "TikTok Ads Manager", "advertiser account"
    CONFIG = ("tiktok_app_id", "tiktok_app_secret")
    SIMULATED_IDENTITY = {"advertiser_id": "demo"}

    def missing_config(self) -> list[str]:
        return [n for n in (SANDBOX_CONFIG if sandbox() else self.CONFIG) if not config.env(n)]

    def direct_connect(self) -> dict | None:
        """Sandbox: the portal-issued token and the one configured advertiser; nothing to authorize in a browser."""
        if not sandbox():
            return None
        adv = config.env("tiktok_sandbox_advertiser_id")
        return {"token": {"access_token": config.env("tiktok_sandbox_token")}, "user": {"name": SANDBOX_LABEL},
                "accounts": [{"id": adv, "name": SANDBOX_LABEL}], "scopes": []}

    def access(self) -> dict:
        s = (config.env("LANDFALL_TIKTOK_ACCESS") or "").upper()
        if s == "SANDBOX":
            return {"state": "SANDBOX", "connectable": True, "note": "TikTok sandbox advertiser: mock delivery, nothing serves"}
        if s == "APPROVED":
            return {"state": "APPROVED", "connectable": True, "note": "Developer app approved"}
        return {"state": "PENDING" if s == "PENDING" else "REQUIRED", "connectable": False,
                "note": "No advertiser can connect until TikTok approves Landfall's developer app"}

    def auth_url(self, state: str, redirect: str) -> str:
        from urllib.parse import urlencode
        return "https://business-api.tiktok.com/portal/auth?" + urlencode({"app_id": config.env("tiktok_app_id"),
                                                                           "state": state, "redirect_uri": redirect})

    def connect(self, params: dict, redirect: str) -> dict:
        from ..delivery import read
        app = {"app_id": config.env("tiktok_app_id"), "secret": config.env("tiktok_app_secret")}
        st, body = read({"method": "POST", "url": f"{base()}/oauth2/access_token/",
                         "json": {**app, "auth_code": params.get("auth_code") or params.get("code", "")}}, {}, tries=1)
        data = (body or {}).get("data") or {}
        if st != 200 or body.get("code") != 0 or not data.get("access_token"):
            raise ValueError(f"TikTok didn't issue a token ({(body or {}).get('message') or st})")
        token = {"access_token": data["access_token"]}  # long-term until revoked
        st, adv = read({"method": "GET", "url": f"{base()}/oauth2/advertiser/get/", "params": app},
                       {"Access-Token": token["access_token"]})
        accounts = [{"id": str(a["advertiser_id"]), "name": a.get("advertiser_name") or str(a["advertiser_id"])}
                    for a in ((adv or {}).get("data") or {}).get("list", [])]
        return {"token": token, "user": {"name": "TikTok advertiser"}, "accounts": accounts, "scopes": data.get("scope", [])}

    def select(self, acc: dict, extra: dict, conn: dict, token: dict | None) -> dict:
        """Currency from /advertiser/info/; the package city's location id from /tool/region/ (ACCOUNT). A city it
        doesn't list stays a blocker, never a guess."""
        from ..delivery import read
        h = self.auth_headers(token, conn)
        st, info = read({"method": "GET", "url": f"{base()}/advertiser/info/", "params": {
            "advertiser_ids": json.dumps([acc["id"]]), "fields": json.dumps(["currency", "timezone", "name"])}}, h)
        row = (((info or {}).get("data") or {}).get("list") or [{}])[0]
        locs = {}
        city = markets.CITIES.get(extra.get("city_id") or "")
        if sandbox():  # the sandbox has no /tool/region/: one configured id, labeled as configured
            if acc["id"] != config.env("tiktok_sandbox_advertiser_id"):
                from ..delivery import Refused
                raise Refused(422, "Only this deployment's sandbox advertiser can be used.")
            if city:
                locs[city["city_id"]] = [str(config.env("tiktok_sandbox_location_id"))]
            return {**acc, "currency": row.get("currency"), "timezone": row.get("timezone"), "location_ids": locs, "sandbox": True}
        if city:
            st, reg = read({"method": "GET", "url": f"{base()}/tool/region/", "params": {
                "advertiser_id": acc["id"], "placements": json.dumps(["PLACEMENT_PANGLE"]), "objective_type": "TRAFFIC"}}, h)
            for r in (((reg or {}).get("data") or {}).get("region_info") or []):
                if markets.fold(r.get("name", "")) == markets.fold(city["name"]):
                    locs[city["city_id"]] = [str(r["location_id"])]
                    break
        return {**acc, "currency": row.get("currency"), "timezone": row.get("timezone"), "location_ids": locs}

    def identity(self, user: dict, sel: dict | None) -> dict:
        return {"advertiser_id": (sel or {}).get("id"), "environment": "SANDBOX" if (sel or {}).get("sandbox") else "PRODUCTION"}

    def label_of(self, user: dict, sel: dict | None) -> str:
        return SANDBOX_LABEL if sandbox() else super().label_of(user, sel)

    def auth_headers(self, token: dict, conn: dict) -> dict:
        return {"Access-Token": token["access_token"]}

    def dispatch_ids(self, action: dict) -> dict:
        rid = lambda step: int(hashlib.sha256(f"{action['id']}:{step}".encode()).hexdigest()[:15], 16)  # noqa: E731
        return {"rid_campaign": rid("campaign"), "rid_adgroup": rid("adgroup")}

    def required(self, pkg: dict) -> list[dict]:
        from ..delivery import money_text
        out: list = []
        c, cr, b = pkg["copy"], pkg["creative"], daily(pkg, self.DEST)
        creatives = [a for a in pkg["assets"] if a["role"] == "CREATIVE"]
        need(out, creatives, "assets", "Add an image for the ad (upload one if your site's couldn't be saved)")
        need(out, not creatives or creatives[0]["artifact"]["sha256"] in pkg.get("rights_confirmed", []), "rights",
             "Confirm you hold the rights to the ad image")
        need(out, (c.get("ad_text") or {}).get("text"), "ad_text", "Write the ad text (at most 100 characters)")
        need(out, cr.get("landing_url"), "landing_url", "Give the landing page")
        need(out, cr.get("display_name"), "display_name", "Give the display name shown on the ad")
        need(out, cr.get("cta"), "cta", "Choose a call to action")
        need(out, b, "budget", "Enter a daily budget, or set one in your plan's digital scenario and re-prepare")
        need(out, not b or b["minor"] >= MIN_DAILY.get(b["ccy"], 0), "budget",
             f"TikTok's ad-group minimum is USD 20 a day; its share of your budget is {money_text(b)}")
        need(out, pkg["timing"]["start"], "start", "Choose a start date")
        too_long(out, [c.get("ad_text")], 100, "ad_text", "The ad text")
        timing(out, pkg)
        return out

    def plan(self, pkg: dict, conn: dict | None) -> dict:
        sel = (conn or {}).get("selected") or {}
        blockers = account_blockers(sel, "TikTok")
        b, geo, c, cr = daily(pkg, self.DEST), pkg["targeting"]["geo"], pkg["copy"], pkg["creative"]
        if sel.get("currency") and b and b["ccy"] != sel["currency"]:
            blockers.append({"field": "budget", "text": f"Budget in {sel['currency']}: the advertiser bills in {sel['currency']}, "
                                                        f"the package says {b['ccy']} (never converted for you)"})
        locs = (sel.get("location_ids") or {}).get(geo["city_id"])
        if sel.get("id") and not locs:
            blockers.append({"field": "location", "text": f"TikTok's targeting list for this advertiser has no location named {geo['name']}"})
        adv = sel.get("id") or "{advertiser_id}"
        ref = reference(pkg)
        image = next((a for a in pkg["assets"] if a["role"] == "CREATIVE"), None)
        art = image and _art(image)
        from ..delivery import EXPONENT
        budget = b and float(Decimal(b["minor"]) / (10 ** EXPONENT.get(b["ccy"], 2)))
        requests = [
            {"step": "identity", "method": "POST", "url": f"{base()}/identity/create/",
             "json": {"advertiser_id": adv, "display_name": cr.get("display_name")}},
            {"step": "image", "method": "POST", "url": f"{base()}/file/image/ad/upload/", "multipart": {
                "fields": {"advertiser_id": adv, "upload_type": "UPLOAD_BY_FILE", "image_signature": art and art["md5"]},
                "file": {"field": "image_file", "sha256": image and image["artifact"]["sha256"],
                         "filename": art and art["filename"], "media_type": art and art["media_type"]}}},
            {"step": "campaign", "method": "POST", "url": f"{base()}/campaign/create/", "json": {
                "advertiser_id": adv, "campaign_name": ref, "objective_type": "TRAFFIC", "budget_mode": "BUDGET_MODE_INFINITE",
                "operation_status": "DISABLE", "request_id": "{{rid_campaign}}"}},
            {"step": "adgroup", "method": "POST", "url": f"{base()}/adgroup/create/", "json": {
                "advertiser_id": adv, "campaign_id": "{{campaign}}", "adgroup_name": f"{ref} · {geo['name']}",
                "promotion_type": "WEBSITE", "placement_type": "PLACEMENT_TYPE_NORMAL", "placements": ["PLACEMENT_PANGLE"],
                "location_ids": locs or [], "budget_mode": "BUDGET_MODE_DAY", "budget": budget,
                "schedule_type": "SCHEDULE_FROM_NOW", "schedule_start_time": pkg["timing"]["start"] and f"{pkg['timing']['start']} 00:00:00",
                "optimization_goal": "CLICK", "billing_event": "CPC", "bid_type": "BID_TYPE_NO_BID",
                "pacing": "PACING_MODE_SMOOTH", "operation_status": "DISABLE", "request_id": "{{rid_adgroup}}"}},
            {"step": "ad", "method": "POST", "url": f"{base()}/ad/create/", "json": {
                "advertiser_id": adv, "adgroup_id": "{{adgroup}}", "creatives": [{
                    "ad_name": ref, "identity_type": "CUSTOMIZED_USER", "identity_id": "{{identity}}", "ad_format": "SINGLE_IMAGE",
                    "image_ids": ["{{image}}"], "ad_text": (c.get("ad_text") or {}).get("text"), "call_to_action": cr.get("cta"),
                    "landing_page_url": cr.get("landing_url"), "operation_status": "DISABLE"}]}}]
        values = [V("Objective", "TRAFFIC", "FIXED", "release configuration"),
                  V("Placement", "PLACEMENT_PANGLE (partner apps, not the TikTok app)", "FIXED", "owner decision 26 Oct 2026"),
                  V("Status (campaign, ad group, ad)", "DISABLE", "FIXED", "never enabled"),
                  V("Campaign budget", "BUDGET_MODE_INFINITE", "FIXED", "the budget lives on the ad group (unverified combination)"),
                  V("Ad group", "WEBSITE · CLICK · CPC · BID_TYPE_NO_BID · PACING_MODE_SMOOTH · SCHEDULE_FROM_NOW", "FIXED",
                    "TikTok's single-image-on-Pangle guide example"),
                  V("Identity", "CUSTOMIZED_USER", "FIXED"), V("Display name", cr.get("display_name"), "FOUNDER"),
                  V("Advertiser", sel.get("name"), "ACCOUNT"), V("Currency", sel.get("currency"), "ACCOUNT"),
                  V("Location ids", locs, "ACCOUNT", "configured for the sandbox (it has no region list)" if sel.get("sandbox")
                    else f"TikTok's region list, matched by the name {geo['name']}"),
                  V("Daily budget", b and f"{budget} {b['ccy']}", "DERIVED", "your budget, at least USD 20"),
                  V("Start (UTC)", pkg["timing"]["start"], "FOUNDER"),
                  V("Image", image and image["artifact"]["sha256"][:12], "DERIVED", "the saved file, by digest"),
                  V("Ad text · CTA", f"{(c.get('ad_text') or {}).get('text')} · {cr.get('cta')}", "FOUNDER", "the reviewed copy")]
        return {"requests": requests, "values": values, "blockers": blockers}

    def allow_targets(self, ident: dict, plan: dict) -> list[str]:
        return [ident["advertiser_id"]]

    ID_OF = {"identity": "identity_id", "image": "image_id", "campaign": "campaign_id", "adgroup": "adgroup_id"}

    def outcome(self, step: str, status: int, body) -> tuple[str, object]:
        if status != 200 or not isinstance(body, dict) or "code" not in body:
            return "unknown", f"HTTP {status}" if status else "no response"
        code, data = body["code"], body.get("data") or {}
        if code == 0:
            if step == "ad":
                ids = data.get("ad_ids") or []
                return ("ok", {"id": str(ids[0])}) if ids else ("unknown", "a reply without the ad id")
            if step == "repause":
                return "ok", {"id": "ok"}
            got = data.get(self.ID_OF.get(step, ""))
            return ("ok", {"id": str(got)}) if got else ("unknown", f"a reply without the {step} id")
        if code in (40001, 40002):
            return "rejected", f"TikTok rejected the {step}: {body.get('message')} (code {code})"
        if code == 40105:
            return "expired", ""
        return "unknown", f"code {code} · request {body.get('request_id')}"

    def read_back(self, action: dict, conn: dict, headers: dict, read) -> dict:
        adv, ids, objs = action["identity"]["advertiser_id"], action["ids"], {}
        for k, path, key in (("campaign", "campaign", "campaign_ids"), ("adgroup", "adgroup", "adgroup_ids"), ("ad", "ad", "ad_ids")):
            if k not in ids:
                continue
            st, body = read({"method": "GET", "url": f"{base()}/{path}/get/", "params": {
                "advertiser_id": adv, "filtering": json.dumps({key: [ids[k]]})}}, headers)
            row = ((((body or {}).get("data") or {}).get("list")) or [{}])[0] if st == 200 and (body or {}).get("code") == 0 else {}
            objs[k] = {"operation_status": row.get("operation_status"), "secondary_status": row.get("secondary_status")}
        ok = len(objs) == 3 and all(o["operation_status"] == "DISABLE" for o in objs.values())
        return {"supported": True, "verified": ok, "objects": objs}

    def repause(self, action: dict, conn: dict, v: dict) -> list[dict]:
        adv, ids = action["identity"]["advertiser_id"], action["ids"]
        keys = {"campaign": "campaign_ids", "adgroup": "adgroup_ids", "ad": "ad_ids"}
        return [{"step": "repause", "method": "POST", "url": f"{base()}/{k}/status/update/",
                 "json": {"advertiser_id": adv, keys[k]: [ids[k]], "operation_status": "DISABLE"}}
                for k, o in v["objects"].items() if o["operation_status"] != "DISABLE"]

    def result(self, action: dict, conn: dict) -> dict:
        return {**{k: action["ids"].get(k) for k in ("identity", "image", "campaign", "adgroup", "ad")},
                "links": {"ads_manager": "https://ads.tiktok.com/i18n/perf/campaign", "link_format": "unverified"}}

    def prepared(self, pkg: dict) -> list[str]:
        return ["Ad text and call to action", "A single image from your site",
                f"{pkg['targeting']['geo']['name']}, daily budget, all disabled · Pangle partner apps, not the TikTok app"]

    def setup_steps(self, pkg: dict) -> list[str]:
        from ..delivery import money_text
        return ["In TikTok Ads Manager, create a Traffic campaign to your website; leave it disabled.",
                f"Ad group: placement Pangle only (not the TikTok app), location {pkg['targeting']['geo']['name']}, "
                f"daily budget {money_text(daily(pkg, 'TIKTOK_ADS'))}, optimize for clicks, no bid cap.",
                "Ad: single image from the package's assets, the ad text, your display name and the landing page.",
                "Keep everything disabled until you have checked it, then record it here: Marked created by you."]


def _art(asset: dict) -> dict:
    from .. import db
    from ..delivery import artifact_bytes
    art = db.get_record(asset["artifact"]["id"])
    data = artifact_bytes(asset["artifact"]) or b""
    return {"md5": hashlib.md5(data).hexdigest(), "filename": art["filename"], "media_type": art["media_type"]}
