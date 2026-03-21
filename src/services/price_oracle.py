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
from typing import Dict
from typing import List
from typing import Optional

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


class PriceOracle:
    """Async CoinGecko price oracle with in-memory TTL cache.

    Args:
        api_key: Optional CoinGecko Pro API key.  If provided, included as
                 the ``x-cg-pro-api-key`` header.  If absent, the free tier
                 is used (rate-limited to ~30 req/min).
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key
        # Flat cache: canonical_asset_id -> (price_usd, fetched_at_epoch)
        self._cache: Dict[str, tuple[float, float]] = {}
        self._lock = asyncio.Lock()

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

        for aid in asset_ids:
            entry = self._cache.get(aid)
            if entry is not None and (now - entry[1]) < _CACHE_TTL_SECONDS:
                result[aid] = entry[0]
            else:
                stale.append(aid)
                result[aid] = None  # pre-populate with None; overwritten on hit

        if stale:
            fetched = await self._fetch_from_coingecko(stale)
            now_refetch = time.monotonic()
            for aid, price in fetched.items():
                if price is not None:
                    result[aid] = price
                    self._cache[aid] = (price, now_refetch)

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
        headers: Dict[str, str] = {}
        if self._api_key:
            headers["x-cg-pro-api-key"] = self._api_key

        async with self._lock:
            for batch_start in range(0, len(asset_ids), _MAX_IDS_PER_CALL):
                batch = asset_ids[batch_start: batch_start + _MAX_IDS_PER_CALL]
                ids_param = ",".join(batch)
                url = (
                    f"{_COINGECKO_BASE}/simple/price"
                    f"?ids={ids_param}&vs_currencies=usd"
                )
                try:
                    async with aiohttp.ClientSession(headers=headers) as session:
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
        _singleton = PriceOracle(api_key=key)
    return _singleton
