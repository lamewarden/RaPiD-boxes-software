#!/usr/bin/env bash
# Remove the service + kiosk autostart. Leaves captured experiments untouched.
set -euo pipefail

RUN_USER="${SUDO_USER:-$USER}"
HOME_DIR="$(eval echo "~$RUN_USER")"

sudo systemctl disable --now rapidboxes.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/rapidboxes.service /etc/rapidboxes.env
sudo systemctl daemon-reload
rm -f "$HOME_DIR/.config/autostart/rapidboxes-kiosk.desktop"
rm -f "$HOME_DIR/.config/autostart/rapidboxes-idle.desktop"

echo "Removed service + kiosk/idle-screen autostart."
echo "Experiment data under ~/rapidboxes was kept; delete it manually if desired."
