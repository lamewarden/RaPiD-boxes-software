# Plant Imaging Controller FAA

Full-stack plant imaging control UI built with React + Vite on the client and Express on the server, with optional Netlify serverless deployment and an additional FastAPI service for program execution demos.

## What This Repository Contains

- A browser UI to configure and run two imaging workflows:
  - Growth program
  - Tropism program
- A Node/Express API integrated into Vite for local development
- A production Node server build that serves the SPA and `/api/*`
- A Netlify function wrapper for the same Express app
- A separate Python FastAPI app under `server/api/` for program-run orchestration demos

## Tech Stack

- Package manager: `pnpm`
- Frontend: React 18, React Router 6, TypeScript, Vite, TailwindCSS 3, Radix UI, Lucide icons
- Backend (Node): Express 5, CORS, Zod-ready shared typings
- Backend (Python, optional): FastAPI + Uvicorn
- Testing: Vitest

## Quick Start

### Prerequisites

- Node.js 22+ recommended
- `pnpm` 10+
- Optional for Python API:
  - Python 3.11
  - `pip` or Conda/Mamba

### Install

```bash
pnpm install
```

### Run App in Development

```bash
pnpm dev
```

Runs Vite on `http://localhost:8080` with the Express app mounted as middleware.  
Client routes and `/api/*` are served on the same port in dev.

### Build + Run Production (Node)

```bash
pnpm build
pnpm start
```

This builds:

- SPA bundle to `dist/spa`
- Server bundle to `dist/server`

Then starts `dist/server/node-build.mjs`, which serves static SPA assets and API endpoints.

### Optional: Run FastAPI Service

```bash
cd server
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000
```

The FastAPI API is then available at `http://localhost:8000/api/*`.

## Scripts

| Command | Description |
|---|---|
| `pnpm dev` | Start Vite dev server with integrated Express middleware |
| `pnpm build` | Build client and server bundles |
| `pnpm build:client` | Build SPA to `dist/spa` |
| `pnpm build:server` | Build Node server to `dist/server` |
| `pnpm start` | Start production Node server bundle |
| `pnpm test` | Run Vitest test suite |
| `pnpm typecheck` | Run TypeScript type checks |
| `pnpm format.fix` | Format repository with Prettier |

## Environment Variables

Defined in `.env`:

- `VITE_PUBLIC_BUILDER_KEY`: public client key placeholder
- `PING_MESSAGE`: response text for `GET /api/ping`

## Application Routes (Frontend)

Defined in `client/App.tsx`.

| Route | Page |
|---|---|
| `/` | Program selection |
| `/growth` | Growth program configuration |
| `/tropism` | Tropism program configuration |
| `/live` | Live camera placeholder view |
| `/progress-growth` | Growth run progress |
| `/progress-tropism` | Tropism run progress |
| `/summary` | End-of-run summary |
| `*` | Not found page |

## Node API Endpoints (Express)

Defined in `server/index.ts` and `server/routes/demo.ts`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/ping` | Ping endpoint using `PING_MESSAGE` |
| `GET` | `/api/demo` | Demo JSON response |

## Python API Endpoints (FastAPI, Optional)

Defined in `server/api/main.py`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/programs` | List demo programs |
| `POST` | `/api/run` | Start a demo run |
| `GET` | `/api/status` | Poll run status |

## Project Structure

```text
client/
  App.tsx
  main.tsx
  global.css
  components/
    TopNav.tsx
    ProgramTabs.tsx
    ParameterControl.tsx
    ui/
  pages/
    Index.tsx
    GrowthProgram.tsx
    TropismProgram.tsx
    CameraLive.tsx
    ProgressGrowth.tsx
    ProgressTropism.tsx
    ExperimentSummary.tsx
    NotFound.tsx

server/
  index.ts
  node-build.ts
  routes/
    demo.ts
  api/
    main.py
  requirements.txt
  environment.yaml

shared/
  api.ts

netlify/
  functions/
    api.ts
```

## Additional Documentation

- `docs/ARCHITECTURE.md`
- `docs/API.md`
- `docs/DEVELOPMENT.md`

