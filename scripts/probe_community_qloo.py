"""Probes for Qloo-anchored community and creator investigations (27 Oct 2026), recorded privately in
live-captures-private/probes/community-qloo/. Montreal, for Death Wish Coffee and UNTUCKit:

A · `urn:audience` with `filter.parents.types` (communities, hobbies_and_interests): is the parent filter applied?
B · `urn:heatmap` with `filter.location.query` (geohash cells): cells and their spread.
C · `urn:heatmap` with `output.heatmap.boundary=urn:entity:locality`: named neighbourhoods per city?
D · `/v2/audiences?filter.query=<name>` → the URN of the top audience.
E · `urn:entity:place` with `signal.demographics.audiences=<URN>` near the city: host venues, geocode names, affinities.
F · `urn:tag` with the brand and `signal.location` (the city point): local themes.
About 12 Qloo requests. Run: LANDFALL_DB=.landfall/community.db uv run python -m scripts.probe_community_qloo
"""
import json

from landfall import config, ledger, markets, qloo
from landfall.cassette import Cassette

OUT = config.PRIVATE / "probes" / "community-qloo"
cas = Cassette("record", OUT)
print(ledger.report())
city = markets.CITIES["montreal"]
POINT = f"POINT({city['lon']} {city['lat']})"
BRANDS = {"Death Wish Coffee": "8E104A95-D942-4B31-88AC-07B26D1DE219", "Untuckit": "C4438E6F-8EA9-463E-850E-43C5D72C4BDF"}


def body(r):
    try:
        return json.loads(r.get("body") or "{}")
    except ValueError:
        return {}


for brand, bid in BRANDS.items():
    print(f"\n==== {brand}")
    r = qloo.get("/v2/insights", {"filter.type": "urn:audience", "signal.interests.entities": bid, "take": 10,
                                  "filter.parents.types": "urn:audience:communities,urn:audience:hobbies_and_interests"},
                 cas, "probe-community")
    ents = (body(r).get("results") or {}).get("entities") or []
    print(f"A · HTTP {r['status']} · {len(ents)} audiences:",
          [(e.get("name"), e.get("disambiguation"), round((e.get("query") or {}).get("affinity") or 0, 3)) for e in ents])
    r = qloo.get("/v2/insights", {"filter.type": "urn:heatmap", "filter.location.query": "Montreal",
                                  "signal.interests.entities": bid}, cas, "probe-community")
    hm = (body(r).get("results") or {}).get("heatmap") or []
    top = sorted(hm, key=lambda c: -c["query"]["affinity"])[:5]
    print(f"B · HTTP {r['status']} · {len(hm)} cells · locality {[(x.get('name')) for x in ((body(r).get('query') or {}).get('localities') or {}).get('filter', [])]}"
          f" · distinct affinities {len({c['query']['affinity'] for c in hm})} · top {[(c['location']['latitude'], c['location']['longitude'], round(c['query']['affinity'], 3), round(c['query']['popularity'], 3)) for c in top]}")
    r = qloo.get("/v2/insights", {"filter.type": "urn:heatmap", "filter.location.query": "Montreal",
                                  "output.heatmap.boundary": "urn:entity:locality", "signal.interests.entities": bid},
                 cas, "probe-community")
    hm = (body(r).get("results") or {}).get("heatmap") or []
    print(f"C · HTTP {r['status']} · {len(hm)} rows · first {json.dumps(hm[:3])[:900]}")
    if not ents:
        continue
    name = ents[0]["name"]
    r = qloo.get("/v2/audiences", {"filter.query": name, "take": 5}, cas, "probe-community")
    rows = (body(r).get("results") or {}).get("audiences") or []
    urn = next((x["id"] for x in rows if x.get("entity_id") == ents[0]["entity_id"]), None)
    print(f"D · HTTP {r['status']} · {name} → {urn}")
    if urn:
        r = qloo.get("/v2/insights", {"filter.type": "urn:entity:place", "filter.location": POINT,
                                      "filter.location.radius": 15000, "take": 25, "signal.demographics.audiences": urn},
                     cas, "probe-community")
        ps = (body(r).get("results") or {}).get("entities") or []
        print(f"E · HTTP {r['status']} · {len(ps)} places · distinct {len({(p.get('query') or {}).get('affinity') for p in ps})} ·",
              [(p["name"], ((p.get("properties") or {}).get("geocode") or {}).get("name"), (p.get("properties") or {}).get("neighborhood"),
                round((p.get("query") or {}).get("affinity") or 0, 3)) for p in ps[:10]])
    r = qloo.get("/v2/insights", {"filter.type": "urn:tag", "signal.interests.entities": bid, "signal.location": POINT,
                                  "take": 20}, cas, "probe-community")
    tags = (body(r).get("results") or {}).get("tags") or []
    print(f"F · HTTP {r['status']} · {len(tags)} tags:", [(t.get("name"), t.get("subtype")) for t in tags])
print("\n" + ledger.report())
