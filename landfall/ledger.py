"""Append-only usage ledger for live provider attempts, with admission against the active authority."""
import fcntl
import json
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

# Verified 23 Oct 2026 from the public AWS price list (AmazonBedrock us-east-1, published 2026-10-16).
PRICE_TABLE = {
    "version": "price_table@1",
    "nova_2_lite": {"input_per_1k": 0.00033, "output_per_1k": 0.00275},
    "nova_grounding_per_request": 0.03,
}

_lock = threading.Lock()


class BudgetExhausted(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


@contextmanager
def _locked():
    """Serialise ledger check-and-write across threads and processes (dev server, scripts)."""
    config.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(config.LEDGER_PATH.with_suffix(".lock"), "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _entries() -> list[dict]:
    if not config.LEDGER_PATH.exists():
        return []
    return [json.loads(x) for x in config.LEDGER_PATH.read_text().split("\n") if x.strip()]


def totals() -> dict:
    """Finalised attempts plus open reservations. An open reservation (dispatched or about to be, never finalised)
    counts as one attempt at its full reserved exposure: unresolved, never assumed to be zero."""
    t = {"usd": 0.0, "qloo": 0, "fetch": 0, "bedrock_calls": 0, "qloo_month_remaining": None,
         "unresolved": 0, "unresolved_usd": 0.0}
    entries = _entries()
    done = {e.get("reservation") for e in entries if e.get("kind") == "attempt"}
    for e in entries:
        kind = e.get("kind")
        if kind == "reserve" and e["id"] in done:
            continue
        if kind not in ("attempt", "reserve"):
            continue
        usd = e.get("exposure_usd", 0.0) if kind == "reserve" else e.get("est_usd", 0.0)
        t["usd"] += usd
        if kind == "reserve":
            t["unresolved"] += 1
            t["unresolved_usd"] += usd
        if e["provider"] == "qloo":
            t["qloo"] += 1
            if e.get("month_remaining") is not None:
                t["qloo_month_remaining"] = e["month_remaining"]
        elif e["provider"] == "fetch":
            t["fetch"] += 1
        elif e["provider"] == "bedrock" and e["operation"] == "Converse":
            t["bedrock_calls"] += 1
    return t


def _limits(t: dict) -> list[tuple[str, float, float]]:
    return [("usd", t["usd"], config.CAP_USD), ("qloo", t["qloo"], config.CAP_QLOO), ("fetch", t["fetch"], config.CAP_FETCH)]


def _append(entry: dict, before: dict) -> None:
    with config.LEDGER_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()  # totals() re-reads the file
        after = totals()
        for (key, b, cap), (_, a, _) in zip(_limits(before), _limits(after)):
            if b < 0.8 * cap <= a:
                f.write(json.dumps({"kind": "notice", "at": entry["at"],
                                    "text": f"80% of {key} authority used ({a:g}/{cap:g})"}) + "\n")


def reserve(provider: str, operation: str, exposure_usd: float = 0.0, **fields) -> str:
    """Atomically admit one live attempt and reserve its worst-case cost before dispatch. Raises BudgetExhausted."""
    with _locked():
        if _now() > config.EXPIRES:
            raise BudgetExhausted("live authority expired")
        t = totals()
        if provider == "bedrock" and t["usd"] + exposure_usd > config.CAP_USD:
            raise BudgetExhausted(f"usd cap reached (${t['usd']:.4f} + ${exposure_usd:.4f} reserve > ${config.CAP_USD:g})")
        if provider in ("qloo", "fetch"):
            cap = config.CAP_QLOO if provider == "qloo" else config.CAP_FETCH
            if t[provider] + 1 > cap:
                raise BudgetExhausted(f"{provider} cap reached ({t[provider]}/{cap})")
        if provider == "qloo" and t["qloo_month_remaining"] is not None and t["qloo_month_remaining"] < config.QLOO_FLOOR:
            raise BudgetExhausted(f"Qloo monthly remaining {t['qloo_month_remaining']} below floor {config.QLOO_FLOOR}")
        rid = uuid.uuid4().hex[:16]
        _append({"kind": "reserve", "id": rid, "at": _now().isoformat(), "provider": provider, "operation": operation,
                 "exposure_usd": round(exposure_usd, 6), **fields}, t)
        return rid


def finalize(rid: str, provider: str, operation: str, *, est_usd: float = 0.0, outcome: str, **fields) -> None:
    """Close a reservation with what actually happened. Transport failures pass est_usd = the reserved exposure."""
    with _locked():
        _append({"kind": "attempt", "reservation": rid, "at": _now().isoformat(), "provider": provider,
                 "operation": operation, "est_usd": round(est_usd, 6), "outcome": outcome,
                 "price_table": PRICE_TABLE["version"], **fields}, totals())


def record(provider: str, operation: str, *, est_usd: float = 0.0, **fields) -> None:
    """A direct attempt line for adjustments and out-of-band diagnostics (no reservation)."""
    with _locked():
        _append({"kind": "attempt", "at": _now().isoformat(), "provider": provider, "operation": operation,
                 "est_usd": round(est_usd, 6), "price_table": PRICE_TABLE["version"], **fields}, totals())


def note(text: str) -> None:
    with _locked():
        with config.LEDGER_PATH.open("a") as f:
            f.write(json.dumps({"kind": "accounting_note", "at": _now().isoformat(), "text": text}) + "\n")


def nova_cost(input_tokens: int, output_tokens: int, grounding_calls: int = 0) -> float:
    p = PRICE_TABLE
    return (input_tokens / 1000 * p["nova_2_lite"]["input_per_1k"] + output_tokens / 1000 * p["nova_2_lite"]["output_per_1k"]
            + grounding_calls * p["nova_grounding_per_request"])


def report() -> str:
    t = totals()
    return (f"Bedrock ${t['usd']:.4f} of ${config.CAP_USD:g} ({t['bedrock_calls']} calls) · "
            f"Qloo {t['qloo']} of {config.CAP_QLOO} attempts (month remaining: {t['qloo_month_remaining']}) · "
            f"Fetches {t['fetch']} of {config.CAP_FETCH} · unresolved {t['unresolved']} (≤ ${t['unresolved_usd']:.4f})")


if __name__ == "__main__":
    print(report())
