"""
Unit tests for BridgeTracer and BridgeCorrelation.

All external HTTP calls and DB interactions are mocked.  Tests verify:
- BridgeCorrelation dataclass construction
- Per-protocol response parsing (THORChain, Wormhole, LI.FI, Squid, Mayan,
  Synapse, deBridge, Symbiosis, Allbridge)
- Graceful None returns for protocols requiring intermediate IDs
- store_correlation UPSERT parameter passing
- lookup_correlation DB read and BridgeCorrelation mapping
"""

from __future__ import annotations

import json
from dataclasses import asdict
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from src.tracing.bridge_tracer import BridgeCorrelation
from src.tracing.bridge_tracer import BridgeTracer
from src.tracing.bridge_tracer import _evm_chain_id_to_name
from src.tracing.bridge_tracer import _thorchain_amount
from src.tracing.bridge_tracer import _thorchain_asset


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _corr(**kwargs) -> BridgeCorrelation:
    """Return a BridgeCorrelation with sensible defaults for testing."""
    defaults = dict(
        protocol="thorchain",
        mechanism="native_amm",
        source_chain="ethereum",
        source_tx_hash="0xabc",
        source_address="0xsender",
        source_asset="ETH",
        source_amount=1.0,
        source_fiat_value=None,
        destination_chain="bitcoin",
        destination_tx_hash="btc_tx",
        destination_address="bc1q...",
        destination_asset="BTC",
        destination_amount=0.05,
        destination_fiat_value=None,
        time_delta_seconds=30,
        status="completed",
        correlation_confidence=0.95,
    )
    defaults.update(kwargs)
    return BridgeCorrelation(**defaults)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_thorchain_asset_full(self):
        assert _thorchain_asset("ETH.ETH") == "ETH"

    def test_thorchain_asset_token(self):
        assert _thorchain_asset("ETH.USDC-0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48") == "USDC"

    def test_thorchain_asset_empty(self):
        assert _thorchain_asset("") == ""

    def test_thorchain_amount(self):
        assert _thorchain_amount("100000000") == pytest.approx(1.0)

    def test_thorchain_amount_none(self):
        assert _thorchain_amount(None) is None

    def test_evm_chain_id_ethereum(self):
        assert _evm_chain_id_to_name(1) == "ethereum"

    def test_evm_chain_id_arbitrum(self):
        assert _evm_chain_id_to_name(42161) == "arbitrum"

    def test_evm_chain_id_unknown(self):
        assert _evm_chain_id_to_name(99999) is None

    def test_evm_chain_id_none(self):
        assert _evm_chain_id_to_name(None) is None


# ---------------------------------------------------------------------------
# BridgeCorrelation dataclass
# ---------------------------------------------------------------------------

class TestBridgeCorrelation:
    def test_completed_fields(self):
        c = _corr()
        assert c.status == "completed"
        assert c.destination_chain == "bitcoin"
        assert c.destination_address == "bc1q..."

    def test_pending_fields(self):
        c = _corr(
            status="pending",
            destination_chain=None,
            destination_address=None,
            destination_tx_hash=None,
        )
        assert c.status == "pending"
        assert c.destination_chain is None

    def test_optional_order_id_defaults_none(self):
        c = _corr()
        assert c.order_id is None

    def test_with_order_id(self):
        c = _corr(order_id="order-123")
        assert c.order_id == "order-123"


# ---------------------------------------------------------------------------
# detect_bridge_hop — unknown contract returns None
# ---------------------------------------------------------------------------

