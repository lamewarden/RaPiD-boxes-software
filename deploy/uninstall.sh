#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Please run uninstall.sh as root (sudo)."
  exit 1
fi

APP_DIR="${RAPIDBOXES_APP_DIR:-/opt/rapidboxes}"
PURGE="${1:-}"

systemctl disable --now rapidboxes.service 2>/dev/null || true
rm -f /etc/systemd/system/rapidboxes.service
rm -f /etc/default/rapidboxes
systemctl daemon-reload

APP_USER="${RAPIDBOXES_APP_USER:-${SUDO_USER:-pi}}"
APP_HOME="$(getent passwd "${APP_USER}" | cut -d: -f6)"
if [[ -n "${APP_HOME}" && -d "${APP_HOME}" ]]; then
  rm -f "${APP_HOME}/.config/autostart/rapidboxes-kiosk.desktop"
fi

if [[ "${PURGE}" == "--purge" ]]; then
  rm -rf "${APP_DIR}"
fi

echo "RaPiD-boxes service removed."
