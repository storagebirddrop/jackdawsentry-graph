from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from datetime import timezone
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import UUID

import pytest

from src.api.auth import User

_USER = User(
    id=UUID("00000000-0000-0000-0000-000000000001"),
    username="analyst",
    email="analyst@example.com",
    role="analyst",
    permissions=["blockchain:read"],
    is_active=True,
    created_at=datetime.now(timezone.utc),
    last_login=datetime.now(timezone.utc),
)

_SESSION_UUID = "11111111-1111-1111-1111-111111111111"


def _fake_session_row() -> dict:
    return {
        "session_id": _SESSION_UUID,
        "seed_address": "0xseed",
        "seed_chain": "ethereum",
        "case_id": None,
        "created_by": str(_USER.id),
        "snapshot": None,
        "snapshot_saved_at": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }


def _make_pool(*, fetchrow_return=None, fetch_side_effect=None):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.fetch = AsyncMock(side_effect=fetch_side_effect)

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire
    return pool


@pytest.mark.asyncio
async def test_asset_catalog_aggregates_token_metadata_and_native_assets():
    from src.api.routers.graph import get_session_asset_catalog

    now = datetime.now(timezone.utc)
    token_rows = [
        {
            "blockchain": "ethereum",
            "asset_address": "0xdac17f958d2ee523a2206206994597c13d831ec7",
            "symbol": "USDT",
            "display_name": "Tether USD",
            "canonical_asset_id": "tether",
            "token_standard": "erc20",
            "observed_transfer_count": 10,
            "last_seen_at": now,
        },
        {
            "blockchain": "bsc",
            "asset_address": "0x55d398326f99059ff775485246999027b3197955",
            "symbol": "USDT",
            "display_name": "Binance-Peg BSC-USD",
            "canonical_asset_id": "tether",
            "token_standard": "bep20",
            "observed_transfer_count": 2,
            "last_seen_at": now,
        },
        {
            "blockchain": "tron",
            "asset_address": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
            "symbol": "USDT",
            "display_name": "Tether USD",
            "canonical_asset_id": "tether",
            "token_standard": "trc20",
            "observed_transfer_count": 3,
            "last_seen_at": now,
        },
        {
            "blockchain": "ethereum",
            "asset_address": "0xfake00000000000000000000000000000000beef",
            "symbol": "USDT",
            "display_name": "Suspicious USDT",
            "canonical_asset_id": None,
            "token_standard": "erc20",
            "observed_transfer_count": 1,
            "last_seen_at": now,
        },
    ]
    native_rows = [
        {
            "blockchain": "ethereum",
            "observed_transfer_count": 7,
            "last_seen_at": now,
        }
    ]

    session_pool = _make_pool(fetchrow_return=_fake_session_row())
    asset_pool = _make_pool(fetch_side_effect=[token_rows, native_rows])

    call_count = 0

    def _pool_factory():
        nonlocal call_count
        call_count += 1
        return session_pool if call_count == 1 else asset_pool

    with patch("src.api.routers.graph.get_postgres_pool", side_effect=_pool_factory):
        response = await get_session_asset_catalog(
            session_id=_SESSION_UUID,
            chains=["ethereum", "tron", "bsc"],
            current_user=_USER,
        )

    assert response.seed_chain == "ethereum"
    usdt_item = next(item for item in response.items if item.asset_key == "canonical:tether")
    assert usdt_item.symbol == "USDT"
    assert set(usdt_item.blockchains) == {"ethereum", "tron"}
    assert set(usdt_item.token_standards) == {"erc20", "trc20"}
    assert usdt_item.observed_transfer_count == 13
    assert usdt_item.identity_status == "verified"
    assert usdt_item.variant_kind == "canonical"

    bridged_item = next(
        item for item in response.items
        if item.asset_key == "asset:bsc:0x55d398326f99059ff775485246999027b3197955"
    )
    assert bridged_item.identity_status == "verified"
    assert bridged_item.variant_kind == "bridged"
    assert bridged_item.canonical_asset_id == "tether"

    suspicious_item = next(
        item for item in response.items
        if item.asset_key == "asset:ethereum:0xfake00000000000000000000000000000000beef"
    )
    assert suspicious_item.identity_status == "heuristic"
    assert suspicious_item.variant_kind == "canonical"

    eth_item = next(item for item in response.items if item.asset_key == "native:ethereum")
    assert eth_item.is_native is True
    assert eth_item.symbol == "ETH"
    assert eth_item.blockchains == ["ethereum"]
    assert eth_item.token_standards == ["native"]


@pytest.mark.asyncio
async def test_asset_catalog_prioritizes_verified_assets_over_low_signal_unknowns():
    from src.api.routers.graph import get_session_asset_catalog

    now = datetime.now(timezone.utc)
    token_rows = [
        {
            "blockchain": "solana",
            "asset_address": "UnknownMint1111111111111111111111111111111111",
            "symbol": "RUG",
            "display_name": "Rug Pull Token",
            "canonical_asset_id": None,
            "token_standard": "spl",
            "observed_transfer_count": 25,
            "last_seen_at": now,
        },
        {
            "blockchain": "ethereum",
            "asset_address": "0xdac17f958d2ee523a2206206994597c13d831ec7",
            "symbol": "USDT",
            "display_name": "Tether USD",
            "canonical_asset_id": "tether",
            "token_standard": "erc20",
            "observed_transfer_count": 5,
            "last_seen_at": now,
        },
    ]
    native_rows = [
        {
            "blockchain": "ethereum",
            "observed_transfer_count": 3,
            "last_seen_at": now,
        }
    ]

    session_pool = _make_pool(fetchrow_return=_fake_session_row())
    asset_pool = _make_pool(fetch_side_effect=[token_rows, native_rows])

    call_count = 0

    def _pool_factory():
        nonlocal call_count
        call_count += 1
        return session_pool if call_count == 1 else asset_pool

    with patch("src.api.routers.graph.get_postgres_pool", side_effect=_pool_factory):
        response = await get_session_asset_catalog(
            session_id=_SESSION_UUID,
            chains=["ethereum", "solana"],
            current_user=_USER,
        )

    keys = [item.asset_key for item in response.items]
    assert keys.index("native:ethereum") < keys.index("asset:solana:unknownmint1111111111111111111111111111111111")
    assert keys.index("canonical:tether") < keys.index("asset:solana:unknownmint1111111111111111111111111111111111")
