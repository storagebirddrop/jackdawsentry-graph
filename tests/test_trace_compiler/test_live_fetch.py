from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.trace_compiler.ingest.live_fetch import fetch_evm_address_history


@pytest.mark.asyncio
async def test_fetch_evm_address_history_leaves_queue_pending_on_api_error():
    """Transient Etherscan failures should not kill the ingest queue row."""
    pg_pool = MagicMock()

    with patch(
        "src.trace_compiler.ingest.live_fetch._etherscan_get",
        new=AsyncMock(side_effect=[None, None]),
    ), patch(
        "src.trace_compiler.ingest.live_fetch._mark_queue",
        new=AsyncMock(),
    ) as mark_queue:
        result = await fetch_evm_address_history("0xabc", "bsc", pg_pool, "key")

    assert result is False
    mark_queue.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_evm_address_history_marks_completed_when_no_history():
    """A clean 'no records found' response should complete the queue row."""
    pg_pool = MagicMock()

    with patch(
        "src.trace_compiler.ingest.live_fetch._etherscan_get",
        new=AsyncMock(side_effect=[[], []]),
    ), patch(
        "src.trace_compiler.ingest.live_fetch._mark_queue",
        new=AsyncMock(),
    ) as mark_queue:
        result = await fetch_evm_address_history("0xabc", "bsc", pg_pool, "key")

    assert result is False
    mark_queue.assert_awaited_once_with("0xabc", "bsc", pg_pool, "completed", None, tx_count=0)


@pytest.mark.asyncio
async def test_fetch_evm_address_history_leaves_queue_pending_when_chain_unsupported():
    """Unsupported direct-live chains should fall through to worker-based ingest."""
    pg_pool = MagicMock()

    with patch(
        "src.trace_compiler.ingest.live_fetch._mark_queue",
        new=AsyncMock(),
    ) as mark_queue:
        result = await fetch_evm_address_history("0xabc", "fantom", pg_pool, "key")

    assert result is False
    mark_queue.assert_not_awaited()
