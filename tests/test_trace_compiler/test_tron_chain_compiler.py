"""
Unit tests for TronChainCompiler (src/trace_compiler/chains/tron.py).

All DB calls are mocked — no running PostgreSQL or Neo4j required.

Covers:
- supported_chains returns ["tron"]
- expand_next / expand_prev return nodes + edges when event store has data
- Empty event store (no pg) returns empty lists without raising
- Token transfer rows from raw_token_transfers are included in results
- Bridge detection works for known bridge contracts
- Pool is None → returns empty gracefully
- Address normalization (lowercases)
- Edge direction: forward → src=seed, backward → src=counterparty
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.trace_compiler.chains.tron import TronChainCompiler
from src.trace_compiler.models import ExpandOptions

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SEED = "ta1aa2bb3cc4dd5ee6ff7gg8hh9ii0jjkk1"  # hex Tron address
COUNTERPARTY = "tb2aa3bb4cc5dd6ee7ff8gg9hh0ii1jjkk2"
TX_HASH_1 = "tx" + "a" * 62
TX_HASH_2 = "tx" + "b" * 62


def _opts(max_results=10):
    return ExpandOptions(max_results=max_results)


def _row(counterparty, tx_hash=TX_HASH_1, value=1.0, symbol=None):
    return {
        "counterparty": counterparty,
        "tx_hash": tx_hash,
        "value_native": value,
        "asset_symbol": symbol,
        "canonical_asset_id": None,
        "timestamp": None,
    }


def _token_row(counterparty, tx_hash=TX_HASH_1, value=100.0, symbol="USDT"):
    return {
        "counterparty": counterparty,
        "tx_hash": tx_hash,
        "value_native": value,
        "asset_symbol": symbol,
        "canonical_asset_id": "tether",
        "timestamp": None,
    }


class _AsyncCtxMgr:
    def __init__(self, inner):
        self._inner = inner

    async def __aenter__(self):
        return self._inner

    async def __aexit__(self, *_):
        pass


def _pg_pool_returning(rows, token_rows=None):
    """Mock asyncpg pool.

    First conn.fetch call returns ``rows`` (raw_transactions).
    Second conn.fetch call returns ``token_rows`` (raw_token_transfers),
    defaulting to empty so rows are not doubled.
    """
    conn = MagicMock()
    _token = token_rows if token_rows is not None else []
    conn.fetch = AsyncMock(side_effect=[rows, _token])
    conn.fetchrow = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCtxMgr(conn))
    return pool


def _pg_pool_returning_token_only(token_rows):
    """Mock asyncpg pool where raw_transactions returns empty, tokens return rows."""
    conn = MagicMock()
    conn.fetch = AsyncMock(side_effect=[[], token_rows])
    conn.fetchrow = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCtxMgr(conn))
    return pool


# ---------------------------------------------------------------------------
# supported_chains
# ---------------------------------------------------------------------------


def test_supported_chains():
    compiler = TronChainCompiler()
    assert compiler.supported_chains == ["tron"]


# ---------------------------------------------------------------------------
# No pool → empty results (no crash)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_next_no_pg_returns_empty():
    """No postgres, no neo4j — returns empty, does not raise."""
    compiler = TronChainCompiler(postgres_pool=None, neo4j_driver=None)
    nodes, edges = await compiler.expand_next(
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="tron",
        options=_opts(),
    )
    assert nodes == []
    assert edges == []


@pytest.mark.asyncio
async def test_expand_prev_no_pg_returns_empty():
    """No postgres — expand_prev also returns empty."""
    compiler = TronChainCompiler(postgres_pool=None, neo4j_driver=None)
    nodes, edges = await compiler.expand_prev(
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="tron",
        options=_opts(),
    )
    assert nodes == []
    assert edges == []


# ---------------------------------------------------------------------------
# expand_next — event store returns rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_next_returns_node_per_unique_counterparty():
    """Two rows with different counterparties produce two address nodes."""
    raw_rows = [
        _row(COUNTERPARTY, TX_HASH_1, 1.0),
        _row(COUNTERPARTY + "x", TX_HASH_2, 2.0),
    ]
    pg = _pg_pool_returning(raw_rows)
    compiler = TronChainCompiler(postgres_pool=pg)

    with patch.object(compiler._bridge, "is_bridge_contract", return_value=False), \
         patch.object(compiler._service, "get_record", return_value=None):
        nodes, edges = await compiler.expand_next(
            session_id="s",
            branch_id="b",
            path_sequence=0,
            depth=0,
            seed_address=SEED,
            chain="tron",
            options=_opts(),
        )

    assert len(nodes) == 2
    assert len(edges) == 2
    for node in nodes:
        assert node.node_type == "address"
        assert node.chain == "tron"


@pytest.mark.asyncio
async def test_expand_next_edges_point_forward():
    """Forward expansion: seed → counterparty."""
    raw_rows = [_row(COUNTERPARTY)]
    pg = _pg_pool_returning(raw_rows)
    compiler = TronChainCompiler(postgres_pool=pg)

    with patch.object(compiler._bridge, "is_bridge_contract", return_value=False), \
         patch.object(compiler._service, "get_record", return_value=None):
        nodes, edges = await compiler.expand_next(
            session_id="s",
            branch_id="b",
            path_sequence=0,
            depth=0,
            seed_address=SEED,
            chain="tron",
            options=_opts(),
        )

    assert len(edges) == 1
    assert edges[0].direction == "forward"
    assert SEED in edges[0].source_node_id
    assert COUNTERPARTY in edges[0].target_node_id


# ---------------------------------------------------------------------------
# expand_prev — event store returns rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_prev_returns_nodes_and_edges():
    """Inbound rows produce nodes and backward edges."""
    raw_rows = [_row(COUNTERPARTY)]
    pg = _pg_pool_returning(raw_rows)
    compiler = TronChainCompiler(postgres_pool=pg)

    with patch.object(compiler._bridge, "is_bridge_contract", return_value=False), \
         patch.object(compiler._service, "get_record", return_value=None):
        nodes, edges = await compiler.expand_prev(
            session_id="s",
            branch_id="b",
            path_sequence=0,
            depth=0,
            seed_address=SEED,
            chain="tron",
            options=_opts(),
        )

    assert len(nodes) == 1
    assert len(edges) == 1
    assert edges[0].direction == "backward"
    assert COUNTERPARTY in edges[0].source_node_id
    assert SEED in edges[0].target_node_id


# ---------------------------------------------------------------------------
# Token transfer rows included
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_transfer_rows_produce_nodes():
    """TRC-20 token transfer rows are included in graph output."""
    token_rows = [_token_row(COUNTERPARTY, symbol="USDT")]
    pg = _pg_pool_returning_token_only(token_rows)
    compiler = TronChainCompiler(postgres_pool=pg)

    with patch.object(compiler._bridge, "is_bridge_contract", return_value=False), \
         patch.object(compiler._service, "get_record", return_value=None):
        nodes, edges = await compiler.expand_next(
            session_id="s",
            branch_id="b",
            path_sequence=0,
            depth=0,
            seed_address=SEED,
            chain="tron",
            options=_opts(),
        )

    assert len(nodes) == 1
    assert nodes[0].address_data.address == COUNTERPARTY.lower()
    assert edges[0].asset_symbol == "USDT"


# ---------------------------------------------------------------------------
# Bridge detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_detection_produces_bridge_node():
    """A counterparty matching a known bridge contract is promoted to bridge node."""
    BRIDGE_ADDR = "tbridge0000000000000000000000000000001"
    raw_rows = [_row(BRIDGE_ADDR)]
    pg = _pg_pool_returning(raw_rows)
    compiler = TronChainCompiler(postgres_pool=pg)

    fake_bridge_node = MagicMock()
    fake_bridge_node.node_id = "bridge-node-1"
    fake_bridge_edge = MagicMock()
    fake_bridge_edge.edge_type = "bridge_hop"

    with patch.object(compiler._bridge, "is_bridge_contract", return_value=True), \
         patch.object(
             compiler._bridge, "process_row",
             new=AsyncMock(return_value=([fake_bridge_node], [fake_bridge_edge]))
         ):
        nodes, edges = await compiler.expand_next(
            session_id="s",
            branch_id="b",
            path_sequence=0,
            depth=0,
            seed_address=SEED,
            chain="tron",
            options=_opts(),
        )

    assert len(nodes) == 1
    assert nodes[0].node_id == "bridge-node-1"
    assert edges[0].edge_type == "bridge_hop"


# ---------------------------------------------------------------------------
# Address normalization
# ---------------------------------------------------------------------------


def test_normalize_address_lowercases():
    """Tron addresses are lowercased during normalization."""
    compiler = TronChainCompiler()
    addr = "TRXUpperCaseAddress1234567890"
    assert compiler._normalize_address(addr) == addr.lower()


# ---------------------------------------------------------------------------
# Native symbol and asset ID
# ---------------------------------------------------------------------------


def test_native_symbol_is_trx():
    compiler = TronChainCompiler()
    assert compiler._native_symbol("tron") == "TRX"


def test_native_canonical_asset_id_is_tron():
    compiler = TronChainCompiler()
    assert compiler._native_canonical_asset_id("tron") == "tron"


# ---------------------------------------------------------------------------
# _try_swap_promotion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_swap_promotion_skips_non_dex():
    """Non-DEX service types do not trigger swap promotion."""
    compiler = TronChainCompiler()
    service_record = MagicMock()
    service_record.service_type = "exchange"

    result = await compiler._try_swap_promotion(
        tx_hash="0x" + "a" * 64,
        seed_node_id="tron:address:seedabc",
        seed_address="seedabc",
        counterparty="cptydef",
        chain="tron",
        session_id="s",
        branch_id="b",
        path_id="p",
        depth=1,
        direction="forward",
        timestamp=None,
        service_record=service_record,
    )
    assert result is None


@pytest.mark.asyncio
async def test_try_swap_promotion_calls_maybe_build_for_dex():
    """DEX service type delegates to _maybe_build_swap_event."""
    compiler = TronChainCompiler()
    service_record = MagicMock()
    service_record.service_type = "dex"
    service_record.protocol_id = "justswap_v1"
    service_record.display_name = "JustSwap (SunSwap V1)"

    with patch.object(
        compiler, "_maybe_build_swap_event", new=AsyncMock(return_value=None)
    ) as mock_build:
        result = await compiler._try_swap_promotion(
            tx_hash="0x" + "b" * 64,
            seed_node_id="tron:address:seedxyz",
            seed_address="seedxyz",
            counterparty="cptyabc",
            chain="tron",
            session_id="s",
            branch_id="b",
            path_id="p",
            depth=0,
            direction="forward",
            timestamp="2024-01-01T00:00:00",
            service_record=service_record,
        )

    mock_build.assert_awaited_once()
    call_kwargs = mock_build.call_args[1]
    assert call_kwargs["protocol_id"] == "justswap_v1"
    assert call_kwargs["chain"] == "tron"
