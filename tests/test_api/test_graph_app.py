from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from datetime import timezone
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from fastapi import FastAPI

from src.api.auth import PERMISSIONS
from src.api.auth import User
from src.api.auth import get_current_user
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


def _load_openapi_paths(*, graph_auth_disabled: bool) -> set[str]:
    from src.api import graph_app as graph_app_module

    previous_docs = graph_app_module.settings.EXPOSE_API_DOCS
    previous_auth_disabled = graph_app_module.settings.GRAPH_AUTH_DISABLED
    graph_app_module.settings.EXPOSE_API_DOCS = True
    graph_app_module.settings.GRAPH_AUTH_DISABLED = graph_auth_disabled
    try:
        env_patch = {
            "NODE_ENV": "development" if graph_auth_disabled else "production",
            "AUTH_DISABLE_CONFIRM": "true" if graph_auth_disabled else "false",
        }
        with patch.dict(os.environ, env_patch, clear=False):
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
        graph_app_module.settings.EXPOSE_API_DOCS = previous_docs
        graph_app_module.settings.GRAPH_AUTH_DISABLED = previous_auth_disabled

    return set(schema["paths"])
def test_graph_app_openapi_can_be_enabled_in_auth_disabled_mode():
    paths = _load_openapi_paths(graph_auth_disabled=True)

    assert "/api/v1/auth/login" not in paths
    assert "/api/v1/graph/sessions" in paths
    assert "/api/v1/graph/sessions/{session_id}/assets" in paths
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


def test_graph_app_openapi_can_be_enabled_in_auth_enabled_mode():
    paths = _load_openapi_paths(graph_auth_disabled=False)

    assert "/api/v1/auth/login" in paths
    assert "/api/v1/graph/sessions" in paths
    assert "/api/v1/graph/sessions/{session_id}/assets" in paths
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


def test_graph_app_requires_auth_when_bypass_confirmation_is_missing():
    from src.api import graph_app as graph_app_module

    previous_auth_disabled = graph_app_module.settings.GRAPH_AUTH_DISABLED
    graph_app_module.settings.GRAPH_AUTH_DISABLED = True
    try:
        with patch.dict(
            os.environ,
            {"NODE_ENV": "production", "AUTH_DISABLE_CONFIRM": "false"},
            clear=False,
        ):
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
                    response = client.get("/api/v1/status")
    finally:
        graph_app_module.settings.GRAPH_AUTH_DISABLED = previous_auth_disabled

    route_paths = {route.path for route in temp_app.routes}

    assert response.status_code == 401
    assert get_current_user not in temp_app.dependency_overrides
    assert "/api/v1/auth/login" in route_paths


def _fetchrow_pool(row):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)

    class _Ctx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *_):
            return False

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Ctx())
    return pool


def test_graph_app_legacy_graph_routes_are_not_registered(graph_client):
    assert graph_client.post("/api/v1/graph/expand", json={}).status_code == 404
    assert graph_client.post("/api/v1/graph/trace", json={}).status_code == 404
    assert graph_client.post("/api/v1/graph/search", json={}).status_code == 404
    assert graph_client.post("/api/v1/graph/cluster", json={}).status_code == 404
    assert graph_client.post("/api/v1/graph/expand-bridge", json={}).status_code == 404
    assert graph_client.post("/api/v1/graph/expand-utxo", json={}).status_code == 404
    assert graph_client.post("/api/v1/graph/expand-solana-tx", json={}).status_code == 404


def test_graph_latency_endpoint_is_hidden_when_metrics_disabled(graph_client):
    from src.api import graph_app as graph_app_module

    previous_metrics = graph_app_module.settings.EXPOSE_METRICS
    graph_app_module.app.dependency_overrides[get_current_user] = lambda: User(
        id="00000000-0000-0000-0000-000000000123",
        username="metrics-reader",
        email="metrics-reader@example.com",
        role="analyst",
        permissions=[PERMISSIONS["read_analysis"]],
        is_active=True,
        created_at=datetime.now(timezone.utc),
        last_login=datetime.now(timezone.utc),
    )
    graph_app_module.settings.EXPOSE_METRICS = False
    try:
        response = graph_client.get("/api/v1/graph/latency")
    finally:
        graph_app_module.settings.EXPOSE_METRICS = previous_metrics
        graph_app_module.app.dependency_overrides.clear()

    assert response.status_code == 404


def test_graph_latency_endpoint_returns_stats_when_metrics_enabled(graph_client):
    from src.api import graph_app as graph_app_module

    previous_metrics = graph_app_module.settings.EXPOSE_METRICS
    graph_app_module.app.dependency_overrides[get_current_user] = lambda: User(
        id="00000000-0000-0000-0000-000000000123",
        username="metrics-reader",
        email="metrics-reader@example.com",
        role="analyst",
        permissions=[PERMISSIONS["read_analysis"]],
        is_active=True,
        created_at=datetime.now(timezone.utc),
        last_login=datetime.now(timezone.utc),
    )
    graph_app_module.settings.EXPOSE_METRICS = True
    try:
        with patch(
            "src.api.routers.graph.get_graph_latency_stats",
            new=AsyncMock(
                return_value={
                    "/sessions/demo/expand": {
                        "p50_ms": 3.1,
                        "p95_ms": 5.9,
                        "p99_ms": 6.4,
                        "mean_ms": 4.0,
                        "sample_count": 10,
                        "window_seconds": 3600,
                    }
                }
            ),
        ):
            response = graph_client.get("/api/v1/graph/latency")
    finally:
        graph_app_module.settings.EXPOSE_METRICS = previous_metrics
        graph_app_module.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["endpoints"]["/sessions/demo/expand"]["sample_count"] == 10


def test_graph_app_resolve_tx_db_hit_serializes_datetime(graph_client):
    from src.api import graph_app as graph_app_module

    row = {
        "tx_hash": "0x" + "a" * 64,
        "from_address": "0xfrom",
        "to_address": "0xto",
        "value_native": 1.25,
        "timestamp": datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc),
        "block_number": 123,
        "status": "confirmed",
    }

    graph_app_module.app.dependency_overrides[get_current_user] = lambda: User(
        id="00000000-0000-0000-0000-000000000123",
        username="resolver",
        email="resolver@example.com",
        role="analyst",
        permissions=[PERMISSIONS["read_analysis"], PERMISSIONS["read_blockchain"]],
        is_active=True,
        created_at=datetime.now(timezone.utc),
        last_login=datetime.now(timezone.utc),
    )
    try:
        with patch("src.api.routers.graph.get_postgres_pool", return_value=_fetchrow_pool(row)):
            resp = graph_client.get(
                "/api/v1/graph/resolve-tx",
                params={"chain": "ethereum", "tx": row["tx_hash"]},
            )
    finally:
        graph_app_module.app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["tx_hash"] == row["tx_hash"]
    assert body["timestamp"] == "2026-03-27T12:00:00Z"


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
        "016_token_metadata_cache.sql",
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
        "016_token_metadata_cache.sql",
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
