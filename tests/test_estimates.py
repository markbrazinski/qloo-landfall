"""Cost estimates beside NOT KNOWN lines (owner, 27 Oct 2026): base × quantity × country price level, in the city's
currency at the dated ECB rate; never summed into `known`; the founder can accept one as an ESTIMATE line."""
from decimal import Decimal

from test_markets import idle, markets_env  # noqa: F401
from test_slice4 import env, research_ready  # noqa: F401
from test_slice5 import line, money_env, planned, pv  # noqa: F401
from test_slice45_runner import spec_env  # noqa: F401
from test_slice6 import deliver_env  # noqa: F401
from test_slice65_plan import plan_env  # noqa: F401

from landfall import estimates, plans

FX = {"rates": {"USD": "1.0800", "CAD": "1.5000"}, "rate_date": "2026-10-26"}


def test_formula_and_currency():
    e = estimates.estimate("popup:STAFF", "montreal", FX)
    ratio = Decimal(str(estimates.LEVELS["values"]["CA"]["ratio"]))
    cad = Decimal(20 * 2 * 8 * 2) * ratio / (Decimal("1.08") / Decimal("1.5"))
    mid = estimates._round2(cad)
    assert e["amount"] == {"minor": int(mid * 100), "ccy": "CAD"}
    assert e["low"]["minor"] == int(estimates._round2(mid * Decimal("0.7")) * 100)
    assert e["high"]["minor"] == int(estimates._round2(mid * Decimal("1.3")) * 100)
    assert e["basis_note"].startswith(f"Estimate: 2 staff × 8 h × 2 days × US$20/h Landfall planning base × {ratio} Canada price level (World Bank")
    assert "ECB rates of 2026-10-26" in e["basis_note"] and e["text"].startswith("CA$")
    assert {s["kind"] for s in e["sources"]} == {"PLANNING_ASSUMPTION", "SOURCED"}
    usd = estimates.estimate("popup:STAFF", "montreal")  # no rate stored: US$, and it says so
    assert usd["amount"]["ccy"] == "USD" and "no exchange rate" in usd["basis_note"]
    own = estimates.estimate("popup:SHIPPING", "montreal", FX)  # freight isn't scaled by local prices
    assert "price level" not in own["basis_note"] and "not scaled" in own["basis_note"]
    assert estimates.estimate("digital:MEDIA_SPEND", "montreal", FX, days=7)["basis_note"].startswith("Estimate: 7 days × US$30/day")


def test_no_price_level_no_estimate():
    assert "TW" not in estimates.LEVELS["values"]
    assert estimates.estimate("popup:STAFF", "taipei", FX) is None
    assert estimates.estimate("retail:OTHER", "montreal", FX) is None  # margin terms: no up-front base


def test_estimates_never_enter_known(client, money_env):
    pid = planned(client, money_env)
    assert line(client, pid, "cl_retail_shipping", "40").status_code == 200
    m = pv(client, pid)["money"]
    assert m["cash_out_known"] == {"minor": 4000, "ccy": "USD"} and m["unknown"]  # exactly as before
    est = [ln for ln in m["lines"] if ln.get("estimate")]
    assert est and all(ln["amount"] is None and ln["basis"] == "NOT_KNOWN" for ln in est)
    assert not any(ln.get("estimate") for ln in m["lines"] if ln["amount"])
    t = m["estimated_total"]
    assert t["low"]["minor"] < t["amount"]["minor"] < t["high"]["minor"] and t["amount"]["minor"] > 4000


def test_accept_estimate(client, money_env):
    pid = planned(client, money_env)
    before = pv(client, pid)["money"]
    e = next(ln for ln in before["lines"] if ln["id"] == "cl_retail_stock")["estimate"]
    r = line(client, pid, "cl_retail_stock", use_estimate=True)
    assert r.status_code == 200, r.text
    after = pv(client, pid)["money"]
    ln = next(x for x in after["lines"] if x["id"] == "cl_retail_stock")
    assert ln["amount"] == e["amount"] and ln["basis"] == "ESTIMATE" and ln["by"] == "founder" and ln["basis_note"] == e["basis_note"]
    assert "estimate" not in ln and "cl_retail_stock" not in after["unknown"]
    assert after["cash_out_known"]["minor"] > before["cash_out_known"]["minor"] and after["assumed_known"]["minor"] > 0
    assert line(client, pid, "cl_retail_stock", use_estimate=True).status_code == 422  # a priced line has no estimate
    rec = plans.view(pid, "toronto")["plan"]
    assert rec["signed_off"] is None
