"""
Unit tests for UTXOChainCompiler.

Verifies:
- P1.3: CoinJoin halt — when any row has is_coinjoin=True, _build_graph returns
  a single halt node with is_coinjoin_halt=True on AddressNodeData, and the node
  has no expandable_directions.
- P1.4: Probable change output — when a row has is_probable_change=True, the
  corresponding InvestigationEdge has is_suspected_change=True.
- Normal forward/backward edge direction for non-CoinJoin rows.
- No crash on empty rows.
- Deduplication: multiple outputs to the same address → one node, N edges.
"""

import pytest

from src.trace_compiler.chains.bitcoin import UTXOChainCompiler
from src.trace_compiler.models import ExpandOptions

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SEED = "1A1zP1eP5QGefi2DMPTfTL5SLmv7Divf"
ADDR_1 = "bc1qxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
ADDR_2 = "bc1qyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy"
TX_1 = "a" * 64
TX_2 = "b" * 64


def _opts(max_results=10):
    return ExpandOptions(max_results=max_results)


def _normal_row(counterparty=ADDR_1, tx_hash=TX_1, value_sats=100_000):
    return {
        "counterparty": counterparty,
        "tx_hash": tx_hash,
        "value_satoshis": value_sats,
        "output_index": 0,
        "script_type": "p2wpkh",
        "is_probable_change": False,
        "is_spent": False,
        "timestamp": None,
        "is_coinjoin": False,
    }


def _coinjoin_row(counterparty=ADDR_1, tx_hash=TX_1):
    row = _normal_row(counterparty=counterparty, tx_hash=tx_hash)
    row["is_coinjoin"] = True
    return row


def _change_row(counterparty=ADDR_1, tx_hash=TX_1, value_sats=1_000):
    row = _normal_row(counterparty=counterparty, tx_hash=tx_hash, value_sats=value_sats)
    row["is_probable_change"] = True
    return row


# ---------------------------------------------------------------------------
# P1.3 — CoinJoin halt semantics
# ---------------------------------------------------------------------------


def test_coinjoin_halt_single_row():
    """A single CoinJoin row → single halt node, is_coinjoin_halt=True."""
    compiler = UTXOChainCompiler()
    nodes, edges = compiler._build_graph(
        rows=[_coinjoin_row()],
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="bitcoin",
        direction="forward",
        options=_opts(),
    )
    assert len(nodes) == 1, "CoinJoin halt must produce exactly one node"
    halt = nodes[0]
    assert halt.address_data is not None
    assert halt.address_data.is_coinjoin_halt is True, (
        "AddressNodeData.is_coinjoin_halt must be True for CoinJoin transactions"
    )
    assert halt.expandable_directions == [], (
        "CoinJoin halt node must not have any expandable directions"
    )
    assert halt.is_highlighted is True


def test_coinjoin_halt_mixed_rows():
    """If any row is CoinJoin, the whole expansion halts — clean rows ignored."""
    compiler = UTXOChainCompiler()
    rows = [
        _normal_row(counterparty=ADDR_1, tx_hash=TX_1),
        _coinjoin_row(counterparty=ADDR_2, tx_hash=TX_2),
    ]
    nodes, edges = compiler._build_graph(
        rows=rows,
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="bitcoin",
        direction="forward",
        options=_opts(),
    )
    assert len(nodes) == 1
    assert nodes[0].address_data.is_coinjoin_halt is True


def test_coinjoin_halt_backward_direction():
    """CoinJoin halt works in backward direction too."""
    compiler = UTXOChainCompiler()
    nodes, edges = compiler._build_graph(
        rows=[_coinjoin_row()],
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="bitcoin",
        direction="backward",
        options=_opts(),
    )
    assert len(nodes) == 1
    assert nodes[0].address_data.is_coinjoin_halt is True


def test_coinjoin_halt_produces_one_edge():
    """The halt node must have exactly one edge connecting it to the seed."""
    compiler = UTXOChainCompiler()
    nodes, edges = compiler._build_graph(
        rows=[_coinjoin_row()],
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="bitcoin",
        direction="forward",
        options=_opts(),
    )
    assert len(edges) == 1


def test_no_coinjoin_does_not_halt():
    """Normal rows with is_coinjoin=False must not produce a halt node."""
    compiler = UTXOChainCompiler()
    nodes, edges = compiler._build_graph(
        rows=[_normal_row()],
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="bitcoin",
        direction="forward",
        options=_opts(),
    )
    assert len(nodes) == 1
    assert not nodes[0].address_data.is_coinjoin_halt  # None or False both acceptable


