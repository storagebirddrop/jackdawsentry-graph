"""
Unit tests for TraceCompiler Redis expansion cache (T7.2).

Verifies that:
- Cached results are returned on the second call without hitting the chain compiler.
- A Redis miss falls through to the chain compiler.
- Redis failures are swallowed (not propagated to the caller).
- Cache key includes session_id, seed_node_id, operation, max_results.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.trace_compiler.compiler import TraceCompiler, _expansion_cache_key
from src.trace_compiler.models import (
    ExpandOptions,
    ExpandRequest,
    InvestigationNode,
    AddressNodeData,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_node_id():
    return "ethereum:address:0xabc"


def _request(op="expand_next", max_results=10):
    return ExpandRequest(
        seed_node_id=_seed_node_id(),
        seed_lineage_id="lineage-x",
        operation_type=op,
        options=ExpandOptions(max_results=max_results),
    )


def _make_node():
    return InvestigationNode(
        node_id="ethereum:address:0xdest",
        node_type="address",
        lineage_id="lin",
        branch_id="br",
        path_id="pa",
        depth=1,
        chain="ethereum",
        display_label="0xdest",
        expandable_directions=["next"],
        address_data=AddressNodeData(address="0xdest", address_type="eoa"),
    )


def _redis_miss():
    """Redis client whose GET always returns None (cache miss)."""
    r = MagicMock()
    r.get = AsyncMock(return_value=None)
    r.setex = AsyncMock(return_value=True)
    return r


def _redis_hit(nodes, edges):
    """Redis client whose GET returns a serialised expansion result."""
    payload = json.dumps({
        "nodes": [n.model_dump(mode="json") for n in nodes],
        "edges": [e.model_dump(mode="json") for e in edges],
    })
    r = MagicMock()
    r.get = AsyncMock(return_value=payload)
    r.setex = AsyncMock(return_value=True)
    return r


def _redis_error():
    """Redis client whose GET always raises."""
    r = MagicMock()
    r.get = AsyncMock(side_effect=Exception("Redis down"))
    r.setex = AsyncMock(side_effect=Exception("Redis down"))
    return r


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------


def test_cache_key_deterministic():
    k1 = _expansion_cache_key("s", "n", "expand_next", 10)
    k2 = _expansion_cache_key("s", "n", "expand_next", 10)
    assert k1 == k2


def test_cache_key_differs_by_session():
    k1 = _expansion_cache_key("session-A", "n", "expand_next", 10)
    k2 = _expansion_cache_key("session-B", "n", "expand_next", 10)
    assert k1 != k2


def test_cache_key_differs_by_operation():
    k1 = _expansion_cache_key("s", "n", "expand_next", 10)
    k2 = _expansion_cache_key("s", "n", "expand_prev", 10)
    assert k1 != k2


def test_cache_key_differs_by_max_results():
    k1 = _expansion_cache_key("s", "n", "expand_next", 10)
    k2 = _expansion_cache_key("s", "n", "expand_next", 20)
    assert k1 != k2


def test_cache_key_prefixed():
    k = _expansion_cache_key("s", "n", "expand_next", 10)
    assert k.startswith("tc:")


# ---------------------------------------------------------------------------
# Cache miss → chain compiler invoked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_cache_miss_hits_chain_compiler():
    """On cache miss, the chain compiler is called."""
    redis = _redis_miss()
    compiler = TraceCompiler(redis_client=redis)

    node = _make_node()
    mock_compiler = MagicMock()
    mock_compiler.expand_next = AsyncMock(return_value=([node], []))
    compiler._chain_compilers["ethereum"] = mock_compiler

    await compiler.expand("session-1", _request())

    mock_compiler.expand_next.assert_called_once()


@pytest.mark.asyncio
async def test_expand_cache_miss_writes_to_redis():
    """After a successful expansion, result is written to Redis."""
    redis = _redis_miss()
    compiler = TraceCompiler(redis_client=redis)

    node = _make_node()
    mock_compiler = MagicMock()
    mock_compiler.expand_next = AsyncMock(return_value=([node], []))
    compiler._chain_compilers["ethereum"] = mock_compiler

    await compiler.expand("session-1", _request())

    redis.setex.assert_called_once()
    args = redis.setex.call_args
    assert args[0][1] == 900  # TTL = 15 min


# ---------------------------------------------------------------------------
# Cache hit → chain compiler NOT called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_cache_hit_skips_chain_compiler():
    """On cache hit, the chain compiler is NOT called."""
    node = _make_node()
    redis = _redis_hit([node], [])
    compiler = TraceCompiler(redis_client=redis)

    mock_compiler = MagicMock()
    mock_compiler.expand_next = AsyncMock(return_value=([node], []))
    compiler._chain_compilers["ethereum"] = mock_compiler

    result = await compiler.expand("session-1", _request())

    mock_compiler.expand_next.assert_not_called()
    assert len(result.added_nodes) == 1


@pytest.mark.asyncio
async def test_expand_cache_hit_returns_correct_node():
    node = _make_node()
    redis = _redis_hit([node], [])
    compiler = TraceCompiler(redis_client=redis)
    compiler._chain_compilers["ethereum"] = MagicMock()

    result = await compiler.expand("session-2", _request())

    assert result.added_nodes[0].node_id == node.node_id


# ---------------------------------------------------------------------------
# Redis failure → swallowed, falls through to chain compiler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_redis_get_error_falls_through():
    """Redis GET failure is swallowed; chain compiler is called."""
    redis = _redis_error()
    compiler = TraceCompiler(redis_client=redis)

    node = _make_node()
    mock_compiler = MagicMock()
    mock_compiler.expand_next = AsyncMock(return_value=([node], []))
    compiler._chain_compilers["ethereum"] = mock_compiler

    # Should not raise
    result = await compiler.expand("session-1", _request())
    mock_compiler.expand_next.assert_called_once()
    assert result is not None


@pytest.mark.asyncio
async def test_expand_redis_setex_error_swallowed():
    """Redis SETEX failure after expansion is swallowed; result still returned."""
    redis = _redis_miss()
    redis.setex = AsyncMock(side_effect=Exception("setex fail"))
    compiler = TraceCompiler(redis_client=redis)

    node = _make_node()
    mock_compiler = MagicMock()
    mock_compiler.expand_next = AsyncMock(return_value=([node], []))
    compiler._chain_compilers["ethereum"] = mock_compiler

    result = await compiler.expand("session-1", _request())
    assert len(result.added_nodes) == 1
