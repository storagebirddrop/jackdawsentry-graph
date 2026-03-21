"""On-demand address ingest trigger.

Called by TraceCompiler.expand() when the chain compiler returns an empty
result set (no events in the raw event store for the queried address).
Queues a row in ``address_ingest_queue`` so the AddressIngestWorker can
fetch and store recent history for that address on the next polling cycle.

Guarantees:
- One active (pending/running) row per (address, blockchain) — idempotent.
- Best-effort: all DB errors are swallowed so a queue failure never blocks
  the expansion response.
- Newly queued addresses always have ``priority = 1`` (slightly above default
  background backfill) so investigator-driven requests are served first.
"""

from __future__ import annotations

import logging
from typing import Any
from typing import Optional

logger = logging.getLogger(__name__)

# Addresses created from fresh investigator sessions get slightly higher
# priority than background backfill rows (priority = 0).
_INGEST_PRIORITY = 1


async def maybe_trigger_address_ingest(
    address: str,
    chain: str,
    pg_pool: Optional[Any],
) -> bool:
    """Queue an address ingest if no data exists and no request is pending.

    Checks whether ``raw_transactions`` already has any rows for this
    (address, chain) pair before inserting.  If data already exists —
    meaning the expansion was empty for other reasons (e.g. filtered out
    by options) — no queue entry is created.

    Args:
        address:  Lowercase address to ingest (EVM hex, Solana base58, etc.).
        chain:    Blockchain name matching ``raw_transactions.blockchain``.
        pg_pool:  asyncpg connection pool.  When ``None``, returns False.

    Returns:
        True if a new queue entry was created, False otherwise.
    """
    if pg_pool is None:
        return False

    try:
        async with pg_pool.acquire() as conn:
            # Check whether raw_transactions already has data for this address.
            row_count = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM raw_transactions
                WHERE blockchain = $1
                  AND (from_address = $2 OR to_address = $2)
                LIMIT 1
                """,
                chain,
                address,
            )
            if row_count and row_count > 0:
                # Data exists — expansion was empty for other reasons.
                return False

            # Also check raw_token_transfers (ERC-20 / SPL token activity).
            token_row_count = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM raw_token_transfers
                WHERE blockchain = $1
                  AND (from_address = $2 OR to_address = $2)
                LIMIT 1
                """,
                chain,
                address,
            )
            if token_row_count and token_row_count > 0:
                return False

            # No data found — insert a queue entry if one isn't already active.
            # The unique partial index on (address, blockchain) WHERE status IN
            # ('pending', 'running') ensures idempotency via ON CONFLICT DO NOTHING.
            result = await conn.fetchval(
                """
                INSERT INTO address_ingest_queue
                    (address, blockchain, priority, status)
                VALUES ($1, $2, $3, 'pending')
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                address,
                chain,
                _INGEST_PRIORITY,
            )

            triggered = result is not None
            if triggered:
                logger.info(
                    "Queued on-demand ingest for %s on %s (id=%s)",
                    address,
                    chain,
                    result,
                )
            return triggered

    except Exception as exc:
        logger.debug(
            "maybe_trigger_address_ingest failed for %s/%s: %s", address, chain, exc
        )
        return False
