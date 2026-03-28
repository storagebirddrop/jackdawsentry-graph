# Wave 06 Public Release Proof

## Claims Under Verification

- The limited/internal release baseline remained intact at the start of this
  run.
- Wave 02 session-authority guardrails remain intact.
- Wave 03 mounted bridge polling remains intact.
- Wave 04 empty-state honesty remains intact.
- Wave 05 hardening remains intact.
- Auth-enabled abuse and perf probes now have real evidence.
- Browser artifacts for bridge polling and the Wave 04 empty-state notice are
  still either present or honestly absent.

## Evidence Matrix

| Claim | Evidence Label | Exact Evidence | File Path / Test / Command / Repro Source / Artifact | Strength Of Proof | Remaining Uncertainty |
|---|---|---|---|---|---|
| Wave 02 session-authority guardrails remain in place | Observed in code | Recent-session restore discovery, `restore_state`, and stale snapshot revision protection remain wired | `src/api/routers/graph.py`, `src/services/graph_sessions.py`, `frontend/app/src/App.tsx`, `frontend/app/src/api/client.ts` | High | No browser artifact of restore flow |
| Wave 03 mounted bridge polling remains in the active investigator path | Observed in code | `useBridgeHopPoller` remains wired through `InvestigationGraph` / `GraphInspectorPanel`; dead drawer path is absent | `frontend/app/src/components/InvestigationGraph.tsx`, `frontend/app/src/components/GraphInspectorPanel.tsx`, `frontend/app/src/store/graphStore.ts` | High | No live browser artifact of polling UI |
| Wave 04 empty-state honesty logic remains present | Observed in code | `_build_empty_state` still distinguishes requested-direction and opposite-direction indexed activity | `src/trace_compiler/compiler.py` | High | No live browser artifact of rendered notice |
| Wave 05 hardening remains intact in repo validation | Observed in executed tests/build/lint/typecheck | Focused suite passed (`91 passed, 0 failed`); frontend build passed; boundary/public-readiness audits passed | `pytest` command from this run; `npm run build`; `.venv/bin/python scripts/quality/boundary_audit.py`; `.venv/bin/python scripts/quality/public_readiness_audit.py` | High | Build success is not a browser artifact |
| The repo was still limited/internal-release safe at the start of this run | Inferred from evidence | Prior guardrails were present in code and the focused suite/build/audits stayed green before the auth-enabled probes | Code anchors above plus executed validation in this run | Medium | Baseline readiness is inferred from combined evidence, not a single probe |
| The documented auth-enabled bootstrap path was incomplete before this run | Observed in runtime/repro | `sed` of `scripts/dev/create_user.py` failed because the file did not exist even though `README.md` referenced it | `sed -n '1,260p' scripts/dev/create_user.py` in this run; `README.md:271-283` | High | None for the missing-file fact |
| The repo now includes a local auth-enabled user bootstrap helper | Observed in code | `scripts/dev/create_user.py` now exists | `scripts/dev/create_user.py` | High | No automated test covers the helper file directly |
| The bootstrap helper actually worked against the local stack database | Observed in runtime/repro | `created: username=wave6_probe email=wave6_probe@local.invalid role=analyst active=True ...` | `DEBUG=true .venv/bin/python scripts/dev/create_user.py --username wave6_probe --password 'Wave6Probe!234' --email wave6_probe@local.invalid --role analyst` | High | Evidence is for one local user creation path |
| Auth-enabled login worked on the local release candidate | Observed in runtime/repro | `POST /api/v1/auth/login` returned `200 OK` with `cache-control: no-store` | `curl -i -X POST http://localhost:8081/api/v1/auth/login ...` in this run | High | Single credentialed local user only |
| Auth-enabled abuse controls were exercised successfully | Observed in runtime/repro | `live_abuse_probe.py` passed; hit `429` on both expand and hop-status abuse loops after expected warmup statuses | `DEBUG=true .venv/bin/python scripts/quality/live_abuse_probe.py --username wave6_probe --password 'Wave6Probe!234'` | High | Probe ran on one local auth-enabled candidate only |
| Auth-enabled perf profiling was exercised successfully | Observed in runtime/repro | `live_perf_probe.py` passed against the auth-enabled stack | `DEBUG=true .venv/bin/python scripts/quality/live_perf_probe.py --username wave6_probe --password 'Wave6Probe!234'` | High | Default seed selection chose low-value `bitcoin/unknown` control-plane path |
| Auth-enabled perf evidence includes a real graph-growth case | Observed in runtime/repro | Ethereum-seeded perf probe returned `200` and non-zero nodes/edges on `expand_neighbors` | `DEBUG=true .venv/bin/python scripts/quality/live_perf_probe.py --username wave6_probe --password 'Wave6Probe!234' --seed-address 0xdac17f958d2ee523a2206206994597c13d831ec7 --seed-chain ethereum --iterations 3` | High | Did not surface bridge-hop nodes in this run |
| Default runtime docs remain disabled | Observed in runtime/repro | Auth-disabled stack served `/docs` `404` and `/openapi.json` `404`; auth-enabled abuse probe also required `/docs` `404` and passed | Prior Wave 5 runtime checks plus `live_abuse_probe.py` output in this run | High | `/openapi.json` was not rechecked during the auth-enabled candidate run specifically |
| The local stack was restored to default auth-disabled mode after the auth-enabled probes | Observed in runtime/repro | Final `/health` body returned `"auth_disabled": true` after restoring `.env` and rebuilding | `curl -sS http://localhost:8081/health` after restoring the default stack in this run | High | None for the final local stack state |
| Concrete browser artifacts for mounted bridge polling were produced in this run | Observed in runtime/repro | Browser capture attempt failed because Playwright/Chrome was unavailable; install path required sudo and could not complete; no screenshot/artifact was produced | `mcp__playwright__browser_navigate` error; `mcp__playwright__browser_install` error in this run | High | Browser behavior itself remains unverified |
| Concrete browser artifacts for the Wave 04 empty-state notice were produced in this run | Observed in runtime/repro | No browser artifact was captured because the browser tooling path was blocked before a UI repro could be recorded | Same Playwright errors from this run | High | Empty-state UI remains unverified at the browser-artifact layer |

