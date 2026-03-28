# Operational Recovery / Rollback Assurance Summary

## Scope

This assurance review focused on whether the standalone graph stack can be rebuilt, restarted, and recovered without silently changing release posture, losing investigator session state, or presenting a misleading recovery state during partial outage.

## What Was Reviewed

- Rebuild recovery for the served frontend bundle
- Deploy and environment drift in the graph compose stack, including the ingest overlay
- Rollback-sensitive runtime posture for the standalone graph API
- Outage and degraded recovery behavior on the served health and status surfaces
- Session and restore-state survivability across service restart
- Health, readiness, and operator visibility during recovery

## Major Issues Found And Addressed

- Recovery posture could drift under hostile caller-shell values because several graph-api mode flags were still interpolated directly in `docker-compose.graph.yml`. Those reviewed flags now remain repo-owned so recovery and rollback no longer silently change docs, metrics, proxy, or rate-limit posture.

## What Is Now Proven

For the reviewed recovery surface, current assurance is supported by code inspection, focused tests, targeted runtime repros, and rebuild checks.

- The reviewed graph-api mode flags now stay anchored to repo-owned configuration during compose rendering, including when the ingest overlay is considered.
- The reviewed served public posture still keeps docs, schema, and reviewed metrics surfaces closed under the current default runtime.
- The current frontend production build succeeds.
- The current stack recovers from an API restart and preserves reviewed session restore state.
- During API restart, the served path reports outage honestly instead of falsely claiming health.
- The current stack returns to healthy service after restart on the reviewed path.

## Residual Risks And Limitations

- The default served recovery surface exposes only coarse `/health`; dependency-aware detailed health remains an operator-only/internal surface.
- Compose recovery still uses `service_started` ordering, so a backend restart can produce a noticeable served `502` window before the stack is healthy again.
- This review did not perform a full older-image rollback rehearsal.
- This review did not deliberately destroy the frontend bundle and then restore it; rebuild survivability is currently supported by successful build evidence rather than a destructive outage drill.

## Explicit Non-Claims

This summary does not claim:

- exhaustive disaster recovery validation across every dependency and deployment shape
- zero-downtime restart behavior
- full rollback assurance for arbitrary older images
- permanent immunity from future recovery or config-drift regressions

## Maintenance Expectations

Revisit this assurance area after:

- deploy or rebuild flow changes
- compose or environment-default changes
- health/status surface changes
- session persistence or restore-state changes
- rollback procedure or release-posture changes

## Repository Note

This repository intentionally keeps the public assurance summary concise. The full internal drill chain, handoff records, and raw outage/repro artifacts are not included in the public docs tree by default.
