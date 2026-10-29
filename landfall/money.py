"""Slice 5 money (BUILD-PLAN "Slice 5" §5.5; CONTRACTS B7): Money in minor units, ECB reference rates, whole-plan totals
and fit. Deterministic: no model ever writes a figure here, and an unknown or unconverted amount is never zero.

- Money is `{minor, ccy}`. Each line keeps its own currency; totals and fit are in USD, the ceiling's currency.
- X → USD is (USD per EUR) ÷ (X per EUR) from one ECB publication, in Decimal, rounded half-even to the cent per line
  before summing [T2].
- Fit [T1]: no ceiling → NOT_ASSESSED; known cash out above it → OVER_CEILING (even with unknowns); any unknown or
  unconverted line → AFFORDABILITY_UNRESOLVED; otherwise FITS_KNOWN_COSTS (equality fits).
"""
import re
import xml.etree.ElementTree as ET
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from .research import Refused

EXPONENT = {**{c: 0 for c in ("JPY", "KRW", "CLP", "VND", "ISK", "PYG", "UGX")},
            **{c: 3 for c in ("KWD", "BHD", "OMR", "JOD", "TND")}}
FLOWS = ("expense", "inventory", "refundable_deposit")
INCLUDED = ("MAIN", "SUPPORTS")
ASSUMED = ("ASSUMED", "ESTIMATE")


def exp(ccy: str) -> int:
    return EXPONENT.get(ccy, 2)


