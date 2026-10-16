#!/usr/bin/env bash
# Delete all market-ai-dev resources from AWS so Terraform can re-create them cleanly.
# Usage: AWS_PROFILE=saml bash scripts/cleanup_aws_resources.sh [region]
# Review the list printed and confirm before resources are deleted.
set -euo pipefail

REGION="${1:-ap-south-1}"
PREFIX="market-ai-dev"

echo "==> This will DELETE all AWS resources with prefix: $PREFIX in $REGION"
echo "    Press Ctrl-C within 10 seconds to abort..."
sleep 10

# ── Lambda ────────────────────────────────────────────────────────────────────
echo "==> Deleting Lambda functions..."
for FN in morning evening weekly; do
  NAME="${PREFIX}-${FN}"
  if aws lambda get-function --function-name "$NAME" --region "$REGION" &>/dev/null; then
    aws lambda delete-function --function-name "$NAME" --region "$REGION"
    echo "    Deleted Lambda: $NAME"
  else
    echo "    Not found (skip): $NAME"
  fi
done

# ── CloudWatch Event Rules (EventBridge) ──────────────────────────────────────
echo "==> Deleting EventBridge rules..."
for RULE in morning evening weekly; do
  NAME="${PREFIX}-${RULE}"
  if aws events describe-rule --name "$NAME" --region "$REGION" &>/dev/null; then
    # Remove targets first
    TARGETS=$(aws events list-targets-by-rule --rule "$NAME" --region "$REGION" \
      --query 'Targets[*].Id' --output text 2>/dev/null || true)
    if [ -n "$TARGETS" ]; then
      aws events remove-targets --rule "$NAME" --ids $TARGETS --region "$REGION"
    fi
    aws events delete-rule --name "$NAME" --region "$REGION"
    echo "    Deleted rule: $NAME"
  else
    echo "    Not found (skip): $NAME"
  fi
done

# ── CloudWatch Log Groups ─────────────────────────────────────────────────────
echo "==> Deleting CloudWatch log groups..."
for FN in morning evening weekly; do
  LG="/aws/lambda/${PREFIX}-${FN}"
  if aws logs describe-log-groups --log-group-name-prefix "$LG" --region "$REGION" \
      --query 'logGroups[0].logGroupName' --output text 2>/dev/null | grep -q "$LG"; then
    aws logs delete-log-group --log-group-name "$LG" --region "$REGION"
    echo "    Deleted log group: $LG"
  else
    echo "    Not found (skip): $LG"
  fi
done

# ── CloudWatch Alarms ─────────────────────────────────────────────────────────
echo "==> Deleting CloudWatch alarms..."
for FN in morning evening weekly; do
  ALARM="${PREFIX}-${FN}-errors"
  if aws cloudwatch describe-alarms --alarm-names "$ALARM" --region "$REGION" \
      --query 'MetricAlarms[0].AlarmName' --output text 2>/dev/null | grep -q "$ALARM"; then
    aws cloudwatch delete-alarms --alarm-names "$ALARM" --region "$REGION"
    echo "    Deleted alarm: $ALARM"
  else
    echo "    Not found (skip): $ALARM"
  fi
done

# ── Secrets Manager ───────────────────────────────────────────────────────────
echo "==> Deleting Secrets Manager secrets (force-delete, no recovery window)..."
for SECRET in groww telegram; do
  NAME="${PREFIX}/${SECRET}"
  if aws secretsmanager describe-secret --secret-id "$NAME" --region "$REGION" &>/dev/null; then
    aws secretsmanager delete-secret --secret-id "$NAME" \
      --force-delete-without-recovery --region "$REGION"
    echo "    Deleted secret: $NAME"
  else
    echo "    Not found (skip): $NAME"
  fi
done

# ── ECR Repository ────────────────────────────────────────────────────────────
echo "==> Deleting ECR repository: $PREFIX"
if aws ecr describe-repositories --repository-names "$PREFIX" --region "$REGION" &>/dev/null; then
  aws ecr delete-repository --repository-name "$PREFIX" --force --region "$REGION"
  echo "    Deleted ECR repo: $PREFIX"
else
  echo "    Not found (skip): $PREFIX"
fi

# ── IAM Role + Policy ─────────────────────────────────────────────────────────
echo "==> Deleting IAM role: ${PREFIX}-lambda"
ROLE="${PREFIX}-lambda"
if aws iam get-role --role-name "$ROLE" &>/dev/null; then
  # Detach / delete all inline policies first
  for POL in $(aws iam list-role-policies --role-name "$ROLE" --query 'PolicyNames' --output text 2>/dev/null); do
    aws iam delete-role-policy --role-name "$ROLE" --policy-name "$POL"
  done
  aws iam delete-role --role-name "$ROLE"
  echo "    Deleted IAM role: $ROLE"
else
  echo "    Not found (skip): $ROLE"
fi

# ── DynamoDB Table ────────────────────────────────────────────────────────────
echo "==> Deleting DynamoDB table: ${PREFIX}-state"
TABLE="${PREFIX}-state"
if aws dynamodb describe-table --table-name "$TABLE" --region "$REGION" &>/dev/null; then
  aws dynamodb delete-table --table-name "$TABLE" --region "$REGION"
  echo "    Deleted DynamoDB table: $TABLE"
else
  echo "    Not found (skip): $TABLE"
fi

# ── S3 Data Bucket ────────────────────────────────────────────────────────────
echo "==> Looking for S3 buckets with prefix: ${PREFIX}-data-"
BUCKETS=$(aws s3api list-buckets --query "Buckets[?starts_with(Name,'${PREFIX}-data-')].Name" \
  --output text 2>/dev/null || true)
for BUCKET in $BUCKETS; do
  echo "    Emptying and deleting bucket: $BUCKET"
  aws s3 rm "s3://$BUCKET" --recursive --region "$REGION"
  aws s3api delete-bucket --bucket "$BUCKET" --region "$REGION"
  echo "    Deleted: $BUCKET"
done

echo ""
echo "==> Cleanup complete. Run 'terraform apply' to re-create resources from scratch."
