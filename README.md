# Jackdaw Sentry Graph

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

## Quick Start

```bash
# create .env with the required database and secret settings
docker compose -f docker-compose.graph.yml up --build
python scripts/split/create_graph_dev_user.py --username analyst --password change-me-now
```

Browse:

```text
http://localhost:8081/login
http://localhost:8081/app/
```

## Development

This repo is the default place for active sprint work on the graph product.

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
