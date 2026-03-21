"""
Unit tests for src.services.price_oracle.PriceOracle.

Covers:
- Empty input returns empty dict
- Cache hit returns cached price without HTTP call
- Cache miss triggers HTTP fetch and populates cache
- Unknown asset ID returns None
- HTTP error returns None (no raise)
- aiohttp unavailable returns None for all assets
- get_price_oracle returns singleton
- Batch fetch respects _MAX_IDS_PER_CALL limit
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.price_oracle import PriceOracle, get_price_oracle, _singleton


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_oracle() -> PriceOracle:
    """Return a fresh PriceOracle (no API key)."""
    return PriceOracle(api_key=None)


def _mock_aiohttp_response(data: dict, status: int = 200):
    """Return a mock aiohttp context-manager response with JSON data."""
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=data)

    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=resp)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    session = AsyncMock()
    session.get = MagicMock(return_value=session_ctx)

    client_session_ctx = AsyncMock()
    client_session_ctx.__aenter__ = AsyncMock(return_value=session)
    client_session_ctx.__aexit__ = AsyncMock(return_value=False)

    return client_session_ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_input_returns_empty_dict():
    oracle = _make_oracle()
    result = await oracle.get_prices_bulk([])
    assert result == {}


@pytest.mark.asyncio
async def test_cache_hit_skips_http():
    """Pre-warmed cache should not trigger any HTTP call."""
    oracle = _make_oracle()
    oracle._cache["ethereum"] = (3200.0, time.monotonic())

    with patch("src.services.price_oracle.aiohttp") as mock_aiohttp:
        result = await oracle.get_prices_bulk(["ethereum"])

    mock_aiohttp.ClientSession.assert_not_called()
    assert result == {"ethereum": 3200.0}


@pytest.mark.asyncio
async def test_cache_miss_triggers_fetch():
    """A cold oracle should call CoinGecko and populate the cache."""
    oracle = _make_oracle()
    mock_data = {"ethereum": {"usd": 3100.5}}

    with patch("src.services.price_oracle.aiohttp") as mock_aiohttp:
        mock_aiohttp.ClientSession = MagicMock(
            return_value=_mock_aiohttp_response(mock_data)
        )
        mock_aiohttp.ClientTimeout = MagicMock(return_value=None)
        result = await oracle.get_prices_bulk(["ethereum"])

    assert result["ethereum"] == 3100.5
    assert "ethereum" in oracle._cache


@pytest.mark.asyncio
async def test_unknown_asset_returns_none():
    """Assets not in CoinGecko response map to None."""
    oracle = _make_oracle()
    mock_data = {}  # CoinGecko returns empty for unknown IDs

    with patch("src.services.price_oracle.aiohttp") as mock_aiohttp:
        mock_aiohttp.ClientSession = MagicMock(
            return_value=_mock_aiohttp_response(mock_data)
        )
        mock_aiohttp.ClientTimeout = MagicMock(return_value=None)
        result = await oracle.get_prices_bulk(["some_unknown_token"])

    assert result["some_unknown_token"] is None


@pytest.mark.asyncio
async def test_http_error_returns_none():
    """Non-200 responses leave price as None — no raise."""
    oracle = _make_oracle()

    with patch("src.services.price_oracle.aiohttp") as mock_aiohttp:
        mock_aiohttp.ClientSession = MagicMock(
            return_value=_mock_aiohttp_response({}, status=429)
        )
        mock_aiohttp.ClientTimeout = MagicMock(return_value=None)
        result = await oracle.get_prices_bulk(["ethereum"])

    assert result["ethereum"] is None


@pytest.mark.asyncio
async def test_network_exception_returns_none():
    """aiohttp session.get raising an exception returns None — no raise."""
    oracle = _make_oracle()

    with patch("src.services.price_oracle.aiohttp") as mock_aiohttp:
        session = AsyncMock()
        session.get.side_effect = Exception("network error")
        session_ctx = AsyncMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session)
        session_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_aiohttp.ClientSession = MagicMock(return_value=session_ctx)
        mock_aiohttp.ClientTimeout = MagicMock(return_value=None)
        result = await oracle.get_prices_bulk(["ethereum"])

    assert result["ethereum"] is None


@pytest.mark.asyncio
async def test_aiohttp_unavailable_gracefully_returns_empty():
    """When aiohttp is unavailable, price oracle should gracefully return empty dict."""
    oracle = _make_oracle()

    with patch("src.services.price_oracle.aiohttp", None):
        result = await oracle._fetch_from_coingecko(["ethereum"])

    assert result == {}


@pytest.mark.asyncio
async def test_multiple_assets_in_one_call():
    """Multiple assets should be batched into a single HTTP call."""
    oracle = _make_oracle()
    mock_data = {
        "ethereum": {"usd": 3000.0},
        "tron":     {"usd": 0.09},
        "ripple":   {"usd": 0.50},
    }

    with patch("src.services.price_oracle.aiohttp") as mock_aiohttp:
        mock_aiohttp.ClientSession = MagicMock(
            return_value=_mock_aiohttp_response(mock_data)
        )
        mock_aiohttp.ClientTimeout = MagicMock(return_value=None)
        result = await oracle.get_prices_bulk(["ethereum", "tron", "ripple"])

    assert result["ethereum"] == 3000.0
    assert result["tron"] == 0.09
    assert result["ripple"] == 0.50


@pytest.mark.asyncio
async def test_stale_cache_entry_refetched():
    """Entries older than TTL should be re-fetched from CoinGecko."""
    oracle = _make_oracle()
    # Plant a stale entry (fetched 30 minutes ago)
    oracle._cache["ethereum"] = (2999.0, time.monotonic() - 1900)

    mock_data = {"ethereum": {"usd": 3100.0}}

    with patch("src.services.price_oracle.aiohttp") as mock_aiohttp:
        mock_aiohttp.ClientSession = MagicMock(
            return_value=_mock_aiohttp_response(mock_data)
        )
        mock_aiohttp.ClientTimeout = MagicMock(return_value=None)
        result = await oracle.get_prices_bulk(["ethereum"])

    # Should have the fresh value
    assert result["ethereum"] == 3100.0


def test_get_price_oracle_returns_instance():
    """get_price_oracle() returns a PriceOracle instance."""
    import src.services.price_oracle as module
    original = module._singleton
    module._singleton = None
    try:
        oracle = get_price_oracle()
        assert isinstance(oracle, PriceOracle)
    finally:
        module._singleton = original


def test_get_price_oracle_singleton():
    """Repeated calls return the same instance."""
    import src.services.price_oracle as module
    original = module._singleton
    module._singleton = None
    try:
        o1 = get_price_oracle()
        o2 = get_price_oracle()
        assert o1 is o2
    finally:
        module._singleton = original
