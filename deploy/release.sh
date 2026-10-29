#!/usr/bin/env bash
# Ship one commit to the Landfall server (docs/DEPLOYMENT-PLAN.md §8). Runs on the builder's machine as landfall-smoke,
# using the landfall-deploy policy that provision.sh grants. Only committed code ships; deploy/Caddyfile ships from here.
#
#   deploy/release.sh <commit>                 build web/dist from that commit, upload, install, restart, health-check
#   deploy/release.sh --rollback               switch back to the previous release
#   deploy/release.sh --logs                   last 60 lines of the service log
#   deploy/release.sh --build-only <commit> <out.tgz>   build the release archive locally (no AWS)
set -euo pipefail
export AWS_PROFILE=${AWS_PROFILE:-landfall} AWS_REGION=us-east-1 AWS_PAGER=""
ROOT=$(cd "$(dirname "$0")/.." && pwd)

build() {  # <commit> <out.tgz>
  local sha work
  sha=$(git -C "$ROOT" rev-parse --verify "$1^{commit}")
  work=$(mktemp -d)
  git -C "$ROOT" archive "$sha" | tar -x -C "$work"
  (cd "$work/web" && npm ci --no-audit --no-fund --loglevel=error && npm run build >/dev/null)
  rm -rf "$work/web/node_modules"
  echo "$sha" > "$work/RELEASE"
  tar -czf "$2" -C "$work" .
  rm -rf "$work"
  echo "built $sha → $2 ($(du -h "$2" | cut -f1))"
}

remote() {  # <script> : run as root with bash on the instance through SSM Run Command (which uses sh), print its output
  local iid cid status
  iid=$(aws ec2 describe-instances --filters Name=tag:Name,Values=landfall Name=instance-state-name,Values=running \
    --query 'Reservations[0].Instances[0].InstanceId' --output text)
  [[ "$iid" == i-* ]] || { echo "No running instance tagged landfall."; exit 1; }
  cid=$(aws ssm send-command --instance-ids "$iid" --document-name AWS-RunShellScript --timeout-seconds 60 \
    --parameters "$(jq -n --arg s "$1" '{commands: ["bash <<'"'"'LANDFALL_EOS'"'"'\n" + $s + "\nLANDFALL_EOS"], executionTimeout: ["1800"]}')" \
    --query Command.CommandId --output text)
  while :; do
    sleep 5
    status=$(aws ssm get-command-invocation --command-id "$cid" --instance-id "$iid" --query Status --output text 2>/dev/null || echo Pending)
    [[ "$status" == Pending || "$status" == InProgress || "$status" == Delayed ]] || break
  done
  aws ssm get-command-invocation --command-id "$cid" --instance-id "$iid" \
    --query '[StandardOutputContent,StandardErrorContent]' --output text
  [[ "$status" == Success ]] || { echo "Remote step ended: $status"; exit 1; }
}

HEALTH='ok=""; t0=$(date +%s)
for i in $(seq 90); do
  if curl -fs -o /dev/null http://127.0.0.1:8000/; then ok=1; echo "healthy after $(( $(date +%s) - t0 )) s"; break; fi; sleep 1
done'

case "${1:-}" in
  --build-only) build "$2" "$3" ;;
  --logs) remote "journalctl -u landfall -n 60 --no-pager" ;;
  --rollback)
    remote "set -euo pipefail
prev=\$(cat /srv/landfall/previous 2>/dev/null || true); [ -n \"\$prev\" ] && [ -d \"\$prev\" ] || { echo 'no previous release'; exit 1; }
cur=\$(readlink -f /srv/landfall/current)
ln -sfn \"\$prev\" /srv/landfall/current && echo \"\$cur\" > /srv/landfall/previous
systemctl restart landfall
$HEALTH
[ -n \"\$ok\" ] || { journalctl -u landfall -n 40 --no-pager; exit 1; }
echo \"now on \$(cat /srv/landfall/current/RELEASE)\"" ;;
  ""|-*) sed -n '2,10p' "$0"; exit 1 ;;
  *)
    sha=$(git -C "$ROOT" rev-parse --verify "$1^{commit}")
    acct=$(aws sts get-caller-identity --query Account --output text)
    bucket="landfall-judging-$acct"
    tgz=$(mktemp -d)/release.tgz
    build "$sha" "$tgz"
    aws s3 cp --only-show-errors "$tgz" "s3://$bucket/releases/$sha.tgz"
    caddy_b64=$(base64 < "$ROOT/deploy/Caddyfile" | tr -d '\n')
    remote "set -euo pipefail
export AWS_DEFAULT_REGION=us-east-1
R=/srv/landfall/releases/$sha
if [ ! -d \$R ]; then
  aws s3 cp --only-show-errors s3://$bucket/releases/$sha.tgz /tmp/release.tgz
  rm -rf \$R.tmp && mkdir -p \$R.tmp && tar -xzf /tmp/release.tgz -C \$R.tmp && mv \$R.tmp \$R && chown -R landfall:landfall \$R
fi
sudo -u landfall -H bash -c 'cd /srv/landfall/releases/$sha && uv sync --frozen --quiet && .venv/bin/playwright install chromium >/dev/null'
\$R/.venv/bin/playwright install-deps chromium >/dev/null 2>&1
echo '$caddy_b64' | base64 -d > /etc/caddy/Caddyfile.new
if ! cmp -s /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile; then
  (set -a; . /etc/caddy/landfall.env; caddy validate --config /etc/caddy/Caddyfile.new --adapter caddyfile >/dev/null)
  mv /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile && systemctl restart caddy && echo 'proxy config updated'
fi
prev=\$(readlink -f /srv/landfall/current 2>/dev/null || true)
ln -sfn \$R /srv/landfall/current
systemctl restart landfall
$HEALTH
if [ -z \"\$ok\" ]; then
  journalctl -u landfall -n 40 --no-pager
  if [ -n \"\$prev\" ] && [ \"\$prev\" != \$R ]; then ln -sfn \"\$prev\" /srv/landfall/current; systemctl restart landfall; echo \"ROLLED BACK to \$prev\"; fi
  exit 1
fi
if [ -n \"\$prev\" ] && [ \"\$prev\" != \$R ]; then echo \"\$prev\" > /srv/landfall/previous; fi
ls -1dt /srv/landfall/releases/*/ | tail -n +4 | xargs -r rm -rf
echo \"released $sha\"" ;;
esac
