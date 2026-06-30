# RaPiD-boxes Installation Guide

This guide describes how to install the current RaPiD-boxes rewrite onto a
**pristine Raspberry Pi OS Bookworm** system and end up with a working fullscreen
touchscreen kiosk.

It is written for a **Raspberry Pi 4 or Raspberry Pi 5** with a fresh SD card,
new OS image, and no previous RaPiD-boxes setup.

## Target setup

After installation, the Pi should:

- boot into Raspberry Pi OS Desktop
- log in automatically to a normal user session
- start the `rapidboxes` backend as a `systemd` service
- open Chromium in fullscreen kiosk mode at `http://localhost:8000`
- save captured experiments under `~/rapidboxes/experiments`

## What this installer expects

The included installer script `deploy/install.sh` assumes:

- **Raspberry Pi OS Bookworm**
- a **desktop session** is available
- you install from a normal user account with `sudo`
- the Pi has network access during installation
- the repository is cloned locally on the Pi

The installer itself will:

- install backend and frontend dependencies
- enable SPI and camera auto-detection in boot config
- create the Python virtual environment
- build the React frontend bundle
- install `/etc/rapidboxes.env`
- install and start the `rapidboxes.service`
- install Chromium kiosk autostart for the current user

The installer does **not** create the operating system image for you, and it does
not configure desktop autologin. That part must be set in Raspberry Pi OS.

## 1. Hardware required

Minimum hardware:

- Raspberry Pi 4 or Raspberry Pi 5
- official or compatible power supply suitable for the board
- microSD card with Raspberry Pi OS Bookworm
- HDMI monitor or touchscreen during setup
- USB keyboard for first setup, unless you already have remote shell access
- Pi Camera Module 3 NoIR / PiNoIR v3 connected to CSI
- SK6812 RGBW LED strip
- two IR LED boards
- 5V power for the LED hardware as required by your chamber build
- recommended: **3.3V to 5V level shifter** for the RGBW strip data line

## 2. Default wiring used by the software

The software defaults are based on **BCM GPIO numbering**.

### RGBW strip

- RGBW strip `DIN` -> **GPIO10** / **SPI0 MOSI** / physical pin **19**
- RGBW strip power -> external **5V** supply sized for the strip
- RGBW strip ground -> Pi ground and strip power ground must be common

Important:

- The old legacy software used **GPIO18** for LED data.
- This rewrite uses **GPIO10** because SPI-based driving works on both Pi 4 and Pi 5.
- Do not wire the current system to GPIO18 unless you also change the software.

### IR LED boards

- IR board control input 1 -> **GPIO26** / physical pin **37**
- IR board control input 2 -> **GPIO23** / physical pin **16**
- IR board grounds -> common ground with the Pi

### Camera

- PiNoIR camera -> CSI camera connector on the Raspberry Pi

### LED segment meaning in software

The code treats the RGBW strip as one device, but uses two default segments:

- `lateralSegment = (0, 21)` for side / bending light in Tropism
- `topSegment = (22, 64)` for top-down visible light in Growth and white-light use

Default persisted LED settings:

- `pixelCount = 70`
- `pixelOrder = GRBW`
- `spiHz = 6400000`

Default IR settings:

- `pins = [26, 23]`

## 3. Prepare the operating system

Use **Raspberry Pi Imager** to write a fresh Raspberry Pi OS image.

Recommended image:

- **Raspberry Pi OS (64-bit)**
- **Bookworm** generation
- **Desktop** variant, not Lite

Recommended settings in Raspberry Pi Imager before writing:

- set hostname if desired
- create your normal user
- configure Wi-Fi if needed
- configure locale, keyboard, timezone
- optionally enable SSH for remote administration

Why Desktop matters:

- the backend service itself can run headless
- the touchscreen app is launched through a **desktop autostart entry**
- without a desktop session, the kiosk browser will not open automatically

## 4. First boot configuration on a pristine Pi

Boot the Pi into Raspberry Pi OS and log in.

Before installing RaPiD-boxes, configure **desktop autologin** so the kiosk can
launch automatically after boot.

Run:

```bash
sudo raspi-config
```

Set:

1. `System Options` -> `Boot / Auto Login` -> `Desktop Autologin`

