"""
Unit tests for EVMChainCompiler (src/trace_compiler/chains/evm.py).

All DB calls are mocked — no running PostgreSQL or Neo4j required.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.trace_compiler.chains.evm import EVMChainCompiler, _native_symbol
from src.trace_compiler.models import ExpandOptions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
_OPTIONS = ExpandOptions(max_results=10)


def _make_compiler(pg=None, neo4j=None):
    return EVMChainCompiler(postgres_pool=pg, neo4j_driver=neo4j)


def _pg_pool_returning(rows):
    """Return a mock asyncpg pool whose conn.fetch always returns `rows`."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCtxMgr(conn))
    return pool


class _AsyncCtxMgr:
    def __init__(self, inner):
        self._inner = inner

    async def __aenter__(self):
        return self._inner

    async def __aexit__(self, *_):
        pass


def _pg_row(**kwargs):
    """Simulate an asyncpg Record as a dict."""
    return kwargs


# ---------------------------------------------------------------------------
# supported_chains
# ---------------------------------------------------------------------------


def test_supported_chains_includes_ethereum():
    c = _make_compiler()
    assert "ethereum" in c.supported_chains


def test_supported_chains_includes_all_evm():
    c = _make_compiler()
    for chain in ("bsc", "polygon", "arbitrum", "base", "avalanche", "optimism"):
        assert chain in c.supported_chains


# ---------------------------------------------------------------------------
# expand_next — event store path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_next_returns_node_per_unique_counterparty():
    rows = [
        _pg_row(counterparty="0xcounterparty1", tx_hash="0xtx1", value_native=1.0,
                asset_symbol=None, canonical_asset_id=None, timestamp=_TS),
        _pg_row(counterparty="0xcounterparty2", tx_hash="0xtx2", value_native=2.0,
                asset_symbol=None, canonical_asset_id=None, timestamp=_TS),
        # Duplicate counterparty — should produce only one node.
        _pg_row(counterparty="0xcounterparty1", tx_hash="0xtx3", value_native=0.5,
                asset_symbol=None, canonical_asset_id=None, timestamp=_TS),
    ]
    pg = _pg_pool_returning(rows)
    c = _make_compiler(pg=pg)

    nodes, edges = await c.expand_next(
        session_id="s", branch_id="b", path_sequence=0, depth=0,
        seed_address="0xseed", chain="ethereum", options=_OPTIONS,
    )

    assert len(nodes) == 2
    addresses = {n.address_data.address for n in nodes}
    assert "0xcounterparty1" in addresses
    assert "0xcounterparty2" in addresses


@pytest.mark.asyncio
async def test_expand_next_edge_direction_is_forward():
    rows = [
        _pg_row(counterparty="0xdest", tx_hash="0xtx", value_native=1.0,
                asset_symbol=None, canonical_asset_id=None, timestamp=_TS),
    ]
    pg = _pg_pool_returning(rows)
    c = _make_compiler(pg=pg)

    nodes, edges = await c.expand_next(
        session_id="s", branch_id="b", path_sequence=0, depth=0,
        seed_address="0xseed", chain="ethereum", options=_OPTIONS,
    )

    assert len(edges) >= 1
    assert edges[0].direction == "forward"
    # Source must be the seed.
    assert "ethereum:address:0xseed" in edges[0].source_node_id


@pytest.mark.asyncio
async def test_expand_next_node_chain_is_correct():
    rows = [_pg_row(counterparty="0xdest", tx_hash="t", value_native=1.0,
                    asset_symbol=None, canonical_asset_id=None, timestamp=_TS)]
    c = _make_compiler(pg=_pg_pool_returning(rows))

    nodes, _ = await c.expand_next(
        session_id="s", branch_id="b", path_sequence=0, depth=0,
        seed_address="0xseed", chain="polygon", options=_OPTIONS,
    )

    assert nodes[0].chain == "polygon"


@pytest.mark.asyncio
async def test_expand_next_node_depth_incremented():
    rows = [_pg_row(counterparty="0xd", tx_hash="t", value_native=None,
                    asset_symbol=None, canonical_asset_id=None, timestamp=_TS)]
    c = _make_compiler(pg=_pg_pool_returning(rows))

    nodes, _ = await c.expand_next(
        session_id="s", branch_id="b", path_sequence=0, depth=2,
        seed_address="0xseed", chain="ethereum", options=_OPTIONS,
    )

    assert nodes[0].depth == 3


