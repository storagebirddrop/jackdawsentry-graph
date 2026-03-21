# Work Queue

Use this file for active graph-product plans, acceptance criteria, and verification steps.

## Script Layout Cleanup [IN PROGRESS]

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

## Investigation Workflow Pass [IN PROGRESS]

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

## Semantic Detection and Enrichment Pass [PLANNED]

Goal:
- turn graph semantics and address intelligence into first-class product
  behavior instead of generic service hits and optional best-effort labels

Acceptance criteria:
- [ ] EVM log decoding emits true `swap_event` nodes with asset-in / asset-out,
      amounts, and protocol context
- [ ] Solana instruction decoding emits real semantic activity nodes instead of
      leaving high-signal program flows as generic transfers
- [ ] bridge hops persist and surface destination chain, destination asset, and
      destination address directly in the public graph contract
- [ ] empty frontier expansions can trigger address-targeted ingest instead of
      staying permanent dead ends
- [ ] newly discovered addresses are enriched during session create / expand
      with `risk_score`, `sanctioned`, `entity_*`, and fraud / abuse labels
- [ ] generic DEX interactions are retired in favor of true `swap_event`
      semantics wherever the chain data supports it
