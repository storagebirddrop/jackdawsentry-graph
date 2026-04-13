# Jackdaw Sentry Graph Memory

Read this file before touching graph schema, graph API, trace compiler semantics, or the React graph contract.

## Repo Role

- This is the canonical home for all new graph-product work.
- The private `jackdawsentry` repo may still serve or embed this graph
  temporarily, but it is not the product source of truth.
- Any graph-to-private dependency must be classified and burned down:
  `migrate`, `duplicate-minimal`, or `private-adapter`.

## Current Shipped State

As of 2026-04-13, current main is the source of truth. Compare all new
work against main, not older recovery branches.

Active shipped graph path on main:
- direct expand
- asset-aware expand for supported non-Bitcoin address flows
  (explicit `All assets` / `Specific assets` modes, specific-assets multi-select,
  stored per-node scope reused by Prev/Next)
- edge selective trace: tx_hash-first, asset-scoped only when safe
  chain-local identity exists; derives at most one safe selector and does not
  inherit inspector multi-selection
- preview/apply: inspector "Filter & Preview" panel previews an expansion
  without applying it; `handlePreviewExpand` / `handleApplyPreview` wired
  in `InvestigationGraph`; candidate edge list rendered in `GraphInspectorPanel`
- date-filtered expansion: `time_from` / `time_to` accepted by the expand
  API and applied as Neo4j time predicates in Bitcoin, EVM, and Solana
  chain compilers; covered by `test_neo4j_time_filter.py`
- candidate selection / subset apply: per-edge checkboxes in the preview
  panel; "Apply selected" prunes both edges and reachable nodes before
  committing the delta via `applyExpansionDelta`
- asset-scope persistence v1: manual export/import and backend session restore
  preserve per-node asset scope; backend autosave persists updated workspace
  snapshots; stale revision conflicts pause autosave with an honest notice
  instead of silently overwriting newer saved state; recovery is explicit via
  `Save my version` or `Load saved version`, and autosave stays paused until
  one of those steps succeeds

Current shipped behavior:
- EVM / Solana / TRON asset-specific token filtering requires chain-local identity
- `All assets` emits no `asset_selectors`; `Specific assets` emits deterministic
  plural `asset_selectors`
- empty `Specific assets` disables expand/preview actions until at least one
  asset is checked
- manual export/import, backend restore, and backend autosave all round-trip
  per-node asset scope through authoritative workspace snapshots
- stale snapshot conflicts pause autosave for the current mount instead of
  silently overwriting newer saved workspace state
- the conflict notice offers explicit recovery actions: `Save my version` and
  `Load saved version`
- autosave does not resume automatically after a conflict; it resumes only
  after an explicit recovery step succeeds
- Bitcoin excluded from the asset-selector path
- `value_fiat` canonical for active-path edge fiat handling
- bridge animation follows `bridge_source` / `bridge_dest`
- layout/manual-placement safeguards intact

Current multi-asset boundary:
- multi-asset selection v1 is shipped for inspector expand/preview and node
  quick Prev/Next on supported non-Bitcoin address flows
- asset-scope persistence v1 is shipped for those same flows across manual
  export/import, backend restore, and backend autosave
- edge selective trace remains single-asset scoped

Cleanup status (2026-04-09):
- full follow-up merge queue complete
- recovery worktrees/stashes cleaned up
- `feat/asset-scoped-expansion-clean` audited as fully superseded;
  docs-only mining pass merged as PR #15; branch deleted

Note (2026-04-10): code audit confirmed preview/apply, date-filtered
expansion, and candidate selection were already fully implemented on main.
The prior "not shipped" designation was inaccurate documentation, not a
code reality.

## Active Decisions

### ADR-001
- Neo4j remains the canonical investigation graph.

### ADR-002
- Raw blockchain facts live in PostgreSQL event-store tables.

### ADR-003
- `src/trace_compiler/` is the semantic boundary between raw events and the investigation graph.

### ADR-004
- `ExpansionResponse v2` is the only graph API contract.

