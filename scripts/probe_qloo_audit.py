"""Qloo capability audit probes (25 Oct 2026): at most 60 Qloo requests, recorded privately in probes/qloo-audit/.

Re-verify: `uv run python -m scripts.probe_qloo_audit [a b c ...]` (no phase = all), then `make usage`.
Every request goes through landfall.qloo.get (ledger, pacer, cache, redaction). A request already recorded in the
directory is read back from the recording instead of being sent again, so a re-run costs only new requests.
Move the directory aside to re-verify everything live (about 50 requests, 58 attempts on 25 Oct 2026).

Phases: a cross-city ranking in one request (destinations, diversify) · b heatmaps · c audiences · d explainability ·
e compare · f stores (references_brand, located search, paging) · g demographics and trending · h city taste ·
i chosen catalog cities ranked in one request · j heatmap at locality boundary · k stability across city sets.
"""
import json
import math
import sys

from landfall import config, ledger, qloo
from landfall.cassette import Cassette, key_of

KOMONO, UNTUCKIT = "DB045A2F-5476-47A0-98FF-B05E3723AF56", "C4438E6F-8EA9-463E-850E-43C5D72C4BDF"
TORONTO = "POINT(-79.3832 43.6532)"  # the point used by the 23 Oct probes, so their unsignalled baseline is reusable
PARIS = "POINT(2.3522 48.8566)"
EUROPE = "POLYGON((-11 35,30 35,30 61,-11 61,-11 35))"
OUT = config.PRIVATE / "probes" / "qloo-audit"
cas = Cassette("record", OUT)
CATALOG = {c["name"].lower(): c for c in json.loads((config.ROOT / "landfall" / "data" / "cities.json").read_text())["cities"]}
sent = []


def recorded() -> dict:
    f = OUT / "exchanges.jsonl"
    return {json.loads(x)["key"]: json.loads(x) for x in f.read_text().split("\n") if x.strip()} if f.exists() else {}


def ask(label: str, path: str, params: dict) -> tuple[int, dict]:
    """One probe. ponytail: a recorded request is replayed from the file, so re-runs never duplicate or re-spend."""
    canon = {k: str(v) for k, v in sorted(params.items())}
    ex = recorded().get(key_of("qloo", path, {"path": path, "params": canon}))
    r = ex["response"] if ex else qloo.get(path, params, cas, "probe-qloo-audit")
    if not ex:
        sent.append(label)
    try:
        body = json.loads(r.get("body") or "{}")
    except ValueError:
        body = {}
    print(f"\n== {label} [{'recorded' if ex else r.get('origin')}] HTTP {r['status']} · {path} {canon}")
    if r["status"] != 200:
        print("   ", (r.get("body") or r.get("error") or "")[:400])
    return r["status"], body


def items(body: dict) -> list[dict]:
    res = body.get("results")
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        for k in ("entities", "heatmap", "tags", "demographics", "trending", "audiences"):
            if isinstance(res.get(k), list):
                return res[k]
    return []


def aff(e):
    return (e.get("query") or {}).get("affinity")


def show(ents, n=12):
    for e in ents[:n]:
        g = ((e.get("properties") or {}).get("geocode") or {})
        a = aff(e)
        print(f"   {str(e.get('name'))[:40]:40} {e.get('subtype') or e.get('types') or ''} "
              f"{'aff=%.4f' % a if isinstance(a, (int, float)) else 'aff=' + str(a)} pop={e.get('popularity')} "
              f"{g.get('city') or ''} {g.get('country_code') or ''}")
    affs = [aff(e) for e in ents if isinstance(aff(e), (int, float))]
    if affs:
        print(f"   {len(ents)} rows · affinity {min(affs):.4f}–{max(affs):.4f} · distinct {len(set(round(a, 6) for a in affs))}")


def rho(x, y):
    """Spearman on paired values (average ranks for ties)."""
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    if len(x) < 3:
        return float("nan")
    rx, ry = ranks(x), ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")


