"""
Unit tests for TraceCompiler stub implementations.

Verifies that the Phase 3 stubs return correctly-shaped
ExpansionResponseV2 / SessionCreateResponse payloads and that all
required fields are present.
"""

import pytest

from src.trace_compiler.compiler import TraceCompiler
from src.trace_compiler.models import (
    ExpandOptions,
    ExpandRequest,
    ExpansionResponseV2,
    SessionCreateRequest,
    SessionCreateResponse,
)


@pytest.fixture
def compiler():
    return TraceCompiler()


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


# ---------------------------------------------------------------------------
# get_bridge_hop_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_hop_status_returns_pending(compiler):
    resp = await compiler.get_bridge_hop_status("session-1", "hop-123")
    assert resp.status == "pending"
    assert resp.hop_id == "hop-123"
