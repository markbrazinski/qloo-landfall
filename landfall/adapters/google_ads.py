"""Google Ads: one Search campaign (Maximize Clicks), ad group, responsive search ad and keywords, all PAUSED, in one
atomic `googleAds:mutate` with temporary ids and `partialFailure: false` (documented: nothing is applied unless every
operation is valid). API v26; access belongs to Landfall's Cloud project (developer tokens sunset 9 Sep 2026).
After a timeout the action is UNCERTAIN and one read-back looks for the Landfall reference in the campaign name; it is
never retried."""
from .. import config
from . import Adapter, V, account_blockers, daily, google_auth_url, google_exchange, google_fresh, need, reference, timing, too_long

API = "https://googleads.googleapis.com/v26"


TEST_LABEL = "Landfall's Google Ads test account"


def test_access() -> bool:
    return (config.env("LANDFALL_GOOGLE_ADS_ACCESS") or "").upper() == "TEST"


def digits(name: str) -> str:
    return "".join(ch for ch in (config.env(name) or "") if ch.isdigit())


def gaql(cid: str, q: str) -> dict:
    return {"method": "POST", "url": f"{API}/customers/{cid}/googleAds:search", "json": {"query": q}}


class GoogleAds(Adapter):
    DEST, PROVIDER, LABEL, KIND = "GOOGLE_ADS", "google_ads", "Google Ads", "GOOGLE_ADS_CREATE_PAUSED_SEARCH"
    CONFIGURATION = "Search: one campaign (Maximize Clicks), ad group, responsive search ad and keywords, all paused"
    WHERE, VALIDATES, FINDS_BY_REFERENCE = "Google Ads", True, True
    CONFIG = ("google_oauth_client_id", "google_oauth_client_secret", "LANDFALL_GOOGLE_ADS_ACCESS")
    SCOPES = "openid email https://www.googleapis.com/auth/adwords"
    SIMULATED_IDENTITY = {"email": "demo@example.com", "customer_id": "0000000000", "login_customer_id": None}

    def missing_config(self) -> list[str]:
        extra = ("google_ads_test_manager_id", "google_ads_test_customer_id") if test_access() else ()
        return [n for n in self.CONFIG + extra if not config.env(n)]

    def access(self) -> dict:
        s = (config.env("LANDFALL_GOOGLE_ADS_ACCESS") or "").upper()
        if s in ("EXPLORER", "BASIC", "STANDARD"):
            return {"state": "APPROVED", "connectable": True, "note": f"{s.title()} access"}
        return {"state": "PENDING" if s == "PENDING" else "REQUIRED", "connectable": bool(s),
                "note": "Test access: test accounts only (no serving, no billing); production needs Explorer or Basic access"}

    def auth_url(self, state: str, redirect: str) -> str:
        return google_auth_url(state, redirect, self.SCOPES)

    def connect(self, params: dict, redirect: str) -> dict:
        from ..delivery import read
        got = google_exchange(params.get("code", ""), redirect, read)
        if test_access():  # listAccessibleCustomers can omit a client reached through a manager: the one account is config
            return {**got, "accounts": [{"id": digits("google_ads_test_customer_id"), "name": TEST_LABEL}]}
        st, body = read({"method": "GET", "url": f"{API}/customers:listAccessibleCustomers"},
                        {"Authorization": f"Bearer {got['token']['access_token']}"})
        names = (body or {}).get("resourceNames", []) if st == 200 else []
        return {**got, "accounts": [{"id": n.split("/")[-1], "name": n.split("/")[-1]} for n in names]}

    def select(self, acc: dict, extra: dict, conn: dict, token: dict | None) -> dict:
        from ..delivery import Refused, read
        login = (extra.get("login_customer_id") or "").replace("-", "") or None
        if test_access():
            login = digits("google_ads_test_manager_id")  # the form's value is ignored: the manager is config
        h = self.auth_headers(token, {"selected": {"login_customer_id": login}})
        st, body = read(gaql(acc["id"], "SELECT customer.id, customer.descriptive_name, customer.currency_code, "
                                        "customer.manager, customer.test_account FROM customer"), h)
        row = ((body or {}).get("results") or [{}])[0].get("customer") if st == 200 else None
        if not row:
            raise Refused(422, f"Google Ads didn't return that account (HTTP {st}); a manager account needs its login id.")
        if test_access() and (str(row.get("id")) != digits("google_ads_test_customer_id") or row.get("testAccount") is not True
                              or row.get("manager") is not False):
            raise Refused(422, "The configured Google Ads account isn't a test client account")
        if row.get("manager"):
            raise Refused(422, "That's a manager account: choose the client account the campaign belongs to.")
        return {"id": acc["id"], "name": row.get("descriptiveName") or acc["id"], "currency": row.get("currencyCode"),
                "test_account": row.get("testAccount"), "login_customer_id": login}

    def identity(self, user: dict, sel: dict | None) -> dict:
        return {"email": user["email"], "customer_id": (sel or {}).get("id"), "login_customer_id": (sel or {}).get("login_customer_id"),
                "environment": "TEST_ACCOUNT" if (sel or {}).get("test_account") else "PRODUCTION"}

    def label_of(self, user, sel) -> str:
        if test_access():  # G-J/G2: the configured ids stay server-side in this deployment's labels
            return TEST_LABEL
        return f"{sel['name']} ({sel['id']}){' · test account' if sel.get('test_account') else ''}" if sel else user["email"]

    def fresh_token(self, conn, token, read):
        return google_fresh(conn, token, read)

    def auth_headers(self, token: dict, conn: dict) -> dict:
        h = {"Authorization": f"Bearer {token['access_token']}"}
        login = ((conn or {}).get("selected") or {}).get("login_customer_id")
        return {**h, "login-customer-id": login} if login else h

    def required(self, pkg: dict) -> list[dict]:
        out: list = []
        c = pkg["copy"]
        need(out, pkg["creative"].get("landing_url"), "landing_url", "Give the final URL")
        need(out, 3 <= len(c.get("headlines", [])) <= 15, "headlines", "Write 3 to 15 headlines (each at most 30 characters)")
        need(out, 2 <= len(c.get("descriptions", [])) <= 4, "descriptions", "Write 2 to 4 descriptions (each at most 90 characters)")
        need(out, c.get("keywords"), "keywords", "Add at least one keyword")
        too_long(out, c.get("headlines"), 30, "headlines", "Headline")
        too_long(out, c.get("descriptions"), 90, "descriptions", "Description")
        too_long(out, [{"text": k["text"]} for k in c.get("keywords", [])], 80, "keywords", "Keyword")
        timing(out, pkg)
        need(out, daily(pkg, self.DEST), "budget", "Enter a daily budget, or set one in your plan's digital scenario and re-prepare")
        need(out, pkg["targeting"]["geo"].get("radius_km"), "radius_km", "Choose a radius around the city (1–50 miles)")
        return out

    def plan(self, pkg: dict, conn: dict | None) -> dict:
        from ..delivery import minor_to
        sel = (conn or {}).get("selected") or {}
        blockers = account_blockers(sel, "Google Ads")
        b, mx = daily(pkg, self.DEST), pkg["budget"].get("max_cpc")
        if sel.get("currency") and b and b["ccy"] != sel["currency"]:
            blockers.append({"field": "budget", "text": f"Budget in {sel['currency']}: the account bills in {sel['currency']}, "
                                                        f"the package says {b['ccy']} (never converted for you)"})
        cid, geo, c = sel.get("id") or "{customer_id}", pkg["targeting"]["geo"], pkg["copy"]
        ref = reference(pkg)
        rn = lambda kind, n: f"customers/{cid}/{kind}/-{n}"  # noqa: E731  temporary ids
        spend = {"cpcBidCeilingMicros": str(minor_to(mx, 6))} if mx else {}
        ops = [
            {"campaignBudgetOperation": {"create": {"resourceName": rn("campaignBudgets", 1), "name": f"{ref} budget",
                                                    "amountMicros": str(minor_to(b, 6)) if b else None,
                                                    "deliveryMethod": "STANDARD", "explicitlyShared": False}}},
            {"campaignOperation": {"create": {"resourceName": rn("campaigns", 2), "name": ref, "status": "PAUSED",
                                              "advertisingChannelType": "SEARCH", "campaignBudget": rn("campaignBudgets", 1),
                                              "targetSpend": spend,
                                              "networkSettings": {"targetGoogleSearch": True, "targetSearchNetwork": False,
                                                                  "targetContentNetwork": False, "targetPartnerSearchNetwork": False},
                                              "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"}}},
            {"campaignCriterionOperation": {"create": {"campaign": rn("campaigns", 2), "proximity": {
                "geoPoint": {"latitudeInMicroDegrees": round(geo["lat"] * 1e6), "longitudeInMicroDegrees": round(geo["lon"] * 1e6)},
                "radius": geo.get("radius_km"), "radiusUnits": "KILOMETERS"}}}},
            {"adGroupOperation": {"create": {"resourceName": rn("adGroups", 3), "name": f"{ref} · {geo['name']}",
                                             "campaign": rn("campaigns", 2), "type": "SEARCH_STANDARD", "status": "PAUSED"}}},
            *[{"adGroupCriterionOperation": {"create": {"adGroup": rn("adGroups", 3), "status": "ENABLED",
                                                        "keyword": {"text": k["text"], "matchType": k["match_type"]}}}}
              for k in c.get("keywords", [])],
            {"adGroupAdOperation": {"create": {"adGroup": rn("adGroups", 3), "status": "PAUSED", "ad": {
                "finalUrls": [pkg["creative"].get("landing_url")],
                "responsiveSearchAd": {"headlines": [{"text": s["text"]} for s in c.get("headlines", [])],
                                       "descriptions": [{"text": s["text"]} for s in c.get("descriptions", [])]}}}}}]
        requests = [{"step": "mutate", "method": "POST", "url": f"{API}/customers/{cid}/googleAds:mutate",
                     "json": {"mutateOperations": ops, "partialFailure": False, "validateOnly": False}}]
        values = [V("Campaign type", "SEARCH", "FIXED", "release configuration"),
                  V("Status (campaign, ad group, ad)", "PAUSED", "FIXED", "new campaigns default to ENABLED, so PAUSED is always sent"),
                  V("Bidding", "Maximize Clicks (target_spend)", "FIXED", "the Search guide's own sample; no ad-group bid"),
                  V("Max CPC ceiling", mx and f"{mx['ccy']} {minor_to(mx, 6)} micros", "FOUNDER" if mx else "FIXED",
                    "" if mx else "none"),
                  V("Networks", "Google Search only", "FIXED"),
                  V("EU political advertising", "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING", "FIXED", "set explicitly"),
                  V("Budget", "STANDARD, not shared", "FIXED"),
                  V("Daily budget", b and f"{minor_to(b, 6)} micros of {b['ccy']}", "DERIVED", "your budget × 10⁶ in micros"),
                  V("Customer account", sel.get("id") and TEST_LABEL if test_access() else sel.get("name"), "ACCOUNT"), V("Currency", sel.get("currency"), "ACCOUNT"),
                  V("Location", f"{geo['name']} + {round(geo['radius_km'] / 1.609344, 1) if geo.get('radius_km') else '?'} mi (proximity)", "DERIVED", "city point from the catalog"),
                  V("Keywords", [f"{k['text']} [{k['match_type']}]" for k in c.get("keywords", [])], "FOUNDER",
                    "PHRASE by default (release setting); change per keyword"),
                  V("Headlines · descriptions", f"{len(c.get('headlines', []))} · {len(c.get('descriptions', []))}", "FOUNDER", "the reviewed copy"),
                  V("Start date", "not sent", "FIXED", "optional; the campaign stays paused until you enable it in Google Ads")]
        return {"requests": requests, "values": values, "blockers": blockers}

    def allow_targets(self, ident: dict, plan: dict) -> list[str]:
        return [ident["customer_id"]]

    def outcome(self, step: str, status: int, body) -> tuple[str, object]:
        if step == "repause" and status == 200 and isinstance(body, dict) and "mutateOperationResponses" in body:
            return "ok", {"id": "ok"}
        if status == 200 and isinstance(body, dict) and body.get("mutateOperationResponses"):
            names = [next(iter(r.values()), {}).get("resourceName") for r in body["mutateOperationResponses"]]
            camp = next((n for n in names if n and "/campaigns/" in n), None)
            if camp and all(names):
                return "ok", {"id": camp, "extra": {"resources": names}}
            return "unknown", "a reply without every resource name"
        if status == 401:
            return "expired", ""
        details = ((body or {}).get("error") or {}).get("details", []) if isinstance(body, dict) else []
        failure = next((d for d in details if "GoogleAdsFailure" in d.get("@type", "")), None)
        if status == 400 and failure:
            errs = failure.get("errors", [])
            return "rejected", "Google Ads rejected it: " + "; ".join(e.get("message", "") for e in errs[:3])
        return "unknown", f"HTTP {status}" if status else "no response"

    def _names(self, action: dict) -> dict:
        res = ((action.get("responses") or {}).get("mutate") or {}).get("resources", [])
        pick = lambda part: next((n for n in res if f"/{part}/" in n), None)  # noqa: E731
        return {"campaign": pick("campaigns"), "ad_group": pick("adGroups"), "ad_group_ad": pick("adGroupAds")}

    def read_back(self, action: dict, conn: dict, headers: dict, read) -> dict:
        cid, n = action["identity"]["customer_id"], self._names(action)
        q = {"campaign": f"SELECT campaign.status, campaign.serving_status, campaign.primary_status FROM campaign "
                         f"WHERE campaign.resource_name = '{n['campaign']}'",
             "ad_group": f"SELECT ad_group.status FROM ad_group WHERE ad_group.resource_name = '{n['ad_group']}'",
             "ad_group_ad": f"SELECT ad_group_ad.status FROM ad_group_ad WHERE ad_group_ad.resource_name = '{n['ad_group_ad']}'"}
        objs = {}
        for k, query in q.items():
            st, body = read(gaql(cid, query), headers)
            row = ((body or {}).get("results") or [{}])[0] if st == 200 else {}
            key = {"campaign": "campaign", "ad_group": "adGroup", "ad_group_ad": "adGroupAd"}[k]
            o = row.get(key) or {}
            objs[k] = {"status": o.get("status"), "serving_status": o.get("servingStatus"), "primary_status": o.get("primaryStatus")}
        return {"supported": True, "verified": all(o["status"] == "PAUSED" for o in objs.values()), "objects": objs}

    def repause(self, action: dict, conn: dict, v: dict) -> list[dict]:
        n, cid = self._names(action), action["identity"]["customer_id"]
        kinds = {"campaign": "campaignOperation", "ad_group": "adGroupOperation", "ad_group_ad": "adGroupAdOperation"}
        ops = [{kinds[k]: {"update": {"resourceName": n[k], "status": "PAUSED"}, "updateMask": "status"}}
               for k, o in v["objects"].items() if o["status"] != "PAUSED" and n[k]]
        return [{"step": "repause", "method": "POST", "url": f"{API}/customers/{cid}/googleAds:mutate",
                 "json": {"mutateOperations": ops, "partialFailure": False}}] if ops else []

    def find(self, action: dict, conn: dict, headers: dict, read) -> list:
        """One read by the Landfall reference in the campaign name. Finding nothing is never proof it wasn't created."""
        ref = action["requests"][0]["json"]["mutateOperations"][1]["campaignOperation"]["create"]["name"]
        st, body = read(gaql(action["identity"]["customer_id"], "SELECT campaign.resource_name, campaign.status FROM campaign "
                                                                  f"WHERE campaign.name = '{ref}'"), headers)
        return [r.get("campaign") for r in (body or {}).get("results", [])] if st == 200 else []

    def validate_remote(self, plan: dict, conn: dict, headers: dict, read) -> dict:
        req = plan["requests"][0]
        st, body = read({**req, "json": {**req["json"], "validateOnly": True}}, headers)
        if st == 200:
            return {"result": "PASSED", "message": "Accepted by validate_only; nothing was created"}
        return {"result": "FAILED", "message": self.outcome("mutate", st, body)[1]}

    def result(self, action: dict, conn: dict) -> dict:
        n = self._names(action)
        camp = (n["campaign"] or "").split("/")[-1]
        return {**n, "links": {"google_ads": f"https://ads.google.com/aw/campaigns?campaignId={camp}", "link_format": "unverified"}}

    def prepared(self, pkg: dict) -> list[str]:
        c = pkg["copy"]
        return [f"{len(c.get('headlines', []))} headlines, {len(c.get('descriptions', []))} descriptions, "
                f"{len(c.get('keywords', []))} keywords", f"{pkg['targeting']['geo']['name']} + radius, daily budget, all paused"]

    def setup_steps(self, pkg: dict) -> list[str]:
        from ..delivery import money_text
        g = pkg["targeting"]["geo"]
        return ["In Google Ads, create a Search campaign without a goal; bidding Maximize Clicks; Google Search network only.",
                f"Location: {g['name']} + {round(g['radius_km'] / 1.609344, 1) if g.get('radius_km') else '?'} mi radius. Budget: {money_text(daily(pkg, 'GOOGLE_ADS'))} a day.",
                "One ad group with the keywords in copy.csv (phrase match unless you changed one).",
                "One responsive search ad with the headlines and descriptions in copy.csv and the final URL.",
                "Pause the campaign before you save it, then record it here: Marked created by you."]

