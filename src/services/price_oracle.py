"""
Standalone CoinGecko price oracle for the Jackdaw Sentry graph product.

Fetches USD spot prices for canonical asset IDs (CoinGecko format, e.g.
``"ethereum"``, ``"tron"``, ``"ripple"``) and caches them in memory to
avoid hammering the free-tier rate limit.

Design:
- Single async ``PriceOracle`` class with ``get_prices_bulk(asset_ids)``
- In-memory TTL cache (``_CACHE_TTL_SECONDS``) — shared across all callers
  in the same process
- Optional Redis secondary cache (not required)
- Returns ``None`` per asset on any error; never raises
- aiohttp is required at runtime; if unavailable the oracle returns None for
  all assets so graph behaviour degrades gracefully

Usage:
    oracle = get_price_oracle()
    prices = await oracle.get_prices_bulk(["ethereum", "tron", "ripple"])
    # {"ethereum": 3200.0, "tron": 0.085, "ripple": 0.52}
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple, Union

try:
    import aiohttp
    _AIOHTTP_AVAILABLE = True
except ImportError:
    aiohttp = None  # type: ignore[assignment]
    _AIOHTTP_AVAILABLE = False

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 900          # 15 minutes — acceptable staleness for compliance use
_COINGECKO_BASE = "https://api.coingecko.com/api/v3"
_REQUEST_TIMEOUT = 8.0            # seconds per HTTP request
_MAX_IDS_PER_CALL = 50            # CoinGecko returns up to 50 ids per call without pagination
_CACHE_VERSION = 1                 # Schema version for cache invalidation


class PriceOracle:
    """Async CoinGecko price oracle with in-memory TTL cache.

    Args:
        api_key:  Optional CoinGecko Pro API key.  If provided, included as
                  the ``x-cg-pro-api-key`` header.  If absent, the free tier
                  is used (rate-limited to ~30 req/min).
        base_url: CoinGecko API base URL.  Defaults to ``_COINGECKO_BASE``.
                  Override to point at a local CoinGecko-compatible mock.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = _COINGECKO_BASE,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        # Flat cache: canonical_asset_id -> (price_usd, fetched_at_monotonic)
        self._cache: Dict[str, Tuple[float, float]] = {}
        # In-flight futures for deduplication: asset_id -> Future
        self._inflight: Dict[str, asyncio.Future] = {}
        self._lock: Optional[asyncio.Lock] = None
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def _lock(self) -> asyncio.Lock:
        """Lazy initialization of asyncio.Lock."""
        if self.__lock is None:
            self.__lock = asyncio.Lock()
        return self.__lock

    @_lock.setter
    def _lock(self, value):
        self.__lock = value

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create a persistent aiohttp session."""
        async with self._lock:
            if self._session is None or self._session.closed:
                headers: Dict[str, str] = {}
                if self._api_key:
                    headers["x-cg-pro-api-key"] = self._api_key
                self._session = aiohttp.ClientSession(headers=headers)
            return self._session

    async def close(self) -> None:
        """Close the persistent session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def get_prices_bulk(
        self,
        asset_ids: List[str],
    ) -> Dict[str, Optional[float]]:
        """Return USD spot prices for the given CoinGecko canonical asset IDs.

        Hits the in-memory cache first; fetches from CoinGecko for any miss.
        Results for all IDs are returned in a single call where possible.

        Args:
            asset_ids: List of CoinGecko canonical IDs (e.g. ``["ethereum"]``).

        Returns:
            Dict mapping each asset_id to its USD price, or ``None`` when the
            price cannot be determined (network error, unknown asset, etc.).
        """
        if not asset_ids:
            return {}

        now = time.monotonic()
        result: Dict[str, Optional[float]] = {}
        stale: List[str] = []
        futures_to_await: List[Tuple[str, asyncio.Future]] = []

        # Lock scope: check cache and register in-flight futures
        async with self._lock:
            for aid in asset_ids:
                entry = self._cache.get(aid)
                if entry is not None and (now - entry[1]) < _CACHE_TTL_SECONDS:
                    result[aid] = entry[0]
                elif aid in self._inflight:
                    # Reuse existing in-flight request
                    futures_to_await.append((aid, self._inflight[aid]))
                    result[aid] = None  # placeholder, will be filled after await
                else:
                    stale.append(aid)
                    result[aid] = None  # pre-populate with None; overwritten on hit

        # Await any in-flight futures outside the lock
        for aid, future in futures_to_await:
            try:
                price = await future
                if price is not None:
                    result[aid] = price
            except Exception as exc:
                logger.debug("In-flight price fetch failed for %s: %s", aid, exc)

        if stale:
            # Create futures for stale assets and fetch outside lock
            async with self._lock:
                # Re-check in case another coroutine started fetching
                to_fetch: List[str] = []
                for aid in stale:
                    if aid in self._inflight:
                        futures_to_await.append((aid, self._inflight[aid]))
                    else:
                        to_fetch.append(aid)
                        self._inflight[aid] = asyncio.get_event_loop().create_future()

            # Fetch prices outside the lock
            if to_fetch:
                fetched = await self._fetch_from_coingecko(to_fetch)
                now_refetch = time.monotonic()

                # Update cache and resolve futures inside lock
                async with self._lock:
                    for aid in to_fetch:
                        price = fetched.get(aid)
                        if aid in self._inflight:
                            future = self._inflight.pop(aid)
                            if not future.done():
                                future.set_result(price)
                        if price is not None:
                            result[aid] = price
                            self._cache[aid] = (price, now_refetch)

            # Await any newly discovered in-flight futures
            for aid, future in futures_to_await:
                if aid not in result or result[aid] is None:
                    try:
                        price = await future
                        if price is not None:
                            result[aid] = price
                    except Exception as exc:
                        logger.debug("In-flight price fetch failed for %s: %s", aid, exc)

        return result

    async def _fetch_from_coingecko(
        self,
        asset_ids: List[str],
    ) -> Dict[str, Optional[float]]:
        """Fetch prices from the CoinGecko /simple/price endpoint.

        Batches IDs into groups of at most ``_MAX_IDS_PER_CALL``.  Returns
        an empty dict (not an error) on any network or parse failure.

        Args:
            asset_ids: IDs to fetch.

        Returns:
            Dict of asset_id → float price (USD); omits IDs not found.
        """
        if aiohttp is None:
            logger.debug("aiohttp not available — price oracle returns None for all assets")
            return {}

        prices: Dict[str, Optional[float]] = {}

        try:
            session = await self._get_session()
            for batch_start in range(0, len(asset_ids), _MAX_IDS_PER_CALL):
                batch = asset_ids[batch_start: batch_start + _MAX_IDS_PER_CALL]
                ids_param = ",".join(batch)
                url = (
                    f"{self._base_url}/simple/price"
                    f"?ids={ids_param}&vs_currencies=usd"
                )
                try:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
                    ) as resp:
                        if resp.status != 200:
                            logger.debug(
                                "CoinGecko returned HTTP %s for ids=%s",
                                resp.status,
                                ids_param[:80],
                            )
                            continue
                        data = await resp.json()
                        for aid in batch:
                            entry = data.get(aid)
                            if entry and isinstance(entry, dict):
                                usd = entry.get("usd")
                                if isinstance(usd, (int, float)):
                                    prices[aid] = float(usd)
                except Exception as exc:
                    logger.debug("CoinGecko fetch failed for batch starting %s: %s", batch_start, exc)
        except Exception as exc:
            logger.debug("CoinGecko session error: %s", exc)

        return prices


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_singleton: Optional[PriceOracle] = None


def get_price_oracle(api_key: Optional[str] = None) -> PriceOracle:
    """Return the process-level price oracle singleton.

    Args:
        api_key: CoinGecko Pro API key.  Only used on first call.

    Returns:
        Shared ``PriceOracle`` instance.
    """
    global _singleton
    if _singleton is None:
        from src.api.config import settings
        key = api_key or getattr(settings, "COINGECKO_API_KEY", None)
        base_url = getattr(settings, "COINGECKO_API_URL", _COINGECKO_BASE)
        _singleton = PriceOracle(api_key=key, base_url=base_url)
    return _singleton
