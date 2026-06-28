#!/usr/bin/env bash
# Launches Chromium in kiosk mode pointing at the local app. Started at login by
# the XDG autostart entry installed by install.sh.
set -u

URL="http://localhost:${RAPIDBOXES_PORT:-8000}"

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
  "--app=$URL"
)

# Prefer Wayland backend when in a Wayland session (Pi OS Bookworm default).
if [ "${WAYLAND_DISPLAY:-}" != "" ] || [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
  FLAGS=(--ozone-platform=wayland "${FLAGS[@]}")
fi

# Chromium is `chromium-browser` on some images, `chromium` on others.
if command -v chromium-browser >/dev/null 2>&1; then
  exec chromium-browser "${FLAGS[@]}"
else
  exec chromium "${FLAGS[@]}"
fi
