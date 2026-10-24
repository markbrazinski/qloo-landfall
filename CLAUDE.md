# CLAUDE.md

Read `AGENTS.md` first; it is the shared operating agreement. Then `docs/PRODUCT-BRIEF.md`, and use `docs/Journey-First-AI-Build-SOP.md` for process and review. Design reference: `design/README.md`.

## Local environment
- `AWS_PROFILE=landfall` selects external AWS credentials. Never copy `~/.aws` into this workspace.
- `.env` holds provider configuration and is secret-bearing. Read it in memory only for authorized calls; never print it. `.env.example` lists names only.
- Preview the design by serving `design/` over HTTP (`python3 -m http.server 8000` from `design/`, then open `/prototype/Landfall.dc.html`). `file://` does not work.
- The workspace is not a Git repository yet, and no stack has been chosen.

## Claude-specific
- Confirm UI claims in a browser. Source inspection does not prove interaction.
- Use subagents for independent review, not for work you can verify directly.
- When asked to continue, re-read the owner's latest instruction before acting; it overrides stale notes.
