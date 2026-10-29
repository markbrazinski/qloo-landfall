"""Slice 5 · D: deterministic digital scenarios (BUILD-PLAN "Slice 5" §5.7 [R1]; CONTRACTS B7 DigitalScenario).

- One `total_daily` in the digital outline's currency, split across the chosen platforms (D5): an equal split in
  minor units, the remainder one each to the first platforms in order; a YOU_SAID allocation is kept and the rest is
  split among the others.
- MEDIA_SPEND = total_daily × horizon_days: one Plan cost line (`by: scenario`). A scenario write is DigitalScenario
  v+1 and Plan v+1 in one transaction.
- Rates are per platform and never applied to another. A platform without rates has UNKNOWN outputs; the aggregate is
  null until every chosen platform has them. Reach is null without a stated or sourced frequency. Qloo is never an
  input, and a benchmark is never called a forecast.
"""
from decimal import Decimal

from . import db, markets, money, plans
from .adapters.tiktok import MIN_DAILY as TIKTOK_MIN
from .research import Refused

VERSION = "scenario@1"
PLATFORMS = ("META_ADS", "GOOGLE_ADS", "TIKTOK_ADS")
LABEL = {"META_ADS": "Meta", "GOOGLE_ADS": "Google", "TIKTOK_ADS": "TikTok"}
# Per-currency daily minimum in minor units. TikTok's is documented (adapters/tiktok.py). Meta's and Google's aren't
# recorded with a source yet, so none is checked: ponytail: add one when it is sourced and dated.
MIN_DAILY = {"TIKTOK_ADS": TIKTOK_MIN}
RATES = ("cpm", "ctr", "cvr")
STATES = ("SOURCED", "YOU_SAID", "ASSUMED", "UNKNOWN")
MEDIA = "digital:MEDIA_SPEND"


def scenario_id(pid: str, cid: str) -> str:
    return f"scn_{pid}_{cid}"


def split(total: dict, platforms: list[str], fixed: dict[str, dict] | None = None) -> dict[str, dict]:
    """Equal split in minor units, remainder one each to the first platforms in order; `fixed` (YOU_SAID) is kept."""
    fixed = {p: a for p, a in (fixed or {}).items() if p in platforms}
    rest = [p for p in platforms if p not in fixed]
    left = total["minor"] - sum(a["minor"] for a in fixed.values())
    if left < 0 or (not rest and left):
        raise Refused(422, "Your platform budgets add up to more than your total." if left < 0
                      else "Your platform budgets add up to less than your total: leave one to share the rest.")
    out = dict(fixed)
    if rest:
        q, r = divmod(left, len(rest))
        out.update({p: {"minor": q + (i < r), "ccy": total["ccy"]} for i, p in enumerate(rest)})
    return {p: out[p] for p in platforms}


def _rng(v) -> dict | None:
    if v is None:
        return None
    try:
        r = {k: Decimal(str(v[k])) for k in ("low", "mid", "high")}
    except Exception:
        raise Refused(422, "Give each rate as low, mid and high numbers.")
    if not (0 < r["low"] <= r["mid"] <= r["high"]):
        raise Refused(422, "Each rate needs 0 < low ≤ mid ≤ high.")
    return {k: str(x) for k, x in r.items()}


