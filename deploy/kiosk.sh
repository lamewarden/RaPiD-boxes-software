#!/usr/bin/env bash
set -euo pipefail

URL="${RAPIDBOXES_UI_URL:-http://127.0.0.1:8000/}"

for _ in $(seq 1 90); do
  if curl -fsS "${URL}api/system" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if command -v chromium-browser >/dev/null 2>&1; then
  BROWSER="chromium-browser"
elif command -v chromium >/dev/null 2>&1; then
  BROWSER="chromium"
else
  echo "No Chromium binary found (chromium-browser/chromium)."
  exit 1
fi

exec "${BROWSER}" \
  --kiosk \
  --incognito \
  --noerrdialogs \
  --disable-session-crashed-bubble \
  --disable-infobars \
  --check-for-update-interval=31536000 \
  "${URL}"
