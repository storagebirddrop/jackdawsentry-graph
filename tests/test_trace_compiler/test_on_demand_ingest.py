"""
Unit tests for on-demand address ingest trigger.

Covers:
- Trigger queues a row when raw_transactions has no data
- Trigger skips (returns False) when data already exists in raw_transactions
- Trigger skips when data exists in raw_token_transfers
- Trigger is idempotent: ON CONFLICT returns False on duplicate
- Trigger returns False and swallows DB errors (best-effort)
- TraceCompiler.expand sets ingest_pending=True when expansion is empty
- TraceCompiler.expand sets ingest_pending=False when expansion returns nodes
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.trace_compiler.ingest.trigger import maybe_trigger_address_ingest


# ---------------------------------------------------------------------------
# maybe_trigger_address_ingest
# ---------------------------------------------------------------------------


def _make_pg_pool(
    tx_count: int = 0,
    token_count: int = 0,
    insert_id: int | None = 42,
):
    """Build a mock asyncpg pool with configurable query results."""
    mock_conn = AsyncMock()

    async def fetchval(query, *args):
        # First call: raw_transactions count
        # Second call: raw_token_transfers count
        # Third call: INSERT RETURNING id
        call_num = getattr(fetchval, "_calls", 0)
        fetchval._calls = call_num + 1
        if call_num == 0:
            return tx_count
        if call_num == 1:
            return token_count
        return insert_id  # INSERT RETURNING id

    fetchval._calls = 0
    mock_conn.fetchval = AsyncMock(side_effect=fetchval)

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_pool


@pytest.mark.asyncio
async def test_trigger_queues_row_when_no_data():
    """Returns True when address has no raw data and insert succeeds."""
    pool = _make_pg_pool(tx_count=0, token_count=0, insert_id=99)
    result = await maybe_trigger_address_ingest("0xabc", "ethereum", pool)
    assert result is True


@pytest.mark.asyncio
async def test_trigger_skips_when_tx_data_exists():
    """Returns False when raw_transactions already has rows for this address."""
    pool = _make_pg_pool(tx_count=5, token_count=0, insert_id=None)
    result = await maybe_trigger_address_ingest("0xabc", "ethereum", pool)
    assert result is False


@pytest.mark.asyncio
async def test_trigger_skips_when_token_transfer_data_exists():
    """Returns False when raw_token_transfers has rows (but raw_transactions is empty)."""
    pool = _make_pg_pool(tx_count=0, token_count=3, insert_id=None)
    result = await maybe_trigger_address_ingest("0xabc", "ethereum", pool)
    assert result is False


@pytest.mark.asyncio
async def test_trigger_idempotent_on_conflict():
    """ON CONFLICT DO NOTHING returns None from RETURNING → False (not an error)."""
    pool = _make_pg_pool(tx_count=0, token_count=0, insert_id=None)
    result = await maybe_trigger_address_ingest("0xabc", "ethereum", pool)
    assert result is False


@pytest.mark.asyncio
async def test_trigger_returns_false_when_pool_is_none():
    result = await maybe_trigger_address_ingest("0xabc", "ethereum", None)
    assert result is False


@pytest.mark.asyncio
async def test_trigger_swallows_db_error():
    """DB errors are swallowed — returns False, does not raise."""
    pool = MagicMock()
    pool.acquire.side_effect = Exception("DB is down")
    result = await maybe_trigger_address_ingest("0xabc", "ethereum", pool)
    assert result is False


# ---------------------------------------------------------------------------
# TraceCompiler.expand — ingest_pending propagation
# ---------------------------------------------------------------------------


def _make_expand_request(chain: str = "ethereum", address: str = "0xtest"):
    from src.trace_compiler.models import ExpandOptions, ExpandRequest
    return ExpandRequest(
        seed_node_id=f"{chain}:address:{address}",
        seed_lineage_id="lineage-x",
        operation_type="expand_next",
        options=ExpandOptions(max_results=10),
    )


@pytest.mark.asyncio
async def test_compiler_expand_ingest_pending_true_on_empty_result():
    """ingest_pending=True when chain compiler returns empty AND trigger queues a row."""
    from src.trace_compiler.compiler import TraceCompiler

    mock_chain_compiler = MagicMock()
    mock_chain_compiler.expand_next = AsyncMock(return_value=([], []))

    compiler = TraceCompiler(postgres_pool=MagicMock(), redis_client=None)
    compiler._chain_compilers["ethereum"] = mock_chain_compiler

    with patch(
        "src.trace_compiler.ingest.trigger.maybe_trigger_address_ingest",
        new=AsyncMock(return_value=True),
    ):
        result = await compiler.expand("session-1", _make_expand_request())

    assert result.ingest_pending is True


@pytest.mark.asyncio
async def test_compiler_expand_ingest_pending_false_when_nodes_returned():
    """ingest_pending=False when expansion returns nodes (trigger not called)."""
    from src.trace_compiler.compiler import TraceCompiler
    from src.trace_compiler.models import AddressNodeData, InvestigationNode

    node = InvestigationNode(
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
    mock_chain_compiler = MagicMock()
    mock_chain_compiler.expand_next = AsyncMock(return_value=([node], []))

    compiler = TraceCompiler(postgres_pool=None, redis_client=None)
    compiler._chain_compilers["ethereum"] = mock_chain_compiler

    with patch(
        "src.trace_compiler.attribution.enricher.enrich_nodes",
        new=AsyncMock(return_value=[node]),
    ):
        result = await compiler.expand("session-1", _make_expand_request())

    assert result.ingest_pending is False
    assert len(result.added_nodes) == 1
