"""
Unit tests for ServiceClassifier.

Verifies:
- Known service contracts are correctly identified (Uniswap, Tornado Cash, etc.)
- Bridge contract exclusion: bridge addresses are NOT classified as services
- process_row returns None for unknown addresses
- process_row returns transaction-centric service activity nodes + edges
- Service interactions are emitted per transaction hash in EVM _build_graph
- Both forward (service_deposit) and backward (service_receipt) edge types
"""

import pytest

from src.trace_compiler.services.service_classifier import ServiceClassifier
from src.trace_compiler.chains.evm import EVMChainCompiler
from src.trace_compiler.models import ExpandOptions

# Well-known contract fixtures
UNISWAP_V3_ROUTER = "0xe592427a0aece92de3edee1f18e0157c05861564"
TORNADO_10ETH     = "0x910cbd523d972eb0a6f4cae4618ad62622b39dbf"
THORCHAIN_ETH     = "0xd37bbe5744d730a1d98d8dc97c42f0ca46ad7146"  # bridge — must be excluded
RANDOM_ADDR       = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
TX_HASH           = "0x" + "ab" * 32
SEED              = "0x" + "cc" * 20
SEED_NODE_ID      = f"ethereum:address:{SEED}"


def _opts():
    return ExpandOptions(max_results=10)


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

def test_uniswap_v3_detected():
    c = ServiceClassifier()
    assert c.is_service_contract("ethereum", UNISWAP_V3_ROUTER) is True


def test_uniswap_v3_case_insensitive():
    c = ServiceClassifier()
    assert c.is_service_contract("ethereum", UNISWAP_V3_ROUTER.upper()) is True


def test_tornado_cash_detected():
    c = ServiceClassifier()
    assert c.is_service_contract("ethereum", TORNADO_10ETH) is True


def test_tornado_cash_service_type():
    c = ServiceClassifier()
    r = c.get_record("ethereum", TORNADO_10ETH)
    assert r is not None
    assert r.service_type == "mixer"


def test_uniswap_service_type():
    c = ServiceClassifier()
    r = c.get_record("ethereum", UNISWAP_V3_ROUTER)
    assert r is not None
    assert r.service_type == "dex"


def test_bridge_contract_excluded():
    """THORChain contract must NOT appear in the service registry."""
    c = ServiceClassifier()
    assert c.is_service_contract("ethereum", THORCHAIN_ETH) is False


def test_unknown_address_returns_none():
    c = ServiceClassifier()
    assert c.get_record("ethereum", RANDOM_ADDR) is None


def test_wrong_chain_returns_none():
    """Uniswap V3 router on ethereum should not match on bitcoin."""
    c = ServiceClassifier()
    assert c.is_service_contract("bitcoin", UNISWAP_V3_ROUTER) is False


# ---------------------------------------------------------------------------
# build_service_node
# ---------------------------------------------------------------------------

def test_build_service_node_type():
    c = ServiceClassifier()
    record = c.get_record("ethereum", UNISWAP_V3_ROUTER)
    node = c.build_service_node(
        record=record,
        contract_address=UNISWAP_V3_ROUTER,
        chain="ethereum",
        tx_hash=TX_HASH,
        session_id="s",
        branch_id="b",
        path_id="p",
        depth=1,
    )
    assert node.node_type == "service"
    assert node.service_data is not None
    assert node.service_data.protocol_id == "uniswap_v3"
    assert node.service_data.service_type == "dex"
    assert UNISWAP_V3_ROUTER in node.service_data.known_contracts
    assert node.activity_summary is not None
    assert node.activity_summary.tx_hash == TX_HASH
    assert node.activity_summary.protocol_id == "uniswap_v3"


def test_build_service_node_label():
    c = ServiceClassifier()
    record = c.get_record("ethereum", TORNADO_10ETH)
    node = c.build_service_node(
        record=record,
        contract_address=TORNADO_10ETH,
        chain="ethereum",
        tx_hash=TX_HASH,
        session_id="s",
        branch_id="b",
        path_id="p",
        depth=2,
    )
    assert "Tornado" in node.display_label
    assert node.display_sublabel.startswith("MIXER")


# ---------------------------------------------------------------------------
# process_row
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_row_unknown_returns_none():
    c = ServiceClassifier()
    result = await c.process_row(
        tx_hash=TX_HASH,
        to_address=RANDOM_ADDR,
        chain="ethereum",
        seed_node_id=SEED_NODE_ID,
        session_id="s",
        branch_id="b",
        path_id="p",
        depth=0,
        timestamp=None,
        value_native=1.0,
        value_fiat=None,
        asset_symbol="ETH",
        canonical_asset_id=None,
        direction="forward",
    )
    assert result is None


