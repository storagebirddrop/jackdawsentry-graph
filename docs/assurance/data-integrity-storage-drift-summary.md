# Data Integrity / Storage Drift Assurance Summary

## Scope

This assurance review focused on whether the standalone graph stack can become misleading because PostgreSQL raw facts, Neo4j graph materialization, ingest behavior, or saved session/cache state drift away from each other or from the facts they are supposed to represent.

## What Was Reviewed

- Cross-store consistency between PostgreSQL raw facts and Neo4j graph expansion paths
- Fallback provenance and reviewed-fact support on the reviewed expansion contract
- Duplicate and replay safety on reviewed ingest and raw-write paths
- Partial ingest and partial materialization behavior
- Session, snapshot, and cache integrity on reviewed restore/save paths
- Stale, missing, or contradictory data framing in the expansion contract
- Reviewed chain-specific fallback behavior for EVM, Bitcoin, and Solana

## Major Issues Found And Addressed

- A reviewed expansion path could return non-empty Neo4j-only graph results even when the PostgreSQL event store had no indexed facts for that path, and the response looked like an ordinary success. The expansion contract now surfaces fallback provenance and an explicit integrity warning, and the investigator UI now shows that warning.

## What Is Now Proven

For the reviewed integrity surface, current assurance is supported by current code inspection, focused tests, targeted runtime repros, and build checks.

- Reviewed fallback-capable expansion paths now record whether data came from indexed PostgreSQL facts or Neo4j fallback.
- The backend expansion contract now exposes fallback provenance and an integrity warning when reviewed non-empty fallback results are returned, and current cache behavior preserves those fields.
- The active investigator UI path remains wired to surface that warning instead of silently treating reviewed fallback results as ordinary success.
- The current reviewed synthetic Neo4j-only EVM repro still returns explicit fallback provenance and warning data in the live API while reviewed PostgreSQL raw-fact counts remain zero.
- Reviewed ingest queue, raw-write idempotency, and snapshot revision protections remain supported by current code and focused tests.

## Residual Risks And Limitations

- This review did not run a broad sampled parity audit across the live PostgreSQL and Neo4j datasets.
- This review did not run a stale bridge-correlation invalidation drill.
- Live drift verification focused on a high-value reviewed EVM fallback path; equivalent live synthetic drills were not rerun for every fallback-capable chain.
- The investigator-facing warning remains validated through code, contract, build, and runtime response evidence, not a captured browser artifact.

## Explicit Non-Claims

This summary does not claim:

- exhaustive parity proof between every PostgreSQL fact and every Neo4j relationship
- complete immunity from future ingest/materialization drift
- full integrity assurance for every bridge or cross-store inference path
- browser-level proof for every investigator-facing integrity warning state

## Maintenance Expectations

Revisit this assurance area after:

- ingest or materialization changes
- schema or storage-boundary changes
- session, snapshot, or cache contract changes
- chain-support or chain-compiler fallback changes
- replay/idempotency logic changes

## Repository Note

This repository intentionally keeps the public assurance summary concise. The full internal drill chain, handoff records, and raw repro artifacts are not included in the public docs tree by default.
