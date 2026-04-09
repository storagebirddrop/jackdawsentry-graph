from __future__ import annotations

from collections import UserDict
from contextlib import asynccontextmanager
from datetime import datetime
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest

from src.collectors.backfill import EventStoreBackfillWorker
from src.collectors.base import BaseCollector
from src.collectors.base import TokenTransfer
from src.collectors.base import Transaction
from src.collectors.cosmos import CosmosCollector
from src.collectors.ethereum import EthereumCollector
from src.collectors.manager import CollectorManager
from src.collectors.solana import SolanaCollector


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


class _FakeSelector:
    def __init__(self, value: str):
        self._value = value

    def hex(self) -> str:
        return self._value


class _FakeContractCall:
    def __init__(self, value):
        self._value = value

    def call(self):
        return self._value


class _FakeContractFunctions:
    def name(self):
        return _FakeContractCall("Test Token")

    def symbol(self):
        return _FakeContractCall("TEST")

    def totalSupply(self):
        return _FakeContractCall(123456)


class _FakeEth:
    def __init__(self):
        self.calls: list[str] = []

    def call(self, payload):
        self.calls.append(payload["data"])
        return bytes.fromhex("0000000000000000000000000000000000000000000000000000000000000006")

    def get_code(self, address):
        return b"\x60\x00"

    def contract(self, address=None, **kwargs):
        return SimpleNamespace(functions=_FakeContractFunctions())


class _FakeW3:
    def __init__(self):
        self.eth = _FakeEth()

    def keccak(self, text: str):
        mapping = {
            "decimals()": "0x313ce56700000000000000000000000000000000000000000000000000000000",
            "name()": "0x06fdde0300000000000000000000000000000000000000000000000000000000",
            "symbol()": "0x95d89b4100000000000000000000000000000000000000000000000000000000",
            "totalSupply()": "0x18160ddd00000000000000000000000000000000000000000000000000000000",
        }
        return _FakeSelector(mapping[text])


@pytest.mark.asyncio
async def test_evm_token_metadata_calls_use_keccak_selectors():
    collector = EthereumCollector("ethereum", {"rpc_url": "http://example.invalid"})
    collector.w3 = _FakeW3()

    decimals = await collector.get_token_decimals("0x1111111111111111111111111111111111111111")
    info = await collector.get_contract_info("0x1111111111111111111111111111111111111111")

    assert decimals == 6
    assert info == {
        "address": "0x1111111111111111111111111111111111111111",
        "has_code": True,
        "bytecode_size": 2,
        "name": "Test Token",
        "symbol": "TEST",
        "total_supply": 123456,
    }
    assert collector.w3.eth.calls == [
        "0x313ce567",
        "0x06fdde03",
        "0x95d89b41",
        "0x18160ddd",
    ]


@pytest.mark.asyncio
async def test_process_stablecoin_transfers_awaits_summary_consumption():
    collector = _DummyCollector("ethereum", {})
    tx = Transaction(
        hash="0xdeadbeef",
        blockchain="ethereum",
        timestamp=datetime.now(timezone.utc),
        token_transfers=[
            TokenTransfer(
                tx_hash="0xdeadbeef",
                blockchain="ethereum",
                transfer_index=0,
                asset_type="erc20",
                asset_symbol="USDC",
                from_address="0x1111",
                to_address="0x2222",
                amount_raw="1000000",
                amount_normalized=1.0,
                canonical_asset_id="usd-coin",
            )
        ],
    )
    result = SimpleNamespace(
        consume=AsyncMock(return_value=SimpleNamespace(relationships_created=1))
    )
    session = SimpleNamespace(run=AsyncMock(return_value=result))

    @asynccontextmanager
    async def fake_neo4j_session():
        yield session

    with patch("src.collectors.base.get_neo4j_session", fake_neo4j_session):
        await collector.process_stablecoin_transfers(tx)

    session.run.assert_awaited_once()
    result.consume.assert_awaited_once()


@pytest.mark.asyncio
async def test_solana_disconnect_clears_closed_client_reference():
    collector = SolanaCollector({})
    client = SimpleNamespace(close=AsyncMock(return_value=None))
    collector.client = client

    await collector.disconnect()

    client.close.assert_awaited_once()
    assert collector.client is None


@pytest.mark.asyncio
async def test_manager_monitor_health_waits_for_startup_grace_before_restarts():
    manager = CollectorManager()
    manager.collectors = {"solana": SimpleNamespace(is_running=False)}
    manager.health_startup_grace_period = 1
    manager.health_check_interval = 300
    manager.is_running = True

    async def fake_sleep(_seconds):
        manager.is_running = False

    with patch.object(manager, "restart_collector", AsyncMock()) as restart_collector:
        with patch("src.collectors.manager.asyncio.sleep", new=AsyncMock(side_effect=fake_sleep)):
            await manager.monitor_health()

    restart_collector.assert_not_called()


@pytest.mark.asyncio
async def test_ethereum_start_skips_pending_monitor_when_connect_fails():
    collector = EthereumCollector("polygon", {"rpc_url": "http://example.invalid"})

    with patch.object(collector, "connect", AsyncMock(return_value=False)):
        with patch.object(collector, "monitor_pending_transactions", AsyncMock()) as monitor_pending_transactions:
            await collector.start()

    monitor_pending_transactions.assert_not_called()


