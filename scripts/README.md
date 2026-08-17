# Scripts Directory

Utility scripts for Fleet Management Platform deployment and maintenance.

## 📁 Structure

```
scripts/
├── deploy.sh              # Server-side deployment script
├── aws_setup.sh           # AWS infrastructure setup (one-time)
├── setup-git-hooks.sh     # Install git hooks for automated testing (optional)
├── lambda/                # Lambda function for CodeCommit trigger
│   ├── deploy_fleet_trigger.py
│   └── requirements.txt
└── README.md              # This file
```

---

## 🚀 deploy.sh

**Purpose:** Server-side deployment script executed automatically via Lambda or manually via SSH.

**Location:** Runs on EC2 server at `/home/ubuntu/ss-fleet-core/scripts/deploy.sh`

**What it does:**
1. Detects Docker Compose command (`docker compose` or `docker-compose`)
2. Checks port availability (8000, 6379, 9090)
3. Finds alternative ports if needed
4. Updates docker-compose.yml with available ports
5. Pulls latest code from CodeCommit
6. Stops existing containers gracefully
7. Builds new Docker images
8. Starts containers
9. Performs health check
10. Rollback on failure

**Compatibility:**
- Automatically detects and uses `docker compose` (native) or `docker-compose` (standalone)
- Works with both Docker Compose V1 and V2

**Usage:**
```bash
# Automatic (via Lambda trigger on git push)
# No manual execution needed

# Manual execution (via SSH)
ssh -i .secrets/ss-gateway-jimi.pem ubuntu@34.236.90.56
cd /home/ubuntu/ss-fleet-core
bash scripts/deploy.sh
```

**Logs:**
- Output: `/home/ubuntu/ss-fleet-core/deploy.log`
- Port configuration: `/home/ubuntu/ss-fleet-core/.ports`

---

## ⚙️ aws_setup.sh

**Purpose:** One-time AWS infrastructure setup for automated deployment.

**Location:** Runs on local machine with AWS CLI configured.

**What it does:**
1. Stores SSH key (`ss-gateway-jimi.pem`) in AWS Secrets Manager
2. Creates IAM Role (`fleet-lambda-deploy-role`) with necessary permissions
3. Packages and deploys Lambda function (`fleet-codecommit-deploy-trigger`)
4. Configures CodeCommit trigger on `main` branch

**Pre-requisites:**
- AWS CLI installed and configured
- SSH key at `.secrets/ss-gateway-jimi.pem`
- Python 3.11+ and pip installed
- AWS credentials with admin permissions

**Usage:**
```bash
# Run once to set up everything
chmod +x scripts/aws_setup.sh
./scripts/aws_setup.sh
```

**What gets created:**
- Secret: `fleet-ssh-key` (Secrets Manager)
- IAM Role: `fleet-lambda-deploy-role`
- IAM Policy: `fleet-lambda-secrets-policy`
- Lambda Function: `fleet-codecommit-deploy-trigger`
- CodeCommit Trigger: On `main` branch pushes

**Re-running:**
- Safe to re-run (updates existing resources)
- Useful for updating Lambda code or SSH key

---

## 🔧 lambda/deploy_fleet_trigger.py

**Purpose:** Lambda function triggered by CodeCommit to execute deployment.

**Runtime:** Python 3.11

**Trigger:** CodeCommit repository `ss-fleet-core` on `main` branch push

**What it does:**
1. Receives CodeCommit push event
2. Retrieves SSH key from Secrets Manager
3. Connects to EC2 via SSH (private IP: 172.31.85.75)
4. Executes `scripts/deploy.sh` on server
5. Returns deployment status

**Environment:**
- Timeout: 300 seconds (5 minutes)
- Memory: 256 MB
- IAM Role: `fleet-lambda-deploy-role`

**Dependencies:**
- `paramiko==3.4.0` - SSH connection
- `boto3==1.34.19` - AWS SDK

**Logs:**
- CloudWatch: `/aws/lambda/fleet-codecommit-deploy-trigger`

**Configuration:**
```python
SECRET_NAME = 'fleet-ssh-key'
EC2_HOST = '34.236.90.56'  # Public IP
EC2_USER = 'ubuntu'
DEPLOY_SCRIPT_PATH = '/home/ubuntu/ss-fleet-core/scripts/deploy.sh'
```

**What it does:**
1. Retrieves SSH key from Secrets Manager
2. Connects to EC2 via SSH
3. Executes `git pull` to update code
4. Runs `scripts/deploy.sh` with sudo

**Security:**
- SSH key retrieved from Secrets Manager (`fleet-ssh-key`)
- No AWS CLI or Git configuration (assumes pre-configured server)
- Uses sudo for deployment commands
- No hardcoded credentials in code

