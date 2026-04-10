from __future__ import annotations

from datetime import datetime
from datetime import timezone
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from src.services.graph_sessions import GraphSessionStore
from src.services.graph_sessions import SnapshotRevisionConflictError
from src.services.graph_sessions import _bootstrap_root_node
from src.services.graph_sessions import _canonical_seed_identifier
from src.trace_compiler.models import NodeStateSnapshot
from src.trace_compiler.models import WorkspaceSnapshotV1


def _workspace_snapshot() -> WorkspaceSnapshotV1:
    store = GraphSessionStore(None)
    workspace, _, _ = store.normalize_workspace(
        {
            "session_id": "00000000-0000-0000-0000-000000000123",
            "seed_address": "0xabc",
            "seed_chain": "ethereum",
            "snapshot": None,
        }
    )
    return workspace


def _workspace_snapshot_payload() -> dict:
    return {
        "schema_version": 1,
        "revision": 1,
        "sessionId": "00000000-0000-0000-0000-000000000123",
        "nodes": [
            {
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
        ],
        "edges": [],
        "positions": {},
        "branches": [],
        "nodeAssetScopes": {
            "ethereum:address:0xabc": [
                {
                    "mode": "asset",
                    "chain": "ethereum",
                    "chain_asset_id": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                    "asset_symbol": "USDC",
                }
            ]
        },
    }


def _writable_pool(result: str = "UPDATE 1"):
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=result)

    class _Ctx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *_):
            return False

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Ctx())
    pool._conn = conn
    return pool


def test_canonical_seed_identifier_preserves_non_evm_address_case():
    assert _canonical_seed_identifier("starknet", "0xABCDEF") == "0xABCDEF"
    assert _canonical_seed_identifier("injective", "injABCdef123") == "injABCdef123"


def test_canonical_seed_identifier_normalizes_evm_addresses():
    assert _canonical_seed_identifier("ethereum", "0xABCDEF") == "0xabcdef"


def test_bootstrap_root_node_rejects_missing_required_fields():
    with pytest.raises(ValueError, match="seed_address"):
        _bootstrap_root_node(
            {
                "session_id": "00000000-0000-0000-0000-000000000123",
                "seed_address": "",
                "seed_chain": "ethereum",
            }
        )


def test_normalize_workspace_preserves_empty_position_hint():
    store = GraphSessionStore(None)
    workspace, restore_state, _ = store.normalize_workspace(
        {
            "session_id": "00000000-0000-0000-0000-000000000123",
            "seed_address": "0xabc",
            "seed_chain": "ethereum",
            "snapshot": [
                NodeStateSnapshot(
                    node_id="ethereum:address:0xabc",
                    lineage_id="lineage-1",
                    branch_id="branch-1",
                    position_hint={},
                ).model_dump(mode="json")
            ],
        }
    )

    assert restore_state == "legacy_bootstrap"
    assert workspace.positions["ethereum:address:0xabc"] == {}


def test_normalize_workspace_preserves_node_asset_scopes_from_full_snapshot():
    store = GraphSessionStore(None)
    workspace, restore_state, _ = store.normalize_workspace(
        {
            "session_id": "00000000-0000-0000-0000-000000000123",
            "seed_address": "0xabc",
            "seed_chain": "ethereum",
            "snapshot": _workspace_snapshot_payload(),
        }
    )

    assert restore_state == "full"
    assert workspace.model_dump(mode="json")["nodeAssetScopes"] == {
        "ethereum:address:0xabc": [
            {
                "mode": "asset",
                "chain": "ethereum",
                "chain_asset_id": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                "asset_symbol": "USDC",
                "canonical_asset_id": None,
            }
        ]
    }


@pytest.mark.asyncio
async def test_save_workspace_snapshot_rejects_revision_skips():
    store = GraphSessionStore(_writable_pool())
    workspace = _workspace_snapshot().model_copy(update={"revision": 3})

    with pytest.raises(SnapshotRevisionConflictError, match="Stale workspace snapshot revision"):
        await store.save_workspace_snapshot(
            session_id="00000000-0000-0000-0000-000000000123",
            owner_user_id="owner-1",
            workspace=workspace,
            saved_at=datetime.now(timezone.utc),
            expected_previous_revision=0,
        )


@pytest.mark.asyncio
async def test_save_workspace_snapshot_persists_expected_revision():
    pool = _writable_pool()
    store = GraphSessionStore(pool)
    workspace = _workspace_snapshot().model_copy(
        update={
            "revision": 1,
            "nodeAssetScopes": {
                "ethereum:address:0xabc": [
                    {
                        "mode": "asset",
                        "chain": "ethereum",
                        "chain_asset_id": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                        "asset_symbol": "USDC",
                    }
                ]
            },
        }
    )
    saved_at = datetime.now(timezone.utc)

    await store.save_workspace_snapshot(
        session_id="00000000-0000-0000-0000-000000000123",
        owner_user_id="owner-1",
        workspace=workspace,
        saved_at=saved_at,
        expected_previous_revision=0,
    )

    assert pool._conn.execute.await_args.args[2] == saved_at
    assert '"nodeAssetScopes": {"ethereum:address:0xabc": [{"mode": "asset", "chain": "ethereum", "chain_asset_id": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "asset_symbol": "USDC"}]}' in pool._conn.execute.await_args.args[1]
