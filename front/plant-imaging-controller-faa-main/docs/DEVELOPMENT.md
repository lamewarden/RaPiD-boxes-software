# Frontend development

## Prerequisites

- Node.js 20+
- Running backend on port 8000 (see `back/README.md`)

## Quick start

```bash
npm install
npm run dev
```

The dev server proxies `/api` to `http://localhost:8000`.

## Quality checks

```bash
npm run typecheck   # TypeScript
npm run test        # Vitest
npm run build       # Production bundle
```

CI runs the same checks on every push (see `.github/workflows/ci.yml`).

## Adding a route

1. Create a page in `client/pages/`
2. Register it in `client/App.tsx` **above** the `*` catch-all route
3. Add navigation from an existing screen (usually `TopNav` or a program tab)

## Styling

- Tailwind utility classes with custom `app-*` color tokens in `tailwind.config.ts`
- Fixed 800×452 design canvas, scaled by `AutoScale`
- Touch targets should be at least ~40px for kiosk use
