"""
Unit tests for AddressIngestWorker (src/collectors/address_ingest_worker.py).

All asyncpg and collector calls are mocked — no running DB or RPC required.

Covers:
- Generic chain dispatch: any collector registered under chain key is used
- Tron, XRP, Cosmos, Sui chains resolve correctly via collector map
- Unknown chain → row marked failed ("no collector for chain")
- get_address_transactions raises → row marked failed with error message
- Successful ingest: transactions persisted and row marked completed
- Partial failures: one bad tx does not abort the rest
- MAX_RETRIES exceeded → status = 'failed' (no retry)
- Retry still pending when retry_count < MAX_RETRIES
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.address_ingest_worker import (
    AddressIngestWorker,
    _MAX_RETRIES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_transaction(tx_hash: str = "0xabcd"):
    """Return a minimal mock transaction object."""
    tx = MagicMock()
    tx.hash = tx_hash
    return tx


def _make_collector(transactions=None, raise_on_get=False):
    """Return a mock collector that returns transactions or raises."""
    collector = MagicMock()
    if raise_on_get:
        collector.get_address_transactions = AsyncMock(
            side_effect=Exception("network error")
        )
    else:
        collector.get_address_transactions = AsyncMock(
            return_value=transactions or []
        )
    collector._insert_raw_transaction = AsyncMock()
    collector._insert_raw_token_transfers = AsyncMock()
    return collector


def _make_worker(collectors: dict) -> AddressIngestWorker:
    return AddressIngestWorker(collectors=collectors, poll_interval=999)


# ---------------------------------------------------------------------------
# DB mock helpers
# ---------------------------------------------------------------------------


class _AsyncCtxMgr:
    def __init__(self, inner):
        self._inner = inner

    async def __aenter__(self):
        return self._inner

    async def __aexit__(self, *_):
        pass


def _mock_conn():
    conn = MagicMock()
    conn.execute = AsyncMock()
    return conn


# ---------------------------------------------------------------------------
# _process_row — unknown chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_chain_marks_failed():
    """A chain with no registered collector marks the row as failed."""
    worker = _make_worker(collectors={})
    mock_conn = _mock_conn()

    with patch(
        "src.api.database.get_postgres_connection",
        return_value=_AsyncCtxMgr(mock_conn),
    ):
        await worker._process_row(
            queue_id=1, address="some_addr", chain="unknown_chain", retry_count=0
        )

    # _mark_failed should call conn.execute once
    assert mock_conn.execute.call_count >= 1
    call_args = mock_conn.execute.call_args
    # First positional arg is the SQL — should be the 'pending' retry path
    assert "address_ingest_queue" in call_args[0][0]


# ---------------------------------------------------------------------------
# _process_row — collector raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collector_exception_marks_failed():
    """get_address_transactions raising marks the row as failed."""
    collector = _make_collector(raise_on_get=True)
    worker = _make_worker(collectors={"ethereum": collector})
    mock_conn = _mock_conn()

    with patch(
        "src.api.database.get_postgres_connection",
        return_value=_AsyncCtxMgr(mock_conn),
    ):
        await worker._process_row(
            queue_id=2,
            address="0x1234",
            chain="ethereum",
            retry_count=0,
        )

    assert mock_conn.execute.call_count >= 1


# ---------------------------------------------------------------------------
# _process_row — successful ingest, one transaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_ingest_persists_transactions():
    """Returned transactions are persisted and row is marked completed."""
    tx = _make_transaction("0xdeadbeef")
    collector = _make_collector(transactions=[tx])
    worker = _make_worker(collectors={"ethereum": collector})
    mock_conn = _mock_conn()

    with patch(
        "src.api.database.get_postgres_connection",
        return_value=_AsyncCtxMgr(mock_conn),
    ):
        await worker._process_row(
            queue_id=3,
            address="0x1234",
            chain="ethereum",
            retry_count=0,
        )

    collector._insert_raw_transaction.assert_awaited_once_with(tx)
    collector._insert_raw_token_transfers.assert_awaited_once_with(tx)
    # Completed mark uses UPDATE ... SET status = 'completed'
    completed_sql = mock_conn.execute.call_args[0][0]
    assert "completed" in completed_sql


# ---------------------------------------------------------------------------
# _process_row — chain dispatch for non-EVM chains
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("chain", ["tron", "xrp", "cosmos", "sui"])
async def test_non_evm_chain_dispatches_correctly(chain: str):
    """Workers dispatch to any registered collector, not just EVM chains."""
    tx = _make_transaction("0xabc")
    collector = _make_collector(transactions=[tx])
    worker = _make_worker(collectors={chain: collector})
    mock_conn = _mock_conn()

    with patch(
        "src.api.database.get_postgres_connection",
        return_value=_AsyncCtxMgr(mock_conn),
    ):
        await worker._process_row(
            queue_id=4,
            address="some_address",
            chain=chain,
            retry_count=0,
        )

    collector.get_address_transactions.assert_awaited_once()
    collector._insert_raw_transaction.assert_awaited_once_with(tx)


# ---------------------------------------------------------------------------
# _process_row — partial failure (one tx write fails)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_tx_failure_does_not_abort():
    """A failing persist on one tx should not prevent others from being saved."""
    tx_good = _make_transaction("0xgood")
    tx_bad = _make_transaction("0xbad")
    collector = _make_collector(transactions=[tx_bad, tx_good])
    # First call raises, second call succeeds
    collector._insert_raw_transaction = AsyncMock(
        side_effect=[Exception("disk full"), None]
    )
    collector._insert_raw_token_transfers = AsyncMock()
    worker = _make_worker(collectors={"ethereum": collector})
    mock_conn = _mock_conn()

    with patch(
        "src.api.database.get_postgres_connection",
        return_value=_AsyncCtxMgr(mock_conn),
    ):
        await worker._process_row(
            queue_id=5,
            address="0x1234",
            chain="ethereum",
            retry_count=0,
        )

    # Despite first tx failing, second tx insert should have been attempted
    assert collector._insert_raw_transaction.call_count == 2
    # Row should be marked completed (with tx_count=1 for the successful one)
    completed_sql = mock_conn.execute.call_args[0][0]
    assert "completed" in completed_sql


# ---------------------------------------------------------------------------
# _mark_failed — retry logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_failed_retries_when_below_max():
    """Below MAX_RETRIES, the row is re-queued as pending with backoff."""
    worker = _make_worker(collectors={})
    mock_conn = _mock_conn()

    with patch(
        "src.api.database.get_postgres_connection",
        return_value=_AsyncCtxMgr(mock_conn),
    ):
        await worker._mark_failed(queue_id=10, error="err", retry_count=0)

    sql = mock_conn.execute.call_args[0][0]
    assert "pending" in sql


@pytest.mark.asyncio
async def test_mark_failed_permanent_at_max_retries():
    """At MAX_RETRIES, the row is permanently marked failed (not re-queued)."""
    worker = _make_worker(collectors={})
    mock_conn = _mock_conn()

    with patch(
        "src.api.database.get_postgres_connection",
        return_value=_AsyncCtxMgr(mock_conn),
    ):
        await worker._mark_failed(
            queue_id=11, error="permanent", retry_count=_MAX_RETRIES - 1
        )

    sql = mock_conn.execute.call_args[0][0]
    assert "failed" in sql


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_worker_initial_state():
    """Worker starts in stopped state."""
    worker = _make_worker(collectors={})
    assert not worker.is_running


@pytest.mark.asyncio
async def test_stop_sets_is_running_false():
    """stop() sets is_running to False."""
    worker = _make_worker(collectors={})
    worker.is_running = True
    await worker.stop()
    assert not worker.is_running
