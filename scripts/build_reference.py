"""Build the packaged reference data for slice 3 (BUILD-PLAN slice 3, "Reference data").

usage: python -m scripts.build_reference <geonames_dir>
  <geonames_dir> holds cities15000.txt, countryInfo.txt and admin1CodesASCII.txt from
  https://download.geonames.org/export/dump/ (GeoNames, CC BY 4.0).

Writes landfall/data/gazetteer.tsv (gazetteer@1), countries.json (countries@1 + eu_customs@2026-10) and
cities.json (catalog@1: the design's 80 cities with an authored slug and the ISO country of the nearest gazetteer
city, checked by eye before commit).
"""
import json
import math
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "landfall" / "data"
ATTRIBUTION = "GeoNames (geonames.org), CC BY 4.0. Subset of cities15000: name, ASCII name, admin1 name, country, lat, lon, population."

# Short alias list (BUILD-PLAN slice 3). Names are matched case-folded; GeoNames' own names are added too.
ALIASES = {
    "US": ["USA", "US", "U.S.", "U.S.A.", "United States", "United States of America", "America"],
    "GB": ["UK", "U.K.", "United Kingdom", "Britain", "Great Britain", "England", "Scotland", "Wales"],
    "CZ": ["Czechia", "Czech Republic"], "NL": ["Holland", "The Netherlands"], "KR": ["South Korea", "Korea"],
    "AE": ["UAE", "United Arab Emirates"], "CH": ["Switzerland", "Schweiz", "Suisse"], "DE": ["Deutschland"],
    "BE": ["Belgique", "België"], "ES": ["España"], "IT": ["Italia"], "AT": ["Österreich"], "TR": ["Türkiye", "Turkey"],
    "RU": ["Russia"], "VN": ["Vietnam", "Viet Nam"], "TW": ["Taiwan"], "HK": ["Hong Kong"], "IE": ["Ireland", "Éire"],
}
EU = ["AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT",
      "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"]


def slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def km(a, b, c, d) -> float:
    p1, p2, dl = math.radians(a), math.radians(c), math.radians(d - b)
    return 6371 * math.acos(min(1, math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)))


def main(src: Path):
    admin1 = {}
    for line in (src / "admin1CodesASCII.txt").read_text().splitlines():
        code, name, *_ = line.split("\t")
        admin1[code] = name
    countries = {}
    for line in (src / "countryInfo.txt").read_text().splitlines():
        if line.startswith("#"):
            continue
        f = line.split("\t")
        countries[f[0]] = [f[4]]
    for iso, names in ALIASES.items():
        countries[iso] += [n for n in names if n not in countries[iso]]
    (DATA / "countries.json").write_text(json.dumps({
        "version": "countries@1", "source": "ISO 3166-1 alpha-2 names from GeoNames countryInfo (CC BY 4.0) plus aliases",
        "countries": countries,
        "eu_customs": {"version": "eu_customs@2026-10", "label": "EU customs union (approximation: member states only)",
                       "members": EU}}, ensure_ascii=False, indent=1))

    rows = []
    for line in (src / "cities15000.txt").read_text().splitlines():
        f = line.split("\t")
        rows.append((f[1], f[2], admin1.get(f"{f[8]}.{f[10]}", ""), f[8], round(float(f[4]), 4), round(float(f[5]), 4),
                     int(f[14] or 0)))
    with (DATA / "gazetteer.tsv").open("w") as out:
        out.write(f"# gazetteer@1 · {ATTRIBUTION}\n")
        out.write("name\tascii\tadmin1\tcountry\tlat\tlon\tpopulation\n")
        for r in rows:
            out.write("\t".join(map(str, r)) + "\n")

    design = json.loads((ROOT / "design" / "fixtures" / "landfall-fixtures.json").read_text())["CITIES"]
    big = [r for r in rows if r[6] >= 300000]
    catalog = []
    for c in design:
        near = min(big, key=lambda r: km(c["la"], c["lo"], r[4], r[5]))
        catalog.append({"city_id": slug(c["n"]), "name": c["n"], "country": near[3], "region": c["reg"],
                        "lat": c["la"], "lon": c["lo"]})
        print(f"{c['n']:>18} → {near[0]}, {near[3]} ({km(c['la'], c['lo'], near[4], near[5]):.0f} km)")
    (DATA / "cities.json").write_text(json.dumps({"version": "catalog@1", "radius_m": 15000, "cities": catalog},
                                                 ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
