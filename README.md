# Landfall

*Your international launch team. Find where your brand belongs worldwide.*

Give Landfall a brand's website. Its team reads the brand, finds where in the world it belongs (with Qloo's cultural
relationships), proposes ways in, compares first launch tests, and prepares the outreach. The founder makes every call.

**Status (24 Oct 2026): slice 3 of 8 built, in review.** URL → a real, sourced brand brief → which Qloo brand you are
(matched automatically when only one confident match exists, otherwise asked while reading) or an editable discovery
profile of Qloo tags you confirm → 80 cities scanned with Qloo (one search per city, coverage shown honestly) → an
explained shortlist of up to six cities you can adjust. City research onward is later slices
([`docs/BUILD-PLAN.md`](docs/BUILD-PLAN.md)).

## Try it

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 20+.

```sh
make setup      # uv sync, npm install, Playwright's Chromium
make demo       # http://localhost:8003 · replays a recorded demo; no credentials, no provider calls
make test       # unit + integration checks (replay)
make e2e        # browser journey against the replay demo
make e2e-cases  # slice-2 journeys replaying the private live cases (owner's machine only)
make e2e-slice3 # slice-3 journeys replaying the private live cases into a fresh database (owner's machine only)
```

Live mode reads real sites with Amazon Nova 2 Lite on Bedrock and spends against the operating allowance:

```sh
cp .env.example .env   # fill in privately; filling it in does not authorize spending
make dev               # http://localhost:8000
make usage             # live spend and requests against the active authority
```

## How it is built

| Part | Choice |
|---|---|
| Models | Amazon Nova 2 Lite (`us.amazon.nova-2-lite-v1:0`) on Bedrock, via [Strands Agents](https://strandsagents.com) |
| Backend | Python 3.12, FastAPI, SQLite, an in-process durable run/task runner, Server-Sent Events |
| Frontend | React + Vite, the supplied Landfall design tokens and components |
| Evidence | Every provider exchange passes one record/replay boundary (`landfall/cassette.py`); live captures stay private, public fixtures are synthetic |

The plan and contracts live in [`docs/BUILD-PLAN.md`](docs/BUILD-PLAN.md) and [`docs/CONTRACTS.md`](docs/CONTRACTS.md).
Landfall prepares and exports; it never sends, publishes, books or pays.

## Repository

| Path | Purpose |
|---|---|
| `landfall/` | API, runner, Brand reader, identity/profile, scan and shortlist stages, providers (fetch, Nova, Qloo), ledger, cassette |
| `landfall/data/` | Packaged reference data: the 80-city catalog, countries and the EU set, a GeoNames gazetteer subset |
| `web/` | Frontend |
| `fixtures/synthetic/` | Fictional Tidewell site and its recorded (synthetic) brand read: the public demo |
| `scripts/` | Provider probes, browser journey, case locking |
| `tests/` | pytest suite |
| `design/` | The supplied Claude Design handoff (visual reference) |
| `docs/` | Product brief, SOP, Build Book |

MIT licensed (original code); see [`LICENSE`](LICENSE) for third-party material. City and country reference data are
derived from [GeoNames](https://www.geonames.org) (CC BY 4.0); the world map uses world-atlas (Natural Earth, public
domain). `scripts/build_reference.py` rebuilds the data from the GeoNames dumps.

## Previous implementation (reference only)

`/Users/markbrazinski/Desktop/coding fun/landfall-agent`. Not a dependency. Consulted only for specific provider
facts (recorded in `docs/BUILD-PLAN.md` §4.1). No code, prompts, schemas, caches or rules were copied.
