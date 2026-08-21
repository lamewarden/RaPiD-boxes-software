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
  chromium nodejs npm curl \
  swayidle wlopm \
  cifs-utils

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

# OTA self-update (Settings -> General "Update" button + the monthly timer)
# tracks whatever branch is actually checked out right now, not a hardcoded
# "main" -- this repo is developed on "v2" while "main" is the older/stable
# branch, and a given box may be deliberately pinned elsewhere. Override
# later by editing RAPIDBOXES_UPDATE_BRANCH in /etc/rapidboxes.env.
UPDATE_BRANCH="$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"

echo "==> Writing /etc/rapidboxes.env..."
sudo tee /etc/rapidboxes.env >/dev/null <<EOF
RAPIDBOXES_SIMULATION=0
RAPIDBOXES_HOST=0.0.0.0
RAPIDBOXES_PORT=$PORT
RAPIDBOXES_SPA_DIR=$FRONT_DIR/dist/spa
RAPIDBOXES_STORAGE_ROOT=$HOME_DIR/rapidboxes/experiments
RAPIDBOXES_SETTINGS_PATH=$HOME_DIR/rapidboxes/settings.json
RAPIDBOXES_UPDATE_BRANCH=$UPDATE_BRANCH
EOF

echo "==> Installing sudoers rule for remote CIFS sync..."
# Remote sync (Settings -> General) mounts an institutional SMB share, which
# needs root. The grant below is deliberately as narrow as it can be made:
#
#   * exactly two commands, mount -t cifs and umount -- never a blanket ALL
#   * a FIXED mount point, so no other path can be mounted or unmounted
#   * a FIXED trailing option string; the only wildcards are the //host/share
#     (validated against a strict allowlist server-side before it can get here)
#     and the random credentials filename inside the service's own private
#     /run directory
#   * the hardening options come LAST in the option string. mount options are
#     last-one-wins, so even if something unexpected slipped in earlier,
#     nosuid/nodev/noexec and the unprivileged uid/gid still take effect.
#
# Commas inside a sudoers command argument must be escaped (an unescaped comma
# would end the command and start a new list entry).
SUDOERS_FILE=/etc/sudoers.d/rapidboxes
MOUNT_BIN="$( [ -x /usr/bin/mount ] && echo /usr/bin/mount || command -v mount )"
UMOUNT_BIN="$( [ -x /usr/bin/umount ] && echo /usr/bin/umount || command -v umount )"
RUN_UID="$(id -u "$RUN_USER")"
RUN_GID="$(id -g "$RUN_USER")"
MOUNT_OPTS="nosuid\,nodev\,noexec\,uid=$RUN_UID\,gid=$RUN_GID\,file_mode=0664\,dir_mode=0775"

sudo install -d -m 0755 /mnt/rapidboxes-remote

SUDOERS_TMP="$(mktemp)"
cat > "$SUDOERS_TMP" <<EOF
# Installed by RaPiD-boxes deploy/install.sh -- remote CIFS sync.
# Scoped to one mount point and one option string; see back/rapidboxes/remote_sync.py.
Cmnd_Alias RAPIDBOXES_CIFS = \\
  $MOUNT_BIN -t cifs //* /mnt/rapidboxes-remote -o credentials=/run/rapidboxes-cifs/cred-*\,$MOUNT_OPTS, \\
  $UMOUNT_BIN /mnt/rapidboxes-remote
$RUN_USER ALL=(root) NOPASSWD: RAPIDBOXES_CIFS
EOF

# NEVER install an unvalidated sudoers file: a syntax error in /etc/sudoers.d
# can lock this account out of sudo entirely.
if sudo visudo -c -f "$SUDOERS_TMP" >/dev/null; then
  sudo install -m 0440 -o root -g root "$SUDOERS_TMP" "$SUDOERS_FILE"
  echo "    installed $SUDOERS_FILE (validated with visudo -c)"
else
  echo "    !! generated sudoers file failed validation -- NOT installed." >&2
  echo "    !! Remote CIFS sync will be unavailable; everything else still works." >&2
  sudo visudo -c -f "$SUDOERS_TMP" >&2 || true
fi
rm -f "$SUDOERS_TMP"

echo "==> Installing systemd service..."
sed -e "s|@USER@|$RUN_USER|g" \
    -e "s|@BACK_DIR@|$BACK_DIR|g" \
    -e "s|@VENV@|$VENV|g" \
    "$REPO_DIR/deploy/rapidboxes.service" | sudo tee /etc/systemd/system/rapidboxes.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now rapidboxes.service

echo "==> Installing monthly OTA self-update timer..."
sed -e "s|@USER@|$RUN_USER|g" \
    -e "s|@BACK_DIR@|$BACK_DIR|g" \
    -e "s|@VENV@|$VENV|g" \
    "$REPO_DIR/deploy/rapidboxes-update.service" | sudo tee /etc/systemd/system/rapidboxes-update.service >/dev/null
sudo cp "$REPO_DIR/deploy/rapidboxes-update.timer" /etc/systemd/system/rapidboxes-update.timer
sudo systemctl daemon-reload
sudo systemctl enable --now rapidboxes-update.timer

echo "==> Installing Chromium kiosk autostart..."
chmod +x "$REPO_DIR/deploy/kiosk.sh"
install -d "$HOME_DIR/.config/autostart"
sed "s|@KIOSK_SH@|$REPO_DIR/deploy/kiosk.sh|g" \
    "$REPO_DIR/deploy/rapidboxes-kiosk.desktop" \
    > "$HOME_DIR/.config/autostart/rapidboxes-kiosk.desktop"

echo "==> Installing idle screen-blank autostart (5min default, RAPIDBOXES_IDLE_TIMEOUT to change)..."
chmod +x "$REPO_DIR/deploy/idle.sh"
sed "s|@IDLE_SH@|$REPO_DIR/deploy/idle.sh|g" \
    "$REPO_DIR/deploy/rapidboxes-idle.desktop" \
    > "$HOME_DIR/.config/autostart/rapidboxes-idle.desktop"

cat <<EOF

==> Done.
    Backend:  systemctl status rapidboxes            (http://localhost:$PORT)
    Logs:     journalctl -u rapidboxes -f
    OTA:      systemctl list-timers rapidboxes-update  (tracks branch: $UPDATE_BRANCH)
              journalctl -u rapidboxes-update -f
    Reboot to apply SPI/camera/group changes and launch the kiosk:
        sudo reboot
EOF
