"""
Unit tests for XRPChainCompiler (src/trace_compiler/chains/xrp.py).

All DB calls are mocked — no running PostgreSQL or Neo4j required.

Covers:
- supported_chains returns ["xrp"]
- Native symbol is "XRP", canonical ID is "ripple"
- Address NOT lowercased — XRP addresses are case-sensitive (base58check)
- expand_next / expand_prev return nodes + edges when event store has data
- Empty event store (no pg) returns empty lists without raising
- Token transfer rows from raw_token_transfers are included
- Bridge detection applied in forward direction
- Edge direction: forward → src=seed, backward → src=counterparty
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.trace_compiler.chains.xrp import XRPChainCompiler
from src.trace_compiler.models import ExpandOptions

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# XRP addresses are base58check with mixed case — use realistic-looking ones.
SEED = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
COUNTERPARTY = "rN7n73473Dd2BofqzEbq8RiFHYfbwvXJdy"
TX_HASH_1 = "A" * 64
TX_HASH_2 = "B" * 64


def _opts(max_results=10):
    return ExpandOptions(max_results=max_results)


def _row(counterparty, tx_hash=TX_HASH_1, value=10.0, symbol=None):
    return {
        "counterparty": counterparty,
        "tx_hash": tx_hash,
        "value_native": value,
        "asset_symbol": symbol,
        "canonical_asset_id": None,
        "timestamp": None,
    }


def _token_row(counterparty, tx_hash=TX_HASH_1, value=50.0, symbol="USDC"):
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
    compiler = XRPChainCompiler()
    assert compiler.supported_chains == ["xrp"]


# ---------------------------------------------------------------------------
# Native symbol and asset ID
# ---------------------------------------------------------------------------


def test_native_symbol_is_xrp():
    compiler = XRPChainCompiler()
    assert compiler._native_symbol("xrp") == "XRP"


def test_native_canonical_asset_id_is_ripple():
    compiler = XRPChainCompiler()
    assert compiler._native_canonical_asset_id("xrp") == "ripple"


# ---------------------------------------------------------------------------
# Address normalization — XRP addresses must NOT be lowercased
# ---------------------------------------------------------------------------


def test_normalize_address_preserves_case():
    """XRP addresses must NOT be lowercased — checksum is case-sensitive."""
    compiler = XRPChainCompiler()
    addr = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
    assert compiler._normalize_address(addr) == addr
    # Verify it's different from lowercased version (not a no-op coincidence)
    assert compiler._normalize_address(addr) != addr.lower()


# ---------------------------------------------------------------------------
# No pool → empty results (no crash)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_next_no_pg_returns_empty():
    """No postgres — expand_next returns empty, does not raise."""
    compiler = XRPChainCompiler(postgres_pool=None, neo4j_driver=None)
    nodes, edges = await compiler.expand_next(
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="xrp",
        options=_opts(),
    )
    assert nodes == []
    assert edges == []


@pytest.mark.asyncio
async def test_expand_prev_no_pg_returns_empty():
    """No postgres — expand_prev returns empty, does not raise."""
    compiler = XRPChainCompiler(postgres_pool=None, neo4j_driver=None)
    nodes, edges = await compiler.expand_prev(
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="xrp",
        options=_opts(),
    )
    assert nodes == []
    assert edges == []


# ---------------------------------------------------------------------------
# expand_next — event store returns rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_next_returns_nodes_and_edges():
    """Rows in the event store produce address nodes and transfer edges."""
    raw_rows = [_row(COUNTERPARTY)]
    pg = _pg_pool_returning(raw_rows)
    compiler = XRPChainCompiler(postgres_pool=pg)

    with patch.object(compiler._bridge, "is_bridge_contract", return_value=False), \
         patch.object(compiler._service, "get_record", return_value=None):
        nodes, edges = await compiler.expand_next(
            session_id="s",
            branch_id="b",
            path_sequence=0,
            depth=0,
            seed_address=SEED,
            chain="xrp",
            options=_opts(),
        )

    assert len(nodes) == 1
    assert nodes[0].node_type == "address"
    assert nodes[0].chain == "xrp"
    assert len(edges) == 1
    assert edges[0].edge_type == "transfer"


@pytest.mark.asyncio
async def test_expand_next_edges_point_forward():
    """Forward expansion: source is seed, target is counterparty."""
    raw_rows = [_row(COUNTERPARTY)]
    pg = _pg_pool_returning(raw_rows)
    compiler = XRPChainCompiler(postgres_pool=pg)

    with patch.object(compiler._bridge, "is_bridge_contract", return_value=False), \
         patch.object(compiler._service, "get_record", return_value=None):
        nodes, edges = await compiler.expand_next(
            session_id="s",
            branch_id="b",
            path_sequence=0,
            depth=0,
            seed_address=SEED,
            chain="xrp",
            options=_opts(),
        )

    assert edges[0].direction == "forward"
    assert SEED in edges[0].source_node_id
    assert COUNTERPARTY in edges[0].target_node_id


# ---------------------------------------------------------------------------
# expand_prev — event store returns rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_prev_edges_point_backward():
    """Backward expansion: source is counterparty, target is seed."""
    raw_rows = [_row(COUNTERPARTY)]
    pg = _pg_pool_returning(raw_rows)
    compiler = XRPChainCompiler(postgres_pool=pg)

    with patch.object(compiler._bridge, "is_bridge_contract", return_value=False), \
         patch.object(compiler._service, "get_record", return_value=None):
        nodes, edges = await compiler.expand_prev(
            session_id="s",
            branch_id="b",
            path_sequence=0,
            depth=0,
            seed_address=SEED,
            chain="xrp",
            options=_opts(),
        )

    assert len(nodes) == 1
    assert edges[0].direction == "backward"
    assert COUNTERPARTY in edges[0].source_node_id
    assert SEED in edges[0].target_node_id


# ---------------------------------------------------------------------------
# Token transfer rows included
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_transfer_rows_produce_nodes():
    """Issued-asset token transfers produce address nodes with symbol preserved."""
    token_rows = [_token_row(COUNTERPARTY, symbol="USDC")]
    pg = _pg_pool_returning_token_only(token_rows)
    compiler = XRPChainCompiler(postgres_pool=pg)

    with patch.object(compiler._bridge, "is_bridge_contract", return_value=False), \
         patch.object(compiler._service, "get_record", return_value=None):
        nodes, edges = await compiler.expand_next(
            session_id="s",
            branch_id="b",
            path_sequence=0,
            depth=0,
            seed_address=SEED,
            chain="xrp",
            options=_opts(),
        )

    assert len(nodes) == 1
    assert nodes[0].address_data.address == COUNTERPARTY  # not lowercased
    assert edges[0].asset_symbol == "USDC"


# ---------------------------------------------------------------------------
# Bridge detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_detection_produces_bridge_node():
    """A counterparty matching a known bridge contract is promoted."""
    BRIDGE_ADDR = "rBridgeXRP0000000000000000000000001"
    raw_rows = [_row(BRIDGE_ADDR)]
    pg = _pg_pool_returning(raw_rows)
    compiler = XRPChainCompiler(postgres_pool=pg)

    fake_bridge_node = MagicMock()
    fake_bridge_node.node_id = "bridge-node-xrp"
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
            chain="xrp",
            options=_opts(),
        )

    assert len(nodes) == 1
    assert nodes[0].node_id == "bridge-node-xrp"
    assert edges[0].edge_type == "bridge_hop"
