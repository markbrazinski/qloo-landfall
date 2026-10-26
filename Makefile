# Landfall developer commands. `make dev` reads live providers and records exchanges privately (uses the active authority).
.PHONY: setup web dev demo test e2e e2e-cases e2e-slice3 e2e-slice35 usage

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

e2e-cases: web    ## slice-2 browser journeys replaying the private live cases L2-A/L2-B (owner's machine; no provider calls)
	rm -f .landfall/cases.db*; LANDFALL_DB=.landfall/cases.db LANDFALL_PROVIDER_MODE=replay LANDFALL_REPLAY_DELAY=0.5 \
	  LANDFALL_REPLAY_CASES=live-captures-private/cases/L2-A-komono/v1,live-captures-private/cases/L2-B-gotbag/v1 \
	  uv run uvicorn landfall.app:app --port 8005 & pid=$$!; sleep 4; \
	  uv run python -m scripts.journey_identity http://localhost:8005 matched komono.com .landfall/e2e-l2a && \
	  uv run python -m scripts.journey_identity http://localhost:8005 ask gotbag.com .landfall/e2e-l2b --remove Travel --search ocean --add Ocean; \
	  rc=$$?; kill $$pid; exit $$rc

e2e-slice3: web   ## slice-3 journeys replaying L3-A/L3-B from URL entry into a fresh database and empty cache (no provider calls)
	rm -f .landfall/slice3.db*; LANDFALL_DB=.landfall/slice3.db LANDFALL_PROVIDER_MODE=replay LANDFALL_REPLAY_DELAY=0.5 \
	  LANDFALL_REPLAY_CASES=live-captures-private/cases/L3-A-komono/v1,live-captures-private/cases/L3-B-gotbag/v1 \
	  uv run uvicorn landfall.app:app --port 8007 & pid=$$!; sleep 4; \
	  uv run python -m scripts.journey_markets http://localhost:8007 komono .landfall/e2e-l3a --home "Antwerp, Belgium" && \
	  uv run python -m scripts.journey_markets http://localhost:8007 gotbag .landfall/e2e-l3b; \
	  rc=$$?; kill $$pid; exit $$rc

e2e-slice35: web  ## slice-3.5 journeys replaying L35-A/L35-B from S01 into a fresh database and empty cache (no provider calls)
	rm -f .landfall/slice35.db*; LANDFALL_DB=.landfall/slice35.db LANDFALL_PROVIDER_MODE=replay LANDFALL_REPLAY_DELAY=0.5 \
	  LANDFALL_REPLAY_CASES=live-captures-private/cases/L35-A-untuckit/v3,live-captures-private/cases/L35-B-gotbag/v3 \
	  uv run uvicorn landfall.app:app --port 8009 & pid=$$!; sleep 4; \
	  uv run python -m scripts.journey_slice35 http://localhost:8009 untuckit .landfall/e2e-l35a-replay && \
	  uv run python -m scripts.journey_slice35 http://localhost:8009 gotbag .landfall/e2e-l35b-replay; \
	  rc=$$?; kill $$pid; exit $$rc

usage:            ## live spend and request counts against the active authority
	uv run python -m landfall.ledger
