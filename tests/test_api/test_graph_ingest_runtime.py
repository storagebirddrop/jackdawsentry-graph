from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from fastapi import Response


@pytest.mark.asyncio
async def test_graph_ingest_runtime_initializes_and_shuts_down_cleanly():
    from src.api import graph_ingest_runtime as ingest_runtime

    stop_event = asyncio.Event()
    runtime_states: list[dict] = []

    async def fake_start_all():
        await asyncio.sleep(3600)

    manager = AsyncMock()
    manager.collectors = {"ethereum": MagicMock(is_running=True)}
    manager.initialize = AsyncMock()
    manager.start_all = AsyncMock(side_effect=fake_start_all)
    manager.stop = AsyncMock()

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
        patch("src.collectors.manager.CollectorManager", return_value=manager),
    ):
        await ingest_runtime.run_graph_ingest_runtime(
            stop_event,
            state_callback=runtime_states.append,
        )

    init_databases.assert_awaited_once()
    run_migrations.assert_awaited_once_with(profile="graph")
    start_monitoring.assert_awaited_once()
    manager.initialize.assert_awaited_once()
    manager.start_all.assert_awaited_once()
    manager.stop.assert_awaited_once()
    stop_monitoring.assert_awaited_once()
    close_clients.assert_awaited_once()
    close_databases.assert_awaited_once()
    assert any(state["status"] == "running" for state in runtime_states)
    assert runtime_states[-1]["status"] == "stopped"


@pytest.mark.asyncio
async def test_graph_ingest_health_endpoints_report_runtime_snapshot():
    from src.api import graph_ingest_runtime as ingest_runtime

    controller = MagicMock()
    controller.snapshot.return_value = {
        "status": "running",
        "ready": True,
        "collector_count": 15,
        "running_collectors": 12,
        "last_error": None,
        "last_update": "2026-04-07T00:00:00+00:00",
    }
    ingest_runtime.app.state.runtime_controller = controller

    try:
        health_response = Response()
        detailed_response = Response()
        health_payload = await ingest_runtime.health_check(health_response)
        detailed_payload = await ingest_runtime.detailed_health_check(detailed_response)
    finally:
        del ingest_runtime.app.state.runtime_controller

    assert health_response.status_code == 200
    assert health_payload["status"] == "healthy"
    assert health_payload["collector_count"] == 15
    assert detailed_response.status_code == 200
    assert detailed_payload["runtime"]["running_collectors"] == 12


@pytest.mark.asyncio
async def test_graph_ingest_health_endpoint_returns_503_when_runtime_is_not_ready():
    from src.api import graph_ingest_runtime as ingest_runtime

    controller = MagicMock()
    controller.snapshot.return_value = {
        "status": "error",
        "ready": False,
        "collector_count": 15,
        "running_collectors": 0,
        "last_error": "boom",
        "last_update": "2026-04-07T00:00:00+00:00",
    }
    ingest_runtime.app.state.runtime_controller = controller

    try:
        response = Response()
        payload = await ingest_runtime.health_check(response)
    finally:
        del ingest_runtime.app.state.runtime_controller

    assert response.status_code == 503
    assert payload["status"] == "error"
    assert payload["last_error"] == "boom"
