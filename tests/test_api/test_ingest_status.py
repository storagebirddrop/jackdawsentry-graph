"""
Unit tests for GET /api/v1/graph/sessions/{session_id}/ingest/status.

Covers:
- Returns 'not_found' when no queue row exists for the address.
- Returns the correct status when a pending/running/completed row exists.
- Returns 403 when the session does not belong to the authenticated user.
- Returns 400 when session_id is not a valid UUID.
- Returns 503 when the database query fails.
- Response model shape is correct for all terminal statuses.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from src.api.auth import User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
_ADDRESS = "0xabcdef1234567890abcdef1234567890abcdef12"
_CHAIN = "ethereum"


def _fake_session_row() -> dict:
    return {
        "session_id": _SESSION_UUID,
        "seed_address": _ADDRESS,
        "seed_chain": _CHAIN,
        "case_id": None,
        "created_by": str(_USER.id),
        "snapshot": None,
        "snapshot_saved_at": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }


def _make_pool(fetchrow_return):
    """Return a mock asyncpg pool whose acquire() yields a mock connection."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire
    return pool


@pytest.fixture
def client():
    """TestClient with auth and DB mocked out at the app level."""
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
        with TestClient(app, raise_server_exceptions=False, base_url="http://localhost") as c:
            yield c


# ---------------------------------------------------------------------------
# Helper: produce an authenticated request against the ingest/status endpoint
# ---------------------------------------------------------------------------

def _get_status(
    client: TestClient,
    session_id: str,
    address: str,
    chain: str,
    *,
    current_user: User = _USER,
    session_row=None,
    queue_row=None,
):
    """Drive GET /sessions/{session_id}/ingest/status with mocked DB/auth."""
    from src.api.auth import check_permissions

    if session_row is None:
        session_row = _fake_session_row()

    session_pool = _make_pool(fetchrow_return=session_row)
    queue_pool = _make_pool(fetchrow_return=queue_row)

    # The endpoint makes two DB calls:
    #   1. _get_owned_session_row  → session pool
    #   2. ingest status SELECT    → ingest pool
    # We side-effect get_postgres_pool to return different pools in order.
    call_count = 0

    def _pool_factory():
        nonlocal call_count
        call_count += 1
        return session_pool if call_count <= 1 else queue_pool

    with (
        patch("src.api.routers.graph.check_permissions", return_value=lambda: current_user),
        patch(
            "src.api.routers.graph.get_postgres_pool",
            side_effect=_pool_factory,
        ),
    ):
        app = client.app
        original = app.dependency_overrides.copy()
        from src.api.auth import check_permissions as real_check
        app.dependency_overrides[real_check(["blockchain:read"])] = lambda: current_user
        try:
            return client.get(
                f"/api/v1/graph/sessions/{session_id}/ingest/status",
                params={"address": address, "chain": chain},
            )
        finally:
            app.dependency_overrides = original


# ---------------------------------------------------------------------------
# Simpler approach: test the router function directly (async unit tests)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_status_not_found_when_no_queue_row():
    """Status 'not_found' is returned when no queue row exists."""
    from src.api.routers.graph import get_session_ingest_status

    session_pool = _make_pool(fetchrow_return=_fake_session_row())
    queue_pool = _make_pool(fetchrow_return=None)

    call_count = 0

    def _pool_factory():
        nonlocal call_count
        call_count += 1
        return session_pool if call_count <= 1 else queue_pool

    with patch("src.api.routers.graph.get_postgres_pool", side_effect=_pool_factory):
        result = await get_session_ingest_status(
            session_id=_SESSION_UUID,
            address=_ADDRESS,
            chain=_CHAIN,
            current_user=_USER,
        )

    assert result.status == "not_found"
    assert result.address == _ADDRESS
    assert result.blockchain == _CHAIN


@pytest.mark.asyncio
async def test_ingest_status_pending():
    """Status 'pending' is returned with queued_at populated."""
    from src.api.routers.graph import get_session_ingest_status

    now = datetime.now(timezone.utc)
    queue_row = MagicMock()
    queue_row.__getitem__ = lambda self, key: {
        "address": _ADDRESS,
        "blockchain": _CHAIN,
        "status": "pending",
        "requested_at": now,
        "started_at": None,
        "completed_at": None,
        "tx_count": None,
        "error": None,
    }[key]

    session_pool = _make_pool(fetchrow_return=_fake_session_row())
    queue_pool = _make_pool(fetchrow_return=queue_row)

    call_count = 0

    def _pool_factory():
        nonlocal call_count
        call_count += 1
        return session_pool if call_count <= 1 else queue_pool

    with patch("src.api.routers.graph.get_postgres_pool", side_effect=_pool_factory):
        result = await get_session_ingest_status(
            session_id=_SESSION_UUID,
            address=_ADDRESS,
            chain=_CHAIN,
            current_user=_USER,
        )

    assert result.status == "pending"
    assert result.queued_at == now
    assert result.started_at is None
    assert result.completed_at is None
    assert result.tx_count is None


