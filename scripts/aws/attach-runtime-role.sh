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
# Same policy on an IAM *user* (access key + secret in .env instead of a role):
#   PRINCIPAL=user ASSURE_S3_BUCKET=... bash scripts/aws/attach-runtime-role.sh assure-app-user
#   aws iam create-access-key --user-name assure-app-user   # → AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
#
# ECS deployments do not need this: infra/terraform creates the task role.
set -euo pipefail

ROLE_NAME="${1:?role or user name (e.g. assure-prod-ssm-role)}"
PRINCIPAL="${PRINCIPAL:-role}"   # role | user
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

if [[ "$PRINCIPAL" == "user" ]]; then
  aws iam put-user-policy --user-name "$ROLE_NAME" --policy-name "$POLICY_NAME" --policy-document "$DOC"
else
  aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name "$POLICY_NAME" --policy-document "$DOC"
fi
echo "Attached inline policy ${POLICY_NAME} to ${PRINCIPAL} ${ROLE_NAME}:"
echo "  bucket  s3://${ASSURE_S3_BUCKET}"
echo "  queues  arn:aws:sqs:${AWS_REGION}:${AWS_ACCOUNT_ID}:${CELERY_SQS_QUEUE_PREFIX}*"
if [[ "$PRINCIPAL" == "user" ]]; then
  echo "Create the key pair with: aws iam create-access-key --user-name ${ROLE_NAME}  → AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in .env"
else
  echo "Remove any AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from .env; boto3 uses the instance role."
fi
