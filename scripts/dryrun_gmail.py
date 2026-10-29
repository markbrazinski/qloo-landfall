"""Slice 6.7 G5 step 1 · private dry run, no provider call: for each project's connected Gmail, build the provider plan of
its outreach packages from the real config and print only whether the actual recipients are exactly the configured test
inbox and the connected sender is the configured test sender. No address is printed.

    uv run python -m scripts.dryrun_gmail [project_id]"""
import base64
import email
import email.policy
import sys

from landfall import db, delivery
from landfall.adapters.gmail import email_routing

rt = email_routing()
print(f"mode: {rt['mode']} · test addresses configured: {'yes' if rt.get('to') else 'no'}")
pids = sys.argv[1:] or [r["project_id"] for r in db.q("SELECT DISTINCT project_id FROM records WHERE kind='connection'")]
for pid in pids:
    c = delivery.conn_for(pid, "EMAIL")
    if not c or c.get("simulated"):
        continue
    sender_ok = (c.get("account_identity") or {}).get("email", "").lower() == (rt.get("sender") or "").lower()
    for pkg in delivery.packages_of(pid):
        if pkg["flow"] != "outreach":
            continue
        plan = delivery.provider_plan(pid, pkg, "EMAIL")
        to_ok = False
        if plan["requests"]:
            m = email.message_from_bytes(base64.urlsafe_b64decode(plan["requests"][0]["json"]["raw"]), policy=email.policy.default)
            to_ok = [a.lower() for a in [m["To"]]] == [(rt.get("to") or "").lower()] and m["Cc"] is None and m["Bcc"] is None
        print(f"{pid} · {pkg['id']} v{pkg['version']}: recipients match the configured test inbox: {'yes' if to_ok else 'no'} · "
              f"sender matches: {'yes' if sender_ok else 'no'} · blockers: {len(plan['blockers'])}")
