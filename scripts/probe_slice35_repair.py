"""Probes for the slice-3.5 plan repairs (REVIEW-SLICE-035-PLAN, 25 Oct 2026); about 15 Qloo requests, recorded privately.

P35-12 · the destination table: retry the unresolved cities, and rank duplicate entities side by side in one request
P35-13 · audiences and demographics through GOT BAG's stand-ins (row shapes)
brand_genres@1 · Qloo brand-genre ids for non-apparel products (the stand-in genre map)
"""
import json

from landfall import config, ledger, markets, qloo
from landfall.cassette import Cassette

OUT = config.PRIVATE / "probes" / "slice3_5_open"
cas = Cassette("record", OUT)
KOMONO = "DB045A2F-5476-47A0-98FF-B05E3723AF56"
STANDINS = None  # read from the recorded stand-in response below


def get(path, params):
    r = qloo.get(path, params, cas, "probe-slice3.5-repair")
    try:
        b = json.loads(r.get("body") or "{}")
    except ValueError:
        b = {}
    res = b.get("results")
    rows = res if isinstance(res, list) else (res or {}).get("entities") or (res or {}).get("tags") or \
        (res or {}).get("demographics") or (res or {}).get("audiences") or [] if isinstance(res, (dict, list)) else []
    return r["status"], rows, r


print(ledger.report())
print("\nP35-12 · retries for the unresolved cities:")
for cid, q in (("wellington", "Wellington New Zealand"), ("bengaluru", "Bangalore"), ("lima", "Lima Peru"),
               ("sao-paulo", "Sao Paulo Brazil")):
    c = markets.CITIES[cid]
    st, rows, _ = get("/search", {"query": q, "types": "urn:entity:destination", "take": 5})
    found = []
    for e in rows:
        loc = e.get("location") or {}
        d = markets.km(c["lat"], c["lon"], loc["lat"], loc["lon"]) if loc.get("lat") is not None else None
        found.append((e.get("name"), e.get("entity_id"), d and round(d), e.get("popularity")))
    print(f"   {cid:10} '{q}': HTTP {st} · {[(n, d, p and round(p, 2)) for n, _, d, p in found]}")

print("\nP35-12 · duplicate destinations side by side (one request, Komono):")
cand = json.loads((OUT / "destinations-candidate.json").read_text())
rec = [json.loads(x) for x in (OUT / "exchanges.jsonl").read_text().split("\n") if x]
dups = {}
for cid in ("amsterdam", "new-york", "helsinki", "vienna"):
    ex = next(e for e in rec if e["request"]["params"].get("types") == "urn:entity:destination"
              and e["request"]["params"].get("query") == markets.CITIES[cid]["name"])
    dups[cid] = [(e["name"], e["entity_id"]) for e in json.loads(ex["response"]["body"])["results"][:2]]
ids = [i for v in dups.values() for _, i in v]
st, rows, _ = get("/v2/insights", {"filter.type": "urn:entity:destination", "signal.interests.entities": KOMONO,
                                   "filter.results.entities": ",".join(ids), "take": 20})
aff = {e["entity_id"]: (e.get("query") or {}).get("affinity") for e in rows}
for cid, pair in dups.items():
    print(f"   {cid:10} " + " | ".join(f"{n} {i[:8]} aff={aff.get(i) and round(aff[i], 4)} "
                                       f"{'(table pick)' if (cand.get(cid) or {}).get('qloo_id') == i else ''}" for n, i in pair))

print("\nP35-13 · audiences and demographics through the stand-ins:")
fashion = next(e for e in rec if e["request"]["params"].get("filter.tags") == "urn:tag:genre:brand:fashion")
stand = [e["entity_id"] for e in json.loads(fashion["response"]["body"])["results"]["entities"][:3]]
st, rows, r = get("/v2/insights", {"filter.type": "urn:audience", "signal.interests.entities": ",".join(stand), "take": 10})
print(f"   audiences: HTTP {st} · {[(a.get('name'), round((a.get('query') or {}).get('affinity') or 0, 3)) for a in rows]}")
st, rows, r = get("/v2/insights", {"filter.type": "urn:demographics", "signal.interests.entities": ",".join(stand)})
print(f"   demographics: HTTP {st} · {len(rows)} rows")
for d in rows:
    print(f"      {d.get('entity_id') or d.get('tag_id') or d.get('id')}: {json.dumps(d.get('query'))[:300]}")

print("\nbrand_genres@1 · brand genre tags:")
for q in ("beverage", "drinks", "beauty", "home", "food", "apparel", "accessories"):
    st, rows, _ = get("/v2/tags", {"filter.query": q, "filter.tag.types": "urn:tag:genre:brand", "take": 5})
    print(f"   '{q}': HTTP {st} · {[(t.get('id') or t.get('tag_id'), t.get('name')) for t in rows][:5]}")
print("\n" + ledger.report())
