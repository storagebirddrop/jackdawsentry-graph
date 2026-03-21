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

## Depth Quality Pass [IN PROGRESS]

Goal:
- improve semantic quality of graph output for highest-traffic compliance
  chains and reduce the fraction of interactions that fall back to generic
  nodes

Acceptance criteria:
- [ ] JustSwap / SunSwap on Tron recognised as DEX contracts (service
      classifier + TronChainCompiler swap promotion)
- [ ] Tron DEX Swap event log dual-write (raw_evm_logs-equivalent for Tron,
      since Tron EVM uses the same V2 sig)
- [ ] AddressIngestWorker handles all chain types, not just EVM
      (currently tested only for Ethereum collector path)
- [ ] price oracle wired for TRX, XRP, ATOM, SUI native assets so fiat
      value filtering works on non-EVM expansions
- [ ] service classifier extended with high-traffic XRP AMM and Cosmos DEX
      contract equivalents (DEX Aggregator → Osmosis, etc.)
