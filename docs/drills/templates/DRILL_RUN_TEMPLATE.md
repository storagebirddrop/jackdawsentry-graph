# Drill Run Template

## Objective

State the narrow goal of the run.

## Scope

- what is in scope
- what is out of scope
- what must not regress

## Inputs

- prior proof packets
- runtime artifacts
- repo files inspected directly

## Evidence Plan

- code inspection targets
- executed validation to rerun
- runtime repro steps
- browser artifacts or probe artifacts to capture

## Working Files

- `records/` for generated markdown packets
- `artifacts/` for screenshots, traces, JSON payloads, repro notes

## Exit Criteria

- exact proof needed to call the run complete
- exact blockers that force downgrade or re-baselining

## Archive Checklist

- move the completed run into `docs/drills/archive/YYYY-MM-DD-slug/`
- keep one durable summary in `docs/releases/` if the outcome matters long-term
- leave the repo root free of transient drill packets
