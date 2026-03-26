"""On-demand address ingest trigger.

Called by TraceCompiler.expand() when the chain compiler returns an empty
result set (no events in the raw event store for the queried address).
Queues a row in ``address_ingest_queue`` and, when an Etherscan API key is
available, immediately fires a background live-fetch task so the data
arrives within seconds rather than waiting for an external ingest worker.

Guarantees:
- One active (pending/running) row per (address, blockchain) — idempotent.
- Best-effort: all DB errors are swallowed so a queue failure never blocks
  the expansion response.
- Newly queued addresses always have ``priority = 1`` (slightly above default
  background backfill) so investigator-driven requests are served first.
- Recently-completed queue entries (within 1 hour) suppress re-queuing to
  avoid hammering the Etherscan API for addresses with no on-chain history.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

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

    Checks whether ``raw_transactions`` or ``raw_token_transfers`` already
    has rows for this (address, chain) pair before inserting.  If data
    exists — meaning the expansion was empty for other reasons (e.g. filtered
    by options) — no queue entry is created.

    When an ``ETHERSCAN_API_KEY`` environment variable is set and the chain
    is supported, fires a background live-fetch task immediately after
    queuing so data arrives within seconds.

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
            # Check whether the event store already has data for this address.
            has_transactions = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM raw_transactions
                    WHERE blockchain = $1
                      AND (from_address = $2 OR to_address = $2)
                    LIMIT 1
                )
                """,
                chain,
                address,
            )
            if has_transactions:
                return False

            has_token_transfers = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM raw_token_transfers
                    WHERE blockchain = $1
                      AND (from_address = $2 OR to_address = $2)
                    LIMIT 1
                )
                """,
                chain,
                address,
            )
            if has_token_transfers:
                return False

            # Suppress re-queuing when a completed entry already exists within
            # the last hour — the address likely has no on-chain history.
            recently_fetched = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM address_ingest_queue
                    WHERE address = $1
                      AND blockchain = $2
                      AND status = 'completed'
                      AND completed_at > NOW() - INTERVAL '1 hour'
                )
                """,
                address,
                chain,
            )
            if recently_fetched:
                logger.debug(
                    "Skipping re-queue for %s/%s — completed within last hour",
                    address,
                    chain,
                )
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

    except Exception as exc:
        logger.warning(
            "maybe_trigger_address_ingest failed for %s/%s: %s", address, chain, exc
        )
        return False

    if not triggered:
        return False

    # --- Live fetch (standalone mode) ----------------------------------------
    # Fire a background coroutine immediately after queuing so data arrives
    # within seconds rather than waiting for an external ingest worker.
    # All live fetch paths are best-effort — failures never block the response.

    # EVM chains: Etherscan v2 API
    api_key = os.environ.get("ETHERSCAN_API_KEY", "").strip()
    if api_key:
        try:
            from src.trace_compiler.ingest.live_fetch import (
                fetch_evm_address_history,
                supported_chain,
            )

            if supported_chain(chain):
                asyncio.ensure_future(
                    fetch_evm_address_history(address, chain, pg_pool, api_key)
                )
                logger.info("Fired background EVM live fetch for %s on %s", address, chain)
        except Exception as exc:
            logger.debug("Failed to fire EVM live fetch for %s/%s: %s", address, chain, exc)

    # Solana: JSON-RPC getSignaturesForAddress + getTransaction
    if chain == "solana":
        rpc_url = os.environ.get("SOLANA_RPC_URL", "").strip() or None
        if rpc_url:
            try:
                from src.trace_compiler.ingest.solana_live_fetch import (
                    fetch_solana_address_history,
                )

                asyncio.ensure_future(
                    fetch_solana_address_history(address, pg_pool, rpc_url)
                )
                logger.info("Fired background Solana live fetch for %s", address)
            except Exception as exc:
                logger.debug("Failed to fire Solana live fetch for %s: %s", address, exc)
        else:
            logger.debug(
                "Solana live fetch skipped for %s — SOLANA_RPC_URL not configured", address
            )

    return True
