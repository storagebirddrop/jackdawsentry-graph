from datetime import datetime
from datetime import timezone
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from src.trace_compiler.asset_selection import build_asset_option
from src.trace_compiler.chains.evm import EVMChainCompiler
from src.trace_compiler.chains.solana import SolanaChainCompiler
from src.trace_compiler.chains.tron import TronChainCompiler
from src.trace_compiler.compiler import TraceCompiler
from src.trace_compiler.models import AssetOptionsRequest


class _AsyncCtxMgr:
    def __init__(self, inner):
        self._inner = inner

    async def __aenter__(self):
        return self._inner

    async def __aexit__(self, *_):
        return False


def _asset_option_pool(*, native_exists: bool, token_rows: list[dict]) -> tuple[MagicMock, MagicMock]:
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=native_exists)
    conn.fetch = AsyncMock(return_value=token_rows)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCtxMgr(conn))
    return pool, conn


def _timestamp(day: int) -> datetime:
    return datetime(2026, 4, day, tzinfo=timezone.utc)


class _StaticAssetOptionCompiler:
    def __init__(self, options):
        self._options = options

    async def list_asset_options(self, *, seed_address: str, chain: str):
        return list(self._options)


@pytest.mark.asyncio
async def test_evm_list_asset_options_dedupes_normalized_contract_ids_and_keeps_native_first():
    pool, conn = _asset_option_pool(
        native_exists=True,
        token_rows=[
            {
                "chain_asset_id": "0xA0B86991C6218B36C1D19D4A2E9EB0CE3606EB48",
                "asset_symbol": "USDC",
                "canonical_asset_id": "usd-coin",
                "last_seen": _timestamp(9),
            },
            {
                "chain_asset_id": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                "asset_symbol": "USDC",
                "canonical_asset_id": "usd-coin",
                "last_seen": _timestamp(8),
            },
            {
                "chain_asset_id": "0xdAC17F958D2ee523A2206206994597C13D831ec7",
                "asset_symbol": "USDT",
                "canonical_asset_id": "tether",
                "last_seen": _timestamp(7),
            },
        ],
    )
    compiler = EVMChainCompiler(postgres_pool=pool)

    options = await compiler.list_asset_options(
        seed_address="0xABCDEFabcdefABCDEFabcdefABCDEFabcdefABCD",
        chain="ethereum",
    )

    assert [option.mode for option in options] == ["native", "asset", "asset"]
    assert [option.chain for option in options] == ["ethereum", "ethereum", "ethereum"]
    assert options[0].display_label == "Native ETH"
    assert options[1].chain_asset_id == "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    assert options[1].display_label == "USDC · 0xa0b86991...06eb48"
    assert options[2].chain_asset_id == "0xdac17f958d2ee523a2206206994597c13d831ec7"
    assert options[2].display_label == "USDT · 0xdac17f95...831ec7"

    assert conn.fetchval.await_args.args[1] == "ethereum"
    assert conn.fetchval.await_args.args[2] == "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"
    token_sql = conn.fetch.await_args.args[0]
    assert "GROUP BY asset_contract" in token_sql
    assert "ORDER BY MAX(timestamp) DESC NULLS LAST" in token_sql