def _platform(p: dict, ccy: str) -> dict:
    if p.get("platform") not in PLATFORMS:
        raise Refused(422, f"Choose platforms from {', '.join(LABEL.values())}.")
    rates = {k: _rng((p.get("rates") or {}).get(k)) for k in RATES}
    for k in ("ctr", "cvr"):
        if rates[k] and Decimal(rates[k]["high"]) > 1:
            raise Refused(422, f"{k.upper()} is a fraction of at most 1 (0.012 for 1.2%).")
    freq = (p.get("rates") or {}).get("frequency")
    if freq is not None and not (isinstance(freq, (int, float)) and freq >= 1):
        raise Refused(422, "Frequency is the average number of times one person sees the ad (at least 1).")
    vals = {**rates, "frequency": freq}
    states = {k: (p.get("input_states") or {}).get(k) or ("UNKNOWN" if v is None else "ASSUMED") for k, v in vals.items()}
    if set(states.values()) - set(STATES):
        raise Refused(422, f"An input is one of {', '.join(STATES)}.")
    for k, st in states.items():
        if (vals[k] is None) != (st == "UNKNOWN"):
            raise Refused(422, f"{k}: a missing value is UNKNOWN, and only a missing value is.")
    src = p.get("source")
    if "SOURCED" in states.values() and not (src and src.get("name") and src.get("as_of") and src.get("context")):
        raise Refused(422, "A sourced rate needs its source: name, date and context.")
    alloc = p.get("allocation")
    if p.get("allocation_by") == "YOU_SAID":
        alloc = money.money(alloc["amount"], alloc["ccy"]) if alloc and "amount" in alloc else alloc
        if not alloc or alloc["ccy"] != ccy:
            raise Refused(422, f"Give the platform budget in {ccy}.")
    basis = ("PLANNER_ESTIMATE" if (src or {}).get("kind") == "PLANNER_ESTIMATE" else "BENCHMARK" if "SOURCED" in states.values()
             else "ASSUMED" if set(states.values()) & {"ASSUMED", "YOU_SAID"} else "UNKNOWN")
    return {"platform": p["platform"], "allocation_by": "YOU_SAID" if p.get("allocation_by") == "YOU_SAID" else "SPLIT",
            "allocation": alloc if p.get("allocation_by") == "YOU_SAID" else None,
            "rates": {**rates, "frequency": freq}, "input_states": states, "source": src if "SOURCED" in states.values() else None,
            "basis": basis}


def _n(d: Decimal) -> int:
    return int(d.to_integral_value())


def compute(scn: dict) -> dict:
    """Per platform from its own allocation: impressions = allocation × days ÷ CPM × 1,000; clicks = impressions × CTR;
    conversions = clicks × CVR; each {low, mid, high}, with each step shown. Then the aggregate."""
    days, rows = scn["horizon_days"], []
    for p in scn["platforms"]:
        r, a = p["rates"], p["allocation"]
        spend = a and money.major(a) * days
        out = {"impressions": None, "clicks": None, "conversions": None, "unique_reach": None}
        steps = []
        if spend is not None and r["cpm"]:
            cpm = {k: Decimal(v) for k, v in r["cpm"].items()}
            out["impressions"] = {"low": _n(spend / cpm["high"] * 1000), "mid": _n(spend / cpm["mid"] * 1000),
                                  "high": _n(spend / cpm["low"] * 1000)}
            steps.append(f"{money.display(a)} a day × {days} days ÷ CPM {cpm['mid']} × 1,000 = {out['impressions']['mid']:,} impressions (mid)")
        for k, rate, name in (("clicks", "ctr", "clicks"), ("conversions", "cvr", "conversions")):
            prev = out["impressions"] if k == "clicks" else out["clicks"]
            if prev and r[rate]:
                x = {j: Decimal(v) for j, v in r[rate].items()}
                out[k] = {j: _n(prev[j] * x[j]) for j in ("low", "mid", "high")}
                steps.append(f"× {rate.upper()} {x['mid']} = {out[k]['mid']:,} {name} (mid)")
        if out["impressions"] and r["frequency"]:
            f = Decimal(str(r["frequency"]))
            out["unique_reach"] = {j: _n(out["impressions"][j] / f) for j in ("low", "mid", "high")}
            steps.append(f"÷ frequency {f} = {out['unique_reach']['mid']:,} people reached (mid)")
        mn = MIN_DAILY.get(p["platform"], {}).get(a["ccy"]) if a else None
        rows.append({"platform": p["platform"], "label": LABEL[p["platform"]], "allocation": a, "allocation_text": money.display(a),
                     "outputs": out, "calculation": steps, "basis": p["basis"],
                     "below_minimum": {"minimum": {"minor": mn, "ccy": a["ccy"]},
                                       "text": f"{LABEL[p['platform']]}'s daily minimum is {money.display({'minor': mn, 'ccy': a['ccy']})}; "
                                               f"this platform's share is {money.display(a)}"}
                     if mn and a["minor"] < mn else None})
    agg = {}
    for k in ("impressions", "clicks", "conversions", "unique_reach"):
        vals = [r["outputs"][k] for r in rows]
        agg[k] = {j: sum(v[j] for v in vals) for j in ("low", "mid", "high")} if vals and all(vals) else None
    have = sum(1 for r in rows if r["outputs"]["impressions"])
    return {"platforms": rows, "aggregate": agg,
            "note": None if have == len(rows) else f"{have} of {len(rows)} platforms have rates"}


