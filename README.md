# Jackdaw Sentry Graph

Standalone investigation graph for blockchain tracing and session-based graph exploration.

## Scope

- graph session creation and restore
- graph expansion via `ExpansionResponse v2`
- bridge hop status polling
- React investigation graph UI
- graph-focused backend/runtime, contracts, and tests

This repository is intentionally narrower than the private Jackdaw Sentry platform.

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
