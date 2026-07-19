#!/usr/bin/env bash
# Launches Chromium in kiosk mode pointing at the local app. Started at login by
# the XDG autostart entry installed by install.sh.
set -u

URL="http://localhost:${RAPIDBOXES_PORT:-8000}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FRONT_DIR="$ROOT_DIR/front/plant-imaging-controller-faa-main"
DIST_INDEX="$FRONT_DIR/dist/spa/index.html"
LOCK_FILE="/tmp/rapidboxes-kiosk.lock"

# Ensure only one kiosk launcher loop runs at a time in the user session.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "kiosk launcher already running"
  exit 0
fi

# Wait for the backend to be ready before opening the browser.
for _ in $(seq 1 60); do
  curl -sf "$URL/api/system" >/dev/null 2>&1 && break
  sleep 1
done

# Disable screen blanking / power management (X11; harmless/no-op under Wayland).
xset s off 2>/dev/null || true
xset -dpms 2>/dev/null || true
xset s noblank 2>/dev/null || true

FLAGS=(
  --kiosk
  --noerrdialogs
  --disable-infobars
  --disable-translate
  --no-first-run
  --check-for-update-interval=31536000
  --overscroll-history-navigation=0
  # Skip the OS keyring (gnome-keyring) for credential storage: this is an
  # autologin kiosk with no real login session to unlock it, so without this
  # flag Chromium pops a "enter keyring password" dialog on every launch.
  --password-store=basic
  # Auto-allow camera/mic permission prompts for the local app (no touch dialog).
  --use-fake-ui-for-media-stream
  --autoplay-policy=no-user-gesture-required
  "--unsafely-treat-insecure-origin-as-secure=$URL"
  "--app=$URL"
)

# Prefer Wayland backend when in a Wayland session (Pi OS Bookworm default).
if [ "${WAYLAND_DISPLAY:-}" != "" ] || [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
  FLAGS=(--ozone-platform=wayland "${FLAGS[@]}")
fi

# Decide which chromium command to use once.
CHROMIUM_CMD="chromium"
if [ -x /usr/lib/chromium/chromium ]; then
  CHROMIUM_CMD="/usr/lib/chromium/chromium"
elif command -v chromium-browser >/dev/null 2>&1; then
  CHROMIUM_CMD="chromium-browser"
fi

needs_spa_build() {
  if [ ! -f "$DIST_INDEX" ]; then
    return 0
  fi
  find "$FRONT_DIR/client" "$FRONT_DIR/shared" \
    "$FRONT_DIR/index.html" "$FRONT_DIR/vite.config.ts" \
    -type f -newer "$DIST_INDEX" 2>/dev/null | head -n 1 | grep -q .
}

maybe_build_spa() {
  if ! command -v npm >/dev/null 2>&1; then
    return
  fi
  if [ ! -d "$FRONT_DIR" ]; then
    return
  fi
  if needs_spa_build; then
    echo "building updated SPA bundle..."
    (cd "$FRONT_DIR" && npm run build)
  fi
}

# Keep kiosk alive: if Chromium exits (crash/close/kill), restart it.
#
# Two guards, both needed to stop this loop running away:
#
#  - Never launch a second Chromium while one is already up. Launching
#    `chromium --app=$URL` against a live instance does NOT start a browser:
#    it asks the running one to open another window and returns immediately.
#    Unguarded, that reads as "Chromium exited", so the loop relaunches, and
#    windows pile up about once a second without limit.
#  - Back off when Chromium exits unusually quickly, so a persistent startup
#    failure retries slowly instead of hammering the machine.
backoff=2
while true; do
  if pgrep -f "chromium.*--app=$URL" >/dev/null 2>&1; then
    sleep 5
    continue
  fi
  maybe_build_spa
  started=$SECONDS
  "$CHROMIUM_CMD" "${FLAGS[@]}"
  if [ $((SECONDS - started)) -lt 10 ]; then
    backoff=$((backoff * 2))
    [ "$backoff" -gt 60 ] && backoff=60
  else
    backoff=2
  fi
  sleep "$backoff"
done
