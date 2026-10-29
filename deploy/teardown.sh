#!/usr/bin/env bash
# Spin Landfall down (docs/DEPLOYMENT-PLAN.md §4.4). Run in AWS CloudShell as an IAM administrator.
# Takes a final backup, then removes everything provision.sh created. The S3 bucket (backups) is kept unless you
# pass --delete-backups; delete it later with:  aws s3 rb s3://landfall-judging-<account> --force  (versioned: empty it first)
set -uo pipefail  # keep going past anything already gone
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="landfall-judging-$ACCT"
read -r -p "Type TEARDOWN to remove the Landfall deployment in $ACCT: " ok; [[ "$ok" == TEARDOWN ]] || exit 1

IID=$(aws ec2 describe-instances --filters Name=tag:Name,Values=landfall Name=instance-state-name,Values=pending,running,stopped \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)
if [[ "$IID" == i-* ]]; then
  echo "== Final backup"
  cid=$(aws ssm send-command --instance-ids "$IID" --document-name AWS-RunShellScript \
    --parameters 'commands=["systemctl stop landfall; sudo -u landfall -H env AWS_PROFILE=landfall /usr/local/bin/landfall-backup && echo backed-up"]' \
    --query Command.CommandId --output text)
  sleep 20; aws ssm get-command-invocation --command-id "$cid" --instance-id "$IID" --query StandardOutputContent --output text
  read -r -p "Download the final backup now? (aws s3 cp s3://$BUCKET/backups/hourly/ . --recursive) Press Enter to continue."
fi

echo "== CloudFront"
CF=$(aws cloudfront list-distributions --query "DistributionList.Items[?Comment=='Landfall'].Id | [0]" --output text)
if [[ "$CF" != None && -n "$CF" ]]; then
  ETAG=$(aws cloudfront get-distribution-config --id "$CF" --query ETag --output text)
  aws cloudfront get-distribution-config --id "$CF" --query DistributionConfig | jq '.Enabled=false' > /tmp/cf.json
  aws cloudfront update-distribution --id "$CF" --if-match "$ETAG" --distribution-config file:///tmp/cf.json >/dev/null
  echo "disabling (≈ 5-15 min)…"; aws cloudfront wait distribution-deployed --id "$CF"
  aws cloudfront delete-distribution --id "$CF" --if-match "$(aws cloudfront get-distribution-config --id "$CF" --query ETag --output text)"
fi

echo "== EC2, Elastic IP, security group"
if [[ "$IID" == i-* ]]; then
  aws ec2 modify-instance-attribute --instance-id "$IID" --no-disable-api-termination
  aws ec2 terminate-instances --instance-ids "$IID" >/dev/null && aws ec2 wait instance-terminated --instance-ids "$IID"
fi
for a in $(aws ec2 describe-addresses --filters Name=tag:Name,Values=landfall --query 'Addresses[].AllocationId' --output text); do
  aws ec2 release-address --allocation-id "$a"
done
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=landfall-origin --query 'SecurityGroups[0].GroupId' --output text)
[[ "$SG" == sg-* ]] && aws ec2 delete-security-group --group-id "$SG"

echo "== IAM, SSM, budget"
aws iam remove-role-from-instance-profile --instance-profile-name landfall-runtime --role-name landfall-runtime
aws iam delete-instance-profile --instance-profile-name landfall-runtime
for pol in $(aws iam list-role-policies --role-name landfall-runtime --query PolicyNames --output text); do
  aws iam delete-role-policy --role-name landfall-runtime --policy-name "$pol"
done
aws iam delete-role --role-name landfall-runtime
aws iam delete-user-policy --user-name landfall-smoke --policy-name landfall-deploy
for n in $(aws ssm get-parameters-by-path --path /landfall/judging --recursive --query 'Parameters[].Name' --output text); do
  aws ssm delete-parameter --name "$n"
done
aws budgets delete-budget --account-id "$ACCT" --budget-name landfall-hosting

if [[ "${1:-}" == --delete-backups ]]; then
  echo "== S3 (all versions)"
  while :; do
    batch=$(aws s3api list-object-versions --bucket "$BUCKET" --max-items 500 --output json |
      jq '{Objects: ([.Versions[]?, .DeleteMarkers[]?] | map({Key, VersionId}))}')
    [[ $(jq '.Objects | length' <<<"$batch") -gt 0 ]] || break
    aws s3api delete-objects --bucket "$BUCKET" --delete "$batch" >/dev/null
  done
  aws s3api delete-bucket --bucket "$BUCKET"
else
  echo "Backups kept in s3://$BUCKET (≈ \$0.01/month). Re-run with --delete-backups to remove them."
fi
echo "Teardown finished. Check Billing next month for any remaining line items."
