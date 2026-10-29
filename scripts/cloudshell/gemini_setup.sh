#!/usr/bin/env bash
# Run by the owner (BUILD-PLAN 6.5 UI.13, owner 27 Oct 2026), from this repo on your Mac or from Google Cloud Shell.
# Not run by agents: it changes your Google Cloud project and billing.
#
#   gcloud auth login
#   bash scripts/cloudshell/gemini_setup.sh [PROJECT_ID] [ENV_FILE]
#     PROJECT_ID  default: preflight-hackathon (an existing project; it never creates one)
#     ENV_FILE    default: ./.env when run from the repo. The key is written there and never printed.
#                 In Cloud Shell (no repo), pass "-" to print it once instead.
#
# What it does, idempotently:
#   1. checks the project has a billing account (Gemini image output has no free tier) and stops if not
#   2. enables the Gemini API (generativelanguage), API Keys and Billing Budgets APIs
#   3. reuses Landfall's service account landfall-app (Service Usage Consumer only, no key file) as the identity the
#      key is bound to, so usage shows under Landfall, not you
#   4. creates (or reuses) one API key restricted to the Gemini API only, bound to that service account
#   5. creates (or reuses) a US$15 budget on this project: email alerts at 50%, 90%, 100% and forecast 100%.
#      A Google budget ALERTS; it does not stop spending. Landfall's own ledger also stops Gemini at US$15.
set -euo pipefail

PROJECT_ID="${1:-preflight-hackathon}"
ENV_FILE="${2:-}"
if [[ -z "$ENV_FILE" && -f .env.example ]]; then ENV_FILE=".env"; fi
KEY_NAME="landfall-gemini"
SA_NAME="landfall-app"
SA_EMAIL="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
BUDGET_NAME="landfall-gemini-15usd"

if ! gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
  echo "Project $PROJECT_ID is not visible to $(gcloud config get-value account 2>/dev/null). Run: gcloud auth login" >&2
  exit 1
fi
gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")

# 1. billing must be on: image output is paid-tier only (ai.google.dev/gemini-api/docs/pricing)
BILLING=$(gcloud billing projects describe "$PROJECT_ID" --format="value(billingAccountName)" 2>/dev/null || true)
if [[ -z "$BILLING" ]]; then
  echo "No billing account is linked to $PROJECT_ID. Link one, then run this again:" >&2
  echo "  https://console.cloud.google.com/billing/linkedaccount?project=$PROJECT_ID" >&2
  exit 1
fi
BILLING_ID="${BILLING#billingAccounts/}"

# 2. APIs
gcloud services enable generativelanguage.googleapis.com apikeys.googleapis.com billingbudgets.googleapis.com \
  iam.googleapis.com cloudbilling.googleapis.com

# 3. the identity the key belongs to (created by gcp_setup.sh; created here if missing, with the same single role)
if ! gcloud iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --display-name="Landfall app" \
    --description="Landfall (qloo-landfall): API identity; no key file"
fi
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA_EMAIL" \
  --role="roles/serviceusage.serviceUsageConsumer" --condition=None >/dev/null

# 4. one API key, usable only for the Gemini API
key=$(gcloud services api-keys list --filter="displayName=$KEY_NAME" --format="value(name)" | head -n1)
if [[ -z "$key" ]]; then
  if ! gcloud services api-keys create --display-name="$KEY_NAME" --api-target=service=generativelanguage.googleapis.com \
       --service-account="$SA_EMAIL" >/dev/null 2>&1; then
    # older gcloud, or binding keys to service accounts not enabled in this org: same restriction, no binding
    gcloud services api-keys create --display-name="$KEY_NAME" --api-target=service=generativelanguage.googleapis.com >/dev/null
  fi
  key=$(gcloud services api-keys list --filter="displayName=$KEY_NAME" --format="value(name)" | head -n1)
fi
KEY_STRING=$(gcloud services api-keys get-key-string "$key" --format="value(keyString)")

# 5. the US$15 budget on this project (alerts go to the billing account's admins and users)
if ! gcloud billing budgets list --billing-account="$BILLING_ID" --format="value(displayName)" | grep -qx "$BUDGET_NAME"; then
  gcloud billing budgets create --billing-account="$BILLING_ID" --display-name="$BUDGET_NAME" \
    --budget-amount=15USD --filter-projects="projects/$PROJECT_NUMBER" \
    --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0 \
    --threshold-rule=percent=1.0,basis=forecasted-spend >/dev/null
fi

if [[ -n "$ENV_FILE" && "$ENV_FILE" != "-" ]]; then
  touch "$ENV_FILE"
  grep -v '^gemini_api_key=' "$ENV_FILE" > "$ENV_FILE.tmp" || true
  echo "gemini_api_key=$KEY_STRING" >> "$ENV_FILE.tmp"
  mv "$ENV_FILE.tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  WHERE="written to $ENV_FILE as gemini_api_key (not printed)"
else
  WHERE="add this line to Landfall's .env, then clear this terminal:

   gemini_api_key=$KEY_STRING"
fi

cat <<EOF

Done · project $PROJECT_ID (number $PROJECT_NUMBER) · billing account $BILLING_ID
- Gemini API enabled; key "$KEY_NAME" restricted to generativelanguage.googleapis.com, owned by $SA_EMAIL
- Budget "$BUDGET_NAME": US\$15 on this project, email alerts at 50/90/100% and forecast 100% (alerts only)
- Key: $WHERE

Tell the builder "gemini key is in". Restart any running Landfall server so it reads the new .env.
EOF
