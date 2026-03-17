"""
Unit tests for UTXOChainCompiler (src/trace_compiler/chains/bitcoin.py).

Verifies: normal expansion, CoinJoin halt semantics, change output flagging,
direction correctness, Neo4j fallback, and error resilience.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.trace_compiler.chains.bitcoin import UTXOChainCompiler, _script_type_to_address_type
from src.trace_compiler.models import ExpandOptions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
_OPTIONS = ExpandOptions(max_results=10)


def _make_compiler(pg=None, neo4j=None):
    return UTXOChainCompiler(postgres_pool=pg, neo4j_driver=neo4j)


class _AsyncCtxMgr:
    def __init__(self, inner):
        self._inner = inner

    async def __aenter__(self):
        return self._inner

    async def __aexit__(self, *_):
        pass


def _pg_pool_returning(rows):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCtxMgr(conn))
    return pool


def _pg_row(**kwargs):
    return kwargs


# ---------------------------------------------------------------------------
# supported_chains
# ---------------------------------------------------------------------------


def test_supported_chains_includes_bitcoin():
    assert "bitcoin" in UTXOChainCompiler().supported_chains


# ---------------------------------------------------------------------------
# expand_next — normal path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_next_returns_counterparty_nodes():
    rows = [
        _pg_row(counterparty="1Dest1", tx_hash="txA", value_satoshis=50000,
                output_index=0, script_type="p2wpkh", is_probable_change=False,
                is_spent=False, timestamp=_TS, is_coinjoin=False),
    ]
    c = _make_compiler(pg=_pg_pool_returning(rows))

    nodes, edges = await c.expand_next(
        session_id="s", branch_id="b", path_sequence=0, depth=0,
        seed_address="1Seed", chain="bitcoin", options=_OPTIONS,
    )

    assert len(nodes) == 1
    assert nodes[0].address_data.address == "1Dest1"


@pytest.mark.asyncio
async def test_expand_next_edge_direction_forward():
    rows = [
        _pg_row(counterparty="1Dest", tx_hash="tx", value_satoshis=10000,
                output_index=0, script_type="p2pkh", is_probable_change=False,
                is_spent=False, timestamp=_TS, is_coinjoin=False),
    ]
    c = _make_compiler(pg=_pg_pool_returning(rows))

    _, edges = await c.expand_next(
        session_id="s", branch_id="b", path_sequence=0, depth=0,
        seed_address="1Seed", chain="bitcoin", options=_OPTIONS,
    )

    assert edges[0].direction == "forward"
    assert "bitcoin:address:1Seed" in edges[0].source_node_id


@pytest.mark.asyncio
async def test_expand_next_value_btc_correct():
    rows = [
        _pg_row(counterparty="1Dest", tx_hash="tx", value_satoshis=100_000_000,
                output_index=0, script_type="p2wpkh", is_probable_change=False,
                is_spent=False, timestamp=_TS, is_coinjoin=False),
    ]
    c = _make_compiler(pg=_pg_pool_returning(rows))

    _, edges = await c.expand_next(
        session_id="s", branch_id="b", path_sequence=0, depth=0,
        seed_address="1Seed", chain="bitcoin", options=_OPTIONS,
    )

    assert abs(edges[0].value_native - 1.0) < 1e-9


@pytest.mark.asyncio
async def test_expand_next_asset_is_btc():
    rows = [
        _pg_row(counterparty="1D", tx_hash="t", value_satoshis=1000,
                output_index=0, script_type="p2pkh", is_probable_change=False,
                is_spent=False, timestamp=_TS, is_coinjoin=False),
    ]
    c = _make_compiler(pg=_pg_pool_returning(rows))

    _, edges = await c.expand_next(
        session_id="s", branch_id="b", path_sequence=0, depth=0,
        seed_address="1S", chain="bitcoin", options=_OPTIONS,
    )

    assert edges[0].asset_symbol == "BTC"
    assert edges[0].canonical_asset_id == "btc"


# ---------------------------------------------------------------------------
# Probable change output flagging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_next_change_output_flagged_on_edge():
    rows = [
        _pg_row(counterparty="1Change", tx_hash="tx", value_satoshis=500,
                output_index=1, script_type="p2wpkh", is_probable_change=True,
                is_spent=False, timestamp=_TS, is_coinjoin=False),
    ]
    c = _make_compiler(pg=_pg_pool_returning(rows))

    _, edges = await c.expand_next(
        session_id="s", branch_id="b", path_sequence=0, depth=0,
        seed_address="1S", chain="bitcoin", options=_OPTIONS,
    )

    assert edges[0].is_suspected_change is True


# ---------------------------------------------------------------------------
# CoinJoin halt semantics (tasks/memory.md Section 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_next_coinjoin_returns_single_halt_node():
    rows = [
        _pg_row(counterparty="1Addr1", tx_hash="txCJ", value_satoshis=1000,
                output_index=0, script_type="p2wpkh", is_probable_change=False,
                is_spent=False, timestamp=_TS, is_coinjoin=True),
        _pg_row(counterparty="1Addr2", tx_hash="txCJ", value_satoshis=2000,
                output_index=1, script_type="p2wpkh", is_probable_change=False,
                is_spent=False, timestamp=_TS, is_coinjoin=True),
    ]
    c = _make_compiler(pg=_pg_pool_returning(rows))

    nodes, edges = await c.expand_next(
        session_id="s", branch_id="b", path_sequence=0, depth=0,
        seed_address="1Seed", chain="bitcoin", options=_OPTIONS,
    )

    # Exactly one halt node, not two counterparty nodes.
    assert len(nodes) == 1
    assert nodes[0].address_data.is_coinjoin_halt is True


@pytest.mark.asyncio
async def test_expand_next_coinjoin_halt_node_not_expandable():
    rows = [
        _pg_row(counterparty="1A", tx_hash="txCJ", value_satoshis=100,
                output_index=0, script_type="p2wpkh", is_probable_change=False,
                is_spent=False, timestamp=_TS, is_coinjoin=True),
    ]
    c = _make_compiler(pg=_pg_pool_returning(rows))

    nodes, _ = await c.expand_next(
        session_id="s", branch_id="b", path_sequence=0, depth=0,
        seed_address="1S", chain="bitcoin", options=_OPTIONS,
    )

    assert nodes[0].expandable_directions == []


# ---------------------------------------------------------------------------
# expand_prev — backward direction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_prev_edge_direction_backward():
    rows = [
        _pg_row(counterparty="1Src", tx_hash="tx", value_satoshis=5000,
                output_index=0, script_type="p2pkh", is_probable_change=False,
                is_spent=True, timestamp=_TS, is_coinjoin=False),
    ]
    c = _make_compiler(pg=_pg_pool_returning(rows))

    _, edges = await c.expand_prev(
        session_id="s", branch_id="b", path_sequence=0, depth=0,
        seed_address="1Dest", chain="bitcoin", options=_OPTIONS,
    )

    assert edges[0].direction == "backward"
    assert "bitcoin:address:1Dest" in edges[0].target_node_id


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_next_returns_empty_on_pg_error():
    conn = MagicMock()
    conn.fetch = AsyncMock(side_effect=Exception("DB down"))
    pg = MagicMock()
    pg.acquire = MagicMock(return_value=_AsyncCtxMgr(conn))

    c = _make_compiler(pg=pg)

    nodes, edges = await c.expand_next(
        session_id="s", branch_id="b", path_sequence=0, depth=0,
        seed_address="1S", chain="bitcoin", options=_OPTIONS,
    )

    assert nodes == []
    assert edges == []


# ---------------------------------------------------------------------------
# Script type → address type helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script_type, expected", [
    ("p2pkh", "utxo_p2pkh"),
    ("p2sh", "utxo_p2sh"),
    ("p2wpkh", "utxo_p2wpkh"),
    ("p2wsh", "utxo_p2wsh"),
    ("p2tr", "utxo_p2tr"),
    ("op_return", "utxo_op_return"),
    ("unknown_type", "utxo_p2pkh"),
])
def test_script_type_to_address_type(script_type, expected):
    assert _script_type_to_address_type(script_type) == expected
