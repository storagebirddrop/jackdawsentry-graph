from __future__ import annotations

from datetime import datetime
from datetime import timezone
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.graph_app import PUBLIC_GRAPH_USER_ID
from src.api.graph_app import app
from src.api.graph_app import get_graph_runtime_user
from src.api.auth import get_current_user
from src.trace_compiler.models import WorkspaceSnapshotV1


SESSION_ID = "11111111-1111-1111-1111-111111111111"
NOW = datetime(2026, 3, 26, 18, 0, tzinfo=timezone.utc)
ROOT_NODE = {
    "node_id": "ethereum:address:0xabc",
    "lineage_id": "lineage-1",
    "node_type": "address",
    "branch_id": "branch-1",
    "path_id": "path-1",
    "depth": 0,
    "display_label": "0xabc",
    "chain": "ethereum",
    "address_data": {
        "address": "0xabc",
        "address_type": "eoa",
    },
}
SESSION_ROW = {
    "session_id": SESSION_ID,
    "seed_address": "0xabc",
    "seed_chain": "ethereum",
    "case_id": None,
    "snapshot": [],
    "snapshot_saved_at": NOW,
    "created_at": NOW,
    "updated_at": NOW,
}


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = get_graph_runtime_user
    try:
        yield TestClient(app, raise_server_exceptions=False, base_url="http://localhost")
    finally:
        app.dependency_overrides.clear()


def _workspace_snapshot() -> WorkspaceSnapshotV1:
    return WorkspaceSnapshotV1(
        sessionId=SESSION_ID,
        revision=0,
        nodes=[],
        edges=[],
        positions={},
        branches=[],
    )


def test_create_session_is_public(client):
    compiler = AsyncMock()
    compiler.create_session.return_value = {
        "session_id": SESSION_ID,
        "root_node": ROOT_NODE,
        "created_at": NOW,
    }

    with patch(
        "src.api.routers.graph._get_trace_compiler",
        new=AsyncMock(return_value=compiler),
    ):
        response = client.post(
            "/api/v1/graph/sessions",
            json={"seed_address": "0xabc", "seed_chain": "ethereum"},
        )

    assert response.status_code == 200
    assert response.json()["session_id"] == SESSION_ID
    assert compiler.create_session.await_args.kwargs["owner_user_id"] == str(PUBLIC_GRAPH_USER_ID)


def test_get_session_is_public(client):
    session_store = MagicMock()
    session_store.normalize_workspace.return_value = (_workspace_snapshot(), "full", [])

    with (
        patch(
            "src.api.routers.graph._get_owned_session_row",
            new=AsyncMock(return_value=SESSION_ROW),
        ),
        patch(
            "src.api.routers.graph._get_graph_session_store",
            return_value=session_store,
        ),
    ):
        response = client.get(f"/api/v1/graph/sessions/{SESSION_ID}")

    assert response.status_code == 200
    assert response.json()["session_id"] == SESSION_ID


def test_expand_session_is_public(client):
    compiler = AsyncMock()
    compiler.expand.return_value = {
        "operation_id": "op-1",
        "operation_type": "expand_next",
        "session_id": SESSION_ID,
        "seed_node_id": ROOT_NODE["node_id"],
        "branch_id": "branch-1",
        "expansion_depth": 1,
        "timestamp": NOW,
        "added_nodes": [],
        "added_edges": [],
    }

    with (
        patch(
            "src.api.routers.graph._get_owned_session_row",
            new=AsyncMock(return_value=SESSION_ROW),
        ),
        patch(
            "src.api.routers.graph._get_trace_compiler",
            new=AsyncMock(return_value=compiler),
        ),
    ):
        response = client.post(
            f"/api/v1/graph/sessions/{SESSION_ID}/expand",
            json={
                "operation_type": "expand_next",
                "seed_node_id": ROOT_NODE["node_id"],
            },
        )

    assert response.status_code == 200
    assert response.json()["session_id"] == SESSION_ID


def test_asset_options_session_is_public(client):
    compiler = AsyncMock()
    compiler.get_asset_options.return_value = {
        "session_id": SESSION_ID,
        "seed_node_id": ROOT_NODE["node_id"],
        "seed_lineage_id": ROOT_NODE["lineage_id"],
        "options": [
            {
                "mode": "all",
                "chain": "ethereum",
                "display_label": "All assets",
            }
        ],
    }

    with (
        patch(
            "src.api.routers.graph._get_owned_session_row",
            new=AsyncMock(return_value=SESSION_ROW),
        ),
        patch(
            "src.api.routers.graph._get_trace_compiler",
            new=AsyncMock(return_value=compiler),
        ),
    ):
        response = client.post(
            f"/api/v1/graph/sessions/{SESSION_ID}/asset-options",
            json={
                "seed_node_id": ROOT_NODE["node_id"],
                "seed_lineage_id": ROOT_NODE["lineage_id"],
            },
        )

    assert response.status_code == 200
    assert response.json()["session_id"] == SESSION_ID
    assert response.json()["options"][0]["mode"] == "all"


def test_bridge_hop_status_is_public(client):
    compiler = AsyncMock()
    compiler.is_bridge_hop_allowed.return_value = True
    compiler.get_bridge_hop_status.return_value = {
        "hop_id": "hop-1",
        "status": "pending",
        "destination_chain": None,
        "destination_tx_hash": None,
        "destination_address": None,
        "correlation_confidence": 0.8,
        "updated_at": NOW,
    }

    with (
        patch(
            "src.api.routers.graph._get_owned_session_row",
            new=AsyncMock(return_value=SESSION_ROW),
        ),
        patch(
            "src.api.routers.graph._get_trace_compiler",
            new=AsyncMock(return_value=compiler),
        ),
    ):
        response = client.get(
            f"/api/v1/graph/sessions/{SESSION_ID}/hops/hop-1/status",
        )

    assert response.status_code == 200
    assert response.json()["hop_id"] == "hop-1"


def test_save_snapshot_is_public(client):
    current_workspace = _workspace_snapshot()
    merged_workspace = current_workspace.model_copy(
        update={
            "nodes": [ROOT_NODE],
            "positions": {ROOT_NODE["node_id"]: {"x": 10, "y": 20}},
        }
    )
    session_store = MagicMock()
    session_store.normalize_workspace.return_value = (current_workspace, "full", [])
    session_store.merge_node_states.return_value = merged_workspace
    session_store.save_workspace_snapshot = AsyncMock()

    with (
        patch(
            "src.api.routers.graph._get_owned_session_row",
            new=AsyncMock(return_value=SESSION_ROW),
        ),
        patch(
            "src.api.routers.graph._get_graph_session_store",
            return_value=session_store,
        ),
    ):
        response = client.post(
            f"/api/v1/graph/sessions/{SESSION_ID}/snapshot",
            json={
                "node_states": [
                    {
                        "node_id": ROOT_NODE["node_id"],
                        "lineage_id": ROOT_NODE["lineage_id"],
                        "branch_id": ROOT_NODE["branch_id"],
                        "is_pinned": False,
                        "is_hidden": False,
                        "position_hint": {"x": 10, "y": 20},
                    }
                ]
            },
        )

    assert response.status_code == 200
    assert "snapshot_id" in response.json()
