"""Probe P65-MK1 (BUILD-PLAN UI.13; owner approval 27 Oct 2026: 3 images): is the scene model callable from this
account, what does a call cost, and are the scenes usable behind the mockup frame?

usage: python -m scripts.probe_mockups [n ≤ 3] [first case index, default 0]

Sends one image first and stops at the first refusal; at most three. Each call is admitted and recorded by the ledger
(§8.1, Bedrock line) through the real `mockups.render_scene`; the exchanges and images are saved privately under
live-captures-private/probes/p65-mk1/ (never committed).
"""
import base64
import json
import sys
import time

from landfall import config, ledger, mockups
from landfall.cassette import Cassette

CASES = [("community", "brisbane", "bags and backpacks made from recycled materials"),
         ("digital", "minneapolis", "men's shirts"),
         ("retail", "brisbane", "bags and backpacks made from recycled materials")]
n = min(int(sys.argv[1]) if len(sys.argv) > 1 else 3, 3)
start = int(sys.argv[2]) if len(sys.argv) > 2 else 0
out = config.PRIVATE / "probes" / "p65-mk1"
out.mkdir(parents=True, exist_ok=True)
cas = Cassette("record", out)
notes = []
for i, (method, city, category) in list(enumerate(CASES))[start:start + n]:
    prompt = mockups.scene_prompt(method, city, category)
    t = time.time()
    try:
        r = mockups.render_scene(cas, prompt, 1000 + i)
    except Exception as e:  # access, subscription or schema: stop at the first refusal
        notes.append({"case": f"{city}/{method}", "ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"})
        print("STOP", notes[-1], flush=True)
        break
    data = base64.b64decode(r["b64"])
    (out / f"{i + 1}-{city}-{method}.jpg").write_bytes(data)
    notes.append({"case": f"{city}/{method}", "ok": True, "bytes": len(data), "seconds": round(time.time() - t, 1),
                  "finish_reasons": r.get("finish_reasons"), "prompt": prompt})
    print("OK", notes[-1], flush=True)
log = out / "notes.json"
prev = json.loads(log.read_text()) if log.exists() else {"runs": []}
prev.setdefault("runs", []).append({"model": mockups.MODEL, "region": mockups.REGION, "results": notes})
log.write_text(json.dumps({"runs": prev["runs"]}, indent=1))
print(ledger.report())
