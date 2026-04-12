# Dashboard S3 Deployment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the WiFi CSI dashboard as a static site on S3 + CloudFront with automated CI/CD from GitHub Actions.

**Architecture:** S3 bucket configured for static website hosting behind CloudFront for HTTPS/caching. GitHub Actions deploys on push to `main` when `dashboard/` files change and tests pass. CloudFormation template defines the infrastructure as code.

**Tech Stack:** AWS CloudFormation, S3, CloudFront, ACM, GitHub Actions, AWS CLI v2

---

## File Structure

| File | Purpose |
|------|---------|
| `infra/cloudformation.yml` | CloudFormation template: S3 bucket, CloudFront distribution, OAC, bucket policy |
| `.github/workflows/deploy-dashboard.yml` | GitHub Actions: test → deploy → invalidate cache |
| `scripts/deploy-dashboard.sh` | Manual deploy script for local use / debugging |

---

### Task 1: CloudFormation Template — S3 + CloudFront

**Files:**
- Create: `infra/cloudformation.yml`

- [ ] **Step 1: Create the CloudFormation template**

```yaml
# infra/cloudformation.yml
AWSTemplateFormatVersion: '2010-09-09'
Description: WiFi CSI Dashboard — S3 static site + CloudFront

Parameters:
  BucketName:
    Type: String
    Default: wifi-csi-dashboard
    Description: S3 bucket name for dashboard hosting

Resources:
  DashboardBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: !Ref BucketName
      PublicAccessBlockConfiguration:
        BlockPublicAcls: true
        BlockPublicPolicy: true
        IgnorePublicAcls: true
        RestrictPublicBuckets: true
      WebsiteConfiguration:
        IndexDocument: index.html
        ErrorDocument: index.html

  CloudFrontOAC:
    Type: AWS::CloudFront::OriginAccessControl
    Properties:
      OriginAccessControlConfig:
        Name: !Sub '${BucketName}-oac'
        OriginAccessControlOriginType: s3
        SigningBehavior: always
        SigningProtocol: sigv4

  BucketPolicy:
    Type: AWS::S3::BucketPolicy
    Properties:
      Bucket: !Ref DashboardBucket
      PolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Sid: AllowCloudFrontServicePrincipal
            Effect: Allow
            Principal:
              Service: cloudfront.amazonaws.com
            Action: s3:GetObject
            Resource: !Sub '${DashboardBucket.Arn}/*'
            Condition:
              StringEquals:
                AWS:SourceArn: !Sub 'arn:aws:cloudfront::${AWS::AccountId}:distribution/${Distribution}'

  Distribution:
    Type: AWS::CloudFront::Distribution
    Properties:
      DistributionConfig:
        Enabled: true
        DefaultRootObject: index.html
        HttpVersion: http2and3
        PriceClass: PriceClass_100
        Origins:
          - Id: S3Origin
            DomainName: !GetAtt DashboardBucket.RegionalDomainName
            OriginAccessControlId: !Ref CloudFrontOAC
            S3OriginConfig:
              OriginAccessIdentity: ''
        DefaultCacheBehavior:
          TargetOriginId: S3Origin
          ViewerProtocolPolicy: redirect-to-https
          AllowedMethods: [GET, HEAD, OPTIONS]
          CachedMethods: [GET, HEAD]
          Compress: true
          CachePolicyId: 658327ea-f89d-4fab-a63d-7e88639e58f6  # CachingOptimized
          ResponseHeadersPolicyId: eaab4381-ed33-4a86-88ca-d9558dc6cd63  # CORS-with-preflight
        CustomErrorResponses:
          - ErrorCode: 403
            ResponseCode: 200
            ResponsePagePath: /index.html
            ErrorCachingMinTTL: 10

Outputs:
  BucketName:
    Value: !Ref DashboardBucket
  DistributionId:
    Value: !Ref Distribution
  DistributionDomainName:
    Value: !GetAtt Distribution.DomainName
    Description: CloudFront URL for the dashboard
```

- [ ] **Step 2: Validate the template locally**

Run: `aws cloudformation validate-template --template-body file://infra/cloudformation.yml`
Expected: `{ "Parameters": [...] }` — valid template

- [ ] **Step 3: Commit**

```bash
git add infra/cloudformation.yml
git commit -m "infra: add CloudFormation template for S3 + CloudFront dashboard hosting (HAL-194)"
```

---

### Task 2: Manual Deploy Script

**Files:**
- Create: `scripts/deploy-dashboard.sh`

- [ ] **Step 1: Create the deploy script**

