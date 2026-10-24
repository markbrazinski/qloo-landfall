"""Package a recorded project as a versioned, checksummed case (SOP §8A).

usage: LANDFALL_DB=<db> python -m scripts.lock_case <project_id> <case_dir> <case_id> <label> [--status S] [--expect "<outcome>" ...]
Refuses to overwrite an existing case: a replacement is a new version directory.
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

from landfall import app, brand, config, db


def write_case(pid: str, case_dir: Path, case_id: str, label: str, status: str, expect: list[str],
               exchanges: Path | None = None, extra: dict | None = None) -> int:
    if (case_dir / "case.json").exists():
        sys.exit(f"{case_dir} is already a case; accepted cases are never overwritten. Use a new version directory.")
    case_dir.mkdir(parents=True, exist_ok=True)
    src = exchanges or config.PRIVATE / "runs" / pid / "exchanges.jsonl"
    if src.resolve() != (case_dir / "exchanges.jsonl").resolve():
        shutil.copy(src, case_dir / "exchanges.jsonl")
    lines = (case_dir / "exchanges.jsonl").read_text()
    for secret in filter(None, [config.QLOO_KEY]):
        assert secret not in lines, "secret found in captured exchanges"
    p = db.q("SELECT * FROM projects WHERE id=?", pid)[0]
    view = app.project_view(p)
    (case_dir / "case.json").write_text(json.dumps({
        "case_id": case_id, "label": label, "status": status, "slice": 1, "input": {"url": p["url"]},
        "recorded_from_project": pid, "recorded_at": p["created_at"], "model": config.NOVA_MODEL,
        "prompt_versions": [brand.PROMPT_VERSION, brand.CHECKER_VERSION], "expected_semantic_outcomes": expect,
        **(extra or {})}, indent=1))
    (case_dir / "trace.json").write_text(json.dumps({
        "reading": view["reading"], "brief": view["brief"], "usage": view["usage"], "activity": view["activity"],
        "run": view["run"]}, indent=1))
    sums = "".join(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}\n"
                   for f in sorted(case_dir.iterdir()) if f.name != "SHA256SUMS")
    (case_dir / "SHA256SUMS").write_text(sums)
    return len(lines.strip().split("\n"))


if __name__ == "__main__":
    pid, case_dir, case_id, label = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]
    expect = [sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--expect"]
    status = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--status"), "proposed: awaiting owner acceptance")
    print(f"locked {case_id} → {case_dir} ({write_case(pid, case_dir, case_id, label, status, expect)} exchanges)")
