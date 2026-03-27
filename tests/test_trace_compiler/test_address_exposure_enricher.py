"""Unit tests for address-level exposure enrichment."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from src.trace_compiler.compiler import TraceCompiler
from src.trace_compiler.lineage import lineage_id as mk_lineage_id
from src.trace_compiler.models import AddressNodeData
from src.trace_compiler.models import InvestigationNode
from src.trace_compiler.models import SessionCreateRequest
from src.trace_compiler.services.address_exposure import AddressExposureEnricher

_TEST_ADDRESS = "0x4aAd0e899DFCDca8c958ecf53455B2383c33F31B"
_TORNADO_ROUTER = "0xd90e2f925da726b50c4ed8d0fb90ad053324f31b"


class _Ctx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_):
        return None


def _pg_pool(counterparties=None):
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=counterparties or [])
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Ctx(conn))
    pool._conn = conn
    return pool


@pytest.mark.asyncio
async def test_lookup_exposure_flags_direct_tornado_router_interaction():
    pg = _pg_pool(
        counterparties=[
            {"counterparty": _TORNADO_ROUTER, "interaction_count": 4},
        ]
    )
    enricher = AddressExposureEnricher(postgres_pool=pg)

    result = await enricher.lookup_exposure(_TEST_ADDRESS, "ethereum")

    assert result is not None
    assert result["entity_name"] == "Tornado Cash-associated address"
    assert result["entity_category"] == "mixer"
    assert result["is_mixer"] is True
    assert result["risk_score"] >= 0.9
    assert result["matched_contract"] == _TORNADO_ROUTER


@pytest.mark.asyncio
async def test_enrich_address_node_sets_mixer_metadata():
    pg = _pg_pool(
        counterparties=[
            {"counterparty": _TORNADO_ROUTER, "interaction_count": 4},
        ]
    )
    enricher = AddressExposureEnricher(postgres_pool=pg)
    node = InvestigationNode(
        node_id=f"ethereum:address:{_TEST_ADDRESS.lower()}",
        lineage_id=mk_lineage_id("session", "branch", "path", 0),
        node_type="address",
        branch_id="branch",
        path_id="path",
        depth=0,
        display_label=_TEST_ADDRESS,
        chain="ethereum",
        expandable_directions=["prev", "next", "neighbors"],
        address_data=AddressNodeData(
            address=_TEST_ADDRESS,
            chain="ethereum",
            address_type="unknown",
        ),
    )

    enriched = await enricher.enrich_address_node(node)

    assert enriched.entity_name == "Tornado Cash-associated address"
    assert enriched.entity_category == "mixer"
    assert enriched.risk_score >= 0.9
    assert enriched.address_data is not None
    assert enriched.address_data.entity_name == "Tornado Cash-associated address"
    assert enriched.address_data.is_mixer is True
    assert enriched.address_data.label == "Tornado Cash exposure"


@pytest.mark.asyncio
async def test_create_session_enriches_root_node_for_direct_mixer_exposure():
    pg = _pg_pool(
        counterparties=[
            {"counterparty": _TORNADO_ROUTER, "interaction_count": 4},
        ]
    )
    compiler = TraceCompiler(postgres_pool=pg)

    resp = await compiler.create_session(
        SessionCreateRequest(seed_address=_TEST_ADDRESS, seed_chain="ethereum")
    )

    assert resp.root_node.entity_name == "Tornado Cash-associated address"
    assert resp.root_node.entity_category == "mixer"
    assert resp.root_node.risk_score >= 0.9
    assert resp.root_node.address_data is not None
    assert resp.root_node.address_data.is_mixer is True
