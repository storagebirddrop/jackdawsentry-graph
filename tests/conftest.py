"""
Jackdaw Sentry - Test Configuration
Pytest fixtures for unit, smoke, and integration tests.

Unit and smoke tests work without external services.
Integration tests (marked @pytest.mark.integration) require running
PostgreSQL, Neo4j, and Redis — skip them with: pytest -m "not integration"
"""

import os
import pytest
from typing import Generator
from unittest.mock import AsyncMock, patch

import fastapi.testclient as fastapi_testclient

from tests.asgi_testclient import ASGITestClient

# ---------------------------------------------------------------------------
# Environment overrides — must happen before any app imports
# ---------------------------------------------------------------------------
os.environ.update(
    {
        "TESTING": "true",
        "LOG_LEVEL": "WARNING",
        "API_SECRET_KEY": "test-secret-key-for-testing-only-1234",
        "ENCRYPTION_KEY": "test-encryption-key-32-chars-long!!",
        "JWT_SECRET_KEY": "test-jwt-secret-key-for-testing-ok",
        "JWT_ALGORITHM": "HS256",
        "JWT_EXPIRE_MINUTES": "30",
        "DATABASE_URL": "postgresql://test:test@localhost:5432/test",
        "NEO4J_URI": "bolt://localhost:7687",
        "REDIS_URL": "redis://localhost:6379/1",
        "API_HOST": "0.0.0.0",
        "API_PORT": "8000",
        "DEBUG": "true",
        "AUDIT_LOG_DIR": "/tmp/jds_test_audit",
    }
)

from src.api.middleware import AuditMiddleware


# ---------------------------------------------------------------------------
# TestClient compatibility shim — patch before test modules import it
# ---------------------------------------------------------------------------
fastapi_testclient.TestClient = ASGITestClient


async def _noop_store_audit_log(self, request_log, response_log):
    """Skip audit persistence in tests to avoid external side effects."""
    return None


AuditMiddleware._store_audit_log = _noop_store_audit_log


# ---------------------------------------------------------------------------
# Fixtures — API client (uses TestClient, no lifespan/DB required)
# ---------------------------------------------------------------------------
@pytest.fixture
def client():
    """Provide a FastAPI TestClient with DB init/shutdown mocked out."""
    from fastapi.testclient import TestClient
    from src.api.main import app

    with (
        patch("src.api.main.init_databases", new_callable=AsyncMock),
        patch("src.api.main.close_databases", new_callable=AsyncMock),
        patch("src.api.main.start_background_tasks", new_callable=AsyncMock),
        patch("src.api.main.stop_background_tasks", new_callable=AsyncMock),
        patch("src.monitoring.alert_rules.ensure_tables", new_callable=AsyncMock),
    ):
        with TestClient(app, raise_server_exceptions=False, base_url="http://localhost") as c:
            yield c


@pytest.fixture
def test_app(client):
    """Backward-compatible alias for suites that expect a shared test client."""
    return client


# ---------------------------------------------------------------------------
# Auth helper fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def jwt_secret():
    return os.environ["JWT_SECRET_KEY"]


@pytest.fixture
def make_token(jwt_secret):
    """Factory fixture: create a JWT with custom claims."""
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone

    def _make(
        sub: str = "testuser",
        user_id: str = "00000000-0000-0000-0000-000000000001",
        permissions: list | None = None,
        expire_minutes: int = 30,
        **extra,
    ) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": sub,
            "user_id": user_id,
            "permissions": permissions or [],
            "iat": now,
            "exp": now + timedelta(minutes=expire_minutes),
            **extra,
        }
        return pyjwt.encode(payload, jwt_secret, algorithm=os.environ.get("JWT_ALGORITHM", "HS256"))

    return _make


@pytest.fixture
def auth_headers(make_token):
    """Bearer header with a valid analyst-role token."""
    token = make_token(sub="analyst", permissions=["analysis:read", "analysis:write"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers(make_token):
    """Bearer header with a valid admin-role token."""
    from src.api.auth import ROLES
    token = make_token(sub="admin", permissions=ROLES["admin"])
    return {"Authorization": f"Bearer {token}"}
