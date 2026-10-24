"""Probes Q1 (brand identity), Q2 (tags), Q3/Q4 (does city discovery change with brand/profile signals?).

Prints summaries only; raw exchanges are kept privately under live-captures-private/probes/qloo/.
"""
import json
import sys

from landfall import config, ledger, qloo
from landfall.cassette import Cassette

cas = Cassette(sys.argv[1] if len(sys.argv) > 1 else "record", config.PRIVATE / "probes" / "qloo")
CITIES = {"Toronto": (-79.3832, 43.6532), "Lisbon": (-9.1393, 38.7223), "Lagos": (3.3792, 6.5244)}


def body(r):
    try:
        return json.loads(r["body"])
    except Exception:
        return {}


def entities(r):
    b = body(r)
    res = b.get("results", b)
    if isinstance(res, dict):
        res = res.get("entities", res.get("tags", []))
    return res if isinstance(res, list) else []


def search(q, types="urn:entity:brand"):
    r = qloo.get("/search", {"query": q, "types": types, "take": 5}, cas, "probe-q1")
    ents = entities(r)
    print(f"\n[search {q!r}] status {r['status']} · {len(ents)} candidates · month remaining {r['headers'].get('x-month-ratelimit-remaining')}")
    for e in ents[:5]:
        props = e.get("properties", {}) or {}
        print(f"  - {e.get('name')!r} types={e.get('types') or e.get('subtype')} props={sorted(props)[:12]} "
              f"website={props.get('website')!r} tags={len(e.get('tags') or [])}")
    return ents


def places(label, city, extra):
    lon, lat = CITIES[city]
    p = {"filter.type": "urn:entity:place", "filter.location": f"POINT({lon} {lat})", "filter.location.radius": 15000, "take": 25, **extra}
    r = qloo.get("/v2/insights", p, cas, "probe-q3")
    ents = entities(r)
    err = "" if r["status"] == 200 else f" body={r['body'][:160]!r}"
    print(f"[places {city} · {label}] status {r['status']} · {len(ents)} places{err}")
    return [e.get("entity_id") for e in ents], ents


def jaccard(a, b):
    return len(set(a) & set(b)) / max(1, len(set(a) | set(b)))


komono = search("Komono")
gotbag = search("GOT BAG")
contrast = search("Glossier")
tags = qloo.get("/v2/tags", {"filter.query": "watches", "take": 5}, cas, "probe-q2")
tag_rows = entities(tags)
print(f"\n[tags 'watches'] status {tags['status']} · rows {len(tag_rows)} · keys {sorted(tag_rows[0]) if tag_rows else None}")
print("  ", [(t.get("name"), t.get("id") or t.get("tag_id")) for t in tag_rows[:5]])

pick = lambda ents, word: next((e for e in ents if word.lower() in (e.get("name") or "").lower()), None)
k, g = pick(komono, "komono"), pick(contrast, "glossier")
if not (k and g):
    sys.exit("probe stopped: needed brand entities not found")
tag_id = (tag_rows[0].get("id") or tag_rows[0].get("tag_id")) if tag_rows else None

summary = {}
for city in ("Toronto", "Lisbon"):
    base, _ = places("no signal", city, {})
    a, a_ents = places("Komono", city, {"signal.interests.entities": k["entity_id"]})
    b, _ = places("Glossier", city, {"signal.interests.entities": g["entity_id"]})
    c, _ = places("tag 'watches'", city, {"signal.interests.tags": tag_id} if tag_id else {})
    summary[city] = {"komono_vs_none": jaccard(a, base), "komono_vs_glossier": jaccard(a, b), "komono_vs_tag": jaccard(a, c),
                     "top5_komono": [e.get("name") for e in a_ents[:5]]}
    if a_ents:
        p0 = a_ents[0]
        print(f"   sample place fields: {sorted(p0)} props={sorted((p0.get('properties') or {}))[:15]}")
        print(f"   query block: {p0.get('query')}")

r = qloo.get("/v2/insights", {"filter.type": "urn:entity:place", "filter.location.query": "Toronto", "take": 25,
                              "signal.interests.entities": k["entity_id"]}, cas, "probe-q3")
print(f"[places Toronto · locality query] status {r['status']} · {len(entities(r))} places")
places("Komono", "Lagos", {"signal.interests.entities": k["entity_id"]})

print("\nOVERLAP (Jaccard of returned place ids, take 25):")
print(json.dumps(summary, indent=1))
print(ledger.report())
