#!/usr/bin/env bash
# Deploy dashboard to S3 and invalidate CloudFront cache.
# Usage: ./scripts/deploy-dashboard.sh [stack-name]
set -euo pipefail

STACK_NAME="${1:-wifi-csi-dashboard}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DASHBOARD_DIR="$SCRIPT_DIR/../dashboard"

# Get outputs from CloudFormation stack
BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue' --output text)
DIST_ID=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query 'Stacks[0].Outputs[?OutputKey==`DistributionId`].OutputValue' --output text)

if [ -z "$BUCKET" ] || [ -z "$DIST_ID" ]; then
  echo "ERROR: Could not read stack outputs. Is '$STACK_NAME' deployed?"
  exit 1
fi

echo "Deploying dashboard/ → s3://$BUCKET"

# Sync static assets (exclude dev/test files)
aws s3 sync "$DASHBOARD_DIR" "s3://$BUCKET" \
  --delete \
  --exclude "node_modules/*" \
  --exclude "package*.json" \
  --exclude "vitest.config.js" \
  --exclude "tests/*" \
  --exclude ".DS_Store" \
  --cache-control "public, max-age=3600"

# HTML files: short cache for quick updates
for html in "$DASHBOARD_DIR"/*.html; do
  [ -f "$html" ] || continue
  aws s3 cp "$html" "s3://$BUCKET/$(basename "$html")" \
    --cache-control "public, max-age=60"
done

# Invalidate CloudFront cache
echo "Invalidating CloudFront distribution $DIST_ID"
aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*" --output text

CF_DOMAIN=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query 'Stacks[0].Outputs[?OutputKey==`DistributionDomainName`].OutputValue' --output text)
echo "Deploy complete. Dashboard: https://$CF_DOMAIN"
