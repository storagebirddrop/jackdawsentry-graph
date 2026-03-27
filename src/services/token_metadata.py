"""Token metadata cache with Redis L1 + Postgres L2 persistence.

Collectors use this service as a read-through cache for token symbol/name/
decimals resolution. Cache misses are fetched inline via a collector-provided
resolver callback. Stale hits are returned immediately and refreshed in the
background.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Dict
from typing import Optional

from src.api.config import settings
from src.api.database import get_postgres_connection
from src.api.database import get_redis_connection

logger = logging.getLogger(__name__)

ResolverFn = Callable[[], Awaitable[Optional["TokenMetadataRecord"]]]

_EVM_LIKE_CHAINS = {
    "ethereum",
    "bsc",
    "polygon",
    "arbitrum",
    "base",
    "avalanche",
    "optimism",
    "starknet",
    "injective",
    "sei",
    "plasma",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_asset_address(blockchain: str, asset_address: str) -> str:
    value = (asset_address or "").strip()
    if not value:
        return value
    if blockchain in _EVM_LIKE_CHAINS or value.startswith("0x"):
        return value.lower()
    return value


def _cache_key(blockchain: str, asset_address: str) -> str:
    return f"token_metadata:{blockchain}:{asset_address}"


@dataclass
class TokenMetadataRecord:
    """Canonical cached metadata for one chain-specific token address."""

    blockchain: str
    asset_address: str
    symbol: Optional[str] = None
    name: Optional[str] = None
    decimals: Optional[int] = None
    metadata_uri: Optional[str] = None
    token_standard: Optional[str] = None
    canonical_asset_id: Optional[str] = None
    source: str = "unknown"
    resolve_status: str = "resolved"
    lookup_attempts: int = 0
    last_error: Optional[str] = None
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    last_refreshed_at: Optional[datetime] = None
    next_refresh_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def is_refresh_due(self, now: Optional[datetime] = None) -> bool:
        """Return True when this cache entry should be refreshed."""
        now = now or _utcnow()
        if self.next_refresh_at is None:
            return True
        return now >= self.next_refresh_at

    def with_defaults(self) -> "TokenMetadataRecord":
        """Ensure timestamps and normalized address fields are populated."""
        now = _utcnow()
        self.asset_address = _normalize_asset_address(self.blockchain, self.asset_address)
        if self.first_seen_at is None:
            self.first_seen_at = now
        if self.last_seen_at is None:
            self.last_seen_at = now
        if self.updated_at is None:
            self.updated_at = now
        return self


class TokenMetadataCache:
    """Redis + Postgres-backed token metadata cache."""

    def __init__(self) -> None:
        self._process_cache: Dict[str, TokenMetadataRecord] = {}
        self._refresh_tasks: Dict[str, asyncio.Task[Any]] = {}

    async def get_metadata(
        self,
        blockchain: str,
        asset_address: str,
        *,
        resolver: Optional[ResolverFn] = None,
        seed: Optional[TokenMetadataRecord] = None,
    ) -> Optional[TokenMetadataRecord]:
        """Return cached metadata for a token, resolving it on miss when needed."""
        chain = (blockchain or "").strip().lower()
        address = _normalize_asset_address(chain, asset_address)
        if not chain or not address:
            return None

        key = _cache_key(chain, address)
        cached = self._process_cache.get(key)
        if cached is None:
            cached = await self._redis_get(chain, address)
        if cached is None:
            cached = await self._postgres_get(chain, address)
            if cached is not None:
                await self._redis_set(cached)

        if cached is not None:
            merged = self._merge_records(cached, seed)
            if merged != cached:
                await self._upsert_record(merged)
                await self._redis_set(merged)
                cached = merged
            self._process_cache[key] = cached
            if resolver is not None and cached.is_refresh_due():
                self._schedule_refresh(cached, resolver, seed=seed)
            return cached

        if seed is not None and resolver is None:
            stored = await self._store_seed(chain, address, seed)
            self._process_cache[key] = stored
            return stored

        if resolver is None:
            return seed.with_defaults() if seed is not None else None

        refreshed = await self.refresh_metadata(
            chain,
            address,
            resolver=resolver,
            seed=seed,
        )
        self._process_cache[key] = refreshed
        return refreshed

    async def refresh_metadata(
        self,
        blockchain: str,
        asset_address: str,
        *,
        resolver: ResolverFn,
        seed: Optional[TokenMetadataRecord] = None,
        existing: Optional[TokenMetadataRecord] = None,
    ) -> TokenMetadataRecord:
        """Refresh one token metadata record immediately."""
        chain = (blockchain or "").strip().lower()
        address = _normalize_asset_address(chain, asset_address)
        base = existing or await self._postgres_get(chain, address)
        if base is not None:
            base = self._merge_records(base, seed)
        elif seed is not None:
            base = self._store_seed_defaults(chain, address, seed)

        now = _utcnow()
        lookup_attempts = (base.lookup_attempts if base is not None else 0) + 1

        resolved: Optional[TokenMetadataRecord] = None
        last_error: Optional[str] = None
        try:
            resolved = await resolver()
        except Exception as exc:
            last_error = str(exc)
            logger.debug("Token metadata resolver failed for %s/%s: %s", chain, address, exc)

        merged = self._merge_records(base, resolved)
        if merged is None:
            merged = TokenMetadataRecord(blockchain=chain, asset_address=address)

        merged.blockchain = chain
        merged.asset_address = address
        merged.lookup_attempts = lookup_attempts
        merged.last_seen_at = now
        merged.last_refreshed_at = now
        merged.updated_at = now
        merged.first_seen_at = (base.first_seen_at if base else now) or now

        if self._is_resolved_record(merged):
            merged.resolve_status = "resolved"
            merged.last_error = None
            merged.next_refresh_at = now + timedelta(
                seconds=settings.TOKEN_METADATA_REFRESH_INTERVAL_SECONDS
            )
        else:
            merged.resolve_status = "error" if last_error else "missing"
            merged.last_error = last_error
            merged.next_refresh_at = now + timedelta(
                seconds=self._error_backoff_seconds(lookup_attempts)
            )

        await self._upsert_record(merged)
        await self._redis_set(merged)
        self._process_cache[_cache_key(chain, address)] = merged
        return merged

    def _schedule_refresh(
        self,
        record: TokenMetadataRecord,
        resolver: ResolverFn,
        *,
        seed: Optional[TokenMetadataRecord] = None,
    ) -> None:
        """Refresh a stale record in the background if no task is running."""
        key = _cache_key(record.blockchain, record.asset_address)
        task = self._refresh_tasks.get(key)
        if task is not None and not task.done():
            return

        async def _runner() -> None:
            try:
                await self.refresh_metadata(
                    record.blockchain,
                    record.asset_address,
                    resolver=resolver,
                    seed=seed,
                    existing=record,
                )
            except Exception:
                logger.debug(
                    "Background token metadata refresh failed for %s/%s",
                    record.blockchain,
                    record.asset_address,
                    exc_info=True,
                )
            finally:
                self._refresh_tasks.pop(key, None)

        self._refresh_tasks[key] = asyncio.create_task(_runner())

    async def _store_seed(
        self,
        blockchain: str,
        asset_address: str,
        seed: TokenMetadataRecord,
    ) -> TokenMetadataRecord:
        """Persist a seed-only record when no resolver is available."""
        stored = self._store_seed_defaults(blockchain, asset_address, seed)
        await self._upsert_record(stored)
        await self._redis_set(stored)
        return stored

    def _store_seed_defaults(
        self,
        blockchain: str,
        asset_address: str,
        seed: TokenMetadataRecord,
    ) -> TokenMetadataRecord:
        now = _utcnow()
        stored = self._merge_records(
            TokenMetadataRecord(blockchain=blockchain, asset_address=asset_address),
            seed,
        ) or TokenMetadataRecord(blockchain=blockchain, asset_address=asset_address)
        stored.blockchain = blockchain
        stored.asset_address = asset_address
        stored.lookup_attempts = max(stored.lookup_attempts, 1)
        stored.last_seen_at = now
        stored.last_refreshed_at = now
        stored.updated_at = now
        stored.first_seen_at = stored.first_seen_at or now
        stored.resolve_status = "resolved" if self._is_resolved_record(stored) else "missing"
        stored.next_refresh_at = now + timedelta(
            seconds=settings.TOKEN_METADATA_REFRESH_INTERVAL_SECONDS
        )
        return stored

    def _merge_records(
        self,
        base: Optional[TokenMetadataRecord],
        patch: Optional[TokenMetadataRecord],
    ) -> Optional[TokenMetadataRecord]:
        """Merge two token metadata records, preferring non-empty patch values."""
        if base is None:
            return patch.with_defaults() if patch is not None else None
        if patch is None:
            return base

        merged = TokenMetadataRecord(**asdict(base))
        for field_name in (
            "symbol",
            "name",
            "decimals",
            "metadata_uri",
            "token_standard",
            "canonical_asset_id",
            "source",
            "resolve_status",
            "lookup_attempts",
            "last_error",
            "first_seen_at",
            "last_seen_at",
            "last_refreshed_at",
            "next_refresh_at",
            "updated_at",
        ):
            value = getattr(patch, field_name)
            if value is not None and value != "":
                setattr(merged, field_name, value)
        return merged.with_defaults()

    def _is_resolved_record(self, record: TokenMetadataRecord) -> bool:
        """Return True when the record carries useful resolved metadata."""
        return bool(
            record.symbol
            or record.name
            or record.decimals is not None
            or record.metadata_uri
        )

    def _error_backoff_seconds(self, lookup_attempts: int) -> int:
        """Return exponential backoff for missing/failed lookups."""
        base = max(1, settings.TOKEN_METADATA_ERROR_BACKOFF_SECONDS)
        return min(
            settings.TOKEN_METADATA_MAX_ERROR_BACKOFF_SECONDS,
            base * (2 ** max(0, lookup_attempts - 1)),
        )

    async def _postgres_get(
        self,
        blockchain: str,
        asset_address: str,
    ) -> Optional[TokenMetadataRecord]:
        try:
            async with get_postgres_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        blockchain,
                        asset_address,
                        symbol,
                        name,
                        decimals,
                        metadata_uri,
                        token_standard,
                        canonical_asset_id,
                        source,
                        resolve_status,
                        lookup_attempts,
                        last_error,
                        first_seen_at,
                        last_seen_at,
                        last_refreshed_at,
                        next_refresh_at,
                        updated_at
                    FROM token_metadata_cache
                    WHERE blockchain = $1 AND asset_address = $2
                    """,
                    blockchain,
                    asset_address,
                )
        except Exception as exc:
            logger.debug(
                "Token metadata Postgres lookup failed for %s/%s: %s",
                blockchain,
                asset_address,
                exc,
            )
            return None

        if row is None:
            return None
        return TokenMetadataRecord(**dict(row)).with_defaults()

    async def _upsert_record(self, record: TokenMetadataRecord) -> None:
        """Persist one token metadata record to Postgres."""
        record = record.with_defaults()
        try:
            async with get_postgres_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO token_metadata_cache (
                        blockchain,
                        asset_address,
                        symbol,
                        name,
                        decimals,
                        metadata_uri,
                        token_standard,
                        canonical_asset_id,
                        source,
                        resolve_status,
                        lookup_attempts,
                        last_error,
                        first_seen_at,
                        last_seen_at,
                        last_refreshed_at,
                        next_refresh_at,
                        updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9,
                        $10, $11, $12, $13, $14, $15, $16, $17
                    )
                    ON CONFLICT (blockchain, asset_address) DO UPDATE SET
                        symbol = COALESCE(EXCLUDED.symbol, token_metadata_cache.symbol),
                        name = COALESCE(EXCLUDED.name, token_metadata_cache.name),
                        decimals = COALESCE(EXCLUDED.decimals, token_metadata_cache.decimals),
                        metadata_uri = COALESCE(EXCLUDED.metadata_uri, token_metadata_cache.metadata_uri),
                        token_standard = COALESCE(EXCLUDED.token_standard, token_metadata_cache.token_standard),
                        canonical_asset_id = COALESCE(EXCLUDED.canonical_asset_id, token_metadata_cache.canonical_asset_id),
                        source = COALESCE(EXCLUDED.source, token_metadata_cache.source),
                        resolve_status = EXCLUDED.resolve_status,
                        lookup_attempts = EXCLUDED.lookup_attempts,
                        last_error = EXCLUDED.last_error,
                        last_seen_at = EXCLUDED.last_seen_at,
                        last_refreshed_at = EXCLUDED.last_refreshed_at,
                        next_refresh_at = EXCLUDED.next_refresh_at,
                        updated_at = EXCLUDED.updated_at
                    """,
                    record.blockchain,
                    record.asset_address,
                    record.symbol,
                    record.name,
                    record.decimals,
                    record.metadata_uri,
                    record.token_standard,
                    record.canonical_asset_id,
                    record.source,
                    record.resolve_status,
                    record.lookup_attempts,
                    record.last_error,
                    record.first_seen_at,
                    record.last_seen_at,
                    record.last_refreshed_at,
                    record.next_refresh_at,
                    record.updated_at,
                )
        except Exception as exc:
            logger.debug(
                "Token metadata Postgres upsert failed for %s/%s: %s",
                record.blockchain,
                record.asset_address,
                exc,
            )

    async def _redis_get(
        self,
        blockchain: str,
        asset_address: str,
    ) -> Optional[TokenMetadataRecord]:
        try:
            async with get_redis_connection() as redis:
                raw = await redis.get(_cache_key(blockchain, asset_address))
        except Exception as exc:
            logger.debug(
                "Token metadata Redis lookup failed for %s/%s: %s",
                blockchain,
                asset_address,
                exc,
            )
            return None

        if not raw:
            return None

        try:
            payload = json.loads(raw)
            for field_name in (
                "first_seen_at",
                "last_seen_at",
                "last_refreshed_at",
                "next_refresh_at",
                "updated_at",
            ):
                if payload.get(field_name):
                    payload[field_name] = datetime.fromisoformat(payload[field_name])
            return TokenMetadataRecord(**payload).with_defaults()
        except Exception:
            logger.debug(
                "Token metadata Redis payload malformed for %s/%s",
                blockchain,
                asset_address,
                exc_info=True,
            )
            return None

    async def _redis_set(self, record: TokenMetadataRecord) -> None:
        payload = asdict(record.with_defaults())
        for field_name, value in list(payload.items()):
            if isinstance(value, datetime):
                payload[field_name] = value.isoformat()

        try:
            async with get_redis_connection() as redis:
                await redis.setex(
                    _cache_key(record.blockchain, record.asset_address),
                    settings.TOKEN_METADATA_REDIS_TTL_SECONDS,
                    json.dumps(payload),
                )
        except Exception as exc:
            logger.debug(
                "Token metadata Redis set failed for %s/%s: %s",
                record.blockchain,
                record.asset_address,
                exc,
            )


_token_metadata_cache: Optional[TokenMetadataCache] = None


def get_token_metadata_cache() -> TokenMetadataCache:
    """Return the process-global token metadata cache instance."""
    global _token_metadata_cache
    if _token_metadata_cache is None:
        _token_metadata_cache = TokenMetadataCache()
    return _token_metadata_cache
