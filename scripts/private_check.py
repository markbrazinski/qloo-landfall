"""Slice 6.7 G4 private repository check: the configured test addresses (read in memory from .env, never printed) must
not appear in any tracked file or in the built frontend. Prints only a count; expected 0."""
import subprocess
import sys

from landfall import config

values = [v for v in (config.env("gmail_test_sender"), config.env("gmail_test_recipient")) if v]
hits = 0
for v in values:
    for args in (["git", "grep", "-F", "-c", "-i", v], ["grep", "-r", "-F", "-c", "-i", v, "web/dist"]):
        out = subprocess.run(args, cwd=config.ROOT, capture_output=True, text=True).stdout
        hits += sum(int(line.rsplit(":", 1)[1]) for line in out.splitlines() if ":" in line)
print(f"configured values checked: {len(values)} · occurrences in tracked files and web/dist: {hits}")
sys.exit(1 if hits else 0)
