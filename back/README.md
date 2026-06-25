# rapidboxes (backend)

Modern, single-process controller for the RaPiD-boxes plant live-imaging chamber.
FastAPI + picamera2 + Adafruit NeoPixel-over-SPI + gpiozero/lgpio. Runs fully
**simulated** off-device for development.

## Develop on a laptop (no hardware)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest                       # unit + engine tests
RAPIDBOXES_SIMULATION=1 python -m rapidboxes   # serves API on :8000
```

In simulation mode the camera synthesizes annotated JPEG frames and the LEDs/IR
just log their state, so the entire REST + WebSocket + MJPEG surface works.

## Configuration (env, prefix `RAPIDBOXES_`)

| Var | Default | Meaning |
|-----|---------|---------|
| `SIMULATION` | auto | `1` = simulated hardware; auto-detects real hardware on a Pi |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | uvicorn bind |
| `STORAGE_ROOT` | `~/rapidboxes/experiments` | where images + metadata are written |
| `SETTINGS_PATH` | `~/rapidboxes/settings.json` | persisted device settings |
| `SPA_DIR` | unset | built React bundle to serve at `/` (set in production) |
| `PREVIEW_FPS` | `5` | MJPEG preview rate |

## Layout

- `hardware/` — `base.py` interfaces; `simulation.py`; real `camera.py` (picamera2),
  `leds.py` (neopixel-spi), `ir.py` (gpiozero); `manager.py` serializes all access.
- `engine/` — `scheduler.py` (pure deadline math) + `runner.py` (state machine).
- `api/` — FastAPI routers (experiments, images, settings, system, preview, ws).
- `storage.py`, `models.py`, `config.py`, `main.py`.
