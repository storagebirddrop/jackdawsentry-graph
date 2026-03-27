from __future__ import annotations

from datetime import datetime
from datetime import timezone

from src.collectors.base import BaseCollector
from src.collectors.base import TokenTransfer
from src.collectors.base import Transaction


class _DummyCollector(BaseCollector):
    async def connect(self) -> bool:
        return True

    async def disconnect(self):
        return None

    async def get_latest_block_number(self) -> int:
        return 0

    async def get_block(self, block_number: int):
        return None

    async def get_transaction(self, tx_hash: str):
        return None

    async def get_address_balance(self, address: str):
        return 0

    async def get_address_transactions(self, address: str, limit: int = 100):
        return []

    async def get_block_transactions(self, block_number: int):
        return []


def test_normalize_token_transfers_coerces_legacy_dict_payload():
    collector = _DummyCollector("ethereum", {})
    tx = Transaction(
        hash="0xdead",
        blockchain="ethereum",
        timestamp=datetime.now(timezone.utc),
        token_transfers=[
            {
                "symbol": "usdc",
                "contract_address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                "from_address": "0x1111",
                "to_address": "0x2222",
                "amount": 12.5,
                "decimals": 6,
            },
        ],
    )

    collector._normalize_token_transfers(tx)

    transfer = tx.token_transfers[0]
    assert isinstance(transfer, TokenTransfer)
    assert transfer.asset_type == "erc20"
    assert transfer.asset_symbol == "USDC"
    assert transfer.amount_raw == "12500000"
    assert transfer.amount_normalized == 12.5
    assert transfer.canonical_asset_id == "usdc"


def test_normalize_token_transfers_falls_back_to_contract_label():
    collector = _DummyCollector("tron", {})
    tx = Transaction(
        hash="0xbeef",
        blockchain="tron",
        timestamp=datetime.now(timezone.utc),
        token_transfers=[
            {
                "contract_address": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
                "from_address": "TA1",
                "to_address": "TB2",
                "amount_raw": 1000,
                "amount_normalized": 0.001,
            },
        ],
    )

    collector._normalize_token_transfers(tx)

    transfer = tx.token_transfers[0]
    assert isinstance(transfer, TokenTransfer)
    assert transfer.asset_type == "trc20"
    assert transfer.asset_symbol == "TR7NHq...Lj6t"
    assert transfer.amount_raw == "1000"
    assert transfer.amount_normalized == 0.001
