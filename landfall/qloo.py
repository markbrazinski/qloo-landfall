"""Qloo adapter: one GET per attempt through the cassette and the ledger. The key never leaves this module."""
import time

import httpx

from . import config, ledger
from .cassette import Cassette

KEEP = ("content-type", "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining",
        "x-month-ratelimit-limit", "x-month-ratelimit-remaining", "x-second-ratelimit-limit", "x-second-ratelimit-remaining")
RETRYABLE = {429, 500, 502, 503, 504}


def _attempt(path: str, params: dict) -> dict:
    rid = ledger.reserve("qloo", path)
    out = {"status": 0, "headers": {}, "body": "", "error": "aborted"}
    try:
        r = httpx.get(config.QLOO_BASE + path, params=params, timeout=20,
                      headers={"X-Api-Key": config.QLOO_KEY, "Accept": "application/json"})
        out = {"status": r.status_code, "headers": {k: r.headers[k] for k in KEEP if k in r.headers}, "body": r.text}
    except httpx.HTTPError as e:
        out = {"status": 0, "headers": {}, "body": "", "error": type(e).__name__}
    finally:
        remaining = out["headers"].get("x-month-ratelimit-remaining")
        ledger.finalize(rid, "qloo", path, status=out["status"], cost_known=False,
                        outcome=str(out["status"]) if out["status"] else "transport_error",
                        month_remaining=int(remaining) if remaining and remaining.isdigit() else None)
    return out


def _live(path: str, params: dict) -> dict:
    """Up to 3 attempts on 429/5xx/network errors, honouring Retry-After; every attempt is ledgered."""
    for n in range(3):
        out = _attempt(path, params)
        if out["status"] not in RETRYABLE and out["status"] != 0:
            return out
        if n < 2:
            wait = out["headers"].get("retry-after")
            time.sleep(float(wait) if wait and wait.replace(".", "").isdigit() else 0.5 * 2 ** n)
    return out


def get(path: str, params: dict, cassette: Cassette, stage: str = "") -> dict:
    params = {k: str(v) for k, v in sorted(params.items())}
    return cassette.call("qloo", path, {"path": path, "params": params}, lambda: _live(path, params), stage)
