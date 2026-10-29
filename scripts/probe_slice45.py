"""Feasibility checks for the slice-4.5 plan (25 Oct 2026), recorded privately in live-captures-private/probes/slice45/.

G · geocoding without a new provider: Qloo /search by name near the city, for the page-found leads of L4-A/L4-B.
S1 · social access rules: robots.txt for tiktok.com, instagram.com, youtube.com (does LandfallBot / * allow profiles?).
S2 · TikTok oEmbed for a creator profile from the P4-4 citations (only if robots allows the endpoint).
S3 · creator discovery from readable sources: one grounding question asking for articles, not social profiles.
About 6 Qloo, 8 fetches and 1 grounding call.
"""
import json

from landfall import config, fetch, ledger, markets, qloo
from landfall.cassette import Cassette
from landfall.nova import make_model

OUT = config.PRIVATE / "probes" / "slice45"
cas = Cassette("record", OUT)
print(ledger.report())

print("\nG · Qloo /search near the city for page-found leads:")
for name, cid in [("I Like You", "minneapolis"), ("Martin Patrick 3", "minneapolis"), ("A Vida Portuguesa", "lisbon"),
                  ("Function Well", "brisbane"), ("Kyoto", "copenhagen"), ("Brisbane Convention & Exhibition Centre", "brisbane")]:
    c = markets.CITIES[cid]
    r = qloo.get("/search", {"query": name, "types": "urn:entity:place", "filter.location": f"{c['lat']},{c['lon']}",
                             "filter.radius": 15, "take": 5}, cas, "probe-slice45")
    rows = json.loads(r.get("body") or "{}").get("results", []) if r.get("status") == 200 else []
    best = [(e.get("name"), (e.get("location") or {}).get("lat"), (e.get("location") or {}).get("lon"),
             (e.get("properties") or {}).get("address")) for e in rows[:3]]
    exact = [b for b in best if markets.fold(b[0] or "") == markets.fold(name)]
    print(f"   {name} ({cid}): HTTP {r.get('status')} · {len(rows)} rows · exact-name: {exact[:1]} · top: {best[:2]}")

print("\nS1 · robots.txt:")
paths = {"tiktok.com": ["/@pl.menswear", "/oembed"], "instagram.com": ["/someprofile/"], "youtube.com": ["/@somechannel", "/oembed"]}
allowed = {}
for host, ps in paths.items():
    rp = fetch.robots_for(f"https://www.{host}/", cas, "probe-slice45")
    for p in ps:
        ok = fetch.robots_allows(f"https://www.{host}{p}", rp)
        allowed[(host, p)] = ok
        print(f"   {host}{p}: {'allowed' if ok else 'disallowed'} for LandfallBot{' (no robots.txt)' if rp is None else ''}")

print("\nS2 · TikTok oEmbed:")
if allowed[("tiktok.com", "/oembed")]:
    r = fetch.get("https://www.tiktok.com/oembed?url=https://www.tiktok.com/@pl.menswear", cas, "probe-slice45")
    body = (r.get("body") or "")[:400]
    print(f"   HTTP {r.get('status')} {r.get('error') or ''} · {body!r}")
else:
    print("   not tried: robots.txt disallows the endpoint")

print("\nS3 · readable creator sources from grounding:")
usage = []
model = make_model(cas, "probe-slice45-grounding", usage.append, max_tokens=500)
resp = model.client.converse(modelId=config.NOVA_MODEL, messages=[{"role": "user", "content": [{"text":
    "Articles, local magazine features or creator websites naming menswear or style content creators based in "
    "Minneapolis. Prefer sources that are not social-media profile pages. Give names and the page links."}]}],
    toolConfig={"tools": [{"systemTool": {"name": "nova_grounding"}}]}, inferenceConfig={"maxTokens": 500})
cites = {c["location"]["web"]["url"]: c["location"]["web"] for b in resp["output"]["message"]["content"]
         for c in (b.get("citationsContent") or {}).get("citations", []) if "web" in c.get("location", {})}
print(f"   {len(cites)} citations · {[u for u in cites][:6]}")
for u in list(cites)[:3]:
    if any(s in u for s in ("tiktok", "instagram", "youtube", "facebook")):
        print(f"   {u[:80]} → social, not read")
        continue
    rp = fetch.robots_for(u, cas, "probe-slice45")
    if not fetch.robots_allows(u, rp):
        print(f"   {u[:80]} → robots disallow")
        continue
    p = fetch.get(u, cas, "probe-slice45")
    ex = fetch.extract(p) if p.get("status") == 200 else {}
    print(f"   {u[:80]} → HTTP {p.get('status')} · {len(ex.get('text') or '')} chars")
print("\n" + ledger.report())
