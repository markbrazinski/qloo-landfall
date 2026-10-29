#!/usr/bin/env bash
# Landfall hosting, step 1 of 2. Run ONCE in AWS CloudShell (us-east-1) as an IAM administrator.
# Creates exactly the resources in docs/DEPLOYMENT-PLAN.md §9: EC2 t4g.small, Elastic IP, security group, S3 bucket,
# SSM parameters, IAM role landfall-runtime (+ the landfall-deploy policy on landfall-smoke), a CloudFront address,
# and a $30 hosting budget alert. It asks for the Qloo values (never echoed) and prints the staging link at the end.
# Undo everything with teardown.sh. Step 2 (shipping code) is deploy/release.sh, run by the builder.
set -euo pipefail
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
NAME=landfall
ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="landfall-judging-$ACCT"
SMOKE_USER=landfall-smoke
UV_VERSION=0.11.7

cat <<EOF
This creates, in account $ACCT, region us-east-1:
  - S3 bucket $BUCKET (private, versioned, encrypted; backups + releases)
  - SSM SecureString parameters under /landfall/judging/
  - IAM role + instance profile landfall-runtime; inline policy landfall-deploy on user $SMOKE_USER
  - security group landfall-origin (port 80 from CloudFront only)
  - EC2 t4g.small (Ubuntu 24.04 arm64, 20 GB gp3, credit mode standard, IMDSv2 hop 1) + one Elastic IP
  - a CloudFront distribution (the public https://<id>.cloudfront.net address)
  - an AWS Budget "landfall-hosting" (\$30, email alerts at \$15/\$25/\$30; alerts only, it stops nothing)
Expected cost ≈ \$0.58/day. Nothing is public until you open the staging link printed at the end.
EOF
read -r -p "Type PROVISION to continue: " ok; [[ "$ok" == PROVISION ]] || exit 1
read -r -p "Qloo server URL (the 'qloo_server' value in .env): " QLOO_SERVER
read -r -s -p "Qloo API key (the 'api_key' value; not shown): " QLOO_KEY; echo
read -r -p "Email for budget alerts: " ALERT_EMAIL
[[ -n "$QLOO_SERVER" && -n "$QLOO_KEY" && -n "$ALERT_EMAIL" ]] || { echo "All three values are needed."; exit 1; }
ORIGIN_SECRET=$(openssl rand -hex 24)
STAGING_KEY=$(openssl rand -hex 16)

echo "== S3"
aws s3api create-bucket --bucket "$BUCKET" >/dev/null
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration '{"Rules":[
  {"ID":"hourly","Filter":{"Prefix":"backups/hourly/"},"Status":"Enabled","NoncurrentVersionExpiration":{"NoncurrentDays":3}},
  {"ID":"daily","Filter":{"Prefix":"backups/daily/"},"Status":"Enabled","Expiration":{"Days":30},"NoncurrentVersionExpiration":{"NoncurrentDays":1}},
  {"ID":"releases","Filter":{"Prefix":"releases/"},"Status":"Enabled","Expiration":{"Days":60},"NoncurrentVersionExpiration":{"NoncurrentDays":1}}]}'

echo "== SSM parameters"
put() { aws ssm put-parameter --name "$1" --type SecureString --value "$2" --overwrite >/dev/null; }
put /landfall/judging/app/qloo_server "$QLOO_SERVER"
put /landfall/judging/app/api_key "$QLOO_KEY"
put /landfall/judging/edge/origin_secret "$ORIGIN_SECRET"
put /landfall/judging/edge/staging_key "$STAGING_KEY"
unset QLOO_KEY

echo "== IAM role landfall-runtime"
aws iam create-role --role-name landfall-runtime --assume-role-policy-document \
  '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
