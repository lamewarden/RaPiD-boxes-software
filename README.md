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

## Touchscreen power management

`deploy/idle.sh` blanks the touchscreen after 5 minutes with no touch, and
turns it back on the instant it's touched — screen power only, never system
sleep or suspend. The backend and any running experiment keep running exactly
as before either way; only the panel's own power state changes.

Built on `swayidle` (touch-as-activity detection) driving `wlopm` (Wayland
output power) against the DSI panel, both installed by `deploy/install.sh` and
autostarted alongside the kiosk. Change the timeout with the
`RAPIDBOXES_IDLE_TIMEOUT` environment variable (seconds) in
`~/.config/autostart/rapidboxes-idle.desktop`; the output name (default
`DSI-1`) is configurable the same way via `RAPIDBOXES_DISPLAY_OUTPUT` if you're
on a different panel — check yours with `wlr-randr`.

## Software updates & version rollback

The box can update itself over the air from `origin/<RAPIDBOXES_UPDATE_BRANCH>`
(default `main`), git-only and fast-forward-only — it never rewrites history or
force-resets. An update (manual or automatic) is refused outright, cleanly and
without side effects, if the working tree has local changes, if the branch has
diverged such that a fast-forward is impossible, or if an experiment is
currently running/paused/finishing.

**Manual**: Settings → General → **Software Update** card. **Check for
Updates** only fetches and compares — it never touches the working tree.
**Update Now** does the actual `git fetch` + fast-forward merge, then rebuilds
only what changed (`pip install` if anything under `back/` changed, `npm
install && npm run build` if anything under `front/` changed), then prompts
**Restart now?** to apply it.

**Automatic**: `rapidboxes-update.timer` runs the same logic unattended once a
month (3am on the 1st, `Persistent=true` so a box that's off at that moment
catches up on next boot). With no user present to confirm a restart, it
restarts on its own — but only if the pull *and* the rebuild both succeeded;
if the rebuild fails, it deliberately leaves the old, still-consistent build
running rather than restart into mismatched code and dependencies (check
`journalctl -u rapidboxes-update` if that happens).