Then update the base OS:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```

After reboot, log back in if needed.

## 5. Clone the repository

If `git` is not installed yet:

```bash
sudo apt install -y git
```

Clone the repository into the default path used by this project:

```bash
cd ~
git clone https://github.com/lamewarden/RaPiD-boxes-software.git RapiDBoxes
cd ~/RapiDBoxes
```

If you are installing a specific branch or commit, check it out now before running
the installer.

## 6. Run the installer

From the repo root:

```bash
cd ~/RapiDBoxes
deploy/install.sh
```

Run it as your normal user. The script uses `sudo` internally when needed.

### What `deploy/install.sh` does

The installer performs these steps:

1. installs required Debian packages:
   - `python3-venv`
   - `python3-picamera2`
   - `python3-lgpio`
   - `chromium`
   - `nodejs`
   - `npm`
   - `curl`
2. enables SPI in boot config with `dtparam=spi=on`
3. enables camera auto-detect with `camera_auto_detect=1`
4. on Pi 4 only, pins `core_freq=500` and `core_freq_min=500` for stable SPI LED timing
5. adds the current user to the `spi`, `gpio`, `video`, and `render` groups
6. creates `back/.venv` with `--system-site-packages`
7. installs Python backend dependencies including Pi-only extras
8. runs `npm install` and `npm run build` for the frontend
9. writes `/etc/rapidboxes.env`
10. installs `/etc/systemd/system/rapidboxes.service`
11. enables and starts the backend service
12. installs the Chromium kiosk autostart `.desktop` file into:

```text
~/.config/autostart/rapidboxes-kiosk.desktop
```

## 7. Reboot after installation

The installer finishes by telling you to reboot. Do that.

```bash
sudo reboot
```

This reboot is important because it applies:

- SPI enablement
- camera-related boot config
- new group membership for the logged-in user
- clean service + kiosk startup in the final environment

## 8. Verify that the installation worked

After reboot, the expected behavior is:

1. Raspberry Pi OS logs into the desktop automatically
2. the backend starts in the background
3. Chromium opens in fullscreen kiosk mode
4. the RaPiD-boxes home screen appears

### Backend health check

In a terminal, run:

```bash
systemctl status rapidboxes
```

You should see it as `active (running)`.

To follow logs live:

```bash
journalctl -u rapidboxes -f
```

To test the API directly:

```bash
curl http://localhost:8000/api/system
```

### Kiosk/browser check

If the service is healthy but no fullscreen UI appears, verify that Chromium is running:

```bash
pgrep -a chromium
```

If needed, log out and back into the desktop session once to trigger XDG autostart.

## 9. Where files are installed

Important installed paths:

- repo root: `~/RapiDBoxes`
- backend virtualenv: `~/RapiDBoxes/back/.venv`
- service file: `/etc/systemd/system/rapidboxes.service`
- environment file: `/etc/rapidboxes.env`
- experiment storage: `~/rapidboxes/experiments`
- persisted settings: `~/rapidboxes/settings.json`
- kiosk autostart: `~/.config/autostart/rapidboxes-kiosk.desktop`

## 10. Useful admin commands

### Restart backend service

```bash
sudo systemctl restart rapidboxes
```

### Stop backend service

```bash
sudo systemctl stop rapidboxes
```

### Start backend service

```bash
sudo systemctl start rapidboxes
```

### Tail backend logs

```bash
journalctl -u rapidboxes -f
```

### Update the installation from the repo

```bash
cd ~/RapiDBoxes
deploy/update.sh
```

This pulls the latest code, reinstalls backend dependencies, rebuilds the UI,
and restarts the service.

### Remove the service and kiosk autostart

```bash
cd ~/RapiDBoxes
deploy/uninstall.sh
```

This removes the systemd service and kiosk autostart entry, but leaves
captured experiment data under `~/rapidboxes` intact.

## 11. Troubleshooting

### The kiosk does not start after boot

Check these first:

- Raspberry Pi OS Desktop is installed, not Lite
- desktop autologin is enabled
- the user actually reached a graphical session
- Chromium is installed
- the backend is running on port 8000

Useful commands:

```bash
systemctl status rapidboxes
pgrep -a chromium
journalctl -u rapidboxes -n 50 --no-pager
```

### The service starts but the camera is not detected

Check:

- CSI ribbon orientation and seating
- camera module compatibility
- that you rebooted after installation
- logs from the service startup

Useful commands:

```bash
journalctl -u rapidboxes -n 100 --no-pager
curl http://localhost:8000/api/system
```

The frontend can also re-check camera presence through the **Live** button.

### The RGBW strip does not react

Check:

- `DIN` is actually wired to **GPIO10** and not the legacy GPIO18 pin
- SPI was enabled and the Pi was rebooted
- strip power and ground are correct
- the Pi ground and LED power ground are common
- the strip really uses **GRBW** order, or adjust settings if not

### The IR lights do not switch on

Check:

- control lines are on **GPIO26** and **GPIO23**
- grounds are common
- the IR boards are powered correctly
- the boards are compatible with simple GPIO on/off control

### The service was installed but the browser still shows an old UI build

Rebuild and restart manually:

```bash
cd ~/RapiDBoxes/front/plant-imaging-controller-faa-main
npm install
npm run build
pkill -9 chromium
sudo systemctl restart rapidboxes
```

The kiosk launcher should reopen Chromium automatically.

## 12. Notes for advanced setups

If you need different hardware defaults, they are controlled through device settings
and backend config rather than the installation guide itself.

Common examples:

- changing LED segment boundaries
- changing IR control pins
- changing storage location
- changing the HTTP port
- running in simulation mode on a laptop

The most important defaults are defined in:

- `back/rapidboxes/models.py`
- `back/rapidboxes/config.py`
- `/etc/rapidboxes.env`

## 13. Minimal install summary

If you already know the platform and only need the shortest path:

```bash
sudo raspi-config
# set Desktop Autologin

sudo apt update && sudo apt full-upgrade -y
sudo reboot

sudo apt install -y git
git clone https://github.com/lamewarden/RaPiD-boxes-software.git ~/RapiDBoxes
cd ~/RapiDBoxes
deploy/install.sh
sudo reboot
```