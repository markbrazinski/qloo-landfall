"""One record/replay boundary for every provider exchange (BUILD-PLAN §5).

Modes: `record` calls live and appends redacted exchanges to a case directory; `live` calls without writing;
`replay` answers only from recorded exchanges and raises NoRecording on a miss (never a silent live call).
"""
import hashlib
import json
import threading
from pathlib import Path

REDACT_HEADERS = {"x-api-key", "authorization", "x-amz-security-token", "x-amz-date", "cookie", "set-cookie"}


class NoRecording(Exception):
    pass


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
        for d in replay_dirs:
            # split on "\n" only: page text may hold U+2028/U+0085, which splitlines() would treat as line breaks
            for line in filter(None, (Path(d) / "exchanges.jsonl").read_text().split("\n")):
                ex = json.loads(line)
                self._index.setdefault(ex["key"], ex)
                self._origin.setdefault(ex["key"], str(d))
        self.misses: list[str] = []

    def call(self, provider: str, op: str, request: dict, live_fn, stage: str = "") -> dict:
        """`request` must be canonical (no secrets, no timestamps). `live_fn()` returns a JSON-able response dict."""
        key = key_of(provider, op, request)
        if self.mode == "replay":
            ex = self._index.get(key)
            if ex is not None:
                self.reused(key, ex)
                return ex["response"]
            if stage not in self.live_stages:
                self.misses.append(f"{provider}:{op}")
                raise NoRecording(f"no recorded {provider} {op} exchange for this request ({key[:12]})")
        response = live_fn()
        if self.mode != "live" and self.record_dir is not None:
            self.write(key, provider, op, request, response, stage)
        return response

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

    def lookup(self, key: str) -> dict | None:
        return self._index.get(key)
