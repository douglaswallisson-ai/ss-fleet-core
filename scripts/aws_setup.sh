#!/bin/bash
set -e

# Fleet Management Platform - AWS Deployment Setup Script
# Configures Secrets Manager, IAM Role, Lambda Function, and CodeCommit Trigger

# Configuration
AWS_REGION="us-east-1"
SECRET_NAME="fleet-ssh-key"
LAMBDA_FUNCTION_NAME="fleet-codecommit-deploy-trigger"
LAMBDA_ROLE_NAME="fleet-lambda-deploy-role"
CODECOMMIT_REPO_NAME="ss-fleet-core"
SSH_KEY_PATH="./.secrets/ss-gateway-jimi.pem"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR:${NC} $1"
}

warning() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING:${NC} $1"
}

# Check if AWS CLI is configured
if ! aws sts get-caller-identity &> /dev/null; then
    error "AWS CLI not configured. Please run 'aws configure' first."
    exit 1
fi

log "AWS CLI configured successfully"

# Step 1: Store SSH key in Secrets Manager
log "Step 1: Storing SSH key in AWS Secrets Manager..."

if [ ! -f "$SSH_KEY_PATH" ]; then
    error "SSH key not found at $SSH_KEY_PATH"
    exit 1
fi

SSH_KEY_CONTENT=$(cat "$SSH_KEY_PATH")

# Check if secret already exists
if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$AWS_REGION" &> /dev/null; then
    warning "Secret $SECRET_NAME already exists. Updating..."
    aws secretsmanager update-secret \
        --secret-id "$SECRET_NAME" \
        --secret-string "{\"ssh_key\":\"$(echo "$SSH_KEY_CONTENT" | sed 's/$/\\n/' | tr -d '\n')\"}" \
        --region "$AWS_REGION"
else
    log "Creating new secret $SECRET_NAME..."
    aws secretsmanager create-secret \
        --name "$SECRET_NAME" \
        --description "SSH private key for Fleet deployment to EC2" \
        --secret-string "{\"ssh_key\":\"$(echo "$SSH_KEY_CONTENT" | sed 's/$/\\n/' | tr -d '\n')\"}" \
        --region "$AWS_REGION"
fi

log "SSH key stored in Secrets Manager successfully"

# Get secret ARN
SECRET_ARN=$(aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$AWS_REGION" --query 'ARN' --output text)
log "Secret ARN: $SECRET_ARN"

# Step 2: Create IAM Role for Lambda
log "Step 2: Creating IAM Role for Lambda..."

TRUST_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF
)

# Check if role already exists
if aws iam get-role --role-name "$LAMBDA_ROLE_NAME" &> /dev/null; then
    warning "IAM Role $LAMBDA_ROLE_NAME already exists. Skipping creation..."
else
    log "Creating IAM Role $LAMBDA_ROLE_NAME..."
    aws iam create-role \
        --role-name "$LAMBDA_ROLE_NAME" \
        --assume-role-policy-document "$TRUST_POLICY" \
        --description "Role for Fleet CodeCommit deployment Lambda function"
fi

# Attach policies
log "Attaching policies to IAM Role..."

# Basic Lambda execution policy
aws iam attach-role-policy \
    --role-name "$LAMBDA_ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"

# Create custom policy for Secrets Manager access
POLICY_NAME="fleet-lambda-secrets-policy"
POLICY_DOCUMENT=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ],
      "Resource": "$SECRET_ARN"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances",
        "ec2:DescribeNetworkInterfaces"
      ],
      "Resource": "*"
    }
  ]
}
EOF
)

# Check if policy already exists
POLICY_ARN=$(aws iam list-policies --query "Policies[?PolicyName=='$POLICY_NAME'].Arn" --output text)

if [ -z "$POLICY_ARN" ]; then
    log "Creating custom IAM policy $POLICY_NAME..."
    POLICY_ARN=$(aws iam create-policy \
        --policy-name "$POLICY_NAME" \
        --policy-document "$POLICY_DOCUMENT" \
        --description "Policy for Fleet Lambda to access Secrets Manager" \
        --query 'Policy.Arn' \
        --output text)
else
    warning "Policy $POLICY_NAME already exists. Using existing policy..."
fi

aws iam attach-role-policy \
    --role-name "$LAMBDA_ROLE_NAME" \
    --policy-arn "$POLICY_ARN"

log "IAM Role configured successfully"

# Get role ARN
ROLE_ARN=$(aws iam get-role --role-name "$LAMBDA_ROLE_NAME" --query 'Role.Arn' --output text)
log "Role ARN: $ROLE_ARN"

# Wait for role to be available
log "Waiting for IAM role to propagate..."
sleep 10

# Step 3: Package and Deploy Lambda Function
log "Step 3: Packaging Lambda function..."

LAMBDA_DIR="./scripts/lambda"
PACKAGE_DIR="./scripts/lambda/package"

