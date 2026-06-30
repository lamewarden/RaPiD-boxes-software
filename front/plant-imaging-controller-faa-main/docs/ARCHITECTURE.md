# Frontend architecture

The UI is a **single-page React app** served by the FastAPI backend in production.

```
Browser (Chromium kiosk)
    │
    ├─ GET /              → index.html + static assets (Vite build)
    ├─ GET /api/*         → FastAPI REST
    ├─ GET /api/preview   → MJPEG live stream
    └─ WS  /api/ws        → experiment status push
```

## Layout

| Path | Component | Role |
|------|-----------|------|
| `client/pages/` | Route screens | Home, program config, progress, gallery, summary |
| `client/components/` | Shared UI | TopNav, settings menus, parameter controls |
| `client/hooks/` | Data hooks | WebSocket status, system info polling |
| `client/lib/api.ts` | API client | Typed fetch wrapper |
| `shared/api.ts` | Contract types | Mirrors backend Pydantic models |

## Dev vs production

- **Dev**: Vite on `:8080` proxies `/api` to `localhost:8000` (`vite.config.ts`).
- **Production**: `deploy/install.sh` runs `npm run build`; FastAPI serves `dist/spa/` at `/`.

## Screen scaling

`AutoScale` wraps the router and scales the fixed 800×452 layout to any touchscreen resolution.
