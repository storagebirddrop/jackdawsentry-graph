"""Address-targeted on-demand ingest worker.

Polls ``address_ingest_queue`` for pending rows created by
``TraceCompiler.expand()`` when it detects an empty event-store frontier.
For each pending row the worker:

1. Claims the row (status → running) with an advisory lock to prevent
   concurrent workers from double-processing the same address.
2. Fetches recent transaction history via the chain-specific collector's
   ``get_address_transactions()`` method.
3. Persists each transaction and its token transfers to the raw event store.
4. Marks the row completed (or failed after ``MAX_RETRIES`` attempts).

The worker is started as a background task by ``CollectorManager`` alongside
the existing ``EventStoreBackfillWorker``.  It runs on a short polling
interval (default 30 s) so investigator-triggered ingests are completed
within a minute of being queued.

Limitations (MVP):
- ``get_address_transactions`` on EVM collectors does block-scan which is
  slow without an indexing service.  In production, override this method in
  each chain-specific collector with an Alchemy / Etherscan call.
- Only one worker instance should run per process (no distributed locking
  beyond DB advisory locks per row via status CAS).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 30          # seconds between polling cycles
_BATCH_SIZE = 5              # max rows per cycle
_MAX_RETRIES = 3             # mark as failed after this many attempts
_TX_FETCH_LIMIT = 200        # max transactions to fetch per address ingest
_RUNNING_STALE_INTERVAL = 300  # seconds (5 minutes) after which running rows are considered stale


class AddressIngestWorker:
    """Background worker that processes on-demand address ingest requests.

    Args:
        collectors: Chain-name → collector mapping from CollectorManager.
        poll_interval: Seconds between polling cycles (default 30).
    """

    def __init__(self, collectors: Dict[str, Any], poll_interval: int = _POLL_INTERVAL):
        self.collectors = collectors
        self.poll_interval = poll_interval
        self.is_running = False

    async def start(self) -> None:
        """Run the polling loop until stopped."""
        self.is_running = True
        logger.info("AddressIngestWorker started (poll=%ds)", self.poll_interval)
        while self.is_running:
            try:
                await self._run_cycle()
            except Exception as exc:
                logger.error("AddressIngestWorker cycle failed: %s", exc)
            await asyncio.sleep(self.poll_interval)

    async def stop(self) -> None:
        """Signal the worker to exit on the next loop iteration."""
        self.is_running = False

    async def _run_cycle(self) -> None:
        """Claim and process up to _BATCH_SIZE pending rows."""
        from src.api.database import get_postgres_connection

        async with get_postgres_connection() as conn:
            rows = await conn.fetch(
                f"""
                UPDATE address_ingest_queue
                SET status = 'running', started_at = NOW()
                WHERE id IN (
                    SELECT id FROM address_ingest_queue
                    WHERE status = 'pending'
                      AND (next_retry_at IS NULL OR next_retry_at <= NOW())
                       OR (status = 'running' AND started_at <= NOW() - INTERVAL '{_RUNNING_STALE_INTERVAL} seconds')
                    ORDER BY priority DESC, requested_at ASC
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id, address, blockchain, retry_count
                """,
                _BATCH_SIZE,
            )

        for row in rows:
            try:
                await self._process_row(
                    queue_id=row["id"],
                    address=row["address"],
                    chain=row["blockchain"],
                    retry_count=row["retry_count"],
                )
            except Exception as e:
                logger.exception(f"Failed to process address ingest row {row['id']}: {e}")
                # Mark the row as failed so it can be retried
                await self._mark_failed(row["id"], str(e), row["retry_count"])

    async def _process_row(
        self, queue_id: int, address: str, chain: str, retry_count: int
    ) -> None:
        """Fetch and store transactions for one queued address."""
        from src.api.database import get_postgres_connection

        collector = self.collectors.get(chain)
        if collector is None:
            logger.warning(
                "No collector available for chain=%s (id=%s)", chain, queue_id
            )
            await self._mark_failed(queue_id, "no collector for chain", retry_count)
            return

        try:
            transactions = await collector.get_address_transactions(
                address, limit=_TX_FETCH_LIMIT
            )
        except Exception as exc:
            logger.warning(
                "get_address_transactions failed addr=%s chain=%s: %s",
                address,
                chain,
                exc,
            )
            await self._mark_failed(queue_id, str(exc), retry_count)
            return

        tx_count = 0
        for tx in transactions:
            try:
                await collector.normalize_token_transfers(tx)
                # Reuse the base collector's dual-write methods — they already
                # apply ON CONFLICT DO NOTHING so re-ingest is safe.
                await collector._insert_raw_transaction(tx)
                await collector._insert_raw_token_transfers(tx)
                # Write DEX Swap event logs when the collector has populated
                # them (e.g. TronCollector with raw_evm_logs_tron, migration 013).
                if getattr(tx, "dex_logs", None):
                    await collector._insert_raw_evm_logs(tx)
                tx_count += 1
            except Exception as exc:
                logger.debug(
                    "Failed to persist tx %s for %s/%s: %s",
                    getattr(tx, "hash", "?"),
                    address,
                    chain,
                    exc,
                )

        await self._mark_completed(queue_id, tx_count)
        logger.info(
            "On-demand ingest complete: addr=%s chain=%s tx_count=%d",
            address,
            chain,
            tx_count,
        )

    async def _mark_completed(self, queue_id: int, tx_count: int) -> None:
        """Set status=completed for a successfully processed row."""
        from src.api.database import get_postgres_connection

        try:
            async with get_postgres_connection() as conn:
                await conn.execute(
                    """
                    UPDATE address_ingest_queue
                    SET status = 'completed',
                        completed_at = $1,
                        tx_count = $2
                    WHERE id = $3
                    """,
                    datetime.now(timezone.utc),
                    tx_count,
                    queue_id,
                )
        except Exception as exc:
            logger.error("Failed to mark id=%s completed: %s", queue_id, exc, exc_info=True)
            raise

    async def _mark_failed(
        self, queue_id: int, error: str, retry_count: int
    ) -> None:
        """Increment retry_count or mark permanently failed after MAX_RETRIES."""
        from src.api.database import get_postgres_connection

        try:
            async with get_postgres_connection() as conn:
                if retry_count >= _MAX_RETRIES - 1:
                    await conn.execute(
                        """
                        UPDATE address_ingest_queue
                        SET status = 'failed',
                            error = $1,
                            retry_count = retry_count + 1
                        WHERE id = $2
                        """,
                        error[:500],
                        queue_id,
                    )
                else:
                    # Exponential back-off: 2^retry_count minutes.
                    backoff_minutes = 2 ** retry_count
                    await conn.execute(
                        """
                        UPDATE address_ingest_queue
                        SET status = 'pending',
                            error = $1,
                            retry_count = retry_count + 1,
                            next_retry_at = NOW() + ($2 || ' minutes')::INTERVAL
                        WHERE id = $3
                        """,
                        error[:500],
                        str(backoff_minutes),
                        queue_id,
                    )
        except Exception as exc:
            logger.error("Failed to mark id=%s failed: %s", queue_id, exc, exc_info=True)
            raise
