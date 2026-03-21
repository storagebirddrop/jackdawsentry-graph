"""
Unit tests for SuiChainCompiler (src/trace_compiler/chains/sui.py).

All DB calls are mocked — no running PostgreSQL or Neo4j required.

Covers:
- supported_chains returns ["sui"]
- expand_next / expand_prev return nodes + edges when event store has data
- Empty event store (no pg) returns empty lists without raising
- Token transfer rows from raw_token_transfers are included in results
- Bridge detection works for known bridge contracts
- Pool is None → returns empty gracefully
- Address normalization (lowercases — 0x-prefixed hex)
- Edge direction: forward → src=seed, backward → src=counterparty
- Native symbol and canonical asset ID
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.trace_compiler.chains.sui import SuiChainCompiler
from src.trace_compiler.models import ExpandOptions

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SEED = "0x" + "a" * 64
COUNTERPARTY = "0x" + "b" * 64
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


def _token_row(counterparty, tx_hash=TX_HASH_1, value=100.0, symbol="USDC"):
    return {
        "counterparty": counterparty,
        "tx_hash": tx_hash,
        "value_native": value,
        "asset_symbol": symbol,
        "canonical_asset_id": "usd-coin",
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
    """Mock asyncpg pool — first fetch returns rows, second returns token_rows."""
    conn = MagicMock()
    _token = token_rows if token_rows is not None else []
    conn.fetch = AsyncMock(side_effect=[rows, _token])
    conn.fetchrow = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCtxMgr(conn))
    return pool


def _pg_pool_returning_token_only(token_rows):
    """Mock where raw_transactions returns empty, token_transfers return rows."""
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
    compiler = SuiChainCompiler()
    assert compiler.supported_chains == ["sui"]


# ---------------------------------------------------------------------------
# No pool → empty results (no crash)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_next_no_pg_returns_empty():
    """No postgres, no neo4j — returns empty, does not raise."""
    compiler = SuiChainCompiler(postgres_pool=None, neo4j_driver=None)
    nodes, edges = await compiler.expand_next(
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="sui",
        options=_opts(),
    )
    assert nodes == []
    assert edges == []


@pytest.mark.asyncio
async def test_expand_prev_no_pg_returns_empty():
    """No postgres — expand_prev also returns empty."""
    compiler = SuiChainCompiler(postgres_pool=None, neo4j_driver=None)
    nodes, edges = await compiler.expand_prev(
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="sui",
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
        _row(COUNTERPARTY + "c", TX_HASH_2, 2.0),
    ]
    pg = _pg_pool_returning(raw_rows)
    compiler = SuiChainCompiler(postgres_pool=pg)

    with patch.object(compiler._bridge, "is_bridge_contract", return_value=False), \
         patch.object(compiler._service, "get_record", return_value=None):
        nodes, edges = await compiler.expand_next(
            session_id="s",
            branch_id="b",
            path_sequence=0,
            depth=0,
            seed_address=SEED,
            chain="sui",
            options=_opts(),
        )

    assert len(nodes) == 2
    assert len(edges) == 2
    for node in nodes:
        assert node.node_type == "address"
        assert node.chain == "sui"


@pytest.mark.asyncio
async def test_expand_next_edges_point_forward():
    """Forward expansion: seed → counterparty."""
    raw_rows = [_row(COUNTERPARTY)]
    pg = _pg_pool_returning(raw_rows)
    compiler = SuiChainCompiler(postgres_pool=pg)

    with patch.object(compiler._bridge, "is_bridge_contract", return_value=False), \
         patch.object(compiler._service, "get_record", return_value=None):
        nodes, edges = await compiler.expand_next(
            session_id="s",
            branch_id="b",
            path_sequence=0,
            depth=0,
            seed_address=SEED,
            chain="sui",
            options=_opts(),
        )

    assert len(edges) == 1
    assert edges[0].direction == "forward"
    assert SEED.lower() in edges[0].source_node_id
    assert COUNTERPARTY.lower() in edges[0].target_node_id


# ---------------------------------------------------------------------------
# expand_prev — event store returns rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_prev_returns_nodes_and_edges():
    """Inbound rows produce nodes and backward edges."""
    raw_rows = [_row(COUNTERPARTY)]
    pg = _pg_pool_returning(raw_rows)
    compiler = SuiChainCompiler(postgres_pool=pg)

    with patch.object(compiler._bridge, "is_bridge_contract", return_value=False), \
         patch.object(compiler._service, "get_record", return_value=None):
        nodes, edges = await compiler.expand_prev(
            session_id="s",
            branch_id="b",
            path_sequence=0,
            depth=0,
            seed_address=SEED,
            chain="sui",
            options=_opts(),
        )

    assert len(nodes) == 1
    assert len(edges) == 1
    assert edges[0].direction == "backward"
    assert COUNTERPARTY.lower() in edges[0].source_node_id
    assert SEED.lower() in edges[0].target_node_id


# ---------------------------------------------------------------------------
# Token transfer rows included
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_transfer_rows_produce_nodes():
    """Sui token transfer rows are included in graph output."""
    token_rows = [_token_row(COUNTERPARTY, symbol="USDC")]
    pg = _pg_pool_returning_token_only(token_rows)
    compiler = SuiChainCompiler(postgres_pool=pg)

    with patch.object(compiler._bridge, "is_bridge_contract", return_value=False), \
         patch.object(compiler._service, "get_record", return_value=None):
        nodes, edges = await compiler.expand_next(
            session_id="s",
            branch_id="b",
            path_sequence=0,
            depth=0,
            seed_address=SEED,
            chain="sui",
            options=_opts(),
        )

    assert len(nodes) == 1
    assert nodes[0].address_data.address == COUNTERPARTY.lower()
    assert edges[0].asset_symbol == "USDC"


# ---------------------------------------------------------------------------
# Bridge detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_detection_produces_bridge_node():
    """A counterparty matching a known bridge contract is promoted to bridge node."""
    BRIDGE_ADDR = "0x" + "c" * 64
    raw_rows = [_row(BRIDGE_ADDR)]
    pg = _pg_pool_returning(raw_rows)
    compiler = SuiChainCompiler(postgres_pool=pg)

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
            chain="sui",
            options=_opts(),
        )

    assert len(nodes) == 1
    assert nodes[0].node_id == "bridge-node-1"
    assert edges[0].edge_type == "bridge_hop"


# ---------------------------------------------------------------------------
# Address normalization
# ---------------------------------------------------------------------------


def test_normalize_address_lowercases():
    """Sui 0x-prefixed hex addresses are lowercased during normalization."""
    compiler = SuiChainCompiler()
    addr = "0x" + "A" * 64
    assert compiler._normalize_address(addr) == addr.lower()


# ---------------------------------------------------------------------------
# Native symbol and asset ID
# ---------------------------------------------------------------------------


def test_native_symbol_is_sui():
    compiler = SuiChainCompiler()
    assert compiler._native_symbol("sui") == "SUI"


def test_native_canonical_asset_id_is_sui():
    compiler = SuiChainCompiler()
    assert compiler._native_canonical_asset_id("sui") == "sui"
