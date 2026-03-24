# Work Queue

Use this file for active graph-product plans, acceptance criteria, and verification steps.

## Script Layout Cleanup [COMPLETE]

Goal:
- remove split-era script naming from the public graph repo and keep only the
  product-facing operational tooling

Acceptance criteria:
- [x] useful scripts moved under `scripts/dev`, `scripts/quality`, or
      `scripts/branding`
- [x] extraction-only scripts removed from the public graph repo
- [x] docs point to the new script locations
- [x] stale split-era wording is removed from product-facing ops files

## Repo Hardening Pass [COMPLETE]

Goal:
- make the public repo read as the canonical graph-product repo, not an
  extraction artifact

Acceptance criteria:
- [x] root guidance files clearly state this repo owns new graph work
- [x] memory file captures the private-repo boundary rule
- [x] stale split-era language is removed from core docs

## Investigation Workflow Pass [COMPLETE]

Goal:
- turn the graph from a generic canvas into an analyst workflow with clear
  route, branch, and path reasoning surfaces

Acceptance criteria:
- [x] bridge intelligence summary is visible on-canvas
- [x] bridge route and protocol focus are investigator actions, not passive
      labels
- [x] branch workspace supports focus and compare flows
- [x] inspector supports pinned path storytelling
- [x] protocol-specific styling and legends cover more than bridge hops
- [x] branch compare includes a visible briefing tied to the current graph lens
- [x] session briefing exports the visible investigation lens as markdown

## Semantic Detection and Enrichment Pass [COMPLETE]

Goal:
- turn graph semantics and address intelligence into first-class product
  behavior instead of generic service hits and optional best-effort labels

Acceptance criteria:
- [x] known EVM DEX / aggregator transactions can emit true `swap_event` nodes
      with asset-in / asset-out, amounts, and protocol context when the raw
      event store has enough tx-leg evidence
- [x] broaden EVM swap detection: V2/V3/V4 Uniswap log ABI decoder +
      dual-write raw_evm_logs pipeline from the EthereumCollector
- [x] Solana instruction decoding emits real swap_event nodes (Raydium,
      Jupiter, Orca, Meteora, Phoenix, OpenBook v2)
- [x] bridge hops persist and surface destination chain, destination asset,
      and destination address via BridgeTracer (ADR-015)
- [x] empty frontier expansions trigger address-targeted ingest via
      address_ingest_queue + AddressIngestWorker (ADR-019)
- [x] newly discovered addresses are enriched during session create / expand
      with risk_score, sanctioned, entity_* labels (ADR-018)
- [x] generic DEX interactions retired in favor of swap_event semantics
      wherever V2/V3/V4 or Solana instruction data supports it

## Multi-Chain Coverage Pass [COMPLETE]

Goal:
- ensure all supported blockchains can be expanded in the investigation graph,
  not just EVM/Bitcoin/Solana

Acceptance criteria:
- [x] Tron chain compiler wired: native TRX + TRC-20 (USDT-Tron priority)
- [x] XRP chain compiler wired: native XRP + IOU token transfers
- [x] Cosmos chain compiler wired: native ATOM + IBC assets
- [x] Sui chain compiler wired: native SUI + Sui tokens
- [x] _GenericTransferChainCompiler base class extracted from EVMChainCompiler
      to avoid 300-line code duplication across chain compilers

## Depth Quality Pass [COMPLETE]

Goal:
- improve semantic quality of graph output for highest-traffic compliance
  chains and reduce the fraction of interactions that fall back to generic
  nodes

Acceptance criteria:
- [x] JustSwap / SunSwap on Tron recognised as DEX contracts (service
      classifier — JustSwap V1, SunSwap V2, SunSwap V3 registered)
- [x] Tron DEX Swap event log dual-write: migration 013 (raw_evm_logs_tron
      partition), TronCollector._extract_dex_logs_tron via gettransactioninfobyid,
      _try_swap_promotion in TronChainCompiler delegates to _maybe_build_swap_event
      (moved to _GenericTransferChainCompiler base for reuse by Tron + EVM)
- [x] AddressIngestWorker handles all chain types — generic collector dispatch
      verified by parametrized tests (tron, xrp, cosmos, sui, partial-failure)
- [x] price oracle wired for TRX, XRP, ATOM, SUI via _native_canonical_asset_id
      in each chain compiler; CoinGecko IDs: tron/ripple/cosmos/sui
- [x] XRP AMM / Cosmos DEX swap detection: migration 014 adds tx_type column;
      XRPL collector stores TransactionType, Cosmos collector stores short
      @type name; _try_tx_type_swap_promotion hook in both compilers promotes
      AMMSwap/OfferCreate and Osmosis MsgSwap*/MsgSplitRoute* to swap_event
      nodes; falls back to labelled dex service node when legs unavailable

## Contract Info & Swap Depth Pass [COMPLETE — 2026-03-24]