@pytest.mark.asyncio
async def test_expand_next_empty_rows_returns_empty():
    pg = _pg_pool_returning([])
    c = _make_compiler(pg=pg)

    nodes, edges = await c.expand_next(
        session_id="s", branch_id="b", path_sequence=0, depth=0,
        seed_address="0xseed", chain="ethereum", options=_OPTIONS,
    )

    assert nodes == []
    assert edges == []


# ---------------------------------------------------------------------------
# expand_prev — backward direction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_prev_edge_direction_is_backward():
    rows = [_pg_row(counterparty="0xsrc", tx_hash="t", value_native=1.0,
                    asset_symbol=None, canonical_asset_id=None, timestamp=_TS)]
    c = _make_compiler(pg=_pg_pool_returning(rows))

    _, edges = await c.expand_prev(
        session_id="s", branch_id="b", path_sequence=0, depth=0,
        seed_address="0xseed", chain="ethereum", options=_OPTIONS,
    )

    assert edges[0].direction == "backward"
    # Target must be the seed.
    assert "ethereum:address:0xseed" in edges[0].target_node_id


# ---------------------------------------------------------------------------
# Token transfer enrichment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_next_includes_token_transfer_rows():
    """Token transfers come back alongside native transfers."""
    rows = [
        _pg_row(counterparty="0xtokenrecip", tx_hash="t", value_native=100.0,
                asset_symbol="USDC", canonical_asset_id="usdc", timestamp=_TS),
    ]
    pg = _pg_pool_returning(rows)
    c = _make_compiler(pg=pg)

    nodes, edges = await c.expand_next(
        session_id="s", branch_id="b", path_sequence=0, depth=0,
        seed_address="0xseed", chain="ethereum", options=_OPTIONS,
    )

    assert any(e.asset_symbol == "USDC" for e in edges)


# ---------------------------------------------------------------------------
# Neo4j fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_next_falls_back_to_neo4j_when_pg_empty():
    """When event store returns nothing, Neo4j result is used."""
    pg = _pg_pool_returning([])

    neo4j_row = {
        "counterparty": "0xneo4jaddr",
        "tx_hash": "0xtxneo",
        "value_native": 0.5,
        "asset_symbol": None,
        "canonical_asset_id": None,
        "timestamp": _TS,
    }

    class _FakeResult:
        """Minimal async-iterable that yields one dict row."""
        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            yield neo4j_row

    neo4j_session = MagicMock()
    neo4j_session.run = AsyncMock(return_value=_FakeResult())
    neo4j_driver = MagicMock()
    neo4j_driver.session = MagicMock(return_value=_AsyncCtxMgr(neo4j_session))

    c = _make_compiler(pg=pg, neo4j=neo4j_driver)

    nodes, _ = await c.expand_next(
        session_id="s", branch_id="b", path_sequence=0, depth=0,
        seed_address="0xseed", chain="ethereum", options=_OPTIONS,
    )

    assert any(n.address_data.address == "0xneo4jaddr" for n in nodes)


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_next_returns_empty_on_pg_error():
    pg = MagicMock()
    conn = MagicMock()
    conn.fetch = AsyncMock(side_effect=Exception("DB down"))
    pg.acquire = MagicMock(return_value=_AsyncCtxMgr(conn))

    c = _make_compiler(pg=pg)

    nodes, edges = await c.expand_next(
        session_id="s", branch_id="b", path_sequence=0, depth=0,
        seed_address="0xseed", chain="ethereum", options=_OPTIONS,
    )

    assert nodes == []
    assert edges == []


# ---------------------------------------------------------------------------
# Native symbol helper
# ---------------------------------------------------------------------------


def test_native_symbol_ethereum():
    assert _native_symbol("ethereum") == "ETH"


def test_native_symbol_bsc():
    assert _native_symbol("bsc") == "BNB"


def test_native_symbol_polygon():
    assert _native_symbol("polygon") == "MATIC"


def test_native_symbol_unknown_defaults_to_eth():
    assert _native_symbol("unknownchain") == "ETH"


# ---------------------------------------------------------------------------
# Async iteration helper for mock Neo4j result
# ---------------------------------------------------------------------------


def aiter_from_list(items):
    """Construct an async iterator from a regular list."""
    async def _gen():
        for item in items:
            yield item
    return _gen().__aiter__()


