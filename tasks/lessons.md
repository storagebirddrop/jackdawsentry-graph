# Lessons

Record concrete mistakes and the follow-up rule that prevents them from recurring.

## 2026-03-26 — `raw_transactions` conflict when co-storing instruction bytes

**What went wrong:** Tried to store Solana instruction bytes in `raw_transactions.input_data`
keyed by `(blockchain='solana', tx_hash, to_address=program_id)` with `ON CONFLICT DO UPDATE`.
`raw_transactions` has `UNIQUE ON (blockchain, tx_hash)` — one row per transaction. The UPDATE
would have overwritten real SOL transfer rows (zeroing `from_address`, `to_address`, `value_raw`).
Worse, the ix_rows insert ran before native SOL inserts, so real SOL transfers would be silently
dropped by `DO NOTHING`.

**Rule:** Before writing to any shared event-store table, check its primary key / unique constraints
(`006_raw_event_store.sql`). `raw_transactions` = 1 row per `(blockchain, tx_hash)`. Secondary data
(instruction bytes, decoded args) must use its own keyed table — `raw_solana_instructions` already
has `decoded_args JSONB` and `PRIMARY KEY (tx_signature, ix_index)`.

## 2026-03-26 — Persistent `self` cache blocks correct behavior on repeated calls

**What went wrong:** Used `self._generic_swap_cache: set` to deduplicate swap attempts across loop
iterations. Worked within a single `_build_graph` call but silently suppressed correct swap
promotion on any subsequent `expand()` call that encountered the same `(tx_hash, counterparty)`.

**Rule:** Dedup sets that are only meaningful within a single method call must be local variables,
not instance attributes. If the same swap_event node would be built twice, `seen_nodes.setdefault`
(which is already local) handles it correctly. Only use `self.*` for state that is intentionally
persistent across calls (e.g. loaded registry data, connection pools).

## 2026-03-26 — Test mock call-count assumptions break silently when new DB queries are added

**What went wrong:** `test_on_demand_ingest.py` uses a hand-rolled `fetchval` side_effect that
routes by call index (0 = tx check, 1 = token check, 2 = INSERT RETURNING id). Adding a third
check (`recently_fetched`) shifted the INSERT to call index 3, so the mock returned `None` for
the insert and every "queues a row" assertion silently became False.

**Rule:** When adding a new DB query to a function that has call-index-based mocks, immediately
update the mock. Prefer named query mocks (match on SQL substring) over positional index
mocks — they survive query reordering.

## 2026-03-26 — `0x` prefix must not be added to UTXO or Solana hashes

**What went wrong:** `get_transaction` in `graph.py` added a `0x` prefix to any 64-char bare hex
string before looking up the transaction. Bitcoin and Ethereum both use 64-char hex hashes, so the
same normalisation wrongly prefixed Bitcoin tx hashes with `0x`, breaking event-store lookups for
UTXO chains.

**Rule:** Always check `chain` (normalised to lowercase) against a known EVM set before prefixing
`tx_hash` with `0x`. Only EVM chains (ethereum, polygon, bsc, arbitrum, base, avalanche, optimism,
starknet, injective) store hashes with the `0x` prefix. UTXO chains and Solana store bare hex or
base58 — never prefix them.

## 2026-03-26 — SQL migration: create new index before dropping old to avoid a uniqueness window

**What went wrong:** Migration 015 dropped `raw_tx_unique` on `(blockchain, tx_hash)` and then
created the replacement index on `(blockchain, tx_hash, transfer_index)` in two separate steps.
Between DROP and CREATE there was a window with no uniqueness enforcement, allowing duplicate rows.

**Rule:** In PostgreSQL migrations that replace a unique index, always `CREATE UNIQUE INDEX` under a
temporary name first, then `DROP INDEX` the old one, then `ALTER INDEX … RENAME TO` the canonical
name. Wrap the three steps in a single transaction block for extra safety.

## 2026-03-26 — Null-safe JSON field access: `(row.get("field") or "")` not `row.get("field", "")`

**What went wrong:** `live_fetch.py` used `row.get("from", "").lower()`. The default `""` only
fires when the key is *absent*. When the JSON explicitly contains `"from": null`,
`row.get("from", "")` returns `None` and `.lower()` raises `AttributeError`.

**Rule:** For any field that might be absent **or** explicitly null, use
`(row.get("field") or "").lower()`. Optionally append `or None` at the end to normalise empty
strings to `None` so downstream code treats both missing and null uniformly.

## 2026-03-26 — Guard against empty `senders` before indexing in Solana token pairing

**What went wrong:** The sender/receiver pairing loop in `solana_live_fetch` unconditionally
accessed `senders[0]` inside a `for i, receiver in enumerate(receivers)` loop. When a mint had
receivers but no negative-delta entries (e.g. a pure airdrop), `senders[0]` raised `IndexError`.

**Rule:** Add `if not senders: continue` as the first statement inside the receiver loop. Only pair
when at least one sender exists; skip the receiver entirely rather than raising.

## 2026-03-28 — Restore discovery must be backend-owned, not local-storage-gated

**What went wrong:** The frontend moved restore payloads to the backend session
contract, but the restore entry point still depended on a browser-local hint to
surface any candidate session at all. Clearing local storage made valid backend
sessions undiscoverable.

**Rule:** If the backend owns session continuity, restore discovery must also be
backend-owned. Browser storage may rank or hint recent sessions, but it must
never be the gate that decides whether restore is possible.

## 2026-03-28 — A contract field is dead code until a user can see or depend on it

**What went wrong:** `restore_state` existed in the backend restore contract,
but the frontend ignored it, so `legacy_bootstrap` rows silently looked like
full restores.

**Rule:** When adding a user-facing contract field, either wire it into visible
behavior immediately or remove it. Do not leave “important later” fields in the
payload without a consumer.

## 2026-03-28 — Autosave needs monotonic conflict protection, not best-effort ordering

**What went wrong:** Client-side debounce reduced write volume, but older
in-flight snapshot requests could still land after newer ones and silently
overwrite fresher workspace state.

**Rule:** Any authoritative autosave path needs a server-enforced monotonic
revision or equivalent compare-and-set check. UI timing alone is not a write
ordering guarantee.

## 2026-03-28 — Unmounted components must not own live truth

**What went wrong:** Bridge-hop polling lived in `BridgeHopDrawer`, which was
not mounted anywhere in the active investigator flow. The mounted inspector
showed stale status even though a poller existed elsewhere in the tree.

**Rule:** Live truth ownership must sit on the mounted path the investigator is
actually using. If a detached component owns the only refresh logic, either
move that logic into the mounted path or delete the detached component.

## 2026-03-28 — Generic shell env names can silently break docker-compose boot

**What went wrong:** The graph API compose service interpolated generic shell
variables like `DEBUG` directly into the container environment. A caller shell
with `DEBUG=release` overrode the repo `.env`, which made
`GRAPH_AUTH_DISABLED=true` fail validation and left nginx serving `502` against
an API that never booted.

**Rule:** For local compose-based runtimes, do not rely on ambient shell values
for safety-critical mode flags when the repo already owns a canonical `.env`.
Load those flags from `env_file` or a repo-specific variable name so the stack
boots predictably from repository state.