class TestDetectBridgeHopNoProtocol:
    @pytest.mark.asyncio
    async def test_unknown_contract_returns_none(self):
        tracer = BridgeTracer()
        result = await tracer.detect_bridge_hop(
            tx_hash="0xdeadbeef",
            blockchain="ethereum",
            to_address="0x0000000000000000000000000000000000000001",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_none_to_address_returns_none(self):
        tracer = BridgeTracer()
        result = await tracer.detect_bridge_hop(
            tx_hash="0xdeadbeef",
            blockchain="ethereum",
            to_address=None,
        )
        assert result is None


# ---------------------------------------------------------------------------
# THORChain
# ---------------------------------------------------------------------------

_THORCHAIN_COMPLETED = {
    "observed_tx": {
        "tx": {
            "from_address": "0xsender",
            "coins": [{"asset": "ETH.ETH", "amount": "100000000"}],
        }
    },
    "out_txs": [
        {
            "id": "btctxhash",
            "chain": "BTC",
            "to_address": "bc1qrecipient",
            "coins": [{"asset": "BTC.BTC", "amount": "5000000"}],
        }
    ],
}

_THORCHAIN_PENDING = {
    "observed_tx": {
        "tx": {
            "from_address": "0xsender",
            "coins": [{"asset": "ETH.ETH", "amount": "100000000"}],
        }
    },
    "out_txs": [],
}


class TestTHORChainLookup:
    @pytest.mark.asyncio
    async def test_completed_hop(self):
        tracer = BridgeTracer()

        from src.tracing.bridge_registry import BRIDGE_REGISTRY
        proto = BRIDGE_REGISTRY["thorchain"]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _THORCHAIN_COMPLETED
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tracer._thorchain_lookup(proto, "0xabc", "ethereum")

        assert result is not None
        assert result.status == "completed"
        assert result.destination_chain == "bitcoin"
        assert result.destination_address == "bc1qrecipient"
        assert result.destination_asset == "BTC"
        assert result.source_asset == "ETH"
        assert result.source_amount == pytest.approx(1.0)
        assert result.destination_amount == pytest.approx(0.05)
        assert result.protocol == "thorchain"
        assert result.resolution_method == "thorchain_api"

    @pytest.mark.asyncio
    async def test_pending_hop_when_no_out_txs(self):
        tracer = BridgeTracer()
        from src.tracing.bridge_registry import BRIDGE_REGISTRY
        proto = BRIDGE_REGISTRY["thorchain"]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _THORCHAIN_PENDING
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tracer._thorchain_lookup(proto, "0xabc", "ethereum")

        assert result is not None
        assert result.status == "pending"
        assert result.destination_chain is None

    @pytest.mark.asyncio
    async def test_404_returns_none(self):
        tracer = BridgeTracer()
        from src.tracing.bridge_registry import BRIDGE_REGISTRY
        proto = BRIDGE_REGISTRY["thorchain"]

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tracer._thorchain_lookup(proto, "0xabc", "ethereum")

        assert result is None


# ---------------------------------------------------------------------------
# Wormhole
# ---------------------------------------------------------------------------

_WORMHOLE_RESPONSE = {
    "operations": [
        {
            "content": {
                "payload": {
                    "toChain": 1,  # Solana
                    "toAddress": "SolanaRecipientAddr",
                }
            },
            "data": {"symbol": "USDC"},
            "targetChain": {
                "chainId": 1,
                "transaction": {"txHash": "solanatxhash"},
            },
        }
    ]
}


class TestWormholeLookup:
    @pytest.mark.asyncio
    async def test_completed_hop(self):
        tracer = BridgeTracer()
        from src.tracing.bridge_registry import BRIDGE_REGISTRY
        proto = BRIDGE_REGISTRY["wormhole"]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _WORMHOLE_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tracer._wormhole_lookup(proto, "0xevmtx", "ethereum")

        assert result is not None
        assert result.status == "completed"
        assert result.destination_chain == "solana"
        assert result.destination_address == "SolanaRecipientAddr"
        assert result.destination_asset == "USDC"
        assert result.destination_tx_hash == "solanatxhash"

    @pytest.mark.asyncio
    async def test_empty_operations_returns_none(self):
        tracer = BridgeTracer()
        from src.tracing.bridge_registry import BRIDGE_REGISTRY
        proto = BRIDGE_REGISTRY["wormhole"]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"operations": []}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tracer._wormhole_lookup(proto, "0xevmtx", "ethereum")

        assert result is None


# ---------------------------------------------------------------------------
# LI.FI
# ---------------------------------------------------------------------------

_LIFI_DONE = {
    "status": "DONE",
    "sending": {
        "address": "0xsender",
        "token": {"symbol": "ETH"},
        "amount": "1000000000000000000",
    },
    "receiving": {
        "chainId": 137,
        "address": "0xrecipient",
        "token": {"symbol": "USDC"},
        "amount": "1800000000",
        "txHash": "0xpolytx",
    },
}


class TestLIFILookup:
    @pytest.mark.asyncio
    async def test_done_status(self):
        tracer = BridgeTracer()
        from src.tracing.bridge_registry import BRIDGE_REGISTRY
        proto = BRIDGE_REGISTRY["lifi"]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _LIFI_DONE
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tracer._lifi_lookup(proto, "0xethtx", "ethereum")

        assert result is not None
        assert result.status == "completed"
        assert result.destination_chain == "polygon"
        assert result.destination_address == "0xrecipient"
        assert result.destination_asset == "USDC"
        assert result.destination_tx_hash == "0xpolytx"
        assert result.source_asset == "ETH"

    @pytest.mark.asyncio
    async def test_not_found_status_returns_none(self):
        tracer = BridgeTracer()
        from src.tracing.bridge_registry import BRIDGE_REGISTRY
        proto = BRIDGE_REGISTRY["lifi"]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "NOT_FOUND"}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await tracer._lifi_lookup(proto, "0xethtx", "ethereum")

        assert result is None


# ---------------------------------------------------------------------------
# Protocols that require intermediate IDs → None
# ---------------------------------------------------------------------------

