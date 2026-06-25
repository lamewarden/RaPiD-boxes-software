#!/usr/bin/env bash
# Pull the latest code, rebuild the UI + backend deps, and restart the service.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACK_DIR="$REPO_DIR/back"
VENV="$BACK_DIR/.venv"
FRONT_DIR="$(dirname "$(find "$REPO_DIR/front" -maxdepth 3 -name package.json | head -1)")"

if [ -d "$REPO_DIR/.git" ]; then
  git -C "$REPO_DIR" pull --ff-only
fi

"$VENV/bin/pip" install "$BACK_DIR[pi]"
( cd "$FRONT_DIR" && npm install --no-audit --no-fund && npm run build )

sudo systemctl restart rapidboxes.service
echo "Updated and restarted. Logs: journalctl -u rapidboxes -f"
