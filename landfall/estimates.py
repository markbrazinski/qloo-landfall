"""Cost estimates for NOT_KNOWN lines (owner, 27 Oct 2026): a labeled, area-adjusted figure shown BESIDE the unknown,
never as if known. A line keeps `amount: None` and `basis: NOT_KNOWN`; the view adds `estimate`.

estimate = US$ planning base × quantity × the country's price level (World Bank, PPP ÷ market rate, US = 1), for items
bought locally; the brand's own goods and their freight are not scaled. Converted with the plan's dated ECB rates into
the city's currency (US$ when no rate is stored, and said so), rounded to two significant figures, low/high ±30%.
The bases are Landfall planning assumptions (data/cost_bases.json), never sourced prices.
"""
import json
from decimal import Decimal

from . import markets, money
from .markets import DATA

BASES = json.loads((DATA / "cost_bases.json").read_text())
LEVELS = json.loads((DATA / "price_levels.json").read_text())
SPREAD = Decimal("0.3")
MEDIA = "digital:MEDIA_SPEND"
# What grows with the budget left under the founder's ceiling (owner, 28 Oct 2026: "if the budget is 10k, then use the
# 10k"): item → (the quantity unit that grows, what the founder reads). Venue hire grows with staff, a day at a time.
LEVERS = {MEDIA: ("day", "the daily ad budget"), "creator:CREATOR_FEE": ("post", "more creator posts"),
          "creator:STOCK": ("units", "more product for creators"), "community:STOCK": ("units", "more product for sampling"),
          "retail:STOCK": ("units", "more stock for the shop"), "popup:HIRE_FEE": ("days", "more pop-up days (venue and staff)")}
TOGETHER = {"popup:HIRE_FEE": ("popup:HIRE_FEE", "popup:STAFF")}
SOURCE = {"title": "World Bank WDI: PPP conversion factor (PA.NUS.PPP) ÷ official exchange rate (PA.NUS.FCRF)",
          "url": "https://data.worldbank.org/indicator/PA.NUS.PPP", "license": LEVELS["license"],
          "retrieved_at": LEVELS["retrieved_at"]}


def _round2(d: Decimal) -> Decimal:
    """Two significant figures: an estimate shouldn't look more precise than it is."""
    if d <= 0:
        return d
    e = d.adjusted() - 1
    return d.scaleb(-e).quantize(Decimal(1)).scaleb(e)


def _num(n) -> str:
    return f"{n:,}"


def _r2(m: dict) -> dict:
    return money.of_major(_round2(money.major(m)), m["ccy"])


def _range(lo: dict, hi: dict) -> str:
    """"US$2,400–4,100": the high end without its currency sign again."""
    return money.display(lo) + "–" + money.display(hi)[len(money.SYMBOL.get(hi["ccy"], hi["ccy"] + " ")):]


def estimate(item_ref: str | None, city_id: str, fx: dict | None = None, days: int | None = None,
             qty: dict[str, int] | None = None) -> dict | None:
    """{amount, low, high, basis_note, sources} in the city's currency, or None (no base for the item, or no price
    level for the country). `days` overrides the media-spend days with the plan's digital scenario; `qty` overrides a
    quantity by its unit (e.g. {"post": 7}) when the plan sizes a lever."""
    base = BASES["bases"].get(item_ref or "")
    city = markets.CITIES.get(city_id)
    if not base or not city:
        return None
    level = LEVELS["values"].get(city["country"])
    if not level:  # no price level for the country: the line stays plain NOT KNOWN
        return None
    over = {**({"days": days} if days and item_ref == MEDIA else {}), **(qty or {})}
    qty = [[over.get(u, q), u] for q, u in base["qty"]]
    n = Decimal(1)
    for q, _ in qty:
        n *= Decimal(q)
    usd = Decimal(base["usd"]) * n
    parts = [" × ".join(f"{_num(q)} {u if q == 1 or u.endswith('s') or u in ('h', 'staff') else u + 's'}" for q, u in qty), f"{money.display(money.of_major(Decimal(base['usd']), 'USD'))}/{base['unit']} {BASES['label']}"]
    sources = [{"title": f"{BASES['label']}: {base['note']}", "kind": "PLANNING_ASSUMPTION"}]
    if base["local"]:
        ratio = Decimal(str(level["ratio"]))
        usd *= ratio
        parts.append(f"{ratio} {markets.COUNTRY_NAME.get(city['country'], city['country'])} price level (World Bank {level['year']})")
        sources.append({**SOURCE, "kind": "SOURCED", "value": level})
    note = "Estimate: " + " × ".join(parts)
    if not base["local"]:
        note += " (the brand's own goods or freight, not scaled by local prices)"
    ccy = markets.CURRENCY.get(city["country"]) or "USD"
    rate = money.usd_rate(ccy, fx)
    if rate is None:
        ccy, rate = "USD", Decimal(1)
        note += f"; in US$, no exchange rate for {markets.CURRENCY.get(city['country'])} is stored yet" if city["country"] != "US" else ""
    elif ccy != "USD":
        note += f"; converted at ECB rates of {fx['rate_date']}"
    mid = _round2(usd / rate)
    lo, hi = _round2(mid * (1 - SPREAD)), _round2(mid * (1 + SPREAD))
    out = {"amount": money.of_major(mid, ccy), "low": money.of_major(lo, ccy), "high": money.of_major(hi, ccy),
           "basis_note": note, "sources": sources, "label": "ESTIMATE · a planning figure, not a quote"}
    return {**out, "text": money.display(out["amount"]), "range_text": _range(out["low"], out["high"])}


def total(known: dict, lines: list[dict], fx: dict | None) -> dict:
    """Known cash out (USD) plus every estimate, with low/high. Lines still unknown or unconverted are counted."""
    mid = lo = hi = known["minor"]
    missing = 0
    for ln in lines:
        e = ln.get("estimate")
        u = e and [money.to_usd(e[k], fx) for k in ("amount", "low", "high")]
        if not u or None in u:
            missing += 1
            continue
        mid, lo, hi = mid + u[0]["minor"], lo + u[1]["minor"], hi + u[2]["minor"]
    out = {"amount": money.usd(mid), "low": money.usd(lo), "high": money.usd(hi), "not_estimated": missing}
    # an estimate reads as one, not to the cent; the amounts stay exact
    return {**out, "text": money.display(_r2(out["amount"])), "range_text": _range(_r2(out["low"]), _r2(out["high"]))}