def build(inputs: dict, base: dict) -> dict:
    """Validated inputs → the scenario payload (allocations, MEDIA_SPEND and outputs)."""
    ccy = base["currency"]
    plats = [_platform(p, ccy) for p in inputs.get("platforms") or []]
    if not plats or len({p["platform"] for p in plats}) != len(plats):
        raise Refused(422, "Choose each platform once, at least one.")
    days = inputs.get("horizon_days", base["horizon_days"])
    if not isinstance(days, int) or not 1 <= days <= 365:
        raise Refused(422, "Give the test length in whole days (1–365).")
    t = inputs.get("total_daily")
    total = money.money(t["amount"], t["ccy"]) if t and "amount" in t else t
    if total and total["ccy"] != ccy:
        raise Refused(422, f"Give the daily budget in {ccy}, the test's currency (never converted for you).")
    fixed = {p["platform"]: p["allocation"] for p in plats if p["allocation_by"] == "YOU_SAID"}
    if not total and fixed and len(fixed) == len(plats):
        total = {"minor": sum(a["minor"] for a in fixed.values()), "ccy": ccy}
    if total:
        alloc = split(total, [p["platform"] for p in plats], fixed)
        for p in plats:
            p["allocation"] = alloc[p["platform"]]
    elif fixed:
        raise Refused(422, "Give the total daily budget, or a budget for every platform.")
    scn = {**base, "horizon_days": days, "horizon_by": "YOU_SAID" if "horizon_days" in inputs else base.get("horizon_by", "ASSUMED"),
           "total_daily": total, "total_by": "YOU_SAID" if total else None, "platforms": plats, "version_tag": VERSION}
    scn["media_spend"] = total and {"minor": total["minor"] * days, "ccy": ccy}
    scn["results"] = compute(scn)
    scn["label"] = "SCENARIO · a calculation from the inputs shown, not a forecast"
    return scn


def base_of(pid: str, cid: str) -> dict:
    """What the scenario is about, from the digital draft's outline (or the city when digital has no draft yet)."""
    from .delivery import destinations_for
    dr = db.get_record(f"drf_{pid}_{cid}_digital")
    dg = (dr or {}).get("digital_outline") or {}
    city = markets.CITIES[cid]
    return {"city_id": cid, "method": "digital", "currency": dg.get("currency") or markets.CURRENCY.get(city["country"]) or "USD",
            "horizon_days": ((dg.get("horizon_days") or {}).get("value")) or 14,
            "horizon_by": ((dg.get("horizon_days") or {}).get("state")) or "ASSUMED",
            "channel": dg.get("channel"), "objective": dg.get("objective"),
            "geography": dg.get("geography") or {"city": city["name"], "country": city["country"]},
            "draft": dr and {"id": dr["id"], "version": dr["version"]},
            "default_platforms": destinations_for(dr) if dr else ["META_ADS"]}


def media_line(scn: dict) -> dict:
    total, days = scn["total_daily"], scn["horizon_days"]
    return {"id": plans.line_id(MEDIA), "label": "Media spend", "item_ref": MEDIA, "amount": scn["media_spend"],
            "basis": "YOU_SAID" if total else "NOT_KNOWN", "flow": "expense", "used_by": ["digital"],
            "basis_note": total and f"{money.display(total)} a day × {days} days, split across "
                                    f"{', '.join(LABEL[p['platform']] for p in scn['platforms'])}",
            "source_claim_ids": [], "by": "scenario"}


