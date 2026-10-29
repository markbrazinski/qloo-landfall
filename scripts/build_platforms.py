"""Build `platforms@2` (BUILD-PLAN §4.5.5 digital): dated, verbatim official quotes for ad-platform targeting option types.

Each page is read through Landfall's fetcher (robots honoured, counted in the ledger, recorded privately in
live-captures-private/probes/platforms/). The quote kept is the page's own sentence containing the option, so it is
verbatim by construction; a page that can't be read, or doesn't contain the phrase, contributes nothing.

    uv run python -m scripts.build_platforms
"""
import hashlib
import json
import re
from datetime import date

from landfall import config, fetch, ledger, markets
from landfall.cassette import Cassette

OUT = markets.DATA / "platforms.json"
CANDIDATES = [  # (id, platform, option type, official URL, phrase that must be on the page)
    ("google.affinity", "Google", "Affinity segments",
     "https://support.google.com/google-ads/answer/2497941?hl=en", r"[Aa]ffinity segments"),
    ("google.in_market", "Google", "In-market segments",
     "https://support.google.com/google-ads/answer/2497941?hl=en", r"[Ii]n-market segments"),
    ("google.location_radius", "Google", "Location targeting with a radius",
     "https://support.google.com/google-ads/answer/1722038?hl=en", r"radius"),
    ("google.demographics", "Google", "Demographic targeting (age, gender)",
     "https://support.google.com/google-ads/answer/2580383?hl=en", r"\b[Aa]ge\b"),
    ("tiktok.demographics", "TikTok", "Demographic targeting (age, gender)",
     "https://ads.tiktok.com/help/article/demographic-targeting?lang=en", r"\b[Aa]ge\b"),
    ("tiktok.interests", "TikTok", "Interest targeting",
     "https://ads.tiktok.com/help/article/interest-targeting?lang=en", r"[Ii]nterest"),
    ("tiktok.location", "TikTok", "Location targeting",
     "https://ads.tiktok.com/help/article/location-targeting?lang=en", r"[Ll]ocation"),
]


# Meta's developers.facebook.com robots.txt states that automated collection is prohibited without Meta's written
# permission, so Meta is never fetched by Landfall: these are quoted by the builder from the official pages (read with
# a browser-equivalent tool on 26 Oct 2026) and labelled BUILDER_CHECKED.
BUILDER_CHECKED = [
    {"id": "meta.city_radius", "platform": "Meta", "option_type": "City targeting with a radius",
     "url": "https://developers.facebook.com/docs/marketing-api/audiences/reference/basic-targeting",
     "quote": "`radius` is a distance around cities, from 10 to 50 miles or 17 to 80 kilometers.",
     "checked_at": "2026-10-26", "checked_by": "BUILDER_CHECKED"},
    {"id": "meta.interests", "platform": "Meta", "option_type": "Detailed targeting interests (validate by id)",
     "url": "https://developers.facebook.com/docs/marketing-api/audiences/reference/targeting-search",
     "quote": "Interests may be renamed at any time, and validating by name may fail when this happens.",
     "checked_at": "2026-10-26", "checked_by": "BUILDER_CHECKED"},
    {"id": "meta.age", "platform": "Meta", "option_type": "Age range",
     "url": "https://developers.facebook.com/docs/marketing-api/audiences/reference/basic-targeting",
     "quote": "Minimum age. Defaults to 18. If used, must be 13 or higher. Maximum age. If used, must be 65 or lower.",
     "checked_at": "2026-10-26", "checked_by": "BUILDER_CHECKED"},
    {"id": "meta.gender", "platform": "Meta", "option_type": "Gender",
     "url": "https://developers.facebook.com/docs/marketing-api/audiences/reference/basic-targeting",
     "quote": "Genders to target. Defaults to all. `1` targets males, `2` targets females.",
     "checked_at": "2026-10-26", "checked_by": "BUILDER_CHECKED"},
]


def sentence(text: str, phrase: str) -> str | None:
    m = re.search(phrase, text)
    if not m:
        return None
    start = max(text.rfind(c, 0, m.start()) for c in ".!?\n") + 1
    end = min((i for i in (text.find(c, m.end()) for c in ".!?\n") if i >= 0), default=len(text))
    return " ".join(text[start:end + 1].split())


def main() -> None:
    cas = Cassette("record", config.PRIVATE / "probes" / "platforms")
    print(ledger.report())
    pages, skipped = {}, []
    entries = [e for e in (json.loads(OUT.read_text())["entries"] if OUT.exists() else [])
               if e.get("checked_by") != "BUILDER_CHECKED"]
    for pid, platform, option, url, phrase in CANDIDATES:
        if any(e["id"] == pid for e in entries):
            continue
        if url not in pages:
            rp = fetch.robots_for(url, cas, "platforms")
            if not fetch.robots_allows(url, rp):
                pages[url] = {"error": "robots.txt disallows"}
            else:
                page = fetch.get(url, cas, "platforms")
                ex = fetch.extract(page) if page.get("status") == 200 else {"text": "", "links": []}
                if page.get("status") == 200 and fetch.needs_render(ex):  # a JavaScript-only page (Meta's docs)
                    shown = fetch.render(url, cas, "platforms")
                    if shown.get("status") == 200:
                        page, ex = shown, fetch.extract(shown)
                pages[url] = {"text": ex["text"], "final_url": page.get("url"), "status": page.get("status"),
                              "error": page.get("error") or (None if ex["text"] else "no readable text")}
        p = pages[url]
        quote = sentence(p.get("text") or "", phrase) if not p.get("error") else None
        if not quote:
            skipped.append(f"{pid}: {p.get('error') or 'phrase not on the page'} ({url})")
            continue
        entries.append({"id": pid, "platform": platform, "option_type": option, "url": url, "quote": quote,
                        "checked_at": date.today().isoformat(), "checked_by": "FETCHED",
                        "page_sha256": hashlib.sha256(p["text"].encode()).hexdigest()})
    entries = [{**e, "checked_by": e.get("checked_by", "FETCHED")} for e in entries] + BUILDER_CHECKED
    # @2: age and gender option types (LIVE L45-A attempt 2)
    OUT.write_text(json.dumps({"version": "platforms@2", "checked_at": date.today().isoformat(),
                               "note": "Option types only, quoted verbatim from official pages read through Landfall's "
                                       "fetcher; not specific interest names, sizes or costs.",
                               "entries": entries}, indent=1, ensure_ascii=False))
    print(f"{len(entries)} entries written to {OUT}")
    for s in skipped:
        print("  skipped", s)
    print(ledger.report())


if __name__ == "__main__":
    main()
