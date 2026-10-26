"""Slice-3.5 open probes (25 Oct 2026), recorded privately; about 90 Qloo requests.

R3 · resolve the 80 catalog cities to Qloo destination ids (the one-time `destinations@1` build; candidate table only)
P35-10 · rank all 80 cities for Komono in one query (pages 1–2)
P35-9 · rank them for GOT BAG's tags (no-match route), alone and with one comparable brand
P35-11 · one-request audience UUID → URN lookup
P35-8 · own-store precision: brands with stores, and a brand without (name collision)
"""
import json
import unicodedata

from landfall import config, ledger, markets, qloo
from landfall.cassette import Cassette

OUT = config.PRIVATE / "probes" / "slice3_5_open"
cas = Cassette("record", OUT)
KOMONO = "DB045A2F-5476-47A0-98FF-B05E3723AF56"
GOTBAG_TAGS = "urn:tag:keyword:qloo:sustainability,urn:tag:keyword:qloo:ocean"


def get(path, params):
    r = qloo.get(path, params, cas, "probe-slice3.5-open")
    try:
        b = json.loads(r.get("body") or "{}")
    except ValueError:
        b = {}
    return r["status"], b, r


def rows(b):
    res = b.get("results")
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        return res.get("entities") or res.get("audiences") or []
    return []


def fold(s):
    return "".join(ch for ch in unicodedata.normalize("NFKD", s or "").casefold() if ch.isalnum())


def spearman(a, b):
    common = [x for x in a if x in b]
    m = len(common)
    if m < 5:
        return None, m
    ra = {x: i for i, x in enumerate(x for x in a if x in common)}
    rb = {x: i for i, x in enumerate(x for x in b if x in common)}
    d2 = sum((ra[x] - rb[x]) ** 2 for x in common)
    return 1 - 6 * d2 / (m * (m * m - 1)), m


print(ledger.report())

# ---- R3: the destination table ----
table, flags = {}, []
for cid in markets.ORDER:
    c = markets.CITIES[cid]
    st, b, _ = get("/search", {"query": c["name"], "types": "urn:entity:destination", "take": 3})
    cands = []
    for e in rows(b):
        loc = e.get("location") or {}
        geo = (e.get("properties") or {}).get("geocode") or {}
        d = markets.km(c["lat"], c["lon"], loc["lat"], loc["lon"]) if loc.get("lat") is not None else None
        cands.append({"id": e.get("entity_id"), "name": e.get("name"), "km": d and round(d),
                      "cc": geo.get("country_code"), "pop": e.get("popularity")})
    ok = [x for x in cands if x["km"] is not None and x["km"] <= 60]
    pick = min(ok, key=lambda x: x["km"]) if ok else None
    if pick is None:
        flags.append((cid, "no destination within 60 km", cands))
    elif len(ok) > 1:
        flags.append((cid, f"{len(ok)} destinations within 60 km", ok))
    elif fold(pick["name"]) != fold(c["name"]):
        flags.append((cid, f"name differs: {pick['name']}", [pick]))
    table[cid] = pick and {"qloo_id": pick["id"], "name": pick["name"], "km": pick["km"], "country": pick["cc"]}
(OUT / "destinations-candidate.json").write_text(json.dumps(table, indent=1, ensure_ascii=False))
print(f"\nR3: {sum(1 for v in table.values() if v)} of 80 resolved; {len(flags)} need a look:")
for cid, why, cs in flags:
    print(f"   {cid:18} {why} · {[(x['name'], x['km'], x['cc']) for x in cs][:3]}")

ids = [v["qloo_id"] for v in table.values() if v]
back = {v["qloo_id"]: cid for cid, v in table.items() if v}


def rank(label, signal):
    out = []
    for page in (1, 2):
        st, b, r = get("/v2/insights", {"filter.type": "urn:entity:destination", **signal,
                                        "filter.results.entities": ",".join(ids), "take": 50, "page": page})
        rs = rows(b)
        print(f"   {label} page {page}: HTTP {st} · {len(rs)} rows" + (f" · {(r.get('body') or '')[:160]}" if st != 200 else ""))
        out += rs
        if st != 200 or len(rs) < 50:
            break
    affs = [round((e.get("query") or {}).get("affinity") or 0, 4) for e in out]
    order = [back.get(e.get("entity_id"), e.get("name")) for e in out]
    pops = [e.get("popularity") or 0 for e in out]
    rho_pop, _ = spearman(order, [o for _, o in sorted(zip(pops, order), key=lambda t: -t[0])])
    print(f"   {label}: {len(out)} of {len(ids)} ranked · distinct affinities {len(set(affs))} · "
          f"Spearman vs popularity {rho_pop and round(rho_pop, 2)}")
    print(f"   top 10: {order[:10]}")
    missing = [cid for cid in back.values() if cid not in order]
    if missing:
        print(f"   not returned: {missing}")
    return order


print("\nP35-10 · Komono over all 80:")
k = rank("Komono", {"signal.interests.entities": KOMONO})
print("\nP35-9 · GOT BAG tags over all 80:")
g = rank("GOT BAG tags", {"signal.interests.tags": GOTBAG_TAGS})
st, b, _ = get("/search", {"query": "Patagonia", "types": "urn:entity:brand", "take": 3})
pat = next((e["entity_id"] for e in rows(b) if fold(e.get("name")) == "patagonia"), None)
print(f"   comparable Patagonia: {pat}")
gp = rank("GOT BAG tags + Patagonia", {"signal.interests.tags": GOTBAG_TAGS, "signal.interests.entities": pat}) if pat else []
for a, bb, name in ((g, k, "GOT BAG tags vs Komono"), (gp, g, "tags+Patagonia vs tags only")):
    rho, m = spearman(a, bb)
    print(f"   {name}: Spearman {rho and round(rho, 2)} over {m} cities")

# ---- P35-11: audience UUID → URN in one request ----
print("\nP35-11 · audiences:")
st, b, _ = get("/v2/insights", {"filter.type": "urn:audience", "signal.interests.entities": KOMONO, "take": 5})
uuids = [e.get("entity_id") for e in rows(b)]
names = [e.get("name") for e in rows(b)]
print(f"   Komono audiences: {names}")
st, b, r = get("/v2/audiences", {"filter.results.audiences": ",".join(uuids), "take": 10})
got = rows(b)
print(f"   batch lookup: HTTP {st} · {len(got)} rows · "
      + (str([(a.get('name'), a.get('id')) for a in got][:5]) if st == 200 else (r.get('body') or '')[:200]))

# ---- P35-8: own-store precision ----
print("\nP35-8 · own stores:")
for brand in ("UNTUCKit", "Allbirds", "Warby Parker", "Komono"):
    st, b, _ = get("/search", {"query": brand, "types": "urn:entity:place", "take": 50})
    rs = rows(b)
    exact = [e for e in rs if fold(e.get("name")) == fold(brand)]
    shop = [e for e in exact if any("genre:place" in (t.get("id") or t.get("tag_id") or "") and
                                    any(w in (t.get("id") or t.get("tag_id") or "") for w in ("store", "shop", "shopping"))
                                    for t in e.get("tags") or [])]
    ccs = sorted({((e.get("properties") or {}).get("geocode") or {}).get("country_code") for e in exact} - {None})
    print(f"   {brand:13} {len(rs)} results · {len(exact)} exact name · {len(shop)} of those tagged as a shop · countries {ccs}")
print("\n" + ledger.report())
