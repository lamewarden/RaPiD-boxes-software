# RaPiD-boxes UI

Touchscreen React UI for the RaPiD-boxes plant live-imaging chamber.

## Stack

- **React 18** + **Vite** + **Tailwind CSS**
- Talks to the **Python FastAPI** backend at `/api` (same origin in production; proxied in dev)
- **No Node.js at runtime** on the Pi — this app is built to static files and served by FastAPI

## Development

```bash
# Terminal 1 — backend (simulation mode)
cd ../../back
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
RAPIDBOXES_SIMULATION=1 python -m rapidboxes    # :8000

# Terminal 2 — UI dev server
npm install
npm run dev                                      # :8080, proxies /api → :8000
```

Open http://localhost:8080

## Scripts

| Command | Purpose |
|---------|---------|
| `npm run dev` | Vite dev server with API proxy |
| `npm run build` | Production build → `dist/spa/` |
| `npm run typecheck` | TypeScript check |
| `npm run test` | Vitest unit tests |

## API contract

TypeScript types in `shared/api.ts` mirror `back/rapidboxes/models.py`. Keep them in sync when changing the API.

See the root [README.md](../../README.md) for the full operator UI guide and hardware notes.
