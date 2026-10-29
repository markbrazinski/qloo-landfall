"""Append-only usage ledger for live provider attempts, with admission against the active authority."""
import fcntl
import json
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

# Verified from the public AWS price list (AmazonBedrock us-east-1, published 2026-10-16): Nova 2 Lite on 23 Oct 2026,
# Nova Premier and Nova Pro (standard on-demand tokens) on 26 Oct 2026. Nova Premier is listed but end-of-life: Converse
# refuses it (LIVE, 26 Oct 2026); its price is kept only so any recorded attempt can be read.
PRICE_TABLE = {
    "version": "price_table@2",
    "nova_2_lite": {"input_per_1k": 0.00033, "output_per_1k": 0.00275},
    "nova_premier": {"input_per_1k": 0.0025, "output_per_1k": 0.0125},
    "nova_pro": {"input_per_1k": 0.0008, "output_per_1k": 0.0032},
    "nova_grounding_per_request": 0.03,
}

# $0 providers whose requests count against the fetch authority (P45-R8: the Shopify Storefront Catalog MCP).
FETCH_LIKE = ("fetch", "shopify_mcp")

# Slice 6.6 (owner, 26 Oct 2026): Influencers.club's 30 requests, split into pools that never borrow from each other.
# Cumulative across environments [P66-R1]: the hosted ledger opens with a `carry_in` line holding the dev totals.
IC = "influencers_club"
IC_POOLS = {"personal": {"requests": 10, "credits": 4.0}, "judges": {"requests": 20, "credits": 6.0}}
IC_JUDGE_PROJECT = {"requests": 2, "credits": 0.6}  # the location lookup is exempt [P66-R3]

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
         "unresolved": 0, "unresolved_usd": 0.0, "gemini_usd": 0.0}
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
        if e["provider"] == "gemini":
            t["gemini_usd"] += usd
        if e["provider"] == "qloo":
            t["qloo"] += 1
            if e.get("month_remaining") is not None:
                t["qloo_month_remaining"] = e["month_remaining"]
        elif e["provider"] in FETCH_LIKE:
            t["fetch"] += 1
        elif e["provider"] == "bedrock" and e["operation"] == "Converse":
            t["bedrock_calls"] += 1
    return t


def ic_used(pool: str, project_id: str | None = None, entries: list[dict] | None = None) -> dict:
    """Influencers.club requests and credits a pool (or one project in it) has used. Every dispatch counts, failures
    included; an open reservation counts at its reserved credits; a project's count leaves out location lookups."""
    entries = _entries() if entries is None else entries
    done = {e.get("reservation"): e for e in entries if e.get("kind") == "attempt" and e.get("provider") == IC}
    u = {"requests": 0, "credits": 0.0}
    for e in entries:
        if e.get("provider") != IC or e.get("pool") != pool:
            continue
        if e.get("kind") == "carry_in" and project_id is None:
            u["requests"] += e["requests"]
            u["credits"] += e["credits"]
        if e.get("kind") != "reserve" or (project_id and (e.get("project_id") != project_id or e.get("lookup"))):
            continue
        fin = done.get(e["id"])
        u["requests"] += 1
        u["credits"] += fin["credits_charged"] if fin else e["credits_reserved"]
    u["credits"] = round(u["credits"], 4)
    return u


def ic_left(pool: str, project_id: str | None = None) -> dict:
    """What a pool can still admit; for a judge project, the smaller of the pool and the project's own cap."""
    used, cap = ic_used(pool), IC_POOLS[pool]
    left = {k: round(cap[k] - used[k], 4) for k in cap}
    if pool == "judges" and project_id:
        pu = ic_used(pool, project_id)
        left = {k: min(left[k], round(IC_JUDGE_PROJECT[k] - pu[k], 4)) for k in left}
    return left


