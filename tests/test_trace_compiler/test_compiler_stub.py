"""
Unit tests for TraceCompiler stub implementations.

Verifies that the Phase 3 stubs return correctly-shaped
ExpansionResponseV2 / SessionCreateResponse payloads and that all
required fields are present.
"""

from datetime import datetime
from datetime import timezone
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from src.trace_compiler.compiler import _expansion_cache_key
from src.trace_compiler.compiler import TraceCompiler
from src.trace_compiler.lineage import branch_id as mk_branch_id
from src.trace_compiler.lineage import edge_id as mk_edge_id
from src.trace_compiler.lineage import lineage_id as mk_lineage_id
from src.trace_compiler.lineage import path_id as mk_path_id
from src.trace_compiler.models import (
    ExpandOptions,
    ExpandRequest,
    ExpansionResponseV2,
    InvestigationEdge,
    InvestigationNode,
    SessionCreateRequest,
    SessionCreateResponse,
)


@pytest.fixture
def compiler():
    return TraceCompiler()


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_):
        return None


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_returns_session_create_response(compiler):
    req = SessionCreateRequest(seed_address="0xabc123", seed_chain="ethereum")
    resp = await compiler.create_session(req)
    assert isinstance(resp, SessionCreateResponse)


@pytest.mark.asyncio
async def test_create_session_session_id_is_uuid(compiler):
    import uuid
    req = SessionCreateRequest(seed_address="0xabc123", seed_chain="ethereum")
    resp = await compiler.create_session(req)
    uuid.UUID(resp.session_id)  # raises if not valid UUID


@pytest.mark.asyncio
async def test_create_session_root_node_has_correct_chain(compiler):
    req = SessionCreateRequest(seed_address="1A1zP1e", seed_chain="bitcoin")
    resp = await compiler.create_session(req)
    assert resp.root_node.chain == "bitcoin"


@pytest.mark.asyncio
async def test_create_session_root_node_depth_is_zero(compiler):
    req = SessionCreateRequest(seed_address="0xabc", seed_chain="ethereum")
    resp = await compiler.create_session(req)
    assert resp.root_node.depth == 0


@pytest.mark.asyncio
async def test_create_session_root_node_has_lineage_fields(compiler):
    req = SessionCreateRequest(seed_address="0xabc", seed_chain="ethereum")
    resp = await compiler.create_session(req)
    node = resp.root_node
    assert node.branch_id
    assert node.path_id
    assert node.lineage_id


@pytest.mark.asyncio
async def test_create_session_different_calls_produce_different_session_ids(compiler):
    req = SessionCreateRequest(seed_address="0xabc", seed_chain="ethereum")
    r1 = await compiler.create_session(req)
    r2 = await compiler.create_session(req)
    assert r1.session_id != r2.session_id


# ---------------------------------------------------------------------------
# expand
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_returns_expansion_response_v2(compiler):
    req = ExpandRequest(
        operation_type="expand_next",
        seed_node_id="ethereum:address:0xabc",
        options=ExpandOptions(),
    )
    resp = await compiler.expand("session-1", req)
    assert isinstance(resp, ExpansionResponseV2)


@pytest.mark.asyncio
async def test_expand_session_id_matches(compiler):
    req = ExpandRequest(
        operation_type="expand_next",
        seed_node_id="ethereum:address:0xabc",
    )
    resp = await compiler.expand("my-session", req)
    assert resp.session_id == "my-session"


@pytest.mark.asyncio
async def test_expand_operation_type_matches(compiler):
    req = ExpandRequest(
        operation_type="expand_prev",
        seed_node_id="bitcoin:address:1A1z",
    )
    resp = await compiler.expand("s", req)
    assert resp.operation_type == "expand_prev"


@pytest.mark.asyncio
async def test_expand_chain_context_derived_from_node_id(compiler):
    req = ExpandRequest(
        operation_type="expand_next",
        seed_node_id="solana:address:5QNe",
    )
    resp = await compiler.expand("s", req)
    assert resp.chain_context.primary_chain == "solana"


@pytest.mark.asyncio
async def test_expand_stub_returns_empty_nodes_and_edges(compiler):
    req = ExpandRequest(
        operation_type="expand_next",
        seed_node_id="ethereum:address:0xabc",
    )
    resp = await compiler.expand("s", req)
    assert resp.added_nodes == []
    assert resp.added_edges == []


@pytest.mark.asyncio
async def test_expand_has_required_timestamp(compiler):
    req = ExpandRequest(
        operation_type="expand_next",
        seed_node_id="ethereum:address:0xabc",
    )
    resp = await compiler.expand("s", req)
    assert resp.timestamp is not None


