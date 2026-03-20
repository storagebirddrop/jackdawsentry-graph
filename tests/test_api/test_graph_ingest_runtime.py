from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_graph_ingest_runtime_initializes_and_shuts_down_cleanly():
    from src.api import graph_ingest_runtime as ingest_runtime

    stop_event = asyncio.Event()

    async def fake_start_all():
        await asyncio.sleep(3600)

    manager = AsyncMock()
    manager.initialize = AsyncMock()
    manager.start_all = AsyncMock(side_effect=fake_start_all)
    manager.stop_all = AsyncMock()

    async def trigger_stop():
        await asyncio.sleep(0)
        stop_event.set()

    asyncio.create_task(trigger_stop())

    with (
        patch("src.api.graph_ingest_runtime.init_databases", new_callable=AsyncMock) as init_databases,
        patch(
            "src.api.migrations.migration_manager.run_database_migrations",
            new_callable=AsyncMock,
            return_value=True,
        ) as run_migrations,
        patch(
            "src.api.graph_ingest_runtime.start_connection_monitoring",
            new_callable=AsyncMock,
        ) as start_monitoring,
        patch(
            "src.api.graph_ingest_runtime.stop_connection_monitoring",
            new_callable=AsyncMock,
        ) as stop_monitoring,
        patch("src.collectors.rpc.factory.close_all_clients", new_callable=AsyncMock) as close_clients,
        patch("src.api.graph_ingest_runtime.close_databases", new_callable=AsyncMock) as close_databases,
        patch("src.api.graph_ingest_runtime.CollectorManager", return_value=manager),
    ):
        await ingest_runtime.run_graph_ingest_runtime(stop_event=stop_event)

    init_databases.assert_awaited_once()
    run_migrations.assert_awaited_once_with(profile="graph")
    start_monitoring.assert_awaited_once()
    manager.initialize.assert_awaited_once()
    manager.start_all.assert_awaited_once()
    manager.stop_all.assert_awaited_once()
    stop_monitoring.assert_awaited_once()
    close_clients.assert_awaited_once()
    close_databases.assert_awaited_once()
