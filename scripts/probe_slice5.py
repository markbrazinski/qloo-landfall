"""Slice 5 probe P5-2 (BUILD-PLAN "Slice 5" §5.11): one fetch of the ECB daily reference XML, parsed, with one cross
rate computed. Recorded privately to live-captures-private/probes/P5-2/; one page fetch under the active authority."""
from decimal import Decimal

from landfall import config, fetch, money
from landfall.cassette import Cassette

out = config.PRIVATE / "probes" / "P5-2"
page = fetch.get(money.ECB_URL, Cassette("record", out), "fx_ecb")
got = money.parse_ecb(page.get("body") or "")
print("status", page.get("status"), "parsed", bool(got))
if got:
    day, rates = got
    fx = {"rates": rates}
    print("rate_date", day, "quotes", len(rates), "USD", rates["USD"], "AUD", rates.get("AUD"), "CAD", rates.get("CAD"))
    print("A$100 →", money.to_usd(money.money("100", "AUD"), fx), "(USD per AUD", (Decimal(rates["USD"]) / Decimal(rates["AUD"])).quantize(Decimal("0.000001")), ")")
