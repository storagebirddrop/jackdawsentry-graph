# Jackdaw Sentry Graph

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/branding/jackdaw-sentry/generated/logo-lockup-dark.svg">
  <img src="assets/branding/jackdaw-sentry/generated/logo-lockup-light.svg" alt="Jackdaw Sentry Graph" width="560">
</picture>

Support via Lightning / Nostr:
`npub1p0jkd532p3c0za2s7fugq0tx30xm2e4v03n6udkqze6ercyf5fesgsy9fv@npub.cash`

Jackdaw Sentry Graph is a self-hosted, open-source blockchain investigation tool built to make fund tracing accessible without expensive industry APIs.

Most professional tracing tools require Chainalysis, Elliptic, or similar subscriptions. This project is built for everyone else: hack and scam victims trying to follow their funds, curious amateurs who want to understand what happened to their money, and under-resourced investigators who need a capable tool without the enterprise price tag.

The ingest service is the core of that accessibility — it rehydrates traces using free-tier RPC endpoints so that the graph can expand even when you have no access to industry data feeds.

A public demo will be made available online when possible. That said, performance on a shared instance will always be limited — for serious investigation work, running this locally or self-hosted is strongly encouraged. The self-hosted path is a first-class goal of this project, not an afterthought.

Hard things this tool is specifically designed to handle:
- tracing through smart contracts (EVM DEX swaps, aggregator hops, bridge contracts)
- following cross-chain flows where assets bridge between networks
- UTXO-level tracing on Bitcoin alongside account-model chains in the same session

Current honest limits:
- **Liquid**: peg-in and peg-out events are marked on the graph, but tracing does not continue inside the Liquid network
- **Lightning**: channel open and close events are marked, but in-channel routing is not traceable

## Important Disclaimer

This project is a work in progress. The graph is a starting point for investigation, not a verdict. Results should never be treated as ground truth — address labels can be incomplete, heuristics can misfire, and the underlying data depends on what has been indexed.

Use this tool to build a picture and generate leads, then verify what you find by reading the actual blockchain data. Part of the goal of this project is to encourage users to learn how blockchains and smart contracts work and can be read directly — not to produce a black box that hands you an answer. The more you understand the underlying data, the better you can judge what the graph is telling you.

OFAC sanctions listings and known-address intelligence are being integrated incrementally. When an address matches a sanctions entry or a known entity, that will be surfaced on the graph — but absence of a flag does not mean an address is clean.

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
- bridge hop status polling and calldata-based destination decoding
- on-demand address ingest when expansion hits an empty frontier
- React investigation graph UI
- graph-focused backend/runtime, contracts, and tests

**Supported chains:** Bitcoin, Lightning Network, Ethereum, BSC, Polygon, Arbitrum, Base, Avalanche, Optimism, Starknet, Injective, Solana, Tron, XRP, Cosmos, Sui.

**Node types on the canvas:** `address`, `entity`, `utxo`, `swap_event`, `atomic_swap`, `bridge_hop`, `btc_sidechain_peg`, `lightning_channel_open`, `lightning_channel_close`, `solana_instruction`, `cluster_summary`, `service`.

## Codebase Map

- `src/api/graph_app.py` — standalone FastAPI graph runtime entry point
- `src/api/routers/graph.py` — graph session, expansion, search, trace, and status endpoints
- `src/api/routers/auth.py` — JWT authentication endpoints
- `src/trace_compiler/` — graph expansion contract and chain-aware compilation logic
  - `chains/` — per-chain handlers: Bitcoin, EVM, Solana, Cosmos, Sui, Tron, XRP
  - `bridges/` — bridge hop compilation and calldata-based destination decoding
  - `calldata/` — EVM and Solana instruction decoder (ABI, Anchor discriminator, heuristic scanners)
  - `ingest/` — on-demand live fetch trigger and chain-specific fetchers
  - `services/` — address exposure and service classification
  - `attribution/` — entity enrichment
- `src/collectors/` — live blockchain data collectors (one per chain) with RPC abstraction layer, backfill, and address ingest worker
- `src/tracing/` — bridge protocol registry, cross-chain bridge tracer, bridge log decoder
- `src/services/` — sanctions screening, price oracle, contract info, entity attribution
- `frontend/app/` — React 19 + TypeScript investigation graph (Zustand, XYFlow, ELK layout)
- `frontend/graph-login.html` — static login shell served ahead of the app

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

The standalone local graph stack is graph-only by default:

- it does not start live collector/backfill workers
- `AUTO_BACKFILL_RAW_EVENT_STORE` stays `false` unless you opt into a different runtime
- `Prev` / `Next` only expand activity that already exists in the local event store / graph dataset

