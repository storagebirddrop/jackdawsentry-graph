# Jackdaw Sentry Graph Frontend

This package is the standalone investigation graph frontend.

## Surface

- React 19 + TypeScript + Vite
- `@xyflow/react` for node/edge interaction
- `elkjs` for layered layout
- Zustand for graph session state

This frontend is the primary graph UI for the canonical `jackdawsentry-graph`
repository.
Do not couple it to the private compliance dashboard.

## Commands

```bash
npm install
npm run lint
npm run build
npm run dev
```

## Runtime Contract

- Auth token comes from `localStorage` and is currently shared with the static login shell.
- Backend contract is `ExpansionResponse v2` via `/api/v1/graph/sessions*`.
- Redirect target for unauthenticated users is `/login`.

## Standalone Validation

Use with the graph-only runtime:

```bash
docker compose -f ../../docker-compose.graph.yml up --build
```

Then visit:

```text
http://localhost:8081/login
http://localhost:8081/app/
```
