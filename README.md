# RaPiD-boxes

Self-assembled plant live-imaging chamber: **Raspberry Pi 5 (or 4) + touchscreen +
SK6812 RGBW NeoPixel strips + IR LED boards + PiNoIR v3 camera**. Captures
controlled-illumination timelapses of plant movement (gravitropism / phototropism).

This is a ground-up rewrite of the original Buster/Pi-3 software
([lamewarden/RaPiD-boxes-software](https://github.com/lamewarden/RaPiD-boxes-software)):
a modern, reliable single-process backend and a touch-screen React UI, targeting
current Raspberry Pi OS (Bookworm).

```
back/    Python FastAPI backend (hardware control + experiment engine + API)
front/   React + Vite + Tailwind touchscreen UI
deploy/  One-shot installer, systemd service, Chromium kiosk autostart
```

## How it runs (one process)

A single Python process (FastAPI via uvicorn, managed by `systemd`) serves the
built React UI **and** the REST API **and** a WebSocket live-status feed **and** an
MJPEG camera preview — all on `localhost`. Chromium runs full-screen in **kiosk**
mode pointing at it, replacing the old Tkinter UI. No Node.js runs on the device
(it's only used to *build* the UI). See `back/README.md` and the project plan for
detail.

## Quick start

### Develop on a laptop (no Raspberry Pi needed)

```bash
# Terminal 1 — backend in simulation mode
cd back
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
RAPIDBOXES_SIMULATION=1 python -m rapidboxes      # :8000

# Terminal 2 — UI dev server (proxies /api to :8000)
cd front/plant-imaging-controller-faa-main
npm install && npm run dev                         # open http://localhost:8080
```

The simulated camera produces annotated frames, so you can drive a whole
experiment (start → live progress → gallery) without hardware.

### Install on a Raspberry Pi (4 or 5, Bookworm)

```bash
git clone <this repo> ~/RapiDBoxes && cd ~/RapiDBoxes
deploy/install.sh
sudo reboot
```

`install.sh` enables SPI + the camera, installs deps, builds the UI, and registers
the `rapidboxes` service + kiosk autostart. After reboot the touchscreen shows the
app. Update later with `deploy/update.sh`; remove with `deploy/uninstall.sh`.

## Hardware wiring notes

- **NeoPixel RGBW data → GPIO10 (SPI0 MOSI).** This is the key change from the
  legacy build: the old PWM/DMA driver on GPIO18 does **not** work on a Pi 5, but
  driving the strip over **SPI works on both Pi 4 and Pi 5**. The installer enables
  SPI and (on Pi 4) pins the core clock so the timing is stable. The strip is
  driven by the Adafruit `neopixel-spi` library (RGBW via `pixel_order`).
- **IR LED boards → GPIO 26 & 23** (BCM), via `gpiozero`/`lgpio` — powered only
  during a capture.
- **Camera:** PiNoIR v3 (Camera Module 3) via `picamera2`.
- Pin counts, strip segments, IR pins, and camera exposure/ISO are configurable at
  runtime (`GET/PUT /api/settings`).

## What an experiment does (Tropism protocol)

Optional white-light pre-illumination → **dark "apical hook"** phase (IR-lit
captures in darkness) → **"bending"** phase (unilateral coloured light between
captures). Imaging cadence, durations, spectrum and intensity are set in the UI.
Images + a structured `metadata.json` are written per experiment and browsable in
the in-app gallery.
