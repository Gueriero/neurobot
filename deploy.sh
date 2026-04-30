#!/bin/bash
set -e

# === Configuration ===
REMOTE_USER="${1:?Usage: ./deploy.sh user@host}"
REMOTE_DIR="/opt/neurobot"

echo "=== Deploying to $REMOTE_USER:$REMOTE_DIR ==="

# Create remote directory
ssh "$REMOTE_USER" "mkdir -p $REMOTE_DIR"

# Copy project files (excluding unnecessary ones)
rsync -avz --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='swagwe_api_wb' \
    --exclude='CONVERSATION.txt' \
    --exclude='README_DEV.md' \
    ./ "$REMOTE_USER:$REMOTE_DIR/"

echo "=== Files uploaded. Starting Docker... ==="

# Install Docker if not present, then start
ssh "$REMOTE_USER" bash -s << 'REMOTE_SCRIPT'
cd /opt/neurobot

# Install Docker if missing
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
fi

# Install docker-compose plugin if missing
if ! docker compose version &> /dev/null; then
    echo "Installing Docker Compose plugin..."
    apt-get update && apt-get install -y docker-compose-plugin
fi

# Stop old containers if running
docker compose down 2>/dev/null || true

# Build and start
docker compose up -d --build

echo ""
echo "=== Status ==="
docker compose ps
echo ""
echo "=== Logs ==="
docker compose logs --tail=20
REMOTE_SCRIPT

echo ""
echo "=== Deploy complete ==="
echo "View logs:  ssh $REMOTE_USER 'cd $REMOTE_DIR && docker compose logs -f app'"
echo "Run now:    ssh $REMOTE_USER 'cd $REMOTE_DIR && docker compose exec app python src/main.py --once'"