def compare(name, a, b, key=lambda e: e.get("entity_id")):
    """Jaccard of two result sets, and Spearman of positions on the common items."""
    ka, kb = [key(e) for e in a], [key(e) for e in b]
    common = [x for x in ka if x in kb]
    j = len(set(ka) & set(kb)) / max(1, len(set(ka) | set(kb)))
    r = rho([ka.index(x) for x in common], [kb.index(x) for x in common])
    print(f"-- {name}: {len(common)} common of {len(ka)}/{len(kb)} · Jaccard {j:.2f} · Spearman(common) {r:.2f}")


def aff_vs_pop(name, ents):
    pairs = [(aff(e), e.get("popularity") if e.get("popularity") is not None else (e.get("query") or {}).get("popularity"))
             for e in ents]
    pairs = [(a, p) for a, p in pairs if isinstance(a, (int, float)) and isinstance(p, (int, float))]
    print(f"-- {name}: Spearman(affinity, popularity) {rho(*zip(*pairs)) if len(pairs) > 2 else float('nan'):.2f} over {len(pairs)}")


def catalog_hits(ents):
    hits = [e for e in ents if str(e.get("name", "")).lower() in CATALOG]
    print(f"-- catalog cities among results: {len(hits)} → {[e['name'] for e in hits]}")
    return hits


def brand_id(name):
    s, b = ask(f"brand id · {name}", "/search", {"query": name, "types": "urn:entity:brand", "take": 3})
    ents = items(b)
    show(ents, 3)
    exact = [e for e in ents if str(e.get("name", "")).lower() == name.lower()]
    return (exact or ents or [{}])[0].get("entity_id")


def phase_a():
    s, b = ask("A1 destination search · Paris", "/search", {"query": "Paris", "types": "urn:entity:destination", "take": 5})
    show(items(b), 5)
    paris = next((e for e in items(b) if e.get("name") == "Paris"), None)
    if paris:
        print("   Paris destination keys:", sorted(paris), "props:", sorted((paris.get("properties") or {}))[:30])
    res = {}
    for lab, sig in (("Komono", KOMONO), ("Untuckit", UNTUCKIT), ("no signal", None)):
        p = {"filter.type": "urn:entity:destination", "take": 50}
        if sig:
            p["signal.interests.entities"] = sig
        s, b = ask(f"A2 destinations · {lab}", "/v2/insights", p)
        res[lab] = items(b)
        show(res[lab], 15)
        if res[lab]:
            print("   first row keys:", sorted(res[lab][0]), "props:", sorted((res[lab][0].get("properties") or {}))[:25])
        catalog_hits(res[lab])
        aff_vs_pop(f"destinations {lab}", res[lab])
    compare("destinations Komono vs Untuckit", res["Komono"], res["Untuckit"])
    if res["no signal"]:
        compare("destinations Komono vs no signal", res["Komono"], res["no signal"])
    s, b = ask("A3 destinations · Komono · page 2", "/v2/insights",
               {"filter.type": "urn:entity:destination", "signal.interests.entities": KOMONO, "take": 50, "page": 2})
    p2 = items(b)
    show(p2, 5)
    print("   page-2 overlap with page 1:", len({e['entity_id'] for e in p2} & {e['entity_id'] for e in res['Komono']}))
    catalog_hits(p2)
    # Score chosen cities inside one request: filter.results.entities with destination ids (from A2/A1)
    pool = {e["name"]: e["entity_id"] for e in res["Komono"] + res["Untuckit"] + p2 + ([paris] if paris else [])
            if str(e.get("name", "")).lower() in CATALOG}
    ids = list(pool.values())[:20]
    if ids:
        s, b = ask(f"A4 destinations · Komono · filter.results.entities ({len(ids)} catalog cities)", "/v2/insights",
                   {"filter.type": "urn:entity:destination", "signal.interests.entities": KOMONO,
                    "filter.results.entities": ",".join(ids), "take": 50})
        show(items(b), 25)
        s, b2 = ask(f"A4 destinations · Untuckit · filter.results.entities ({len(ids)} catalog cities)", "/v2/insights",
                    {"filter.type": "urn:entity:destination", "signal.interests.entities": UNTUCKIT,
                     "filter.results.entities": ",".join(ids), "take": 50})
        show(items(b2), 25)
        compare("same city set ranked · Komono vs Untuckit", items(b), items(b2))
    div = {}
    for lab, sig in (("Komono", KOMONO), ("Untuckit", UNTUCKIT), ("no signal", None)):
        p = {"filter.type": "urn:entity:place", "diversify.by": "properties.geocode.city", "diversify.take": 1, "take": 50}
        if sig:
            p["signal.interests.entities"] = sig
        s, b = ask(f"A5 places diversified by city · {lab}", "/v2/insights", p)
        div[lab] = items(b)
        show(div[lab], 12)
    city = lambda e: ((e.get("properties") or {}).get("geocode") or {}).get("city")
    for lab, ents in div.items():
        cities = [city(e) for e in ents]
        print(f"-- {lab}: {len(set(cities))} distinct cities; catalog: {[c for c in cities if c and c.lower() in CATALOG]}")
    compare("diversified cities Komono vs Untuckit", div["Komono"], div["Untuckit"], key=city)
    if div["no signal"]:
        compare("diversified cities Komono vs no signal", div["Komono"], div["no signal"], key=city)


