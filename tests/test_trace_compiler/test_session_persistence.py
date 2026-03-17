"""Unit tests for investigation session persistence (issue #7).

Verifies:
- create_session() inserts a row into graph_sessions when PG pool is available.
- create_session() silently succeeds even when PG INSERT fails.
- create_session() still returns a valid response when no PG pool is configured.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.trace_compiler.compiler import TraceCompiler
from src.trace_compiler.models import SessionCreateRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pg_pool(execute_raises=False):
    """Return a mock asyncpg pool."""
    conn = MagicMock()
    if execute_raises:
        conn.execute = AsyncMock(side_effect=Exception("DB error"))
    else:
        conn.execute = AsyncMock(return_value=None)

    class _Ctx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *_):
            pass

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Ctx())
    pool._conn = conn  # expose for assertion
    return pool


def _create_request(seed_address="0xabc", seed_chain="ethereum"):
    return SessionCreateRequest(seed_address=seed_address, seed_chain=seed_chain)


# ---------------------------------------------------------------------------
# create_session persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_inserts_graph_sessions_row():
    """When PG pool is available, create_session INSERTs into graph_sessions."""
    pg = _pg_pool()
    compiler = TraceCompiler(postgres_pool=pg)
    request = _create_request()

    resp = await compiler.create_session(request)

    assert resp.session_id is not None
    pg._conn.execute.assert_called_once()
    sql, *args = pg._conn.execute.call_args[0]
    assert "graph_sessions" in sql
    assert "INSERT" in sql.upper()


@pytest.mark.asyncio
async def test_create_session_returns_valid_response_on_pg_failure():
    """PG INSERT failure is swallowed; response is still returned."""
    pg = _pg_pool(execute_raises=True)
    compiler = TraceCompiler(postgres_pool=pg)

    resp = await compiler.create_session(_create_request())

    assert resp.session_id is not None
    assert resp.root_node is not None


@pytest.mark.asyncio
async def test_create_session_no_pg_returns_valid_response():
    """No PG pool configured: create_session still returns a valid response."""
    compiler = TraceCompiler(postgres_pool=None)

    resp = await compiler.create_session(_create_request())

    assert resp.session_id is not None
    assert resp.root_node.chain == "ethereum"


@pytest.mark.asyncio
async def test_create_session_root_node_has_correct_fields():
    """Root node has correct node_id, chain, and expandable_directions."""
    compiler = TraceCompiler(postgres_pool=None)
    resp = await compiler.create_session(_create_request(seed_address="0xdeadbeef", seed_chain="bsc"))

    root = resp.root_node
    assert root.chain == "bsc"
    assert "0xdeadbeef" in root.node_id
    assert "next" in root.expandable_directions
    assert "prev" in root.expandable_directions


@pytest.mark.asyncio
async def test_create_session_persists_seed_address_and_chain():
    """The INSERT call receives the correct seed_address and seed_chain args."""
    pg = _pg_pool()
    compiler = TraceCompiler(postgres_pool=pg)
    await compiler.create_session(_create_request(seed_address="0xtest", seed_chain="polygon"))

    args = pg._conn.execute.call_args[0]
    # args[0] is SQL; subsequent args are the parameter values
    param_values = list(args[1:])
    assert "0xtest" in param_values
    assert "polygon" in param_values
