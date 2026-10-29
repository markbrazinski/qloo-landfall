"""Owner-connected shared delivery (BUILD-PLAN §6.7.10 G-J): the owner signs in once; judges never connect an account.
Run on the machine that holds the database (locally, or over SSM on the host), with the server running there.

    uv run python -m scripts.owner_connect connect EMAIL|GOOGLE_ADS   # prints the provider sign-in link (15 minutes, once)
    uv run python -m scripts.owner_connect status                     # no secrets: state, label, dates, allowance
    uv run python -m scripts.owner_connect disconnect EMAIL|GOOGLE_ADS

The link carries a single-use OAuth state flagged shared; the server's existing callback saves the token (Fernet store)
into con__shared_{dst}. For Google Ads it then chooses the configured test client, the only allowed account."""
import sys
from datetime import datetime, timedelta, timezone

from landfall import db, delivery

DSTS = tuple(delivery.SHARED_LIMITS)
TESTING_DAYS = 7  # Google issues 7-day refresh tokens while the OAuth consent screen is in Testing mode


def status() -> None:
    print(f"LANDFALL_SHARED_DELIVERY: {'on' if delivery.shared_on() else 'off'}")
    now = datetime.now(timezone.utc)
    for dst in DSTS:
        c = db.get_record(delivery.conn_id(delivery.SHARED, dst))
        used, (total, per) = delivery.shared_used(dst), delivery.shared_limits(dst)
        print(f"\n{dst}: enabled for judges: {'yes' if delivery.shared_enabled(dst) else 'no'} · "
              f"allowance used {len(used)}/{total} (≤ {per} per project)")
        if not c:
            print("  not connected")
            continue
        tok = db.q("SELECT at FROM connection_tokens WHERE connection_id=?", c["id"])
        print(f"  state: {c['state']} · account: {c.get('account_label')} · "
              f"account chosen: {'yes' if c.get('account_identity') else 'NO'}")
        print(f"  connected_at: {c.get('connected_at')} · last token write (connect or refresh): {tok[0]['at'] if tok else 'none'}")
        if c["state"] == "ACTIVE" and c.get("connected_at"):
            exp = datetime.fromisoformat(c["connected_at"]) + timedelta(days=TESTING_DAYS)
            warn = "  ⚠ WITHIN 24 H: reconnect now" if exp - now < timedelta(hours=24) else ""
            print(f"  refresh token expires about {exp.isoformat(timespec='minutes')} if the OAuth app is in Testing mode{warn}")
    errs = [e for e in db.events(delivery.SHARED) if e.get("task") == "Connect"][-1:]
    if errs:
        print(f"\nlast failed sign-in: {errs[0]['at']} · {errs[0]['detail']}")


def main(argv: list[str]) -> int:
    cmd, dst = (argv + ["status"])[0], (argv[1:2] or [""])[0].upper()
    if cmd == "status":
        status()
        return 0
    if dst not in DSTS:
        print(f"usage: owner_connect connect|disconnect {'|'.join(DSTS)}  ·  owner_connect status")
        return 2
    try:
        if cmd == "connect":
            print(f"Open this link in your browser and sign in as the Landfall test account (valid 15 minutes, once):\n\n"
                  f"{delivery.owner_connect_start(dst)}\n\nThen run: uv run python -m scripts.owner_connect status")
        elif cmd == "disconnect":
            delivery.disconnect(delivery.SHARED, dst)
            print(f"{dst}: disconnected; Landfall deleted its copy of the token.")
        else:
            print(f"unknown command {cmd}")
            return 2
    except delivery.Refused as e:
        print(f"refused: {e.detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