def phase_b():
    hm = {}
    for lab, sig in (("Komono", KOMONO), ("Untuckit", UNTUCKIT)):
        s, b = ask(f"B1 heatmap · Europe · boundary city · {lab}", "/v2/insights",
                   {"filter.type": "urn:heatmap", "filter.location": EUROPE, "output.heatmap.boundary": "city",
                    "signal.interests.entities": sig})
        rows = items(b)
        hm[lab] = rows
        print("   rows", len(rows), "first:", json.dumps(rows[:2])[:600], "| top-level query:", json.dumps(b.get("query"))[:300])
        aff_vs_pop(f"heatmap Europe {lab}", rows)
    key = lambda e: json.dumps(e.get("location"), sort_keys=True)
    compare("heatmap Europe Komono vs Untuckit (cells)", hm["Komono"][:100], hm["Untuckit"][:100], key=key)
    cell = {}
    for lab, sig in (("Komono", KOMONO), ("Untuckit", UNTUCKIT)):
        s, b = ask(f"B2 heatmap · Paris geohash · {lab}", "/v2/insights",
                   {"filter.type": "urn:heatmap", "filter.location.query": "Paris", "signal.interests.entities": sig})
        rows = items(b)
        cell[lab] = {(r.get("location") or {}).get("geohash"): r["query"] for r in rows}
        print("   rows", len(rows), "first:", json.dumps(rows[:1])[:300], "| query:", json.dumps(b.get("query"))[:250])
        aff_vs_pop(f"heatmap Paris {lab}", rows)
    common = [g for g in cell["Komono"] if g in cell["Untuckit"]]
    for f in ("affinity", "affinity_rank", "popularity"):
        x = [cell["Komono"][g].get(f) for g in common]
        y = [cell["Untuckit"][g].get(f) for g in common]
        ok = [(a, c) for a, c in zip(x, y) if isinstance(a, (int, float)) and isinstance(c, (int, float))]
        print(f"-- Paris cells Komono vs Untuckit · {f}: Spearman {rho(*zip(*ok)) if len(ok) > 2 else float('nan'):.3f} over {len(ok)}")


def phase_c():
    s, b = ask("C1 audiences (deprecated type) · Komono", "/v2/insights",
               {"filter.type": "urn:audience", "signal.interests.entities": KOMONO, "take": 10})
    auds = items(b)
    show(auds, 10)
    name = (auds[0]["name"] if auds else "Hiking")
    s, b = ask(f"C2 audience lookup · {name}", "/v2/audiences", {"filter.query": name, "take": 5})
    rows = items(b) or (b.get("results") or {}).get("entities", [])
    print("   ", json.dumps(rows[:3])[:700])
    urn = next((r.get("id") for r in rows if str(r.get("id", "")).startswith("urn:audience")), None)
    if not urn:
        print("   no URN id returned; audience signal probe skipped")
        return
    s, b = ask(f"C3 places · Toronto · signal.demographics.audiences={urn}", "/v2/insights",
               {"filter.type": "urn:entity:place", "filter.location": TORONTO, "filter.location.radius": 15000, "take": 25,
                "signal.demographics.audiences": urn})
    a = items(b)
    show(a, 10)
    base = [json.loads(x) for x in (config.PRIVATE / "probes" / "qloo" / "exchanges.jsonl").read_text().split("\n") if x.strip()]
    base = [x for x in base if x["request"]["params"] == {"filter.location": TORONTO, "filter.location.radius": "15000",
                                                              "filter.type": "urn:entity:place", "take": "25"}]
    unsig = items(json.loads(base[0]["response"]["body"])) if base else []
    compare("audience-signalled vs unsignalled Toronto (23 Oct capture)", a, unsig)
    s, b = ask(f"C4 destinations · Komono + audience {urn}", "/v2/insights",
               {"filter.type": "urn:entity:destination", "signal.interests.entities": KOMONO,
                "signal.demographics.audiences": urn, "take": 50})
    show(items(b), 10)