### ADR-005
- `frontend/app/` is the primary graph frontend.

### ADR-006
- `jackdawsentry-graph` is the canonical repo for graph feature ownership.

### ADR-007
- Private-repo graph dependencies must be actively migrated out rather than
  accepted as permanent coupling.

### ADR-008
- Branch compare/focus is the primary comparison surface for branch-scoped
  investigation workflow.

### ADR-009
- Analyst storytelling should be derived from backend `path_id` and
  `lineage_id` metadata, not invented as separate client-only identifiers.

### ADR-010
- Pinned path storytelling is an analyst-memory aid. Keep it lightweight,
  derived from current graph state, and explainable from the selected node.

### ADR-011
- Protocol and primitive legends should be derived from shared frontend semantic
  metadata so node accents, legend counts, and inspector focus actions stay in
  sync.

### ADR-012
- Branch compare should summarize the currently visible investigation lens, not
  a hidden global graph state. Compare briefings must respect active route,
  semantic, and pinned-path focus.

### ADR-013
- Session briefings should export the visible graph lens as human-readable
  markdown and stay paired with JSON snapshots rather than replacing them.

### ADR-014
- The standalone local graph compose profile is graph-only. It does not imply
  live collectors or backfill are running, so empty `Prev` / `Next` expansions
  must be surfaced honestly as "no indexed activity in the current dataset."

### ADR-015
- Investigators need a first-class way to abandon the current graph and start a
  fresh search without logging out. `New Investigation` resets the canvas back
  to `SessionStarter` while keeping the authenticated browser session alive.

### ADR-016
- Live collectors and raw-event-store backfill must run as a separate ingest
  runtime, not inside the request-serving graph API. The default compose stack
  stays request-only; `docker-compose.graph.ingest.yml` is the opt-in sidecar
  overlay for ingestion.
- Any review, test, or operational handling of `docker-compose.graph.yml` must
  account for `docker-compose.graph.ingest.yml` as the paired ingest overlay.
  The base compose file alone is not the full graph-runtime posture whenever
  ingest behavior, collector availability, or backfill expectations matter.

### ADR-017
- Semantic activity detection is a near-term product priority. The public graph
  must graduate from generic service hits to first-class semantic nodes when
  chain data can support it:
  - decode EVM logs into real `swap_event` nodes
  - decode Solana instructions into real `swap_event` / instruction semantics
  - keep bridge hops rich enough to surface destination chain, destination
    asset, and destination address directly in the graph contract

### ADR-018
- Address intelligence must run in the session pipeline, not as an optional
  afterthought. Newly discovered addresses should be screened and labeled
  during session create/expand with graph-safe enrichment that can populate:
  `risk_score`, `sanctioned`, `entity_*`, and fraud / abuse labels.

### ADR-019
- Empty graph frontiers should not remain dead ends when ingestion can help.
  On-demand ingest fires immediately in standalone mode: `trigger.py` queues
  `address_ingest_queue` AND fires `fetch_evm_address_history` / `fetch_solana_address_history`
  as background coroutines so data arrives within seconds, not on the next worker poll.
  The request-serving graph API remains isolated — live fetchers run via `asyncio.ensure_future`.

### ADR-020
- EVM `swap_event` generation is now active for known DEX and aggregator
  transactions when the raw event store can justify both sides of the asset
  transformation from native-value legs and persisted ERC-20 transfers.
  Keep the logic honest: when the transaction context is incomplete, fall back
  to a generic `service` node rather than inventing swap semantics.

### ADR-024 (COMPLETE — Ingest-Pending Auto-Retry)
- `GET /sessions/{session_id}/ingest/status?address=X&chain=Y` added to graph router.
  Queries `address_ingest_queue` directly; inherits session ownership auth.
  Returns `IngestStatusResponse` with `status` ∈ {pending, running, completed, failed, not_found}.
- Frontend polling loop: `useIngestPoller` (5 s interval, 3-min timeout) via render-null
  `<IngestPoller>` component. `IngestPendingContext` passes pending node set to `AddressNode`.
