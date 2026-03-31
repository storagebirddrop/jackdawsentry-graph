"""
Unit tests for TraceCompiler Redis expansion cache (T7.2).

Verifies that:
- Cached results are returned on the second call without hitting the chain compiler.
- A Redis miss falls through to the chain compiler.
- Redis failures are swallowed (not propagated to the caller).
- Cache key excludes session_id: same expansion is shared
  across all investigation sessions; session_id is overridden at serve time.
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
    """Redis client whose GET returns a serialised expansion result (full format).

    Builds the payload using the same format that TraceCompiler.expand() writes
    to Redis, so that the cache deserialization path is exercised correctly.
    """
    from src.trace_compiler.models import (
        ExpansionResponseV2, ChainContext, AssetContext, LayoutHints, PaginationMeta
    )
    from datetime import datetime, timezone

    response = ExpansionResponseV2(
        operation_id="test-op-id",
        operation_type="expand_next",
        session_id="test-session",
        seed_node_id=_seed_node_id(),
        seed_lineage_id="lineage-x",
        branch_id="test-branch",
        expansion_depth=0,
        added_nodes=nodes,
        added_edges=edges,
        has_more=False,
        pagination=PaginationMeta(),
        layout_hints=LayoutHints(),
        chain_context=ChainContext(primary_chain="ethereum", chains_present=["ethereum"]),
        asset_context=AssetContext(),
        timestamp=datetime(2026, 3, 17, tzinfo=timezone.utc),
    )
    payload = json.dumps({
        "operation_id": response.operation_id,
        "operation_type": response.operation_type,
        "session_id": response.session_id,
        "seed_node_id": response.seed_node_id,
        "seed_lineage_id": response.seed_lineage_id,
        "branch_id": response.branch_id,
        "expansion_depth": response.expansion_depth,
        "nodes": [n.model_dump(mode="json") for n in response.added_nodes],
        "edges": [e.model_dump(mode="json") for e in response.added_edges],
        "has_more": response.has_more,
        "pagination": response.pagination.model_dump(mode="json"),
        "layout_hints": response.layout_hints.model_dump(mode="json"),
        "chain_context": response.chain_context.model_dump(mode="json"),
        "asset_context": response.asset_context.model_dump(mode="json"),
        "timestamp": response.timestamp.isoformat(),
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
    r = _request("expand_next", max_results=10)
    r.seed_node_id = "n"
    k1 = _expansion_cache_key("session-a", r)
    k2 = _expansion_cache_key("session-a", r)
    assert k1 == k2


def test_cache_key_scoped_to_session():
    """Different sessions must produce different cache keys (security isolation)."""
    r = _request("expand_next", max_results=10)
    r.seed_node_id = "n"
    k1 = _expansion_cache_key("session-a", r)
    k2 = _expansion_cache_key("session-b", r)
    assert k1 != k2, "Cache must be session-scoped so results cannot bleed across investigators"


def test_cache_key_differs_by_seed():
    r1 = _request("expand_next", max_results=10)
    r1.seed_node_id = "ethereum:address:0xaaa"
    r2 = _request("expand_next", max_results=10)
    r2.seed_node_id = "ethereum:address:0xbbb"
    k1 = _expansion_cache_key("session-a", r1)
    k2 = _expansion_cache_key("session-a", r2)
    assert k1 != k2


def test_cache_key_differs_by_operation():
    r1 = _request("expand_next", max_results=10)
    r1.seed_node_id = "n"
    r2 = _request("expand_prev", max_results=10)
    r2.seed_node_id = "n"
    k1 = _expansion_cache_key("session-a", r1)
    k2 = _expansion_cache_key("session-a", r2)
    assert k1 != k2


def test_cache_key_differs_by_max_results():
    r1 = _request("expand_next", max_results=10)
    r1.seed_node_id = "n"
    r2 = _request("expand_next", max_results=20)
    r2.seed_node_id = "n"
    k1 = _expansion_cache_key("session-a", r1)
    k2 = _expansion_cache_key("session-a", r2)
    assert k1 != k2


def test_cache_key_prefixed():
    r = _request("expand_next", max_results=10)
    r.seed_node_id = "n"
    k = _expansion_cache_key("session-a", r)
    assert k.startswith("tc:")


# ---------------------------------------------------------------------------
# Time-field cache key isolation (V1 selective expansion)
# ---------------------------------------------------------------------------


def test_cache_key_differs_by_time_from():
    """Different time_from values must produce different cache keys."""
    from datetime import datetime, timezone

    r1 = ExpandRequest(
        seed_node_id="n",
        seed_lineage_id="lin",
        operation_type="expand_next",
        options=ExpandOptions(time_from=datetime(2024, 1, 1, tzinfo=timezone.utc)),
    )
    r2 = ExpandRequest(
        seed_node_id="n",
        seed_lineage_id="lin",
        operation_type="expand_next",
        options=ExpandOptions(time_from=datetime(2024, 6, 1, tzinfo=timezone.utc)),
    )
    assert _expansion_cache_key("s", r1) != _expansion_cache_key("s", r2)


def test_cache_key_differs_by_time_to():
    """Different time_to values must produce different cache keys."""
    from datetime import datetime, timezone

    r1 = ExpandRequest(
        seed_node_id="n",
        seed_lineage_id="lin",
        operation_type="expand_next",
        options=ExpandOptions(time_to=datetime(2024, 12, 31, tzinfo=timezone.utc)),
    )
    r2 = ExpandRequest(
        seed_node_id="n",
        seed_lineage_id="lin",
        operation_type="expand_next",
        options=ExpandOptions(time_to=datetime(2023, 12, 31, tzinfo=timezone.utc)),
    )
    assert _expansion_cache_key("s", r1) != _expansion_cache_key("s", r2)


def test_cache_key_differs_from_baseline_when_time_from_set():
    """A request with time_from must differ from the same request without it."""
    from datetime import datetime, timezone

    base = ExpandRequest(
        seed_node_id="n",
        seed_lineage_id="lin",
        operation_type="expand_next",
        options=ExpandOptions(),
    )
    filtered = ExpandRequest(
        seed_node_id="n",
        seed_lineage_id="lin",
        operation_type="expand_next",
        options=ExpandOptions(time_from=datetime(2024, 1, 1, tzinfo=timezone.utc)),
    )
    assert _expansion_cache_key("s", base) != _expansion_cache_key("s", filtered)


def test_cache_key_none_time_matches_default():
    """Explicit None time fields must produce the same key as omitting them."""
    r1 = ExpandRequest(
        seed_node_id="n",
        seed_lineage_id="lin",
        operation_type="expand_next",
        options=ExpandOptions(),
    )
    r2 = ExpandRequest(
        seed_node_id="n",
        seed_lineage_id="lin",
        operation_type="expand_next",
        options=ExpandOptions(time_from=None, time_to=None),
    )
    assert _expansion_cache_key("s", r1) == _expansion_cache_key("s", r2)


@pytest.mark.asyncio
async def test_expand_neighbors_propagates_time_fields():
    """expand_neighbors must forward time_from and time_to to both fwd and bwd compilers."""
    from datetime import datetime, timezone

    time_from = datetime(2024, 1, 1, tzinfo=timezone.utc)
    time_to = datetime(2024, 12, 31, tzinfo=timezone.utc)

    redis = _redis_miss()
    compiler = TraceCompiler(redis_client=redis)

    fwd_calls: list = []
    bwd_calls: list = []

    class _MockCompiler:
        async def expand_next(self, *, options, **kwargs):
            fwd_calls.append(options)
            return ([], [])

        async def expand_prev(self, *, options, **kwargs):
            bwd_calls.append(options)
            return ([], [])

    compiler._chain_compilers["ethereum"] = _MockCompiler()

    request = ExpandRequest(
        seed_node_id="ethereum:address:0xabc",
        seed_lineage_id="lin",
        operation_type="expand_neighbors",
        options=ExpandOptions(time_from=time_from, time_to=time_to),
    )
    await compiler.expand("session-1", request)

    assert len(fwd_calls) == 1, "expand_next should be called once"
    assert len(bwd_calls) == 1, "expand_prev should be called once"
    assert fwd_calls[0].time_from == time_from, "time_from not propagated to expand_next"
    assert fwd_calls[0].time_to == time_to, "time_to not propagated to expand_next"
    assert bwd_calls[0].time_from == time_from, "time_from not propagated to expand_prev"
    assert bwd_calls[0].time_to == time_to, "time_to not propagated to expand_prev"


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


@pytest.mark.asyncio
async def test_expand_cache_hit_overrides_session_id():
    """Cache hit must return the *current* session_id, not the cached one."""
    node = _make_node()
    redis = _redis_hit([node], [])  # cached under session "test-session"
    compiler = TraceCompiler(redis_client=redis)
    compiler._chain_compilers["ethereum"] = MagicMock()

    result = await compiler.expand("session-NEW", _request())

    assert result.session_id == "session-NEW", (
        "Cache hit must override session_id with current session (P1.2)"
    )


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
