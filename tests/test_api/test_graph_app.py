from __future__ import annotations

from pathlib import Path
from contextlib import asynccontextmanager
import json
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from fastapi import FastAPI

from src.api.middleware import AuditMiddleware
from src.api.middleware import GraphLatencyMiddleware
from src.api.middleware import RateLimitMiddleware
from src.api.middleware import SecurityMiddleware
from src.api.migrations.migration_manager import MigrationManager


@pytest.fixture
def graph_client():
    from src.api.graph_app import app

    with (
        patch("src.api.graph_app.init_databases", new_callable=AsyncMock),
        patch("src.api.graph_app.close_databases", new_callable=AsyncMock),
        patch(
            "src.api.migrations.migration_manager.run_database_migrations",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        with TestClient(app, raise_server_exceptions=False, base_url="http://localhost") as client:
            yield client


def test_graph_app_root_and_health(graph_client):
    root_response = graph_client.get("/")
    assert root_response.status_code == 200
    assert root_response.json()["name"] == "Jackdaw Sentry Graph API"

    health_response = graph_client.get("/health")
    assert health_response.status_code == 200
    assert health_response.json()["service"] == "Jackdaw Sentry Graph API"


def test_graph_app_docs_disabled_by_default(graph_client):
    assert graph_client.get("/openapi.json").status_code == 404
    assert graph_client.get("/docs").status_code == 404


def test_graph_app_openapi_can_be_enabled():
    from src.api import graph_app as graph_app_module

    graph_app_module.settings.EXPOSE_API_DOCS = True
    try:
        temp_app = graph_app_module.create_graph_app()
        with (
            patch("src.api.graph_app.init_databases", new_callable=AsyncMock),
            patch("src.api.graph_app.close_databases", new_callable=AsyncMock),
            patch(
                "src.api.migrations.migration_manager.run_database_migrations",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            with TestClient(
                temp_app,
                raise_server_exceptions=False,
                base_url="http://localhost",
            ) as client:
                schema = client.get("/openapi.json").json()
    finally:
        graph_app_module.settings.EXPOSE_API_DOCS = False

    paths = set(schema["paths"])
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/graph/sessions" in paths
    assert "/api/v1/graph/sessions/{session_id}/expand" in paths
    assert "/api/v1/graph/expand" not in paths
    assert "/api/v1/graph/trace" not in paths
    assert "/api/v1/graph/search" not in paths
    assert "/api/v1/graph/cluster" not in paths
    assert "/api/v1/graph/expand-bridge" not in paths
    assert "/api/v1/graph/expand-utxo" not in paths
    assert "/api/v1/graph/expand-solana-tx" not in paths
    assert "/api/v1/setup/status" not in paths
    assert "/api/v1/compliance/statistics" not in paths


def test_graph_app_legacy_graph_routes_are_not_registered(graph_client):
    assert graph_client.post("/api/v1/graph/expand", json={}).status_code == 404
    assert graph_client.post("/api/v1/graph/trace", json={}).status_code == 404
    assert graph_client.post("/api/v1/graph/search", json={}).status_code == 404
    assert graph_client.post("/api/v1/graph/cluster", json={}).status_code == 404
    assert graph_client.post("/api/v1/graph/expand-bridge", json={}).status_code == 404
    assert graph_client.post("/api/v1/graph/expand-utxo", json={}).status_code == 404
    assert graph_client.post("/api/v1/graph/expand-solana-tx", json={}).status_code == 404


def test_graph_app_middleware_excludes_audit():
    from src.api import graph_app as graph_app_module

    graph_app_module.settings.TESTING = False
    try:
        temp_app = FastAPI()
        graph_app_module.configure_middleware(temp_app)
    finally:
        graph_app_module.settings.TESTING = True

    middleware_classes = {middleware.cls for middleware in temp_app.user_middleware}

    assert SecurityMiddleware in middleware_classes
    assert RateLimitMiddleware in middleware_classes
    assert GraphLatencyMiddleware in middleware_classes
    assert AuditMiddleware not in middleware_classes


@pytest.mark.asyncio
async def test_graph_profile_only_includes_graph_bootstrap_migrations(tmp_path: Path):
    for name in [
        "001_initial_schema.sql",
        "002_seed_admin_user.sql",
        "003_sanctioned_addresses.sql",
        "005_bridge_correlations.sql",
        "006_raw_event_store.sql",
        "007_graph_sessions.sql",
        "009_event_store_backfill.sql",
    ]:
        (tmp_path / name).write_text("-- sql", encoding="utf-8")

    manager = MigrationManager()
    manager.migrations_dir = tmp_path

    pending = await manager.get_pending_migrations(profile="graph")

    assert pending == [
        "001_initial_schema.sql",
        "005_bridge_correlations.sql",
        "006_raw_event_store.sql",
        "007_graph_sessions.sql",
        "009_event_store_backfill.sql",
    ]


@pytest.mark.asyncio
async def test_graph_app_ingest_status_detects_collector_metrics():
    from src.api import graph_app as graph_app_module

    class FakeRedis:
        async def get(self, key: str):
            assert key == "collector_metrics"
            return json.dumps(
                {
                    "running_collectors": 3,
                    "total_collectors": 5,
                    "total_transactions": 42,
                    "total_blocks": 7,
                    "last_update": "2026-03-21T00:00:00Z",
                }
            )

    @asynccontextmanager
    async def fake_redis_connection():
        yield FakeRedis()

    with patch("src.api.database.get_redis_connection", fake_redis_connection):
        status = await graph_app_module.get_ingest_runtime_status()

    assert status["detected"] is True
    assert status["running_collectors"] == 3
    assert status["total_collectors"] == 5


@pytest.mark.asyncio
async def test_graph_app_ingest_status_reports_request_only_mode():
    from src.api import graph_app as graph_app_module

    class FakeRedis:
        async def get(self, key: str):
            assert key == "collector_metrics"
            return None

    @asynccontextmanager
    async def fake_redis_connection():
        yield FakeRedis()

    with patch("src.api.database.get_redis_connection", fake_redis_connection):
        status = await graph_app_module.get_ingest_runtime_status()

    assert status["detected"] is False
    assert "request-serving graph API" in status["message"]