@pytest.mark.asyncio
async def test_process_row_known_service_forward():
    c = ServiceClassifier()
    result = await c.process_row(
        tx_hash=TX_HASH,
        to_address=UNISWAP_V3_ROUTER,
        chain="ethereum",
        seed_node_id=SEED_NODE_ID,
        session_id="s",
        branch_id="b",
        path_id="p",
        depth=0,
        timestamp="2026-01-01T00:00:00Z",
        value_native=1.0,
        value_fiat=3000.0,
        asset_symbol="ETH",
        canonical_asset_id=None,
        direction="forward",
    )
    assert result is not None
    nodes, edges = result
    assert len(nodes) == 1
    assert nodes[0].node_type == "service"
    assert nodes[0].activity_summary is not None
    assert nodes[0].activity_summary.activity_type == "dex_interaction"
    assert len(edges) == 1
    assert edges[0].edge_type == "service_deposit"
    assert edges[0].source_node_id == SEED_NODE_ID
    assert edges[0].target_node_id == nodes[0].node_id


@pytest.mark.asyncio
async def test_process_row_known_service_backward():
    """Inbound direction: service → seed, edge_type=service_receipt."""
    c = ServiceClassifier()
    result = await c.process_row(
        tx_hash=TX_HASH,
        to_address=UNISWAP_V3_ROUTER,
        chain="ethereum",
        seed_node_id=SEED_NODE_ID,
        session_id="s",
        branch_id="b",
        path_id="p",
        depth=0,
        timestamp=None,
        value_native=0.5,
        value_fiat=None,
        asset_symbol="ETH",
        canonical_asset_id=None,
        direction="backward",
    )
    assert result is not None
    nodes, edges = result
    assert edges[0].edge_type == "service_receipt"
    assert edges[0].source_node_id == nodes[0].node_id  # service → seed
    assert edges[0].target_node_id == SEED_NODE_ID
    assert edges[0].activity_summary is not None
    assert edges[0].activity_summary.direction == "backward"


# ---------------------------------------------------------------------------
# EVM _build_graph integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evm_build_graph_service_row_produces_service_node():
    """EVM _build_graph replaces known service address with service node."""
    evm = EVMChainCompiler()
    rows = [
        {
            "counterparty": UNISWAP_V3_ROUTER,
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
        seed_address=SEED,
        chain="ethereum",
        direction="forward",
        options=_opts(),
        prices=None,
    )
    assert len(nodes) == 1
    assert nodes[0].node_type == "service"
    assert nodes[0].service_data.service_type == "dex"
    assert len(edges) == 1
    assert edges[0].edge_type == "service_deposit"


@pytest.mark.asyncio
async def test_evm_build_graph_service_nodes_are_transaction_specific():
    """Multiple transfers to the same protocol produce one service node per tx."""
    evm = EVMChainCompiler()
    # Two transfers to the same Uniswap V3 contract
    rows = [
        {
            "counterparty": UNISWAP_V3_ROUTER,
            "tx_hash": "0x" + "a1" * 32,
            "value_native": 1.0,
            "asset_symbol": "ETH",
            "canonical_asset_id": None,
            "timestamp": None,
        },
        {
            "counterparty": UNISWAP_V3_ROUTER,
            "tx_hash": "0x" + "b2" * 32,
            "value_native": 2.0,
            "asset_symbol": "ETH",
            "canonical_asset_id": None,
            "timestamp": None,
        },
    ]
    nodes, edges = await evm._build_graph(
        rows=rows,
        session_id="sess",
        branch_id="br",
        path_sequence=0,
        depth=0,
        seed_address=SEED,
        chain="ethereum",
        direction="forward",
        options=_opts(),
        prices=None,
    )
    assert len(nodes) == 2
    assert all(node.node_type == "service" for node in nodes)
    assert nodes[0].node_id != nodes[1].node_id
    assert len(edges) == 2


@pytest.mark.asyncio
async def test_evm_build_graph_bridge_takes_priority_over_service():
    """Bridge contract addresses are handled by BridgeHopCompiler, not ServiceClassifier."""
    evm = EVMChainCompiler()
    rows = [
        {
            "counterparty": THORCHAIN_ETH,
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
        seed_address=SEED,
        chain="ethereum",
        direction="forward",
        options=_opts(),
        prices=None,
    )
    assert len(nodes) == 1
    # Must be bridge_hop, not service
    assert nodes[0].node_type == "bridge_hop"
    assert edges[0].edge_type == "bridge_source"
