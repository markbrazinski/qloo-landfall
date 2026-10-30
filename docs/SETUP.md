# Landfall setup reference

The [README](../README.md) covers install, the recorded run and live mode. This page lists every setting, command and
API route.

## Settings

Settings come from the environment, then from `.env` in the repository root (environment variables win). Copy
[`.env.example`](../.env.example) to `.env`. A line left as `NAME=` with nothing after it counts as set-to-empty, so
leave settings that have defaults commented out unless you mean to change them.

### Models and providers

| Setting | Default | Effect |
|---|---|---|
| `AWS_PROFILE` | unset | AWS profile for Bedrock. Unset uses boto3's normal credential chain (environment, SSO, instance role). |
| `AWS_REGION` | `us-east-1` | Bedrock region. |
| `LANDFALL_NOVA_MODEL` | `us.amazon.nova-2-lite-v1:0` | Model for reading, research, specialists and plans. |
| `LANDFALL_COPY_MODEL` | `us.amazon.nova-pro-v1:0` | Model that writes and checks ad and message copy. |
| `qloo_server` | none | Qloo Insights API base URL. Required for live mode. |
| `api_key` | none | Qloo API key. Required for live mode. Never logged or recorded. |
| `gemini_api_key` | unset | Google AI Studio key for mockup images. Unset uses Stability on Bedrock. |
| `LANDFALL_IMAGE_MODEL` | `gemini-nano-banana-2.1` | Image model when a Gemini key is set. |
| `LANDFALL_IMAGE_REGION` | `global` for Gemini | Image model region. |
| `influencers_club_api_key` | unset | Creator discovery. Unset shows creator discovery as not connected. |
| `LANDFALL_IC_TERMS_V24_CONFIRMED` | unset | Set to `1` to open creator discovery before 1 Nov 2026. |
| `LANDFALL_IC_ADMISSION` | `open` | Any other value pauses live creator discovery. |
| `LANDFALL_ENABLE_YOUTUBE`, `youtube_api_key` | unset | Both set (`1` and a key) enable YouTube channel lookups for creators. |
| `SHOPIFY_AGENT_PROFILE_URL` | unset | Read products from a Shopify agent profile instead of the brand's pages and feed. |

### Spending caps

Every paid call is admitted against these before it is made. `make usage` shows the totals.

| Setting | Default | Effect |
|---|---|---|
| `LANDFALL_CAP_USD` | `75` | Total Bedrock spend, in US dollars. |
| `LANDFALL_CAP_QLOO` | `2500` | Total Qloo requests. |
| `LANDFALL_QLOO_FLOOR` | `1000` | Live research stops when this many Qloo requests remain. |
| `LANDFALL_CAP_FETCH` | `3000` | Total page fetches. |
| `LANDFALL_CAP_GEMINI_USD` | `15` | Total Gemini image spend, in US dollars. |
| `LANDFALL_AUTHORITY_EXPIRES` | unset | ISO 8601 date and time after which paid calls stop. Unset: no end date. |
| `LANDFALL_LEDGER` | `live-captures-private/usage-ledger.jsonl` | The spending ledger file. |

Creator discovery has its own allowance: 10 requests and 4 credits in total for a local key, 20 requests and 6 credits
for a hosted deployment (`landfall/ledger.py`, `IC_POOLS`).

### Runtime

| Setting | Default | Effect |
|---|---|---|
| `LANDFALL_DB` | `.landfall/landfall.db` | SQLite database path. |
| `LANDFALL_PROVIDER_MODE` | `record` | `record`: live calls, written to `live-captures-private/runs/`. `live`: live calls, not written. `replay`: recorded exchanges only. |
| `LANDFALL_REPLAY_CASES` | unset | Comma-separated recording folders replayed for projects entered by URL. |
| `LANDFALL_LIVE_STAGES` | unset | With replay cases, the stages allowed to go live when the recording has no answer. |
| `LANDFALL_REPLAY_DELAY` | `0` | Seconds to wait per replayed page or model call. |
| `LANDFALL_DEMO_CASE` | Tidewell | Recording used when a replayed project names none. |
| `LANDFALL_AUTO_PLAN` | `1` | Choosing a first city builds its plan automatically. `0` leaves each step to the founder. |
| `LANDFALL_ALLOW_PRIVATE_FETCH`, `LANDFALL_HOST_MAP` | unset | Only for re-recording the Tidewell site from a local server (e.g. `tidewell.example=127.0.0.1:8791`). |

### Delivery

Unset, direct delivery shows as unavailable and every item can still be exported.

