#!/usr/bin/env bash
# Grant the EC2 instance role (or any role) what the application needs at
# runtime — S3 objects bucket, SQS queues, Textract fallback, optional Bedrock —
# so the app authenticates with the machine's IAM role and no AWS keys exist
# in any .env. The policy template is scripts/aws/iam-policy-assure-runtime.json.
#
#   ASSURE_S3_BUCKET=assure-prod-objects \
#   CELERY_SQS_QUEUE_PREFIX=assure-prod- \
#   bash scripts/aws/attach-runtime-role.sh assure-prod-ssm-role
#
# ECS deployments do not need this: infra/terraform creates the task role.
set -euo pipefail

ROLE_NAME="${1:?role name (e.g. assure-prod-ssm-role)}"
: "${ASSURE_S3_BUCKET:?set ASSURE_S3_BUCKET}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-eu-central-1}}"
CELERY_SQS_QUEUE_PREFIX="${CELERY_SQS_QUEUE_PREFIX:-assure-}"
AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
POLICY_NAME="${POLICY_NAME:-assure-runtime}"

TEMPLATE="$(dirname "$0")/iam-policy-assure-runtime.json"
DOC="$(sed -e "s#\${ASSURE_S3_BUCKET}#${ASSURE_S3_BUCKET}#g" \
          -e "s#\${AWS_REGION}#${AWS_REGION}#g" \
          -e "s#\${AWS_ACCOUNT_ID}#${AWS_ACCOUNT_ID}#g" \
          -e "s#\${CELERY_SQS_QUEUE_PREFIX}#${CELERY_SQS_QUEUE_PREFIX}#g" "$TEMPLATE")"

aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name "$POLICY_NAME" --policy-document "$DOC"
echo "Attached inline policy ${POLICY_NAME} to ${ROLE_NAME}:"
echo "  bucket  s3://${ASSURE_S3_BUCKET}"
echo "  queues  arn:aws:sqs:${AWS_REGION}:${AWS_ACCOUNT_ID}:${CELERY_SQS_QUEUE_PREFIX}*"
echo "Remove any AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from .env.production; boto3 uses the instance role."
