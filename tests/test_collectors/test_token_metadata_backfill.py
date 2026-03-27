from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest

from src.collectors.token_metadata_backfill import TokenMetadataBackfillWorker
from src.services.token_metadata import TokenMetadataRecord


def _fake_candidate(**overrides):
    now = datetime.now(timezone.utc)
    candidate = {
        "blockchain": "ethereum",
        "asset_address": "0xabc123",
        "last_seen_at": now,
        "seed_symbol": "USDT",
        "seed_canonical_asset_id": "usdt",
        "resolve_status": None,
        "next_refresh_at": None,
    }
    candidate.update(overrides)
    return candidate


@pytest.mark.asyncio
async def test_token_metadata_backfill_worker_refreshes_candidates():
    collector = SimpleNamespace(
        _default_token_asset_type=lambda: "erc20",
        _fetch_token_metadata=AsyncMock(
            return_value=TokenMetadataRecord(
                blockchain="ethereum",
                asset_address="0xabc123",
                symbol="USDT",
                name="Tether USD",
                decimals=6,
                token_standard="erc20",
                canonical_asset_id="usdt",
                source="rpc",
            )
        ),
    )
    cache = SimpleNamespace(
        refresh_metadata=AsyncMock(
            return_value=TokenMetadataRecord(
                blockchain="ethereum",
                asset_address="0xabc123",
                symbol="USDT",
                canonical_asset_id="usdt",
                resolve_status="resolved",
            )
        )
    )

    @asynccontextmanager
    async def fake_postgres_connection():
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[_fake_candidate()])
        yield conn

    with patch(
        "src.collectors.token_metadata_backfill.get_postgres_connection",
        fake_postgres_connection,
    ):
        worker = TokenMetadataBackfillWorker({"ethereum": collector}, poll_interval=10, batch_size=5)
        worker._cache = cache
        worker.is_running = True
        await worker._run_cycle()

    cache.refresh_metadata.assert_awaited_once()
    args, kwargs = cache.refresh_metadata.await_args
    assert args[:2] == ("ethereum", "0xabc123")
    seed = kwargs["seed"]
    assert seed.symbol == "USDT"
    assert seed.canonical_asset_id == "usdt"
    assert seed.token_standard == "erc20"
    assert seed.source == "event_store_seed"


@pytest.mark.asyncio
async def test_token_metadata_backfill_worker_ignores_placeholder_seed_symbol():
    collector = SimpleNamespace(
        _default_token_asset_type=lambda: "erc20",
        _fetch_token_metadata=AsyncMock(return_value=None),
    )
    cache = SimpleNamespace(
        refresh_metadata=AsyncMock(
            return_value=TokenMetadataRecord(
                blockchain="ethereum",
                asset_address="0xdeadbeef",
                resolve_status="missing",
            )
        )
    )

    @asynccontextmanager
    async def fake_postgres_connection():
        conn = AsyncMock()
        conn.fetch = AsyncMock(
            return_value=[
                _fake_candidate(
                    asset_address="0xdeadbeef",
                    seed_symbol="0xdead...beef",
                    seed_canonical_asset_id=None,
                )
            ]
        )
        yield conn

    with patch(
        "src.collectors.token_metadata_backfill.get_postgres_connection",
        fake_postgres_connection,
    ):
        worker = TokenMetadataBackfillWorker({"ethereum": collector}, poll_interval=10, batch_size=5)
        worker._cache = cache
        worker.is_running = True
        await worker._run_cycle()

    _, kwargs = cache.refresh_metadata.await_args
    seed = kwargs["seed"]
    assert seed.symbol is None