**Version card**: the same Settings → General panel shows the commit the box
is currently running and how long it's been running it. Every successful
update (manual or automatic) is recorded, so once at least one update has
happened, a **Roll back to `<hash>`** button appears — this checks out the
previous recorded commit (`git checkout --detach`, not `git reset --hard`, so
the tracked branch pointer is left alone) and re-runs the same rebuild step.
Rollback is not permanent protection: if the box still auto-tracks the branch
you rolled back from, the next monthly check (or a manually-pressed "Update
Now") can land back on that same commit once the branch has moved past it
again — the UI flags this when you roll back.

## SSH access

Settings → General also shows what you need to reach the box over SSH: the
account name (the same user `rapidboxes.service` runs as), whether `sshd` is
currently active, the box's LAN IP, and the exact command to run —
`ssh <user>@<ip>`. Handy since the box normally only has a touchscreen
attached, not a keyboard.

## Remote storage sync (CIFS/SMB)

The box can mount an institutional SMB/CIFS share and copy experiment images to
it automatically as they are captured, replacing the legacy hand-run
`sudo mount -t cifs //ds.asuch.cas.cz/ueb/lhr /mnt/Shared -o user=…,pass=…`.

Settings → General → **Remote Sync** card:

- **On/off toggle**: arms syncing for the researcher currently set on the home
  screen.
- **Server / share**: pre-filled with `//ds.asuch.cas.cz/ueb/lhr` and freely
  editable. The value is checked against a strict allowlist
  (`//host/share[/folder]`, letters, digits, dots, hyphens and underscores
  only) before it can reach the mount command; anything else is rejected with
  a clear message.
- **Username** and **Password** for the share account. The password field is
  masked, and when a password is already set the field shows a fixed-width
  placeholder — the UI is never told the real password or its length.
- **Check Connection**: enabled only once a username and password are both
  present. It mounts the share (if it isn't already), proves the destination
  folder is actually writable, and reports the real error text if not —
  "wrong password" and "host unreachable" need different fixes.
- **Sync Entire Folder**: a one-shot bulk copy of *every* local experiment
  belonging to the current researcher, not just newly captured images.
- **Status**: mounted / not mounted / credentials needed, the destination path,
  the last successful sync time, and a count of files still waiting to be
  copied.

**Remote layout** mirrors the legacy convention: the mounted share, then a
subfolder named after the researcher (created if missing), then one folder per
experiment:

```
/mnt/rapidboxes-remote/<researcher>/<YYYY-MM-DD>_<researcher>_<name>/
    dark_00000.jpg
    metadata.json
    <name>.xml
```

Thumbnails are not copied — they are regenerated locally on demand.

**Sync stops** when the toggle is switched off, or when the researcher name
changes (starting an experiment under a different name switches sync off and
says so, rather than quietly writing into someone else's folder).

**The local experiment always wins.** Copying happens on a background queue,
never on the capture path, so a slow, hung or dead share cannot delay the
capture schedule. A failed copy is logged, counted as pending, and retried on
the next capture; it never aborts or errors a running experiment.

### The password is deliberately never written to disk

This is a design decision, not an oversight: the password is held in memory for
the lifetime of the backend process and is written nowhere — not to
`settings.json`, not to `remote_sync.json`, not to logs, and not to any
credentials file that outlives the mount call itself. It is also never returned
by any API endpoint (this box has no authentication and binds `0.0.0.0`, so
anything it serves is readable by anyone on the LAN); the API exposes only a
`passwordSet` boolean.

**The operational consequence: after any restart the password is gone and must
be re-entered.** That includes a reboot, a power blip, and the monthly
`rapidboxes-update.timer` OTA restart. In that state sync does not quietly
pretend to work — the Remote Sync card turns orange and reads **"Inactive —
credentials needed after restart"** until someone re-enters the password and
presses Check Connection. The same tradeoff is stated as helper text next to
the password field and confirmed by a toast when credentials are accepted, so
it is known *before* anyone leaves the box on a long unattended run. Server,
username and the on/off setting all persist normally; only the password does
not.

### What the sudoers entry grants

Mounting needs root, so `deploy/install.sh` installs
`/etc/sudoers.d/rapidboxes` (mode 0440, validated with `visudo -c` **before**
installation — a malformed sudoers file can lock the account out of `sudo`
entirely). It grants the service account exactly two commands and nothing else:

```
Cmnd_Alias RAPIDBOXES_CIFS = \
  /usr/bin/mount -t cifs //* /mnt/rapidboxes-remote -o credentials=/run/rapidboxes-cifs/cred-*\,nosuid\,nodev\,noexec\,uid=1000\,gid=1000\,file_mode=0664\,dir_mode=0775, \
  /usr/bin/umount /mnt/rapidboxes-remote
<user> ALL=(root) NOPASSWD: RAPIDBOXES_CIFS
```

That is: one fixed mount point, one fixed trailing option string, and no
blanket `ALL`. The hardening options (`nosuid,nodev,noexec` and the
unprivileged uid/gid) come last on purpose — mount options are last-one-wins.
`deploy/uninstall.sh` removes the rule, the mount point and the mount.

The password reaches `mount` through a `credentials=` file created 0600 with
`tempfile.mkstemp` in the service's private `/run/rapidboxes-cifs` directory
(systemd `RuntimeDirectory=`, on tmpfs), and that file is unlinked in a
`finally` the instant `mount` returns, success or failure. It is never passed
as `-o pass=…`, because `ps aux` is world-readable. Every subprocess call uses
a fixed argument list; `shell=True` is never used anywhere in the backend, and
a test enforces that.

**Simulation mode** (`RAPIDBOXES_SIMULATION=1`, i.e. laptop development) never
attempts a real mount: the share is emulated by a local directory so the whole
sync path stays exercisable with no CIFS server present.

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
- **Downloads**: opens the downloads menu and lets the user grab a ZIP of their
  own past experiment folders (images + metadata + saved config).
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

### Downloads menu

The downloads menu lists previous experiments from history, filtered to the
ones whose saved `username` matches the current researcher name (the same
name shown on the **User** button, and the same one baked into each
experiment's folder name). This box has no login/auth, so the filter is a
convenience for finding your own runs quickly, not an access-control
boundary — any experiment folder is reachable by anyone on the LAN who knows
its ID.

- Each row shows the experiment name, start date, and image count, plus a
  **Download ZIP** button.
- Tapping **Download ZIP** downloads a `.zip` of that experiment's entire
  folder — every captured image, `metadata.json`, and the saved `.xml`
  protocol config — via `GET /api/experiments/{id}/download`. The browser
  saves it like any other file download; no separate tool (SSH, Samba, etc.)
  is required.
- The **X** button closes the downloads menu.

### Settings menu

The settings menu has two tabs:

- **Camera**: opens the full camera settings panel.
- **General**: system info (hostname, version, disk space), LED strip segment
  editor, IR pin display, remote CIFS sync configuration, software update /
  rollback controls, and SSH access info. See
  [Software updates & version rollback](#software-updates--version-rollback),
  [SSH access](#ssh-access) and
  [Remote storage sync](#remote-storage-sync-cifssmb) below.

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
- **over-the-air self-update** (manual button + monthly unattended timer),
  fast-forward-only and refused cleanly on any risk of data loss
- **one-click rollback** to the previously-running version, with how-long-it-ran
  tracked automatically
- an in-app **SSH access** panel (username, status, IP, ready-to-run command)
- **remote CIFS/SMB sync**: images copied to an institutional share as they are
  captured, plus a one-shot bulk copy of a researcher's whole back catalogue —
  off the capture path, so a dead share can never stall or fail a running
  experiment. The share password is session-only and never written to disk, and
  the UI says so plainly both while it is typed and after a restart clears it.
- a **downloads menu** for grabbing a ZIP of your own experiment files
  (images + metadata + config) straight from the browser, no SSH/Samba needed
