# Landfall developer commands. `make dev` reads live providers and records exchanges privately (uses the active authority).
.PHONY: setup web dev demo test e2e e2e-cases e2e-slice3 e2e-slice35 live-slice4 e2e-slice4 live-slice45 e2e-slice45 live-slice6 e2e-slice6 e2e-slice65 live-slice5 e2e-slice5 usage check-private dryrun-gmail owner-connect owner-status

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

RESEARCH_STAGES = research,research_search,research_fetch,research_places,research_urns,research_check

live-slice4: web  ## slice-4 live cases L4-A/L4-B from S01: upstream replays L35-A/L35-B v3, only the research stages go live (spends under the active authority)
	rm -f .landfall/l4.db*; LANDFALL_DB=.landfall/l4.db LANDFALL_REPLAY_DELAY=0.5 \
	  LANDFALL_REPLAY_CASES=live-captures-private/cases/L35-A-untuckit/v3,live-captures-private/cases/L35-B-gotbag/v3 \
	  LANDFALL_LIVE_STAGES=$(RESEARCH_STAGES) uv run uvicorn landfall.app:app --host 127.0.0.1 --port 8020 & pid=$$!; sleep 4; \
	  uv run python -m scripts.journey_slice4 http://127.0.0.1:8020 untuckit .landfall/l4a-live; a=$$?; \
	  uv run python -m scripts.journey_slice4 http://127.0.0.1:8020 gotbag .landfall/l4b-live; b=$$?; \
	  kill $$pid; exit $$((a || b))

e2e-slice4: web   ## slice-4 journeys replaying the accepted L4-A/L4-B cases from S01 into a fresh database and empty cache (no provider calls)
	rm -f .landfall/slice4.db*; LANDFALL_DB=.landfall/slice4.db LANDFALL_PROVIDER_MODE=replay LANDFALL_REPLAY_DELAY=0.2 \
	  LANDFALL_REPLAY_CASES=live-captures-private/cases/L4-A-untuckit/v2,live-captures-private/cases/L4-B-gotbag/v2 \
	  uv run uvicorn landfall.app:app --host 127.0.0.1 --port 8021 & pid=$$!; sleep 4; \
	  uv run python -m scripts.journey_slice4 http://127.0.0.1:8021 untuckit .landfall/e2e-l4a-replay && \
	  uv run python -m scripts.journey_slice4 http://127.0.0.1:8021 gotbag .landfall/e2e-l4b-replay; \
	  rc=$$?; kill $$pid; exit $$rc

SPECIALIST_STAGES = products_fetch,specialist,specialist_search,specialist_fetch,specialist_places,specialist_check,creator_profile,youtube_channel,develop

live-slice45: web ## slice-4.5 live cases L45-A/L45-B from S01: upstream replays L4-A/L4-B v2, only the specialist stages go live (spends under the active authority)
	rm -f .landfall/l45.db*; LANDFALL_DB=.landfall/l45.db LANDFALL_REPLAY_DELAY=0.2 \
	  LANDFALL_REPLAY_CASES=live-captures-private/cases/L4-A-untuckit/v2,live-captures-private/cases/L4-B-gotbag/v2 \
	  LANDFALL_LIVE_STAGES=$(SPECIALIST_STAGES) uv run uvicorn landfall.app:app --host 127.0.0.1 --port 8022 & pid=$$!; sleep 4; \
	  uv run python -m scripts.journey_slice45 http://127.0.0.1:8022 untuckit .landfall/l45a-live; a=$$?; \
	  uv run python -m scripts.journey_slice45 http://127.0.0.1:8022 gotbag .landfall/l45b-live; b=$$?; \
	  kill $$pid; exit $$((a || b))

e2e-slice45: web  ## slice-4.5 journeys replaying L45-A/L45-B v1 from S01 into a fresh database (no provider calls)
	rm -f .landfall/slice45.db*; LANDFALL_DB=.landfall/slice45.db LANDFALL_PROVIDER_MODE=replay LANDFALL_REPLAY_DELAY=0.05 \
	  LANDFALL_REPLAY_CASES=live-captures-private/cases/L45-A-untuckit/v3,live-captures-private/cases/L45-B-gotbag/v2 \
	  uv run uvicorn landfall.app:app --host 127.0.0.1 --port 8023 & pid=$$!; sleep 4; \
	  uv run python -m scripts.journey_slice45 http://127.0.0.1:8023 untuckit .landfall/e2e-l45a-replay && \
	  uv run python -m scripts.journey_slice45 http://127.0.0.1:8023 gotbag .landfall/e2e-l45b-replay; \
	  rc=$$?; kill $$pid; exit $$rc

DELIVERY_STAGES = develop,specialist_check,delivery_copy,delivery_fetch

live-slice6: web  ## slice-6 preparation cases L6-A/L6-B from S01: upstream replays L45-B v2/L45-A v3, only the delivery stages go live (spends under the active authority; nothing is sent)
	rm -f .landfall/l6.db*; LANDFALL_DB=.landfall/l6.db LANDFALL_REPLAY_DELAY=0.05 \
	  LANDFALL_REPLAY_CASES=live-captures-private/cases/L45-A-untuckit/v3,live-captures-private/cases/L45-B-gotbag/v2 \
	  LANDFALL_LIVE_STAGES=$(DELIVERY_STAGES) uv run uvicorn landfall.app:app --host 127.0.0.1 --port 8024 & pid=$$!; sleep 4; \
	  uv run python -m scripts.journey_slice6 http://127.0.0.1:8024 gotbag .landfall/l6a-live; a=$$?; \
	  uv run python -m scripts.journey_slice6 http://127.0.0.1:8024 untuckit .landfall/l6b-live; b=$$?; \
	  kill $$pid; exit $$((a || b))

