# Jackdaw Sentry Graph

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/branding/jackdaw-sentry/generated/logo-lockup-dark.svg">
  <img src="assets/branding/jackdaw-sentry/generated/logo-lockup-light.svg" alt="Jackdaw Sentry Graph" width="560">
</picture>

Standalone investigation graph for blockchain tracing and session-based graph exploration.

## Repo Posture

This is the canonical home for all new graph-product work.

- Build graph features here first.
- Treat the private `jackdawsentry` repo as a temporary integration host while
  it still serves the graph internally.
- Do not let graph ownership drift back into the private repo.

### Boundary Rule

If graph code depends on something that only exists in the private repo, resolve
it deliberately:

1. move it into this repo if it is graph-owned
2. duplicate a minimal graph-safe primitive if both repos truly need it
3. keep a thin private adapter only when the behavior is genuinely
   proprietary/private-platform specific

This repository is intentionally narrower than the private Jackdaw Sentry platform.

## Scope

- graph session creation and restore
- graph expansion via `ExpansionResponse v2`
- bridge hop status polling
- React investigation graph UI
- graph-focused backend/runtime, contracts, and tests

## Codebase Map

- `src/api/graph_app.py` runs the standalone FastAPI graph runtime
- `src/api/routers/graph.py` exposes graph session, expansion, search, trace,
  and status endpoints
- `src/trace_compiler/` owns the graph expansion contract and chain-aware
  compilation logic
- `frontend/app/` contains the React 19 + TypeScript investigation graph
- `frontend/graph-login.html` is the static login shell served ahead of the app

## Quick Start

```bash
cp .env.example .env
docker compose -f docker-compose.graph.yml up -d --build
python scripts/dev/create_dev_user.py --username analyst --password change-me-now
```

Browse:

```text
http://localhost:8081/login
http://localhost:8081/app/
```

API docs stay disabled by default. Set `EXPOSE_API_DOCS=true` only when you
explicitly need them in a trusted environment.

## Development

This repo is the default place for active sprint work on the graph product.

## Investigation Workflow

The current investigation shell is built around a few deliberate analyst
workflows:

- bridge intelligence cards surface visible protocols, dominant routes, and
  bridge-hop status mix directly on the canvas
- route focus lets investigators narrow the graph to a protocol or route slice
  without losing surrounding context
- branch workspace supports single-branch focus and two-branch compare using the
  backend branch metadata tracked in session state
- the inspector is the narrative surface for node detail, lineage, branch
  actions, and active investigation context

When you add new graph UX, prefer actions that help an analyst answer
"what happened here?" or "how does this branch differ?" over generic dashboard
chrome.

Backend verification:

```bash
pytest tests/test_trace_compiler -q
```

Frontend verification:

```bash
cd frontend/app
npm install
npm run lint
npm run build
```

Repo verification helpers:

```bash
python scripts/quality/boundary_audit.py
python scripts/quality/public_readiness_audit.py
python scripts/quality/live_abuse_probe.py --username analyst --password change-me-now
python scripts/dev/load_perf_fixture_dataset.py
python scripts/quality/live_perf_probe.py --username analyst --password change-me-now
```

`load_perf_fixture_dataset.py` seeds both a dense local hub and a bridge/cross-chain
fixture slice so the live perf probe can exercise bridge-hop rendering and
status polling instead of only same-chain address expansion.

Local graph login:

- username: `analyst`
- password: `change-me-now`

## Support

For usage questions, bug reports, and feature requests, open a
[GitHub issue](https://github.com/storagebirddrop/jackdawsentry-graph/issues)
in this repository.

For maintainer contact or private coordination, use
`jackdawsentry.support@dawgus.com`.

For security issues, do not open a public issue. Follow [SECURITY.md](SECURITY.md).

This support surface covers the standalone graph product only. The private
`jackdawsentry` platform and compliance workflows are out of scope here.

More detail lives in [SUPPORT.md](SUPPORT.md).

## Branding

The canonical brand source and generated favicon/logo pack live under
`assets/branding/jackdaw-sentry/`.

To regenerate the public logo, favicon, app icons, and social preview assets:

```bash
python scripts/branding/generate_brand_assets.py
```

See [assets/branding/jackdaw-sentry/README.md](assets/branding/jackdaw-sentry/README.md)
for the asset inventory and usage notes.

## License

MIT. See [LICENSE](LICENSE).

## Security

Please see [SECURITY.md](SECURITY.md) before reporting vulnerabilities.

## Relationship To The Private Repo

The private `jackdawsentry` repo may temporarily embed or serve this graph while
the boundary is being tightened. That does not change ownership:

- graph product evolution belongs here
- private-platform compliance and enterprise workflows belong there
- graph-to-private dependencies should be migrated out over time
