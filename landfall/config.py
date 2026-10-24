"""Settings from the environment and the private `.env` (values are never printed or logged)."""
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "live-captures-private"


def _dotenv() -> dict:
    path = ROOT / ".env"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


_ENV = {**_dotenv(), **os.environ}


def env(name: str, default: str | None = None) -> str | None:
    return _ENV.get(name, default)


AWS_PROFILE = env("AWS_PROFILE", "landfall")
AWS_REGION = env("AWS_REGION", "us-east-1")
NOVA_MODEL = env("LANDFALL_NOVA_MODEL", "us.amazon.nova-2-lite-v1:0")
QLOO_BASE = (env("qloo_server") or "").rstrip("/")
QLOO_KEY = env("api_key") or ""

DB_PATH = Path(env("LANDFALL_DB", str(ROOT / ".landfall" / "landfall.db")))
LEDGER_PATH = Path(env("LANDFALL_LEDGER", str(PRIVATE / "usage-ledger.jsonl")))

# Active authority (BUILD-PLAN §8.1). One cumulative allowance across sessions.
CAP_USD = float(env("LANDFALL_CAP_USD", "75"))
CAP_QLOO = int(env("LANDFALL_CAP_QLOO", "2500"))
CAP_FETCH = int(env("LANDFALL_CAP_FETCH", "3000"))
QLOO_FLOOR = int(env("LANDFALL_QLOO_FLOOR", "1000"))
EXPIRES = datetime.fromisoformat(env("LANDFALL_AUTHORITY_EXPIRES", "2026-10-30T23:59:00-07:00"))

# record | live | replay. Tests and the demo force replay.
PROVIDER_MODE = env("LANDFALL_PROVIDER_MODE", "record")
# Only the synthetic-site recorder sets this; production never fetches private addresses.
ALLOW_PRIVATE_FETCH = env("LANDFALL_ALLOW_PRIVATE_FETCH") == "1"
# e.g. "tidewell.example=127.0.0.1:8791": serves the fictional synthetic site locally; honoured only with the flag above.
HOST_MAP = {k: (v.split(":")[0], int(v.split(":")[1])) for k, v in
            (pair.split("=") for pair in (env("LANDFALL_HOST_MAP") or "").split(",") if "=" in pair)}
