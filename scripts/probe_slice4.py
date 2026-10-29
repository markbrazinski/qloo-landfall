"""Probes for the slice-4 plan (city research), 25 Oct 2026; about 12 Qloo requests, 3 web-grounding calls and up to
9 page fetches, recorded privately in live-captures-private/probes/slice4/.

P4-1 · method-filtered place search: the brand signal plus a method's genre tags in one city (retail, pop-up, community)
P4-2 · audience-signalled places: an audience URN (resolved by name, accepted on a UUID match), alone and with the brand
P4-3 · web grounding for local partners: citations, and whether those pages can be fetched under our fetcher's rules
P4-4 · creators: whether creator profiles from grounding can be fetched at all
"""
import json

from landfall import config, fetch, ledger, markets, qloo
from landfall.cassette import Cassette
from landfall.nova import make_model

OUT = config.PRIVATE / "probes" / "slice4"
cas = Cassette("record", OUT)
UNTUCKIT = "C4438E6F-8EA9-463E-850E-43C5D72C4BDF"
AUDIENCES = [("Fishing", "2D160ADC-1673-4D12-9530-D6E78DE07087"), ("Adrenaline Rushers", "1D7AF41D-8ECF-414C-B6C9-7CA9FA276DBD")]
G = "urn:tag:genre:place:"
METHOD_TAGS = {
    "retail": ["clothing_store", "mens_clothing_store", "boutique", "fashion_accessories_store", "shoe_store"],
    "popup": ["event_venue", "art_gallery", "shopping_mall", "live_music_venue"],
    "community": ["gym", "park", "club"],
}


def places(city, **signal):
    c = markets.CITIES[city]
    params = {"filter.type": "urn:entity:place", "filter.location": f"POINT({c['lon']} {c['lat']})",
              "filter.location.radius": 15000, "take": 25, **signal}
    r = qloo.get("/v2/insights", params, cas, "probe-slice4")
    try:
        ents = json.loads(r.get("body") or "{}")["results"]["entities"]
    except (ValueError, KeyError, TypeError):
        ents = []
    return r["status"], [markets.parse_place(e, i + 1) for i, e in enumerate(ents)]


def genres(p):
    return [g["qloo_id"].split(":place:")[-1] for g in p["genres"]]


print(ledger.report())
print("\nP4-1 · method-filtered places, Untuckit, Minneapolis:")
st, base = places("minneapolis", **{"signal.interests.entities": UNTUCKIT})
base_ids = {p["qloo_id"] for p in base}
print(f"   unfiltered: HTTP {st} · {len(base)} places · {[p['name'] for p in base[:5]]}")
for method, tags in METHOD_TAGS.items():
    st, ps = places("minneapolis", **{"signal.interests.entities": UNTUCKIT, "filter.tags": ",".join(G + t for t in tags)})
    on = sum(1 for p in ps if set(genres(p)) & set(tags))
    print(f"   {method:9}: HTTP {st} · {len(ps)} places · {on} carry a requested genre · {len({p['qloo_id'] for p in ps} & base_ids)} "
          f"also in the unfiltered search · {[(p['name'], genres(p)[:2]) for p in ps[:5]]}")

print("\nP4-2 · audience-signalled places, Minneapolis:")
urns = {}
for name, uuid in AUDIENCES:
    r = qloo.get("/v2/audiences", {"filter.query": name, "take": 5}, cas, "probe-slice4")
    rows = json.loads(r.get("body") or "{}").get("results", {}).get("audiences", []) if r["status"] == 200 else []
    match = [x for x in rows if x.get("entity_id") == uuid]
    urns[name] = match[0]["id"] if len(match) == 1 else None
    print(f"   '{name}': HTTP {r['status']} · {len(rows)} rows · UUID match: {urns[name]}")
for name, urn in urns.items():
    if not urn:
        continue
    st, alone = places("minneapolis", **{"signal.demographics.audiences": urn})
    st2, both = places("minneapolis", **{"signal.demographics.audiences": urn, "signal.interests.entities": UNTUCKIT})
    a, b = {p["qloo_id"] for p in alone}, {p["qloo_id"] for p in both}
    print(f"   {name}: alone HTTP {st} {len(alone)} · with Untuckit HTTP {st2} {len(both)} · overlap alone/brand-only "
          f"{len(a & base_ids)}, with/brand-only {len(b & base_ids)} · alone {[p['name'] for p in alone[:4]]} · with {[p['name'] for p in both[:4]]}")


def ground(question):
    usage = []
    model = make_model(cas, "probe-slice4-grounding", usage.append, max_tokens=500)
    resp = model.client.converse(modelId=config.NOVA_MODEL, messages=[{"role": "user", "content": [{"text": question}]}],
                                 toolConfig={"tools": [{"systemTool": {"name": "nova_grounding"}}]},
                                 inferenceConfig={"maxTokens": 500})
    blocks = resp["output"]["message"]["content"]
    cites = [c["location"]["web"] for b in blocks for c in (b.get("citationsContent") or {}).get("citations", [])
             if "web" in c.get("location", {})]
    text = " ".join(b.get("text", "") for b in blocks if "text" in b)
    return list({c["url"]: c for c in cites}.values()), text, usage


def try_fetch(url):
    try:
        rp = fetch.robots_for(url, cas, "probe-slice4")
        if not fetch.robots_allows(url, rp):
            return "robots disallow"
        page = fetch.get(url, cas, "probe-slice4")
        ex = fetch.extract(page) if page.get("status") == 200 else {}
        return f"HTTP {page.get('status')} {page.get('error') or ''} · {len(ex.get('text') or '')} chars"
    except Exception as e:
        return f"{type(e).__name__}: {str(e)[:80]}"


for label, q, n in (("P4-3 · partners (retail)", "Independent menswear boutiques in Minneapolis that stock outside brands. "
                     "Give names and their websites.", 3),
                    ("P4-3 · partners (pop-up)", "Pop-up retail space or short-term shop rental in Montreal. Give names and websites.", 3),
                    ("P4-4 · creators", "Minneapolis menswear or men's style content creators on Instagram or TikTok. Give names and "
                     "profile links.", 3)):
    cites, text, usage = ground(q)
    print(f"\n{label}: {len(cites)} citations · usage {[u.get('grounding_calls') for u in usage]} grounding · "
          f"answer: {text[:220]!r}")
    for c in cites[:n]:
        print(f"   {c.get('domain')}: {c['url'][:90]} → {try_fetch(c['url'])}")
print("\n" + ledger.report())