@pytest.mark.asyncio
async def test_expand_cache_hit_restamps_session_lineage():
    old_session_id = "session-old"
    new_session_id = "session-new"
    seed_node_id = "ethereum:address:0xabc"
    old_branch_id = mk_branch_id(old_session_id, seed_node_id, 0)
    old_path_id = mk_path_id(old_branch_id, 0)
    old_node = InvestigationNode(
        node_id="ethereum:address:0xdef",
        lineage_id=mk_lineage_id(old_session_id, old_branch_id, old_path_id, 1),
        node_type="address",
        branch_id=old_branch_id,
        path_id=old_path_id,
        depth=1,
        display_label="0xdef",
        chain="ethereum",
        expandable_directions=["next"],
    )
    old_edge = InvestigationEdge(
        edge_id=mk_edge_id(seed_node_id, old_node.node_id, old_branch_id, "0xfeed"),
        source_node_id=seed_node_id,
        target_node_id=old_node.node_id,
        branch_id=old_branch_id,
        path_id=old_path_id,
        edge_type="transfer",
        tx_hash="0xfeed",
    )
    redis = MagicMock()
    redis.get = AsyncMock(return_value='{"operation_type":"expand_next","seed_node_id":"ethereum:address:0xabc","seed_lineage_id":"seed-lineage-old","branch_id":"ignored","expansion_depth":1,"nodes":[%s],"edges":[%s],"has_more":false,"pagination":{"page_size":50,"max_results":50,"has_more":false,"next_token":null},"layout_hints":{"suggested_layout":"layered","anchor_node_ids":["ethereum:address:0xabc"],"new_branch_root_id":"ethereum:address:0xdef","collapse_candidates":[]},"chain_context":{"primary_chain":"ethereum","chains_present":["ethereum"]},"asset_context":{"assets_present":[],"total_value_fiat":null},"timestamp":"2026-01-01T00:00:00+00:00"}' % (
            old_node.model_dump_json(),
            old_edge.model_dump_json(),
        ))
    compiler = TraceCompiler(redis_client=redis)

    req = ExpandRequest(
        operation_type="expand_next",
        seed_node_id=seed_node_id,
        seed_lineage_id="seed-lineage-new",
    )
    resp = await compiler.expand(new_session_id, req)

    expected_branch_id = mk_branch_id(new_session_id, seed_node_id, 0)
    expected_path_id = mk_path_id(expected_branch_id, 0)
    assert resp.seed_lineage_id == "seed-lineage-new"
    assert resp.branch_id == expected_branch_id
    assert resp.added_nodes[0].branch_id == expected_branch_id
    assert resp.added_nodes[0].path_id == expected_path_id
    assert resp.added_nodes[0].lineage_id == mk_lineage_id(
        new_session_id,
        expected_branch_id,
        expected_path_id,
        resp.added_nodes[0].depth,
    )
    assert resp.added_edges[0].branch_id == expected_branch_id
    assert resp.added_edges[0].path_id == expected_path_id
    assert resp.added_edges[0].edge_id == mk_edge_id(
        resp.added_edges[0].source_node_id,
        resp.added_edges[0].target_node_id,
        expected_branch_id,
        resp.added_edges[0].tx_hash,
    )


def test_expansion_cache_key_is_session_scoped_and_option_sensitive():
    base_request = ExpandRequest(
        operation_type="expand_next",
        seed_node_id="ethereum:address:0xabc",
        options=ExpandOptions(
            depth=1,
            max_results=25,
            page_size=25,
            asset_filter=["USDC", "eth"],
            min_value_fiat=100.0,
            include_services=True,
            follow_bridges=True,
        ),
    )
    reordered_assets_request = ExpandRequest(
        operation_type="expand_next",
        seed_node_id="ethereum:address:0xabc",
        options=ExpandOptions(
            depth=1,
            max_results=25,
            page_size=25,
            asset_filter=["ETH", "usdc"],
            min_value_fiat=100.0,
            include_services=True,
            follow_bridges=True,
        ),
    )
    deeper_request = ExpandRequest(
        operation_type="expand_next",
        seed_node_id="ethereum:address:0xabc",
        options=ExpandOptions(depth=2, max_results=25, page_size=25),
    )

    key_a = _expansion_cache_key("session-a", base_request)
    key_b = _expansion_cache_key("session-b", base_request)
    key_reordered = _expansion_cache_key("session-a", reordered_assets_request)
    key_deeper = _expansion_cache_key("session-a", deeper_request)

    assert key_a != key_b
    assert key_a == key_reordered
    assert key_a != key_deeper


