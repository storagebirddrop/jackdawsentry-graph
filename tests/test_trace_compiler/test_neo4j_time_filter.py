"""
Regression tests for F-1: Neo4j fallback Cypher queries apply time filters.

Verifies that when options.time_from / options.time_to are set, the generated
Cypher for EVM, Bitcoin, and Solana Neo4j fallback methods includes the expected
timestamp predicates.  Also verifies the no-filter (None) baseline produces
clean Cypher without spurious predicates.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.trace_compiler.models import ExpandOptions


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_TIME_FROM = datetime(2024, 1, 1, tzinfo=timezone.utc)
_TIME_TO = datetime(2024, 12, 31, tzinfo=timezone.utc)

_FILTERED = ExpandOptions(time_from=_TIME_FROM, time_to=_TIME_TO, max_results=5)
_UNFILTERED = ExpandOptions(max_results=5)


class _AsyncCtxMgr:
    """Minimal async context manager wrapper for mocking."""

    def __init__(self, obj):
        self._obj = obj

    async def __aenter__(self):
        return self._obj

    async def __aexit__(self, *_):
        pass


class _FakeResult:
    """Empty async-iterable Neo4j result (no rows needed for Cypher capture)."""

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        return
        yield  # make this an async generator


def _make_neo4j_session():
    """Return a mock neo4j session that records the last Cypher string passed to run()."""
    captured = {}

    async def _run(cypher, **params):
        captured["cypher"] = cypher
        captured["params"] = params
        return _FakeResult()

    session = MagicMock()
    session.run = _run
    session._captured = captured
    return session


def _make_neo4j_driver(session):
    driver = MagicMock()
    driver.session = MagicMock(return_value=_AsyncCtxMgr(session))
    return driver


def _pg_empty():
    """asyncpg pool that always returns no rows (forces Neo4j fallback)."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCtxMgr(conn))
    return pool


# ---------------------------------------------------------------------------
# EVM fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evm_outbound_neo4j_includes_time_predicates():
    """_fetch_outbound_neo4j must include both timestamp predicates when set."""
    from src.trace_compiler.chains.evm import EVMChainCompiler

    neo4j_session = _make_neo4j_session()
    c = EVMChainCompiler(
        postgres_pool=_pg_empty(),
        neo4j_driver=_make_neo4j_driver(neo4j_session),
    )

    await c._fetch_outbound_neo4j("0xseed", "ethereum", _FILTERED)

    cypher = neo4j_session._captured["cypher"]
    assert "t.timestamp >= $time_from" in cypher, "Expected lower-bound predicate in Cypher"
    assert "t.timestamp <= $time_to" in cypher, "Expected upper-bound predicate in Cypher"


@pytest.mark.asyncio
async def test_evm_inbound_neo4j_includes_time_predicates():
    from src.trace_compiler.chains.evm import EVMChainCompiler

    neo4j_session = _make_neo4j_session()
    c = EVMChainCompiler(
        postgres_pool=_pg_empty(),
        neo4j_driver=_make_neo4j_driver(neo4j_session),
    )

    await c._fetch_inbound_neo4j("0xseed", "ethereum", _FILTERED)

    cypher = neo4j_session._captured["cypher"]
    assert "t.timestamp >= $time_from" in cypher
    assert "t.timestamp <= $time_to" in cypher


@pytest.mark.asyncio
async def test_evm_outbound_neo4j_no_predicates_when_unfiltered():
    """Without time options, fallback Cypher must contain no timestamp predicates."""
    from src.trace_compiler.chains.evm import EVMChainCompiler

    neo4j_session = _make_neo4j_session()
    c = EVMChainCompiler(
        postgres_pool=_pg_empty(),
        neo4j_driver=_make_neo4j_driver(neo4j_session),
    )

    await c._fetch_outbound_neo4j("0xseed", "ethereum", _UNFILTERED)

    cypher = neo4j_session._captured["cypher"]
    assert "time_from" not in cypher
    assert "time_to" not in cypher


