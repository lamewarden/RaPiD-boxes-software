#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Please run update.sh as root (sudo)."
  exit 1
fi

APP_DIR="${RAPIDBOXES_APP_DIR:-/opt/rapidboxes}"
BRANCH="${RAPIDBOXES_BRANCH:-main}"
BACK_DIR="${APP_DIR}/back"

if [[ ! -d "${APP_DIR}/.git" ]]; then
  echo "No git repository found at ${APP_DIR}"
  exit 1
fi

git -C "${APP_DIR}" fetch origin
git -C "${APP_DIR}" checkout "${BRANCH}"
git -C "${APP_DIR}" pull --ff-only origin "${BRANCH}"

if [[ -x "${BACK_DIR}/.venv/bin/pip" ]]; then
  "${BACK_DIR}/.venv/bin/pip" install \
    fastapi \
    "uvicorn[standard]" \
    pydantic \
    pydantic-settings \
    pillow \
    python-multipart \
    gpiozero \
    adafruit-circuitpython-neopixel-spi \
    adafruit-blinka
fi

systemctl restart rapidboxes.service
systemctl --no-pager --full status rapidboxes.service | sed -n '1,40p'
