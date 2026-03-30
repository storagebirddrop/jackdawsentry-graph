"""
Jackdaw Sentry - Raw event-store bootstrap backfill worker.

Uses already-initialized collectors to backfill a recent window of historical
blocks into PostgreSQL ``raw_*`` tables. Progress is checkpointed in Postgres so
fresh installs and redeploys resume automatically.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict
from typing import Iterable

from src.api.config import settings
from src.api.database import get_postgres_connection

logger = logging.getLogger(__name__)

DEFAULT_BACKFILL_WINDOWS = {
    "ethereum": 50_000,
    "bitcoin": 2_000,
    "solana": 20_000,
    "tron": 20_000,
    "xrp": 10_000,
    "bsc": 50_000,
    "polygon": 50_000,
    "arbitrum": 30_000,
    "base": 30_000,
    "avalanche": 30_000,
    "optimism": 30_000,
    "starknet": 10_000,
    "injective": 10_000,
    "cosmos": 10_000,
    "sui": 10_000,
}

BACKFILL_PRIORITY = [
    "ethereum",
    "bitcoin",
    "bsc",
    "polygon",
    "arbitrum",
    "base",
    "optimism",
    "avalanche",
    "solana",
    "tron",
    "xrp",
    "starknet",
    "injective",
    "cosmos",
    "sui",
]


class EventStoreBackfillWorker:
    """Background worker that backfills recent history into raw event tables."""

    def __init__(self, collectors: Dict[str, Any]):
        self.collectors = collectors
        self.interval_seconds = settings.BACKFILL_INTERVAL_SECONDS
        self.batch_size = settings.BACKFILL_BLOCK_BATCH_SIZE
        self.max_chains_per_cycle = settings.BACKFILL_CHAINS_PER_CYCLE
        self.block_timeout_seconds = settings.BACKFILL_BLOCK_TIMEOUT_SECONDS
        self.is_running = False

    async def start(self):
        """Run the backfill loop until stopped."""
        if not settings.AUTO_BACKFILL_RAW_EVENT_STORE:
            logger.info("Raw event-store bootstrap backfill is disabled")
            return

        if not settings.DUAL_WRITE_RAW_EVENT_STORE:
            logger.info(
                "Raw event-store bootstrap backfill skipped because dual-write is disabled"
            )
            return

        self.is_running = True
        logger.info("Starting raw event-store bootstrap backfill worker...")

        while self.is_running:
            try:
                await self.run_cycle()
            except Exception as exc:
                logger.error("Event-store bootstrap backfill cycle failed: %s", exc)
            await asyncio.sleep(self.interval_seconds)

    async def stop(self):
        """Stop the worker on the next loop iteration."""
        self.is_running = False

    async def run_cycle(self):
        """Run a single backfill cycle across a subset of chains."""
        processed = 0

        for blockchain in self._ordered_chains(self.collectors):
            if processed >= self.max_chains_per_cycle:
                break

            collector = self.collectors.get(blockchain)
            if collector is None or not getattr(collector, "is_running", False):
                continue

            if blockchain == "lightning":
                continue  # public Lightning fallback already emits current state

            state = await self._ensure_chain_state(blockchain, collector)
            if not state or state["status"] == "completed":
                continue

            logger.info(
                "Backfill cycle starting for %s at block %s (target=%s)",
                blockchain,
                state["next_block"],
                state["target_block"],
            )
            await self._backfill_chain(blockchain, collector, state)
            processed += 1

    def _ordered_chains(self, collectors: Dict[str, Any]) -> Iterable[str]:
        """Yield collectors ordered by bootstrap priority."""
        seen = set()
        for chain in BACKFILL_PRIORITY:
            if chain in collectors:
                seen.add(chain)
                yield chain
        for chain in sorted(collectors):
            if chain not in seen:
                yield chain

    def _window_for_chain(self, blockchain: str) -> int:
        """Return the recent-history window to bootstrap for a chain."""
        return DEFAULT_BACKFILL_WINDOWS.get(blockchain, 10_000)

    async def _ensure_chain_state(self, blockchain: str, collector: Any) -> Dict[str, Any] | None:
        """Create or refresh a chain's bootstrap state row."""
        latest_block = await collector.get_latest_block_number()
        if latest_block <= 0:
            return None

        async with get_postgres_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT blockchain, status, latest_observed_block, target_block, next_block,
                       attempted_blocks, attempted_transactions, last_error
                FROM event_store_backfill_state
                WHERE blockchain = $1
                """,
                blockchain,
            )

            if row is None:
                target_block = max(0, latest_block - self._window_for_chain(blockchain))
                await conn.execute(
                    """
                    INSERT INTO event_store_backfill_state (
                        blockchain, status, latest_observed_block,
                        target_block, next_block, started_at, updated_at
                    ) VALUES ($1, 'running', $2, $3, $4, NOW(), NOW())
                    """,
                    blockchain,
                    latest_block,
                    target_block,
                    latest_block,
                )
                return {
                    "blockchain": blockchain,
                    "status": "running",
                    "latest_observed_block": latest_block,
                    "target_block": target_block,
                    "next_block": latest_block,
                    "attempted_blocks": 0,
                    "attempted_transactions": 0,
                    "last_error": None,
                }

            await conn.execute(
                """
                UPDATE event_store_backfill_state
                SET latest_observed_block = GREATEST(COALESCE(latest_observed_block, 0), $2),
                    updated_at = NOW()
                WHERE blockchain = $1
                """,
                blockchain,
                latest_block,
            )

        return {
            "blockchain": row["blockchain"],
            "status": row["status"],
            "latest_observed_block": max(row["latest_observed_block"] or 0, latest_block),
            "target_block": row["target_block"],
            "next_block": row["next_block"],
            "attempted_blocks": row["attempted_blocks"],
            "attempted_transactions": row["attempted_transactions"],
            "last_error": row["last_error"],
        }

    async def _backfill_chain(self, blockchain: str, collector: Any, state: Dict[str, Any]):
        """Backfill a small descending block range for a chain."""
        next_block = int(state["next_block"])
        target_block = int(state["target_block"])

        if next_block < target_block:
            await self._mark_completed(blockchain)
            return

        batch_end = max(target_block, next_block - self.batch_size + 1)
        attempted_blocks = 0
        attempted_transactions = 0
        current_next_block = next_block

        try:
            for block_number in range(next_block, batch_end - 1, -1):
                try:
                    block_attempted_transactions = await asyncio.wait_for(
                        self._backfill_single_block(collector, block_number),
                        timeout=self.block_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    await self._update_state(
                        blockchain,
                        status="running",
                        next_block=block_number - 1,
                        attempted_blocks=0,
                        attempted_transactions=0,
                        last_error=(
                            f"Timed out backfilling block {block_number} "
                            f"after {self.block_timeout_seconds}s"
                        ),
                        completed=False,
                    )
                    logger.warning(
                        "Timed out backfilling block %s for %s after %ss; "
                        "skipping to next block",
                        block_number,
                        blockchain,
                        self.block_timeout_seconds,
                    )
                    current_next_block = block_number - 1
                    continue

                attempted_blocks += 1
                attempted_transactions += block_attempted_transactions
                current_next_block = block_number - 1

                await self._update_state(
                    blockchain,
                    status="running",
                    next_block=current_next_block,
                    attempted_blocks=1,
                    attempted_transactions=block_attempted_transactions,
                    last_error=None,
                    completed=False,
                )

                logger.info(
                    "Backfilled block %s for %s (%s txs attempted); next block=%s",
                    block_number,
                    blockchain,
                    block_attempted_transactions,
                    current_next_block,
                )

            completed = current_next_block < target_block
            if completed:
                await self._update_state(
                    blockchain,
                    status="completed",
                    next_block=current_next_block,
                    attempted_blocks=0,
                    attempted_transactions=0,
                    last_error=None,
                    completed=True,
                )

            if attempted_blocks:
                logger.info(
                    "Backfilled %s blocks (%s txs attempted) for %s; next block=%s",
                    attempted_blocks,
                    attempted_transactions,
                    blockchain,
                    current_next_block,
                )
        except Exception as exc:
            await self._update_state(
                blockchain,
                status="error",
                next_block=current_next_block,
                attempted_blocks=0,
                attempted_transactions=0,
                last_error=str(exc),
                completed=False,
            )
            logger.error("Backfill failed for %s: %s", blockchain, exc)

    async def _backfill_single_block(self, collector: Any, block_number: int) -> int:
        """Backfill a single block and return attempted transaction count.

        Uses ``backfill_block`` when available (EVM collectors) which fetches
        the full block and receipts concurrently — essential for dense Ethereum
        blocks with 300-600+ transactions where serial fetching exceeds any
        reasonable timeout.  Falls back to the serial hash-by-hash path for
        collectors that don't implement ``backfill_block``.
        """
        if hasattr(collector, "backfill_block"):
            transactions = await collector.backfill_block(block_number)
        else:
            tx_hashes = await collector.get_block_transactions(block_number)
            transactions = []
            for tx_hash in tx_hashes:
                tx = await collector.get_transaction(tx_hash)
                if tx:
                    transactions.append(tx)

        block_attempted_transactions = 0
        for tx in transactions:
            await collector.normalize_token_transfers(tx)
            await collector._insert_raw_transaction(tx)
            if tx.token_transfers:
                await collector._insert_raw_token_transfers(tx)
            if tx.inputs:
                await collector._insert_raw_utxo_inputs(tx)
            if tx.outputs:
                await collector._insert_raw_utxo_outputs(tx)
            if tx.blockchain == "solana":
                await collector._insert_raw_solana_instructions(tx)
            block_attempted_transactions += 1

        return block_attempted_transactions

    async def _mark_completed(self, blockchain: str):
        """Mark a chain as fully backfilled for the configured window."""
        await self._update_state(
            blockchain,
            status="completed",
            next_block=-1,
            attempted_blocks=0,
            attempted_transactions=0,
            last_error=None,
            completed=True,
        )

    async def _update_state(
        self,
        blockchain: str,
        *,
        status: str,
        next_block: int,
        attempted_blocks: int,
        attempted_transactions: int,
        last_error: str | None,
        completed: bool,
    ):
        """Persist backfill progress."""
        async with get_postgres_connection() as conn:
            await conn.execute(
                """
                UPDATE event_store_backfill_state
                SET status = $2,
                    next_block = $3,
                    attempted_blocks = attempted_blocks + $4,
                    attempted_transactions = attempted_transactions + $5,
                    last_error = $6,
                    updated_at = NOW(),
                    completed_at = CASE
                        WHEN $7 THEN COALESCE(completed_at, NOW())
                        ELSE NULL
                    END
                WHERE blockchain = $1
                """,
                blockchain,
                status,
                next_block,
                attempted_blocks,
                attempted_transactions,
                last_error,
                completed,
            )