def phase_d():
    aesop, muji = brand_id("Aesop"), brand_id("Muji")
    print("   ids:", aesop, muji)
    sig = ",".join(x for x in (KOMONO, aesop, muji) if x)
    s, b = ask("D1 places · Toronto · Komono+Aesop+Muji · explainability", "/v2/insights",
               {"filter.type": "urn:entity:place", "filter.location": TORONTO, "filter.location.radius": 15000, "take": 25,
                "signal.interests.entities": sig, "feature.explainability": "true"})
    ents = items(b)
    show(ents, 6)
    print("   top-level query:", json.dumps(b.get("query"))[:1200])
    for e in ents[:4]:
        print("   ", e.get("name"), json.dumps((e.get("query") or {}).get("explainability"))[:400])
    s, b = ask("D2 destinations · Komono+Aesop+Muji · explainability", "/v2/insights",
               {"filter.type": "urn:entity:destination", "take": 25, "signal.interests.entities": sig,
                "feature.explainability": "true"})
    ents = items(b)
    show(ents, 10)
    catalog_hits(ents)
    print("   top-level query:", json.dumps(b.get("query"))[:1200])
    for e in ents[:4]:
        print("   ", e.get("name"), json.dumps((e.get("query") or {}).get("explainability"))[:400])
    return aesop


def phase_e(paris_dest=None):
    s, b = ask("E1 compare · Komono vs Untuckit", "/v2/analysis/compare",
               {"a.signal.interests.entities": KOMONO, "b.signal.interests.entities": UNTUCKIT, "take": 10})
    print("   keys:", list(b.keys()), json.dumps(b.get("results"))[:900])
    if paris_dest:
        s, b = ask("E2 compare · Komono vs Paris (destination)", "/v2/analysis/compare",
                   {"a.signal.interests.entities": KOMONO, "b.signal.interests.entities": paris_dest, "take": 10})
        print("   keys:", list(b.keys()), json.dumps(b.get("results"))[:900])


def phase_f(aesop):
    city = lambda e: ((e.get("properties") or {}).get("geocode") or {}).get("city")
    if aesop:
        s, b = ask("F1 places · references_brand Aesop · Paris 15 km", "/v2/insights",
                   {"filter.type": "urn:entity:place", "filter.references_brand": aesop, "filter.location": PARIS,
                    "filter.location.radius": 15000, "take": 50})
        show(items(b), 8)
        s, b = ask("F2 places · references_brand Aesop · global", "/v2/insights",
                   {"filter.type": "urn:entity:place", "filter.references_brand": aesop, "take": 50})
        g1 = items(b)
        show(g1, 5)
        print("   names:", sorted({e.get("name") for e in g1}), "cities:", sorted({str(city(e)) for e in g1})[:60])
        s, b = ask("F3 places · references_brand Aesop · global · page 2", "/v2/insights",
                   {"filter.type": "urn:entity:place", "filter.references_brand": aesop, "take": 50, "page": 2})
        g2 = items(b)
        print("   page 2 rows", len(g2), "overlap with page 1", len({e['entity_id'] for e in g1} & {e['entity_id'] for e in g2}))
    for lab, bid in (("Untuckit", UNTUCKIT), ("Komono", KOMONO)):
        s, b = ask(f"F4 places · references_brand {lab} · global", "/v2/insights",
                   {"filter.type": "urn:entity:place", "filter.references_brand": bid, "take": 50})
        ents = items(b)
        show(ents, 5)
        print("   names:", sorted({e.get("name") for e in ents})[:20], "cities:", len({city(e) for e in ents}))
    s, b = ask("F5 search · Aesop places · near Paris 20 mi", "/search",
               {"query": "Aesop", "types": "urn:entity:place", "filter.location": PARIS, "filter.radius": 20, "take": 50})
    r = items(b)
    print("   rows", len(r), "cities:", sorted({str(city(e)) for e in r}), "names:", sorted({e.get("name") for e in r})[:15])
    s, b = ask("F5b search · Aesop places · near Paris 20 mi (lat,lon)", "/search",
               {"query": "Aesop", "types": "urn:entity:place", "filter.location": "48.8566,2.3522", "filter.radius": 20,
                "take": 50})
    r = items(b)
    print("   rows", len(r), "cities:", sorted({str(city(e)) for e in r}), "names:", sorted({e.get("name") for e in r})[:15])
    s, b = ask("F6 search · Aesop places · global · page 2", "/search",
               {"query": "Aesop", "types": "urn:entity:place", "take": 50, "page": 2})
    r = items(b)
    print("   rows", len(r), "names:", sorted({e.get("name") for e in r})[:15], "countries:",
          sorted({str(((e.get('properties') or {}).get('geocode') or {}).get('country_code')) for e in r}))