aws iam put-role-policy --role-name landfall-runtime --policy-name landfall-runtime --policy-document "{
 \"Version\":\"2012-10-17\",\"Statement\":[
  {\"Effect\":\"Allow\",\"Action\":\"bedrock:InvokeModel\",\"Resource\":[
    \"arn:aws:bedrock:us-east-1:$ACCT:inference-profile/us.amazon.nova-2-lite-v1:0\",
    \"arn:aws:bedrock:*::foundation-model/amazon.nova-2-lite-v1:0\"]},
  {\"Effect\":\"Allow\",\"Action\":\"bedrock:InvokeTool\",\"Resource\":\"arn:aws:bedrock::$ACCT:system-tool/amazon.nova_grounding\"},
  {\"Effect\":\"Allow\",\"Action\":[\"ssm:GetParameters\",\"ssm:GetParametersByPath\"],\"Resource\":[
    \"arn:aws:ssm:us-east-1:$ACCT:parameter/landfall/judging/*\",\"arn:aws:ssm:us-east-1:$ACCT:parameter/landfall/judging\"]},
  {\"Effect\":\"Allow\",\"Action\":\"s3:PutObject\",\"Resource\":\"arn:aws:s3:::$BUCKET/backups/*\"},
  {\"Effect\":\"Allow\",\"Action\":\"s3:GetObject\",\"Resource\":[\"arn:aws:s3:::$BUCKET/releases/*\",\"arn:aws:s3:::$BUCKET/backups/*\"]},
  {\"Effect\":\"Allow\",\"Action\":\"s3:ListBucket\",\"Resource\":\"arn:aws:s3:::$BUCKET\"},
  {\"Effect\":\"Allow\",\"Action\":[\"ssm:UpdateInstanceInformation\",\"ssmmessages:CreateControlChannel\",
    \"ssmmessages:CreateDataChannel\",\"ssmmessages:OpenControlChannel\",\"ssmmessages:OpenDataChannel\",
    \"ec2messages:AcknowledgeMessage\",\"ec2messages:DeleteMessage\",\"ec2messages:FailMessage\",
    \"ec2messages:GetEndpoint\",\"ec2messages:GetMessages\",\"ec2messages:SendReply\"],\"Resource\":\"*\"}]}"
aws iam create-instance-profile --instance-profile-name landfall-runtime >/dev/null
aws iam add-role-to-instance-profile --instance-profile-name landfall-runtime --role-name landfall-runtime

echo "== Security group (port 80 from CloudFront only)"
VPC=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
[[ "$VPC" != None ]] || { echo "No default VPC in us-east-1; create one (aws ec2 create-default-vpc) and re-run teardown.sh then this."; exit 1; }
SG=$(aws ec2 create-security-group --group-name landfall-origin --description "Landfall: HTTP from CloudFront only" \
  --vpc-id "$VPC" --query GroupId --output text)
PL=$(aws ec2 describe-managed-prefix-lists --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \
  --query 'PrefixLists[0].PrefixListId' --output text)
aws ec2 authorize-security-group-ingress --group-id "$SG" \
  --ip-permissions "IpProtocol=tcp,FromPort=80,ToPort=80,PrefixListIds=[{PrefixListId=$PL,Description=CloudFront}]" >/dev/null

echo "== EC2"
AMI=$(aws ssm get-parameter --name /aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id \
  --query Parameter.Value --output text)
USERDATA=$(mktemp)
# ---------------------------------------------------------------- first-boot setup (runs as root on the instance)
cat >"$USERDATA" <<BOOT
#!/bin/bash
set -euo pipefail  # no -x: secrets are fetched below and must not reach the console log
exec > >(tee /var/log/landfall-bootstrap.log) 2>&1
export DEBIAN_FRONTEND=noninteractive AWS_DEFAULT_REGION=us-east-1
apt-get update
apt-get install -y curl unzip sqlite3 jq gpg debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
apt-get update && apt-get install -y caddy
curl -sSf https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip -o /tmp/awscli.zip
unzip -q /tmp/awscli.zip -d /tmp && /tmp/aws/install
curl -LsSf https://astral.sh/uv/$UV_VERSION/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh

fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=200M\nMaxRetentionSec=14day\n' > /etc/systemd/journald.conf.d/landfall.conf
systemctl restart systemd-journald

useradd --system --home-dir /srv/landfall --create-home --shell /bin/bash landfall
mkdir -p /srv/landfall/{data,releases,backup,www,.aws}
echo "$BUCKET" > /srv/landfall/bucket

# The app asks boto3 for the profile "landfall" (nova.py); on the server that profile reads the instance role.
cat > /usr/local/bin/imds-credentials <<'PY'
#!/usr/bin/env python3
"""credential_process for the 'landfall' AWS profile: the instance role's temporary credentials via IMDSv2."""
import json, urllib.request
B = "http://169.254.169.254/latest"
def get(path, method="GET", headers=None):
    req = urllib.request.Request(B + path, method=method, headers=headers or {})
    return urllib.request.urlopen(req, timeout=2).read().decode()
token = get("/api/token", "PUT", {"X-aws-ec2-metadata-token-ttl-seconds": "300"})
h = {"X-aws-ec2-metadata-token": token}
role = get("/meta-data/iam/security-credentials/", headers=h).split()[0]
c = json.loads(get("/meta-data/iam/security-credentials/" + role, headers=h))
print(json.dumps({"Version": 1, "AccessKeyId": c["AccessKeyId"], "SecretAccessKey": c["SecretAccessKey"],
                  "SessionToken": c["Token"], "Expiration": c["Expiration"]}))
PY
chmod 755 /usr/local/bin/imds-credentials
printf '[profile landfall]\ncredential_process = /usr/local/bin/imds-credentials\nregion = us-east-1\n' > /srv/landfall/.aws/config

cat > /srv/landfall/landfall.env <<'ENV'
AWS_PROFILE=landfall
AWS_REGION=us-east-1
LANDFALL_ENV=judging
LANDFALL_PROVIDER_MODE=live
LANDFALL_DB=/srv/landfall/data/landfall.db
LANDFALL_LEDGER=/srv/landfall/data/ledger.jsonl
LANDFALL_CAP_USD=45
LANDFALL_CAP_QLOO=31000
LANDFALL_CAP_FETCH=6000
LANDFALL_QLOO_FLOOR=1000
LANDFALL_AUTHORITY_EXPIRES=2099-12-31T23:59:00-08:00
LANDFALL_ENABLE_YOUTUBE=1
ENV

# Secrets are read from SSM into the process environment at each start; nothing secret is written to disk.
cat > /usr/local/bin/landfall-start <<'SH'
#!/bin/bash
set -euo pipefail
while read -r name value; do export "\${name##*/}=\$value"; done < <(aws ssm get-parameters-by-path \
  --path /landfall/judging/app --with-decryption --query 'Parameters[].[Name,Value]' --output text)
exec /srv/landfall/current/.venv/bin/uvicorn landfall.app:app --host 127.0.0.1 --port 8000 --no-access-log
SH
chmod 755 /usr/local/bin/landfall-start

# Hourly backup: one consistent SQLite snapshot plus the ledger copied under the app's own lock.
cat > /usr/local/bin/landfall-backup <<'SH'
#!/bin/bash
set -euo pipefail
B=\$(cat /srv/landfall/bucket); D=/srv/landfall/data; W=/srv/landfall/backup
rm -f "\$W"/*
if [ -f "\$D/landfall.db" ]; then sqlite3 "\$D/landfall.db" "VACUUM INTO '\$W/landfall.db'"; fi
if [ -f "\$D/ledger.jsonl" ]; then flock "\$D/ledger.lock" cp "\$D/ledger.jsonl" "\$W/ledger.jsonl"; fi
shopt -s nullglob; files=("\$W"/*); [ \${#files[@]} -gt 0 ] || exit 0
gzip -f "\${files[@]}"
for f in "\$W"/*.gz; do
  aws s3 cp --only-show-errors "\$f" "s3://\$B/backups/hourly/\$(basename "\$f")"
  if [ "\$(date -u +%H)" = 03 ]; then
    aws s3 cp --only-show-errors "\$f" "s3://\$B/backups/daily/\$(date -u +%F)/\$(basename "\$f")"
  fi
done
SH
chmod 755 /usr/local/bin/landfall-backup

cat > /etc/systemd/system/landfall.service <<'UNIT'
[Unit]
Description=Landfall (one process: API, runner, SSE)
After=network-online.target
Wants=network-online.target
[Service]
User=landfall
WorkingDirectory=/srv/landfall/current
Environment=HOME=/srv/landfall
EnvironmentFile=/srv/landfall/landfall.env
ExecStart=/usr/local/bin/landfall-start
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/landfall-backup.service <<'UNIT'
[Unit]
Description=Landfall hourly backup to S3
[Service]
Type=oneshot
User=landfall
Environment=HOME=/srv/landfall
Environment=AWS_PROFILE=landfall
ExecStart=/usr/local/bin/landfall-backup
UNIT
cat > /etc/systemd/system/landfall-backup.timer <<'UNIT'
[Unit]
Description=Landfall hourly backup
[Timer]
OnCalendar=hourly
Persistent=true
[Install]
WantedBy=timers.target
UNIT

cat > /srv/landfall/www/starting.html <<'HTML'
<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="3"><title>Landfall is starting</title>
<body style="font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:20vh auto;padding:0 16px;color:#1d2a33">
<h1 style="font-size:1.4rem">Landfall is starting up</h1>
<p>This takes a few seconds. Your work is saved, and this page will reload by itself.</p></body>
HTML
chown -R landfall:landfall /srv/landfall

# Edge secrets for Caddy come from SSM; root-only file read by systemd.
ORIGIN_SECRET=\$(aws ssm get-parameters --names /landfall/judging/edge/origin_secret --with-decryption --query 'Parameters[0].Value' --output text)
STAGING_KEY=\$(aws ssm get-parameters --names /landfall/judging/edge/staging_key --with-decryption --query 'Parameters[0].Value' --output text)
printf 'ORIGIN_SECRET=%s\nSTAGING_KEY=%s\n' "\$ORIGIN_SECRET" "\$STAGING_KEY" > /etc/caddy/landfall.env
chmod 600 /etc/caddy/landfall.env
mkdir -p /etc/systemd/system/caddy.service.d
printf '[Service]\nEnvironmentFile=/etc/caddy/landfall.env\n' > /etc/systemd/system/caddy.service.d/landfall.conf
cat > /etc/caddy/Caddyfile <<'CADDY'
$(cat "$(dirname "$0")/Caddyfile" 2>/dev/null || true)
CADDY
systemctl daemon-reload
systemctl enable landfall.service landfall-backup.timer
systemctl start landfall-backup.timer
systemctl restart caddy
echo "bootstrap done"
BOOT
# ----------------------------------------------------------------
grep -q 'X-Origin-Verify' "$USERDATA" || { echo "deploy/Caddyfile must sit next to this script (upload both)."; exit 1; }
IID=""
for _ in 1 2 3 4; do  # a new instance profile can take a few seconds to become usable
  IID=$(aws ec2 run-instances --image-id "$AMI" --instance-type t4g.small --credit-specification CpuCredits=standard \
    --iam-instance-profile Name=landfall-runtime --security-group-ids "$SG" \
    --metadata-options HttpTokens=required,HttpPutResponseHopLimit=1,HttpEndpoint=enabled,HttpProtocolIpv6=disabled \
    --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=20,VolumeType=gp3,Encrypted=true,DeleteOnTermination=true}' \
    --disable-api-termination --user-data "file://$USERDATA" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" "ResourceType=volume,Tags=[{Key=Name,Value=$NAME}]" \
    --query 'Instances[0].InstanceId' --output text) && break
  echo "instance profile not ready yet; retrying in 10 s"; sleep 10
done
[[ -n "$IID" ]] || { echo "Could not launch the instance."; exit 1; }
rm -f "$USERDATA"
aws ec2 wait instance-running --instance-ids "$IID"
ALLOC=$(aws ec2 allocate-address --domain vpc --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$NAME}]" \
  --query AllocationId --output text)
aws ec2 associate-address --instance-id "$IID" --allocation-id "$ALLOC" >/dev/null
IP=$(aws ec2 describe-addresses --allocation-ids "$ALLOC" --query 'Addresses[0].PublicIp' --output text)
ORIGIN="ec2-${IP//./-}.compute-1.amazonaws.com"

echo "== Builder deploy permission on $SMOKE_USER"
aws iam put-user-policy --user-name "$SMOKE_USER" --policy-name landfall-deploy --policy-document "{
 \"Version\":\"2012-10-17\",\"Statement\":[
  {\"Effect\":\"Allow\",\"Action\":\"s3:PutObject\",\"Resource\":\"arn:aws:s3:::$BUCKET/releases/*\"},
  {\"Effect\":\"Allow\",\"Action\":\"s3:GetObject\",\"Resource\":\"arn:aws:s3:::$BUCKET/backups/*\"},
  {\"Effect\":\"Allow\",\"Action\":\"ssm:SendCommand\",\"Resource\":[
    \"arn:aws:ec2:us-east-1:$ACCT:instance/$IID\",\"arn:aws:ssm:us-east-1::document/AWS-RunShellScript\"]},
  {\"Effect\":\"Allow\",\"Action\":[\"ssm:GetCommandInvocation\",\"ssm:ListCommandInvocations\",\"ec2:DescribeInstances\"],\"Resource\":\"*\"}]}"

echo "== CloudFront"
policy() { aws cloudfront "list-$1-policies" --type managed --output text \
  --query "$2PolicyList.Items[?$2Policy.$2PolicyConfig.Name=='$3'].$2Policy.Id"; }
CACHE=$(policy cache Cache Managed-CachingDisabled)
ORQ=$(policy origin-request OriginRequest Managed-AllViewerExceptHostHeader)
CF=$(aws cloudfront create-distribution --query 'Distribution.[Id,DomainName]' --output text --distribution-config "{
 \"CallerReference\":\"landfall-$(date +%s)\",\"Comment\":\"Landfall\",\"Enabled\":true,\"PriceClass\":\"PriceClass_100\",
 \"HttpVersion\":\"http2and3\",\"IsIPV6Enabled\":true,
 \"Origins\":{\"Quantity\":1,\"Items\":[{\"Id\":\"landfall-ec2\",\"DomainName\":\"$ORIGIN\",
   \"CustomHeaders\":{\"Quantity\":1,\"Items\":[{\"HeaderName\":\"X-Origin-Verify\",\"HeaderValue\":\"$ORIGIN_SECRET\"}]},
   \"CustomOriginConfig\":{\"HTTPPort\":80,\"HTTPSPort\":443,\"OriginProtocolPolicy\":\"http-only\",\"OriginReadTimeout\":60,
     \"OriginKeepaliveTimeout\":5,\"OriginSslProtocols\":{\"Quantity\":1,\"Items\":[\"TLSv1.2\"]}}}]},
 \"DefaultCacheBehavior\":{\"TargetOriginId\":\"landfall-ec2\",\"ViewerProtocolPolicy\":\"redirect-to-https\",\"Compress\":false,
   \"AllowedMethods\":{\"Quantity\":7,\"Items\":[\"GET\",\"HEAD\",\"OPTIONS\",\"PUT\",\"PATCH\",\"POST\",\"DELETE\"],
     \"CachedMethods\":{\"Quantity\":2,\"Items\":[\"GET\",\"HEAD\"]}},
   \"CachePolicyId\":\"$CACHE\",\"OriginRequestPolicyId\":\"$ORQ\"}}")
read -r CF_ID CF_DOMAIN <<<"$CF"

aws ssm put-parameter --name /landfall/judging/outputs --type String --overwrite --value \
  "instance=$IID bucket=$BUCKET eip=$ALLOC sg=$SG cloudfront=$CF_ID url=https://$CF_DOMAIN" >/dev/null
echo "== Budget"
aws budgets create-budget --account-id "$ACCT" --budget "{
 \"BudgetName\":\"landfall-hosting\",\"BudgetType\":\"COST\",\"TimeUnit\":\"ANNUALLY\",
 \"BudgetLimit\":{\"Amount\":\"30\",\"Unit\":\"USD\"},
 \"TimePeriod\":{\"Start\":\"$(date -u +%Y-%m-01)T00:00:00Z\"},
 \"CostFilters\":{\"Service\":[\"Amazon Elastic Compute Cloud - Compute\",\"EC2 - Other\",\"Amazon Simple Storage Service\",
   \"Amazon CloudFront\",\"Amazon Virtual Private Cloud\"]}}" --notifications-with-subscribers "[
 {\"Notification\":{\"NotificationType\":\"ACTUAL\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":50,\"ThresholdType\":\"PERCENTAGE\"},\"Subscribers\":[{\"SubscriptionType\":\"EMAIL\",\"Address\":\"$ALERT_EMAIL\"}]},
 {\"Notification\":{\"NotificationType\":\"ACTUAL\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":83,\"ThresholdType\":\"PERCENTAGE\"},\"Subscribers\":[{\"SubscriptionType\":\"EMAIL\",\"Address\":\"$ALERT_EMAIL\"}]},
 {\"Notification\":{\"NotificationType\":\"ACTUAL\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":100,\"ThresholdType\":\"PERCENTAGE\"},\"Subscribers\":[{\"SubscriptionType\":\"EMAIL\",\"Address\":\"$ALERT_EMAIL\"}]}]" \
  || echo "WARNING: budget alert not created; create a \$30 cost budget in the Billing console."

cat <<EOF

Done. CloudFront takes ~5-10 minutes to go live; the server takes ~5 minutes to set itself up.
  Address:       https://$CF_DOMAIN
  Staging link:  https://$CF_DOMAIN/?staging_key=$STAGING_KEY      (only you; it sets a cookie and opens the site)
  Instance:      $IID   Bucket: $BUCKET
Until the builder ships the first release, the address shows "Landfall is starting up".
Tell the builder "provisioned" (the address is not secret; the staging link is).
EOF
