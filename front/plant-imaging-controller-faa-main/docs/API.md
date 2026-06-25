# API Documentation

This repository includes two API surfaces:

1. Express API (TypeScript) in `server/`
2. Optional FastAPI service (Python) in `server/api/`

## Express API

Base URL:

- Dev: `http://localhost:8080`
- Production Node: `http://localhost:<PORT>` (default `3000`)
- Netlify: same site origin via serverless function redirect

## `GET /api/ping`

Returns a simple ping message.

Response example:

```json
{
  "message": "ping pong"
}
```

Notes:

- Value comes from `process.env.PING_MESSAGE`, fallback `"ping"`

## `GET /api/demo`

Demo route with typed response (`DemoResponse` in `shared/api.ts`).

Response example:

```json
{
  "message": "Hello from Express server"
}
```

## Errors and Fallbacks (Production Node Runtime)

In `server/node-build.ts`, unknown paths starting with `/api/` return:

```json
{
  "error": "API endpoint not found"
}
```

with HTTP `404`.

## Optional FastAPI Service

Base URL (default): `http://localhost:8000`

## `GET /api/health`

Health endpoint.

Response example:

```json
{
  "ok": true
}
```

## `GET /api/programs`

Returns available demo programs.

Response example:

```json
[
  { "id": "demo_sleep", "name": "Demo: sleep 5s" },
  { "id": "demo_echo", "name": "Demo: echo params" }
]
```

## `POST /api/run`

Starts a demo program if the service is idle.

Request body:

```json
{
  "program_id": "demo_echo",
  "params": { "example": 123 }
}
```

Response (started):

```json
{
  "status": "started",
  "run_id": "uuid",
  "program_id": "demo_echo"
}
```

Response (busy):

```json
{
  "status": "busy",
  "run_id": "uuid",
  "program_id": "demo_sleep"
}
```

Response (unknown program):

```json
{
  "status": "error",
  "message": "Unknown program_id"
}
```

## `GET /api/status`

Polls current process state.

Possible responses:

- idle
- running
- finished (+ `exit_code`)
- error (+ `exit_code`)

## Data Contracts

## TypeScript Shared Contract

`shared/api.ts`:

```ts
export interface DemoResponse {
  message: string;
}
```

Use `@shared/api` on both client and server for type-safe route payloads.

