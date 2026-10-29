"""Package a recorded project as a versioned, checksummed case (SOP §8A).

usage: LANDFALL_DB=<db> python -m scripts.lock_case <project_id> <case_dir> <case_id> <label> [--status S] [--expect "<outcome>" ...]
       [--slice N] [--screens DIR] [--notes notes.json] [--extra extra.json]
       [--exchanges exchanges.jsonl]
Refuses to overwrite an existing case: a replacement is a new version directory.
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

from landfall import app, brand, config, db, identity, markets, ranking, scan


def write_case(pid: str, case_dir: Path, case_id: str, label: str, status: str, expect: list[str],
               exchanges: Path | None = None, extra: dict | None = None, slice_no: int = 1,
               screens: Path | None = None, notes: dict | None = None) -> int:
    if (case_dir / "case.json").exists():
        sys.exit(f"{case_dir} is already a case; accepted cases are never overwritten. Use a new version directory.")
    case_dir.mkdir(parents=True, exist_ok=True)
    src = exchanges or config.PRIVATE / "runs" / pid / "exchanges.jsonl"
    if src.resolve() != (case_dir / "exchanges.jsonl").resolve():
        shutil.copy(src, case_dir / "exchanges.jsonl")
    if screens:
        (case_dir / "screens").mkdir()
        for f in sorted(screens.glob("*.png")):
            shutil.copy(f, case_dir / "screens" / f.name)
    lines = (case_dir / "exchanges.jsonl").read_text()
    for secret in filter(None, [config.QLOO_KEY]):
        assert secret not in lines, "secret found in captured exchanges"
    p = db.q("SELECT * FROM projects WHERE id=?", pid)[0]
    view = app.project_view(p)
    (case_dir / "case.json").write_text(json.dumps({
        "case_id": case_id, "label": label, "status": status, "slice": slice_no, "input": {"url": p["url"]},
        "recorded_from_project": pid, "recorded_at": p["created_at"], "model": config.NOVA_MODEL,
        "prompt_versions": [brand.PROMPT_VERSION, brand.CHECKER_VERSION]
        + ([identity.PROFILE_VERSION] if slice_no >= 2 else []) + ([scan.STRATEGIST_VERSION] if slice_no >= 3 else []),
        **({"reference_data": [markets.CATALOG_VERSION, "countries@1", "eu_customs@2026-10", markets.GAZETTEER_VERSION,
                               markets.CHANNELS_VERSION, *(ranking.REFERENCE.values() if slice_no >= 3.5 else [])]}
           if slice_no >= 3 else {}),
        "expected_semantic_outcomes": expect,
        **(extra or {})}, indent=1))
    history = {k: db.records(k, pid) for k in ("identity", "profile")} if slice_no >= 2 else {}
    markets_trace = {}
    if slice_no >= 3:
        gens = db.records("scan_generation", pid)
        history.update(brief=db.records("brief", pid), assessment_set=db.records("assessment_set", pid),
                       shortlist=db.records("shortlist", pid), founder_input=db.records("founder_input", pid),
                       **{k: db.records(k, pid) for k in ("current_presence", "standins", "qloo_ranking", "audience_profile",
                                                          "audience_urns", "comparable_presence")},
                       scan_generation={"versions": len(gens), "first": gens[0], "last": gens[-1]} if gens else None)
        markets_trace = {"markets": scan.markets_view(pid), "city_scans": [
            {k: r.get(k) for k in ("id", "version", "city_id", "status", "scan_run_id", "origin", "attempts", "interrupted",
                                   "attempt_log", "error", "fetched_at")} | {"returned_count": len(r.get("places") or [])}
            for r in db.records("city_scan", pid)]}
    (case_dir / "trace.json").write_text(json.dumps({
        "reading": view["reading"], "brief": view["brief"], "usage": view["usage"], "activity": view["activity"],
        "run": view["run"], **({"identity": view["identity"], "profile": view["profile"], "markets_stage": view.get("markets"),
                               "markets_reason": view["markets_reason"], "record_history": history,
                               "runs": [dict(r) for r in db.q("SELECT id, kind, input_key, status, started_at, ended_at "
                                                              "FROM runs WHERE project_id=? ORDER BY started_at", pid)]}
                              if slice_no >= 2 else {}), **markets_trace, **({"notes": notes} if notes else {})}, indent=1))
    files = sorted(f for f in case_dir.rglob("*") if f.is_file() and f.name != "SHA256SUMS")
    sums = "".join(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(case_dir)}\n" for f in files)
    (case_dir / "SHA256SUMS").write_text(sums)
    return len(lines.strip().split("\n"))


if __name__ == "__main__":
    pid, case_dir, case_id, label = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]
    expect = [sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--expect"]
    status = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--status"), "proposed: awaiting owner acceptance")
    opt = lambda name: next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == name), None)  # noqa: E731
    n = write_case(pid, case_dir, case_id, label, status, expect, slice_no=(lambda v: int(v) if v.is_integer() else v)(float(opt("--slice") or 1)),
                   screens=Path(opt("--screens")) if opt("--screens") else None,
                   notes=json.loads(Path(opt("--notes")).read_text()) if opt("--notes") else None,
                   extra=json.loads(Path(opt("--extra")).read_text()) if opt("--extra") else None,
                   exchanges=Path(opt("--exchanges")) if opt("--exchanges") else None)
    print(f"locked {case_id} → {case_dir} ({n} exchanges)")
