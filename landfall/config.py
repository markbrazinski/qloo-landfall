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
# Slice 6 copy writer and its sentence check (owner, 26 Oct 2026: "a better model" for ad copy). Checked 26 Oct 2026:
# Nova 2 Pro is not available to this account, and Nova Premier is end-of-life (Converse refuses it). Nova Pro is the
# larger Nova model that answers; chosen against Nova 2 Lite on the L6 cases (SLICE-06-REPORT).
NOVA_COPY_MODEL = env("LANDFALL_COPY_MODEL", "us.amazon.nova-pro-v1:0")
QLOO_BASE = (env("qloo_server") or "").rstrip("/")
QLOO_KEY = env("api_key") or ""

DB_PATH = Path(env("LANDFALL_DB", str(ROOT / ".landfall" / "landfall.db")))
LEDGER_PATH = Path(env("LANDFALL_LEDGER", str(PRIVATE / "usage-ledger.jsonl")))

# Active authority (BUILD-PLAN §8.1). One cumulative allowance across sessions.
CAP_USD = float(env("LANDFALL_CAP_USD", "75"))
CAP_QLOO = int(env("LANDFALL_CAP_QLOO", "2500"))
CAP_FETCH = int(env("LANDFALL_CAP_FETCH", "3000"))
# Gemini mockup scenes (owner, 27 Oct 2026: "add budget of 15 dollars"): its own cap, not §8.1's expiry.
CAP_GEMINI_USD = float(env("LANDFALL_CAP_GEMINI_USD", "15"))
QLOO_FLOOR = int(env("LANDFALL_QLOO_FLOOR", "1000"))
EXPIRES = datetime.fromisoformat(env("LANDFALL_AUTHORITY_EXPIRES", "2026-10-30T23:59:00-07:00"))

# record | live | replay. Tests and the demo force replay.
PROVIDER_MODE = env("LANDFALL_PROVIDER_MODE", "record")
# Mixed replay/live (BUILD-PLAN §5): projects entered by URL replay these accepted case dirs at every provider boundary,
# and only the named stages may go live on a miss (recorded to live-captures-private/runs/<project>/).
REPLAY_CASES = [Path(d) for d in (env("LANDFALL_REPLAY_CASES") or "").split(",") if d]
LIVE_STAGES = tuple(s for s in (env("LANDFALL_LIVE_STAGES") or "").split(",") if s)
# Seconds to wait per replayed page or model exchange, so a replay paces like a read (browser checks of "while reading").
REPLAY_DELAY = float(env("LANDFALL_REPLAY_DELAY") or 0)
# Only the synthetic-site recorder sets this; production never fetches private addresses.
ALLOW_PRIVATE_FETCH = env("LANDFALL_ALLOW_PRIVATE_FETCH") == "1"
# e.g. "tidewell.example=127.0.0.1:8791": serves the fictional synthetic site locally; honoured only with the flag above.
HOST_MAP = {k: (v.split(":")[0], int(v.split(":")[1])) for k, v in
            (pair.split("=") for pair in (env("LANDFALL_HOST_MAP") or "").split(",") if "=" in pair)}
# Owner, 27 Oct 2026: choosing a beachhead builds the plan by itself (landfall/autoplan.py). Tests drive each step.
AUTO_PLAN = env("LANDFALL_AUTO_PLAN", "1") == "1"
