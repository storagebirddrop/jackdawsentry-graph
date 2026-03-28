"""Focused HTTP tests for the session contract entrypoints."""

from datetime import datetime
from datetime import timezone
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.graph_app import app
from src.trace_compiler.compiler import SessionPersistenceError

VALID_SESSION_ID = "00000000-0000-0000-0000-000000000123"


@pytest.fixture
def client():
    return TestClient(app)


def _root_node_payload() -> dict:
    return {
        "node_id": "ethereum:address:0xabc",
        "lineage_id": "lineage-1",
        "node_type": "address",
        "branch_id": "branch-1",
        "path_id": "path-1",
        "depth": 0,
        "display_label": "0xabc",
        "chain": "ethereum",
        "expandable_directions": ["prev", "next", "neighbors"],
        "address_data": {
            "address": "0xabc",
            "chain": "ethereum",
            "address_type": "unknown",
        },
    }


def _session_create_response() -> dict:
    return {
        "session_id": VALID_SESSION_ID,
        "root_node": _root_node_payload(),
        "created_at": datetime(2026, 3, 27, tzinfo=timezone.utc),
    }


def _workspace_snapshot_payload(session_id: str = VALID_SESSION_ID) -> dict:
    return {
        "schema_version": 1,
        "revision": 0,
        "sessionId": session_id,
        "nodes": [_root_node_payload()],
        "edges": [],
        "positions": {
            "ethereum:address:0xabc": {
                "x": 12.5,
                "y": 34.0,
            }
        },
        "branches": [
            {
                "branchId": "branch-1",
                "color": "#3b82f6",
                "seedNodeId": "ethereum:address:0xabc",
                "minDepth": 0,
                "maxDepth": 0,
                "nodeCount": 1,
            }
        ],
        "workspacePreferences": {
            "selectedAssets": [],
            "pinnedAssetKeys": [],
            "assetCatalogScope": "session",
        },
    }


def _session_row(snapshot) -> dict:
    return {
        "session_id": VALID_SESSION_ID,
        "snapshot": snapshot,
        "seed_address": "0xabc",
        "seed_chain": "ethereum",
        "case_id": None,
        "snapshot_saved_at": None,
        "created_at": datetime(2026, 3, 27, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 3, 27, tzinfo=timezone.utc),
    }


def _raising_pg_pool():
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=Exception("DB error"))

    class _Ctx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *_):
            return False

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Ctx())
    return pool


def _writable_pg_pool():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")

    class _Ctx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *_):
            return False

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Ctx())
    pool._conn = conn
    return pool


def _listing_pg_pool(rows):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)

    class _Ctx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *_):
            return False

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Ctx())
    pool._conn = conn
    return pool


