# Landfall developer commands. Maintainer targets live in maint.mk.
.PHONY: setup web dev replay test usage

setup:            ## install Python and web dependencies and the test browser
	uv sync
	cd web && npm install
	uv run playwright install chromium

web:              ## build the frontend into web/dist (served by the API)
	cd web && npm run build

dev: web          ## live app on http://localhost:8000 (needs .env; records provider exchanges to live-captures-private/)
	uv run uvicorn landfall.app:app --port 8000

replay: web       ## recorded runs on http://localhost:8003: replayed, no provider calls, no credentials needed
	LANDFALL_DB=.landfall/replay.db LANDFALL_PROVIDER_MODE=replay LANDFALL_REQUIRE_GRANT=on uv run uvicorn landfall.app:app --port 8003

test: web         ## unit + integration checks (replay; no live calls; the static-route tests need web/dist)
	uv run pytest -q

usage:            ## live spend and request counts against the spending caps
	uv run python -m landfall.ledger

-include maint.mk