def phase_g():
    for lab, sig in (("Komono", KOMONO), ("Untuckit", UNTUCKIT)):
        s, b = ask(f"G1 demographics · {lab}", "/v2/insights", {"filter.type": "urn:demographics", "signal.interests.entities": sig})
        print("   ", json.dumps(b.get("results"))[:600])
    s, b = ask("G2 trending · Komono", "/v2/trending",
               {"filter.type": "urn:entity:brand", "signal.interests.entities": KOMONO,
                "filter.start_date": "2026-07-01", "filter.end_date": "2026-10-20"})
    print("   ", json.dumps(b.get("results"))[:900])
    s, b = ask("G3 trending · Muji", "/v2/trending",
               {"filter.type": "urn:entity:brand", "signal.interests.entities": "E12201A5-CC50-40AF-97AE-C54A2CA303F7",
                "filter.start_date": "2026-07-01", "filter.end_date": "2026-10-20"})
    print("   ", json.dumps(b.get("results"))[:900])


def phase_i():
    """Rank a chosen set of catalog cities, across continents, inside one request per brand."""
    prior = [x for x in recorded().values() if "filter.results.entities" in x["request"]["params"]
             and x["request"]["params"].get("filter.type") == "urn:entity:destination"]
    ids = prior[0]["request"]["params"]["filter.results.entities"].split(",") if prior else []
    for name in ("Tokyo", "New York", "London", "Berlin", "Seoul", "Mexico City"):
        s, b = ask(f"I1 destination search · {name}", "/search", {"query": name, "types": "urn:entity:destination", "take": 3})
        rows = items(b)
        for e in rows:
            print(f"   {e.get('name')!r} {e.get('entity_id')} {(e.get('properties') or {}).get('country')} pop={e.get('popularity')}")
        hit = next((e for e in rows if str(e.get("name", "")).lower() == name.lower()), None)
        if hit:
            ids.append(hit["entity_id"])
    out = {}
    for lab, sig in (("Komono", KOMONO), ("Untuckit", UNTUCKIT)):
        s, b = ask(f"I2 destinations · {lab} · filter.results.entities ({len(ids)} catalog cities)", "/v2/insights",
                   {"filter.type": "urn:entity:destination", "signal.interests.entities": sig,
                    "filter.results.entities": ",".join(ids), "take": 50})
        out[lab] = items(b)
        show(out[lab], 25)
        aff_vs_pop(f"{lab} chosen cities", out[lab])
    compare("chosen cities · Komono vs Untuckit", out["Komono"], out["Untuckit"])


def phase_k():
    """Komono on a smaller chosen set that includes Paris (A4 gave Paris 0.7739): are scores stable across sets?"""
    ids = ["70E3820A-B1CF-4676-9CDA-C5F6539C53B9", "1EB0550F-0F82-43A2-B501-4D33B5488FD1", "A888626E-4FB4-4260-B948-056C875A65BA",
           "29D176AB-418C-4A6D-B4DA-18E1A9D5FC72", "3B132477-9818-4087-9100-A74128389F09", "AA6F2D58-008A-4FC8-B374-C103D4D86B16",
           "98B97D1C-9BC8-40EA-A3AC-831A83D0A8A4"]  # Paris, Tokyo, New York, London, Berlin, Seoul, Mexico City (A1, I1)
    s, b = ask("K1 destinations · Komono · filter.results.entities (7 catalog cities)", "/v2/insights",
               {"filter.type": "urn:entity:destination", "signal.interests.entities": KOMONO,
                "filter.results.entities": ",".join(ids), "take": 50})
    show(items(b), 10)
    aff_vs_pop("Komono chosen cities", items(b))


