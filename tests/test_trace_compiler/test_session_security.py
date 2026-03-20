from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient

from src.api.auth import PERMISSIONS
from src.api.auth import User
from src.api.auth import get_current_user
from src.api.graph_app import app
from src.trace_compiler.models import AddressNodeData
from src.trace_compiler.models import InvestigationNode
from src.trace_compiler.models import SessionCreateResponse


def _user() -> User:
    return User(
        id=UUID("00000000-0000-0000-0000-000000000111"),
        username="security-analyst",
        email="security-analyst@example.com",
        role="analyst",
        permissions=[
            PERMISSIONS["read_blockchain"],
            PERMISSIONS["write_blockchain"],
        ],
        is_active=True,
        created_at=datetime.now(timezone.utc),
        last_login=datetime.now(timezone.utc),
    )


def _root_node() -> InvestigationNode:
    return InvestigationNode(
        node_id="ethereum:address:0xabc",
        lineage_id="lineage-1",
        node_type="address",
        branch_id="branch-1",
        path_id="path-1",
        depth=0,
        display_label="0xabc",
        chain="ethereum",
        expandable_directions=["prev", "next", "neighbors"],
        address_data=AddressNodeData(address="0xabc", address_type="eoa"),
    )


class _AcquireCtx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_):
        return False


def _pg_pool(row=None):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)
    conn.execute = AsyncMock(return_value="UPDATE 1")
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AcquireCtx(conn))
    pool._conn = conn
    return pool


def _session_row() -> dict:
    return {
        "session_id": UUID("00000000-0000-0000-0000-000000000999"),
        "seed_address": "0xabc",
        "seed_chain": "ethereum",
        "case_id": None,
        "created_by": str(_user().id),
        "snapshot": [{"node_id": "ethereum:address:0xabc"}],
        "snapshot_saved_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }


@contextmanager
def _graph_client():
    with (
        patch("src.api.graph_app.init_databases", new_callable=AsyncMock),
        patch("src.api.graph_app.close_databases", new_callable=AsyncMock),
        patch(
            "src.api.migrations.migration_manager.run_database_migrations",
            new_callable=AsyncMock,
            return_value=True,
        ),
        TestClient(app, raise_server_exceptions=False, base_url="http://localhost") as client,
    ):
        yield client


def test_create_session_passes_owner_to_compiler():
    user = _user()
    compiler = MagicMock()
    compiler.create_session = AsyncMock(
        return_value=SessionCreateResponse(
            session_id="00000000-0000-0000-0000-000000000999",
            root_node=_root_node(),
            created_at=datetime.now(timezone.utc),
        )
    )

    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with (
            patch("src.api.routers.graph._get_trace_compiler", new=AsyncMock(return_value=compiler)),
            _graph_client() as client,
        ):
            response = client.post(
                "/api/v1/graph/sessions",
                json={"seed_address": "0xabc", "seed_chain": "ethereum"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert compiler.create_session.await_args.kwargs["owner_user_id"] == str(user.id)


def test_get_session_returns_404_when_not_owned():
    user = _user()
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with (
            patch("src.api.routers.graph.get_postgres_pool", return_value=_pg_pool(row=None)),
            _graph_client() as client,
        ):
            response = client.get(
                "/api/v1/graph/sessions/00000000-0000-0000-0000-000000000999",
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_get_session_returns_owned_snapshot():
    user = _user()
    row = _session_row()
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with (
            patch("src.api.routers.graph.get_postgres_pool", return_value=_pg_pool(row=row)),
            _graph_client() as client,
        ):
            response = client.get(
                "/api/v1/graph/sessions/00000000-0000-0000-0000-000000000999",
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "00000000-0000-0000-0000-000000000999"
    assert payload["snapshot"] == [{"node_id": "ethereum:address:0xabc"}]


def test_expand_rejects_unsupported_chain_filter():
    user = _user()
    compiler = MagicMock()
    compiler.expand = AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with (
            patch("src.api.routers.graph.get_postgres_pool", return_value=_pg_pool(row=_session_row())),
            patch("src.api.routers.graph._get_trace_compiler", new=AsyncMock(return_value=compiler)),
            _graph_client() as client,
        ):
            response = client.post(
                "/api/v1/graph/sessions/00000000-0000-0000-0000-000000000999/expand",
                json={
                    "operation_type": "expand_next",
                    "seed_node_id": "ethereum:address:0xabc",
                    "options": {"chain_filter": ["bitcoin"]},
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    compiler.expand.assert_not_called()


def test_bridge_hop_status_requires_session_allowlist():
    user = _user()
    compiler = MagicMock()
    compiler.is_bridge_hop_allowed = AsyncMock(return_value=False)
    compiler.get_bridge_hop_status = AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with (
            patch("src.api.routers.graph.get_postgres_pool", return_value=_pg_pool(row=_session_row())),
            patch("src.api.routers.graph._get_trace_compiler", new=AsyncMock(return_value=compiler)),
            _graph_client() as client,
        ):
            response = client.get(
                "/api/v1/graph/sessions/00000000-0000-0000-0000-000000000999/hops/0xhop/status",
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    compiler.get_bridge_hop_status.assert_not_called()


def test_expand_rejects_unsupported_continuation_token():
    user = _user()
    compiler = MagicMock()
    compiler.expand = AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with (
            patch("src.api.routers.graph.get_postgres_pool", return_value=_pg_pool(row=_session_row())),
            patch("src.api.routers.graph._get_trace_compiler", new=AsyncMock(return_value=compiler)),
            _graph_client() as client,
        ):
            response = client.post(
                "/api/v1/graph/sessions/00000000-0000-0000-0000-000000000999/expand",
                json={
                    "operation_type": "expand_next",
                    "seed_node_id": "ethereum:address:0xabc",
                    "options": {"continuation_token": "cursor-1"},
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    compiler.expand.assert_not_called()


def test_expand_rejects_max_results_over_limit():
    user = _user()
    compiler = MagicMock()
    compiler.expand = AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with (
            patch("src.api.routers.graph.get_postgres_pool", return_value=_pg_pool(row=_session_row())),
            patch("src.api.routers.graph._get_trace_compiler", new=AsyncMock(return_value=compiler)),
            _graph_client() as client,
        ):
            response = client.post(
                "/api/v1/graph/sessions/00000000-0000-0000-0000-000000000999/expand",
                json={
                    "operation_type": "expand_next",
                    "seed_node_id": "ethereum:address:0xabc",
                    "options": {"max_results": 101},
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    compiler.expand.assert_not_called()


def test_expand_rejects_depth_over_limit():
    user = _user()
    compiler = MagicMock()
    compiler.expand = AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with (
            patch("src.api.routers.graph.get_postgres_pool", return_value=_pg_pool(row=_session_row())),
            patch("src.api.routers.graph._get_trace_compiler", new=AsyncMock(return_value=compiler)),
            _graph_client() as client,
        ):
            response = client.post(
                "/api/v1/graph/sessions/00000000-0000-0000-0000-000000000999/expand",
                json={
                    "operation_type": "expand_next",
                    "seed_node_id": "ethereum:address:0xabc",
                    "options": {"depth": 4},
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    compiler.expand.assert_not_called()
