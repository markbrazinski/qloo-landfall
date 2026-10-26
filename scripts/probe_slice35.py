"""Probes for the slice-3.5 plan (25 Oct 2026): at most 7 Qloo requests, recorded privately.

1. Can one Qloo request rank localities (cities) for a brand, so cities are compared inside one request (allowed)
   rather than across separate requests (not allowed)? Is that ranking brand-sensitive, or just popularity?
2. Does Qloo hold a brand's own stores as place entities (to leave out where the brand already is)?
3. Does Qloo return audience entities for a brand?
"""
import json
import sys

from landfall import config, ledger, qloo
from landfall.cassette import Cassette

UNTUCKIT, KOMONO = "C4438E6F-8EA9-463E-850E-43C5D72C4BDF", "DB045A2F-5476-47A0-98FF-B05E3723AF56"
cas = Cassette("record", config.PRIVATE / "probes" / "slice3_5")


def ask(label, path, params):
    r = qloo.get(path, params, cas, "probe-slice3.5")
    try:
        body = json.loads(r.get("body") or "{}")
    except ValueError:
        body = {}
    res = body.get("results")
    ents = res if isinstance(res, list) else (res or {}).get("entities", []) if isinstance(res, dict) else []
    print(f"\n== {label}: HTTP {r['status']} · {len(ents)} results")
    if r["status"] != 200:
        print("  ", (r.get("body") or "")[:300])
    for e in ents[:12]:
        p = e.get("properties") or {}
        geo = p.get("geocode") or {}
        print(f"   {e.get('name')!r:42} {e.get('subtype') or e.get('types')} "
              f"{'aff=%.3f' % e['query']['affinity'] if (e.get('query') or {}).get('affinity') is not None else ''} "
              f"pop={e.get('popularity')} {geo.get('city') or ''} {geo.get('country_code') or ''}")
    return ents


loc = {}
for label, sig in (("locality · Untuckit", UNTUCKIT), ("locality · Komono", KOMONO), ("locality · no signal", None)):
    params = {"filter.type": "urn:entity:locality", "take": 50}
    if sig:
        params["signal.interests.entities"] = sig
    loc[label] = ask(label, "/v2/insights", params)
ask("own stores as places · UNTUCKit", "/search", {"query": "UNTUCKit", "types": "urn:entity:place", "take": 25})
ask("own stores as places · Komono", "/search", {"query": "Komono", "types": "urn:entity:place", "take": 25})
ask("audiences · Untuckit", "/v2/insights", {"filter.type": "urn:audience", "signal.interests.entities": UNTUCKIT, "take": 10})


def order(ents):
    return [e.get("entity_id") for e in ents]


u, k, n = (order(loc[x]) for x in loc)
for a, b, name in ((u, n, "Untuckit vs no signal"), (k, n, "Komono vs no signal"), (u, k, "Untuckit vs Komono")):
    common = [x for x in a if x in b]
    if len(common) >= 5:
        ra, rb = {x: i for i, x in enumerate(a)}, {x: i for i, x in enumerate(b)}
        m = len(common)
        ranks_a = sorted(common, key=lambda x: ra[x])
        pos_b = sorted(common, key=lambda x: rb[x])
        d2 = sum((ranks_a.index(x) - pos_b.index(x)) ** 2 for x in common)
        rho = 1 - 6 * d2 / (m * (m * m - 1))
        print(f"\n{name}: {m} common of {len(a)}/{len(b)} · Spearman on common {rho:.2f} · "
              f"Jaccard {len(set(a) & set(b)) / len(set(a) | set(b)):.2f}")
    else:
        print(f"\n{name}: only {len(common)} common")
print("\n" + ledger.report())
sys.exit(0)