- `handleExpand` now checks `response.ingest_pending`; adds to `ingestPendingMap` and stores
  retry payload in `ingestRetryRef`. `handleIngestComplete` auto-re-calls `handleExpand`.
- `AddressNode` renders a pulsing "Fetching data…" banner when `isIngestPending=true`.
- 8 new backend tests in `tests/test_api/test_ingest_status.py`. 546 total tests pass.

### ADR-023 (COMPLETE — Contract Info & Swap Depth Pass)
- `src/services/contract_info.py` added: `get_contract_info(address, chain, *, redis_client)`
  resolves EVM contract deployer/tx via Etherscan v2 unified API (chainid param covers
  ETH/BSC/Polygon/Arbitrum/Base/Avalanche/Optimism with one key); Solana detection via
  `getAccountInfo` executable flag + BPF Upgradeable Loader programData fetch for
  upgrade_authority; 7-day Redis TTL (deployment data is immutable).
- `AddressNodeData` extended: `is_contract`, `deployer`, `deployment_tx`,
  `upgrade_authority`, `deployer_entity` — all optional, zero-default.
- `enrich_nodes` (enricher.py) applies contract info concurrently per expansion;
  `address_type` is flipped to "contract"/"program" on confirmed contracts; deployer
  entity names resolved via secondary `lookup_addresses_bulk` pass; `redis_client`
  forwarded at all three compiler call sites in `compiler.py`.
- Service classifier broadened: PancakeSwap V2 (renamed from "pancakeswap"),
  PancakeSwap V3 (BSC + ETH via SmartRouter V3 CREATE2 address), Camelot V2 (Arbitrum).
  No new ABI decoder needed — all use existing Uniswap V2/V3 Swap event sigs.
- `_extract_dex_logs_tron` address format bug fixed: was producing 21-byte hex
  ("41" + raw_addr); now produces canonical 25-byte hex (41 prefix + 20-byte body +
  4-byte double-SHA256 checksum) matching the service classifier and event store.
- End-to-end Tron swap promotion test added: JustSwap USDT→USDC produces correct
  `swap_event` node via patched `_fetch_outbound_event_store` + token transfer legs.

### ADR-022 (COMPLETE — Bridge Log-Decode Resolution Pass)
- `src/tracing/bridge_log_decoder.py` added: fetches tx receipt via aiohttp,
  decodes bridge events using keccak256 topic0 sigs (pycryptodome / eth_hash fallback).
- All 6 previously-pending protocols now resolve in `BridgeTracer`:
  - Across: V3FundsDeposited → depositId → `/deposit/status` API
  - Celer: Send → transferId → POST `/v2/getTransferStatus`
  - Stargate: Swap → LayerZero V1 chainId → dest chain labelled (no REST API)
  - Rango: txId param on `/basic/status` (no log decode)
  - Relay: originTxHash search on `/requests` (no log decode)
  - Chainflip: SwapNative/SwapToken → dest chain labelled; stays `pending` —
    swap_id (broker deposit channel) not emitted in EVM events
- `EthereumCollector._extract_dex_logs` extended: also stores Across/Celer/
  Stargate/Chainflip events in `raw_evm_logs` for future transactions.

### ADR-021 (COMPLETE — Attribution & Sanctions Data Pass)
- `src/services/sanctions.py` implemented: OFAC SDN XML fetch + JSON file cache
  (`/tmp/jackdaw_ofac_cache.json`, 24h TTL). ETH addresses match all EVM chains.
- `src/services/entity_attribution.py` implemented: hardcoded CEX/VASP
  seed (Binance, Coinbase, Kraken, OKX, Bybit, Lido, RocketPool, Compound, Maker)
  + Etherscan v2 label API when `ETHERSCAN_API_KEY` is configured.
  See entity_attribution.py for current seed counts per chain.
- Frontend field-name mismatches fixed in `graphVisuals.tsx`:
  - `node.sanctioned` now checked alongside `address.is_sanctioned`
  - `node.entity_category` now drives category badge (enricher sets top-level)
  - `node.risk_score` now drives risk pill in `AddressNode.tsx`
