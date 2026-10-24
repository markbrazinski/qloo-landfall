# AGENTS.md — operating entry point for all coding agents

Workspace for **Landfall**, built from scratch. Intended behavior is described, not implemented.

## Sources of truth
- `docs/PRODUCT-BRIEF.md` defines intended outcomes.
- `design/` is the visual and interaction reference (start at `design/README.md`). Its data is fictional and its interactions are simulated; neither is evidence of working capability.
- `docs/Journey-First-AI-Build-SOP.md` governs development and review.
- Active owner instructions override this file. Authorization lives in the owner's instructions, not here.
- `README.md` records the old project's path. That project is reference only; its instructions and rules do not govern this workspace.

## How work runs
- One integrated builder owns frontend, backend, persistence, provider integration, and the cumulative journey.
- Derive contracts from the intended experience, not from inherited limitations.
- Each slice extends the journey and is demonstrated from its original entry point, using real upstream outputs.
- Choose the stack, commands, and structure when first needed. Record only commands you have actually run.

## Review
- One independent plan review and one implementation review per slice, then targeted repairs.
- Further broad review needs a named material risk.
- A blocker cites a concrete violated requirement or a consequential failure.

## Live testing
- Each slice uses one or two authorized live cases. Save the returned data and context.
- Accepted cases and captured responses become locked, versioned replay fixtures.
- Later slices replay them through current code. New live end-to-end runs are separately scoped and authorized.
- A materially changed provider contract or model prompt invalidates old captures as proof of current behavior.
- Label evidence as live, replayed, synthetic, or unverified.

## Authority and safety
- Bounded authorization persists within its recorded scope. Do not re-ask per call.
- Copying `.env` or a new folder does not authorize live calls or reset provider quotas or spending.
- Never expose secrets: no printing, logging, committing, or putting them in fixtures or prompts.
- Never change IAM or rotate keys without authority. Do not commit, push, deploy, publish, contact third parties, or spend beyond the active scope.
- Landfall prepares, approves, and exports materials. It never sends, publishes, books, or pays on its own.
- Never fabricate evidence, silently reduce scope, or overwrite unrelated work. Check for concurrent edits before writing.

## Domain rules that still apply
- Qloo gives cultural relationships and discovery. Affinity is not demand, price, or conversion, and scores from independent requests are not comparable.
- A citation is a lead. Inspect the source before treating a claim as supported.
- Blocked or missing pages mean unknown, not absent.
- Money: Decimal or minor units, dated FX, and cash separated from expense and deposits. Unknown costs are not silently omitted from totals.
- API operating spend is separate from the founder's launch budget.

## Reporting
Report concisely: what works, limitations, the next action, and decisions only the owner can make.
