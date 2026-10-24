# Landfall developer commands. `make dev` reads live providers and records exchanges privately (uses the active authority).
.PHONY: setup web dev demo test e2e usage

setup:            ## install Python and web dependencies and the test browser
	uv sync
	cd web && npm install
	uv run playwright install chromium

web:              ## build the frontend into web/dist (served by the API)
	cd web && npm run build

dev: web          ## live app on http://localhost:8000 (records provider exchanges to live-captures-private/)
	uv run uvicorn landfall.app:app --port 8000

demo: web         ## replay-only app on http://localhost:8003: no provider calls, no credentials needed
	LANDFALL_DB=.landfall/demo.db LANDFALL_PROVIDER_MODE=replay uv run uvicorn landfall.app:app --port 8003

test:             ## unit + integration checks (replay; no live calls)
	uv run pytest -q

e2e: web          ## browser journey against a replay server (synthetic Tidewell demo)
	rm -f .landfall/e2e.db*; LANDFALL_DB=.landfall/e2e.db LANDFALL_PROVIDER_MODE=replay uv run uvicorn landfall.app:app --port 8004 & \
	  pid=$$!; sleep 4; uv run python -m scripts.journey http://localhost:8004 demo .landfall/e2e "Portland, Oregon"; rc=$$?; kill $$pid; exit $$rc

usage:            ## live spend and request counts against the active authority
	uv run python -m landfall.ledger
