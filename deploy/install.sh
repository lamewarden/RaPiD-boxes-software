#!/usr/bin/env bash
#
# One-shot, idempotent installer for the RaPiD-boxes controller on Raspberry Pi
# OS (Bookworm), Pi 4 or Pi 5. Run from a normal user (it uses sudo as needed):
#
#     deploy/install.sh
#
# It enables SPI + camera, installs dependencies, builds the UI, and registers a
# systemd service + Chromium kiosk autostart. Re-running it is safe.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACK_DIR="$REPO_DIR/back"
VENV="$BACK_DIR/.venv"
PORT="${RAPIDBOXES_PORT:-8000}"
RUN_USER="${SUDO_USER:-$USER}"
HOME_DIR="$(eval echo "~$RUN_USER")"

# Locate the front-end project (the dir containing package.json under front/).
FRONT_DIR="$(dirname "$(find "$REPO_DIR/front" -maxdepth 3 -name package.json | head -1)")"

echo "==> Repo:   $REPO_DIR"
echo "==> Back:   $BACK_DIR"
echo "==> Front:  $FRONT_DIR"
echo "==> User:   $RUN_USER ($HOME_DIR)"

MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo 'unknown')"
echo "==> Board:  $MODEL"

echo "==> Installing system packages..."
sudo apt-get update
sudo apt-get install -y \
  python3-venv python3-picamera2 python3-lgpio \
  chromium-browser nodejs npm curl

echo "==> Enabling SPI + camera (and pinning core clock on Pi 4)..."
CONFIG=/boot/firmware/config.txt
[ -f "$CONFIG" ] || CONFIG=/boot/config.txt
add_cfg() { grep -qxF "$1" "$CONFIG" || echo "$1" | sudo tee -a "$CONFIG" >/dev/null; }
add_cfg "dtparam=spi=on"
add_cfg "camera_auto_detect=1"
case "$MODEL" in
  *"Pi 4"*)
    # Stabilise SPI clock so NeoPixel-over-SPI timing doesn't drift with CPU scaling.
    add_cfg "core_freq=500"
    add_cfg "core_freq_min=500"
    ;;
esac

echo "==> Adding $RUN_USER to hardware groups..."
sudo usermod -aG spi,gpio,video,render "$RUN_USER" || true

echo "==> Creating Python venv (with system site-packages for picamera2/lgpio)..."
python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install "$BACK_DIR[pi]"

echo "==> Building the UI bundle..."
( cd "$FRONT_DIR" && npm install --no-audit --no-fund && npm run build )

echo "==> Writing /etc/rapidboxes.env..."
sudo tee /etc/rapidboxes.env >/dev/null <<EOF
RAPIDBOXES_SIMULATION=0
RAPIDBOXES_HOST=127.0.0.1
RAPIDBOXES_PORT=$PORT
RAPIDBOXES_SPA_DIR=$FRONT_DIR/dist/spa
RAPIDBOXES_STORAGE_ROOT=$HOME_DIR/rapidboxes/experiments
RAPIDBOXES_SETTINGS_PATH=$HOME_DIR/rapidboxes/settings.json
EOF

echo "==> Installing systemd service..."
sed -e "s|@USER@|$RUN_USER|g" \
    -e "s|@BACK_DIR@|$BACK_DIR|g" \
    -e "s|@VENV@|$VENV|g" \
    "$REPO_DIR/deploy/rapidboxes.service" | sudo tee /etc/systemd/system/rapidboxes.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now rapidboxes.service

echo "==> Installing Chromium kiosk autostart..."
chmod +x "$REPO_DIR/deploy/kiosk.sh"
install -d "$HOME_DIR/.config/autostart"
sed "s|@KIOSK_SH@|$REPO_DIR/deploy/kiosk.sh|g" \
    "$REPO_DIR/deploy/rapidboxes-kiosk.desktop" \
    > "$HOME_DIR/.config/autostart/rapidboxes-kiosk.desktop"

cat <<EOF

==> Done.
    Backend:  systemctl status rapidboxes      (http://localhost:$PORT)
    Logs:     journalctl -u rapidboxes -f
    Reboot to apply SPI/camera/group changes and launch the kiosk:
        sudo reboot
EOF
