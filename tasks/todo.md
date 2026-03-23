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
- [~] XRP AMM / Cosmos DEX service classifier DEFERRED — XRP Ledger DEX is
      order-book-based (no fixed contract address; AMM pool IDs are dynamic),
      Cosmos Hub has minimal DEX activity (Osmosis is separate chain).
      Requires tx-type detection (AMMSwap tx type on XRP, IBC MsgSwap on
      Osmosis), not address lookup. Tracked for future pass.

## Bridge Log-Decode Resolution Pass [COMPLETE]

Goal:
- resolve the 6 bridge protocols that stay `status=pending` because they require
  an intermediate ID extracted from decoded event logs, not the tx hash alone

Context:
- `BridgeHopCompiler.process_row()` now calls `BridgeTracer.detect_bridge_hop()`
  inline on first encounter and caches results in `bridge_correlations`
- 9 of 15 protocols resolve immediately via tx hash (THORChain, Wormhole,
  Allbridge, Synapse, LI.FI, Squid, Mayan, deBridge, Symbiosis)
- 6 protocols now resolved:
  - Across: log-decode V3FundsDeposited → depositId → status API (completed/pending)
  - Celer: log-decode Send → transferId → POST status API (completed/pending)
  - Stargate: log-decode Swap → LayerZero chainId → dest chain labelled (no dest tx)
  - Rango: txId-based status API (no log decode needed)
  - Relay: originTxHash-based API search (no log decode needed)
  - Chainflip: log-decode SwapNative/SwapToken → dest chain labelled (swap_id unavailable)

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
- [~] Solana/XRP/Cosmos attribution: no seed data; future pass needed
