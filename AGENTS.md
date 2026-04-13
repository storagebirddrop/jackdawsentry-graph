# AGENTS.md — Shared Agent Instructions

Shared cross-agent instruction file for `jackdawsentry-graph`.
Read this before starting graph-related work.

## Source of Truth

Current `main` is the source of truth.

Compare all new work against `main`, not stale recovery branches, older task memory, or superseded PR branches.

## Active Shipped Graph Path

The currently shipped session graph path includes **direct expand**, **preview/apply**, **date-filtered expansion**, **candidate selection / subset apply**, and **asset-scope persistence**.

Shipped behavior:
- **asset-aware expand** for supported non-Bitcoin address flows
  - inspector-based `All assets` / `Specific assets` selection
  - specific-assets mode supports multi-select with stored per-node scope reused by Prev/Next
  - empty `Specific assets` disables expand/preview actions until at least one asset is checked
  - manual export/import and backend session restore preserve per-node asset scope
  - backend autosave persists updated asset-scope snapshots
  - stale snapshot conflicts pause autosave with an honest notice instead of silently overwriting newer saved state
- **edge selective trace** is `tx_hash`-first and only adds at most one safe asset scope when chain-local identity exists (EVM, Solana, TRON)
  - edge trace does not inherit inspector multi-selection
- **preview/apply**: inspector "Filter & Preview" panel runs an expansion without committing it to the canvas; investigators review candidate edges before applying
- **date-filtered expansion**: `time_from` / `time_to` bounds accepted by the expand API and applied as time predicates in Bitcoin, EVM, and Solana chain compilers
- **candidate selection / subset apply**: per-edge checkboxes in the preview panel; "Apply selected" prunes both edges and reachable nodes before committing the delta
- Bitcoin is excluded from the asset-selector path
- `value_fiat` is the canonical active-path edge fiat field
- bridge animation follows backend `bridge_source` / `bridge_dest`
- layout/manual-placement safeguards are intact

## Current Multi-Asset Boundary

- multi-asset selection v1 and asset-scope persistence v1 are shipped for inspector expand/preview and node quick `Prev` / `Next` on supported non-Bitcoin address flows
- `All assets` emits no `asset_selectors`; `Specific assets` emits deterministic plural `asset_selectors`
- manual export/import, backend session restore, and backend autosave all round-trip per-node asset scope
- stale snapshot conflicts pause autosave with a visible notice instead of overwriting newer saved state
- edge selective trace remains single-asset scoped and must not inherit inspector multi-selection
- Bitcoin remains excluded from the asset-selector path

Do not remove the README **Active Graph Contract** section that documents this current boundary.

## Required Reading Before Graph Work

- `tasks/memory.md` — architectural decisions (ADRs), current shipped state, guardrails, security invariants
- `tasks/lessons.md` — concrete past mistakes and the rules that prevent them

These are the authoritative repo-memory files. Consult them before changing:
- graph schema
- graph API
- trace compiler semantics
- React graph contract

## Guardrails (Summary)

Full guardrails live in `tasks/memory.md`. Key constraints:

- Do not widen this repo into the private compliance dashboard.
- Do not prefix UTXO or Solana tx hashes with `0x`.
- Do not invent swap semantics from thin evidence.
- Do not emit `swap_event` without both asset legs justified from persisted transaction context.
- Auth must fail closed. Missing or non-owned session IDs return `404`.
- Expansion guardrails are mandatory: `depth <= 3`, `max_results <= 100`, `page_size <= 50`.
