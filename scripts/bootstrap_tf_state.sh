#!/usr/bin/env bash
# Run ONCE to create the S3 bucket and DynamoDB table that store Terraform state.
# Usage: AWS_PROFILE=saml bash scripts/bootstrap_tf_state.sh [region]
set -euo pipefail

REGION="${1:-ap-south-1}"
BUCKET="market-ai-tf-state-${REGION}"
TABLE="market-ai-tf-locks"

echo "==> Creating TF state bucket: $BUCKET (region: $REGION)"
if aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null; then
  echo "    Bucket already exists – skipping."
else
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION"
  fi
  aws s3api put-bucket-versioning --bucket "$BUCKET" \
    --versioning-configuration Status=Enabled
  aws s3api put-bucket-encryption --bucket "$BUCKET" \
    --server-side-encryption-configuration '{
      "Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]
    }'
  aws s3api put-public-access-block --bucket "$BUCKET" \
    --public-access-block-configuration \
      "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
  echo "    Bucket created and hardened."
fi

echo "==> Creating TF lock table: $TABLE"
if aws dynamodb describe-table --table-name "$TABLE" --region "$REGION" 2>/dev/null; then
  echo "    Table already exists – skipping."
else
  aws dynamodb create-table \
    --table-name "$TABLE" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region "$REGION"
  echo "    Table created."
fi

echo ""
echo "Bootstrap complete. Add this to infrastructure/main.tf terraform{} block:"
echo ""
echo "  backend \"s3\" {"
echo "    bucket         = \"$BUCKET\""
echo "    key            = \"market-ai/dev/terraform.tfstate\""
echo "    region         = \"$REGION\""
echo "    dynamodb_table = \"$TABLE\""
echo "    encrypt        = true"
echo "    assume_role = {"
echo "      role_arn = \"arn:aws:iam::<ACCOUNT_ID>:role/<DEPLOY_ROLE>\""
echo "    }"
echo "  }"
