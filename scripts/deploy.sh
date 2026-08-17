#!/bin/bash
set -e

# Fleet Management Platform - Deployment Script
# Executes on EC2 server via Lambda trigger from CodeCommit
#
# ⚠️ CRITICAL: This application MUST ALWAYS run on port 8000
# - External integrations depend on port 8000
# - Firewall rules configured for port 8000
# - Client documentation references port 8000

PROJECT_DIR="/home/ubuntu/ss-fleet-core"
LOG_FILE="/home/ubuntu/ss-fleet-core/deploy.log"

# FIXED PORTS - NEVER CHANGE THESE
API_PORT=8000
REDIS_INTERNAL_PORT=6379
PROMETHEUS_INTERNAL_PORT=9090

# External ports (can be mapped differently to avoid conflicts)
REDIS_EXTERNAL_PORT=6380
PROMETHEUS_EXTERNAL_PORT=9091

# Detect docker-compose command (docker compose vs docker-compose)
if command -v docker-compose &> /dev/null; then
    DOCKER_COMPOSE="docker-compose"
elif docker compose version &> /dev/null; then
    DOCKER_COMPOSE="docker compose"
else
    echo "ERROR: Neither 'docker-compose' nor 'docker compose' found"
    exit 1
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1" | tee -a "$LOG_FILE"
}

error() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR:${NC} $1" | tee -a "$LOG_FILE"
}

warning() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING:${NC} $1" | tee -a "$LOG_FILE"
}

info() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')] INFO:${NC} $1" | tee -a "$LOG_FILE"
}

# Force kill process on port (if any)
force_free_port() {
    local port=$1
    local port_name=$2

    info "Checking port $port ($port_name)..."

    # Find process using the port
    local pid=$(lsof -ti:$port 2>/dev/null || true)

    if [ -n "$pid" ]; then
        warning "Port $port is in use by process $pid"
        log "Attempting to identify process..."
        ps -p $pid -o comm= 2>/dev/null || echo "Unknown process"

        log "Killing process $pid to free port $port..."
        kill -9 $pid 2>/dev/null || true

        # Wait and verify
        sleep 2

        # Check if port is now free
        local check_pid=$(lsof -ti:$port 2>/dev/null || true)
        if [ -z "$check_pid" ]; then
            log "✓ Port $port successfully freed"
        else
            error "Failed to free port $port. Process $check_pid still using it."
            return 1
        fi
    else
        log "✓ Port $port is available"
    fi

    return 0
}

# Stop all Docker containers for this project
stop_docker_containers() {
    log "Stopping existing Docker containers..."

    # Stop containers gracefully first
    $DOCKER_COMPOSE down --timeout 30 2>/dev/null || true

    # Force remove any containers that might be stuck
    docker ps -a --filter "name=fleet_" --format "{{.ID}}" | xargs -r docker rm -f 2>/dev/null || true

    log "✓ All containers stopped"
}

# Verify critical requirements
verify_requirements() {
    log "Verifying deployment requirements..."

    # Check if lsof is installed (needed for port checking)
    if ! command -v lsof &> /dev/null; then
        warning "lsof not found. Installing..."
        sudo apt-get update && sudo apt-get install -y lsof || {
            error "Failed to install lsof"
            exit 1
        }
    fi

    # Check if .env exists
    if [ ! -f .env ]; then
        error ".env file not found! Deployment aborted."
        exit 1
    fi

    log "✓ All requirements verified"
}

# Main deployment function
deploy() {
    log "========================================="
    log "Fleet Management Platform - Deploy"
    log "========================================="
    log "Using Docker Compose command: $DOCKER_COMPOSE"
    log "Target API Port: $API_PORT (FIXED)"
    log "========================================="

    # Navigate to project directory
    cd "$PROJECT_DIR" || {
        error "Failed to navigate to $PROJECT_DIR"
        exit 1
    }

    # Verify requirements
    verify_requirements

    # NOTE: Git pull is now handled by Lambda function before executing this script
    log "Code pull handled by Lambda deployment trigger"

    # Stop all existing containers FIRST
    stop_docker_containers

    # Force free the critical API port 8000
    log "Ensuring port $API_PORT is available for API..."
    force_free_port $API_PORT "API" || {
        error "Cannot proceed - port $API_PORT must be available"
        exit 1
    }

    # Verify docker-compose.yml has correct port configuration
    log "Verifying docker-compose.yml configuration..."
    if ! grep -q '"8000:8000"' docker-compose.yml; then
        error "docker-compose.yml does not have correct port mapping (8000:8000)"
        error "Please fix docker-compose.yml before deploying"
        exit 1
    fi
    log "✓ docker-compose.yml port configuration verified"

    # Build new images
    log "Building Docker images..."
    $DOCKER_COMPOSE build --no-cache || {
        error "Docker build failed"
        exit 1
    }

    # Start containers
    log "Starting containers..."
    $DOCKER_COMPOSE up -d || {
        error "Failed to start containers"
        exit 1
    }

    # Wait for API to be ready
    log "Waiting for API to be ready on port $API_PORT..."
    MAX_RETRIES=30
    RETRY_COUNT=0

    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if curl -sf http://localhost:$API_PORT/health > /dev/null 2>&1; then
            log "✓ API health check passed"
            break
        fi

        RETRY_COUNT=$((RETRY_COUNT + 1))
        info "Waiting for API... ($RETRY_COUNT/$MAX_RETRIES)"
        sleep 2
    done

    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        error "API health check failed after $MAX_RETRIES attempts"

        # Show container logs for debugging
        log "Container logs:"
        $DOCKER_COMPOSE logs --tail=50 api

        # Rollback
        log "Rolling back deployment..."
        $DOCKER_COMPOSE down

        exit 1
    fi

    # Verify API is actually on port 8000
    log "Verifying API is running on correct port..."
    API_RESPONSE=$(curl -s http://localhost:$API_PORT/ | grep -o '"version"' || true)
    if [ -n "$API_RESPONSE" ]; then
        log "✓ API responding correctly on port $API_PORT"
    else
        warning "API might not be responding as expected"
    fi

    # Clean up old Docker images
    log "Cleaning up old Docker images..."
    docker image prune -af --filter "until=72h" 2>/dev/null || {
        warning "Failed to clean up old images"
    }

    # Display final status
    log "========================================="
    log "✓ Deployment completed successfully!"
    log "========================================="
    log "API Port: $API_PORT (http://localhost:$API_PORT)"
    log "Redis Port: $REDIS_EXTERNAL_PORT (external) -> $REDIS_INTERNAL_PORT (internal)"
    log "Prometheus Port: $PROMETHEUS_EXTERNAL_PORT (external) -> $PROMETHEUS_INTERNAL_PORT (internal)"
    log "========================================="

    # Show running containers
    log "Running containers:"
    docker ps --filter "name=fleet_" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

    log "========================================="
    log "Health check: http://localhost:$API_PORT/health"
    log "API docs: http://localhost:$API_PORT/docs"
    log "========================================="
}

# Execute deployment
deploy
