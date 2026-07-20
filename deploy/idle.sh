#!/usr/bin/env bash
# Blanks the touchscreen after RAPIDBOXES_IDLE_TIMEOUT seconds of no touch, and
# restores it the instant the screen is touched again. This is screen power
# only -- swayidle's timeout/resume pair drives wlopm to turn the DSI output
# off/on -- it never sleeps or suspends the Pi. The backend and any running
# experiment are completely unaffected either way; only the panel's own power
# state changes. Restarted forever, like kiosk.sh, so a crash just resumes.
set -u

IDLE_TIMEOUT="${RAPIDBOXES_IDLE_TIMEOUT:-300}"   # 5 minutes
OUTPUT="${RAPIDBOXES_DISPLAY_OUTPUT:-DSI-1}"

while true; do
  swayidle -w \
    timeout "$IDLE_TIMEOUT" "wlopm --off $OUTPUT" \
    resume "wlopm --on $OUTPUT"
  sleep 1
done
