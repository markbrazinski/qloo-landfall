"""Probe for REVIEW-SLICE-04-PLAN P4-R1 (25 Oct 2026): the exact Qloo combinations slice 4 will send, with method genre
filters, for both signal routes. About 6 Qloo requests, recorded privately in live-captures-private/probes/slice4/.

matched (Untuckit, Minneapolis): brand + retail genres (P4-1 repeat, cache), brand + audience URN + retail genres
no match (GOT BAG, Brisbane):   profile tags + retail genres; audience URN + the three stand-ins + retail genres;
                                audience URN alone + retail genres
"""
import json

from landfall import config, ledger, markets, qloo
from landfall.cassette import Cassette

cas = Cassette("record", config.PRIVATE / "probes" / "slice4")
G = "urn:tag:genre:place:"
FASHION = ",".join(G + t for t in ("clothing_store", "mens_clothing_store", "womens_clothing_store", "boutique",
                                   "fashion_accessories_store", "shoe_store"))
UNTUCKIT = "C4438E6F-8EA9-463E-850E-43C5D72C4BDF"
STANDINS = "AE83AFF1-3795-4497-A6C8-3A20AE8C7428,292ABE3A-D479-4CDA-9D49-8289FB64235C,A391970A-A241-4346-AAD3-E42BD6B4B3DE"
TAGS = "urn:tag:keyword:qloo:sustainability,urn:tag:keyword:qloo:ocean"
SUSTAINABILITY = ("Sustainability", "109574B3-A21D-4935-AC94-1DC3C7B56BEC")


def places(city, label, **signal):
    c = markets.CITIES[city]
    params = {"filter.type": "urn:entity:place", "filter.location": f"POINT({c['lon']} {c['lat']})",
              "filter.location.radius": 15000, "take": 25, "filter.tags": FASHION, **signal}
    r = qloo.get("/v2/insights", params, cas, "probe-slice4-r1")
    try:
        ents = json.loads(r.get("body") or "{}")["results"]["entities"]
    except (ValueError, KeyError, TypeError):
        ents = None
    rows = [markets.parse_place(e, i + 1) for i, e in enumerate(ents or [])]
    aff = {(e.get("query") or {}).get("affinity") for e in ents or []}
    print(f"   {label}: HTTP {r['status']} · {len(rows)} places · {len(aff)} distinct affinities · "
          f"{[(p['name'], [g['qloo_id'].split(':place:')[-1] for g in p['genres']][:2]) for p in rows[:5]]}")
    return {p["qloo_id"] for p in rows}


print(ledger.report())
r = qloo.get("/v2/audiences", {"filter.query": SUSTAINABILITY[0], "take": 5}, cas, "probe-slice4-r1")
rows = json.loads(r.get("body") or "{}").get("results", {}).get("audiences", []) if r["status"] == 200 else []
match = [x for x in rows if x.get("entity_id") == SUSTAINABILITY[1]]
urn = match[0]["id"] if len(match) == 1 else None
print(f"'Sustainability' URN: HTTP {r['status']} · {len(rows)} rows · UUID match → {urn}")
fishing = "urn:audience:hobbies_and_interests:fishing"  # resolved by UUID match in P4-2

print("\nmatched · Untuckit · Minneapolis · retail genres:")
a = places("minneapolis", "brand", **{"signal.interests.entities": UNTUCKIT})
b = places("minneapolis", "brand + audience (Fishing)", **{"signal.interests.entities": UNTUCKIT,
                                                          "signal.demographics.audiences": fishing})
print(f"   overlap: {len(a & b)} of {len(b)}")
print("\nno match · GOT BAG · Brisbane · retail genres:")
t = places("brisbane", "profile tags", **{"signal.interests.tags": TAGS})
if urn:
    s = places("brisbane", "audience + stand-ins", **{"signal.demographics.audiences": urn, "signal.interests.entities": STANDINS})
    o = places("brisbane", "audience alone", **{"signal.demographics.audiences": urn})
    print(f"   overlap tags/stand-ins+audience {len(t & s)}, tags/audience-alone {len(t & o)}, stand-ins/alone {len(s & o)}")
print("\n" + ledger.report())

# Follow-up (same day): the two remaining combinations the plan would choose between.
print("\nfollow-up:")
s2 = places("brisbane", "GOT BAG stand-ins alone", **{"signal.interests.entities": STANDINS})
u2 = places("minneapolis", "Untuckit audience (Fishing) alone", **{"signal.demographics.audiences": fishing})
print("\n" + ledger.report())
