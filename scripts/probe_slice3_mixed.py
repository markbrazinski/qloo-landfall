"""Probe P3-4 (slice-3 plan review, 24 Oct 2026): 1 Qloo request, recorded privately.

Do tag signals and an entity signal together, in the encoding the scan will send, return a 200 whose results
differ from tags alone (P3-1a) and from the entity alone (Q4)? The entity stands in for a ticked comparable.
"""
import json

from landfall import config, ledger, qloo
from landfall.cassette import Cassette

cas = Cassette("record", config.PRIVATE / "probes" / "slice3")
params = {"filter.type": "urn:entity:place", "filter.location": "POINT(-79.3832 43.6532)",
          "filter.location.radius": 15000, "take": 25,
          "signal.interests.tags": "urn:tag:keyword:qloo:sustainability,urn:tag:keyword:qloo:ocean",
          "signal.interests.entities": "DB045A2F-5476-47A0-98FF-B05E3723AF56"}
r = qloo.get("/v2/insights", params, cas, "probe-slice3")
ents = (json.loads(r["body"]).get("results") or {}).get("entities", []) if r["status"] == 200 else []
mixed = {e["entity_id"] for e in ents}


def prior(path, match):
    for x in (config.PRIVATE / "probes" / path / "exchanges.jsonl").read_text().split("\n"):
        if x and json.loads(x)["op"] == "/v2/insights" and match(json.loads(x)["request"]["params"]):
            return {e["entity_id"] for e in json.loads(json.loads(x)["response"]["body"])["results"]["entities"]}


tags = prior("slice3", lambda p: p.get("filter.location") == params["filter.location"] and "filter.tags" not in p
             and "signal.interests.entities" not in p and p.get("signal.interests.tags"))
ent = prior("qloo", lambda p: p.get("filter.location") == params["filter.location"]
            and p.get("signal.interests.entities") == params["signal.interests.entities"])
j = lambda a, b: round(len(a & b) / len(a | b), 3) if a and b else None  # noqa: E731
print(f"HTTP {r['status']} · {len(ents)} places · affinity on {sum(1 for e in ents if 'affinity' in (e.get('query') or {}))}")
print("names:", [e["name"] for e in ents[:10]])
print("Jaccard mixed vs tags-only:", j(mixed, tags), "· vs entity-only:", j(mixed, ent))
print(ledger.report())
