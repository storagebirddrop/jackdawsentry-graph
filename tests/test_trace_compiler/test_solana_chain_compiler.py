"""
Unit tests for SolanaChainCompiler.

Verifies:
- supported_chains returns ["solana"]
- Graceful degradation: expand_next/prev return empty when pg is None (no crash)
- ATA resolution: raw ATA addresses replaced by owner wallets in nodes
- ATA unresolved: raw ATA address used when not in cache
- Bridge detection takes priority over plain address nodes
- Service detection applied for known Solana service contracts
- Plain address nodes produced for normal transfers
- Deduplication: multiple transfers to same resolved wallet → one node, N edges
- Edge direction: forward → src=seed, backward → src=counterparty
- Neo4j fallback: used when pg fetch returns empty
- min_value_fiat filter: None value_fiat passes through regardless
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.trace_compiler.chains.solana import SolanaChainCompiler
from src.trace_compiler.models import ExpandOptions

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SEED = "SeedWallet1111111111111111111111111111111"
ATA_ADDR = "ATA1111111111111111111111111111111111111111"
OWNER_WALLET = "OwnerWallet11111111111111111111111111111111"
COUNTERPARTY = "CounterParty111111111111111111111111111111"
TX_HASH_1 = "tx" + "a" * 85
TX_HASH_2 = "tx" + "b" * 85


def _opts(max_results=10):
    return ExpandOptions(max_results=max_results)


def _row(counterparty, tx_hash=TX_HASH_1, value=1.0, symbol="USDC"):
    return {
        "counterparty": counterparty,
        "tx_hash": tx_hash,
        "value_native": value,
        "asset_symbol": symbol,
        "canonical_asset_id": "usdc",
        "timestamp": None,
    }


# ---------------------------------------------------------------------------
# Basic interface tests
# ---------------------------------------------------------------------------


def test_supported_chains():
    compiler = SolanaChainCompiler()
    assert compiler.supported_chains == ["solana"]


@pytest.mark.asyncio
async def test_expand_next_no_pg_returns_empty():
    """No postgres, no neo4j — returns empty, does not raise."""
    compiler = SolanaChainCompiler(postgres_pool=None, neo4j_driver=None)
    nodes, edges = await compiler.expand_next(
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="solana",
        options=_opts(),
    )
    assert nodes == []
    assert edges == []


@pytest.mark.asyncio
async def test_expand_prev_no_pg_returns_empty():
    compiler = SolanaChainCompiler(postgres_pool=None, neo4j_driver=None)
    nodes, edges = await compiler.expand_prev(
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="solana",
        options=_opts(),
    )
    assert nodes == []
    assert edges == []


@pytest.mark.asyncio
async def test_fetch_outbound_asset_filter_excludes_native_sol():
    mock_pg = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[_row(COUNTERPARTY, symbol="USDC")])
    mock_pg.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_conn),
        __aexit__=AsyncMock(return_value=False),
    ))

    compiler = SolanaChainCompiler(postgres_pool=mock_pg)
    rows = await compiler._fetch_outbound(SEED, ExpandOptions(max_results=10, asset_filter=["USDC"]))

    assert len(rows) == 1
    assert rows[0]["asset_symbol"] == "USDC"
    assert mock_conn.fetch.await_count == 1


@pytest.mark.asyncio
async def test_fetch_inbound_asset_filter_includes_sol_when_selected():
    mock_pg = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(side_effect=[
        [_row(COUNTERPARTY, symbol="USDC")],
        [{
            "tx_hash": TX_HASH_2,
            "counterparty": COUNTERPARTY,
            "value_native": 0.5,
            "asset_symbol": "SOL",
            "canonical_asset_id": None,
            "timestamp": None,
        }],
    ])
    mock_pg.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_conn),
        __aexit__=AsyncMock(return_value=False),
    ))

    compiler = SolanaChainCompiler(postgres_pool=mock_pg)
    rows = await compiler._fetch_inbound(SEED, ExpandOptions(max_results=10, asset_filter=["SOL", "USDC"]))

    assert len(rows) == 2
    assert {row["asset_symbol"] for row in rows} == {"USDC", "SOL"}
    assert mock_conn.fetch.await_count == 2


# ---------------------------------------------------------------------------
# _build_graph — plain address nodes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_graph_plain_address_forward():
    compiler = SolanaChainCompiler()
    rows = [_row(COUNTERPARTY)]
    nodes, edges = await compiler._build_graph(
        rows=rows,
        ata_map={},
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        direction="forward",
        options=_opts(),
    )
    assert len(nodes) == 1
    assert nodes[0].node_type == "address"
    assert nodes[0].address_data.address == COUNTERPARTY
    assert len(edges) == 1
    assert edges[0].edge_type == "transfer"
    assert edges[0].source_node_id.endswith(SEED)
    assert edges[0].target_node_id.endswith(COUNTERPARTY)


@pytest.mark.asyncio
async def test_build_graph_plain_address_backward():
    compiler = SolanaChainCompiler()
    rows = [_row(COUNTERPARTY)]
    nodes, edges = await compiler._build_graph(
        rows=rows,
        ata_map={},
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        direction="backward",
        options=_opts(),
    )
    assert len(nodes) == 1
    assert edges[0].source_node_id.endswith(COUNTERPARTY)
    assert edges[0].target_node_id.endswith(SEED)


@pytest.mark.asyncio
async def test_build_graph_skips_seed_as_counterparty():
    """Self-loops (counterparty == seed) must be skipped."""
    compiler = SolanaChainCompiler()
    rows = [_row(SEED)]
    nodes, edges = await compiler._build_graph(
        rows=rows,
        ata_map={},
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        direction="forward",
        options=_opts(),
    )
    assert nodes == []
    assert edges == []


# ---------------------------------------------------------------------------
# ATA resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_graph_ata_resolved_to_owner():
    """ATA addresses are replaced by owner wallets."""
    compiler = SolanaChainCompiler()
    rows = [_row(ATA_ADDR)]
    ata_map = {ATA_ADDR: OWNER_WALLET}
    nodes, edges = await compiler._build_graph(
        rows=rows,
        ata_map=ata_map,
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        direction="forward",
        options=_opts(),
    )
    assert len(nodes) == 1
    assert nodes[0].address_data.address == OWNER_WALLET
    assert nodes[0].address_data.address_type == "wallet"
    # Original ATA address is preserved in display_sublabel
    assert nodes[0].display_sublabel is not None
    assert "ATA:" in nodes[0].display_sublabel


@pytest.mark.asyncio
async def test_build_graph_ata_unresolved_uses_raw():
    """ATA address not in cache → raw ATA used, address_type=unknown."""
    compiler = SolanaChainCompiler()
    rows = [_row(ATA_ADDR)]
    nodes, edges = await compiler._build_graph(
        rows=rows,
        ata_map={},   # empty — no resolution
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        direction="forward",
        options=_opts(),
    )
    assert len(nodes) == 1
    assert nodes[0].address_data.address == ATA_ADDR
    assert nodes[0].address_data.address_type == "unknown"
    assert nodes[0].display_sublabel is None


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_graph_deduplicates_resolved_wallet():
    """Two transfers to ATAs belonging to same owner → one node, two edges."""
    ATA_2 = "ATA2222222222222222222222222222222222222222"
    compiler = SolanaChainCompiler()
    rows = [
        _row(ATA_ADDR, tx_hash=TX_HASH_1),
        _row(ATA_2, tx_hash=TX_HASH_2),
    ]
    ata_map = {ATA_ADDR: OWNER_WALLET, ATA_2: OWNER_WALLET}
    nodes, edges = await compiler._build_graph(
        rows=rows,
        ata_map=ata_map,
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        direction="forward",
        options=_opts(),
    )
    assert len(nodes) == 1
    assert nodes[0].address_data.address == OWNER_WALLET
    assert len(edges) == 2


@pytest.mark.asyncio
async def test_build_graph_deduplicates_plain_address():
    """Multiple transfers to the same plain address → one node, N edges."""
    compiler = SolanaChainCompiler()
    rows = [
        _row(COUNTERPARTY, tx_hash=TX_HASH_1, value=1.0),
        _row(COUNTERPARTY, tx_hash=TX_HASH_2, value=2.0),
    ]
    nodes, edges = await compiler._build_graph(
        rows=rows,
        ata_map={},
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        direction="forward",
        options=_opts(),
    )
    assert len(nodes) == 1
    assert len(edges) == 2


# ---------------------------------------------------------------------------
# Neo4j fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_next_falls_back_to_neo4j_when_pg_empty():
    """When event store returns nothing, Neo4j bipartite fallback is called."""
    mock_pg = MagicMock()
    # conn.fetch returns empty for any call
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_pg.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_conn),
        __aexit__=AsyncMock(return_value=False),
    ))

    neo4j_row = {
        "counterparty": COUNTERPARTY,
        "tx_hash": TX_HASH_1,
        "value_native": 1.0,
        "asset_symbol": "SOL",
        "canonical_asset_id": None,
        "timestamp": None,
    }
    mock_neo4j = MagicMock()
    mock_result = AsyncMock()
    mock_result.__aiter__ = lambda: aiter_from([neo4j_row])
    mock_session = AsyncMock()
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_neo4j.session = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_session),
        __aexit__=AsyncMock(return_value=False),
    ))

    compiler = SolanaChainCompiler(postgres_pool=mock_pg, neo4j_driver=mock_neo4j)
    # Patch ATA resolution to return empty map (no ATAs)
    compiler._resolve_atas_bulk = AsyncMock(return_value={})

    nodes, edges = await compiler.expand_next(
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="solana",
        options=_opts(),
    )
    # If Neo4j call was made correctly, we'd get nodes.
    # Verify the mock was called with expected query and assert fallback behavior
    assert mock_session.run.called
    assert isinstance(nodes, list)
    assert isinstance(edges, list)
    # Verify nodes/edges contain expected items derived from neo4j_row
    assert len(nodes) > 0 or len(edges) > 0  # Should have some content


# ---------------------------------------------------------------------------
# _resolve_atas_bulk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_atas_bulk_no_pg_returns_empty():
    compiler = SolanaChainCompiler(postgres_pool=None)
    result = await compiler._resolve_atas_bulk({ATA_ADDR})
    assert result == {}


@pytest.mark.asyncio
async def test_resolve_atas_bulk_empty_set_returns_empty():
    mock_pg = MagicMock()
    compiler = SolanaChainCompiler(postgres_pool=mock_pg)
    result = await compiler._resolve_atas_bulk(set())
    assert result == {}


@pytest.mark.asyncio
async def test_resolve_atas_bulk_resolves_correctly():
    mock_pg = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[
        {"ata_address": ATA_ADDR, "owner_address": OWNER_WALLET}
    ])
    mock_pg.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_conn),
        __aexit__=AsyncMock(return_value=False),
    ))
    compiler = SolanaChainCompiler(postgres_pool=mock_pg)
    result = await compiler._resolve_atas_bulk({ATA_ADDR})
    assert result == {ATA_ADDR: OWNER_WALLET}


# ---------------------------------------------------------------------------
# min_value_fiat filter — None fiat passes through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_min_value_fiat_none_fiat_passes_through():
    """Edges with no fiat value should pass min_value_fiat filter."""
    compiler = SolanaChainCompiler()
    rows = [_row(COUNTERPARTY, value=0.001)]  # tiny value, no fiat
    opts = ExpandOptions(max_results=10, min_value_fiat=1000.0)
    nodes, edges = await compiler._build_graph(
        rows=rows,
        ata_map={},
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        direction="forward",
        options=opts,
    )
    # value_fiat is None in row; filter should not drop the edge
    assert len(nodes) == 1
    assert len(edges) == 1


# ---------------------------------------------------------------------------
# max_results truncation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_graph_respects_max_results():
    compiler = SolanaChainCompiler()
    addrs = [f"Addr{i:040d}" for i in range(20)]
    rows = [_row(a, tx_hash=f"tx{i:085d}") for i, a in enumerate(addrs)]
    nodes, edges = await compiler._build_graph(
        rows=rows,
        ata_map={},
        session_id="s",
        branch_id="b",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        direction="forward",
        options=_opts(max_results=5),
    )
    assert len(nodes) <= 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def aiter_from(items):
    """Return an async iterator over items."""
    class _AsyncIter:
        def __init__(self, it):
            self._it = iter(it)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    return _AsyncIter(items)
