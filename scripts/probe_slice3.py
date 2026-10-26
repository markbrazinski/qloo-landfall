"""Probes P3-1..P3-3 (slice-3 planning, 24 Oct 2026): 4 Qloo requests, recorded privately.

P3-1 do GOT BAG's confirmed keyword tags, as signals, shift place results (vs the captured unsignalled requests)?
P3-2 does filter.tags narrow brand-signalled places to a channel (shopping) and keep query.affinity?
P3-3 the same channel filter on the tag-signal (profile) route.
"""
import json

from landfall import config, ledger, qloo
from landfall.cassette import Cassette

cas = Cassette("record", config.PRIVATE / "probes" / "slice3")
TAGS = "urn:tag:keyword:qloo:sustainability,urn:tag:keyword:qloo:ocean"
KOMONO = "DB045A2F-5476-47A0-98FF-B05E3723AF56"
TORONTO, LISBON = "POINT(-79.3832 43.6532)", "POINT(-9.1393 38.7223)"
base = {"filter.type": "urn:entity:place", "filter.location.radius": 15000, "take": 25}


def run(label, params):
    r = qloo.get("/v2/insights", {**base, **params}, cas, "probe-slice3")
    ents = (json.loads(r["body"]).get("results") or {}).get("entities", []) if r["status"] == 200 else []
    genres = {}
    for e in ents:
        for t in e.get("tags") or []:
            if t["type"] == "urn:tag:genre:place" and t["name"] != "Place":
                genres[t["name"]] = genres.get(t["name"], 0) + 1
    aff = [round(e["query"]["affinity"], 3) for e in ents if "affinity" in (e.get("query") or {})]
    print(f"\n[{label}] HTTP {r['status']} · {len(ents)} places · affinity on {len(aff)} {aff[:3]}")
    print("  genres:", sorted(genres.items(), key=lambda x: -x[1])[:8])
    print("  names:", [e["name"] for e in ents[:10]])
    if r["status"] != 200:
        print("  body:", r["body"][:300])
    return [e["entity_id"] for e in ents]


p31t = run("P3-1a GOT BAG tags · Toronto", {"filter.location": TORONTO, "signal.interests.tags": TAGS})
p31l = run("P3-1b GOT BAG tags · Lisbon", {"filter.location": LISBON, "signal.interests.tags": TAGS})
run("P3-2 Komono + shopping filter · Toronto", {"filter.location": TORONTO, "signal.interests.entities": KOMONO,
                                                 "filter.tags": "urn:tag:genre:place:shopping"})
run("P3-3 GOT BAG tags + shopping filter · Toronto", {"filter.location": TORONTO, "signal.interests.tags": TAGS,
                                                       "filter.tags": "urn:tag:genre:place:shopping"})
old = [json.loads(x) for x in (config.PRIVATE / "probes" / "qloo" / "exchanges.jsonl").read_text().split("\n") if x]
def ids(loc, sig):
    for x in old:
        p = x["request"]["params"]
        if x["op"] == "/v2/insights" and p.get("filter.location") == loc and p.get("signal.interests.entities") == sig:
            return [e["entity_id"] for e in json.loads(x["response"]["body"])["results"]["entities"]]
j = lambda a, b: round(len(set(a) & set(b)) / len(set(a) | set(b)), 3) if a and b else None  # noqa: E731
print("\nJaccard vs unsignalled (captured 23 Oct): Toronto", j(p31t, ids(TORONTO, None)), "· Lisbon", j(p31l, ids(LISBON, None)))
print("Jaccard vs Komono-signalled: Toronto", j(p31t, ids(TORONTO, KOMONO)), "· Lisbon", j(p31l, ids(LISBON, KOMONO)))
print("\n" + ledger.report())
