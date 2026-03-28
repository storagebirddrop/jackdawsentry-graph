# Security Policy

## Reporting

Please report security issues privately to the maintainers before opening a
public issue.

Include:
- affected version or commit
- impact summary
- reproduction steps
- suggested mitigation if available

## Scope

This policy covers the standalone graph product repository only.

## Security Posture

- `GRAPH_AUTH_DISABLED=true` is a local-development mode only. Production use
  must keep auth enabled and fail closed.
- Investigation sessions are owner-bound. Missing or non-owned session IDs must
  not leak whether a session exists.
- Browser bearer tokens belong in `sessionStorage` only. Do not move them into
  `localStorage`.
- Browser-local persistence must not become the authority for restorable graph
  state. Authoritative session restore and autosave live in the backend session
  snapshot contract.
- Snapshot writes are revision-guarded. Stale autosave writes must fail with a
  conflict instead of silently overwriting newer workspace state.
- Mounted UI paths own live investigator truth. Detached or unmounted polling
  code must not be the only freshness path for bridge-hop status.
- Empty-state messages must stay honest about what the indexed dataset does and
  does not contain.

## Public Runtime Defaults

- API docs stay disabled by default.
- Legacy flat graph endpoints stay disabled by default.
- Proxy headers are untrusted by default.
- Expansion guardrails remain mandatory:
  - depth <= `3`
  - `max_results` <= `100`
  - `page_size` <= `50`

## Verification Expectations

Before calling a stack release-ready:

```bash
python scripts/quality/boundary_audit.py
python scripts/quality/public_readiness_audit.py
```

For auth-enabled release candidates, also run:

```bash
python scripts/quality/live_abuse_probe.py --username <user> --password <password>
python scripts/quality/live_perf_probe.py --username <user> --password <password>
```

When the stack is running in auth-disabled local mode, do not overclaim those
auth-dependent probes as completed. Record what was and was not exercised.