def phase_j():
    """Heatmap at locality boundary across Europe, one request per brand."""
    rows = {}
    for lab, sig in (("Komono", KOMONO), ("Untuckit", UNTUCKIT)):
        s, b = ask(f"J1 heatmap · Europe · boundary urn:entity:locality · {lab}", "/v2/insights",
                   {"filter.type": "urn:heatmap", "filter.location": EUROPE, "output.heatmap.boundary": "urn:entity:locality",
                    "signal.interests.entities": sig})
        rows[lab] = items(b)
        print("   rows", len(rows[lab]), "first:", json.dumps(rows[lab][:2])[:700])
        aff_vs_pop(f"locality heatmap {lab}", rows[lab])
    key = lambda e: e.get("entity_id") or e.get("name") or json.dumps(e.get("location"), sort_keys=True)
    for lab in rows:
        top = sorted(rows[lab], key=lambda e: -((e.get("query") or {}).get("affinity_rank") or 0))[:15]
        print(f"-- {lab} top by affinity_rank:", [(e.get("name") or (e.get("location") or {}).get("geohash"),
                                                  round((e.get("query") or {}).get("affinity_rank") or 0, 3)) for e in top])
    compare("locality heatmap Komono vs Untuckit (row order)", rows["Komono"], rows["Untuckit"], key=key)


def phase_h(aesop):
    for lab, loc in (("Paris", PARIS), ("New York", "POINT(-74.006 40.7128)")):
        s, b = ask(f"H1 brands · signal.location {lab} · results = Komono, Untuckit, Aesop", "/v2/insights",
                   {"filter.type": "urn:entity:brand", "signal.location": loc,
                    "filter.results.entities": ",".join(x for x in (KOMONO, UNTUCKIT, aesop) if x), "take": 10})
        show(items(b), 5)
    s, b = ask("H2 tags · Komono", "/v2/insights", {"filter.type": "urn:tag", "signal.interests.entities": KOMONO, "take": 20})
    kt = items(b)
    print("   ", [(t.get("name"), round(aff(t), 3) if isinstance(aff(t), (int, float)) else aff(t)) for t in kt][:20])
    s, b = ask("H3 tags · Komono · signal.location Paris", "/v2/insights",
               {"filter.type": "urn:tag", "signal.interests.entities": KOMONO, "signal.location": PARIS, "take": 20})
    pt = items(b)
    print("   ", [(t.get("name"), round(aff(t), 3) if isinstance(aff(t), (int, float)) else aff(t)) for t in pt][:20])
    compare("tags Komono vs Komono+Paris", kt, pt, key=lambda t: t.get("tag_id") or t.get("id"))
    s, b = ask("H4 places · Toronto · Komono · page 2", "/v2/insights",
               {"filter.type": "urn:entity:place", "filter.location": TORONTO, "filter.location.radius": 15000, "take": 25,
                "signal.interests.entities": KOMONO, "page": 2})
    show(items(b), 3)


phases = set(sys.argv[1:]) or set("abcdefghijk")
paris_dest = None
if "a" in phases:
    phase_a()
"b" in phases and phase_b()
"c" in phases and phase_c()
aesop = phase_d() if phases & set("dfh") else None
if "e" in phases:
    s, b = ask("A1 destination search · Paris", "/search", {"query": "Paris", "types": "urn:entity:destination", "take": 5})
    paris_dest = next((e["entity_id"] for e in items(b) if e.get("name") == "Paris"), None)
    phase_e(paris_dest)
"f" in phases and phase_f(aesop)
"g" in phases and phase_g()
"h" in phases and phase_h(aesop)
"i" in phases and phase_i()
"j" in phases and phase_j()
"k" in phases and phase_k()
print(f"\nsent live this run: {len(sent)}")
print(ledger.report())
