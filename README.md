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

For a clean SD-card install guide from a pristine Raspberry Pi OS image, see
[INSTALL.md](INSTALL.md).

## Hardware wiring map

The software assumes the following **default Raspberry Pi BCM pin mapping**.

| Light source / device | Default connection | Used for | Notes |
| --- | --- | --- | --- |
| RGBW LED strip data input (`DIN`) | **GPIO10** (`SPI0 MOSI`) | All visible white / colour illumination | This is the current supported wiring for both **Pi 4 and Pi 5**. |
| Legacy RGBW data input | **GPIO18** | Old PWM/DMA `rpi_ws281x` path | Legacy only; no longer the recommended wiring, especially on Pi 5. |
| IR LED board 1 control | **GPIO26** | Dark-phase / night capture illumination | Controlled as a simple on/off output. |
| IR LED board 2 control | **GPIO23** | Dark-phase / night capture illumination | Controlled in parallel with GPIO26. |
| Camera | CSI ribbon connector | Image capture + live preview | PiNoIR v3 / Camera Module 3 expected. |

Important hardware notes:

- **RGBW strip data now belongs on GPIO10, not GPIO18.** The rewrite moved the
  strip to SPI because the old PWM/DMA path is not portable to Pi 5.
- The installer enables **SPI** automatically.
- The RGBW data line should be driven through a **3.3V to 5V level shifter**
  when feeding a 5V SK6812 / NeoPixel strip.
- The strip is handled as **one logical RGBW device** in software, but the default
  settings split it into two functional lighting zones:
  - `lateralSegment = (0, 21)` for the side / bending light
  - `topSegment = (22, 64)` for the top day / white light
- Default LED settings also assume `pixelCount = 70` and `pixelOrder = GRBW`.
- Default IR settings use the two legacy control pins `[26, 23]`.
- These defaults are persisted in `settings.json` and exposed by `GET/PUT /api/settings`.

## What the programs do

### Tropism program

The Tropism workflow models the legacy three-stage protocol:

1. dark "apical hook" phase with IR-lit image capture
2. bending phase with **lateral coloured light** between captures

The user configures dark-phase duration, light-phase duration, spectra,
interval between captures, and visible-light intensity.

### Growth program

The Growth workflow runs a repeating **day/night photoperiod**:

1. one baseline photo at the start of the run
2. top-down day lighting for `dayLengthHours`
3. dark / night period for the remainder of the day
4. repeated over `experimentLengthDays`

Growth always uses the **top LED segment** for visible lighting. Night captures
can use either:

- **IR** photo illumination for dark capture
- **RGBW** photo illumination for low visible-light capture

Each experiment writes images plus structured metadata into:

`{storage_root}/{YYYY-MM-DD}_{username}_{experimentName}/`

By default that storage root is:

`~/rapidboxes/experiments`

## UI guide

The touchscreen UI is organized around a persistent top navigation bar and a set
of full-screen task views.

### Home screen

The home screen contains two large program buttons:

- **Growth Program**: opens the Growth configuration screen.
- **Tropism Program**: opens the Tropism configuration screen.

The home screen also shows the **top navigation bar**, which is reused on the
program configuration screens.

### Top navigation bar

From left to right:

- **Close**: calls `/api/system/close-kiosk` and closes the Chromium kiosk window.
  This does **not** stop the backend service.
- **Import**: opens the import menu and lets the user load a previous experiment
  configuration.
- **User**: opens the on-screen keyboard to change the saved researcher name.
- **Gallery**: opens the image gallery.
- **Live**: opens the live camera preview.
  - If the camera is currently missing, the button switches into a re-check action.
  - Tapping it triggers `/api/system/recheck-camera`; if a camera is found, the
    live preview opens.
- **Settings**: opens the settings menu.

### Import menu

The import menu lists previous experiments from history.

- Tapping any row loads the saved phase / light configuration.
- Camera settings from the saved experiment are also pushed back into the current
  device settings.
- Import is **protocol-aware**:
  - a Growth config opens the **Growth** screen
  - a Tropism config opens the **Tropism** screen
- The **X** button closes the import menu without loading anything.

### Settings menu

The settings menu has two tabs:

- **Camera**: opens the full camera settings panel.
- **General**: system info (hostname, version, disk space), LED strip segment editor, and IR pin display.

The **X** button closes the settings menu.

### Camera settings menu

The camera panel is the main operator menu for session camera tuning.

Controls:

