"""Session workspace helpers for the standalone graph product."""

from __future__ import annotations

import json
from typing import Any
from typing import Mapping

from src.trace_compiler.lineage import branch_id as mk_branch_id
from src.trace_compiler.lineage import lineage_id as mk_lineage_id
from src.trace_compiler.lineage import node_id as mk_node_id
from src.trace_compiler.lineage import path_id as mk_path_id
from src.trace_compiler.models import AddressNodeData
from src.trace_compiler.models import InvestigationNode
from src.trace_compiler.models import NodeStateSnapshot
from src.trace_compiler.models import RecentSessionSummary
from src.trace_compiler.models import WorkspaceBranchSnapshot
from src.trace_compiler.models import WorkspaceSnapshotV1

_BRANCH_COLORS = (
    "#3b82f6",
    "#10b981",
    "#f59e0b",
    "#8b5cf6",
    "#ef4444",
    "#06b6d4",
    "#f97316",
    "#84cc16",
)
_EVM_SESSION_CHAINS = {
    "ethereum",
    "polygon",
    "bsc",
    "arbitrum",
    "base",
    "avalanche",
    "optimism",
    "starknet",
    "injective",
}


class SnapshotRevisionConflictError(RuntimeError):
    """Raised when a snapshot write loses the revision race."""


def _branch_color(branch_id: str) -> str:
    hash_value = 0
    for char in branch_id:
        hash_value = (hash_value * 31 + ord(char)) & 0xFFFFFFFF
    return _BRANCH_COLORS[hash_value % len(_BRANCH_COLORS)]


def _truncate_identifier(value: str, *, head: int = 10, tail: int = 8) -> str:
    if not value or len(value) <= head + tail + 3:
        return value
    return f"{value[:head]}...{value[-tail:]}"


def _canonical_seed_identifier(chain: str, address: str) -> str:
    if not isinstance(address, str):
        return address
    value = address.strip()
    if chain in _EVM_SESSION_CHAINS and value.startswith("0x"):
        return value.lower()
    return value


def _bootstrap_root_node(row: Mapping[str, Any]) -> InvestigationNode:
    session_id = str(row["session_id"])
    seed_address = str(row["seed_address"])
    seed_chain = str(row["seed_chain"])
    canonical_seed = _canonical_seed_identifier(seed_chain, seed_address)
    node_id = mk_node_id(seed_chain, "address", canonical_seed)
    branch_id = mk_branch_id(session_id, node_id, 0)
    path_id = mk_path_id(branch_id, 0)
    lineage_id = mk_lineage_id(session_id, branch_id, path_id, 0)

    return InvestigationNode(
        node_id=node_id,
        lineage_id=lineage_id,
        node_type="address",
        branch_id=branch_id,
        path_id=path_id,
        depth=0,
        display_label=_truncate_identifier(seed_address),
        chain=seed_chain,
        expandable_directions=["prev", "next", "neighbors"],
        address_data=AddressNodeData(
            address=seed_address,
            chain=seed_chain,
            address_type="unknown",
        ),
    )


def _bootstrap_branch(root_node: InvestigationNode) -> WorkspaceBranchSnapshot:
    return WorkspaceBranchSnapshot(
        branchId=root_node.branch_id,
        color=_branch_color(root_node.branch_id),
        seedNodeId=root_node.node_id,
        minDepth=root_node.depth,
        maxDepth=root_node.depth,
        nodeCount=1,
    )


def _parse_snapshot(raw_snapshot: Any) -> Any:
    if isinstance(raw_snapshot, str):
        try:
            return json.loads(raw_snapshot)
        except json.JSONDecodeError:
            return None
    return raw_snapshot


def _apply_legacy_node_states(
    workspace: WorkspaceSnapshotV1,
    raw_snapshot: Any,
) -> WorkspaceSnapshotV1:
    if not isinstance(raw_snapshot, list):
        return workspace

    try:
        node_states = [NodeStateSnapshot.model_validate(item) for item in raw_snapshot]
    except Exception:
        return workspace

    state_by_node_id = {state.node_id: state for state in node_states}
    if not state_by_node_id:
        return workspace

    updated_nodes = []
    updated_positions = dict(workspace.positions)
    for node in workspace.nodes:
        state = state_by_node_id.get(node.node_id)
        if state is None:
            updated_nodes.append(node)
            continue

        updated_nodes.append(
            node.model_copy(
                update={
                    "is_pinned": state.is_pinned,
                    "is_hidden": state.is_hidden,
                }
            )
        )
        if state.position_hint:
            updated_positions[node.node_id] = state.position_hint

    return workspace.model_copy(
        update={
            "nodes": updated_nodes,
            "positions": updated_positions,
        }
    )