def save(pid: str, cid: str, base_version: int, scenario_version: int | None, inputs: dict) -> dict:
    """PUT /plans/{city}/scenario: DigitalScenario v+1 and Plan v+1 (the MEDIA_SPEND line and the pin) together.
    Approved packages are unchanged; a package takes the new budget only when it is re-prepared from a signed plan."""
    plan = db.get_record(plans.plan_id(pid, cid))
    if not plan or plans.roles_of(plan).get("digital") not in plans.INCLUDED:
        raise Refused(422, "Add digital to your plan first.")
    scn = build(inputs, base_of(pid, cid))
    sid = scenario_id(pid, cid)
    plans.ensure_fx(pid, [media_line(scn)])  # before the transaction, never inside it

    def change(p):
        p["cost_lines"] = [ln for ln in p.get("cost_lines") or [] if ln.get("item_ref") != MEDIA] + [media_line(scn)]
        return p

    def also(p):
        cur = db.get_record(sid)
        if (cur["version"] if cur else None) != scenario_version:
            raise Refused(409, f"Your scenario changed since you loaded it (now v{cur['version'] if cur else 0}).")
        rec = db.put_record("scenario", pid, scn, inputs=[{"id": p.get("id", plans.plan_id(pid, cid)), "keys": []}], rid=sid)
        p["scenario"] = {"id": rec["id"], "version": rec["version"]}
        return p["scenario"]
    out = plans.write(pid, cid, base_version, change, "Changed the digital scenario",
                      f"{money.display(scn['total_daily']) or 'no daily budget'} · {scn['horizon_days']} days · "
                      + ", ".join(LABEL[p["platform"]] for p in scn["platforms"]), also=also)
    return {"version": out["version"], "scenario": out["also"]}


def of_plan(plan: dict | None) -> dict | None:
    pin = (plan or {}).get("scenario")
    return pin and db.get_record(pin["id"], pin["version"])


def view(pid: str, cid: str, plan: dict | None) -> dict | None:
    """The plan's pinned scenario, or an unsaved starting point, while digital is MAIN or SUPPORTS."""
    if plans.roles_of(plan).get("digital") not in plans.INCLUDED:
        return None
    rec = of_plan(plan)
    base = base_of(pid, cid)
    if rec:
        return {**{k: v for k, v in rec.items() if k not in ("kind", "project_id", "inputs")}, "platforms_offered": list(PLATFORMS),
                "default_platforms": base["default_platforms"]}
    return {"id": None, "version": None, **base, "total_daily": None, "platforms": [], "media_spend": None, "results": None,
            "platforms_offered": list(PLATFORMS), "label": "SCENARIO · a calculation from the inputs shown, not a forecast"}


if __name__ == "__main__":  # the §5.7 worked example (AC5-8), offline
    usd = lambda x: {"minor": x, "ccy": "USD"}  # noqa: E731
    assert split(usd(3000), list(PLATFORMS)) == {p: usd(1000) for p in PLATFORMS}
    assert list(split(usd(3100), list(PLATFORMS)).values()) == [usd(1034), usd(1033), usd(1033)]
    assert split(usd(3000), list(PLATFORMS), {"TIKTOK_ADS": usd(2000)}) == {"META_ADS": usd(500), "GOOGLE_ADS": usd(500), "TIKTOK_ADS": usd(2000)}
    assert split(usd(3000), ["META_ADS", "GOOGLE_ADS"]) == {"META_ADS": usd(1500), "GOOGLE_ADS": usd(1500)}
    try:
        split(usd(3000), list(PLATFORMS), {"META_ADS": usd(2000), "GOOGLE_ADS": usd(2000)})
        raise AssertionError
    except Refused as e:
        assert "more than your total" in e.detail
    base = {"currency": "USD", "horizon_days": 14}
    rng = lambda lo, mid, hi: {"low": lo, "mid": mid, "high": hi}  # noqa: E731
    s = build({"total_daily": {"amount": "30", "ccy": "USD"}, "platforms": [
        {"platform": "META_ADS", "rates": {"cpm": rng("8", "10", "12"), "ctr": rng("0.008", "0.01", "0.012")}},
        {"platform": "GOOGLE_ADS"}, {"platform": "TIKTOK_ADS"}]}, base)
    assert s["media_spend"] == usd(42000) and [p["allocation"] for p in s["platforms"]] == [usd(1000)] * 3
    meta, google, tiktok = s["results"]["platforms"]
    assert meta["outputs"]["impressions"] == rng(11667, 14000, 17500) and meta["outputs"]["clicks"]["mid"] == 140
    assert meta["outputs"]["conversions"] is None and meta["outputs"]["unique_reach"] is None  # no CVR, no frequency
    assert google["outputs"]["impressions"] is None and s["results"]["aggregate"]["impressions"] is None  # never Meta's rates
    assert s["results"]["note"] == "1 of 3 platforms have rates"
    assert tiktok["below_minimum"] and not meta["below_minimum"]
    print("scenarios ok")