class _PendingFilterThatExpires:
    def get_new_entries(self):
        raise Exception("{'code': -32000, 'message': 'filter not found'}")


class _PendingFilterW3:
    def __init__(self):
        self.eth = SimpleNamespace(filter=lambda _kind: _PendingFilterThatExpires())


class _BlockCachingEth:
    def __init__(self):
        self.transaction_lookup_calls = 0
        self.tx_hash = bytes.fromhex("11" * 32)
        self.block_hash = bytes.fromhex("22" * 32)
        self.parent_hash = bytes.fromhex("33" * 32)

    def get_block(self, block_number, full_transactions=True):
        return {
            "hash": self.block_hash,
            "number": block_number,
            "timestamp": 1710000000,
            "transactions": [
                UserDict(
                    {
                        "hash": self.tx_hash,
                        "from": "0x1111",
                        "to": "0x2222",
                        "value": 0,
                        "blockNumber": block_number,
                        "blockHash": self.block_hash,
                        "gasPrice": 1,
                    }
                )
            ],
            "parentHash": self.parent_hash,
            "miner": "0xminer",
            "difficulty": 1,
            "size": 1,
        }

    def get_transaction(self, tx_hash):
        self.transaction_lookup_calls += 1
        raise Exception(f"Transaction with hash: '{tx_hash}' not found.")

    def get_transaction_receipt(self, tx_hash):
        return {"gasUsed": 21000, "status": 1, "contractAddress": None}


class _BlockCachingW3:
    def __init__(self):
        self.eth = _BlockCachingEth()


@pytest.mark.asyncio
async def test_pending_transaction_monitor_stops_when_rpc_loses_filter():
    collector = EthereumCollector("ethereum", {"rpc_url": "http://example.invalid"})
    collector.w3 = _PendingFilterW3()
    collector.is_running = True

    with patch("src.collectors.ethereum.asyncio.sleep", new=AsyncMock()) as sleep_mock:
        await collector.monitor_pending_transactions()

    sleep_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_cosmos_load_last_processed_block_clamps_to_retention_floor():
    collector = CosmosCollector("injective", {"rest_url": "https://example.invalid"})
    redis = SimpleNamespace(get=AsyncMock(return_value="159055476"))

    @asynccontextmanager
    async def fake_redis_connection():
        yield redis

    with patch.object(collector, "get_latest_block_number", AsyncMock(return_value=160600000)):
        with patch.object(
            collector,
            "get_first_available_block_number",
            AsyncMock(return_value=160559587),
        ):
            with patch("src.api.database.get_redis_connection", fake_redis_connection):
                await collector.load_last_processed_block()

    assert collector.last_block_processed == 160559586


@pytest.mark.asyncio
async def test_cosmos_pruned_block_fetch_fast_forwards_checkpoint():
    collector = CosmosCollector("injective", {"rest_url": "https://example.invalid"})
    collector.last_block_processed = 159055475
    collector._remember_first_available_block(160559587)

    with patch.object(collector, "_get_json", AsyncMock(return_value=None)):
        block = await collector.get_block(159055476)
        tx_hashes = await collector.get_block_transactions(159055476)

    assert block is None
    assert tx_hashes == []
    assert collector.last_block_processed == 160559586


@pytest.mark.asyncio
async def test_backfill_state_clamps_target_to_first_available_block():
    collector = SimpleNamespace(
        is_running=True,
        get_latest_block_number=AsyncMock(return_value=160565000),
        get_first_available_block_number=AsyncMock(return_value=160559587),
    )
    worker = EventStoreBackfillWorker({"injective": collector})
    conn = SimpleNamespace(
        fetchrow=AsyncMock(
            return_value={
                "blockchain": "injective",
                "status": "running",
                "latest_observed_block": 160565000,
                "target_block": 159055476,
                "next_block": 160565000,
                "attempted_blocks": 0,
                "attempted_transactions": 0,
                "last_error": None,
            }
        ),
        execute=AsyncMock(return_value=None),
    )

    @asynccontextmanager
    async def fake_postgres_connection():
        yield conn

    with patch("src.collectors.backfill.get_postgres_connection", fake_postgres_connection):
        state = await worker._ensure_chain_state("injective", collector)

    assert state["target_block"] == 160559587


@pytest.mark.asyncio
async def test_evm_uses_cached_block_transactions_when_hash_lookup_is_missing():
    collector = EthereumCollector(
        "avalanche",
        {"rpc_url": "http://example.invalid", "erc20_tracking": False},
    )
    collector.w3 = _BlockCachingW3()

    block = await collector.get_block(123)
    tx_hashes = await collector.get_block_transactions(123)
    tx = await collector.get_transaction(tx_hashes[0])

    assert block is not None
    assert tx_hashes == ["0x" + "11" * 32]
    assert tx is not None
    assert tx.hash == tx_hashes[0]
    assert collector.w3.eth.transaction_lookup_calls == 0
