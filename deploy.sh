#!/usr/bin/env bash
# deploy.sh — pull latest master and restart services on the DigitalOcean droplet
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh
#
# Assumes:
#   - repo is already cloned on the droplet
#   - Python venv lives at  <repo_root>/backend/venv
#   - Backend runs as a systemd service named  rfq-backend
#   - nginx (or similar) serves frontend/dist as static files

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$REPO_DIR/backend"
FRONTEND_DIR="$REPO_DIR/frontend"
VENV="$BACKEND_DIR/venv"
BACKEND_SERVICE="${BACKEND_SERVICE:-rfq-backend}"   # override: BACKEND_SERVICE=my-svc ./deploy.sh
NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=4096}"

echo "==> [1/5] Pulling latest code from master…"
cd "$REPO_DIR"
git pull origin master

echo "==> [2/5] Installing / upgrading backend Python deps…"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$BACKEND_DIR/requirements.txt"

echo "==> [3/5] Building frontend…"
cd "$FRONTEND_DIR"
npm install --silent
export NODE_OPTIONS
echo "    Using NODE_OPTIONS=$NODE_OPTIONS"
npm run build

echo "==> [4/5] Restarting backend service ($BACKEND_SERVICE)…"
if systemctl is-active --quiet "$BACKEND_SERVICE" 2>/dev/null; then
    systemctl restart "$BACKEND_SERVICE"
    echo "    Service restarted."
else
    echo "    WARNING: systemd service '$BACKEND_SERVICE' not found or not running."
    echo "    Start it manually with:  systemctl start $BACKEND_SERVICE"
    echo "    Or run ad-hoc:           cd $BACKEND_DIR && $VENV/bin/python run.py"
fi

echo "==> [5/5] Done.  Deployment complete."
