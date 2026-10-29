#!/usr/bin/env bash
# AWS CloudShell, run by the owner as an IAM administrator. OPTIONAL (BUILD-PLAN §4.5.13 item 3): it lets Landfall's
# IAM user place page-found leads Qloo misses with Amazon Location Places v2. Running it is the IAM change; using it
# live also needs a spending line added to the active authority (≤ $5), which this script does not grant.
#
#   bash aws_location_grant.sh            # attach the inline policy
#   bash aws_location_grant.sh --revoke   # remove it
set -euo pipefail

USER_NAME="landfall-smoke"
POLICY_NAME="landfall-geo-places"

if [[ "${1:-}" == "--revoke" ]]; then
  aws iam delete-user-policy --user-name "$USER_NAME" --policy-name "$POLICY_NAME"
  echo "Removed $POLICY_NAME from $USER_NAME."
  exit 0
fi

aws iam put-user-policy --user-name "$USER_NAME" --policy-name "$POLICY_NAME" --policy-document '{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["geo-places:SearchText", "geo-places:GetPlace", "geo-places:SearchNearby"],
    "Resource": "arn:aws:geo-places:us-east-1::provider/default"
  }]
}'
aws iam get-user-policy --user-name "$USER_NAME" --policy-name "$POLICY_NAME" --query PolicyDocument
echo "Granted. Tell the builder the spending line you approve for Amazon Location before any live use."
