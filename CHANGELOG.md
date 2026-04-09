# Changelog

All notable changes to Jackdaw Sentry Graph will be documented in this file.

## [2026-04-09] - Active Graph Contract Docs Alignment

### 📘 Documentation
- Documented direct expand as the active shipped session graph path
- Clarified asset-aware expand behavior for non-Bitcoin address nodes:
  inspector asset selection, stored per-node `Prev` / `Next` reuse, and
  Bitcoin exclusion from the selector path
- Clarified that edge selective trace is `tx_hash`-first and only asset-scoped
  when safe chain-local identity exists for EVM, Solana, and Tron
- Declared `value_fiat` as the canonical active-path edge fiat field
- Declared bridge animation alignment with backend `bridge_source` /
  `bridge_dest`
- Explicitly noted that preview/apply, date-filter, and candidate-selection
  flows are not part of the current shipped path

## [2026-03-31] - Graph expansion stability, layout, and restore UX

### Frontend
- Incremental node placement and ELK `fixedPositions` keep existing graph nodes pinned during targeted re-layouts.
- Inspector filter state resets when the selected node changes, preventing stale preview inputs and counterpart display glitches.
- Graph store and layout metadata track stable node placement through delta application and restore flows.

### Backend
- Backfill is enabled by default in `.env.example`, with RPC coverage documented for the graph runtime's supported chains.
- Backfill block timeout increased from 30 seconds to 120 seconds to reduce false failures on slow RPC nodes.
- Time-window filtering is wired through the generic transfer, EVM, Solana, and Bitcoin compilers, with targeted Neo4j time-filter coverage.
- `GET /api/v1/graph/sessions/recent` and monotonic autosave conflict protection support backend-owned restore discovery.
- The default graph compose setup binds nginx to `127.0.0.1`.

## [2026-03-28] - Release hardening and investigator-facing safeguards

### Security and runtime
- Auth remains enabled by default; bypass now requires development-mode configuration plus explicit runtime confirmation.
- Public graph surface hardening removed legacy exposure paths, tightened session ownership invariants, and documented operator guidance for local-only deployment.
- Expansion responses now include integrity-warning metadata when fallback graph reads are used.

### Frontend
- Investigator-facing truthfulness states and recovery flows were tightened so fallback behavior is visible instead of silent.

## [2026-03-26] - Solana live ingest and retry coverage

### Ingest
- Solana live fetch uses serial `getTransaction` retrieval with HTTP 429 and JSON-RPC 429 backoff handling.
- Raw Solana instruction storage was rebuilt to match ingest writes and downstream compiler reads.
- On-demand ingest retry flows now track pending address fetches through `address_ingest_queue`.

### Frontend
- `IngestPendingContext`, `useIngestPoller`, and canvas banners surface pending fetch state while background ingest completes.

## [2026-03-23 to 2026-03-24] - Bridge correlation, attribution, and deeper semantics

### Tracing and enrichment
- Bridge deposit log decode and tracer follow-up logic correlate Across, Celer, Stargate, and Chainflip hops from on-chain data.
- Sanctions screening, entity attribution seeds, and contract metadata enrichment were added across the supported graph chains.
- Address nodes now surface sanctions, entity category, deployer, and risk metadata in the investigation graph.

### Semantics
- DEX detection expanded with XRP AMM, Cosmos DEX, Balancer V2, Curve, and Solidly coverage.
- Mixer taint propagation and sanctioned-service semantics are carried through graph expansion results.

## [2026-03-21] - Multi-chain compilers, swap semantics, and on-demand ingest

### Chains
- Added Tron graph expansion support and implemented chain compilers for XRP, Cosmos, and Sui.
- XRP, Cosmos, and Sui compilers are present in the repo but are not yet registered in `TraceCompiler`, so expansion still returns empty results for those chains.
- `_GenericTransferChainCompiler` centralizes shared transfer-expansion behavior across Tron, XRP, Cosmos, and Sui.

### Semantics and runtime
- EVM log decoding and Solana instruction decoding promote swap activity to first-class `swap_event` nodes.
- Tron DEX swap detection now flows from ingest through graph expansion.
- Empty-frontier expansion can trigger background address ingest for supported live-fetch paths.

## [2026-03-20] - Standalone graph runtime release and investigation workflow

### Security and session model
- The standalone graph runtime shipped with owner-bound sessions, expansion guardrails, and backend-controlled restore contracts.
- Auth defaults were hardened for the public graph runtime, and production defaults removed unnecessary docs and flat-route exposure.

### Investigation workflow
- The graph UI gained branch compare, focus workflows, pinned-path storytelling, semantic legends, bridge intelligence, and session briefing support.
- Deterministic graph fixtures and bridge-focused coverage improved release readiness and regression testing.
