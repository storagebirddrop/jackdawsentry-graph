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
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.trace_compiler.models import (
    AddressNodeData,
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