## Guardrails Rechecked

- Observed in code: recent-session restore discovery remains present.
- Observed in code: `restore_state` handling remains present.
- Observed in code: snapshot revision conflict protection remains present.
- Observed in code: mounted bridge polling remains present in the active UI
  path.
- Observed in code: indexed directional empty-state honesty remains present.
- Observed in executed tests/build/lint/typecheck: the focused suite remained
  green after the Wave 6 helper addition.

## Auth-Enabled Probe Status

- Observed in runtime/repro: `live_abuse_probe.py` passed against a temporary
  auth-enabled local candidate using a real created user.
- Observed in runtime/repro: `live_perf_probe.py` passed against the same
  candidate.
- Observed in runtime/repro: an additional Ethereum-seeded perf run exercised a
  non-zero graph expansion path.

## Browser Artifact Status

- Observed in runtime/repro: no concrete browser artifact was produced in this
  run.
- Observed in runtime/repro: Playwright could not launch because Chrome was not
  installed, and the install path failed under non-interactive sudo
  restrictions.

## Regressions Found

- Observed in runtime/repro: no Wave 02 / 03 / 04 / 05 product regression was
  reproduced during this gate run.
- Observed in runtime/repro: the missing `scripts/dev/create_user.py` helper
  was a real release-evidence blocker and was fixed in this run.

## What Is Proven

- Observed in code: Waves 02 through 05 guardrails remain present.
- Observed in executed tests/build/lint/typecheck: the focused suite, build,
  and static audits are green after this run.
- Observed in runtime/repro: auth-enabled abuse controls were exercised and
  passed.
- Observed in runtime/repro: auth-enabled perf profiling was exercised and
  passed.
- Observed in runtime/repro: the repo now contains a working local bootstrap
  path for auth-enabled probe users.

## What Is Only Inferred

- Inferred from evidence: the project is strong enough to move beyond a purely
  limited/internal posture because the auth-enabled probe gap is now closed.
- Inferred from evidence: bridge polling and empty-state UI are likely correct
  in the browser because their code/tests/build remained stable, but the
  browser-artifact layer is still missing.

## What Is Still Unverified

- Claimed in docs/history but not yet verified: a browser artifact showing
  mounted bridge polling updating the active UI.
- Claimed in docs/history but not yet verified: a browser artifact showing the
  Wave 04 empty-state notice in the running UI.
- Claimed in docs/history but not yet verified: unrestricted public-release
  readiness beyond limited/beta external rollout.

## Public-Release Exit Criteria Assessment

1. Wave 02 session-authority guardrails remain intact: Met
2. Wave 03 bridge polling remains intact: Partially Met
3. Wave 04 empty-state honesty remains intact: Partially Met
4. Wave 05 release hardening remains intact: Met
5. Stronger public-release evidence exists or is honestly absent: Partially Met
