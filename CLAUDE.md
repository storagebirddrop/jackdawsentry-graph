# CLAUDE.md

This repository is the standalone MIT graph product.

Working assumptions:
- This is the canonical home for all new graph-product work.
- Optimize for graph product quality, public readiness, and clean contracts.
- Do not reintroduce compliance dashboard, setup wizard, or private enterprise workflow assumptions.
- `ExpansionResponse v2` is the canonical frontend/backend graph contract.
- The graph runtime is centered on `src.api.graph_app:app`, `docker-compose.graph.yml`, and `frontend/app/`.
- The private `jackdawsentry` repo may still embed this graph temporarily, but it is not the source of truth for graph features.

Files to read before graph work:
- `tasks/memory.md`
- `README.md`
- `SECURITY.md`
- `tasks/lessons.md`

Hard rules:
- New graph features start here, not in the private repo.
- If a graph dependency still lives only in the private repo, either migrate it here, duplicate the minimal safe primitive, or leave only a thin private adapter behind.
- Keep docs public-safe and contributor-safe.
- Prefer minimal shared primitives over inventing a third shared repo.
