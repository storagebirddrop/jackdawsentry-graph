# Release Gate Packet

## Current Release Recommendation

Ready for limited/internal release only

## Scope Reviewed

- Wave 02 session-authority guardrails
- Wave 03 mounted bridge polling path
- Wave 04 empty-state honesty
- Wave 05 docs alignment, dead-path cleanup, compose hardening, rebuilt-stack
  evidence, and release-readiness probes

## Guardrails That Must Not Regress

- backend recent-session restore discovery
- surfaced `restore_state` handling
- snapshot revision conflict protection
- mounted bridge polling ownership in the active inspector path
- indexed directional empty-state honesty
- disabled `/docs` and `/openapi.json` by default

## Correctness Status

- Observed in code: prior correctness waves remain present in the repo.
- Observed in executed tests/build/lint/typecheck: the focused regression suite
  passed (`91 passed, 0 failed`).
- Observed in runtime/repro: the rebuilt stack served the session-authority flow
  correctly after the Wave 5 fixes.

## Session-Authority Status

- Observed in code: backend restore discovery and snapshot revision protection
  remain in place.
- Observed in runtime/repro:
  - session create → `200`
  - recent-session discovery → `200`
  - session restore → `200`
  - first snapshot save with incremented revision → `200`
  - repeated stale revision → `409`
- Observed in runtime/repro: Wave 5 fixed a live `/sessions/recent` regression
  caused by UUID normalization drift between mocked tests and asyncpg rows.

## Bridge Polling Status

- Observed in code: mounted bridge polling remains owned by
  `useBridgeHopPoller` in the active investigator path.
- Observed in executed tests/build/lint/typecheck: browser-surface assertions
  and frontend build still pass.
- Claimed in docs/history but not yet verified: no live browser artifact was
  captured in this run showing the mounted bridge poller updating the UI.

## Empty-State Honesty Status

- Observed in code: `_build_empty_state` still distinguishes:
  - no indexed activity
  - indexed requested-direction activity with no new graph results
  - indexed opposite-direction activity
- Observed in executed tests/build/lint/typecheck: compiler stub regressions
  covering those branches remain green.
- Claimed in docs/history but not yet verified: no live browser artifact was
  captured in this run for the rendered empty-state notice text.

## Documentation Alignment Status

- Observed in code: `tasks/memory.md`, `tasks/lessons.md`, `tasks/todo.md`,
  `README.md`, and `SECURITY.md` now reflect the shipped session-authority,
  mounted bridge polling, and empty-state behavior far better than the pre-Wave
  5 state.
- Observed in code: stale “Phase 3 stub” router docstrings were removed from
  active session endpoints.

## Perf / Rollout Evidence

- Observed in executed tests/build/lint/typecheck:
  - `boundary_audit.py` passed
  - `public_readiness_audit.py` passed
  - frontend production build passed
- Observed in runtime/repro:
  - rebuilt local stack booted successfully after compose hardening
  - `/health` returned `200`
  - `/api/v1/status` returned `200`
  - `/docs` returned `404`
  - `/openapi.json` returned `404`
  - live session-authority probe latencies:
    - health: `46.4 ms`
    - status: `7.4 ms`
    - session create: `178.4 ms`
    - recent sessions: `11.7 ms`
    - session restore: `10.8 ms`
    - first snapshot save: `335.7 ms`
    - stale snapshot rejection: `7.6 ms`
  - local dataset footprint:
    - PostgreSQL `raw_transactions` estimate: `186`
    - PostgreSQL `raw_token_transfers` estimate: `564`
    - PostgreSQL `graph_sessions`: `94`
    - PostgreSQL `bridge_correlations`: `7`
    - Neo4j nodes: `634821`
    - Neo4j relationships: `729389`

## Known Limitations

- No auth-enabled `live_abuse_probe.py` result in this packet.
- No auth-enabled `live_perf_probe.py` result in this packet.
- No live browser artifact for mounted bridge polling.
- No live browser artifact for the Wave 04 empty-state notice.
- Frontend build still reports large chunks after minification.
- Session creation persistence is still split between `TraceCompiler` and
  `GraphSessionStore`.

## Open Risks

- Ambient shell env drift was a real rollout hazard in this run; Wave 5 hardened
  the compose path, but other deployment paths still need normal release
  discipline.
- Frontend interaction proof remains weaker than API/runtime proof.
- Large frontend bundles may affect slower environments even though the build
  succeeds.
- Broader production confidence still depends on auth-enabled probes and a
  refreshed hostile release gate.

## Explicit Non-Claims

- This packet does **not** claim a live browser proof for mounted bridge polling.
- This packet does **not** claim a live browser proof for the rendered Wave 04
  empty-state notice.
- This packet does **not** claim auth-enabled abuse resistance verification.
- This packet does **not** claim a full production perf characterization under a
  credentialed release candidate.
- This packet does **not** claim that the current local dataset represents every
  deployment footprint.

## Release Decision

Ready for limited/internal release only

## Conditions On Release

- keep the focused regression suite green
- keep `/docs` and `/openapi.json` disabled by default
- preserve recent-session restore discovery, `restore_state`, and snapshot
  revision conflict handling
- preserve the mounted bridge polling path and indexed directional empty-state
  logic
- run auth-enabled `live_abuse_probe.py` and `live_perf_probe.py` before making
  any broader production claim
- capture concrete browser/runtime artifacts for:
  - mounted bridge polling
  - Wave 04 empty-state notice text

## Required Post-Release Monitoring

- monitor `/health` and `/api/v1/status` after deploy/rebuilds
- monitor `5xx` rates on `/api/v1/graph/sessions/recent`
- monitor `409 Stale workspace snapshot revision` rates to distinguish healthy
  conflict protection from unexpected autosave churn
- monitor bridge-hop status polling latency and error rates once browser/runtime
  evidence is available
- re-run the focused regression suite and frontend build after any release
  hotfix touching session restore/autosave, bridge polling, or empty-state
  messaging
