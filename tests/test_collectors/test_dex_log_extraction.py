"""
Unit tests for EthereumCollector._extract_dex_logs and base _insert_raw_evm_logs.

Covers:
- V2/V3/V4 Swap logs are recognised and returned
- Non-Swap logs (ERC-20 Transfer) are ignored
- Multiple DEX logs in one receipt are all returned
- HexBytes topics and address are handled correctly
- Data field is hex-encoded correctly
- Malformed log entries are skipped gracefully
- _insert_raw_evm_logs calls executemany with correct row tuple shape
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timezone

import pytest

from src.collectors.ethereum import EthereumCollector


# ---------------------------------------------------------------------------
# Constants matching EthereumCollector._DEX_SWAP_SIGS
# ---------------------------------------------------------------------------

_V2_SIG = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
_V3_SIG = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
_V4_SIG = "0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83"
_TRANSFER_SIG = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeHex:
    """Simulates a web3.py HexBytes object with a .hex() method."""
    def __init__(self, value: str):
        # Strip '0x' prefix for storage — hex() should return it without prefix
        self._val = value.lstrip("0x")

    def hex(self) -> str:
        return self._val


def _sig_bytes(sig: str) -> _FakeHex:
    """Return a fake HexBytes for a keccak256 event signature (with 0x prefix)."""
    return _FakeHex(sig)


def _make_collector() -> EthereumCollector:
    """Return an EthereumCollector with minimal dependencies."""
    c = EthereumCollector.__new__(EthereumCollector)
    c.blockchain = "ethereum"
    c.erc20_tracking = True
    return c


def _make_log(
    topic0: _FakeHex,
    address: str = "0xpool",
    data: bytes = b"\x00" * 128,
    extra_topics=None,
    log_index: int = 0,
) -> dict:
    """Build a minimal receipt log dict."""
    topics = [topic0] + (extra_topics or [])
    return {
        "address": address,
        "topics": topics,
        "data": _FakeHex("0x" + data.hex()),
        "logIndex": log_index,
    }


# ---------------------------------------------------------------------------
# _extract_dex_logs — DEX signatures recognised
# ---------------------------------------------------------------------------


def test_v2_swap_log_extracted():
    collector = _make_collector()
    log = _make_log(_sig_bytes(_V2_SIG), log_index=3)
    result = collector._extract_dex_logs({"logs": [log]})
    assert len(result) == 1
    assert result[0]["event_sig"] == _V2_SIG


def test_v3_swap_log_extracted():
    collector = _make_collector()
    log = _make_log(_sig_bytes(_V3_SIG), log_index=1)
    result = collector._extract_dex_logs({"logs": [log]})
    assert len(result) == 1
    assert result[0]["event_sig"] == _V3_SIG


def test_v4_swap_log_extracted():
    collector = _make_collector()
    log = _make_log(_sig_bytes(_V4_SIG))
    result = collector._extract_dex_logs({"logs": [log]})
    assert len(result) == 1
    assert result[0]["event_sig"] == _V4_SIG


def test_transfer_log_ignored():
    """ERC-20 Transfer events must NOT be stored in raw_evm_logs."""
    collector = _make_collector()
    log = _make_log(_sig_bytes(_TRANSFER_SIG))
    result = collector._extract_dex_logs({"logs": [log]})
    assert result == []


def test_multiple_swap_logs_returned():
    """When a tx hits multiple pools the collector captures all Swap logs."""
    collector = _make_collector()
    logs = [
        _make_log(_sig_bytes(_V3_SIG), address="0xpool1", log_index=2),
        _make_log(_sig_bytes(_TRANSFER_SIG), log_index=3),  # ignored
        _make_log(_sig_bytes(_V3_SIG), address="0xpool2", log_index=4),
    ]
    result = collector._extract_dex_logs({"logs": logs})
    assert len(result) == 2
    contracts = {r["contract"] for r in result}
    assert "0xpool1" in contracts
    assert "0xpool2" in contracts


def test_contract_address_lowercased():
    collector = _make_collector()
    log = _make_log(_sig_bytes(_V2_SIG), address="0xABCDEF")
    result = collector._extract_dex_logs({"logs": [log]})
    assert result[0]["contract"] == "0xabcdef"


def test_log_index_preserved():
    collector = _make_collector()
    log = _make_log(_sig_bytes(_V3_SIG), log_index=7)
    result = collector._extract_dex_logs({"logs": [log]})
    assert result[0]["log_index"] == 7


def test_topics_extracted():
    """topic1 and topic2 (indexed args) are preserved."""
    collector = _make_collector()
    sender_topic = _FakeHex("000000000000000000000000" + "a" * 40)
    recipient_topic = _FakeHex("000000000000000000000000" + "b" * 40)
    log = _make_log(
        _sig_bytes(_V3_SIG),
        extra_topics=[sender_topic, recipient_topic],
    )
    result = collector._extract_dex_logs({"logs": [log]})
    assert result[0]["topic1"] is not None
    assert result[0]["topic2"] is not None
    assert result[0]["topic3"] is None


def test_data_hex_encoded():
    """Raw bytes data is converted to a 0x-prefixed hex string."""
    collector = _make_collector()
    raw = bytes([0xDE, 0xAD, 0xBE, 0xEF] + [0] * 124)
    log = _make_log(_sig_bytes(_V2_SIG), data=raw)
    result = collector._extract_dex_logs({"logs": [log]})
    data_hex = result[0]["data"]
    assert isinstance(data_hex, str)
    assert data_hex.lower().startswith("0x")
    assert "deadbeef" in data_hex.lower()


def test_empty_logs_returns_empty():
    collector = _make_collector()
    result = collector._extract_dex_logs({"logs": []})
    assert result == []


def test_no_logs_key_returns_empty():
    collector = _make_collector()
    result = collector._extract_dex_logs({})
    assert result == []


def test_malformed_log_skipped_gracefully():
    """A log dict missing required fields should not raise."""
    collector = _make_collector()
    # logs without topics key — should just be skipped
    result = collector._extract_dex_logs({"logs": [{}]})
    assert result == []


# ---------------------------------------------------------------------------
# _DEX_SWAP_SIGS class attribute
# ---------------------------------------------------------------------------


def test_dex_swap_sigs_contains_all_three():
    assert _V2_SIG in EthereumCollector._DEX_SWAP_SIGS
    assert _V3_SIG in EthereumCollector._DEX_SWAP_SIGS
    assert _V4_SIG in EthereumCollector._DEX_SWAP_SIGS


def test_transfer_sig_not_in_dex_swap_sigs():
    assert _TRANSFER_SIG not in EthereumCollector._DEX_SWAP_SIGS


# ---------------------------------------------------------------------------
# base._insert_raw_evm_logs — DB write shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_raw_evm_logs_calls_executemany():
    """_insert_raw_evm_logs issues one executemany call with correct row shape."""
    from src.collectors.base import Transaction

    tx = Transaction(
        hash="0xdeadbeef",
        blockchain="ethereum",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        dex_logs=[
            {
                "log_index": 2,
                "contract": "0xpool",
                "event_sig": _V3_SIG,
                "topic1": "0xsender",
                "topic2": "0xrecipient",
                "topic3": None,
                "data": "0x" + "00" * 128,
            }
        ],
    )

    mock_conn = AsyncMock()
    mock_conn.executemany = AsyncMock()

    with patch(
        "src.collectors.base.get_postgres_connection"
    ) as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        # Use EthereumCollector (concrete subclass) to avoid ABC instantiation error.
        collector = EthereumCollector.__new__(EthereumCollector)
        collector.blockchain = "ethereum"
        await collector._insert_raw_evm_logs(tx)

    mock_conn.executemany.assert_called_once()
    rows = mock_conn.executemany.call_args[0][1]
    assert len(rows) == 1
    row = rows[0]
    # (blockchain, tx_hash, log_index, contract, event_sig, t1, t2, t3, data, timestamp)
    assert row[0] == "ethereum"
    assert row[1] == "0xdeadbeef"
    assert row[2] == 2
    assert row[3] == "0xpool"
    assert row[4] == _V3_SIG


@pytest.mark.asyncio
async def test_insert_raw_evm_logs_skips_empty():
    """No DB call when tx.dex_logs is empty."""
    from src.collectors.base import Transaction

    tx = Transaction(
        hash="0xabc", blockchain="ethereum",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        dex_logs=[],
    )
    collector = EthereumCollector.__new__(EthereumCollector)
    collector.blockchain = "ethereum"

    with patch("src.collectors.base.get_postgres_connection") as mock_ctx:
        await collector._insert_raw_evm_logs(tx)
        mock_ctx.assert_not_called()


@pytest.mark.asyncio
async def test_insert_raw_evm_logs_swallows_db_error():
    """DB failures are logged as warnings — not raised."""
    from src.collectors.base import Transaction

    tx = Transaction(
        hash="0xabc", blockchain="ethereum",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        dex_logs=[{
            "log_index": 0, "contract": "0xpool",
            "event_sig": _V3_SIG, "topic1": None,
            "topic2": None, "topic3": None, "data": None,
        }],
    )
    collector = EthereumCollector.__new__(EthereumCollector)
    collector.blockchain = "ethereum"

    with patch("src.collectors.base.get_postgres_connection") as mock_ctx:
        mock_ctx.side_effect = Exception("DB is down")
        # Should not raise
        await collector._insert_raw_evm_logs(tx)