- **Resolution**: Full / Half / Quarter sensor modes.
- **Color Mode**: Grayscale or Color capture.
- **JPEG Quality**: compression quality slider.
- **Exposure**: capture exposure time.
- **ISO**: sensor gain.
- **Settle Time**: delay before capture after changing light state.
- **AWB Red Gain** and **AWB Blue Gain**: manual white-balance gains.
- **Test Photo**: captures one preview frame with the currently edited settings.
- **2x**: captures a test photo and opens it zoomed in.
- **Default**: restores the built-in camera defaults in the editor.
- **Save**: writes the current camera settings into the active device settings.

Behavior notes:

- If an experiment is running or paused, camera settings become **read-only**.
- Camera settings reset to the backend defaults at process start; they are
  intentionally **session-scoped**, even if they were saved earlier.

### Program tabs

Both program screens include two large tabs:

- **Growth Program**: switches to the Growth configuration screen.
- **Tropism Program**: switches to the Tropism configuration screen.

These are navigation tabs, not start buttons.

### Growth program screen

The Growth screen configures day/night photoperiod experiments.

Controls:

- **Day Length**: sets the lit portion of the day, in hours.
- **Experiment Length (Days)**: total run length in days.
- **Day Spectrum**: chooses one or more visible spectra (`white`, `red`, `green`, `blue`).
- **Interval Between Images (MIN)**: capture cadence.
- **Light Intensity**: visible day-light intensity.
- **Photo Illumination**:
  - **IR (Dark)** uses the IR boards for night captures.
  - **RGBW (White @10%, Top)** uses the top RGBW segment for night captures.
- **Test Photo**: preview a night capture with the selected illumination source.
- **Start Experiment**: validates camera availability and starts a Growth run.
- **Experiment name** button (tag icon): opens the on-screen keyboard to edit the
  saved experiment name.
- **Reset** button (rotate icon): restores the default Growth settings.

Behavior notes:

- If the camera is disconnected, the start button first tries a camera re-check.
- Imported Growth configs restore both Growth-specific parameters and saved camera settings.

### Tropism program screen

The Tropism screen configures apical-hook / bending experiments.

Controls:

- **Dark Phase** checkbox: enables or disables the dark apical-hook stage.
- **Dark Phase** slider: sets dark-phase duration when enabled.
- **Light Phase Length (h)**: sets the bending / lateral illumination phase duration.
- **Day Spectrum**: chooses one or more visible spectra for the bending light.
- **Interval Between Images (MIN)**: capture cadence.
- **Light Intensity**: visible-light intensity during the bending phase.
- **Start Experiment**: validates camera availability and starts a Tropism run.
- **Experiment name** button (tag icon): opens the on-screen keyboard to edit the
  saved experiment name.
- **Reset** button (rotate icon): restores the default Tropism settings.

Behavior notes:

- Imported Tropism configs restore both Tropism-specific parameters and saved camera settings.
- The Tropism bending phase uses the **lateral LED segment**, not the top segment.

### Live preview screen

The live preview screen shows the backend MJPEG stream from `/api/preview`.

- **Close**: returns to the page the user came from.

This is intended as a framing / sanity-check tool, not a capture workflow by itself.

### Progress screens

Growth and Tropism each have their own progress screen, but the controls behave
the same way.

Displayed information:

- live/reconnecting connection indicator
- progress bar
- last captured image
- elapsed time
- captured image count vs planned image count
- next capture countdown (when a capture is scheduled)
- current phase label
- current day counter on Growth runs

Buttons:

- **Close** in the top bar: returns to the home screen without sending stop.
- **Pause**: pauses the active experiment.
- **Resume**: resumes a paused experiment.
- **Stop**: requests experiment stop and opens the summary screen.
- **Summary**: appears when the run is no longer active and opens the summary screen.

When the backend reports `done`, the UI automatically navigates to the summary screen.

### Measurement finished screen

The finish screen summarizes the just-completed experiment.

Displayed information:

- total elapsed time
- total frames captured
- first frame
- last frame
- final storage path on disk

Button:

- **Close**: requests `/api/system/restart-service`, waits for the backend to come
  back up, and then returns the kiosk to the home screen.

This is intentionally different from the top-nav **Close** button. On the summary
screen, **Close** performs a full backend restart so the kiosk returns to a clean state.

### Gallery screen

The gallery screen lists the current image set returned by `/api/images`.

- **Close**: returns to the home screen.
- Header text: shows the active experiment id and current image count.
- **Thumbnail tap**: opens the full-size image in an overlay.
- The gallery auto-refreshes every 5 seconds.

## Implemented operator-facing features in this rewrite

Compared with the old single-purpose UI flow, the current system now includes:

- a dedicated **Growth** program alongside Tropism
- protocol-aware **import of previous experiment configurations**
- a dedicated **camera settings** panel with test-photo support
- a **live preview** page
- an in-app **gallery**
- a **measurement finished** screen with first/last frame preview and storage path
- a restart-based summary close flow that returns the kiosk to the home screen