e2e-slice65: web  ## slice-6.5 journeys (Brand → Markets → Plan → Act) replaying L6-A/L6-B v4 from S01 into a fresh database (no provider calls)
	rm -f .landfall/slice65.db*; LANDFALL_DB=.landfall/slice65.db LANDFALL_PROVIDER_MODE=replay LANDFALL_REPLAY_DELAY=0.05 \
	  LANDFALL_REPLAY_CASES=live-captures-private/cases/L6-A-gotbag/v4,live-captures-private/cases/L6-B-untuckit/v4 \
	  uv run uvicorn landfall.app:app --host 127.0.0.1 --port 8027 & pid=$$!; sleep 4; \
	  uv run python -m scripts.journey_slice65 http://127.0.0.1:8027 gotbag .landfall/e2e-l65a-replay && \
	  uv run python -m scripts.journey_slice65 http://127.0.0.1:8027 untuckit .landfall/e2e-l65b-replay; \
	  rc=$$?; kill $$pid; exit $$rc

STRATEGY_STAGES = launch_strategist,strategist_check,fx_ecb

live-slice5: web  ## slice-5 live cases L5-A/L5-B from S01: upstream replays L6-A/L6-B v4, only the strategist and ECB stages go live (spends under the active authority; nothing is sent)
	rm -f .landfall/l5.db*; LANDFALL_DB=.landfall/l5.db LANDFALL_REPLAY_DELAY=0.05 \
	  LANDFALL_REPLAY_CASES=live-captures-private/cases/L6-A-gotbag/v4,live-captures-private/cases/L6-B-untuckit/v4 \
	  LANDFALL_LIVE_STAGES=$(STRATEGY_STAGES) uv run uvicorn landfall.app:app --host 127.0.0.1 --port 8028 & pid=$$!; sleep 4; \
	  uv run python -m scripts.journey_slice5 http://127.0.0.1:8028 gotbag .landfall/l5a-live; a=$$?; \
	  uv run python -m scripts.journey_slice5 http://127.0.0.1:8028 untuckit .landfall/l5b-live; b=$$?; \
	  kill $$pid; exit $$((a || b))

e2e-slice5: web   ## slice-5 journeys replaying the locked L5-A/L5-B v1 from S01 into a fresh database (no provider calls)
	rm -f .landfall/slice5.db*; LANDFALL_DB=.landfall/slice5.db LANDFALL_PROVIDER_MODE=replay LANDFALL_REPLAY_DELAY=0.05 \
	  LANDFALL_REPLAY_CASES=live-captures-private/cases/L5-A-gotbag/v1,live-captures-private/cases/L5-B-untuckit/v1 \
	  uv run uvicorn landfall.app:app --host 127.0.0.1 --port 8029 & pid=$$!; sleep 4; \
	  uv run python -m scripts.journey_slice5 http://127.0.0.1:8029 gotbag .landfall/e2e-l5a-replay && \
	  uv run python -m scripts.journey_slice5 http://127.0.0.1:8029 untuckit .landfall/e2e-l5b-replay; \
	  rc=$$?; kill $$pid; exit $$rc

e2e-slice6: web   ## slice-6 journeys replaying L6-A/L6-B v4 from S01 into a fresh database (no provider calls)
	rm -f .landfall/slice6.db*; LANDFALL_DB=.landfall/slice6.db LANDFALL_PROVIDER_MODE=replay LANDFALL_REPLAY_DELAY=0.05 \
	  LANDFALL_REPLAY_CASES=live-captures-private/cases/L6-A-gotbag/v4,live-captures-private/cases/L6-B-untuckit/v4 \
	  uv run uvicorn landfall.app:app --host 127.0.0.1 --port 8025 & pid=$$!; sleep 4; \
	  uv run python -m scripts.journey_slice6 http://127.0.0.1:8025 gotbag .landfall/e2e-l6a-replay && \
	  uv run python -m scripts.journey_slice6 http://127.0.0.1:8025 untuckit .landfall/e2e-l6b-replay; \
	  rc=$$?; kill $$pid; exit $$rc

usage:            ## live spend and request counts against the active authority
	uv run python -m landfall.ledger

check-private:    ## 6.7: the configured test addresses appear in no tracked file or web/dist (prints a count only)
	uv run python -m scripts.private_check

dryrun-gmail:     ## 6.7 G5: yes/no that a connected Gmail's plans route only to the test inbox (no provider call)
	uv run python -m scripts.dryrun_gmail

owner-connect:    ## G-J: owner signs in once for shared judge delivery, DST=EMAIL|GOOGLE_ADS (run where the DB is; prints the link)
	uv run python -m scripts.owner_connect connect $(DST)

owner-status:     ## G-J: shared connections without secrets (state, label, dates, 7-day Testing-mode expiry, allowance)
	uv run python -m scripts.owner_connect status

.PHONY: access-create access-list access-revoke
access-create:    ## A1 owner: make access-create KIND=judge|owner [LABEL=judges] (prints the link once)
	uv run python -m scripts.access create $(KIND) $(LABEL)

access-list:      ## A1 owner: list judge/owner links (hashes only)
	uv run python -m scripts.access list

access-revoke:    ## A1 owner: make access-revoke LABEL=judges (bound browsers lose access on their next request)
	uv run python -m scripts.access revoke $(LABEL)