# ---------------------------------------------------------------------------
# P1.4 — Probable change output suppression
# ---------------------------------------------------------------------------


def test_probable_change_sets_is_suspected_change():
    """Rows with is_probable_change=True must produce edges with is_suspected_change=True."""
    compiler = UTXOChainCompiler()
    nodes, edges = compiler._build_graph(
        rows=[_change_row()],
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="bitcoin",
        direction="forward",
        options=_opts(),
    )
    assert len(edges) == 1
    assert edges[0].is_suspected_change is True, (
        "is_probable_change=True in row must produce is_suspected_change=True on edge"
    )


def test_non_change_output_is_not_suspected_change():
    """Normal rows (is_probable_change=False) produce is_suspected_change=False."""
    compiler = UTXOChainCompiler()
    nodes, edges = compiler._build_graph(
        rows=[_normal_row()],
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="bitcoin",
        direction="forward",
        options=_opts(),
    )
    assert len(edges) == 1
    assert edges[0].is_suspected_change is False


def test_mixed_change_and_normal_rows():
    """Only the change-output edge is is_suspected_change=True; others are False."""
    compiler = UTXOChainCompiler()
    rows = [
        _normal_row(counterparty=ADDR_1, tx_hash=TX_1),
        _change_row(counterparty=ADDR_2, tx_hash=TX_1, value_sats=1_000),
    ]
    nodes, edges = compiler._build_graph(
        rows=rows,
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="bitcoin",
        direction="forward",
        options=_opts(),
    )
    assert len(nodes) == 2
    assert len(edges) == 2
    change_edges = [e for e in edges if e.is_suspected_change]
    normal_edges = [e for e in edges if not e.is_suspected_change]
    assert len(change_edges) == 1
    assert len(normal_edges) == 1


# ---------------------------------------------------------------------------
# Edge direction
# ---------------------------------------------------------------------------


def test_forward_edge_direction():
    """Forward: seed → counterparty."""
    compiler = UTXOChainCompiler()
    nodes, edges = compiler._build_graph(
        rows=[_normal_row()],
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="bitcoin",
        direction="forward",
        options=_opts(),
    )
    seed_node_id = edges[0].source_node_id
    assert seed_node_id.endswith(SEED)
    assert edges[0].target_node_id.endswith(ADDR_1)


def test_backward_edge_direction():
    """Backward: counterparty → seed."""
    compiler = UTXOChainCompiler()
    nodes, edges = compiler._build_graph(
        rows=[_normal_row()],
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="bitcoin",
        direction="backward",
        options=_opts(),
    )
    assert edges[0].source_node_id.endswith(ADDR_1)
    assert edges[0].target_node_id.endswith(SEED)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_rows_returns_empty():
    compiler = UTXOChainCompiler()
    nodes, edges = compiler._build_graph(
        rows=[],
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="bitcoin",
        direction="forward",
        options=_opts(),
    )
    assert nodes == []
    assert edges == []


def test_deduplicates_same_counterparty():
    """Two outputs to the same address → one node, two edges."""
    compiler = UTXOChainCompiler()
    rows = [
        _normal_row(counterparty=ADDR_1, tx_hash=TX_1, value_sats=50_000),
        _normal_row(counterparty=ADDR_1, tx_hash=TX_2, value_sats=30_000),
    ]
    nodes, edges = compiler._build_graph(
        rows=rows,
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="bitcoin",
        direction="forward",
        options=_opts(),
    )
    assert len(nodes) == 1
    assert len(edges) == 2


def test_skips_self_loop():
    """Output address == seed address must be skipped."""
    compiler = UTXOChainCompiler()
    nodes, edges = compiler._build_graph(
        rows=[_normal_row(counterparty=SEED)],
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="bitcoin",
        direction="forward",
        options=_opts(),
    )
    assert nodes == []
    assert edges == []


def test_value_btc_converted_from_satoshis():
    """value_satoshis must be converted to BTC (÷1e8) on the edge."""
    compiler = UTXOChainCompiler()
    nodes, edges = compiler._build_graph(
        rows=[_normal_row(value_sats=100_000_000)],  # 1 BTC
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="bitcoin",
        direction="forward",
        options=_opts(),
    )
    assert edges[0].value_native == pytest.approx(1.0)
    assert edges[0].asset_symbol == "BTC"