def money(amount: str, ccy: str) -> dict:
    """Founder text "25" or "25.50" in an ISO currency → {minor, ccy}; never rounded silently."""
    ccy = (ccy or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", ccy):
        raise Refused(422, "Give the currency as a three-letter code, e.g. USD.")
    try:
        d = Decimal(str(amount).replace(",", "").strip())
    except InvalidOperation:
        raise Refused(422, "Give the amount as a number, e.g. 25.")
    minor = d * (10 ** exp(ccy))
    if not d.is_finite() or d <= 0 or minor != minor.to_integral_value():
        raise Refused(422, f"Give the amount in whole {ccy} minor units (at most {exp(ccy)} decimal places).")
    return {"minor": int(minor), "ccy": ccy}


def major(m: dict) -> Decimal:
    return Decimal(m["minor"]) / (10 ** exp(m["ccy"]))


def of_major(d: Decimal, ccy: str) -> dict:
    return {"minor": int((d * (10 ** exp(ccy))).to_integral_value(ROUND_HALF_EVEN)), "ccy": ccy}


def money_text(m: dict | None) -> str:
    if not m:
        return "not set"
    return f"{m['ccy']} {major(m):.{exp(m['ccy'])}f}"


SYMBOL = {"USD": "US$", "AUD": "A$", "CAD": "CA$", "NZD": "NZ$", "EUR": "€", "GBP": "£"}


def display(m: dict | None) -> str | None:
    """"US$1,200" or "A$1,250.50": whole amounts without decimals."""
    if not m:
        return None
    d = major(m)
    body = f"{d:,.0f}" if d == d.to_integral_value() else f"{d:,.{exp(m['ccy'])}f}"
    return f"{SYMBOL[m['ccy']]}{body}" if m["ccy"] in SYMBOL else f"{m['ccy']} {body}"


def minor_to(m: dict, unit_exp: int) -> int:
    """Minor units → another integer unit (Google micros: 6)."""
    return m["minor"] * 10 ** (unit_exp - exp(m["ccy"]))


# ---- prices in page text (S5-13): only an unambiguous currency gives Money ----
PREFIX = {"US$": "USD", "USD": "USD", "A$": "AUD", "AU$": "AUD", "AUD": "AUD", "CA$": "CAD", "C$": "CAD", "CAD": "CAD",
          "NZ$": "NZD", "NZD": "NZD", "€": "EUR", "EUR": "EUR", "£": "GBP", "GBP": "GBP"}
CODES = ("USD AUD CAD NZD EUR GBP CHF SEK NOK DKK PLN CZK JPY CNY HKD SGD KRW INR MXN BRL ZAR AED TWD THB MYR IDR PHP "
         "ILS TRY ARS CLP COP PEN UYU EGP MAD NGN KES GHS SAR QAR VND").split()
_NUM = r"(\d{1,3}(?:[,.\s]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
_PRE = "|".join(re.escape(p) for p in sorted(set(PREFIX) | set(CODES), key=len, reverse=True))
PRICE = re.compile(rf"(?<![A-Za-z$])({_PRE})\s?{_NUM}|{_NUM}\s?({_PRE})(?![A-Za-z])")


def _amount(text: str) -> Decimal | None:
    t = text.replace(" ", "")
    if re.search(r"[.,]\d{1,2}$", t):  # the last separator before 1–2 digits is the decimal point
        whole, frac = t[:-len(re.search(r"[.,](\d{1,2})$", t).group(0))], re.search(r"[.,](\d{1,2})$", t).group(1)
        t = re.sub(r"[.,]", "", whole) + "." + frac
    else:
        t = re.sub(r"[.,]", "", t)
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def prices(text: str) -> list[dict]:
    """Every price in `text` whose currency is unambiguous ("189 EUR", "€189", "US$40"). A bare "$" or "¥" is several
    currencies, so it gives nothing."""
    out = []
    for m in PRICE.finditer(text or ""):
        pre, n1, n2, post = m.groups()
        code = PREFIX.get(pre or post) or (pre or post)
        d = _amount(n1 or n2)
        if code in CODES and d is not None and d > 0:
            out.append(of_major(d, code))
    return out


def parse_price(text: str) -> dict | None:
    got = prices(text)
    return got[0] if got else None


# ---- ECB reference rates (D4, §5.5) ----
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"


def parse_ecb(xml: str) -> tuple[str, dict[str, str]] | None:
    """The daily reference XML → (rate_date, {ccy: units per EUR}); None when it isn't that document."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    day = next((c for c in root.iter() if c.get("time")), None)
    if day is None:
        return None
    rates = {c.get("currency"): c.get("rate") for c in day if c.get("currency") and c.get("rate")}
    return (day.get("time"), rates) if rates.get("USD") else None


def usd_rate(ccy: str, fx: dict | None) -> Decimal | None:
    """USD per one unit of `ccy` from one publication (`fx.rates` are units per EUR)."""
    if ccy == "USD":
        return Decimal(1)
    if not fx or "USD" not in fx["rates"]:
        return None
    if ccy == "EUR":
        return Decimal(fx["rates"]["USD"])
    if ccy not in fx["rates"]:
        return None
    return Decimal(fx["rates"]["USD"]) / Decimal(fx["rates"][ccy])


def to_usd(m: dict, fx: dict | None) -> dict | None:
    r = usd_rate(m["ccy"], fx)
    return None if r is None else of_major(major(m) * r, "USD")


# ---- whole-plan totals and fit (§5.5) ----
def counts(line: dict, roles: dict[str, str]) -> bool:
    """A line counts once while any of the methods using it is MAIN or SUPPORTS."""
    return any(roles.get(m) in INCLUDED for m in line["used_by"])


def fit_of(cash_out_minor: int, ceiling: dict | None, unresolved: bool) -> str:
    if not ceiling:
        return "NOT_ASSESSED"
    if cash_out_minor > ceiling["minor"]:
        return "OVER_CEILING"
    return "AFFORDABILITY_UNRESOLVED" if unresolved else "FITS_KNOWN_COSTS"


def usd(minor: int) -> dict:
    return {"minor": minor, "ccy": "USD"}


def totals(lines: list[dict], roles: dict[str, str], fx: dict | None, ceiling: dict | None) -> dict:
    """Known amounts by flow (USD), the unknown and unconverted lines (counted, never summed), and fit."""
    known = dict.fromkeys(FLOWS, 0)
    assumed, unknown, unconverted = 0, [], []
    for ln in lines:
        if not counts(ln, roles):
            continue
        if ln["amount"] is None:
            unknown.append(ln["id"])
            continue
        u = to_usd(ln["amount"], fx)
        if u is None:
            unconverted.append(ln["id"])
            continue
        known[ln["flow"]] += u["minor"]
        assumed += u["minor"] if ln["basis"] in ASSUMED else 0
    cash = sum(known.values())
    left = not unknown and not unconverted
    out = {"expense_known": usd(known["expense"]), "inventory_known": usd(known["inventory"]),
           "deposits_known": usd(known["refundable_deposit"]), "cash_out_known": usd(cash), "assumed_known": usd(assumed),
           "unknown": unknown, "unconverted": unconverted, "ceiling": ceiling,
           "fit": fit_of(cash, ceiling, not left),
           "remaining": usd(ceiling["minor"] - cash) if ceiling and left and cash <= ceiling["minor"] else None}
    out["text"] = total_text(out)
    return out


def total_text(t: dict) -> str:
    """"US$3,200 + 3 not known", never $0 for what isn't known."""
    n, c = len(t["unknown"]), len(t["unconverted"])
    parts = [display(t["cash_out_known"])] if t["cash_out_known"]["minor"] or not (n or c) else []
    parts += [f"{n} not known"] * bool(n) + [f"{c} not converted"] * bool(c)
    text = " + ".join(parts) if parts else "Nothing costed"
    if t["assumed_known"]["minor"]:
        text += f", of which {display(t['assumed_known'])} assumed"
    return text


# ---- return estimate [T3]: gross receipts, never profit ----
DIRECT = ("popup", "community", "creator", "digital")


def gross_receipts(est: dict | None, roles: dict[str, str], fx: dict | None) -> dict | None:
    """units × sell-through × unit price: the retail price when any included method sells to customers directly, the
    wholesale price when retail (stockists) is the only one. Any missing input → None (the strip shows the success
    measure only). One estimate per plan, so units shared by several methods count once."""
    inc = [m for m, r in roles.items() if r in INCLUDED]
    if not est or not inc:
        return None
    kind = "retail" if any(m in DIRECT for m in inc) else "wholesale"
    price, st = est.get(f"{kind}_price"), est.get("sell_through") or {}
    if not est.get("units") or not price or st.get("low") is None or st.get("high") is None:
        return None
    low, high = (of_major(major(price) * est["units"] * Decimal(str(st[k])), price["ccy"]) for k in ("low", "high"))
    return {"low": low, "high": high, "price": kind,
            "usd": {"low": to_usd(low, fx), "high": to_usd(high, fx)},
            "label": "gross receipts if it sells through, not profit"}


# ---- the founder's return without a sell-through (owner, 28 Oct 2026: no default share of the cost) ----
SELL_THROUGH = Decimal("0.5")  # share of the units sold during the test
REPLACE = "add your sell-through to replace it"


def _whole(m: dict) -> str:
    return display(usd(round(m["minor"] / 100) * 100))


def assumed_return(planned: dict, units: int | None, price: dict | None, price_why: str, units_why: str,
                   sell_through: float | None) -> dict | None:
    """Never a forecast: units × a USD unit price × sell-through, only when units and a price are known (None
    otherwise: no share of the planned cost). `price_why`/`units_why` say where each came from; sell-through is the
    founder's midpoint or 50%."""
    if not (units and price):
        return None
    st = Decimal(str(sell_through)) if sell_through is not None else SELL_THROUGH
    amt = of_major(major(price) * units * st, "USD")
    note = (f"assumed {units:,} units ({units_why}) × {display(price)} ({price_why}) × {st * 100:.0f}% sold"
            f"{' (you said)' if sell_through is not None else ''} during the test; {REPLACE if sell_through is None else ''}").rstrip("; ")
    return {"amount": amt, "basis": "UNITS_PRICE", "label": "ASSUMED", "text": f"≈ {_whole(amt)} back", "note": note}


def if_it_works(planned: dict, low: dict | None, high: dict | None, whose: str) -> str | None:
    """"Net ≈ US$6,860 after the assumed return": the planned cost less the return (USD), a range for the founder's."""
    if not planned["minor"] or not low or not high:
        return None
    a, b = planned["minor"] - high["minor"], planned["minor"] - low["minor"]
    if b <= 0:
        return f"Covers the planned cost after {whose}"
    rng = _whole(usd(max(a, 0))) + ("" if a == b else "–" + _whole(usd(b))[3:])  # [3:] drops "US$"
    return f"Net ≈ {rng} after {whose}"


if __name__ == "__main__":  # ponytail: the self-check; the slice-5 tests cover the plan paths
    fx = {"rates": {"USD": "1.0800", "AUD": "1.6500", "JPY": "160.00"}}
    assert to_usd(money("165", "AUD"), fx) == usd(10800)  # 165 × 1.08 / 1.65 = 108.00
    assert to_usd(money("100", "EUR"), fx) == usd(10800) and to_usd(money("1", "JPY"), fx) == usd(1)  # 0.00675 → 0.01
    assert to_usd(money("1", "GBP"), fx) is None
    assert parse_price("Price 189 EUR incl. VAT") == {"minor": 18900, "ccy": "EUR"} and parse_price("$40") is None
    assert prices("A$1,250.50 or €12,50") == [{"minor": 125050, "ccy": "AUD"}, {"minor": 1250, "ccy": "EUR"}]
    ln = lambda i, a, f="expense", u=("popup",), b="YOU_SAID": {"id": i, "amount": a, "flow": f, "used_by": list(u), "basis": b}  # noqa: E731
    roles = {"popup": "MAIN", "community": "SUPPORTS"}
    t = totals([ln("a", usd(10000)), ln("b", None)], roles, None, usd(10000))
    assert t["fit"] == "AFFORDABILITY_UNRESOLVED" and t["text"] == "US$100 + 1 not known" and t["remaining"] is None
    assert totals([ln("a", usd(10000))], roles, None, usd(10000))["fit"] == "FITS_KNOWN_COSTS"
    assert totals([ln("a", usd(10001)), ln("b", None)], roles, None, usd(10000))["fit"] == "OVER_CEILING"
    assert totals([ln("a", usd(1))], roles, None, None)["fit"] == "NOT_ASSESSED"
    assert totals([ln("a", money("5", "GBP"))], roles, fx, usd(100))["unconverted"] == ["a"]
    assert totals([ln("a", usd(500), "refundable_deposit"), ln("b", usd(100), "inventory", ("x",))], roles, None, None)[
        "expense_known"] == usd(0)
    assert assumed_return(usd(980000), None, None, "", "", None) is None  # no default share of the cost (owner, 28 Oct)
    b = assumed_return(usd(980000), 100, usd(800), "you said", "you said", None)
    assert b["amount"] == usd(40000) and b["basis"] == "UNITS_PRICE" and "50% sold" in b["note"]
    assert if_it_works(usd(1000), usd(500), usd(2000), "x") == "Net ≈ US$0–5 after x"
    assert if_it_works(usd(1000), usd(2000), usd(3000), "x") == "Covers the planned cost after x"
    assert assumed_return(usd(0), None, None, "", "", None) is None
    print("money ok")