### Optional Ingest Runtime

When you want live collectors plus raw-event-store backfill, run the ingest
sidecar overlay alongside the normal graph stack:

```bash
docker compose \
  -f docker-compose.graph.yml \
  -f docker-compose.graph.ingest.yml \
  up -d --build
```

That overlay starts a dedicated `graph-ingest` service. The request-serving
`graph-api` stays lean, while the sidecar owns:

- blockchain collectors
- raw event-store dual-write
- bootstrap backfill into `raw_transactions` / `raw_token_transfers`

The ingest overlay uses sidecar-specific env flags so it can default to live
ingestion even when the base `.env` keeps the request-serving graph API in
request-only mode:

- `GRAPH_INGEST_DUAL_WRITE_RAW_EVENT_STORE`
- `GRAPH_INGEST_AUTO_BACKFILL_RAW_EVENT_STORE`
- `GRAPH_INGEST_BACKFILL_INTERVAL_SECONDS`
- `GRAPH_INGEST_BACKFILL_BLOCK_BATCH_SIZE`
- `GRAPH_INGEST_BACKFILL_CHAINS_PER_CYCLE`
- `GRAPH_INGEST_BACKFILL_BLOCK_TIMEOUT_SECONDS`

You can check whether the ingest sidecar is being observed through the
authenticated status endpoint:

```text
GET /api/v1/status
```

`runtime.ingest.detected=true` means the graph API can currently see collector
metrics in Redis. If it is `false`, you are still in request-only mode.

For local graph exploration, load representative data first:

```bash
python scripts/dev/load_perf_fixture_dataset.py
```

## Recent Improvements

- **Security**: Resolved authentication bypass; added audit logging on sensitive endpoints
- **Ingest**: Solana live fetch with SPL balance-diff parsing and rate-limit resilience; on-demand address trigger when expansion hits an empty frontier
- **Semantic nodes**: EVM swap_event promotion from log-aware decoding; Solana generic DEX swap_event for unregistered programs; bridge calldata destination decoding via EVM ABI and Solana Anchor heuristics
- **Reliability**: Enhanced error handling, stale row reclamation, null-safe JSON field access, robust data validation

See [CHANGELOG.md](CHANGELOG.md) for detailed technical changes and migration notes.

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
- `New Investigation` clears the current graph and returns to the seed-search
  screen without forcing a logout or full browser refresh
- pinned path stories let investigators keep a few narrative arcs visible while
  comparing branches or bridge routes
- compare briefing turns active branch focus into a side-by-side summary of
  visible nodes, bridge hops, paths, and semantic rails
- protocol legend cards now summarize visible swap, bridge, lightning, sidechain,
  service, and Solana rails directly on the canvas
- session briefings turn the current visible investigation lens into a markdown
  artifact you can copy or export alongside the raw session snapshot
- the inspector is the narrative surface for node detail, lineage, branch
  actions, pinned paths, and active investigation context
- empty `Prev` / `Next` expansions now explain that no indexed activity was
  found in the current dataset instead of silently doing nothing

When you add new graph UX, prefer actions that help an analyst answer
"what happened here?" or "how does this branch differ?" over generic dashboard
chrome.

## Near-Term Product Priorities

Remaining work to reach full investigator-grade coverage:

- run address enrichment during session create / expand so newly discovered
  addresses are screened and labeled immediately against sanctions, AML / CFT,
  fraud, and entity datasets
- add a graph-safe enrichment adapter that stamps `risk_score`, `sanctioned`,
  `entity_*`, and fraud labels into the public graph contract
- deepen EVM `swap_event` detection beyond the current tx-leg inference path
  into full log-aware multi-hop routing decoding
- resolve Allbridge Core bridge destinations from on-chain PDA state (currently
  blocked: destination lives in an ephemeral PDA; requires BridgeTracer API)

What is active today:
- bridge hops with protocol classification
- Lightning channel open / close markers (tracing does not continue inside Lightning)
- BTC peg-in / peg-out markers for Liquid (tracing does not continue inside Liquid)
- EVM DEX / aggregator `swap_event` nodes when both asset legs are present
  in the raw event store
- Solana DEX `swap_event` nodes for registered protocols and generic fallback
  for unregistered programs
- Solana bridge instruction decoding via Anchor discriminator + EVM / Tron
  address heuristics
- on-demand address ingest when expansion hits an empty frontier
- DEX / aggregator interactions with incomplete context still fall back to
  generic `service` nodes

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
