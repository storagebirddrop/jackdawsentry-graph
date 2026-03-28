# Graph Truthfulness Assurance Summary

## Scope

In this project, graph truthfulness means the investigator-facing graph should not imply more certainty, completeness, freshness, or causality than the underlying evidence supports. This includes session restore behavior, empty-state and degraded-state messaging, and wording around unresolved bridge or correlation signals.

## What Was Reviewed

- Restore and session truthfulness, including which saved workspace is presented as the latest available state
- Real served-path parity for the public investigator UI
- Degraded-state and timeout honesty for pending, unavailable, or incomplete ingest states
- Empty-state and absence framing under directional or dataset-limited conditions
- Pending bridge and correlation wording for unresolved hops
- Investigator-facing UI wording wherever backend state may be technically correct but still easy to overread

## Major Issues Found And Addressed

- Restore discovery previously allowed a stale browser-local hint to compete with backend authority. The restore path was tightened to follow the backend’s newest recent session.
- A degraded ingest path could continue to imply active progress after the backend no longer had a queue record. That state now surfaces as unavailable rather than active fetching.
- An unsupported-chain timeout path could quiet down after timeout even while the backend still remained pending. The timeout warning now persists and keeps incomplete-state uncertainty visible.
- Unresolved bridge hops previously used generic `Confidence` wording. The active pending-hop UI now uses correlation-specific wording so the percentage is scoped to unresolved correlation evidence rather than generic certainty.

## What Is Now Proven

For the reviewed scenarios, the current repository state is supported by code inspection, focused tests, targeted runtime checks, and investigator-facing browser verification.

- Restore selection follows backend session authority rather than stale browser-local state for the reviewed restore flow.
- The real served `/app/` path is aligned with the current frontend build and remains part of the reviewed evidence surface.
- `not_found` ingest states no longer present as active progress in the reviewed degraded-state flow.
- Unsupported-chain timeout handling keeps degraded-state uncertainty visible instead of going quiet while pending remains unresolved.
- Pending unresolved bridge hops use correlation-specific wording in the active investigator path.
- Directional empty-state handling remains dataset-scoped in the reviewed absence-framing logic.
- Autosave conflict handling continues to distinguish successful saves from stale-revision conflicts in the reviewed conflict path.

## Residual Risks And Limitations

- Deployments that run without a live ingest sidecar remain operationally limited. That is an environment limitation, not a current proven truthfulness defect by itself.
- Some reviewed areas are stronger than others in current proof freshness. Directional empty-state behavior and autosave conflict behavior remain supported, but were not re-captured in every latest browser pass.
- Bridge wording consistency outside the active unresolved-hop path may still deserve future review if additional UI surfaces are introduced or expanded.
- This summary reflects the reviewed scenarios, not an exhaustive proof of every chain, provider, timeout, or partial-data combination.

## Explicit Non-Claims

This summary does not claim:

- exhaustive verification of every blockchain, ingest provider, or runtime configuration
- permanent immunity from future UI regressions
- full operational ingest availability in every deployment
- that every possible graph interpretation risk has been eliminated

## Maintenance Expectations

Revisit this assurance area after:

- major UI or investigator workflow changes
- session restore or autosave behavior changes
- ingest-state, timeout, or empty-state behavior changes
- bridge or correlation presentation changes
- any deployment change that could affect the served investigator path

## Repository Note

This repository intentionally keeps the public assurance summary concise. Detailed internal drill history, intermediate repro chains, and internal review records are not included here by default.
