# Assurance

This directory contains concise, public-facing summaries of the major assurance areas reviewed for Jackdaw Sentry.

These summaries are intended to document what was reviewed, what was found, what is currently supported by evidence, and what residual risks remain. They are not a dump of detailed assessment records, raw repro logs, or intermediate remediation planning.

## Purpose

The goal of this directory is to preserve the outcome of assurance work without overwhelming the public repository with internal process detail.

Each summary is meant to answer:
- what assurance area was reviewed
- what meaningful issues were found and addressed
- what is currently supported by code, tests, runtime repros, or artifacts
- what remains limited, bounded, or explicitly not claimed
- when this area should be revisited

## Current Assurance Areas

- [Graph Truthfulness Summary](./graph-truthfulness-summary.md)
  Investigator-facing correctness, honesty of graph interpretation, degraded-state messaging, restore behavior, and bridge/correlation wording.

- [Security / Authz / Abuse Summary](./security-authz-abuse-summary.md)
  Authentication posture, authorization boundaries, abuse resistance, route exposure posture, and reviewed hostile-path protections.

- [Operational Recovery / Rollback Summary](./operational-recovery-rollback-summary.md)
  Recovery from broken rebuilds, caller-shell/env drift, restart behavior, rollback safety, and operational truthfulness during outage windows.

- [Data Integrity / Storage Drift Summary](./data-integrity-storage-drift-summary.md)
  Cross-store consistency, fallback provenance, reviewed-fact support, session/cache integrity, and storage-drift risks.

- [Performance / Scale / Analyst Workload Summary](./performance-scale-analyst-workload-summary.md)
  Large-session behavior, repeated expansion, polling churn, concurrent session pressure, and analyst-facing behavior under moderate workload.

- [Dependency / Supply-Chain / Release Provenance Summary](./dependency-supply-chain-release-provenance-summary.md)
  Dependency locking, build/release provenance, packaged frontend asset trust, container/compose provenance, and reviewed release-path integrity.

## How To Read These Summaries

These summaries are intentionally concise. They are not meant to replace:
- source code review
- test review
- release gates
- security review
- operational runbooks

They should be read as assurance posture snapshots, not as absolute guarantees.

A summary may distinguish between:
- **proven** behavior
- **bounded residual risk**
- **partially proven** areas
- **explicit non-claims**

That distinction is intentional and important.

## What Is Not Kept Here

This directory does **not** include the full internal drill history by default. In particular, the public repo should generally avoid storing:
- detailed assessment records
- handoff files
- intermediate remediation plans
- raw repro logs
- raw screenshots or JSON artifacts
- environment-specific debugging outputs
- local-only operational details

Those materials may still exist in private working branches, internal archives, or temporary review records.

## Maintenance Expectations

This directory should be updated when an assurance area changes materially, especially after:
- auth or route posture changes
- session/restore/autosave behavior changes
- ingest/materialization or storage-boundary changes
- Docker/compose or release-path changes
- major frontend rendering/polling changes
- dependency or CI/release pipeline changes

If an assurance area is re-reviewed, update the relevant summary to reflect the current final state rather than appending a long historical diary.

## Repository Convention

The public repository should prefer:
- one concise summary per assurance area
- actual code/config/test fixes
- user-facing docs that match the shipped posture

The public repository should avoid turning this directory into a process archive.

## Status Interpretation

Unless a summary explicitly says otherwise, these documents should be read as:

- current best-known assurance posture for the reviewed area
- scoped to the reviewed surfaces and evidence available at the time
- subject to re-validation after future changes
