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

# Idempotent: re-installs autostart entries so installs from before a given
# feature was added (e.g. the idle screen-blank) pick it up without a full
# reinstall. A logout/login (or reboot) is still needed to pick up a new one.
command -v swayidle >/dev/null 2>&1 && command -v wlopm >/dev/null 2>&1 || {
  echo "==> Installing swayidle + wlopm (needed for the idle screen-blank)..."
  sudo apt-get update && sudo apt-get install -y swayidle wlopm
}
install -d "$HOME/.config/autostart"
chmod +x "$REPO_DIR/deploy/idle.sh"
sed "s|@IDLE_SH@|$REPO_DIR/deploy/idle.sh|g" \
    "$REPO_DIR/deploy/rapidboxes-idle.desktop" \
    > "$HOME/.config/autostart/rapidboxes-idle.desktop"

sudo systemctl restart rapidboxes.service

# The kiosk's Chromium tab is a long-lived SPA session -- it does NOT
# reload itself just because the backend restarted, so without this a
# deploy silently ships zero UI changes to the physical screen: found in
# production after the kiosk ran the exact same page for 22+ hours across
# many deploys. kiosk.sh's own watchdog loop (already running under the
# desktop session) relaunches Chromium fresh within seconds whenever it's
# not already running, so killing it here is enough -- no direct
# Chromium/CDP control needed. Same bluntness as restarting the backend
# service above: this does interrupt whatever's currently on the shared
# touchscreen, same as every deploy already does to the backend WS
# connection.
pkill -f "chromium.*--app=http://localhost:${RAPIDBOXES_PORT:-8000}" 2>/dev/null || true

echo "Updated and restarted. Logs: journalctl -u rapidboxes -f"