@pytest.mark.asyncio
async def test_solana_list_asset_options_dedupes_repeated_mints_and_keeps_selector_labels_stable():
    pool, conn = _asset_option_pool(
        native_exists=True,
        token_rows=[
            {
                "chain_asset_id": "EPjFWdd5AufqSSqeM2qN1xzybAPq3n1LhF7sB7fJf5D",
                "asset_symbol": "USDC",
                "canonical_asset_id": "usd-coin",
                "last_seen": _timestamp(9),
            },
            {
                "chain_asset_id": "EPjFWdd5AufqSSqeM2qN1xzybAPq3n1LhF7sB7fJf5D",
                "asset_symbol": "USDC",
                "canonical_asset_id": "usd-coin",
                "last_seen": _timestamp(8),
            },
            {
                "chain_asset_id": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6nBjYa5B1pPB263",
                "asset_symbol": None,
                "canonical_asset_id": None,
                "last_seen": _timestamp(7),
            },
        ],
    )
    compiler = SolanaChainCompiler(postgres_pool=pool)

    options = await compiler.list_asset_options(
        seed_address="SoLSeed11111111111111111111111111111111111",
        chain="solana",
    )

    assert [option.mode for option in options] == ["native", "asset", "asset"]
    assert options[0].display_label == "Native SOL"
    assert options[1].chain_asset_id == "EPjFWdd5AufqSSqeM2qN1xzybAPq3n1LhF7sB7fJf5D"
    assert options[1].display_label.startswith("USDC · EPjFWdd5Au...")
    assert options[2].asset_symbol is None
    assert options[2].chain_asset_id == "DezXAZ8z7PnrnRJjz3wXBoRgixCa6nBjYa5B1pPB263"
    assert options[2].display_label.startswith("Asset · DezXAZ8z7P...")

    token_sql = conn.fetch.await_args.args[0]
    assert "GROUP BY asset_contract" in token_sql
    assert "ORDER BY MAX(timestamp) DESC NULLS LAST" in token_sql


@pytest.mark.asyncio
async def test_tron_list_asset_options_dedupes_repeated_contracts_and_keeps_native_first():
    pool, conn = _asset_option_pool(
        native_exists=True,
        token_rows=[
            {
                "chain_asset_id": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
                "asset_symbol": "USDT",
                "canonical_asset_id": "tether",
                "last_seen": _timestamp(9),
            },
            {
                "chain_asset_id": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
                "asset_symbol": "USDT",
                "canonical_asset_id": "tether",
                "last_seen": _timestamp(8),
            },
            {
                "chain_asset_id": "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8",
                "asset_symbol": "USDC",
                "canonical_asset_id": "usd-coin",
                "last_seen": _timestamp(7),
            },
        ],
    )
    compiler = TronChainCompiler(postgres_pool=pool)

    options = await compiler.list_asset_options(
        seed_address="TSeedAddress1111111111111111111111111111",
        chain="tron",
    )

    assert [option.mode for option in options] == ["native", "asset", "asset"]
    assert options[0].display_label == "Native TRX"
    assert options[1].chain_asset_id == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    assert options[1].display_label.startswith("USDT · TR7NHqjeKQ...")
    assert options[2].chain_asset_id == "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8"
    assert options[2].display_label.startswith("USDC · TEkxiTehnz...")

    token_sql = conn.fetch.await_args.args[0]
    assert "GROUP BY asset_contract" in token_sql
    assert "ORDER BY MAX(timestamp) DESC NULLS LAST" in token_sql


@pytest.mark.asyncio
async def test_trace_compiler_get_asset_options_prepends_all_only_for_non_bitcoin():
    compiler = TraceCompiler()
    compiler._chain_compilers["ethereum"] = _StaticAssetOptionCompiler(
        options=[
            build_asset_option(mode="native", chain="ethereum", asset_symbol="ETH"),
            build_asset_option(
                mode="asset",
                chain="ethereum",
                chain_asset_id="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                asset_symbol="USDC",
                canonical_asset_id="usd-coin",
            ),
        ]
    )

    response = await compiler.get_asset_options(
        "session-1",
        AssetOptionsRequest(
            seed_node_id="ethereum:address:0xA0A0A0A0A0A0A0A0A0A0A0A0A0A0A0A0A0A0A0A0",
            seed_lineage_id="lineage-1",
        ),
    )

    assert response.seed_node_id == "ethereum:address:0xa0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0"
    assert [option.mode for option in response.options] == ["all", "native", "asset"]
    assert response.options[0].display_label == "All assets"
    assert response.options[1].display_label == "Native ETH"
    assert response.options[2].display_label == "USDC · 0xa0b86991...06eb48"

    bitcoin_response = await compiler.get_asset_options(
        "session-1",
        AssetOptionsRequest(
            seed_node_id="bitcoin:address:bc1qassetoptionstest000000000000000000000",
            seed_lineage_id="lineage-btc",
        ),
    )

    assert bitcoin_response.options == []
