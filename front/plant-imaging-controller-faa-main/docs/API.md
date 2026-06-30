# API reference (frontend view)

The backend is **FastAPI** (`back/rapidboxes/`). All endpoints are under `/api`.

## Health

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | `{"ok": true, "version": "..."}` |

## Experiments

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/experiments` | Start Tropism or Growth run |
| GET | `/api/experiments/current` | Current status |
| POST | `/api/experiments/current/pause` | Pause |
| POST | `/api/experiments/current/resume` | Resume |
| POST | `/api/experiments/current/stop` | Stop |
| GET | `/api/experiments/history` | Past experiments |
| GET | `/api/experiments/{id}/config` | Saved config XML as JSON |

## Images & preview

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/images` | Latest experiment images |
| GET | `/api/images/{id}/{imageId}` | Full-size JPEG |
| GET | `/api/images/{id}/{imageId}/thumb` | Thumbnail |
| GET | `/api/preview` | MJPEG stream |
| GET | `/api/preview/test-photo?source=ir\|rgbw` | Growth night illumination test |
| POST | `/api/preview/test-photo` | Camera settings test capture |

## Settings & system

| Method | Path | Description |
|--------|------|-------------|
| GET/PUT | `/api/settings` | Camera, LED, IR device settings |
| GET | `/api/system` | Hostname, disk space, camera status |
| POST | `/api/system/recheck-camera` | Hot-plug camera probe |
| POST | `/api/system/close-kiosk` | Close Chromium kiosk |
| POST | `/api/system/restart-service` | Restart backend |

## WebSocket

| Path | Payload |
|------|---------|
| `/api/ws` | `ExperimentStatus` JSON on each state change |

Full model definitions: `shared/api.ts` and `back/rapidboxes/models.py`.
