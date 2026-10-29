#!/usr/bin/env bash
# Run by the owner (BUILD-PLAN §4.5.13 items 2 and 6b), from a Mac terminal or Google Cloud Shell. Not run by agents.
#
#   gcloud auth login
#   bash scripts/cloudshell/gcp_setup.sh [PROJECT_ID]      # default: preflight-hackathon (an existing project)
#
# What it does, idempotently, in the existing project (it never creates a project or touches billing):
#   1. enables the YouTube Data API v3 and the Gmail API
#   2. creates Landfall's own service account, landfall-app, with one role: Service Usage Consumer (use the project's
#      enabled APIs; nothing else). No key file is created: slice 4.5 needs none, and a key file would be a secret.
#   3. creates (or reuses) one API key restricted to the YouTube Data API v3 and prints it once, for .env
#   4. prints the console steps gcloud cannot do for an External app: consent screen in Testing, the test user, the
#      gmail.send scope, and a Desktop OAuth client (its id and secret go in .env)
set -euo pipefail

PROJECT_ID="${1:-preflight-hackathon}"
KEY_NAME="landfall-youtube"
SA_NAME="landfall-app"
SA_EMAIL="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

if ! gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
  echo "Project $PROJECT_ID is not visible to $(gcloud config get-value account 2>/dev/null). Run: gcloud auth login" >&2
  exit 1
fi
gcloud config set project "$PROJECT_ID" >/dev/null

gcloud services enable youtube.googleapis.com gmail.googleapis.com apikeys.googleapis.com iam.googleapis.com

if ! gcloud iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --display-name="Landfall app" \
    --description="Landfall (qloo-landfall): YouTube Data API and Gmail integration identity"
fi
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA_EMAIL" \
  --role="roles/serviceusage.serviceUsageConsumer" --condition=None >/dev/null

key=$(gcloud services api-keys list --filter="displayName=$KEY_NAME" --format="value(name)" | head -n1)
if [[ -z "$key" ]]; then
  gcloud services api-keys create --display-name="$KEY_NAME" --api-target=service=youtube.googleapis.com >/dev/null
  key=$(gcloud services api-keys list --filter="displayName=$KEY_NAME" --format="value(name)" | head -n1)
fi

console="https://console.cloud.google.com"
cat <<EOF

Project: $PROJECT_ID · service account: $SA_EMAIL (Service Usage Consumer only; no key file)

1) YouTube Data API key (restricted to youtube.googleapis.com). Add this line to Landfall's .env, then clear this
   terminal. When Landfall is hosted (slice 8), also restrict the key to the server's IP in the console.

   youtube_api_key=$(gcloud services api-keys get-key-string "$key" --format="value(keyString)")

2) Gmail OAuth in Testing mode (the console only; gcloud cannot create an External consent screen).
   NOTE: a project has ONE consent screen. If preflight-hackathon already has one, Gmail's consent page will show
   that app's name; reuse it (step a can be skipped) or put Landfall in its own project.
   a. Branding: app name "Landfall", your support email      $console/auth/branding?project=$PROJECT_ID
   b. Audience: User type External, Publishing status Testing; add your own Gmail as a test user
                                                              $console/auth/audience?project=$PROJECT_ID
   c. Data access: add the scope .../auth/gmail.send only     $console/auth/scopes?project=$PROJECT_ID
      (gmail.readonly is added only if slice 7 reads replies; it is a Restricted scope)
   d. Clients: Create client, type "Desktop app", name "landfall-local"
                                                              $console/auth/clients?project=$PROJECT_ID
      Add to .env:
        google_oauth_client_id=<client id>
        google_oauth_client_secret=<client secret>
        gmail_test_recipient=<the one designated test recipient address>

   Testing-mode tokens expire after 7 days, so the demo reconnects weekly.

3) Then:  uv run python -m scripts.early_access gmail      (dry run: shows the exact email)
          uv run python -m scripts.early_access gmail --go (one real send, after you approve)
EOF