Goal:
- fill the remaining semantic quality gaps identified at the close of the
  Depth Quality Pass: contract deployer/creator resolution, broader DEX
  service classifier coverage, Tron swap event address format bug, and
  pre-existing test failures

Acceptance criteria:
- [x] `src/services/contract_info.py` — `get_contract_info(address, chain)`
      resolves whether an address is a smart contract / Solana program;
      Etherscan v2 unified API covers ETH/BSC/Polygon/Arbitrum/Base/
      Avalanche/Optimism; Solana uses `getAccountInfo` + programData fetch
      for upgradeable-loader authority; 7-day Redis TTL for immutable data
- [x] `AddressNodeData` extended: `is_contract`, `deployer`, `deployment_tx`,
      `upgrade_authority`, `deployer_entity` fields
- [x] `enrich_nodes` applies contract info concurrently (asyncio.gather);
      deployer entity resolved via secondary `lookup_addresses_bulk` pass;
      `address_type` flipped to "contract" / "program" on confirmed contracts;
      `redis_client` forwarded at all three compiler call sites
- [x] `graph_dependencies.py` `get_contract_info` stub added
- [x] 13 new tests in `tests/test_services/test_contract_info.py`; 5 new
      enricher tests in `tests/test_trace_compiler/test_address_enrichment.py`
- [x] PancakeSwap V2 renamed (was "pancakeswap"); PancakeSwap V3 registered
      on BSC + ETH (SmartRouter V3 via CREATE2 same address); Camelot V2
      registered on Arbitrum — no new ABI decoder needed (identical Swap
      event sigs as Uniswap V2 / V3)
- [x] `_extract_dex_logs_tron` address format bug fixed: was producing 21-byte
      hex ("41" + raw_addr); now produces canonical 25-byte hex (41 prefix +
      20-byte body + 4-byte double-SHA256 checksum) matching the service
      classifier and `_fetch_dex_swap_log` query format
- [x] End-to-end Tron swap promotion test added: JustSwap USDT→USDC swap
      produces correct `swap_event` node with protocol_id, assets, amounts,
      and swap_input/swap_output edges
- [x] EVM int128 sign-extension fix in test helper `_encode_i128_abi` (was
      zero-padding; now uses `.to_bytes(32, "big", signed=True)`)
- [x] Pre-existing test failures repaired: Sui NameError (COUNTERPARTY2ASH_1
      typo), relay bridge test (live API call made non-deterministic; now
      mocked), price oracle mock pattern (ClientSession used directly, not
      as async context manager)

## Ingest-Pending Auto-Retry Pass [COMPLETE — 2026-03-24]

Goal:
- when expansion returns `ingest_pending=true` (empty frontier + new queue row),
  the frontend should automatically poll and retry the expansion once ingestion
  completes rather than leaving a silent dead end for the investigator

Acceptance criteria:
- [x] `src/trace_compiler/models.py` — `IngestStatusResponse` model added
- [x] `GET /sessions/{session_id}/ingest/status?address=X&chain=Y` endpoint added
      to `src/api/routers/graph.py`; inherits session ownership auth; queries
      `address_ingest_queue` table; returns `not_found` when no row exists
- [x] 8 backend tests in `tests/test_api/test_ingest_status.py` covering
      not_found / pending / running / completed / failed / 503 / 400 / 404
- [x] `frontend/app/src/types/graph.ts` — `ingest_pending?: boolean` added to
      `ExpansionResponseV2`; `IngestStatusResponse` interface added
- [x] `frontend/app/src/api/client.ts` — `getIngestStatus()` function added
- [x] `frontend/app/src/context/IngestPendingContext.tsx` — React context
      (`pendingNodeIds: ReadonlySet<string>`) for nodes with active ingest jobs
- [x] `frontend/app/src/hooks/useIngestPoller.ts` — polls every 5 s, 3-min
      timeout; calls `onComplete` on 'completed', `onTimeout` on 'failed'
      or timeout; treats network errors as transient (keeps polling)
- [x] `frontend/app/src/components/IngestPoller.tsx` — render-null component
      that calls `useIngestPoller`; allows React-idiomatic per-node instances
      from a dynamic list without violating rules-of-hooks
- [x] `InvestigationGraph.tsx` — checks `response.ingest_pending` in
      `handleExpand`; adds to `ingestPendingMap`; renders `<IngestPoller>` per
      pending node; `handleIngestComplete` auto-retriggers the expansion;
      `handleIngestTimeout` shows error notice; `<IngestPendingContext.Provider>`
      wraps the return
- [x] `AddressNode.tsx` — shows "Fetching data…" pulsing banner when
      `isIngestPending=true` is injected into node data
- [x] TypeScript: `npx tsc --noEmit` passes with 0 errors
- [x] 546 unit tests pass (538 prior + 8 new)

## Bridge Log-Decode Resolution Pass [IN PROGRESS]