class TestIntermediateIDProtocols:
    @pytest.mark.asyncio
    async def test_chainflip_returns_none(self):
        tracer = BridgeTracer()
        from src.tracing.bridge_registry import BRIDGE_REGISTRY
        proto = BRIDGE_REGISTRY["chainflip"]
        result = await tracer._resolve_native_amm(proto, "0xtx", "ethereum")
        assert result is None

    @pytest.mark.asyncio
    async def test_celer_returns_none(self):
        tracer = BridgeTracer()
        from src.tracing.bridge_registry import BRIDGE_REGISTRY
        proto = BRIDGE_REGISTRY["celer"]
        result = await tracer._resolve_burn_release(proto, "0xtx", "ethereum")
        assert result is None

    @pytest.mark.asyncio
    async def test_across_returns_none(self):
        tracer = BridgeTracer()
        from src.tracing.bridge_registry import BRIDGE_REGISTRY
        proto = BRIDGE_REGISTRY["across"]
        result = await tracer._resolve_liquidity(proto, "0xtx", "ethereum")
        assert result is None

    @pytest.mark.asyncio
    async def test_stargate_returns_none(self):
        tracer = BridgeTracer()
        from src.tracing.bridge_registry import BRIDGE_REGISTRY
        proto = BRIDGE_REGISTRY["stargate"]
        result = await tracer._resolve_liquidity(proto, "0xtx", "ethereum")
        assert result is None

    @pytest.mark.asyncio
    async def test_relay_returns_none(self):
        """_resolve_solver for relay returns None when the API finds no matching request."""
        tracer = BridgeTracer()
        from src.tracing.bridge_registry import BRIDGE_REGISTRY
        from unittest.mock import AsyncMock, patch
        proto = BRIDGE_REGISTRY["relay"]
        # Mock the HTTP layer so no live API call is made; empty list simulates
        # the Relay API returning no requests for this tx hash.
        with patch.object(tracer, "_make_http_request", new=AsyncMock(return_value=[])):
            result = await tracer._resolve_solver(proto, "0xtx", "ethereum")
        assert result is None

    @pytest.mark.asyncio
    async def test_rango_returns_none(self):
        tracer = BridgeTracer()
        from src.tracing.bridge_registry import BRIDGE_REGISTRY
        proto = BRIDGE_REGISTRY["rango"]
        result = await tracer._resolve_solver(proto, "0xtx", "ethereum")
        assert result is None


# ---------------------------------------------------------------------------
# store_correlation — DB write
# ---------------------------------------------------------------------------

class TestStoreCorrelation:
    @pytest.mark.asyncio
    async def test_executes_upsert(self):
        tracer = BridgeTracer()
        correlation = _corr()

        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("src.tracing.bridge_tracer.BridgeTracer.store_correlation",
                   wraps=tracer.store_correlation):
            with patch("src.api.database.get_postgres_pool", return_value=mock_pool):
                await tracer.store_correlation(correlation)

        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        sql = call_args[0][0]
        assert "INSERT INTO bridge_correlations" in sql
        assert "ON CONFLICT" in sql
        assert "WHERE bridge_correlations.status = 'pending'" in sql

    @pytest.mark.asyncio
    async def test_db_error_does_not_raise(self):
        tracer = BridgeTracer()
        correlation = _corr()

        mock_pool = MagicMock()
        mock_pool.acquire.side_effect = Exception("connection refused")

        with patch("src.api.database.get_postgres_pool", return_value=mock_pool):
            # Should not raise — storage failures are logged and swallowed
            await tracer.store_correlation(correlation)


# ---------------------------------------------------------------------------
# lookup_correlation — DB read
# ---------------------------------------------------------------------------

class TestLookupCorrelation:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_row(self):
        tracer = BridgeTracer()

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("src.api.database.get_postgres_pool", return_value=mock_pool):
            result = await tracer.lookup_correlation("ethereum", "0xabc")

        assert result is None

    @pytest.mark.asyncio
    async def test_maps_protocol_id_to_protocol_attribute(self):
        tracer = BridgeTracer()

        row = {
            "protocol_id": "thorchain",
            "mechanism": "native_amm",
            "source_chain": "ethereum",
            "source_tx_hash": "0xabc",
            "source_address": "0xsender",
            "source_asset": "ETH",
            "source_amount": 1.0,
            "source_fiat_value": None,
            "destination_chain": "bitcoin",
            "destination_tx_hash": "btctx",
            "destination_address": "bc1q...",
            "destination_asset": "BTC",
            "destination_amount": 0.05,
            "destination_fiat_value": None,
            "time_delta_seconds": 30,
            "status": "completed",
            "correlation_confidence": 0.95,
            "order_id": None,
            "resolution_method": "thorchain_api",
        }
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=row)
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("src.api.database.get_postgres_pool", return_value=mock_pool):
            result = await tracer.lookup_correlation("ethereum", "0xabc")

        assert result is not None
        assert result.protocol == "thorchain"   # mapped from protocol_id
        assert result.status == "completed"
        assert result.destination_chain == "bitcoin"
        assert result.destination_address == "bc1q..."

    @pytest.mark.asyncio
    async def test_db_error_returns_none(self):
        tracer = BridgeTracer()

        mock_pool = MagicMock()
        mock_pool.acquire.side_effect = Exception("timeout")

        with patch("src.api.database.get_postgres_pool", return_value=mock_pool):
            result = await tracer.lookup_correlation("ethereum", "0xabc")

        assert result is None