| Setting | Effect |
|---|---|
| `LANDFALL_TOKEN_KEY` | Fernet key that encrypts connection tokens at rest. |
| `LANDFALL_PUBLIC_URL` | The app's base URL; OAuth returns to `<url>/api/connections/callback`. |
| `LANDFALL_DELIVERY_ALLOWLIST` | Comma-separated addresses and account ids a live write may reach. Empty refuses every live write. |
| `google_oauth_client_id`, `google_oauth_client_secret` | Google OAuth client for Gmail and Google Ads. |
| `LANDFALL_EMAIL_MODE` | `test_redirect` (default): mail goes only from `gmail_test_sender` to `gmail_test_recipient`, and the intended recipient is shown, not contacted. `direct` sends to the intended recipient. |
| `gmail_test_sender`, `gmail_test_recipient` | The connected Gmail address and the inbox that receives redirected mail. |
| `LANDFALL_GMAIL_ACCESS` | `TESTING`, `PENDING` or `VERIFIED`: the Gmail app's verification status. |
| `LANDFALL_GOOGLE_ADS_ACCESS` | `TEST`, `PENDING`, `EXPLORER`, `BASIC` or `STANDARD`. Unset: Google Ads is not enabled. |
| `google_ads_test_manager_id`, `google_ads_test_customer_id` | With `TEST` access, the test customer and the manager account it is read through. |
| `LANDFALL_META_ACCESS` | `APP_ROLES`, `PENDING`, `ADVANCED` or `SANDBOX`. |
| `meta_app_id`, `meta_app_secret`, `meta_login_config_id`, `meta_ad_account_id`, `meta_graph_version` | Meta app and ad account (`act_<digits>`); Graph version defaults to `v25.0`. |
| `LANDFALL_TIKTOK_ACCESS` | `PENDING`, `APPROVED` or `SANDBOX`. |
| `tiktok_app_id`, `tiktok_app_secret`, `tiktok_sandbox_token`, `tiktok_sandbox_advertiser_id`, `tiktok_sandbox_location_id` | TikTok app and sandbox advertiser. |

### Hosting

| Setting | Effect |
|---|---|
| `LANDFALL_REQUIRE_GRANT` | `on`: only browsers holding an access link research live; everyone else gets the recorded runs. Unset: everything is live. |
| `LANDFALL_ENV` | `judging` marks a hosted deployment: it uses the hosted creator allowance and refuses `LANDFALL_EMAIL_MODE=direct`. |
| `LANDFALL_SHARED_DELIVERY` | `on`: projects without their own connection may use the operator's shared Gmail (redirected) and Google Ads test connections. |
| `LANDFALL_SHARED_EMAIL_TOTAL`, `LANDFALL_SHARED_EMAIL_PER_PROJECT`, `LANDFALL_SHARED_GOOGLE_ADS_TOTAL`, `LANDFALL_SHARED_GOOGLE_ADS_PER_PROJECT` | Shared-connection allowances; defaults 20 in total and 2 per project for each. |

## Commands

| Command | What it does |
|---|---|
| `make setup` | `uv sync`, `npm install` in `web/`, and Playwright's Chromium. |
| `make web` | Build the frontend into `web/dist/` (the API serves it). |
| `make replay` | Recorded runs on port 8003: replay mode, a separate database, the brand picker on. |
| `make dev` | Live app on port 8000, recording every provider exchange. |
| `make test` | Build the frontend, then run the pytest suite (replay only, no live calls). |
| `make usage` | Spend and request counts so far against each cap. |
| `uv run python -m scripts.record_demo walk <base> <brand> <out> [--demo]` | Drive one full run in a browser against a running server, from the brand URL to the Launch page. Without `--demo` it researches live and spends; `--demo` starts from the brand picker and walks a recorded run (server started with `LANDFALL_REQUIRE_GRANT=on`). Never presses Send or Create. |
| `uv run python -m scripts.build_reference` | Rebuild the city, country and gazetteer data from the GeoNames dumps. |
| `deploy/provision.sh`, `deploy/release.sh <commit>`, `deploy/teardown.sh` | Create, ship to and remove the AWS deployment (see the README). |

## API routes

All routes are JSON and scoped to the browser session that created the project (cookie `lf_session`). Live progress
streams from `GET /api/projects/{pid}/events` as Server-Sent Events. Any other path serves the web app.

**Session and access:** `GET /api/session` · `GET|POST /access`

**Projects and brand:** `POST /api/projects` · `GET /api/projects/{pid}` · `GET|PATCH /api/projects/{pid}/brief` ·
`GET|POST /api/projects/{pid}/products` · `GET /api/projects/{pid}/products/{prid}/snapshot` ·
`GET /api/projects/{pid}/activity` · `POST /api/projects/{pid}/brand-read/retry` ·
`POST /api/projects/{pid}/brand-read/description` · `GET /api/sources/{sid}` · `POST /api/runs/{run_id}/cancel`