class GraphSessionStore:
    """Helpers for loading and storing session workspace snapshots."""

    def __init__(self, postgres_pool):
        self._pg = postgres_pool

    def normalize_workspace(
        self,
        row: Mapping[str, Any],
    ) -> tuple[WorkspaceSnapshotV1, str, Any]:
        raw_snapshot = _parse_snapshot(row.get("snapshot"))

        if isinstance(raw_snapshot, dict):
            try:
                workspace = WorkspaceSnapshotV1.model_validate(raw_snapshot)
                return workspace, "full", raw_snapshot
            except Exception:
                pass

        root_node = _bootstrap_root_node(row)
        legacy_workspace = WorkspaceSnapshotV1(
            sessionId=str(row["session_id"]),
            nodes=[root_node],
            edges=[],
            positions={},
            branches=[_bootstrap_branch(root_node)],
            workspacePreferences=None,
        )
        legacy_workspace = _apply_legacy_node_states(legacy_workspace, raw_snapshot)
        return legacy_workspace, "legacy_bootstrap", raw_snapshot

    def merge_node_states(
        self,
        workspace: WorkspaceSnapshotV1,
        node_states: list[NodeStateSnapshot],
    ) -> WorkspaceSnapshotV1:
        """Apply legacy node-state snapshots onto a full workspace payload."""
        if not node_states:
            return workspace

        state_by_node_id = {state.node_id: state for state in node_states}
        updated_nodes = []
        updated_positions = dict(workspace.positions)

        for node in workspace.nodes:
            state = state_by_node_id.get(node.node_id)
            if state is None:
                updated_nodes.append(node)
                continue

            updated_nodes.append(
                node.model_copy(
                    update={
                        "is_pinned": state.is_pinned,
                        "is_hidden": state.is_hidden,
                    }
                )
            )
            if state.position_hint is not None:
                updated_positions[node.node_id] = state.position_hint

        return workspace.model_copy(
            update={
                "nodes": updated_nodes,
                "positions": updated_positions,
            }
        )

    async def list_recent_sessions(
        self,
        *,
        owner_user_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        if self._pg is None:
            raise RuntimeError("Session store unavailable")

        async with self._pg.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    session_id,
                    seed_address,
                    seed_chain,
                    created_at,
                    updated_at,
                    snapshot_saved_at
                FROM graph_sessions
                WHERE created_by = $1
                ORDER BY COALESCE(snapshot_saved_at, updated_at, created_at) DESC
                LIMIT $2
                """,
                owner_user_id,
                limit,
            )

        items: list[dict[str, Any]] = []
        for row in rows:
            summary = RecentSessionSummary(
                session_id=str(row["session_id"]),
                seed_address=row.get("seed_address"),
                seed_chain=row.get("seed_chain"),
                created_at=row.get("created_at"),
                updated_at=row.get("updated_at"),
                snapshot_saved_at=row.get("snapshot_saved_at"),
            )
            items.append(summary.model_dump())

        return items

    async def save_workspace_snapshot(
        self,
        *,
        session_id: str,
        owner_user_id: str,
        workspace: WorkspaceSnapshotV1,
        saved_at,
        expected_previous_revision: int,
    ) -> None:
        if self._pg is None:
            raise RuntimeError("Session store unavailable")

        async with self._pg.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE graph_sessions
                SET snapshot = $1::jsonb,
                    snapshot_saved_at = $2
                WHERE session_id = $3::uuid
                  AND created_by = $4
                  AND COALESCE(
                        CASE
                            WHEN jsonb_typeof(snapshot) = 'object' THEN (snapshot->>'revision')::int
                            ELSE NULL
                        END,
                        0
                  ) = $5
                """,
                json.dumps(workspace.model_dump(mode="json")),
                saved_at,
                session_id,
                owner_user_id,
                expected_previous_revision,
            )
        if result != "UPDATE 1":
            raise SnapshotRevisionConflictError("Stale workspace snapshot revision")
