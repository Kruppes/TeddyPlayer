#!/bin/bash
# Build ToniePlayer with version info
# Usage: ./build.sh

set -e

export GIT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
export BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)

echo "Building ToniePlayer..."
echo "  Commit: $GIT_COMMIT"
echo "  Time:   $BUILD_TIME"

docker compose down 2>/dev/null || true
docker compose up -d --build

echo ""
echo "Build complete! Access at http://localhost:8754"
