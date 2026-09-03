#!/usr/bin/env bash
# Launch t4g.small EC2 (ARM64) with stealth SG + Cloudflare Tunnel bootstrap.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# --- Config (override via env) ---
AWS_REGION="${AWS_REGION:-us-east-1}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t4g.small}"
VOLUME_GB="${VOLUME_GB:-30}"
VOLUME_TYPE="${VOLUME_TYPE:-gp3}"
INSTANCE_NAME="${INSTANCE_NAME:-assure-prod}"
KEY_NAME="${KEY_NAME:-}"  # optional; prefer SSM Session Manager (no SSH port)
ASSURE_GIT_REF="${ASSURE_GIT_REF:-p4-account-wallet}"
ENV_FILE="$ROOT/.env.production"

bash "$ROOT/scripts/aws/preflight.sh"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE — run: bash scripts/aws/setup-tunnel.sh" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

if [ -z "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]; then
  echo "CLOUDFLARE_TUNNEL_TOKEN not set in $ENV_FILE" >&2
  exit 1
fi

TUNNEL_ID="${TUNNEL_ID:-}"
CREDS_FILE="$ROOT/scripts/aws/credentials/${TUNNEL_ID}.json"
CF_CONFIG="$ROOT/scripts/aws/cloudflared-config.yml"

AMI_ID="$(aws ssm get-parameters \
  --names /aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id \
  --region "$AWS_REGION" \
  --query 'Parameters[0].Value' \
  --output text 2>/dev/null || true)"
if [ -z "$AMI_ID" ] || [ "$AMI_ID" = "None" ]; then
  AMI_ID="$(aws ec2 describe-images \
    --region "$AWS_REGION" \
    --owners 099720109477 \
    --filters "Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-arm64-server-*" "Name=state,Values=available" \
    --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
    --output text)"
fi
if [ -z "$AMI_ID" ] || [ "$AMI_ID" = "None" ]; then
  echo "Could not resolve Ubuntu 24.04 ARM64 AMI in $AWS_REGION." >&2
  exit 1
fi

echo "Using Ubuntu 24.04 ARM64 AMI: $AMI_ID"

# --- IAM role for SSM (no inbound SSH required) ---
ROLE_NAME="${INSTANCE_NAME}-ssm-role"
PROFILE_NAME="${INSTANCE_NAME}-ssm-profile"

if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "ec2.amazonaws.com"},
        "Action": "sts:AssumeRole"
      }]
    }'
  aws iam attach-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
fi

if ! aws iam get-instance-profile --instance-profile-name "$PROFILE_NAME" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$PROFILE_NAME"
  aws iam add-role-to-instance-profile \
    --instance-profile-name "$PROFILE_NAME" \
    --role-name "$ROLE_NAME"
  sleep 10
fi

PROFILE_ARN="$(aws iam get-instance-profile \
  --instance-profile-name "$PROFILE_NAME" \
  --query 'InstanceProfile.Arn' \
  --output text)"

# --- Stealth security group: egress only, no inbound HTTP/HTTPS/SSH ---
SG_NAME="${INSTANCE_NAME}-stealth-sg"
VPC_ID="$(aws ec2 describe-vpcs \
  --filters Name=isDefault,Values=true \
  --region "$AWS_REGION" \
  --query 'Vpcs[0].VpcId' \
  --output text)"

SG_ID="$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=$SG_NAME" "Name=vpc-id,Values=$VPC_ID" \
  --region "$AWS_REGION" \
  --query 'SecurityGroups[0].GroupId' \
  --output text 2>/dev/null || echo "None")"

if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  SG_ID="$(aws ec2 create-security-group \
    --group-name "$SG_NAME" \
    --description "Assure prod - egress only, Cloudflare Tunnel inbound" \
    --vpc-id "$VPC_ID" \
    --region "$AWS_REGION" \
    --query 'GroupId' \
    --output text)"
  echo "Created stealth security group: $SG_ID (no inbound rules)"
fi

# --- Build user-data payload ---
# cloud_init.sh already embeds tunnel credentials; only inject .env.production here.
USERDATA="$(mktemp)"
{
  echo "#!/bin/bash"
  echo "set -euxo pipefail"
  echo "cat > /tmp/assure.env.production <<'ENVEOF'"
  cat "$ENV_FILE"
  echo ""
  echo "ENVEOF"
  cat "$ROOT/scripts/aws/cloud_init.sh"
} > "$USERDATA"

# --- Launch instance ---
RUN_ARGS=(
  --region "$AWS_REGION"
  --image-id "$AMI_ID"
  --instance-type "$INSTANCE_TYPE"
  --iam-instance-profile "Arn=${PROFILE_ARN}"
  --security-group-ids "$SG_ID"
  --credit-specification "CpuCredits=standard"
  --block-device-mappings "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":${VOLUME_GB},\"VolumeType\":\"${VOLUME_TYPE}\",\"DeleteOnTermination\":true}}]"
  --user-data "file://${USERDATA}"
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${INSTANCE_NAME}},{Key=Project,Value=assure},{Key=ManagedBy,Value=scripts/aws/provision-ec2.sh}]"
  --metadata-options "HttpTokens=required,HttpEndpoint=enabled"
)

if [ -n "$KEY_NAME" ]; then
  RUN_ARGS+=(--key-name "$KEY_NAME")
fi

echo "Launching ${INSTANCE_TYPE} in ${AWS_REGION} (${VOLUME_GB} GiB ${VOLUME_TYPE}, cpu-credits=standard)..."
INSTANCE_ID="$(aws ec2 run-instances "${RUN_ARGS[@]}" \
  --query 'Instances[0].InstanceId' \
  --output text)"

echo ""
echo "Instance launched: $INSTANCE_ID"
echo "Waiting for running state..."
aws ec2 wait instance-running --region "$AWS_REGION" --instance-ids "$INSTANCE_ID"

PUBLIC_IP="$(aws ec2 describe-instances \
  --region "$AWS_REGION" \
  --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' \
  --output text)"

echo ""
echo "=== Provision summary ==="
echo "Instance ID:  $INSTANCE_ID"
echo "Region:       $AWS_REGION"
echo "Type:         $INSTANCE_TYPE (cpu-credits=standard)"
echo "Volume:       ${VOLUME_GB} GiB ${VOLUME_TYPE}"
echo "Security SG:  $SG_ID (stealth — no inbound web ports)"
echo "Public IP:    ${PUBLIC_IP:-none} (not required — Cloudflare Tunnel is outbound-only)"
echo "App URL:      https://${APP_HOST:-getassureai.com}"
echo ""
echo "Connect via SSM (no SSH port open):"
echo "  aws ssm start-session --target $INSTANCE_ID --region $AWS_REGION"
echo ""
echo "Tail bootstrap log on instance:"
echo "  sudo tail -f /var/log/cloud-init-output.log"
