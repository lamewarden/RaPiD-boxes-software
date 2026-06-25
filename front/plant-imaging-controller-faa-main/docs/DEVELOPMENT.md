# Development Guide

## Setup

## 1) Install Dependencies

```bash
pnpm install
```

## 2) Configure Environment

Edit `.env` as needed:

```env
VITE_PUBLIC_BUILDER_KEY=__BUILDER_PUBLIC_KEY__
PING_MESSAGE="ping pong"
```

## 3) Start Development Server

```bash
pnpm dev
```

Open `http://localhost:8080`.

## Daily Workflow

## Client Changes

- Add pages in `client/pages/`
- Register routes in `client/App.tsx`
- Use shared UI primitives from `client/components/ui/`
- Use app color tokens from `client/global.css` + `tailwind.config.ts`

## API Changes (Express)

1. Add handler in `server/routes/`
2. Register route in `server/index.ts`
3. Add/update shared interfaces in `shared/api.ts` if used by client

## API Changes (FastAPI, optional)

- Edit `server/api/main.py`
- Run locally with Uvicorn for testing

## Build and Release

## Node Production Build

```bash
pnpm build
pnpm start
```

## Netlify Build

Build command in `netlify.toml`:

```bash
npm run build:client
```

Publish directory:

- `dist/spa`

Serverless function directory:

- `netlify/functions`

## Type Checking and Tests

```bash
pnpm typecheck
pnpm test
```

## Formatting

```bash
pnpm format.fix
```

## Troubleshooting

## Port Already in Use

- Vite dev server expects `8080`
- Stop conflicting process or change Vite server port in `vite.config.ts`

## API Not Found in Production

- Ensure route is registered in `createServer()` (`server/index.ts`)
- Ensure path begins with `/api/`

## Netlify API 404

- Verify `[[redirects]]` rule in `netlify.toml`
- Ensure function build includes `netlify/functions/api.ts`

## Python API Missing Dependencies

```bash
cd server
pip install -r requirements.txt
```

or use `environment.yaml` with Conda/Mamba.

## Documentation Update Checklist

When changing behavior, update:

1. `README.md` for top-level flow
2. `docs/ARCHITECTURE.md` for runtime/build model changes
3. `docs/API.md` for endpoint/contract changes
4. `docs/DEVELOPMENT.md` for command/process changes

