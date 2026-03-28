# Wave 06 Public Release Gate

## Objective

Determine whether Jackdaw Sentry can move beyond the existing
limited/internal-release posture by re-checking the shipped guardrails,
gathering stronger auth-enabled runtime evidence, and making a hard release
decision from current repository and runtime reality.

## Why This Gate Now

- Observed in code: Waves 02 through 05 are already implemented in the repo.
- Observed in executed tests/build/lint/typecheck: the focused regression suite,
  frontend build, and repo-native audits were still green at the start of the
  gate.
- Claimed in docs/history but not yet verified: Wave 05 left the project at
  "Ready for limited/internal release only" because auth-enabled probes and
  browser artifacts were still missing.
- Inferred from evidence: the next honest step was a public-release gate run,
  not another feature wave.

## Baseline Summary

- Observed in code: the Wave 02 session-authority guardrails remain present:
  recent-session restore discovery, surfaced `restore_state`, and snapshot
  revision conflict protection.
- Observed in code: the Wave 03 mounted bridge poller remains owned by the
  active investigator path, not a dead drawer path.
- Observed in code: the Wave 04 empty-state honesty branches remain present in
  the compiler.
- Observed in executed tests/build/lint/typecheck: the focused regression suite
  passed (`91 passed, 0 failed`), `npm run build` passed, and both
  `boundary_audit.py` and `public_readiness_audit.py` passed.
- Observed in runtime/repro: the default auth-disabled stack remained healthy
  and continued to serve `/health` with `"auth_disabled": true`.
- Observed in runtime/repro: the auth-enabled evidence gap was partially a repo
  tooling gap because `README.md` referenced `scripts/dev/create_user.py`, but
  that helper did not exist before this run.

## Execution Classification

Mixed gate/remediation run

## Scope Executed

- Rechecked the limited/internal baseline against current code and executed
  validation.
- Added the smallest safe release-blocker fix needed to exercise the
  auth-enabled release candidate path:
  - `scripts/dev/create_user.py`
- Created a local auth-enabled probe user.
- Temporarily rebuilt the stack with `GRAPH_AUTH_DISABLED=false` to exercise:
  - login
  - auth-enabled abuse controls
  - auth-enabled perf profiling
- Restored the local stack to the default auth-disabled dev posture after the
  probes.
- Attempted to gather browser artifacts and recorded the result honestly.

## Files Changed

- `scripts/dev/create_user.py`
- `WAVE_06_PUBLIC_RELEASE_GATE.md`
- `WAVE_06_PUBLIC_RELEASE_PROOF.md`
- `PUBLIC_RELEASE_GATE_PACKET.md`
- `NEXT_WAVE_HANDOFF.md`
- `MASTER_EXECUTION_PLAN_UPDATED.md`

## Evidence Gathered

- Observed in executed tests/build/lint/typecheck:
  - focused regression suite: `91 passed, 0 failed`
  - frontend production build passed
  - `boundary_audit.py` passed
  - `public_readiness_audit.py` passed
- Observed in runtime/repro:
  - auth-disabled default stack returned `/health` `200`
  - auth-enabled candidate returned `/health` `200` with
    `"auth_disabled": false`
  - auth-enabled login API returned `200`
  - auth-enabled `live_abuse_probe.py` passed
  - auth-enabled `live_perf_probe.py` passed
  - explicit Ethereum-seeded auth-enabled perf probe passed with non-zero graph
    growth on `expand_neighbors`
- Observed in runtime/repro:
  - browser artifact capture was attempted but blocked by missing
    Playwright/Chrome installation plus non-interactive sudo restrictions in
    this environment

## Validation Performed

- `.venv/bin/pytest tests/test_api/test_graph_app.py tests/test_api/test_ingest_status.py tests/test_api/test_browser_surface.py tests/test_trace_compiler/test_session_endpoints.py tests/test_trace_compiler/test_session_security.py tests/test_trace_compiler/test_session_persistence.py tests/test_trace_compiler/test_expansion_cache.py tests/test_trace_compiler/test_compiler_stub.py -q`
- `npm run build` in `frontend/app`
- `.venv/bin/python scripts/quality/boundary_audit.py`
- `.venv/bin/python scripts/quality/public_readiness_audit.py`
- `DEBUG=true .venv/bin/python scripts/dev/create_user.py --username wave6_probe --password 'Wave6Probe!234' --email wave6_probe@local.invalid --role analyst`
- Temporary auth-enabled rebuild via:
  - `DEBUG=true docker compose -f docker-compose.graph.yml up -d --build graph-api graph-nginx`
- Auth-enabled probes:
  - `DEBUG=true .venv/bin/python scripts/quality/live_abuse_probe.py --username wave6_probe --password 'Wave6Probe!234'`
  - `DEBUG=true .venv/bin/python scripts/quality/live_perf_probe.py --username wave6_probe --password 'Wave6Probe!234'`
  - `DEBUG=true .venv/bin/python scripts/quality/live_perf_probe.py --username wave6_probe --password 'Wave6Probe!234' --seed-address 0xdac17f958d2ee523a2206206994597c13d831ec7 --seed-chain ethereum --iterations 3`
- Restored the default auth-disabled dev posture afterward and rechecked
  `/health`.

## Results

- Observed in executed tests/build/lint/typecheck: the focused regression suite
  passed (`91 passed, 0 failed`).
- Observed in executed tests/build/lint/typecheck: the frontend production
  build passed, with the existing large-chunk warnings still present.
- Observed in executed tests/build/lint/typecheck: both repo-native audits
  passed.
- Observed in runtime/repro: the auth-enabled abuse probe passed.
- Observed in runtime/repro: the auth-enabled perf probe passed.
- Observed in runtime/repro: the explicit Ethereum-seeded perf probe produced
  successful graph expansion responses with non-zero added nodes/edges on
  `expand_neighbors`.
- Observed in runtime/repro: the local stack was returned to the default
  auth-disabled posture after the gate run.

## Remaining Gaps

- Observed in runtime/repro: no browser artifact was captured for mounted bridge
  polling because the environment lacked a usable Playwright/Chrome install and
  could not complete the browser installation path without sudo.
- Observed in runtime/repro: no browser artifact was captured for the Wave 04
  empty-state notice for the same reason.
- Inferred from evidence: bridge polling and empty-state UI behavior are still
  stronger at the code/test/build layer than at the live browser-artifact layer.
- Observed in executed tests/build/lint/typecheck: the frontend build still
  reports large chunks after minification.

## Release Decision Rationale

- Observed in code: the prior correctness and hardening guardrails remain in
  place.
- Observed in executed tests/build/lint/typecheck: the regression net and repo
  audits are green.
- Observed in runtime/repro: the auth-enabled candidate now has real abuse and
  perf evidence instead of a documentation-only claim.
- Observed in runtime/repro: the repo now includes the documented local
  user-bootstrap helper needed to reproduce auth-enabled probes.
- Inferred from evidence: this is strong enough to upgrade the posture from
  limited/internal release to limited/beta external release.
- Observed in runtime/repro: the browser-artifact gap is still open, so the
  evidence is not yet strong enough for an unrestricted public-release claim.
