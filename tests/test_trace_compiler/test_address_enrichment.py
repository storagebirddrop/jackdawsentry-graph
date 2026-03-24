"""
Unit tests for address enrichment (AddressEnricher).

Covers:
- Entity attribution fields are applied to address nodes
- Risk score is raised by entity risk_level
- Sanctions flag is set and risk floored at 0.95
- Only address-type nodes are touched; swap_event/service/bridge_hop pass through
- Best-effort: service errors are swallowed
- Risk score is never lowered by enrichment
- Multi-chain expansions group correctly
- Mixer taint propagation: address connected to mixer service node gets
  mixer_interaction risk factor and risk_score floored at 0.75
- Sanctioned-counterparty propagation: address connected to sanctioned node
  gets sanctioned_counterparty risk factor and risk_score floored at 0.65
- Mixer service nodes carry risk_score=0.9 and sanctioned flag when applicable
- Contract info: is_contract, deployer, deployment_tx, address_type and
  deployer_entity are applied to address nodes for supported chains
- Contract info: redis_client is forwarded so results are cached
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.trace_compiler.models import (
    AddressNodeData,
    InvestigationEdge,
    InvestigationNode,
    ServiceNodeData,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _addr_node(
    address: str = "0xaaa",
    chain: str = "ethereum",
    risk_score: float = 0.0,
    node_id: str | None = None,
) -> InvestigationNode:
    nid = node_id or f"{chain}:address:{address}"
    return InvestigationNode(
        node_id=nid,
        node_type="address",
        lineage_id="lin",
        branch_id="br",
        path_id="pa",
        depth=1,
        chain=chain,
        display_label=address,
        expandable_directions=["next"],
        risk_score=risk_score,
        address_data=AddressNodeData(address=address, address_type="eoa"),
    )


def _svc_node(address: str = "0xdex") -> InvestigationNode:
    return InvestigationNode(
        node_id=f"ethereum:service:{address}",
        node_type="service",
        lineage_id="lin",
        branch_id="br",
        path_id="pa",
        depth=1,
        chain="ethereum",
        display_label=address,
        expandable_directions=[],
        service_data=ServiceNodeData(
            protocol_id="uniswap_v3",
            service_type="dex",
            known_contracts=[address],
        ),
    )


# ---------------------------------------------------------------------------
# Entity attribution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entity_fields_applied():
    node = _addr_node("0xaaa")
    entity_result = {
        "0xaaa": {
            "entity_name": "Binance",
            "entity_type": "cex",
            "category": "exchange",
            "risk_level": "low",
        }
    }
    with (
        patch(
            "src.api.graph_dependencies.lookup_addresses_bulk",
            new=AsyncMock(return_value=entity_result),
        ),
        patch(
            "src.api.graph_dependencies.screen_address",
            new=AsyncMock(return_value={"matched": False}),
        ),
    ):
        from src.trace_compiler.attribution.enricher import enrich_nodes
        result = await enrich_nodes([node])

    assert result[0].entity_name == "Binance"
    assert result[0].entity_type == "cex"
    assert result[0].entity_category == "exchange"


@pytest.mark.asyncio
async def test_entity_risk_level_raises_score():
    node = _addr_node("0xbbb")
    entity_result = {
        "0xbbb": {"entity_name": "Mixer", "entity_type": "mixer", "risk_level": "high"}
    }
    with (
        patch(
            "src.api.graph_dependencies.lookup_addresses_bulk",
            new=AsyncMock(return_value=entity_result),
        ),
        patch(
            "src.api.graph_dependencies.screen_address",
            new=AsyncMock(return_value={"matched": False}),
        ),
    ):
        from src.trace_compiler.attribution.enricher import enrich_nodes
        result = await enrich_nodes([node])

    assert result[0].risk_score == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_entity_risk_does_not_lower_compiler_score():
    node = _addr_node("0xccc", risk_score=0.85)
    entity_result = {"0xccc": {"risk_level": "medium"}}
    with (
        patch(
            "src.api.graph_dependencies.lookup_addresses_bulk",
            new=AsyncMock(return_value=entity_result),
        ),
        patch(
            "src.api.graph_dependencies.screen_address",
            new=AsyncMock(return_value={"matched": False}),
        ),
    ):
        from src.trace_compiler.attribution.enricher import enrich_nodes
        result = await enrich_nodes([node])

    assert result[0].risk_score == pytest.approx(0.85)  # unchanged


# ---------------------------------------------------------------------------
# Sanctions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sanctioned_flag_set():
    node = _addr_node("0xofac")
    with (
        patch(
            "src.api.graph_dependencies.lookup_addresses_bulk",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "src.api.graph_dependencies.screen_address",
            new=AsyncMock(
                return_value={"matched": True, "list_name": "OFAC-SDN"}
            ),
        ),
    ):
        from src.trace_compiler.attribution.enricher import enrich_nodes
        result = await enrich_nodes([node])

    assert result[0].sanctioned is True
    assert result[0].risk_score >= 0.95
    assert result[0].sanctions_list == "OFAC-SDN"
    assert "sanctions" in result[0].risk_factors


@pytest.mark.asyncio
async def test_sanctions_risk_floored_at_095():
    node = _addr_node("0xofac", risk_score=0.3)
    with (
        patch(
            "src.api.graph_dependencies.lookup_addresses_bulk",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "src.api.graph_dependencies.screen_address",
            new=AsyncMock(return_value={"matched": True}),
        ),
    ):
        from src.trace_compiler.attribution.enricher import enrich_nodes
        result = await enrich_nodes([node])

    assert result[0].risk_score >= 0.95


# ---------------------------------------------------------------------------
# Non-address nodes pass through unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_node_not_enriched():
    svc = _svc_node()
    with (
        patch(
            "src.api.graph_dependencies.lookup_addresses_bulk",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "src.api.graph_dependencies.screen_address",
            new=AsyncMock(return_value={"matched": False}),
        ),
    ):
        from src.trace_compiler.attribution.enricher import enrich_nodes
        result = await enrich_nodes([svc])

    assert result[0] is svc  # reference identity preserved — no model_copy called


# ---------------------------------------------------------------------------
# Best-effort: service errors swallowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entity_lookup_error_swallowed():
    node = _addr_node("0xerr")
    with (
        patch(
            "src.api.graph_dependencies.lookup_addresses_bulk",
            new=AsyncMock(side_effect=Exception("entity service down")),
        ),
        patch(
            "src.api.graph_dependencies.screen_address",
            new=AsyncMock(return_value={"matched": False}),
        ),
    ):
        from src.trace_compiler.attribution.enricher import enrich_nodes
        result = await enrich_nodes([node])

    # Should not raise; node returned as-is.
    assert result[0].entity_name is None
    assert result[0].risk_score == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_sanctions_screen_error_swallowed():
    node = _addr_node("0xerr2")
    with (
        patch(
            "src.api.graph_dependencies.lookup_addresses_bulk",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "src.api.graph_dependencies.screen_address",
            new=AsyncMock(side_effect=Exception("sanctions service down")),
        ),
    ):
        from src.trace_compiler.attribution.enricher import enrich_nodes
        result = await enrich_nodes([node])

    assert result[0].sanctioned is False


# ---------------------------------------------------------------------------
# Multi-chain grouping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multichain_nodes_enriched_correctly():
    eth_node = _addr_node("0xeth", chain="ethereum")
    sol_node = _addr_node("SolAddr", chain="solana")

    lookup_calls = []

    async def mock_lookup(addresses, chain):
        lookup_calls.append((chain, addresses))
        if chain == "ethereum":
            return {"0xeth": {"entity_name": "EthEntity", "risk_level": "low"}}
        return {}

    with (
        patch(
            "src.api.graph_dependencies.lookup_addresses_bulk",
            new=mock_lookup,
        ),
        patch(
            "src.api.graph_dependencies.screen_address",
            new=AsyncMock(return_value={"matched": False}),
        ),
    ):
        from src.trace_compiler.attribution.enricher import enrich_nodes
        result = await enrich_nodes([eth_node, sol_node])

    chains_called = {c for c, _ in lookup_calls}
    assert "ethereum" in chains_called
    assert "solana" in chains_called

    eth_result = next(n for n in result if n.chain == "ethereum")
    sol_result = next(n for n in result if n.chain == "solana")
    assert eth_result.entity_name == "EthEntity"
    assert sol_result.entity_name is None


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_list_returns_empty():
    from src.trace_compiler.attribution.enricher import enrich_nodes
    result = await enrich_nodes([])
    assert result == []


# ---------------------------------------------------------------------------
# Taint propagation helpers
# ---------------------------------------------------------------------------


def _mixer_svc_node(node_id: str = "ethereum:service:tc") -> InvestigationNode:
    """Mixer service node with risk signals already set (as build_service_node does)."""
    return InvestigationNode(
        node_id=node_id,
        node_type="service",
        lineage_id="lin",
        branch_id="br",
        path_id="pa",
        depth=1,
        chain="ethereum",
        display_label="Tornado Cash",
        expandable_directions=[],
        risk_score=1.0,
        risk_factors=["mixer", "sanctions"],
        sanctioned=True,
        service_data=ServiceNodeData(
            protocol_id="tornado_cash",
            service_type="mixer",
            known_contracts=["0xpool"],
        ),
    )


def _edge(src: str, tgt: str) -> InvestigationEdge:
    return InvestigationEdge(
        edge_id=f"{src}->{tgt}",
        source_node_id=src,
        target_node_id=tgt,
        branch_id="br",
        path_id="pa",
        edge_type="transfer",
        direction="forward",
    )


# ---------------------------------------------------------------------------
# Mixer taint propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mixer_taint_propagates_to_connected_address():
    """Address connected to a mixer service node gets mixer_interaction risk."""
    wallet = _addr_node("0xwallet", node_id="ethereum:address:0xwallet")
    mixer = _mixer_svc_node()
    edge = _edge("ethereum:address:0xwallet", "ethereum:service:tc")

    with (
        patch("src.api.graph_dependencies.lookup_addresses_bulk", new=AsyncMock(return_value={})),
        patch("src.api.graph_dependencies.screen_address", new=AsyncMock(return_value={"matched": False})),
    ):
        from src.trace_compiler.attribution.enricher import enrich_nodes
        result = await enrich_nodes([wallet, mixer], edges=[edge])

    wallet_result = next(n for n in result if n.node_id == "ethereum:address:0xwallet")
    assert "mixer_interaction" in wallet_result.risk_factors
    assert wallet_result.risk_score >= 0.75


@pytest.mark.asyncio
async def test_mixer_taint_applies_regardless_of_edge_direction():
    """Taint propagates even when the mixer is the edge source (withdrawal)."""
    wallet = _addr_node("0xrecv", node_id="ethereum:address:0xrecv")
    mixer = _mixer_svc_node()
    # Edge goes mixer → wallet (withdrawal direction)
    edge = _edge("ethereum:service:tc", "ethereum:address:0xrecv")

    with (
        patch("src.api.graph_dependencies.lookup_addresses_bulk", new=AsyncMock(return_value={})),
        patch("src.api.graph_dependencies.screen_address", new=AsyncMock(return_value={"matched": False})),
    ):
        from src.trace_compiler.attribution.enricher import enrich_nodes
        result = await enrich_nodes([wallet, mixer], edges=[edge])

    wallet_result = next(n for n in result if n.node_id == "ethereum:address:0xrecv")
    assert "mixer_interaction" in wallet_result.risk_factors


@pytest.mark.asyncio
async def test_no_taint_without_edges():
    """Without edges, no taint propagation occurs (backwards compatible)."""
    wallet = _addr_node("0xclean", node_id="ethereum:address:0xclean")
    mixer = _mixer_svc_node()

    with (
        patch("src.api.graph_dependencies.lookup_addresses_bulk", new=AsyncMock(return_value={})),
        patch("src.api.graph_dependencies.screen_address", new=AsyncMock(return_value={"matched": False})),
    ):
        from src.trace_compiler.attribution.enricher import enrich_nodes
        result = await enrich_nodes([wallet, mixer])  # no edges kwarg

    wallet_result = next(n for n in result if n.node_id == "ethereum:address:0xclean")
    assert "mixer_interaction" not in wallet_result.risk_factors
    assert wallet_result.risk_score == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Sanctioned-counterparty taint propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sanctioned_address_taints_counterparty():
    """Address connected to a sanctioned address gets sanctioned_counterparty risk."""
    clean = _addr_node("0xclean", node_id="ethereum:address:0xclean")
    bad = _addr_node("0xbad", node_id="ethereum:address:0xbad")
    edge = _edge("ethereum:address:0xbad", "ethereum:address:0xclean")

    async def mock_screen(addr, chain):
        return {"matched": True, "list_name": "OFAC SDN"} if addr == "0xbad" else {"matched": False}

    with (
        patch("src.api.graph_dependencies.lookup_addresses_bulk", new=AsyncMock(return_value={})),
        patch("src.api.graph_dependencies.screen_address", new=AsyncMock(side_effect=mock_screen)),
    ):
        from src.trace_compiler.attribution.enricher import enrich_nodes
        result = await enrich_nodes([clean, bad], edges=[edge])

    clean_result = next(n for n in result if n.node_id == "ethereum:address:0xclean")
    bad_result = next(n for n in result if n.node_id == "ethereum:address:0xbad")

    assert bad_result.sanctioned is True
    assert "sanctioned_counterparty" in clean_result.risk_factors
    assert clean_result.risk_score >= 0.65


@pytest.mark.asyncio
async def test_sanctioned_service_node_taints_counterparty():
    """Address connected to a sanctioned service node also gets the taint."""
    wallet = _addr_node("0xsender", node_id="ethereum:address:0xsender")
    mixer = _mixer_svc_node()  # sanctioned=True
    edge = _edge("ethereum:address:0xsender", "ethereum:service:tc")

    with (
        patch("src.api.graph_dependencies.lookup_addresses_bulk", new=AsyncMock(return_value={})),
        patch("src.api.graph_dependencies.screen_address", new=AsyncMock(return_value={"matched": False})),
    ):
        from src.trace_compiler.attribution.enricher import enrich_nodes
        result = await enrich_nodes([wallet, mixer], edges=[edge])

    wallet_result = next(n for n in result if n.node_id == "ethereum:address:0xsender")
    # Gets both mixer_interaction (from service_type=mixer) AND
    # sanctioned_counterparty (from sanctioned=True) signals.
    assert "mixer_interaction" in wallet_result.risk_factors
    assert "sanctioned_counterparty" in wallet_result.risk_factors
    assert wallet_result.risk_score >= 0.75


# ---------------------------------------------------------------------------
# Mixer service node risk signals (set at build time, not enricher)
# ---------------------------------------------------------------------------


def test_mixer_service_node_risk_signals():
    """Mixer service node carries risk_score >= 0.9 and mixer risk factor."""
    mixer = _mixer_svc_node()
    assert mixer.risk_score >= 0.9
    assert "mixer" in mixer.risk_factors


def test_sanctioned_service_node_flags():
    """Sanctioned mixer service node has sanctioned=True and risk_score=1.0."""
    mixer = _mixer_svc_node()
    assert mixer.sanctioned is True
    assert mixer.risk_score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Contract deployer / creator enrichment
# ---------------------------------------------------------------------------


class _FakeContractInfo:
    """Minimal stand-in for ContractInfo dataclass."""

    def __init__(self, is_contract, deployer=None, deployment_tx=None, upgrade_authority=None):
        self.is_contract = is_contract
        self.deployer = deployer
        self.deployment_tx = deployment_tx
        self.upgrade_authority = upgrade_authority


@pytest.mark.asyncio
async def test_contract_fields_applied_to_address_data():
    """EVM contract: is_contract, deployer, deployment_tx, and address_type are set."""
    node = _addr_node("0xcontract", chain="ethereum")

    contract_info = _FakeContractInfo(
        is_contract=True,
        deployer="0xdeployer",
        deployment_tx="0xtxhash",
    )

    with (
        patch(
            "src.api.graph_dependencies.lookup_addresses_bulk",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "src.api.graph_dependencies.screen_address",
            new=AsyncMock(return_value={"matched": False}),
        ),
        patch(
            "src.api.graph_dependencies.get_contract_info",
            new=AsyncMock(return_value=contract_info),
        ),
    ):
        from src.trace_compiler.attribution.enricher import enrich_nodes
        result = await enrich_nodes([node])

    addr_data = result[0].address_data
    assert addr_data is not None
    assert addr_data.is_contract is True
    assert addr_data.deployer == "0xdeployer"
    assert addr_data.deployment_tx == "0xtxhash"
    assert addr_data.address_type == "contract"


@pytest.mark.asyncio
async def test_solana_program_address_type_set_to_program():
    """Solana executable program: address_type is set to 'program'."""
    node = _addr_node("ProgramAddr1", chain="solana")

    contract_info = _FakeContractInfo(
        is_contract=True,
        deployer="AuthAddr111",
        upgrade_authority="AuthAddr111",
    )

    with (
        patch(
            "src.api.graph_dependencies.lookup_addresses_bulk",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "src.api.graph_dependencies.screen_address",
            new=AsyncMock(return_value={"matched": False}),
        ),
        patch(
            "src.api.graph_dependencies.get_contract_info",
            new=AsyncMock(return_value=contract_info),
        ),
    ):
        from src.trace_compiler.attribution.enricher import enrich_nodes
        result = await enrich_nodes([node])

    addr_data = result[0].address_data
    assert addr_data is not None
    assert addr_data.address_type == "program"
    assert addr_data.upgrade_authority == "AuthAddr111"


@pytest.mark.asyncio
async def test_deployer_entity_resolved():
    """When the deployer address has a known entity, deployer_entity is populated."""
    node = _addr_node("0xcontract", chain="ethereum")

    contract_info = _FakeContractInfo(
        is_contract=True,
        deployer="0xdeployer",
        deployment_tx="0xtx",
    )

    def _entity_side_effect(addresses, chain):
        if "0xdeployer" in addresses:
            return {"0xdeployer": {"entity_name": "Binance Hot Wallet", "risk_level": "low"}}
        return {}

    with (
        patch(
            "src.api.graph_dependencies.lookup_addresses_bulk",
            new=AsyncMock(side_effect=_entity_side_effect),
        ),
        patch(
            "src.api.graph_dependencies.screen_address",
            new=AsyncMock(return_value={"matched": False}),
        ),
        patch(
            "src.api.graph_dependencies.get_contract_info",
            new=AsyncMock(return_value=contract_info),
        ),
    ):
        from src.trace_compiler.attribution.enricher import enrich_nodes
        result = await enrich_nodes([node])

    assert result[0].address_data is not None
    assert result[0].address_data.deployer_entity == "Binance Hot Wallet"


@pytest.mark.asyncio
async def test_eoa_leaves_address_data_unchanged():
    """Non-contract address: address_data fields remain at defaults."""
    node = _addr_node("0xeoa", chain="ethereum")

    with (
        patch(
            "src.api.graph_dependencies.lookup_addresses_bulk",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "src.api.graph_dependencies.screen_address",
            new=AsyncMock(return_value={"matched": False}),
        ),
        patch(
            "src.api.graph_dependencies.get_contract_info",
            new=AsyncMock(return_value=_FakeContractInfo(is_contract=False)),
        ),
    ):
        from src.trace_compiler.attribution.enricher import enrich_nodes
        result = await enrich_nodes([node])

    addr_data = result[0].address_data
    assert addr_data is not None
    assert addr_data.is_contract is False
    assert addr_data.deployer is None
    assert addr_data.address_type == "eoa"


@pytest.mark.asyncio
async def test_redis_client_forwarded_to_contract_info():
    """The redis_client kwarg passed to enrich_nodes is forwarded to get_contract_info."""
    node = _addr_node("0xcontract", chain="ethereum")
    mock_redis = AsyncMock()

    captured_kwargs = {}

    async def _capture_contract_info(addr, chain, *, redis_client=None):
        captured_kwargs["redis_client"] = redis_client
        return _FakeContractInfo(is_contract=False)

    with (
        patch(
            "src.api.graph_dependencies.lookup_addresses_bulk",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "src.api.graph_dependencies.screen_address",
            new=AsyncMock(return_value={"matched": False}),
        ),
        patch(
            "src.api.graph_dependencies.get_contract_info",
            new=_capture_contract_info,
        ),
    ):
        from src.trace_compiler.attribution.enricher import enrich_nodes
        await enrich_nodes([node], redis_client=mock_redis)

    assert captured_kwargs.get("redis_client") is mock_redis
