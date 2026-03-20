# CLAUDE.md

This repository is the standalone MIT graph product.

Working assumptions:
- Optimize for graph product quality, public readiness, and clean contracts.
- Do not reintroduce compliance dashboard, setup wizard, or private enterprise workflow assumptions.
- `ExpansionResponse v2` is the canonical frontend/backend graph contract.
- The graph runtime is centered on `src.api.graph_app:app`, `docker-compose.graph.yml`, and `frontend/app/`.

Files to read before graph work:
- `tasks/memory.md`
- `docs/split/public-readiness-checklist.md`
- `docs/split/ownership-matrix.md`

Hard rules:
- No new imports from graph-owned modules into private-only modules.
- Keep docs public-safe and contributor-safe.
- Prefer minimal shared primitives over inventing a third shared repo.
