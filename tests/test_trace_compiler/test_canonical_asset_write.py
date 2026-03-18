"""
Unit tests for TraceCompiler._upsert_canonical_assets.

Verifies that CanonicalAsset nodes are MERGE'd into Neo4j after a non-empty
expansion, using only unique symbols, and that failures are swallowed silently.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.trace_compiler.compiler import TraceCompiler
from src.trace_compiler.models import InvestigationEdge


def _edge(symbol, canonical_id, asset_chain="ethereum"):
    return InvestigationEdge(
        edge_id="e1",
        source_node_id="a:address:0xaaa",
        target_node_id="a:address:0xbbb",
        branch_id="br1",
        path_id="p1",
        edge_type="transfer",
        asset_symbol=symbol,
        canonical_asset_id=canonical_id,
        asset_chain=asset_chain,
    )


def _neo4j_mock():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.run = AsyncMock()
    driver = MagicMock()
    driver.session = MagicMock(return_value=session)
    return driver, session


@pytest.mark.asyncio
async def test_upsert_single_asset_calls_neo4j():
    """Single edge with canonical_asset_id triggers one Neo4j MERGE call."""
    driver, session = _neo4j_mock()
    compiler = TraceCompiler(neo4j_driver=driver)
    edges = [_edge("USDC", "ethereum:USDC:0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")]

    await compiler._upsert_canonical_assets(edges)

    session.run.assert_called_once()
    call_args = session.run.call_args
    params = call_args.kwargs.get("assets") or call_args.args[1]
    assert len(params) == 1
    assert params[0]["symbol"] == "USDC"


@pytest.mark.asyncio
async def test_upsert_deduplicates_same_symbol():
    """Multiple edges with the same symbol produce only one MERGE param."""
    driver, session = _neo4j_mock()
    compiler = TraceCompiler(neo4j_driver=driver)
    edges = [
        _edge("USDT", "cid-usdt"),
        _edge("USDT", "cid-usdt"),
        _edge("USDT", "cid-usdt"),
    ]

    await compiler._upsert_canonical_assets(edges)

    session.run.assert_called_once()
    params = session.run.call_args.kwargs.get("assets") or session.run.call_args.args[1]
    assert len(params) == 1


@pytest.mark.asyncio
async def test_upsert_multiple_distinct_assets():
    """Three different symbols produce three MERGE params."""
    driver, session = _neo4j_mock()
    compiler = TraceCompiler(neo4j_driver=driver)
    edges = [
        _edge("USDC", "cid-usdc"),
        _edge("USDT", "cid-usdt"),
        _edge("WETH", "cid-weth"),
    ]

    await compiler._upsert_canonical_assets(edges)

    params = session.run.call_args.kwargs.get("assets") or session.run.call_args.args[1]
    symbols = {p["symbol"] for p in params}
    assert symbols == {"USDC", "USDT", "WETH"}


@pytest.mark.asyncio
async def test_upsert_skips_edges_without_canonical_id():
    """Edges with no canonical_asset_id are silently skipped."""
    driver, session = _neo4j_mock()
    compiler = TraceCompiler(neo4j_driver=driver)
    edges = [
        _edge("ETH", None),          # no canonical_asset_id
        _edge(None, "cid-x"),        # no symbol
        _edge("USDC", "cid-usdc"),   # valid
    ]

    await compiler._upsert_canonical_assets(edges)

    params = session.run.call_args.kwargs.get("assets") or session.run.call_args.args[1]
    assert len(params) == 1
    assert params[0]["symbol"] == "USDC"


@pytest.mark.asyncio
async def test_upsert_no_valid_edges_skips_neo4j():
    """No valid edges → Neo4j is never called."""
    driver, session = _neo4j_mock()
    compiler = TraceCompiler(neo4j_driver=driver)
    edges = [_edge("ETH", None), _edge(None, "cid")]

    await compiler._upsert_canonical_assets(edges)

    session.run.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_empty_edge_list_skips_neo4j():
    """Empty edge list → Neo4j is never called."""
    driver, session = _neo4j_mock()
    compiler = TraceCompiler(neo4j_driver=driver)

    await compiler._upsert_canonical_assets([])

    session.run.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_neo4j_error_is_swallowed():
    """Neo4j write failure is swallowed — never propagates."""
    driver, session = _neo4j_mock()
    session.run = AsyncMock(side_effect=RuntimeError("bolt connection lost"))
    compiler = TraceCompiler(neo4j_driver=driver)
    edges = [_edge("USDC", "cid-usdc")]

    # Must not raise
    await compiler._upsert_canonical_assets(edges)


@pytest.mark.asyncio
async def test_upsert_no_neo4j_driver_skips_silently():
    """Without a Neo4j driver, method returns immediately without error."""
    compiler = TraceCompiler(neo4j_driver=None)
    edges = [_edge("USDC", "cid-usdc")]

    # Must not raise; no driver means no write
    await compiler._upsert_canonical_assets(edges)