@pytest.mark.asyncio
async def test_expand_neighbors_splits_max_results_across_directions():
    compiler = TraceCompiler()
    request = ExpandRequest(
        operation_type="expand_neighbors",
        seed_node_id="ethereum:address:0xabc",
        options=ExpandOptions(max_results=5, page_size=25, depth=1),
    )

    class FakeCompiler:
        def __init__(self):
            self.forward_limits = []
            self.backward_limits = []

        async def expand_next(self, **kwargs):
            self.forward_limits.append(kwargs["options"].max_results)
            branch_id = kwargs["branch_id"]
            path_id = mk_path_id(branch_id, kwargs["path_sequence"])
            nodes = []
            edges = []
            for index in range(kwargs["options"].max_results):
                node_id = f"ethereum:address:0xfwd{index}"
                nodes.append(
                    InvestigationNode(
                        node_id=node_id,
                        lineage_id=mk_lineage_id(
                            kwargs["session_id"],
                            branch_id,
                            path_id,
                            1,
                        ),
                        node_type="address",
                        branch_id=branch_id,
                        path_id=path_id,
                        depth=1,
                        display_label=node_id,
                        chain="ethereum",
                        expandable_directions=["next"],
                    )
                )
                edges.append(
                    InvestigationEdge(
                        edge_id=mk_edge_id(
                            request.seed_node_id,
                            node_id,
                            branch_id,
                            f"0xfwd{index}",
                        ),
                        source_node_id=request.seed_node_id,
                        target_node_id=node_id,
                        branch_id=branch_id,
                        path_id=path_id,
                        edge_type="transfer",
                        tx_hash=f"0xfwd{index}",
                    )
                )
            return nodes, edges

        async def expand_prev(self, **kwargs):
            self.backward_limits.append(kwargs["options"].max_results)
            branch_id = kwargs["branch_id"]
            path_id = mk_path_id(branch_id, kwargs["path_sequence"])
            nodes = []
            edges = []
            for index in range(kwargs["options"].max_results):
                node_id = f"ethereum:address:0xbwd{index}"
                nodes.append(
                    InvestigationNode(
                        node_id=node_id,
                        lineage_id=mk_lineage_id(
                            kwargs["session_id"],
                            branch_id,
                            path_id,
                            1,
                        ),
                        node_type="address",
                        branch_id=branch_id,
                        path_id=path_id,
                        depth=1,
                        display_label=node_id,
                        chain="ethereum",
                        expandable_directions=["prev"],
                    )
                )
                edges.append(
                    InvestigationEdge(
                        edge_id=mk_edge_id(
                            node_id,
                            request.seed_node_id,
                            branch_id,
                            f"0xbwd{index}",
                        ),
                        source_node_id=node_id,
                        target_node_id=request.seed_node_id,
                        branch_id=branch_id,
                        path_id=path_id,
                        edge_type="transfer",
                        tx_hash=f"0xbwd{index}",
                    )
                )
            return nodes, edges

    fake = FakeCompiler()
    compiler._chain_compilers = {"ethereum": fake}

    response = await compiler.expand("session-neighbors", request)

    assert fake.forward_limits == [3]
    assert fake.backward_limits == [2]
    assert len(response.added_nodes) == 5
    assert len(response.added_edges) == 5


# ---------------------------------------------------------------------------
# get_bridge_hop_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_hop_status_returns_pending(compiler):
    resp = await compiler.get_bridge_hop_status("session-1", "hop-123")
    assert resp.status == "pending"
    assert resp.hop_id == "hop-123"


@pytest.mark.asyncio
async def test_bridge_hop_status_returns_destination_fields_from_db():
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "status": "completed",
            "destination_tx_hash": "0xabc123",
            "destination_chain": "bitcoin",
            "destination_address": "bc1qexample",
            "correlation_confidence": 0.99,
            "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
    )
    pg = MagicMock()
    pg.acquire = MagicMock(return_value=_AcquireCtx(conn))
    compiler = TraceCompiler(postgres_pool=pg)

    resp = await compiler.get_bridge_hop_status("session-1", "hop-123")

    assert resp.status == "completed"
    assert resp.destination_tx_hash == "0xabc123"
    assert resp.destination_chain == "bitcoin"
    assert resp.destination_address == "bc1qexample"
    assert resp.correlation_confidence == 0.99