class TestCreateSession:
    def test_allows_auth_disabled_runtime_and_returns_compiler_response(self, client):
        compiler = SimpleNamespace(
            create_session=AsyncMock(return_value=_session_create_response()),
        )

        with patch("src.api.routers.graph._get_trace_compiler", new=AsyncMock(return_value=compiler)):
            resp = client.post(
                "/api/v1/graph/sessions",
                json={"seed_address": "0xabc", "seed_chain": "ethereum"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == VALID_SESSION_ID
        assert body["root_node"]["node_id"] == "ethereum:address:0xabc"

    def test_returns_503_on_session_persistence_failure(self, client):
        compiler = SimpleNamespace(
            create_session=AsyncMock(side_effect=SessionPersistenceError("Session store unavailable")),
        )

        with patch("src.api.routers.graph._get_trace_compiler", new=AsyncMock(return_value=compiler)):
            resp = client.post(
                "/api/v1/graph/sessions",
                json={"seed_address": "0xabc", "seed_chain": "ethereum"},
            )

        assert resp.status_code == 503
        assert resp.json() == {"detail": "Session store unavailable"}


class TestGetSession:
    def test_lists_recent_sessions_for_restore_discovery(self, client):
        pool = _listing_pg_pool(
            [
                {
                    "session_id": UUID("00000000-0000-0000-0000-000000000999"),
                    "seed_address": "0xaaa",
                    "seed_chain": "ethereum",
                    "created_at": datetime(2026, 3, 27, tzinfo=timezone.utc),
                    "updated_at": datetime(2026, 3, 27, tzinfo=timezone.utc),
                    "snapshot_saved_at": datetime(2026, 3, 28, tzinfo=timezone.utc),
                }
            ]
        )

        with patch("src.api.routers.graph.get_postgres_pool", return_value=pool):
            resp = client.get("/api/v1/graph/sessions/recent?limit=1")

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["items"][0]["session_id"] == "00000000-0000-0000-0000-000000000999"
        assert payload["items"][0]["seed_chain"] == "ethereum"
        assert pool._conn.fetch.await_args.args[2] == 1

    def test_rejects_invalid_uuid(self, client):
        resp = client.get("/api/v1/graph/sessions/fake-session-id")
        assert resp.status_code == 400
        assert resp.json() == {"detail": "Invalid session_id: must be a UUID"}

    def test_returns_full_workspace_when_snapshot_is_v1(self, client):
        session_row = _session_row(_workspace_snapshot_payload())

        with (
            patch(
                "src.api.routers.graph._get_owned_session_row",
                new=AsyncMock(return_value=session_row),
            ),
            patch("src.api.routers.graph.get_postgres_pool", return_value=MagicMock()),
        ):
            resp = client.get(f"/api/v1/graph/sessions/{VALID_SESSION_ID}")

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["restore_state"] == "full"
        assert payload["workspace"]["sessionId"] == VALID_SESSION_ID
        assert payload["workspace"]["nodes"][0]["node_id"] == "ethereum:address:0xabc"
        assert payload["nodes"] == payload["workspace"]["nodes"]
        assert payload["edges"] == payload["workspace"]["edges"]
        assert payload["branch_map"]["branch-1"]["seedNodeId"] == "ethereum:address:0xabc"

    def test_returns_legacy_bootstrap_for_legacy_snapshot(self, client):
        session_row = _session_row([{"node_id": "ethereum:address:0xabc"}])

        with (
            patch(
                "src.api.routers.graph._get_owned_session_row",
                new=AsyncMock(return_value=session_row),
            ),
            patch("src.api.routers.graph.get_postgres_pool", return_value=MagicMock()),
        ):
            resp = client.get(f"/api/v1/graph/sessions/{VALID_SESSION_ID}")

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["restore_state"] == "legacy_bootstrap"
        assert payload["snapshot"] == [{"node_id": "ethereum:address:0xabc"}]
        assert payload["workspace"]["sessionId"] == VALID_SESSION_ID
        assert payload["nodes"][0]["node_id"] == "ethereum:address:0xabc"
        assert payload["edges"] == []


class TestExpandSessionNode:
    def test_rejects_invalid_uuid(self, client):
        resp = client.post(
            "/api/v1/graph/sessions/fake-session-id/expand",
            json={
                "operation_type": "expand_next",
                "seed_node_id": "ethereum:address:0xabc",
            },
        )
        assert resp.status_code == 400
        assert resp.json() == {"detail": "Invalid session_id: must be a UUID"}


class TestBridgeHopStatus:
    def test_rejects_invalid_uuid(self, client):
        resp = client.get(
            "/api/v1/graph/sessions/fake-session/hops/fake-hop/status",
        )
        assert resp.status_code == 400
        assert resp.json() == {"detail": "Invalid session_id: must be a UUID"}


class TestSaveSnapshot:
    def test_rejects_invalid_uuid(self, client):
        resp = client.post(
            "/api/v1/graph/sessions/fake-session/snapshot",
            json={"node_states": []},
        )
        assert resp.status_code == 400
        assert resp.json() == {"detail": "Invalid session_id: must be a UUID"}

    def test_returns_503_when_snapshot_write_fails(self, client):
        session_row = _session_row([])

        with (
            patch("src.api.routers.graph._get_owned_session_row", new=AsyncMock(return_value=session_row)),
            patch("src.api.routers.graph.get_postgres_pool", return_value=_raising_pg_pool()),
        ):
            resp = client.post(
                f"/api/v1/graph/sessions/{VALID_SESSION_ID}/snapshot",
                json={"node_states": []},
            )

        assert resp.status_code == 503
        assert resp.json() == {"detail": "Session store unavailable"}

    def test_accepts_full_workspace_payload_and_persists_it(self, client):
        session_row = _session_row([])
        pool = _writable_pg_pool()
        payload = _workspace_snapshot_payload()
        payload["revision"] = 1

        with (
            patch("src.api.routers.graph._get_owned_session_row", new=AsyncMock(return_value=session_row)),
            patch("src.api.routers.graph.get_postgres_pool", return_value=pool),
        ):
            resp = client.post(
                f"/api/v1/graph/sessions/{VALID_SESSION_ID}/snapshot",
                json=payload,
            )

        assert resp.status_code == 200
        assert resp.json()["revision"] == 1
        persisted_snapshot = pool._conn.execute.await_args.args[1]
        assert '"schema_version": 1' in persisted_snapshot
        assert '"revision": 1' in persisted_snapshot
        assert f'"sessionId": "{VALID_SESSION_ID}"' in persisted_snapshot
        assert '"positions": {"ethereum:address:0xabc": {"x": 12.5, "y": 34.0}}' in persisted_snapshot

    def test_rejects_mismatched_workspace_session_id(self, client):
        session_row = _session_row([])
        payload = _workspace_snapshot_payload("00000000-0000-0000-0000-000000000777")
        payload["revision"] = 1

        with (
            patch(
                "src.api.routers.graph._get_owned_session_row",
                new=AsyncMock(return_value=session_row),
            ),
            patch("src.api.routers.graph.get_postgres_pool", return_value=MagicMock()),
        ):
            resp = client.post(
                f"/api/v1/graph/sessions/{VALID_SESSION_ID}/snapshot",
                json=payload,
            )

        assert resp.status_code == 400
        assert resp.json() == {
            "detail": "Snapshot sessionId does not match session_id path parameter"
        }

    def test_upgrades_legacy_node_state_payload_to_workspace_snapshot(self, client):
        session_row = _session_row([{"node_id": "ethereum:address:0xabc"}])
        pool = _writable_pg_pool()

        with (
            patch("src.api.routers.graph._get_owned_session_row", new=AsyncMock(return_value=session_row)),
            patch("src.api.routers.graph.get_postgres_pool", return_value=pool),
        ):
            resp = client.post(
                f"/api/v1/graph/sessions/{VALID_SESSION_ID}/snapshot",
                json={
                    "node_states": [
                        {
                            "node_id": "ethereum:address:0xabc",
                            "lineage_id": "lineage-1",
                            "branch_id": "branch-1",
                            "is_pinned": True,
                            "is_hidden": False,
                            "position_hint": {"x": 90.0, "y": 120.0},
                        }
                    ]
                },
            )

        assert resp.status_code == 200
        assert resp.json()["revision"] == 1
        persisted_snapshot = pool._conn.execute.await_args.args[1]
        assert f'"sessionId": "{VALID_SESSION_ID}"' in persisted_snapshot
        assert '"revision": 1' in persisted_snapshot
        assert '"is_pinned": true' in persisted_snapshot
        assert '"positions": {"ethereum:address:0xabc": {"x": 90.0, "y": 120.0}}' in persisted_snapshot

    def test_rejects_stale_workspace_revision(self, client):
        session_row = _session_row(_workspace_snapshot_payload())

        with (
            patch("src.api.routers.graph._get_owned_session_row", new=AsyncMock(return_value=session_row)),
            patch("src.api.routers.graph.get_postgres_pool", return_value=_writable_pg_pool()),
        ):
            resp = client.post(
                f"/api/v1/graph/sessions/{VALID_SESSION_ID}/snapshot",
                json=_workspace_snapshot_payload(),
            )

        assert resp.status_code == 409
        assert resp.json() == {"detail": "Stale workspace snapshot revision"}

    def test_rejects_revision_conflict_when_db_write_loses_race(self, client):
        session_row = _session_row(_workspace_snapshot_payload())
        pool = _writable_pg_pool()
        pool._conn.execute = AsyncMock(return_value="UPDATE 0")
        payload = _workspace_snapshot_payload()
        payload["revision"] = 1

        with (
            patch("src.api.routers.graph._get_owned_session_row", new=AsyncMock(return_value=session_row)),
            patch("src.api.routers.graph.get_postgres_pool", return_value=pool),
        ):
            resp = client.post(
                f"/api/v1/graph/sessions/{VALID_SESSION_ID}/snapshot",
                json=payload,
            )

        assert resp.status_code == 409
        assert resp.json() == {"detail": "Stale workspace snapshot revision"}
