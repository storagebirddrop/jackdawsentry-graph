from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from src.trace_compiler.chains.evm import EVMChainCompiler
from src.trace_compiler.compiler import TraceCompiler
from src.trace_compiler.models import AddressNodeData
from src.trace_compiler.models import AssetContext
from src.trace_compiler.models import ChainContext
from src.trace_compiler.models import ExpandOptions
from src.trace_compiler.models import ExpandRequest
from src.trace_compiler.models import ExpansionResponseV2
from src.trace_compiler.models import InvestigationNode
from src.trace_compiler.models import LayoutHints
from src.trace_compiler.models import PaginationMeta


class _AsyncCtxMgr:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *_):
        return False


def _make_request() -> ExpandRequest:
    return ExpandRequest(
        operation_type="expand_next",
        seed_node_id="ethereum:address:0xseed",
        seed_lineage_id="lineage-x",
        options=ExpandOptions(max_results=10),
    )


def _make_node() -> InvestigationNode:
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


def _pg_pool_returning(rows):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCtxMgr(conn))
    return pool


@pytest.mark.asyncio
async def test_evm_compiler_marks_neo4j_fallback_source_when_pg_empty():
    pg = _pg_pool_returning([])

    neo4j_row = {
        "counterparty": "0xneo4jaddr",
        "tx_hash": "0xtxneo",
        "value_native": 0.5,
        "asset_symbol": None,
        "canonical_asset_id": None,
        "timestamp": "2026-03-28T00:00:00Z",
    }

    class _FakeResult:
        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            yield neo4j_row

    neo4j_session = MagicMock()
    neo4j_session.run = AsyncMock(return_value=_FakeResult())
    neo4j_driver = MagicMock()
    neo4j_driver.session = MagicMock(return_value=_AsyncCtxMgr(neo4j_session))

    compiler = EVMChainCompiler(postgres_pool=pg, neo4j_driver=neo4j_driver)
    nodes, _ = await compiler.expand_next(
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address="0xseed",
        chain="ethereum",
        options=ExpandOptions(max_results=10),
    )

    assert any(node.address_data.address == "0xneo4jaddr" for node in nodes)
    assert compiler._consume_expansion_data_sources() == ["neo4j_fallback"]


@pytest.mark.asyncio
async def test_trace_compiler_expand_surfaces_integrity_warning_for_fallback():
    compiler = TraceCompiler(postgres_pool=None, redis_client=None)
    node = _make_node()

    class _FallbackCompiler:
        async def expand_next(self, **kwargs):
            return [node], []

        def _consume_expansion_data_sources(self):
            return ["neo4j_fallback"]

    compiler._chain_compilers["ethereum"] = _FallbackCompiler()

    response = await compiler.expand("session-1", _make_request())

    assert response.data_sources == ["neo4j_fallback"]
    assert response.integrity_warning is not None
    assert "Neo4j" in response.integrity_warning
    assert "PostgreSQL event store had no indexed facts" in response.integrity_warning


@pytest.mark.asyncio
async def test_trace_compiler_cache_hit_preserves_integrity_warning_metadata():
    node = _make_node()
    warning = (
        "This ETHEREUM expansion returned fallback results from Neo4j while "
        "the PostgreSQL event store had no indexed facts for the reviewed path. "
        "Treat these results as provisional until raw facts are ingested."
    )
    response = ExpansionResponseV2(
        operation_id="test-op-id",
        operation_type="expand_next",
        session_id="cached-session",
        seed_node_id="ethereum:address:0xseed",
        seed_lineage_id="lineage-x",
        branch_id="cached-branch",
        expansion_depth=1,
        added_nodes=[node],
        added_edges=[],
        has_more=False,
        pagination=PaginationMeta(),
        layout_hints=LayoutHints(),
        chain_context=ChainContext(primary_chain="ethereum", chains_present=["ethereum"]),
        asset_context=AssetContext(),
        data_sources=["neo4j_fallback"],
        integrity_warning=warning,
        timestamp=datetime(2026, 3, 28, tzinfo=timezone.utc),
    )
    payload = json.dumps(
        {
            "operation_id": response.operation_id,
            "operation_type": response.operation_type,
            "session_id": response.session_id,
            "seed_node_id": response.seed_node_id,
            "seed_lineage_id": response.seed_lineage_id,
            "branch_id": response.branch_id,
            "expansion_depth": response.expansion_depth,
            "nodes": [item.model_dump(mode="json") for item in response.added_nodes],
            "edges": [item.model_dump(mode="json") for item in response.added_edges],
            "has_more": response.has_more,
            "pagination": response.pagination.model_dump(mode="json"),
            "layout_hints": response.layout_hints.model_dump(mode="json"),
            "chain_context": response.chain_context.model_dump(mode="json"),
            "asset_context": response.asset_context.model_dump(mode="json"),
            "data_sources": response.data_sources,
            "integrity_warning": response.integrity_warning,
            "timestamp": response.timestamp.isoformat(),
        }
    )

    redis = MagicMock()
    redis.get = AsyncMock(return_value=payload)
    redis.setex = AsyncMock(return_value=True)
    compiler = TraceCompiler(redis_client=redis)

    result = await compiler.expand("session-new", _make_request())

    assert result.data_sources == ["neo4j_fallback"]
    assert result.integrity_warning == warning