Goal:
- resolve the 6 bridge protocols that stay `status=pending` because they require
  an intermediate ID extracted from decoded event logs, not the tx hash alone

Context:
- `BridgeHopCompiler.process_row()` now calls `BridgeTracer.detect_bridge_hop()`
  inline on first encounter and caches results in `bridge_correlations`
- 9 of 15 protocols resolve immediately via tx hash (THORChain, Wormhole,
  Allbridge, Synapse, LI.FI, Squid, Mayan, deBridge, Symbiosis)
- 5 protocols fully resolved:
  - Across: log-decode V3FundsDeposited → depositId → status API (completed/pending)
  - Celer: log-decode Send → transferId → POST status API (completed/pending)
  - Stargate: log-decode Swap → LayerZero chainId → dest chain labelled (no dest tx)
  - Rango: txId-based status API (no log decode needed)
  - Relay: originTxHash-based API search (no log decode needed)
- 1 protocol partially resolved:
  - Chainflip: log-decode SwapNative/SwapToken → dest chain labelled (swap_id unavailable)
    swap_id (broker deposit channel ID) is not emitted as an EVM event and
    requires broker API integration to fully resolve; noted for future pass

Acceptance criteria:
- [x] `src/tracing/bridge_log_decoder.py` — fetches tx receipt via aiohttp,
      decodes Across V3FundsDeposited, Celer Send, Stargate Swap, Chainflip
      SwapNative/SwapToken using topic0 signatures computed via pycryptodome keccak256
- [x] extracted IDs written to `bridge_correlations.order_id` (depositId / transferId)
- [x] destination chain populated from decoded events for Stargate and Chainflip
- [x] `BridgeTracer._resolve_liquidity`, `_resolve_burn_release`,
      `_resolve_solver`, `_resolve_native_amm` all dispatch to new resolvers
- [x] `EthereumCollector._extract_dex_logs` extended to also store bridge events
      (Across, Celer, Stargate, Chainflip) in `raw_evm_logs` for future txs
- [~] Chainflip: dest chain labelled but status stays `pending` — swap_id (broker
      deposit channel ID) is not emitted as an EVM event and requires broker API
      integration to fully resolve; noted for future pass

## Attribution & Sanctions Data Pass [COMPLETE — ADR-021]

Goal:
- make address enrichment real: every newly discovered address in a session
  should be screened against a sanctions list and labelled with entity/VASP
  identity where known, using free or open data sources where possible

Acceptance criteria:
- [x] `src/services/sanctions.py` — `screen_address(address, chain)` against
      OFAC SDN public XML; covers ETH/XBT/XMR/LTC/etc. crypto address entries;
      JSON file cache at `/tmp/jackdaw_ofac_cache.json`, refreshed daily;
      ETH addresses matched across all EVM chains
- [x] `src/services/entity_attribution.py` — `lookup_addresses_bulk` backed by
      21-entry hardcoded CEX/VASP seed (Binance ×7, Coinbase ×4, Kraken ×3,
      OKX ×2, Bybit, Lido, RocketPool, Compound V3, MakerDAO) + optional
      Etherscan v2 label lookup when `ETHERSCAN_API_KEY` is set
- [x] `graph_dependencies.py` hooks now resolve to real implementations
      (ImportError stubs only fire when running in private-only mode)
- [x] `InvestigationNode.sanctioned` (top-level) now recognised by frontend:
      `graphVisuals.tsx` `semanticMetaForNode`, `nodeGlyphKind`, `semanticBadges`
      all check `node.sanctioned || address.is_sanctioned`
- [x] `node.entity_category` (enricher-set) now shown as category badge;
      `node.risk_score` (enricher-set) now drives AddressNode risk pill
- [x] sanctioned addresses render with red `#dc2626` accent + Sanctioned badge
      + sanction glyph (existing frontend logic now fires correctly)
- [~] service_classifier lending/staking entries already present (Aave V3);
      Compound/Lido/RocketPool covered via entity_attribution seed instead
- [x] Tron + Bitcoin attribution seeds added: `entity_attribution.py` refactored
      to per-chain `_CHAIN_SEEDS` dispatch; `_SEED_TRON` (Binance ×2, OKX,
      Huobi/HTX ×2, Bybit) and `_SEED_BITCOIN` (Binance cold ×2, Coinbase
      cold, Kraken cold) now active; `_build_seed` lowercases all keys uniformly
- [x] Solana seed added: Binance ×2, Binance.US, Coinbase ×3, Kraken, OKX ×3,
      Bybit (11 entries; verified via Solscan labels)
- [x] XRP seed added: Kraken, Coinbase, Bitstamp (3 entries; verified via
      Bithomp/XRPSCAN labels); inactive Binance address omitted (high-risk flag)
- [~] Cosmos/Sui attribution: JS-gated explorers blocked verification;
      no confirmed addresses added — future pass needed