```bash
#!/usr/bin/env bash
# scripts/deploy-dashboard.sh — Deploy dashboard to S3 and invalidate CloudFront cache
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
aws s3 cp "$DASHBOARD_DIR/index.html" "s3://$BUCKET/index.html" \
  --cache-control "public, max-age=60"
aws s3 cp "$DASHBOARD_DIR/editor.html" "s3://$BUCKET/editor.html" \
  --cache-control "public, max-age=60"

# Invalidate CloudFront cache
echo "Invalidating CloudFront distribution $DIST_ID"
aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*" --output text

echo "Deploy complete. Dashboard: https://$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query 'Stacks[0].Outputs[?OutputKey==`DistributionDomainName`].OutputValue' --output text)"
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x scripts/deploy-dashboard.sh`

- [ ] **Step 3: Commit**

```bash
git add scripts/deploy-dashboard.sh
git commit -m "scripts: add manual dashboard deploy script (HAL-194)"
```

---

### Task 3: GitHub Actions CI/CD Workflow

**Files:**
- Create: `.github/workflows/deploy-dashboard.yml`

- [ ] **Step 1: Create the workflow**

```yaml
# .github/workflows/deploy-dashboard.yml
name: Deploy Dashboard

on:
  push:
    branches: [main]
    paths:
      - 'dashboard/**'
      - '.github/workflows/deploy-dashboard.yml'

  workflow_dispatch: {}

permissions:
  id-token: write
  contents: read

env:
  AWS_REGION: us-east-1
  STACK_NAME: wifi-csi-dashboard

jobs:
  test:
    name: Dashboard Tests
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: dashboard
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm
          cache-dependency-path: dashboard/package-lock.json
      - run: npm ci
      - run: npm test

  deploy:
    name: Deploy to S3
    needs: test
    runs-on: ubuntu-latest
    environment: production
    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}

      - name: Get stack outputs
        id: stack
        run: |
          BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
            --query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue' --output text)
          DIST_ID=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
            --query 'Stacks[0].Outputs[?OutputKey==`DistributionId`].OutputValue' --output text)
          echo "bucket=$BUCKET" >> "$GITHUB_OUTPUT"
          echo "distribution_id=$DIST_ID" >> "$GITHUB_OUTPUT"

      - name: Sync to S3
        run: |
          aws s3 sync dashboard/ s3://${{ steps.stack.outputs.bucket }} \
            --delete \
            --exclude "node_modules/*" \
            --exclude "package*.json" \
            --exclude "vitest.config.js" \
            --exclude "tests/*" \
            --exclude ".DS_Store" \
            --cache-control "public, max-age=3600"

          # HTML: short cache
          aws s3 cp dashboard/index.html s3://${{ steps.stack.outputs.bucket }}/index.html \
            --cache-control "public, max-age=60"
          aws s3 cp dashboard/editor.html s3://${{ steps.stack.outputs.bucket }}/editor.html \
            --cache-control "public, max-age=60"

      - name: Invalidate CloudFront
        run: |
          aws cloudfront create-invalidation \
            --distribution-id ${{ steps.stack.outputs.distribution_id }} \
            --paths "/*"
```

- [ ] **Step 2: Verify workflow syntax**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/deploy-dashboard.yml')); print('Valid YAML')"`
Expected: `Valid YAML`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/deploy-dashboard.yml
git commit -m "ci: add GitHub Actions workflow for dashboard S3 deployment (HAL-193)"
```

---

### Task 4: Final Verification and Documentation

- [ ] **Step 1: Verify all files are in place**

Run:
```bash
ls -la infra/cloudformation.yml scripts/deploy-dashboard.sh .github/workflows/deploy-dashboard.yml
```
Expected: All three files exist

- [ ] **Step 2: Run dashboard tests to confirm nothing broke**

Run: `cd dashboard && npm test`
Expected: All tests pass

- [ ] **Step 3: Final commit with all files**

```bash
git add infra/ scripts/ .github/
git commit -m "feat: complete S3 + CloudFront deployment infrastructure (HAL-192, HAL-193, HAL-194)

- CloudFormation template: S3 bucket, CloudFront with OAC, CORS headers
- GitHub Actions: test → deploy → cache invalidation on push to main
- Manual deploy script for local use

Co-Authored-By: Paperclip <noreply@paperclip.ing>"
```

---

## Setup Instructions (Post-Deploy)

After creating these files, the human operator needs to:

1. **Deploy the CloudFormation stack:**
   ```bash
   aws cloudformation deploy --template-file infra/cloudformation.yml \
     --stack-name wifi-csi-dashboard --parameter-overrides BucketName=wifi-csi-dashboard
   ```

2. **Set up GitHub OIDC for AWS** (one-time):
   - Create IAM OIDC identity provider for `token.actions.githubusercontent.com`
   - Create deploy role with S3 + CloudFront permissions
   - Add `AWS_DEPLOY_ROLE_ARN` to GitHub repo secrets

3. **Create GitHub environment** `production` with required reviewers (optional)

4. **First deploy:** Push to main or use `workflow_dispatch`