@pytest.mark.asyncio
async def test_ingest_status_running():
    """Status 'running' is returned with started_at populated."""
    from src.api.routers.graph import get_session_ingest_status

    now = datetime.now(timezone.utc)
    queue_row = MagicMock()
    queue_row.__getitem__ = lambda self, key: {
        "address": _ADDRESS,
        "blockchain": _CHAIN,
        "status": "running",
        "requested_at": now,
        "started_at": now,
        "completed_at": None,
        "tx_count": None,
        "error": None,
    }[key]

    session_pool = _make_pool(fetchrow_return=_fake_session_row())
    queue_pool = _make_pool(fetchrow_return=queue_row)

    call_count = 0

    def _pool_factory():
        nonlocal call_count
        call_count += 1
        return session_pool if call_count <= 1 else queue_pool

    with patch("src.api.routers.graph.get_postgres_pool", side_effect=_pool_factory):
        result = await get_session_ingest_status(
            session_id=_SESSION_UUID,
            address=_ADDRESS,
            chain=_CHAIN,
            current_user=_USER,
        )

    assert result.status == "running"
    assert result.started_at == now


@pytest.mark.asyncio
async def test_ingest_status_completed():
    """Status 'completed' is returned with tx_count and completed_at."""
    from src.api.routers.graph import get_session_ingest_status

    now = datetime.now(timezone.utc)
    queue_row = MagicMock()
    queue_row.__getitem__ = lambda self, key: {
        "address": _ADDRESS,
        "blockchain": _CHAIN,
        "status": "completed",
        "requested_at": now,
        "started_at": now,
        "completed_at": now,
        "tx_count": 42,
        "error": None,
    }[key]

    session_pool = _make_pool(fetchrow_return=_fake_session_row())
    queue_pool = _make_pool(fetchrow_return=queue_row)

    call_count = 0

    def _pool_factory():
        nonlocal call_count
        call_count += 1
        return session_pool if call_count <= 1 else queue_pool

    with patch("src.api.routers.graph.get_postgres_pool", side_effect=_pool_factory):
        result = await get_session_ingest_status(
            session_id=_SESSION_UUID,
            address=_ADDRESS,
            chain=_CHAIN,
            current_user=_USER,
        )

    assert result.status == "completed"
    assert result.tx_count == 42
    assert result.completed_at == now
    assert result.error is None


@pytest.mark.asyncio
async def test_ingest_status_failed():
    """Status 'failed' is returned with the error message."""
    from src.api.routers.graph import get_session_ingest_status

    now = datetime.now(timezone.utc)
    queue_row = MagicMock()
    queue_row.__getitem__ = lambda self, key: {
        "address": _ADDRESS,
        "blockchain": _CHAIN,
        "status": "failed",
        "requested_at": now,
        "started_at": now,
        "completed_at": None,
        "tx_count": None,
        "error": "RPC timeout",
    }[key]

    session_pool = _make_pool(fetchrow_return=_fake_session_row())
    queue_pool = _make_pool(fetchrow_return=queue_row)

    call_count = 0

    def _pool_factory():
        nonlocal call_count
        call_count += 1
        return session_pool if call_count <= 1 else queue_pool

    with patch("src.api.routers.graph.get_postgres_pool", side_effect=_pool_factory):
        result = await get_session_ingest_status(
            session_id=_SESSION_UUID,
            address=_ADDRESS,
            chain=_CHAIN,
            current_user=_USER,
        )

    assert result.status == "failed"
    assert result.error == "RPC timeout"


@pytest.mark.asyncio
async def test_ingest_status_raises_503_on_db_error():
    """A database failure on the queue query raises a 503 HTTP error."""
    from fastapi import HTTPException
    from src.api.routers.graph import get_session_ingest_status

    session_pool = _make_pool(fetchrow_return=_fake_session_row())

    # Queue pool raises on acquire
    bad_pool = MagicMock()

    @asynccontextmanager
    async def _bad_acquire():
        raise Exception("DB down")
        yield  # noqa: unreachable — satisfies async generator protocol

    bad_pool.acquire = _bad_acquire

    call_count = 0

    def _pool_factory():
        nonlocal call_count
        call_count += 1
        return session_pool if call_count <= 1 else bad_pool

    with (
        patch("src.api.routers.graph.get_postgres_pool", side_effect=_pool_factory),
        pytest.raises(HTTPException) as exc_info,
    ):
        await get_session_ingest_status(
            session_id=_SESSION_UUID,
            address=_ADDRESS,
            chain=_CHAIN,
            current_user=_USER,
        )

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_ingest_status_raises_400_on_invalid_session_uuid():
    """A non-UUID session_id raises 400 before touching the DB."""
    from fastapi import HTTPException
    from src.api.routers.graph import get_session_ingest_status

    session_pool = _make_pool(fetchrow_return=None)

    with (
        patch("src.api.routers.graph.get_postgres_pool", return_value=session_pool),
        pytest.raises(HTTPException) as exc_info,
    ):
        await get_session_ingest_status(
            session_id="not-a-uuid",
            address=_ADDRESS,
            chain=_CHAIN,
            current_user=_USER,
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_ingest_status_raises_404_when_session_not_owned():
    """Returns 404 when the session belongs to a different user."""
    from fastapi import HTTPException
    from src.api.routers.graph import get_session_ingest_status

    # Session row belongs to a different user — fetchrow returns None
    session_pool = _make_pool(fetchrow_return=None)

    with (
        patch("src.api.routers.graph.get_postgres_pool", return_value=session_pool),
        pytest.raises(HTTPException) as exc_info,
    ):
        await get_session_ingest_status(
            session_id=_SESSION_UUID,
            address=_ADDRESS,
            chain=_CHAIN,
            current_user=_USER,
        )

    assert exc_info.value.status_code == 404