**Qloo identity and markets:** `POST /api/projects/{pid}/identity` · `POST /api/projects/{pid}/identity/retry` ·
`PUT /api/projects/{pid}/profile` · `POST /api/projects/{pid}/profile/retry` · `GET /api/projects/{pid}/qloo/tags` ·
`GET /api/projects/{pid}/qloo/brands` · `GET /api/projects/{pid}/qloo/places/{qloo_id}` ·
`PATCH /api/projects/{pid}/intent` · `GET|PUT /api/projects/{pid}/presence` · `GET|PUT /api/projects/{pid}/standins` ·
`GET|PUT /api/projects/{pid}/audience` · `POST /api/projects/{pid}/audience/retry` ·
`POST /api/projects/{pid}/ranking/retry` · `POST /api/projects/{pid}/scan` · `POST /api/projects/{pid}/scan/retry` ·
`GET /api/projects/{pid}/markets` · `PATCH /api/projects/{pid}/shortlist` ·
`POST /api/projects/{pid}/shortlist/retry` · `POST /api/projects/{pid}/shortlist/update`

**City research and leads:** `POST /api/projects/{pid}/research` · `POST /api/projects/{pid}/research/continue` ·
`POST /api/projects/{pid}/research/retry` · `PATCH /api/projects/{pid}/workspaces/{wid}` ·
`GET /api/projects/{pid}/cities/{cid}/methods/{method}` ·
`POST /api/projects/{pid}/cities/{cid}/methods/{method}/investigate` (`?dry_run=1` returns the cost first) ·
`POST /api/projects/{pid}/cities/{cid}/methods/{method}/continue` · `POST /api/projects/{pid}/leads` ·
`GET /api/projects/{pid}/leads/{lid}` · `PUT /api/projects/{pid}/leads/{lid}/location` ·
`POST /api/projects/{pid}/leads/{lid}/contact` · `POST /api/projects/{pid}/leads/{lid}/check` ·
`POST /api/projects/{pid}/leads/{lid}/enrich` · `GET /api/projects/{pid}/opportunities` ·
`POST /api/projects/{pid}/approaches/{aid}/rule-out` · `POST /api/projects/{pid}/approaches/{aid}/undo` ·
`GET /api/projects/{pid}/dossiers/{did}` · `POST /api/projects/{pid}/dossiers/{did}/develop`

**Plans:** `GET|PUT /api/projects/{pid}/plans/{cid}` · `POST /api/projects/{pid}/plans/{cid}/auto` ·
`POST /api/projects/{pid}/plans/{cid}/propose` · `GET /api/projects/{pid}/plans/{cid}/proposals/{prop}/options/{opt}/preview` ·
`POST /api/projects/{pid}/plans/{cid}/proposals/{prop}/adopt` · `PATCH /api/projects/{pid}/plans/{cid}/cost-lines/{lid}` ·
`PATCH /api/projects/{pid}/plans/{cid}/return` · `POST /api/projects/{pid}/plans/{cid}/fx/retry` ·
`GET|PUT /api/projects/{pid}/plans/{cid}/scenario` · `POST /api/projects/{pid}/plans/{cid}/sign-off`

**Launch kit:** `GET /api/projects/{pid}/plans/{cid}/launch` · `GET /api/projects/{pid}/plans/{cid}/act` ·
`POST /api/projects/{pid}/plans/{cid}/launch/prewarm` · `PUT /api/projects/{pid}/plans/{cid}/launch/settings` ·
`POST /api/projects/{pid}/plans/{cid}/launch/pick` · `POST /api/projects/{pid}/plans/{cid}/rights` ·
`GET|POST /api/projects/{pid}/plans/{cid}/mockups` · `GET /api/projects/{pid}/mockups/{rid}/image` ·
`GET /api/projects/{pid}/artifacts/{sha}` · `GET /api/projects/{pid}/plans/{cid}/creators` ·
`POST /api/projects/{pid}/plans/{cid}/creators/search` ·
`POST /api/projects/{pid}/creators/searches/{sid}/accounts/{i}/choose` · `GET|PATCH /api/projects/{pid}/drafts/{rid}` ·
`POST /api/projects/{pid}/drafts/{rid}/package` · `GET|POST|PUT /api/projects/{pid}/templates[/{tid}]`

**Packages and delivery:** `GET|PATCH /api/projects/{pid}/packages/{pkg_id}` ·
`POST /api/projects/{pid}/packages/{pkg_id}/assets` · `GET /api/projects/{pid}/packages/{pkg_id}/exports/{fmt}` ·
`POST /api/projects/{pid}/packages/{pkg_id}/wording` · `POST /api/projects/{pid}/packages/{pkg_id}/apply-template` ·
`POST /api/projects/{pid}/packages/{pkg_id}/set-aside` ·
`POST /api/projects/{pid}/packages/{pkg_id}/destinations/{dst}/approve|validate|execute|manual|manual/withdraw` ·
`POST /api/projects/{pid}/executions/{aid}/resolve` · `POST /api/projects/{pid}/connections/{dst}/start` ·
`GET /api/connections/callback` · `PUT /api/projects/{pid}/connections/{dst}/account` ·
`DELETE /api/projects/{pid}/connections/{dst}`