- Tron seed added: Binance ×2, OKX, Huobi/HTX ×2, Bybit (6 entries)
- Bitcoin seed added: Binance cold ×2, Coinbase cold, Kraken cold (4 entries)
- `entity_attribution.py` refactored to per-chain `_CHAIN_SEEDS` dispatch;
  `_build_seed` lowercases all keys; EVM/Tron/Bitcoin now active
- Solana seed: Binance ×2, Binance.US, Coinbase ×3, Kraken, OKX ×3, Bybit (11 entries; Solscan)
- XRP seed: Kraken, Coinbase, Bitstamp (3 entries; Bithomp/XRPSCAN); inactive Binance omitted
- Remaining gap: Cosmos and Sui (JS-gated explorers blocked address verification)

### ADR-025 (COMPLETE — Solana Live Ingest & Coverage Pass)
- `solana_live_fetch.py` uses sequential `getTransaction` fetching (`_TX_BATCH_SIZE=1`)
  with 12 s 429 back-off (3 retries) to survive public RPC rate limits. Both HTTP 429
  and JSON-RPC error-code 429 are handled.
- Instruction bytes for unrecognised programs stored in `raw_solana_instructions.decoded_args`
  as `{"raw_data": hex}` — NOT `raw_transactions.input_data`, which has a UNIQUE constraint on
  `(blockchain, tx_hash)` and would conflict with real SOL transfer rows.
- `src/trace_compiler/calldata/solana_decoder.py` — heuristic scanner; 12-zero-byte + 20-byte
  EVM address pattern and Tron 0x41 prefix pattern; inline base58 (no external library);
  confidence 0.75 (best-effort, not authoritative).
- `BridgeHopCompiler._calldata_destination` routes Solana to `_calldata_destination_solana`
  before EVM path — avoids `tx_hash.lower()` which corrupts base58 signatures.
- `SolanaChainCompiler._build_graph` generic swap fallback: `elif service_record is None`
  attempts `_maybe_build_solana_swap_event` for any unregistered counterparty that has both
  SPL legs. Dedup via local `generic_swap_seen: set` per `_build_graph` call — not `self`
  (a `self`-level cache would block correct promotion on subsequent expand calls).
- Known limitation: Allbridge encodes the bridge destination in an ephemeral PDA, not in
  instruction bytes — heuristic scanner returns None for it; BridgeTracer API path is primary.
- Known tech debt: `test_on_demand_ingest.py` mock needs updating (3 → 4 `fetchval` calls);
  no tests yet for `solana_decoder`, `_calldata_destination_solana`, or generic swap path.
- Bug-fix patch (2026-03-26):
  - `get_transaction` (`graph.py`): `0x` prefix now gated on `chain in _EVM_CHAINS`; UTXO/Solana
    hashes preserved as bare hex/base58.
  - Migration 015 (`015_raw_transactions_transfer_index.sql`): create `raw_tx_unique_new` first,
    drop `raw_tx_unique`, then rename — no uniqueness window.
  - `live_fetch.py`: `from` field access changed to `(row.get("from") or "").lower() or None`
    to guard against explicit JSON `null`.
  - `solana_live_fetch.py`: added `if not senders: continue` guard in receiver pairing loop
    to prevent `IndexError` on mint-only (airdrop) transfers.

### ADR-026 (COMPLETE — Session Authority Contract)
- Backend-owned `WorkspaceSnapshotV1` rows are the authoritative restore and
  autosave contract for investigation sessions.
- Restore discovery comes from `GET /api/v1/graph/sessions/recent`, not from
  browser-local graph payloads.
- Browser storage may keep a recent-session hint and safe workspace preferences,
  but it must not become the source of truth for restorable graph state.
- Per-node asset scope now rides inside the same authoritative workspace
  snapshot contract for manual export/import, backend restore, and autosave.
- Snapshot writes are monotonic. Stale autosave writes must fail with a
  revision conflict instead of silently overwriting newer workspace state.
