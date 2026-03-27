"""Historical token metadata backfill worker.

Continuously scans distinct token contracts/mints already observed in
``raw_token_transfers`` and incrementally resolves them into
``token_metadata_cache``. This lets the explorer improve token labels over
time without blocking investigators on ad hoc lookups.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from typing import Dict
from typing import Optional

from src.api.config import settings
from src.api.database import get_postgres_connection
from src.services.token_metadata import TokenMetadataRecord
from src.services.token_metadata import get_token_metadata_cache

logger = logging.getLogger(__name__)


class TokenMetadataBackfillWorker:
    """Refresh token metadata for assets already seen in the raw event store."""

    def __init__(
        self,
        collectors: Dict[str, Any],
        *,
        poll_interval: int = settings.TOKEN_METADATA_BACKFILL_INTERVAL_SECONDS,
        batch_size: int = settings.TOKEN_METADATA_BACKFILL_BATCH_SIZE,
    ) -> None:
        self.collectors = collectors
        self.poll_interval = max(10, poll_interval)
        self.batch_size = max(1, batch_size)
        self.is_running = False
        self._cache = get_token_metadata_cache()

    async def start(self) -> None:
        """Run the backfill loop until stopped."""
        self.is_running = True
        logger.info(
            "TokenMetadataBackfillWorker started (poll=%ds batch=%d)",
            self.poll_interval,
            self.batch_size,
        )
        while self.is_running:
            try:
                await self._run_cycle()
            except Exception as exc:
                logger.error("Token metadata backfill cycle failed: %s", exc, exc_info=True)
            await asyncio.sleep(self.poll_interval)

    async def stop(self) -> None:
        """Signal the worker to stop on the next polling boundary."""
        self.is_running = False

    async def _run_cycle(self) -> None:
        candidates = await self._fetch_candidates()
        if not candidates:
            return

        refreshed = 0
        for candidate in candidates:
            if not self.is_running:
                break
            if await self._process_candidate(candidate):
                refreshed += 1

        logger.info(
            "Token metadata backfill cycle processed %d/%d candidates",
            refreshed,
            len(candidates),
        )

    async def _fetch_candidates(self) -> list[dict[str, Any]]:
        active_chains = sorted({chain for chain in self.collectors.keys() if chain})
        if not active_chains:
            return []

        async with get_postgres_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    rt.blockchain,
                    rt.asset_contract AS asset_address,
                    MAX(rt.timestamp) AS last_seen_at,
                    MAX(NULLIF(rt.asset_symbol, '')) AS seed_symbol,
                    MAX(NULLIF(rt.canonical_asset_id, '')) AS seed_canonical_asset_id,
                    tmc.resolve_status,
                    tmc.next_refresh_at
                FROM raw_token_transfers rt
                LEFT JOIN token_metadata_cache tmc
                  ON tmc.blockchain = rt.blockchain
                 AND tmc.asset_address = rt.asset_contract
                WHERE rt.blockchain = ANY($1::text[])
                  AND rt.asset_contract IS NOT NULL
                  AND rt.asset_contract <> ''
                GROUP BY
                    rt.blockchain,
                    rt.asset_contract,
                    tmc.resolve_status,
                    tmc.next_refresh_at,
                    tmc.asset_address
                HAVING tmc.asset_address IS NULL
                    OR tmc.next_refresh_at IS NULL
                    OR tmc.next_refresh_at <= NOW()
                ORDER BY
                    MAX(rt.timestamp) DESC,
                    rt.blockchain ASC,
                    rt.asset_contract ASC
                LIMIT $2
                """,
                active_chains,
                self.batch_size,
            )
        return [dict(row) for row in rows]

    async def _process_candidate(self, candidate: dict[str, Any]) -> bool:
        blockchain = str(candidate.get("blockchain") or "").strip().lower()
        asset_address = str(candidate.get("asset_address") or "").strip()
        if not blockchain or not asset_address:
            return False

        collector = self.collectors.get(blockchain)
        if collector is None:
            logger.debug(
                "Skipping token metadata backfill for %s/%s: no collector available",
                blockchain,
                asset_address,
            )
            return False

        seed_symbol = self._clean_seed_symbol(
            candidate.get("seed_symbol"),
            asset_address=asset_address,
        )
        seed = TokenMetadataRecord(
            blockchain=blockchain,
            asset_address=asset_address,
            symbol=seed_symbol,
            canonical_asset_id=candidate.get("seed_canonical_asset_id"),
            token_standard=collector._default_token_asset_type(),
            source="event_store_seed",
            last_seen_at=self._as_datetime(candidate.get("last_seen_at")),
        )

        async def resolver() -> Optional[TokenMetadataRecord]:
            return await collector._fetch_token_metadata(asset_address)

        record = await self._cache.refresh_metadata(
            blockchain,
            asset_address,
            resolver=resolver,
            seed=seed,
        )
        logger.debug(
            "Token metadata refreshed for %s/%s -> status=%s symbol=%s canonical=%s",
            blockchain,
            asset_address,
            record.resolve_status,
            record.symbol,
            record.canonical_asset_id,
        )
        return True

    @staticmethod
    def _as_datetime(value: Any) -> Optional[datetime]:
        return value if isinstance(value, datetime) else None

    @staticmethod
    def _clean_seed_symbol(value: Any, *, asset_address: str) -> Optional[str]:
        if not isinstance(value, str):
            return None
        symbol = value.strip()
        if not symbol:
            return None

        lowered_symbol = symbol.lower()
        lowered_address = asset_address.lower()
        if lowered_symbol == lowered_address:
            return None
        if "..." in symbol:
            return None
        if len(symbol) > 16 and lowered_symbol in lowered_address:
            return None
        return symbol