# Create package directory
mkdir -p "$PACKAGE_DIR"

# Install dependencies using Docker for Lambda compatibility
log "Installing Lambda dependencies (using Docker for compatibility)..."
if command -v docker &> /dev/null; then
    log "Using Docker to build Lambda-compatible package..."
    docker run --rm \
        -v "$PWD/$LAMBDA_DIR":/var/task \
        -v "$PWD/$PACKAGE_DIR":/var/task/package \
        public.ecr.aws/lambda/python:3.11 \
        pip install -r /var/task/requirements.txt -t /var/task/package --quiet --no-cache-dir
else
    warning "Docker not found. Using local pip (may cause compatibility issues)..."
    pip install -r "$LAMBDA_DIR/requirements.txt" -t "$PACKAGE_DIR" --quiet --platform manylinux2014_x86_64 --only-binary=:all:
fi

# Copy Lambda function
cp "$LAMBDA_DIR/deploy_fleet_trigger.py" "$PACKAGE_DIR/"

# Create deployment package
cd "$PACKAGE_DIR"
zip -r ../lambda_deployment.zip . > /dev/null
cd - > /dev/null

log "Lambda package created"

# Deploy Lambda function
log "Deploying Lambda function..."

if aws lambda get-function --function-name "$LAMBDA_FUNCTION_NAME" --region "$AWS_REGION" &> /dev/null; then
    warning "Lambda function $LAMBDA_FUNCTION_NAME already exists. Updating code..."
    aws lambda update-function-code \
        --function-name "$LAMBDA_FUNCTION_NAME" \
        --zip-file "fileb://scripts/lambda/lambda_deployment.zip" \
        --region "$AWS_REGION" > /dev/null
else
    log "Creating new Lambda function $LAMBDA_FUNCTION_NAME..."
    aws lambda create-function \
        --function-name "$LAMBDA_FUNCTION_NAME" \
        --runtime python3.11 \
        --role "$ROLE_ARN" \
        --handler deploy_fleet_trigger.lambda_handler \
        --zip-file "fileb://scripts/lambda/lambda_deployment.zip" \
        --timeout 300 \
        --memory-size 256 \
        --description "Automated deployment trigger for Fleet Management Platform" \
        --region "$AWS_REGION" > /dev/null
fi

log "Lambda function deployed successfully"

# Get Lambda function ARN
LAMBDA_ARN=$(aws lambda get-function --function-name "$LAMBDA_FUNCTION_NAME" --region "$AWS_REGION" --query 'Configuration.FunctionArn' --output text)
log "Lambda ARN: $LAMBDA_ARN"

# Step 4: Configure CodeCommit Trigger
log "Step 4: Configuring CodeCommit trigger..."

# Add Lambda permission for CodeCommit
log "Adding Lambda permission for CodeCommit..."
aws lambda add-permission \
    --function-name "$LAMBDA_FUNCTION_NAME" \
    --statement-id "AllowExecutionFromCodeCommit" \
    --action "lambda:InvokeFunction" \
    --principal codecommit.amazonaws.com \
    --source-arn "arn:aws:codecommit:$AWS_REGION:$(aws sts get-caller-identity --query 'Account' --output text):$CODECOMMIT_REPO_NAME" \
    --region "$AWS_REGION" 2>/dev/null || warning "Permission already exists"

# Get CodeCommit repository ARN
REPO_ARN="arn:aws:codecommit:$AWS_REGION:$(aws sts get-caller-identity --query 'Account' --output text):$CODECOMMIT_REPO_NAME"

# Create trigger configuration
TRIGGER_CONFIG=$(cat <<EOF
{
  "repositoryName": "$CODECOMMIT_REPO_NAME",
  "triggers": [
    {
      "name": "fleet-deployment-trigger",
      "destinationArn": "$LAMBDA_ARN",
      "branches": ["main"],
      "events": ["all"]
    }
  ]
}
EOF
)

# Apply trigger
log "Creating CodeCommit trigger..."
aws codecommit put-repository-triggers --cli-input-json "$TRIGGER_CONFIG" --region "$AWS_REGION"

log "CodeCommit trigger configured successfully"

# Clean up
log "Cleaning up temporary files..."
rm -rf "$PACKAGE_DIR"
rm -f "./scripts/lambda/lambda_deployment.zip"

log "========================================="
log "AWS Deployment Setup Complete!"
log "========================================="
log "Secret Name: $SECRET_NAME"
log "IAM Role: $LAMBDA_ROLE_NAME"
log "Lambda Function: $LAMBDA_FUNCTION_NAME"
log "CodeCommit Repo: $CODECOMMIT_REPO_NAME"
log "Trigger: Enabled on 'main' branch"
log "========================================="
log "Next Steps:"
log "1. Ensure EC2 security group allows Lambda to connect"
log "2. Run initial deployment manually on server"
log "3. Test by pushing to main branch"
log "========================================="
