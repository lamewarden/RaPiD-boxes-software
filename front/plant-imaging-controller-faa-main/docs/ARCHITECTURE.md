# Architecture

## Overview

This repository has three runtime layers:

1. React SPA (`client/`)
2. Express API (`server/index.ts`)
3. Optional FastAPI process (`server/api/main.py`)

In local frontend development, Vite runs on port `8080` and mounts Express as middleware so both SPA and `/api/*` are available from one origin.

## Runtime Modes

## 1) Development Mode

- Entry: `pnpm dev`
- Config: `vite.config.ts`
- Behavior:
  - Vite serves the client
  - `createServer()` from `server/index.ts` is attached to Vite middleware
  - `/api/*` handled by Express in the same process

## 2) Production Node Mode

- Build:
  - `pnpm build:client` -> `dist/spa`
  - `pnpm build:server` -> `dist/server`
- Start:
  - `pnpm start` -> `node dist/server/node-build.mjs`
- Behavior:
  - Express API from `createServer()`
  - Static serving from `dist/spa`
  - Catch-all route serves `index.html` for SPA routes
  - Unknown `/api/*` and `/health` paths return 404 JSON

## 3) Netlify Serverless Mode

- Netlify function entry: `netlify/functions/api.ts`
- Uses `serverless-http` to wrap `createServer()`
- Netlify redirect in `netlify.toml` routes `/api/*` to `/.netlify/functions/api/:splat`
- SPA publish directory: `dist/spa`

## 4) Optional Python API Mode

- Entry: `server/api/main.py`
- Framework: FastAPI
- Purpose:
  - Demo orchestration endpoints for long-running program process simulation
  - Independent from Express runtime

## Client Architecture

## Routing

Defined in `client/App.tsx` using React Router 6:

- `/` `Index`
- `/growth` `GrowthProgram`
- `/tropism` `TropismProgram`
- `/live` `CameraLive`
- `/progress-growth` `ProgressGrowth`
- `/progress-tropism` `ProgressTropism`
- `/summary` `ExperimentSummary`
- `*` `NotFound`

## UI Composition

- `TopNav` provides shared top controls
- `ProgramTabs` toggles between growth and tropism program pages
- `ParameterControl` encapsulates a reusable numeric+slider control pattern
- Tailwind tokens and app theme are defined in `client/global.css` and mapped in `tailwind.config.ts`

## State Model

- Program pages (`GrowthProgram`, `TropismProgram`) hold local form state in React state
- Progress pages hold runtime timer state (`elapsed`, `isPaused`) locally
- Cross-page handoff uses `navigate(..., { state })` for transient data

## Server Architecture

## Express Layer

- Entry factory: `createServer()` in `server/index.ts`
- Middleware:
  - `cors()`
  - JSON body parser
  - URL-encoded parser
- Routes:
  - `GET /api/ping`
  - `GET /api/demo`

## Shared Types

- `shared/api.ts` contains cross-runtime TypeScript interfaces (currently `DemoResponse`)
- Aliases configured in Vite and TS:
  - `@/*` -> `client/*`
  - `@shared/*` -> `shared/*`

## Build Pipeline

## Client Build

- Tool: Vite
- Output: `dist/spa`

## Server Build

- Tool: Vite library build (`vite.config.server.ts`)
- Entry: `server/node-build.ts`
- Output: `dist/server/*.mjs`
- Target: Node 22
- Externalized modules include Node built-ins and `express`, `cors`

## Known Boundaries and Decisions

- The React app currently uses local component state, not a centralized store
- Express endpoints are intentionally minimal in this template
- FastAPI service is separate and does not proxy through Express by default
- Netlify deployment path only wraps Express, not the Python API

