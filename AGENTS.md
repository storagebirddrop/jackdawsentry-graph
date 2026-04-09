# AGENTS.md — Shared Agent Instructions

Shared cross-agent instruction file for `jackdawsentry-graph`.
Read this before starting graph-related work.

## Source of Truth

Current `main` is the source of truth.

Compare all new work against `main`, not stale recovery branches, older task memory, or superseded PR branches.

## Active Shipped Graph Path

The currently shipped session graph path is **direct expand**.

Shipped behavior:
- **asset-aware expand** for supported non-Bitcoin address flows
  - inspector-based single-asset selection
  - stored per-node asset scope reused by Prev/Next
- **edge selective trace** is `tx_hash`-first and only adds asset scope when safe chain-local identity exists (EVM, Solana, TRON)
- Bitcoin is excluded from the asset-selector path
- `value_fiat` is the canonical active-path edge fiat field
- bridge animation follows backend `bridge_source` / `bridge_dest`
- layout/manual-placement safeguards are intact

## Not Part of the Current Active Shipped Path

- preview/apply
- date filtering
- candidate selection / subset apply
- multi-asset selection

Do not build on top of these as if they are already shipped.
Do not remove the README **Active Graph Contract** section that documents this boundary.

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
