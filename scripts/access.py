"""Owner CLI for judge and owner links (DEPLOYMENT-PLAN §5, A1). Run on the host (over SSM) against its database.

usage: python -m scripts.access create judge|owner [label]
       python -m scripts.access list
       python -m scripts.access revoke <label or hash>
A created link is printed once and never stored; lose it and revoke it, then create a new one."""
import sys

from landfall import config, grants


def main(argv: list[str]) -> int:
    cmd, *args = argv or ["help"]
    if cmd == "create" and args and args[0] in grants.KINDS:
        kind, label = args[0], (args[1] if len(args) > 1 else ("judges" if args[0] == "judge" else "owner"))
        raw = grants.create(kind, label)
        base = (config.env("LANDFALL_PUBLIC_URL") or "http://127.0.0.1:8000").rstrip("/")
        print(f"{kind} link '{label}' (shown once; only its hash is kept):\n{base}/?{kind}_id={raw}")
        return 0
    if cmd == "list":
        for g in grants.list():
            print(f"{g['label']:<16} {g['kind']:<6} created {g['created_at']}  "
                  f"{'revoked ' + g['revoked_at'] if g['revoked_at'] else 'active'}  {g['hash']}")
        return 0
    if cmd == "revoke" and len(args) == 1:
        n = grants.revoke(args[0])
        print(f"revoked {n} link(s); bound browsers lose access on their next request")
        return 0 if n else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
