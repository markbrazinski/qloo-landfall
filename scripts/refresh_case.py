"""Versioned replacement evidence after a prompt change (SOP §8A).

Runs the real brand pipeline against an earlier case version. Every recorded exchange is replayed; only stages
named in --live may go live, and only for requests the earlier version does not hold (a changed prompt, or a page
the reader newly asks for). The new case gets the reused exchanges (each pointing at its original) plus the newly
obtained ones. The earlier version is never modified.

usage: python -m scripts.refresh_case <old_dir> <new_dir> <case_id> <label> --live support_check[,brand_reader,brand]
       [--expect "..."] [--status S] [--reason "..."]
"""
import json
import sys
import tempfile
import time
from pathlib import Path

from landfall import config, db, ledger, runner
from landfall.cassette import Cassette
from scripts.lock_case import write_case

v1, v2, case_id, label = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
expect = [sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--expect"]
status = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--status"), "proposed: awaiting owner acceptance")
live = tuple(next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--live"), "support_check").split(","))
reason = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--reason"), "prompt change")
if v2.exists():
    sys.exit(f"{v2} exists; never overwritten")
meta = json.loads((v1 / "case.json").read_text())
before = ledger.totals()

db.reset_for_tests(Path(tempfile.mkdtemp(prefix="lf-refresh-")) / "refresh.db")
runner.cassette_for = lambda pid: Cassette("replay", record_dir=v2, replay_dirs=[v1], live_stages=live)
pid = db.new_id("prj")
db.x("INSERT INTO projects VALUES(?,?,?,?,?,?,?)", pid, "refresh", meta["input"]["url"], "live", None, None, db.now())
db.put_record("founder_input", pid, {"objective": "Explore where to expand for me", "ceiling_usd": None,
                                     "include": None, "exclude": None}, rid=f"input_{pid}")
run_id = runner.start(pid, "brand_read", "attempt-1", {"url": meta["input"]["url"]})
while db.q("SELECT status FROM runs WHERE id=?", run_id)[0]["status"] in ("queued", "running"):
    time.sleep(0.3)
status_run = db.q("SELECT status, detail FROM runs WHERE id=?", run_id)[0]
if status_run["status"] != "completed":
    sys.exit(f"refresh run ended {status_run['status']}: {status_run['detail']}")

rows = [json.loads(x) for x in (v2 / "exchanges.jsonl").read_text().strip().split("\n")]
reused = sum(1 for r in rows if "reused_from" in r.get("provenance", {}))
after = ledger.totals()
n = write_case(pid, v2, case_id, label, status, expect, exchanges=v2 / "exchanges.jsonl", extra={
    "replaces": str(v1), "reason": reason + "; earlier version kept unchanged",
    "provenance": {"reused_exchanges": reused, "new_live_exchanges": len(rows) - reused, "live_stages": list(live),
                   "original_capture": meta.get("recorded_at"), "v1_checksums": (v1 / "SHA256SUMS").read_text()},
    "incremental_usage": {"bedrock_usd": round(after["usd"] - before["usd"], 6),
                          "bedrock_calls": after["bedrock_calls"] - before["bedrock_calls"],
                          "fetches": after["fetch"] - before["fetch"], "qloo": after["qloo"] - before["qloo"]}})
print(f"{case_id}: {n} exchanges ({reused} reused, {n - reused} new live) → {v2}")
print(json.dumps(json.loads((v2 / "case.json").read_text())["incremental_usage"]))
