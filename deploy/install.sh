#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Please run install.sh as root (sudo)."
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

APP_DIR="${APP_DIR:-${REPO_DIR}}"
APP_USER="${APP_USER:-${SUDO_USER:-pi}}"

BACK_DIR="${APP_DIR}/back"
SPA_DIR="${APP_DIR}/front/plant-imaging-controller-faa-main/dist/spa"
ENV_FILE="/etc/default/rapidboxes"
SERVICE_OUT="/etc/systemd/system/rapidboxes.service"

if [[ ! -d "${BACK_DIR}/rapidboxes" ]]; then
  echo "Backend sources were not found in ${BACK_DIR}"
  exit 1
fi

apt-get update
apt-get install -y \
  python3 \
  python3-venv \
  python3-pip \
  python3-picamera2 \
  python3-lgpio \
  git \
  curl

# Optional kiosk dependencies (best effort on non-Pi systems).
apt-get install -y chromium-browser unclutter || true
apt-get install -y chromium || true

if command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_spi 0 || true
  raspi-config nonint do_camera 0 || true
fi

usermod -aG video,gpio,spi,render "${APP_USER}" 2>/dev/null || true

python3 -m venv --system-site-packages "${BACK_DIR}/.venv"
"${BACK_DIR}/.venv/bin/pip" install --upgrade pip
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

cat > "${ENV_FILE}" <<EOF
RAPIDBOXES_HOST=0.0.0.0
RAPIDBOXES_PORT=8000
RAPIDBOXES_SPA_DIR=${SPA_DIR}
RAPIDBOXES_SIMULATION=0
EOF

sed \
  -e "s|__APP_DIR__|${APP_DIR}|g" \
  -e "s|__APP_USER__|${APP_USER}|g" \
  "${SCRIPT_DIR}/rapidboxes.service" > "${SERVICE_OUT}"

systemctl daemon-reload
systemctl enable --now rapidboxes.service

APP_HOME="$(getent passwd "${APP_USER}" | cut -d: -f6)"
if [[ -n "${APP_HOME}" && -d "${APP_HOME}" ]]; then
  mkdir -p "${APP_HOME}/.config/autostart"
  sed -e "s|__APP_DIR__|${APP_DIR}|g" \
    "${SCRIPT_DIR}/rapidboxes-kiosk.desktop" \
    > "${APP_HOME}/.config/autostart/rapidboxes-kiosk.desktop"
  chown "${APP_USER}:${APP_USER}" "${APP_HOME}/.config/autostart/rapidboxes-kiosk.desktop"
fi

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:8000/api/system" >/dev/null 2>&1; then
    echo "RaPiD-boxes is up: http://127.0.0.1:8000/"
    exit 0
  fi
  sleep 1
done

echo "Installation finished, but service health check did not pass."
exit 1