@pytest.mark.asyncio
async def test_evm_outbound_neo4j_only_lower_bound():
    """Only time_from set: Cypher must include >= predicate but not <= predicate."""
    from src.trace_compiler.chains.evm import EVMChainCompiler

    neo4j_session = _make_neo4j_session()
    c = EVMChainCompiler(
        postgres_pool=_pg_empty(),
        neo4j_driver=_make_neo4j_driver(neo4j_session),
    )

    opts = ExpandOptions(time_from=_TIME_FROM, max_results=5)
    await c._fetch_outbound_neo4j("0xseed", "ethereum", opts)

    cypher = neo4j_session._captured["cypher"]
    assert "t.timestamp >= $time_from" in cypher
    assert "t.timestamp <= $time_to" not in cypher


# ---------------------------------------------------------------------------
# Bitcoin fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bitcoin_outbound_neo4j_includes_time_predicates():
    from src.trace_compiler.chains.bitcoin import UTXOChainCompiler

    neo4j_session = _make_neo4j_session()
    c = UTXOChainCompiler(
        postgres_pool=_pg_empty(),
        neo4j_driver=_make_neo4j_driver(neo4j_session),
    )

    await c._fetch_outbound_neo4j("bc1qseed", "bitcoin", _FILTERED)

    cypher = neo4j_session._captured["cypher"]
    assert "t.timestamp >= $time_from" in cypher
    assert "t.timestamp <= $time_to" in cypher


@pytest.mark.asyncio
async def test_bitcoin_inbound_neo4j_includes_time_predicates():
    from src.trace_compiler.chains.bitcoin import UTXOChainCompiler

    neo4j_session = _make_neo4j_session()
    c = UTXOChainCompiler(
        postgres_pool=_pg_empty(),
        neo4j_driver=_make_neo4j_driver(neo4j_session),
    )

    await c._fetch_inbound_neo4j("bc1qseed", "bitcoin", _FILTERED)

    cypher = neo4j_session._captured["cypher"]
    assert "t.timestamp >= $time_from" in cypher
    assert "t.timestamp <= $time_to" in cypher


@pytest.mark.asyncio
async def test_bitcoin_outbound_neo4j_no_predicates_when_unfiltered():
    from src.trace_compiler.chains.bitcoin import UTXOChainCompiler

    neo4j_session = _make_neo4j_session()
    c = UTXOChainCompiler(
        postgres_pool=_pg_empty(),
        neo4j_driver=_make_neo4j_driver(neo4j_session),
    )

    await c._fetch_outbound_neo4j("bc1qseed", "bitcoin", _UNFILTERED)

    cypher = neo4j_session._captured["cypher"]
    assert "time_from" not in cypher
    assert "time_to" not in cypher


# ---------------------------------------------------------------------------
# Solana fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_solana_outbound_neo4j_includes_time_predicates():
    from src.trace_compiler.chains.solana import SolanaChainCompiler

    neo4j_session = _make_neo4j_session()
    c = SolanaChainCompiler(
        postgres_pool=_pg_empty(),
        neo4j_driver=_make_neo4j_driver(neo4j_session),
    )

    await c._fetch_outbound_neo4j("SolanaSeedAddr", _FILTERED)

    cypher = neo4j_session._captured["cypher"]
    assert "t.timestamp >= $time_from" in cypher
    assert "t.timestamp <= $time_to" in cypher


@pytest.mark.asyncio
async def test_solana_inbound_neo4j_includes_time_predicates():
    from src.trace_compiler.chains.solana import SolanaChainCompiler

    neo4j_session = _make_neo4j_session()
    c = SolanaChainCompiler(
        postgres_pool=_pg_empty(),
        neo4j_driver=_make_neo4j_driver(neo4j_session),
    )

    await c._fetch_inbound_neo4j("SolanaSeedAddr", _FILTERED)

    cypher = neo4j_session._captured["cypher"]
    assert "t.timestamp >= $time_from" in cypher
    assert "t.timestamp <= $time_to" in cypher


@pytest.mark.asyncio
async def test_solana_outbound_neo4j_no_predicates_when_unfiltered():
    from src.trace_compiler.chains.solana import SolanaChainCompiler

    neo4j_session = _make_neo4j_session()
    c = SolanaChainCompiler(
        postgres_pool=_pg_empty(),
        neo4j_driver=_make_neo4j_driver(neo4j_session),
    )

    await c._fetch_outbound_neo4j("SolanaSeedAddr", _UNFILTERED)

    cypher = neo4j_session._captured["cypher"]
    assert "time_from" not in cypher
    assert "time_to" not in cypher
