"""Build `destinations@1` (landfall/data/destinations.json) from the recorded destination searches (no provider calls).

Inputs: live-captures-private/probes/slice3_5_open/exchanges.jsonl, the R3 searches (one per catalog city) and the
P35-12 repair searches ("Bangalore", "Sao Paulo Brazil", "Wellington New Zealand", "Lima Peru").

Pick rule (BUILD-PLAN slice 3.5, R1): a candidate must have the catalog name (or a known alias) and lie within 60 km of
the catalog point, or have no location but the exact name; then the nearest, then the higher popularity. Every other
same-named candidate is kept as `alternative_id`. Cities with no candidate are listed as unmapped.

    uv run python -m scripts.build_destinations
"""
import json

from landfall import config, markets

SRC = config.PRIVATE / "probes" / "slice3_5_open" / "exchanges.jsonl"
OUT = markets.DATA / "destinations.json"
REPAIR = {"Bangalore": "bengaluru", "Sao Paulo Brazil": "sao-paulo", "Wellington New Zealand": "wellington",
          "Lima Peru": "lima"}


def main():
    by_city: dict[str, list[dict]] = {}
    names = {c["name"]: cid for cid, c in markets.CITIES.items()}
    for line in filter(None, SRC.read_text().split("\n")):
        ex = json.loads(line)
        p = ex["request"]["params"]
        if ex["request"]["path"] != "/search" or p.get("types") != "urn:entity:destination":
            continue
        cid = names.get(p["query"]) or REPAIR.get(p["query"])
        if cid:
            by_city.setdefault(cid, []).extend(json.loads(ex["response"]["body"]).get("results") or [])
    table, dupes = {}, []
    for cid, city in markets.CITIES.items():
        ok = {markets.fold(n) for n in [city["name"], *markets.ALIASES.get(cid, [])]}
        cands = {}
        for e in by_city.get(cid, []):
            loc = e.get("location") or {}
            km = (markets.km(city["lat"], city["lon"], loc["lat"], loc["lon"]) if loc.get("lat") is not None else None)
            if markets.fold(e.get("name")) in ok and (km is None or km <= 60):
                cands[e["entity_id"]] = {"qloo_id": e["entity_id"], "name": e["name"], "km": km and round(km, 1),
                                         "popularity": e.get("popularity")}
        if not cands:
            table[cid] = {"unmapped": "no Qloo destination with this name near the city (P35-12)"}
            continue
        ranked = sorted(cands.values(), key=lambda c: (c["km"] is None, c["km"] or 0, -(c["popularity"] or 0)))
        pick = ranked[0]
        reason = ("same name, no location: accepted by exact name (P35-12)" if pick["km"] is None
                  else f"same name, {pick['km']} km from the catalog point")
        if len(ranked) > 1:
            reason += "; nearest, then higher popularity, of " + str(len(ranked))
            dupes.append(cid)
        table[cid] = {"qloo_id": pick["qloo_id"], "name": pick["name"], "pick_reason": reason,
                      **({"alternative_id": ranked[1]["qloo_id"]} if len(ranked) > 1 else {})}
    ids = [v["qloo_id"] for v in table.values() if "qloo_id" in v]
    assert len(ids) == len(set(ids)), "two catalog cities picked the same destination"
    OUT.write_text(json.dumps({"version": "destinations@1", "source": "recorded R3 + P35-12 searches, 25 Oct 2026",
                               "duplicates": dupes, "cities": table}, indent=1, ensure_ascii=False) + "\n")
    unmapped = [c for c, v in table.items() if "unmapped" in v]
    print(f"{len(ids)} mapped · unmapped: {unmapped} · {len(dupes)} with a same-name duplicate: {dupes}")


if __name__ == "__main__":
    main()