def _admit_ic(pool: str, credits: float, project_id: str | None, lookup: bool) -> None:
    if config.env("LANDFALL_IC_ADMISSION", "open") != "open":  # dev after the hosted handover, or a restore not yet reconciled
        raise BudgetExhausted("Influencers.club admission is closed here")
    if pool not in IC_POOLS:
        raise BudgetExhausted(f"no Influencers.club pool {pool}")
    left = ic_left(pool, None if lookup else project_id)
    if left["requests"] < 1 or left["credits"] < credits - 1e-9:
        raise BudgetExhausted(f"the {pool} creator-discovery allowance is used ({left['requests']:g} requests, {left['credits']:g} credits left)")


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
        if provider == IC:  # its own allocation (owner, 26 Oct 2026), not §8.1's dollars or expiry
            _admit_ic(fields["pool"], fields["credits_reserved"], fields.get("project_id"), bool(fields.get("lookup")))
        elif provider == "gemini":  # its own US$15 (owner, 27 Oct 2026), not §8.1's expiry
            g = totals()["gemini_usd"]
            if g + exposure_usd > config.CAP_GEMINI_USD:
                raise BudgetExhausted(f"Gemini cap reached (${g:.4f} + ${exposure_usd:.4f} > ${config.CAP_GEMINI_USD:g})")
        elif _now() > config.EXPIRES:
            raise BudgetExhausted("live authority expired")
        t = totals()
        if provider == "bedrock" and t["usd"] - t["gemini_usd"] + exposure_usd > config.CAP_USD:
            raise BudgetExhausted(f"usd cap reached (${t['usd']:.4f} + ${exposure_usd:.4f} reserve > ${config.CAP_USD:g})")
        if provider in ("qloo", *FETCH_LIKE):
            key = "qloo" if provider == "qloo" else "fetch"
            cap = config.CAP_QLOO if key == "qloo" else config.CAP_FETCH
            if t[key] + 1 > cap:
                raise BudgetExhausted(f"{key} cap reached ({t[key]}/{cap})")
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


def ic_carry_in(pool: str, requests: int, credits: float, text: str) -> None:
    """The opening entry of a new ledger (the hosted handover, P66-R1): what was already spent elsewhere."""
    with _locked():
        _append({"kind": "carry_in", "at": _now().isoformat(), "provider": IC, "pool": pool, "requests": requests,
                 "credits": credits, "text": text}, totals())


def note(text: str) -> None:
    with _locked():
        with config.LEDGER_PATH.open("a") as f:
            f.write(json.dumps({"kind": "accounting_note", "at": _now().isoformat(), "text": text}) + "\n")


def nova_cost(input_tokens: int, output_tokens: int, grounding_calls: int = 0, model: str | None = None) -> float:
    p = PRICE_TABLE
    m = p["nova_premier"] if "premier" in (model or "") else p["nova_pro"] if "nova-pro" in (model or "") else p["nova_2_lite"]
    if model and not any(k in model for k in ("premier", "nova-pro", "nova-2-lite")):
        raise ValueError(f"no verified price for {model}")  # never a guessed price for an unknown model
    return (input_tokens / 1000 * m["input_per_1k"] + output_tokens / 1000 * m["output_per_1k"]
            + grounding_calls * p["nova_grounding_per_request"])


def report() -> str:
    t = totals()
    return (f"Bedrock ${t['usd'] - t['gemini_usd']:.4f} of ${config.CAP_USD:g} ({t['bedrock_calls']} calls) · "
            f"Gemini ${t['gemini_usd']:.4f} of ${config.CAP_GEMINI_USD:g} · "
            f"Qloo {t['qloo']} of {config.CAP_QLOO} attempts (month remaining: {t['qloo_month_remaining']}) · "
            f"Fetches {t['fetch']} of {config.CAP_FETCH} · unresolved {t['unresolved']} (≤ ${t['unresolved_usd']:.4f})"
            + "".join(f" · Influencers.club {k}: {u['requests']} of {c['requests']} requests, {u['credits']:g} of {c['credits']:g} credits"
                      for k, c in IC_POOLS.items() for u in [ic_used(k)]))


if __name__ == "__main__":
    print(report())
