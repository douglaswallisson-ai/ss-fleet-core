#!/bin/bash
# Port Validation Script
# Ensures API is always running on port 8000

set -e

EXPECTED_PORT=8000
CONTAINER_NAME="fleet_api"

echo "🔍 Validating API port configuration..."

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "❌ ERROR: Container ${CONTAINER_NAME} is not running"
    exit 1
fi

# Extract actual port from docker ps
ACTUAL_PORT=$(docker ps --filter name=${CONTAINER_NAME} --format '{{.Ports}}' | grep -oP '0\.0\.0\.0:\K\d+(?=->8000)')

if [ -z "$ACTUAL_PORT" ]; then
    echo "❌ ERROR: Could not determine API port"
    exit 1
fi

# Validate port
if [ "$ACTUAL_PORT" != "$EXPECTED_PORT" ]; then
    echo "❌ ERROR: API is running on port $ACTUAL_PORT (expected: $EXPECTED_PORT)"
    echo ""
    echo "To fix this issue:"
    echo "  1. Edit docker-compose.yml and ensure ports are set to '8000:8000'"
    echo "  2. Run: docker-compose down && docker-compose up -d"
    echo "  3. Re-run this validation script"
    exit 1
fi

# Test if API is responding
if curl -f -s http://localhost:${EXPECTED_PORT}/ > /dev/null; then
    echo "✅ SUCCESS: API is correctly running on port ${EXPECTED_PORT}"
    echo "   Status: http://localhost:${EXPECTED_PORT}/"
    echo "   Docs:   http://localhost:${EXPECTED_PORT}/docs"
else
    echo "⚠️  WARNING: API is on correct port but not responding"
    echo "   Check logs: docker-compose logs api"
    exit 1
fi
