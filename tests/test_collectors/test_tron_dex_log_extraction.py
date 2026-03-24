"""
Unit tests for TronCollector._extract_dex_logs_tron.

Verifies that DEX Swap event logs are correctly extracted from TronGrid's
wallet/gettransactioninfobyid response and returned in the schema expected
by BaseCollector._insert_raw_evm_logs (raw_evm_logs_tron, migration 013).

Covers:
- V2 Swap signature matched → log entry returned
- V3 Swap signature matched → log entry returned
- Unknown event signature → entry skipped
- No logs in tx_info → empty list
- Empty tx_info response → empty list
- RPC call failure → empty list (no raise)
- Log index assigned sequentially per entry
- Contract address normalised to 25-byte hex (41 prefix + 20-byte addr + 4-byte checksum)
- data and topic hex strings get 0x prefix
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.tron import TronCollector, _DEX_SWAP_SIGS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_V2_SIG = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
_V3_SIG = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
_UNKNOWN_SIG = "0x0000000000000000000000000000000000000000000000000000000000000000"

_CONTRACT_RAW = "e95812d8d5b5412d2b9f3a4d5a87ca15c5c51f33"  # 20-byte hex, no prefix
# Canonical 25-byte Tron hex: version byte (41) + 20-byte body + 4-byte double-SHA256 checksum.
# Matches TronCollector.hex_to_base58 output and the service classifier's event-store format.
_addr_21 = bytes.fromhex("41" + _CONTRACT_RAW)
_CONTRACT_FULL = (_addr_21 + hashlib.sha256(hashlib.sha256(_addr_21).digest()).digest()[:4]).hex().lower()

_TOPIC1 = "a" * 64
_DATA = "b" * 128


def _make_collector() -> TronCollector:
    """Minimal TronCollector with no live session."""
    collector = TronCollector.__new__(TronCollector)
    collector.blockchain = "tron"
    collector.session = MagicMock()
    collector.rpc_url = "https://api.trongrid.io"
    return collector


def _tx_info(logs: list) -> dict:
    return {"log": logs, "id": "txabc123"}


def _log_entry(sig_hex: str, extra_topics=None) -> dict:
    """Build a TronGrid log entry dict (topics without 0x prefix)."""
    # TronGrid returns topics without '0x' prefix
    topic0_raw = sig_hex[2:]  # strip leading 0x
    topics = [topic0_raw]
    if extra_topics:
        topics.extend(extra_topics)
    return {
        "address": _CONTRACT_RAW,
        "topics": topics,
        "data": _DATA,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_swap_sig_extracted():
    """V2 Swap signature in log topics[0] → one log entry returned."""
    collector = _make_collector()
    tx_info = _tx_info([_log_entry(_V2_SIG)])

    with patch.object(collector, "rpc_call", new=AsyncMock(return_value=tx_info)):
        logs = await collector._extract_dex_logs_tron("txhash123")

    assert len(logs) == 1
    assert logs[0]["event_sig"] == _V2_SIG


@pytest.mark.asyncio
async def test_v3_swap_sig_extracted():
    """V3 Swap signature in log topics[0] → one log entry returned."""
    collector = _make_collector()
    tx_info = _tx_info([_log_entry(_V3_SIG)])

    with patch.object(collector, "rpc_call", new=AsyncMock(return_value=tx_info)):
        logs = await collector._extract_dex_logs_tron("txhash456")

    assert len(logs) == 1
    assert logs[0]["event_sig"] == _V3_SIG


@pytest.mark.asyncio
async def test_unknown_sig_skipped():
    """Log entries with unrecognized event signatures are silently skipped."""
    collector = _make_collector()
    tx_info = _tx_info([_log_entry(_UNKNOWN_SIG)])

    with patch.object(collector, "rpc_call", new=AsyncMock(return_value=tx_info)):
        logs = await collector._extract_dex_logs_tron("txhash789")

    assert logs == []


@pytest.mark.asyncio
async def test_mixed_sigs_only_known_returned():
    """Only entries with known DEX Swap sigs are returned; unknown are skipped."""
    collector = _make_collector()
    tx_info = _tx_info([
        _log_entry(_UNKNOWN_SIG),
        _log_entry(_V2_SIG),
        _log_entry(_UNKNOWN_SIG),
        _log_entry(_V3_SIG),
    ])

    with patch.object(collector, "rpc_call", new=AsyncMock(return_value=tx_info)):
        logs = await collector._extract_dex_logs_tron("txhashXXX")

    assert len(logs) == 2
    assert {log["event_sig"] for log in logs} == {_V2_SIG, _V3_SIG}


@pytest.mark.asyncio
async def test_no_log_field_returns_empty():
    """tx_info with no 'log' key returns empty list without raising."""
    collector = _make_collector()
    tx_info = {"id": "txabc"}  # no 'log' field

    with patch.object(collector, "rpc_call", new=AsyncMock(return_value=tx_info)):
        logs = await collector._extract_dex_logs_tron("txhash000")

    assert logs == []


@pytest.mark.asyncio
async def test_empty_tx_info_returns_empty():
    """Empty tx_info (None or {}) returns empty list."""
    collector = _make_collector()

    with patch.object(collector, "rpc_call", new=AsyncMock(return_value=None)):
        logs = await collector._extract_dex_logs_tron("txhashNone")

    assert logs == []


@pytest.mark.asyncio
async def test_rpc_exception_returns_empty():
    """RPC call raising an exception returns empty list — does not raise."""
    collector = _make_collector()

    with patch.object(
        collector, "rpc_call", new=AsyncMock(side_effect=Exception("timeout"))
    ):
        logs = await collector._extract_dex_logs_tron("txhashERR")

    assert logs == []


@pytest.mark.asyncio
async def test_contract_address_normalised_to_25_byte_hex():
    """The 20-byte contract address is normalised to 25-byte Tron hex (41 prefix + 4-byte checksum)."""
    collector = _make_collector()
    tx_info = _tx_info([_log_entry(_V2_SIG)])

    with patch.object(collector, "rpc_call", new=AsyncMock(return_value=tx_info)):
        logs = await collector._extract_dex_logs_tron("txhashPFX")

    assert logs[0]["contract"] == _CONTRACT_FULL
    assert len(logs[0]["contract"]) == 50  # 25 bytes × 2 hex chars each


@pytest.mark.asyncio
async def test_data_gets_0x_prefix():
    """Log data hex string gets '0x' prefix in returned dict."""
    collector = _make_collector()
    tx_info = _tx_info([_log_entry(_V2_SIG)])

    with patch.object(collector, "rpc_call", new=AsyncMock(return_value=tx_info)):
        logs = await collector._extract_dex_logs_tron("txhashDATA")

    assert logs[0]["data"] == "0x" + _DATA


@pytest.mark.asyncio
async def test_log_index_assigned_sequentially():
    """log_index is assigned 0, 1, 2, ... in order of matched entries."""
    collector = _make_collector()
    tx_info = _tx_info([_log_entry(_V2_SIG), _log_entry(_V3_SIG)])

    with patch.object(collector, "rpc_call", new=AsyncMock(return_value=tx_info)):
        logs = await collector._extract_dex_logs_tron("txhashIDX")

    assert logs[0]["log_index"] == 0
    assert logs[1]["log_index"] == 1


@pytest.mark.asyncio
async def test_extra_topics_stored():
    """Topics beyond topic0 are stored in topic1/topic2/topic3 with 0x prefix."""
    collector = _make_collector()
    t1 = "c" * 64
    t2 = "d" * 64
    entry = _log_entry(_V2_SIG, extra_topics=[t1, t2])
    tx_info = _tx_info([entry])

    with patch.object(collector, "rpc_call", new=AsyncMock(return_value=tx_info)):
        logs = await collector._extract_dex_logs_tron("txhashTOPICS")

    assert logs[0]["topic1"] == "0x" + t1
    assert logs[0]["topic2"] == "0x" + t2
    assert logs[0]["topic3"] is None


def test_dex_swap_sigs_constant():
    """_DEX_SWAP_SIGS contains exactly the V2 and V3 Uniswap Swap signatures."""
    assert _V2_SIG in _DEX_SWAP_SIGS
    assert _V3_SIG in _DEX_SWAP_SIGS
    assert len(_DEX_SWAP_SIGS) == 2
