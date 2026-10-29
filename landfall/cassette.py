"""One record/replay boundary for every provider exchange (BUILD-PLAN §5).

Modes: `record` calls live and appends redacted exchanges to a case directory; `live` calls without writing;
`replay` answers only from recorded exchanges and raises NoRecording on a miss (never a silent live call).
"""
import copy
import functools
import hashlib
import json
import threading
from pathlib import Path

REDACT_HEADERS = {"x-api-key", "authorization", "x-amz-security-token", "x-amz-date", "cookie", "set-cookie"}


class NoRecording(Exception):
    pass


def _stamp(d: Path) -> float:
    return max((f.stat().st_mtime for f in (d / "exchanges.jsonl", d / "exchanges.jsonl.gz") if f.exists()), default=0)


@functools.lru_cache(maxsize=8)
def _parsed(d: str, _mtime: float) -> tuple[dict, ...]:
    """Parsed once per case file version: every run and many requests open a cassette, and a demo case is ~100 MB.
    ponytail: 8 cases held in memory, shared by cassettes, so a served response is a copy.
    Split on "\n" only: page text may hold U+2028/U+0085, which splitlines() would treat as line breaks."""
    return tuple(json.loads(line) for line in read_exchanges(Path(d)).split("\n") if line)


def read_exchanges(d: Path) -> str:
    """A case's exchanges.jsonl, or exchanges.jsonl.gz (the recorded demo cases: Qloo answers make them ~100 MB raw)."""
    gz = d / "exchanges.jsonl.gz"
    if gz.exists():
        import gzip
        return gzip.decompress(gz.read_bytes()).decode()
    return (d / "exchanges.jsonl").read_text()


def key_of(provider: str, op: str, request: dict) -> str:
    canon = json.dumps({"p": provider, "op": op, "r": request}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode()).hexdigest()


def redact_headers(headers: dict) -> dict:
    return {k: ("<redacted>" if k.lower() in REDACT_HEADERS else v) for k, v in headers.items()}


class Cassette:
    def __init__(self, mode: str, record_dir: Path | None = None, replay_dirs: list[Path] = (),
                 live_stages: tuple[str, ...] = ()):
        """In replay mode, `live_stages` names the only stages allowed to go live on a miss (e.g. a changed checker);
        with a `record_dir`, replayed and new exchanges are both written there with their provenance."""
        assert mode in {"record", "live", "replay"}
        self.mode = mode
        self.record_dir = record_dir
        self.live_stages = set(live_stages)
        self._origin: dict[str, str] = {}
        self._lock = threading.Lock()
        self._index: dict[str, dict] = {}
        self._repeats: dict[str, list[dict]] = {}  # later exchanges recorded for the same key, in order
        self._served: dict[tuple, int] = {}
        for d in replay_dirs:
            for ex in _parsed(str(d), _stamp(Path(d))):
                if ex["key"] in self._index:
                    self._repeats.setdefault(ex["key"], []).append(ex)
                self._index.setdefault(ex["key"], ex)
                self._origin.setdefault(ex["key"], str(d))
        self.misses: list[str] = []

    def call(self, provider: str, op: str, request: dict, live_fn, stage: str = "") -> dict:
        """`request` must be canonical (no secrets, no timestamps). `live_fn()` returns a JSON-able response dict."""
        key = key_of(provider, op, request)
        if self.mode == "replay":
            ex = self.recorded(key, stage)
            if ex is not None:
                self.reused(key, ex)
                self.pace(provider)
                return copy.deepcopy(ex["response"])
            if stage not in self.live_stages:
                self.misses.append(f"{provider}:{op}")
                raise NoRecording(f"no recorded {provider} {op} exchange for this request ({key[:12]})")
        response = live_fn()
        if self.mode != "live" and self.record_dir is not None:
            self.write(key, provider, op, request, response, stage)
        return response

    def pace(self, provider: str) -> None:
        """Optional replay pacing for pages and model calls (LANDFALL_REPLAY_DELAY); Qloo answers stay immediate."""
        from . import config
        if config.REPLAY_DELAY and provider in ("fetch", "bedrock"):
            import time
            time.sleep(config.REPLAY_DELAY)

    def reused(self, key: str, ex: dict) -> None:
        """When building a replacement case, carry a replayed exchange over with a pointer to its original."""
        if self.record_dir is not None:
            self.write(key, ex["provider"], ex["op"], ex["request"], ex["response"], ex.get("stage", ""),
                       provenance={"reused_from": self._origin.get(key)})

    def write(self, key, provider, op, request, response, stage="", provenance=None):
        from datetime import datetime, timezone
        provenance = provenance or {"obtained": "live", "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        line = json.dumps({"key": key, "stage": stage, "provider": provider, "op": op, "provenance": provenance,
                           "request": request, "response": response}, ensure_ascii=False)
        with self._lock:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            with (self.record_dir / "exchanges.jsonl").open("a") as f:
                f.write(line + "\n")

    def lookup(self, key: str, stage: str | None = None) -> dict | None:
        return self.recorded(key, stage)

    def recorded(self, key: str, stage: str | None = None) -> dict | None:
        """The recorded exchanges for a key in their recorded order (a 429 then the retry's 200, LIVE L4-A), then the last
        one again. A key recorded once answers as it always did. With a `stage`, that stage's own recordings come first:
        the same page read by research and then by a specialist can differ (a fresh error reference, a later retry), and
        a run's cassette starts its count at zero, so without it the specialist replays research's copy (demo cases)."""
        first = self._index.get(key)
        if first is None or key not in self._repeats:
            return first
        seq = [first, *self._repeats[key]]
        seq = [e for e in seq if e.get("stage") == stage] or seq
        with self._lock:
            n = self._served.get((key, stage), 0)
            self._served[(key, stage)] = n + 1
        return seq[min(n, len(seq) - 1)]
