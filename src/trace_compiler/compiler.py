"""
TraceCompiler — orchestrates chain-specific compilers to produce
``ExpansionResponse v2`` payloads from the raw event store.

This is the semantic boundary between raw blockchain facts (PostgreSQL event
store, Neo4j canonical graph) and the investigation-view graph served to the
frontend.

Current state (Phase 3): skeleton with stub implementations.
All methods raise ``NotImplementedError`` and will be filled in during
Phase 4 (chain compiler implementations).

Reference: PHASE3_IMPLEMENTATION_SPEC.md Section 5 (Service 2 — Trace
Compiler).
"""

import logging
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict
from typing import Optional

from src.trace_compiler.lineage import new_operation_id
from src.trace_compiler.models import AssetContext
from src.trace_compiler.models import BridgeHopStatusResponse
from src.trace_compiler.models import ChainContext
from src.trace_compiler.models import ExpandRequest
from src.trace_compiler.models import ExpansionResponseV2
from src.trace_compiler.models import InvestigationNode
from src.trace_compiler.models import LayoutHints
from src.trace_compiler.models import PaginationMeta
from src.trace_compiler.models import SessionCreateRequest
from src.trace_compiler.models import SessionCreateResponse

logger = logging.getLogger(__name__)


class TraceCompiler:
    """Orchestrates chain-specific compilers and produces ExpansionResponse v2.

    Instantiated once at application startup and injected via FastAPI
    dependency injection into the graph router.

    Phase 3 status: all public methods are stubs that return minimal valid
    responses.  Chain-specific compilation (UTXO, EVM, Solana, bridge
    resolution) is implemented in Phase 4.

    Args:
        neo4j_driver:   Async Neo4j driver for canonical graph reads/writes.
        postgres_pool:  Async asyncpg pool for event store reads.
        redis_client:   Async Redis client for cache reads/writes.
    """

    def __init__(self, neo4j_driver=None, postgres_pool=None, redis_client=None):
        self._neo4j = neo4j_driver
        self._pg = postgres_pool
        self._redis = redis_client

    async def create_session(
        self, request: SessionCreateRequest
    ) -> SessionCreateResponse:
        """Create a new investigation session and return the root node.

        Phase 3 stub: generates a session ID and a placeholder root node.

        Args:
            request: Session creation parameters (seed address, chain, optional case_id).

        Returns:
            SessionCreateResponse with session_id and root InvestigationNode.
        """
        # TODO Phase 4: persist session to Neo4j InvestigationAnnotation, resolve
        # seed address into a full InvestigationNode from the canonical graph.
        import uuid
        from src.trace_compiler.lineage import branch_id as mk_branch
        from src.trace_compiler.lineage import lineage_id as mk_lineage
        from src.trace_compiler.lineage import node_id as mk_node
        from src.trace_compiler.lineage import path_id as mk_path
        from src.trace_compiler.models import AddressNodeData

        session_id = str(uuid.uuid4())
        _node_id = mk_node(request.seed_chain, "address", request.seed_address)
        _branch = mk_branch(session_id, _node_id, 0)
        _path = mk_path(_branch, 0)
        _lineage = mk_lineage(session_id, _branch, _path, 0)

        root_node = InvestigationNode(
            node_id=_node_id,
            lineage_id=_lineage,
            node_type="address",
            branch_id=_branch,
            path_id=_path,
            depth=0,
            display_label=request.seed_address[:12] + "…",
            chain=request.seed_chain,
            expandable_directions=["prev", "next", "neighbors"],
            address_data=AddressNodeData(
                address=request.seed_address,
                address_type="unknown",
            ),
        )

        return SessionCreateResponse(
            session_id=session_id,
            root_node=root_node,
            created_at=datetime.now(timezone.utc),
        )

    async def expand(
        self,
        session_id: str,
        request: ExpandRequest,
    ) -> ExpansionResponseV2:
        """Expand a node within an investigation session.

        Dispatches to the appropriate chain compiler based on the seed
        node_id prefix (``"{chain}:..."``).

        Phase 3 stub: returns an empty expansion response with correct
        metadata scaffolding.  Chain-specific expansion is implemented in
        Phase 4.

        Args:
            session_id: Investigation session UUID.
            request:    Expansion parameters (operation_type, seed_node_id, options).

        Returns:
            ExpansionResponseV2 with lineage-tagged nodes and edges.
        """
        # TODO Phase 4: dispatch to chain-specific compiler based on seed chain.
        from src.trace_compiler.lineage import branch_id as mk_branch
        from src.trace_compiler.lineage import new_operation_id

        chain = request.seed_node_id.split(":")[0] if ":" in request.seed_node_id else "unknown"
        _branch = mk_branch(session_id, request.seed_node_id, 0)

        logger.debug(
            "TraceCompiler.expand stub: session=%s op=%s seed=%s",
            session_id,
            request.operation_type,
            request.seed_node_id,
        )

        return ExpansionResponseV2(
            operation_id=new_operation_id(),
            operation_type=request.operation_type,
            session_id=session_id,
            seed_node_id=request.seed_node_id,
            seed_lineage_id=request.seed_lineage_id,
            branch_id=_branch,
            expansion_depth=request.options.depth,
            added_nodes=[],
            added_edges=[],
            has_more=False,
            pagination=PaginationMeta(
                page_size=request.options.page_size,
                max_results=request.options.max_results,
            ),
            layout_hints=LayoutHints(),
            chain_context=ChainContext(
                primary_chain=chain,
                chains_present=[chain],
            ),
            asset_context=AssetContext(),
            timestamp=datetime.now(timezone.utc),
        )

    async def get_bridge_hop_status(
        self, session_id: str, hop_id: str
    ) -> BridgeHopStatusResponse:
        """Return the current resolution status of a bridge hop.

        Phase 3 stub: always returns ``status="pending"``.

        Args:
            session_id: Investigation session UUID (for authorization).
            hop_id:     BridgeHop.hop_id value.

        Returns:
            BridgeHopStatusResponse with current status fields.
        """
        # TODO Phase 4: query bridge_correlations table by hop_id.
        return BridgeHopStatusResponse(
            hop_id=hop_id,
            status="pending",
            updated_at=datetime.now(timezone.utc),
        )