- On a stale snapshot conflict, the frontend pauses autosave for the current
  mount and surfaces an honest notice rather than retrying with overwrite
  behavior.
- Conflict recovery is explicit. The shipped UI offers `Save my version` and
  `Load saved version`, and autosave stays paused until one of those recovery
  paths succeeds.

### ADR-027 (COMPLETE — Mounted Bridge Polling Ownership)
- The mounted investigator path owns bridge-hop freshness:
  `InvestigationGraph` + `useBridgeHopPoller` + `GraphInspectorPanel`.
- Detached or unmounted components must not own live polling truth for bridge
  status. Dead polling paths should be removed or explicitly de-scoped.

### ADR-028 (COMPLETE — Empty-State Honesty)
- Empty frontiers must distinguish:
  - no indexed activity in the current dataset
  - indexed activity in the requested direction that produced no new graph
    results for this expansion
  - indexed activity that exists only in the opposite direction
- Event-store directionality takes precedence over live-lookup hints when
  choosing empty-state wording.

## Guardrails

- Do not widen this repo into the private compliance dashboard.
- Do not implement new graph-first features in the private repo and sync them
  back later.
- Keep graph session/state continuity and layout quality high priority.
- Prefer state-of-the-art graph UX only when it keeps the standalone product clearer, not more coupled.
- Branch and path workflow should stay explainable to investigators. Do not add
  clever controls that hide lineage state or make the current focus ambiguous.
- Do not invent swap semantics from thin evidence. Only emit `swap_event`
  when both asset legs can be justified from persisted transaction context;
  otherwise keep the activity as a generic `service` interaction.
- Do not prefix tx hashes with `0x` unconditionally. Only EVM-family chains
  (ethereum, polygon, bsc, arbitrum, base, avalanche, optimism, starknet,
  injective) store hashes with the `0x` prefix. UTXO and Solana hashes are
  bare hex or base58 and must never be prefixed.
- Filter zero-value native transfers (`AND value_native > 0`) in
  `_GenericTransferChainCompiler` queries. Rows with `value_native = 0`
  in `raw_transactions` represent contract calls or pure IOU movements
  where no native asset changed hands; including them produces meaningless
  zero-value edges in the investigation graph.
- Do not treat graph-safe enrichment as optional in the long term. The target
  product behavior is immediate screening and labeling of newly discovered
  addresses within the session flow.

## Security Invariants

- Auth must fail closed. If user lookup or auth backends fail, requests are denied.
- Graph sessions are owner-bound. Missing or non-owned session IDs return `404`.
- Bridge-hop polling is session-scoped. A hop ID is only visible after it was emitted into that session.
- Production defaults keep `/docs`, `/redoc`, `/openapi.json`, and legacy flat graph endpoints disabled.
- Proxy headers are untrusted by default. Only enable proxy trust in controlled deployments.
- Browser bearer tokens may only live in `sessionStorage` during the current wave. Do not reintroduce `localStorage`.
- Expansion guardrails are mandatory: depth <= `3`, `max_results` <= `100`, `page_size` <= `50`.
- Unsupported expansion controls must fail fast rather than silently no-op.
- Security-sensitive backend or nginx changes are not complete until the live stack is rebuilt and `python scripts/quality/live_abuse_probe.py ...` passes against the running deployment.
- Performance claims are not credible without representative graph data. Run `python scripts/quality/live_perf_probe.py ...` and record the dataset footprint before treating local timings as meaningful.
- For local performance work, `python scripts/dev/load_perf_fixture_dataset.py` is the supported way to create a deterministic high-degree plus bridge/cross-chain fixture set without relying on private-repo ingestion.
- Known remaining gaps (deferred, low urgency for MVP):
  - Chainflip full resolution: swap_id (broker deposit channel ID) not emitted
    as an EVM event — requires broker API integration; stays `pending` for now
  - Cosmos / Sui attribution seeds: JS-gated explorers blocked address
    verification; no confirmed addresses added yet
  - Graph session expiry cleanup job: operational hygiene, not a product blocker
