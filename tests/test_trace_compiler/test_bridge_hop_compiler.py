"""
Unit tests for BridgeHopCompiler.

Verifies:
- Registry loads and contract lookup works for known protocols
- lookup_correlation returns None when pg pool is absent
- build_hop_node produces correct pending and completed nodes
- process_row returns None for non-bridge addresses
- process_row returns (nodes, edges) for known bridge contracts
- EVM _build_graph produces bridge_hop nodes for bridge contract rows
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.trace_compiler.bridges.hop_compiler import BridgeHopCompiler
from src.trace_compiler.chains.evm import EVMChainCompiler
from src.trace_compiler.models import ExpandOptions


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

THORCHAIN_ETH = "0xd37bbe5744d730a1d98d8dc97c42f0ca46ad7146"
RANDOM_ADDR    = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
TX_HASH        = "0x" + "ab" * 32


def _opts():
    return ExpandOptions(max_results=10)


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

def test_is_bridge_contract_known_protocol():
    c = BridgeHopCompiler()
    assert c.is_bridge_contract("ethereum", THORCHAIN_ETH) is True


def test_is_bridge_contract_case_insensitive():
    c = BridgeHopCompiler()
    assert c.is_bridge_contract("ethereum", THORCHAIN_ETH.upper()) is True


def test_is_bridge_contract_unknown_address():
    c = BridgeHopCompiler()
    assert c.is_bridge_contract("ethereum", RANDOM_ADDR) is False


def test_get_protocol_returns_protocol():
    c = BridgeHopCompiler()
    p = c.get_protocol("ethereum", THORCHAIN_ETH)
    assert p is not None
    assert p.protocol_id == "thorchain"


def test_get_protocol_unknown_returns_none():
    c = BridgeHopCompiler()
    assert c.get_protocol("ethereum", RANDOM_ADDR) is None


# ---------------------------------------------------------------------------
# lookup_correlation — no pool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lookup_correlation_no_pool_returns_none():
    c = BridgeHopCompiler(postgres_pool=None)
    result = await c.lookup_correlation("ethereum", TX_HASH)
    assert result is None


# ---------------------------------------------------------------------------
# build_hop_node — pending (no correlation)
# ---------------------------------------------------------------------------

def test_build_hop_node_pending():
    c = BridgeHopCompiler()
    protocol = c.get_protocol("ethereum", THORCHAIN_ETH)
    node = c.build_hop_node(
        protocol=protocol,
        correlation=None,
        tx_hash=TX_HASH,
        source_chain="ethereum",
        session_id="sess1",
        branch_id="br1",
        path_id="p1",
        depth=1,
    )
    assert node.node_type == "bridge_hop"
    assert node.bridge_hop_data is not None
    assert node.bridge_hop_data.hop_id == TX_HASH
    assert node.bridge_hop_data.status == "pending"
    assert node.bridge_hop_data.protocol_id == "thorchain"
    assert node.bridge_hop_data.source_chain == "ethereum"
    assert node.activity_summary is not None
    assert node.activity_summary.source_tx_hash == TX_HASH
    assert node.activity_summary.activity_type == "bridge"
    assert "pending" in node.display_label.lower() or "THORChain" in node.display_label


def test_build_hop_node_completed():
    c = BridgeHopCompiler()
    protocol = c.get_protocol("ethereum", THORCHAIN_ETH)
    corr = {
        "status": "completed",
        "destination_chain": "bitcoin",
        "source_asset": "ETH",
        "destination_asset": "BTC",
        "source_amount": 1.5,
        "destination_amount": 0.05,
        "time_delta_seconds": 45,
        "correlation_confidence": 0.99,
        "destination_tx_hash": "0x" + "cd" * 32,
        "order_id": "ord-123",
    }
    node = c.build_hop_node(
        protocol=protocol,
        correlation=corr,
        tx_hash=TX_HASH,
        source_chain="ethereum",
        session_id="sess1",
        branch_id="br1",
        path_id="p1",
        depth=2,
    )
    assert node.bridge_hop_data.status == "completed"
    assert node.bridge_hop_data.hop_id == TX_HASH
    assert node.bridge_hop_data.destination_chain == "bitcoin"
    assert node.bridge_hop_data.source_amount == 1.5
    assert node.bridge_hop_data.destination_amount == 0.05
    assert node.bridge_hop_data.correlation_confidence == 0.99
    assert node.activity_summary is not None
    assert node.activity_summary.destination_tx_hash == "0x" + "cd" * 32
    assert node.activity_summary.order_id == "ord-123"
    assert "next" in node.expandable_directions


# ---------------------------------------------------------------------------
# build_dest_node
# ---------------------------------------------------------------------------

def test_build_dest_node_with_destination():
    c = BridgeHopCompiler()
    corr = {
        "destination_address": "bc1q" + "a" * 38,
        "destination_chain": "bitcoin",
    }
    node = c.build_dest_node(
        correlation=corr,
        session_id="s",
        branch_id="b",
        path_id="p",
        depth=2,
    )
    assert node is not None
    assert node.node_type == "address"
    assert node.chain == "bitcoin"


def test_build_dest_node_missing_address_returns_none():
    c = BridgeHopCompiler()
    node = c.build_dest_node(
        correlation={"destination_chain": "bitcoin"},
        session_id="s",
        branch_id="b",
        path_id="p",
        depth=2,
    )
    assert node is None


# ---------------------------------------------------------------------------
# process_row — non-bridge
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_row_non_bridge_returns_none():
    c = BridgeHopCompiler()
    result = await c.process_row(
        tx_hash=TX_HASH,
        to_address=RANDOM_ADDR,
        source_chain="ethereum",
        seed_node_id="ethereum:address:0xseed",
        session_id="s",
        branch_id="b",
        path_id="p",
        depth=0,
        timestamp=None,
        value_native=1.0,
        value_fiat=None,
        asset_symbol="ETH",
        canonical_asset_id=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_process_row_bridge_no_correlation_returns_pending_node():
    c = BridgeHopCompiler()
    result = await c.process_row(
        tx_hash=TX_HASH,
        to_address=THORCHAIN_ETH,
        source_chain="ethereum",
        seed_node_id="ethereum:address:0xseed",
        session_id="s",
        branch_id="b",
        path_id="p",
        depth=0,
        timestamp="2026-01-01T00:00:00Z",
        value_native=2.0,
        value_fiat=5000.0,
        asset_symbol="ETH",
        canonical_asset_id=None,
    )
    assert result is not None
    nodes, edges = result
    assert len(nodes) >= 1
    hop_node = nodes[0]
    assert hop_node.node_type == "bridge_hop"
    assert hop_node.bridge_hop_data.status == "pending"
    assert hop_node.activity_summary is not None
    assert hop_node.activity_summary.tx_hash == TX_HASH
    # One edge: seed → bridge_hop
    assert len(edges) == 1
    assert edges[0].edge_type == "bridge_source"
    assert edges[0].activity_summary is not None


@pytest.mark.asyncio
async def test_process_row_bridge_completed_returns_dest_node():
    """Completed correlation produces hop node + dest address node + 2 edges."""
    c = BridgeHopCompiler()

    async def _mock_lookup(chain, tx_hash):
        return {
            "status": "completed",
            "destination_chain": "bitcoin",
            "destination_address": "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
            "source_asset": "ETH",
            "destination_asset": "BTC",
            "source_amount": 1.0,
            "destination_amount": 0.03,
            "time_delta_seconds": 30,
            "correlation_confidence": 0.99,
            "destination_tx_hash": "0x" + "ef" * 32,
            "order_id": "bridge-order-1",
        }

    c.lookup_correlation = _mock_lookup

    result = await c.process_row(
        tx_hash=TX_HASH,
        to_address=THORCHAIN_ETH,
        source_chain="ethereum",
        seed_node_id="ethereum:address:0xseed",
        session_id="s",
        branch_id="b",
        path_id="p",
        depth=0,
        timestamp="2026-01-01T00:00:00Z",
        value_native=1.0,
        value_fiat=3000.0,
        asset_symbol="ETH",
        canonical_asset_id=None,
    )
    assert result is not None
    nodes, edges = result
    # hop_node + dest_node
    assert len(nodes) == 2
    node_types = {n.node_type for n in nodes}
    assert "bridge_hop" in node_types
    assert "address" in node_types
    # bridge_source + bridge_dest
    assert len(edges) == 2
    edge_types = {e.edge_type for e in edges}
    assert "bridge_source" in edge_types
    assert "bridge_dest" in edge_types
    hop_node = next(node for node in nodes if node.node_type == "bridge_hop")
    assert hop_node.activity_summary is not None
    assert hop_node.activity_summary.destination_tx_hash == "0x" + "ef" * 32


# ---------------------------------------------------------------------------
# EVM _build_graph integration: bridge contract row → bridge_hop node
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evm_build_graph_bridge_row_produces_bridge_hop_node():
    """EVM _build_graph replaces bridge contract address with bridge_hop node."""
    evm = EVMChainCompiler()
    # Return a pending correlation (no pg pool)
    rows = [
        {
            "counterparty": THORCHAIN_ETH,
            "tx_hash": TX_HASH,
            "value_native": 1.0,
            "asset_symbol": "ETH",
            "canonical_asset_id": None,
            "timestamp": "2026-01-01T00:00:00Z",
        }
    ]
    nodes, edges = await evm._build_graph(
        rows=rows,
        session_id="sess",
        branch_id="br",
        path_sequence=0,
        depth=0,
        seed_address="0xseed",
        chain="ethereum",
        direction="forward",
        options=_opts(),
        prices=None,
    )
    assert len(nodes) == 1
    assert nodes[0].node_type == "bridge_hop"
    assert len(edges) == 1
    assert edges[0].edge_type == "bridge_source"


@pytest.mark.asyncio
async def test_evm_build_graph_non_bridge_row_produces_address_node():
    """EVM _build_graph leaves non-bridge addresses as plain address nodes."""
    evm = EVMChainCompiler()
    rows = [
        {
            "counterparty": RANDOM_ADDR,
            "tx_hash": TX_HASH,
            "value_native": 1.0,
            "asset_symbol": "ETH",
            "canonical_asset_id": None,
            "timestamp": None,
        }
    ]
    nodes, edges = await evm._build_graph(
        rows=rows,
        session_id="sess",
        branch_id="br",
        path_sequence=0,
        depth=0,
        seed_address="0xseed",
        chain="ethereum",
        direction="forward",
        options=_opts(),
        prices=None,
    )
    assert len(nodes) == 1
    assert nodes[0].node_type == "address"
    assert len(edges) == 1
    assert edges[0].edge_type == "transfer"
