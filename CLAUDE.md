# RaPiD-boxes — project guide (for Claude Code)

Self-assembled plant live-imaging chamber. Captures controlled-illumination
timelapses of plant movement (gravitropism / phototropism) on a Raspberry Pi.
This repo is a **ground-up rewrite** of the legacy Buster/Pi-3 software
(github.com/lamewarden/RaPiD-boxes-software): a reliable single-process backend +
a touchscreen React UI, targeting Raspberry Pi OS **Bookworm** on **Pi 4 or Pi 5**.

## Repository layout
```
back/    Python FastAPI backend — hardware control, experiment engine, API
front/   React + Vite + Tailwind touchscreen UI (nested: front/plant-imaging-controller-faa-main/)
deploy/  install.sh / update.sh / uninstall.sh, systemd unit, Chromium kiosk autostart
```

## Architecture (decided & built)
- **One process**: FastAPI (uvicorn) serves the built React SPA **and** REST **and** a
  WebSocket live-status feed **and** an MJPEG camera preview, all on `localhost`.
  Chromium runs full-screen in **kiosk** mode against it (replaces the old Tkinter UI).
  **No Node.js at runtime** — Node only builds the UI.
- **Hardware behind interfaces** (`back/rapidboxes/hardware/base.py`), chosen by config:
  - Real (Pi): `picamera2`, `gpiozero`+`lgpio` (IR), Adafruit `neopixel-spi` (RGBW LEDs).
  - **Simulation**: synthesizes annotated JPEGs, logs LED/IR — the entire stack runs on a
    laptop with no hardware (`RAPIDBOXES_SIMULATION=1`).
  - `manager.py` serializes all device access (no races) and guarantees lights-off +
    camera-release on stop/crash.
- **Engine** (`back/rapidboxes/engine/`): state machine `idle→running⇄paused→done/error`
  + a monotonic **deadline scheduler** (`scheduler.py`, pure/unit-tested) that fixes the
  legacy negative-sleep interval slip. Atomic `metadata.json`; interrupted-run detection.
- **API contract**: pydantic models in `back/rapidboxes/models.py` are the source of truth,
  mirrored as TS in `front/.../shared/api.ts` — keep the two in sync.

## Experiment = the Tropism protocol
Optional white-light pre-illumination → **dark "apical hook"** (IR-lit captures in
darkness) → **"bending"** (unilateral coloured light between captures; lights off during
each exposure). Cadence/durations/spectrum/intensity set in the UI. Images + metadata are
written per experiment and browsable in the in-app gallery.

## Run it
```bash
# Dev on a laptop (no Pi):
cd back && python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
RAPIDBOXES_SIMULATION=1 python -m rapidboxes            # :8000
cd front/plant-imaging-controller-faa-main && npm install && npm run dev   # :8080 (proxies /api)

# On a Pi (Bookworm, 4 or 5):
deploy/install.sh && sudo reboot
```
Tests: `cd back && pytest`. UI typecheck/build: `npm run typecheck` / `npm run build`.

## Conventions / gotchas
- Backend must stay **Python 3.9-compatible** (laptop dev box is 3.9): use `Optional[...]`,
  not `X | None`, in pydantic models. The Pi runs 3.11.
- `picamera2` + `lgpio` come from **apt**; the venv is created `--system-site-packages`.
- Add new hardware behind the `hardware/base.py` interfaces (+ a simulation impl).

## Wiring change vs the legacy build (important)
- **NeoPixel RGBW data line moves from GPIO18 → GPIO10 (SPI0 MOSI).** The legacy
  rpi_ws281x PWM/DMA path does not work on Pi 5 (RP1); SPI works on **both Pi 4 and Pi 5**.
  The installer enables SPI and (on Pi 4) pins the core clock for stable timing.
  A 3.3V→5V level shifter on the data line is recommended (e.g. 74AHCT125).
- IR LED boards stay on **BCM 26 & 23** (now via gpiozero/lgpio, not RPi.GPIO).
- Camera: PiNoIR v3 (Camera Module 3) on the CSI port, via picamera2.

## Status & next steps
Done + verified in simulation: backend (10 pytest), full single-process integration
(SPA + API + WS + MJPEG), UI wired (Tropism→progress→summary, live preview, gallery,
on-screen keyboard), screen-agnostic `AutoScale`, deploy scripts. Scope = **Tropism only**
(Growth page disabled). Dropped: fisheye correction, auto video assembly.

Open:
1. **Confirm the SPI LED line on real hardware** (Pi 4 available now). Only `hardware/leds.py`
   changes if an external-MCU fallback is needed instead.
2. Optional: a Settings UI page for `GET/PUT /api/settings` (API exists; no page yet).
3. Optional later: re-enable Growth (day/night) program; fisheye/video post-processing.
4. Consider flattening `front/plant-imaging-controller-faa-main/` → `front/`.

Full design rationale: `~/.claude/plans/lets-setup-new-project-stateless-cake.md`.
