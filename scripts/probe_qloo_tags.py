"""Probe Q2b (slice-2 planning): how can a brand with no Qloo match be described in Qloo terms?

At most 5 Qloo requests. Prints summaries; raw exchanges stay private under live-captures-private/probes/qloo-tags/.
"""
import json

from landfall import config, ledger, qloo
from landfall.cassette import Cassette

cas = Cassette("record", config.PRIVATE / "probes" / "qloo-tags")


def show(label, path, params):
    r = qloo.get(path, params, cas, "probe-q2b")
    try:
        b = json.loads(r["body"])
    except Exception:
        b = {}
    res = b.get("results", b)
    rows = res.get("tags") or res.get("entities") if isinstance(res, dict) else res
    rows = rows or []
    print(f"\n[{label}] {path} {params} → {r['status']} · {len(rows)} rows")
    for x in rows[:6]:
        tags = x.get("tags") or []
        print(f"   {x.get('name')!r} id={x.get('tag_id') or x.get('id') or x.get('entity_id')} type={x.get('type') or x.get('types')}"
              + (f" · {len(tags)} tags e.g. {[t.get('name') for t in tags[:5]]}" if tags else ""))
    if not rows:
        print("   body:", r["body"][:200])


show("tag search, singular", "/v2/tags", {"filter.query": "watch", "take": 5})
show("tag search, typed", "/v2/tags", {"filter.query": "tea", "filter.tag.types": "urn:tag:genre:brand", "take": 5})
show("tag search, keyword", "/v2/tags", {"filter.query": "recycled", "take": 5})
show("comparable brands by words", "/search", {"query": "recycled backpacks", "types": "urn:entity:brand", "take": 5})
show("comparable brands by category", "/search", {"query": "sparkling tea", "types": "urn:entity:brand", "take": 5})
print("\n" + ledger.report())