**Prerequisites (on EC2 server):**
- AWS CLI configured with CodeCommit credentials
- Git configured with CodeCommit credential helper
- User has sudo access
- Docker and Docker Compose installed

---

## 🔄 Deployment Workflow

### Automatic Deployment (Normal Flow)

```
1. Developer: git push origin main
2. CodeCommit: Receives push event
3. Lambda: fleet-codecommit-deploy-trigger triggered
4. Lambda: Retrieves SSH key from Secrets Manager
5. Lambda: SSH to EC2 (172.31.85.75)
6. EC2: Executes scripts/deploy.sh
7. EC2: Git pull, Docker build, Docker up
8. EC2: Health check
9. Lambda: Returns success/failure
```

### Manual Deployment (Fallback)

```bash
# Connect to server
ssh -i .secrets/ss-gateway-jimi.pem ubuntu@34.236.90.56

# Navigate to project
cd /home/ubuntu/ss-fleet-core

# Execute deployment
bash scripts/deploy.sh
```

---

## 🛠️ Troubleshooting

### aws_setup.sh fails

**Error: "AWS CLI not configured"**
```bash
aws configure
# Enter credentials when prompted
```

**Error: "SSH key not found"**
```bash
# Ensure key is in correct location
ls -la .secrets/ss-gateway-jimi.pem

# If missing, obtain from team and place in .secrets/
```

**Error: "Permission denied"**
```bash
chmod +x scripts/aws_setup.sh
```

### deploy.sh fails

**Error: "Port already in use"**
- Normal behavior, script finds alternative port
- Check `.ports` file for assigned ports

**Error: "Git pull failed"**
```bash
# Check Git configuration on server
ssh -i .secrets/ss-gateway-jimi.pem ubuntu@34.236.90.56
cd /home/ubuntu/ss-fleet-core
git status
git pull origin main
```

**Error: "Docker build failed"**
```bash
# Check server disk space
df -h

# Check Docker status
docker ps
docker-compose ps
```

### Lambda fails

**Check CloudWatch Logs:**
```
AWS Console → CloudWatch → Log groups → /aws/lambda/fleet-codecommit-deploy-trigger
```

**Common issues:**
- SSH key not in Secrets Manager
- EC2 security group blocking Lambda
- Incorrect EC2 IP address
- Timeout (increase Lambda timeout if needed)

---

## 📋 Maintenance

### Update Lambda Function

```bash
# After modifying deploy_fleet_trigger.py
./scripts/aws_setup.sh
# Script automatically updates existing Lambda
```

### Update SSH Key

```bash
# 1. Replace .secrets/ss-gateway-jimi.pem with new key
# 2. Re-run setup
./scripts/aws_setup.sh
# Script updates existing secret
```

### Remove Deployment Automation

```bash
# Remove CodeCommit trigger
aws codecommit put-repository-triggers \
  --repository-name ss-fleet-core \
  --triggers '[]' \
  --region us-east-1

# Delete Lambda function
aws lambda delete-function \
  --function-name fleet-codecommit-deploy-trigger \
  --region us-east-1

# Delete IAM resources (see DEPLOYMENT.md for full cleanup)
```

---

## 🔧 setup-git-hooks.sh

**Purpose:** Install git hooks for automated testing (optional for development)

**What it does:**
- Creates `pre-commit` hook: Runs security tests before commit
- Creates `pre-push` hook: Runs all tests before push

**Usage:**
```bash
# Install hooks
chmod +x scripts/setup-git-hooks.sh
./scripts/setup-git-hooks.sh

# Bypass hooks temporarily (if needed)
git commit --no-verify
git push --no-verify
```

**Note:** This is optional for local development. Not required for deployment.

---

## 🔒 Security Notes

- **Never commit** `.secrets/` directory or `*.pem` files
- **Never commit** AWS credentials or API keys
- SSH key stored only in:
  - Local: `.secrets/ss-gateway-jimi.pem` (gitignored)
  - AWS: Secrets Manager (`fleet-ssh-key`, encrypted at rest)
- Lambda uses public IP (34.236.90.56) for EC2 connection
- IAM Role has minimum required permissions
- **Server Prerequisites:** AWS CLI and Git must be pre-configured manually by developer

---

## 📚 Additional Documentation

- [DEPLOYMENT.md](../DEPLOYMENT.md) - Complete deployment guide
- [ARCHITECTURE.md](../ARCHITECTURE.md) - System architecture
- [README.md](../README.md) - Project overview

---

**Last Updated:** 2025-01-14