# ---------------------------------------------------------------------------
# Price oracle integration (T7.4 wiring into EVMChainCompiler)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_next_annotates_edge_value_fiat():
    """Edges carry value_fiat when price oracle returns a price."""
    row = _pg_row(
        counterparty="0xdest",
        tx_hash="0xtx",
        value_native=2.0,
        asset_symbol="ETH",
        canonical_asset_id="ethereum",
        timestamp=_TS,
    )
    # First fetch returns native txs; second returns no token transfers.
    conn = MagicMock()
    conn.fetch = AsyncMock(side_effect=[[row], []])
    pg = MagicMock()
    pg.acquire = MagicMock(return_value=_AsyncCtxMgr(conn))
    compiler = _make_compiler(pg=pg)

    with patch(
        "src.trace_compiler.chains.evm.price_oracle.get_prices_bulk",
        new_callable=AsyncMock,
        return_value={"ethereum": 3000.0},
    ):
        nodes, edges = await compiler.expand_next(
            session_id="s", branch_id="b", path_sequence=0,
            depth=0, seed_address="0xseed", chain="ethereum",
            options=ExpandOptions(max_results=10),
        )

    assert len(edges) == 1
    assert edges[0].value_fiat == pytest.approx(6000.0)


@pytest.mark.asyncio
async def test_expand_next_no_price_leaves_value_fiat_none():
    """Edges have value_fiat=None when price oracle returns no data."""
    row = _pg_row(
        counterparty="0xdest",
        tx_hash="0xtx",
        value_native=1.5,
        asset_symbol="ETH",
        canonical_asset_id="ethereum",
        timestamp=_TS,
    )
    conn = MagicMock()
    conn.fetch = AsyncMock(side_effect=[[row], []])
    pg = MagicMock()
    pg.acquire = MagicMock(return_value=_AsyncCtxMgr(conn))
    compiler = _make_compiler(pg=pg)

    with patch(
        "src.trace_compiler.chains.evm.price_oracle.get_prices_bulk",
        new_callable=AsyncMock,
        return_value={"ethereum": None},
    ):
        nodes, edges = await compiler.expand_next(
            session_id="s", branch_id="b", path_sequence=0,
            depth=0, seed_address="0xseed", chain="ethereum",
            options=ExpandOptions(max_results=10),
        )

    assert len(edges) == 1
    assert edges[0].value_fiat is None


@pytest.mark.asyncio
async def test_expand_next_min_value_fiat_filters_low_transfers():
    """Transfers below min_value_fiat are excluded from the result."""
    rows = [
        _pg_row(
            counterparty="0xrich",
            tx_hash="0xtx1",
            value_native=10.0,
            asset_symbol="ETH",
            canonical_asset_id="ethereum",
            timestamp=_TS,
        ),
        _pg_row(
            counterparty="0xpoor",
            tx_hash="0xtx2",
            value_native=0.001,
            asset_symbol="ETH",
            canonical_asset_id="ethereum",
            timestamp=_TS,
        ),
    ]
    pg = _pg_pool_returning(rows)
    compiler = _make_compiler(pg=pg)

    with patch(
        "src.trace_compiler.chains.evm.price_oracle.get_prices_bulk",
        new_callable=AsyncMock,
        return_value={"ethereum": 3000.0},
    ):
        nodes, edges = await compiler.expand_next(
            session_id="s", branch_id="b", path_sequence=0,
            depth=0, seed_address="0xseed", chain="ethereum",
            options=ExpandOptions(max_results=10, min_value_fiat=100.0),
        )

    # Only the 10 ETH * $3000 = $30,000 transfer survives the $100 filter.
    node_addresses = [n.address_data.address for n in nodes]
    assert "0xrich" in node_addresses
    assert "0xpoor" not in node_addresses


@pytest.mark.asyncio
async def test_expand_next_min_value_fiat_skips_filter_when_no_price():
    """When price oracle returns None, transfers are NOT filtered out."""
    row = _pg_row(
        counterparty="0xdest",
        tx_hash="0xtx",
        value_native=0.0001,
        asset_symbol="ETH",
        canonical_asset_id="ethereum",
        timestamp=_TS,
    )
    pg = _pg_pool_returning([row])
    compiler = _make_compiler(pg=pg)

    with patch(
        "src.trace_compiler.chains.evm.price_oracle.get_prices_bulk",
        new_callable=AsyncMock,
        return_value={"ethereum": None},
    ):
        nodes, edges = await compiler.expand_next(
            session_id="s", branch_id="b", path_sequence=0,
            depth=0, seed_address="0xseed", chain="ethereum",
            options=ExpandOptions(max_results=10, min_value_fiat=1000.0),
        )

    # Cannot filter without price data — transfer is kept.
    assert len(nodes) == 1
