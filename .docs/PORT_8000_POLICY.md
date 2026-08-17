# Port 8000 Policy - Critical Configuration

## ⚠️ CRITICAL REQUIREMENT

**The Fleet Management Platform API MUST ALWAYS run on port 8000.**

This is a **non-negotiable requirement** due to:

1. **External Integrations**: External systems are configured to connect to port 8000
2. **Firewall Rules**: AWS Security Groups and network policies are configured for port 8000
3. **Client Documentation**: All client documentation references port 8000
4. **Service Contracts**: SLAs and service agreements specify port 8000

## Problem Solved

### Previous Issue

The deployment script (`scripts/deploy.sh`) was **automatically modifying** `docker-compose.yml` when port 8000 was in use:

```bash
# OLD BEHAVIOR (REMOVED):
sed -i "s/\"8000:8000\"/\"$api_port:8000\"/" docker-compose.yml
# This would change the file to: "8001:8000" if port 8000 was busy
```

This caused:
- ❌ Uncommitted changes in the repository
- ❌ API running on wrong port (8001, 8002, etc.)
- ❌ External integrations failing
- ❌ Client connection errors

### New Solution

The deployment script now **forces port 8000 to be available**:

```bash
# NEW BEHAVIOR:
1. Stop all Docker containers first
2. Kill any process using port 8000
3. Verify docker-compose.yml has correct port (8000:8000)
4. Start containers on port 8000
5. Validate API is responding on port 8000
```

## Technical Implementation

### 1. Deploy Script Changes (`scripts/deploy.sh`)

**Key functions added:**

```bash
force_free_port() {
    # Finds and kills any process using port 8000
    # Uses lsof to identify the process
    # Uses kill -9 to force termination
}

stop_docker_containers() {
    # Stops all fleet_* containers
    # Removes stuck containers
}

verify_requirements() {
    # Ensures lsof is installed
    # Validates .env exists
}
```

**Deployment flow:**

```
1. Stop all existing Docker containers
2. Force free port 8000 (kill any process using it)
3. Verify docker-compose.yml has "8000:8000"
4. Build and start containers
5. Wait for health check on port 8000
6. Validate API is responding correctly
```

### 2. Pre-Commit Hook Enhancement

The pre-commit hook now validates `docker-compose.yml`:

```bash
# .git/hooks/pre-commit
if git diff --cached --name-only | grep -q "docker-compose.yml"; then
    if ! grep -q '"8000:8000"' docker-compose.yml; then
        echo "❌ COMMIT CANCELLED: docker-compose.yml port must be 8000:8000"
        exit 1
    fi
fi
```

This prevents committing incorrect port configurations.

### 3. Port Validation Script

The validation script (`.scripts/validate-port.sh`) can be run manually:

```bash
bash .scripts/validate-port.sh
```

**What it checks:**
- ✅ Container is running
- ✅ Port mapping is 8000:8000
- ✅ API is responding on port 8000

## Usage Guidelines

### For Developers

**Local development:**
```bash
# Start containers
docker compose up -d

# Validate port
bash .scripts/validate-port.sh

# If port is wrong, the pre-commit hook will prevent you from committing
```

**If you see port conflicts:**
```bash
# Find what's using port 8000
lsof -ti:8000

# Kill the process
kill -9 $(lsof -ti:8000)

# Restart containers
docker compose down
docker compose up -d
```

### For DevOps/Deployment

**Automated deployment (EC2/Lambda):**
```bash
# The deploy script handles everything automatically:
bash scripts/deploy.sh

# It will:
# 1. Stop old containers
# 2. Free port 8000
# 3. Verify configuration
# 4. Deploy on port 8000
```

**Manual deployment verification:**
```bash
# After deployment, verify:
curl http://localhost:8000/health

# Check container ports:
docker ps --filter name=fleet_

# Expected output:
# 0.0.0.0:8000->8000/tcp
```

## Configuration Files

### docker-compose.yml (FIXED)

```yaml
api:
  ports:
    # ⚠️ CRITICAL: PORT 8000 IS FIXED - NEVER CHANGE THIS VALUE
    - "8000:8000"
```

**This must NEVER be changed to:**
- ❌ `"8001:8000"`
- ❌ `"8080:8000"`
- ❌ `"3000:8000"`
- ❌ Any other external port

### Environment Variables

Port 8000 is also hardcoded in:

- `app/core/config.py`: `PORT = 8000`
- `Dockerfile`: `EXPOSE 8000`
- `docker-compose.yml`: `command: uvicorn app.main:app --host 0.0.0.0 --port 8000`

## Troubleshooting

### Issue: "Port 8000 is already in use"

**Solution:**
```bash
# Find the process
lsof -ti:8000

# Kill it
kill -9 $(lsof -ti:8000)

# Or let the deploy script handle it automatically
bash scripts/deploy.sh
```

### Issue: "docker-compose.yml has wrong port"

**Solution:**
```bash
# Edit docker-compose.yml manually
# Change line 64 to:
- "8000:8000"

# Verify
grep '"8000:8000"' docker-compose.yml

# Redeploy
docker compose down
docker compose up -d
```

### Issue: "Pre-commit hook blocking commit"

**Solution:**
```bash
# Fix docker-compose.yml first
# Ensure it has "8000:8000"

# Then commit
git add docker-compose.yml
git commit -m "Fix port configuration"

# DO NOT use --no-verify unless absolutely necessary
```

## Monitoring and Alerts

### Health Check Endpoint

```bash
# Always use port 8000 for health checks
curl http://localhost:8000/health

# Expected response:
{
  "status": "healthy",
  "database": "connected",
  "redis": "connected"
}
```

### Automated Validation

The deployment script automatically validates:
1. Port configuration in docker-compose.yml
2. Container is running on port 8000
3. API responds on port 8000
4. Health check passes

If any validation fails, deployment is **automatically rolled back**.

## Security Considerations

### AWS Security Group Rules

**Inbound rules must allow:**
- Port 8000 (TCP) from authorized IP ranges
- Port 22 (SSH) for administration

**Firewall configuration:**
```bash
# Allow port 8000
sudo ufw allow 8000/tcp

# Verify
sudo ufw status
```

### Process Isolation

The `force_free_port()` function:
- ✅ Only kills processes on port 8000
- ✅ Logs which process was killed
- ✅ Verifies port is freed before proceeding
- ❌ Does not kill system processes (protected)

## Change History

### Version 1.0 (Current)
- ✅ Deploy script forces port 8000 availability
- ✅ Pre-commit hook validates docker-compose.yml
- ✅ Automatic port validation in deployment
- ✅ Documentation created

### Version 0.x (Deprecated)
- ❌ Deploy script modified docker-compose.yml automatically
- ❌ API could run on ports 8001, 8002, etc.
- ❌ No validation of port configuration

## References

- [docker-compose.yml](../docker-compose.yml) - Lines 61-64
- [deploy.sh](../scripts/deploy.sh) - Main deployment script
- [validate-port.sh](../.scripts/validate-port.sh) - Port validation
- [.git/hooks/pre-commit](../.git/hooks/pre-commit) - Pre-commit validation

## Support

If you encounter issues with port 8000:

1. Check logs: `docker compose logs api`
2. Run validation: `bash .scripts/validate-port.sh`
3. Review this document
4. Contact DevOps team

**Remember: Port 8000 is not optional. It is a critical requirement.**
