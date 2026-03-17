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
from typing import List
from typing import Optional

from src.trace_compiler.chains.bitcoin import UTXOChainCompiler
from src.trace_compiler.chains.evm import EVMChainCompiler
from src.trace_compiler.lineage import new_operation_id
from src.trace_compiler.models import AssetContext
from src.trace_compiler.models import BridgeHopStatusResponse
from src.trace_compiler.models import ChainContext
from src.trace_compiler.models import ExpandRequest
from src.trace_compiler.models import ExpansionResponseV2
from src.trace_compiler.models import InvestigationEdge
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

        # Chain compiler registry: keyed by chain name.
        _evm = EVMChainCompiler(postgres_pool, neo4j_driver, redis_client)
        _btc = UTXOChainCompiler(postgres_pool, neo4j_driver, redis_client)
        self._chain_compilers: Dict[str, Any] = {
            chain: _evm for chain in _evm.supported_chains
        }
        self._chain_compilers.update(
            {chain: _btc for chain in _btc.supported_chains}
        )

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
        from src.trace_compiler.lineage import branch_id as mk_branch

        # Derive chain from the canonical node_id format: "{chain}:{type}:{id}".
        parts = request.seed_node_id.split(":", 2)
        chain = parts[0] if parts else "unknown"
        node_type = parts[1] if len(parts) > 1 else "address"
        identifier = parts[2] if len(parts) > 2 else request.seed_node_id

        _branch = mk_branch(session_id, request.seed_node_id, 0)

        added_nodes: List[InvestigationNode] = []
        added_edges: List[InvestigationEdge] = []

        compiler = self._chain_compilers.get(chain)
        if compiler is not None and node_type == "address":
            try:
                op = request.operation_type
                if op == "expand_next":
                    added_nodes, added_edges = await compiler.expand_next(
                        session_id=session_id,
                        branch_id=_branch,
                        path_sequence=0,
                        depth=0,
                        seed_address=identifier,
                        chain=chain,
                        options=request.options,
                    )
                elif op in ("expand_prev", "expand_previous"):
                    added_nodes, added_edges = await compiler.expand_prev(
                        session_id=session_id,
                        branch_id=_branch,
                        path_sequence=0,
                        depth=0,
                        seed_address=identifier,
                        chain=chain,
                        options=request.options,
                    )
                elif op == "expand_neighbors":
                    fwd_n, fwd_e = await compiler.expand_next(
                        session_id=session_id,
                        branch_id=_branch,
                        path_sequence=0,
                        depth=0,
                        seed_address=identifier,
                        chain=chain,
                        options=request.options,
                    )
                    bwd_n, bwd_e = await compiler.expand_prev(
                        session_id=session_id,
                        branch_id=_branch,
                        path_sequence=1,
                        depth=0,
                        seed_address=identifier,
                        chain=chain,
                        options=request.options,
                    )
                    # Deduplicate nodes by node_id.
                    seen = {n.node_id for n in fwd_n}
                    added_nodes = fwd_n + [n for n in bwd_n if n.node_id not in seen]
                    added_edges = fwd_e + bwd_e
                else:
                    logger.debug(
                        "TraceCompiler.expand: unhandled op=%s (no chain compiler for it)",
                        op,
                    )
            except Exception as exc:
                logger.warning(
                    "TraceCompiler.expand chain compiler failed (chain=%s op=%s): %s",
                    chain,
                    request.operation_type,
                    exc,
                )
        else:
            logger.debug(
                "TraceCompiler.expand: no compiler for chain=%s node_type=%s",
                chain,
                node_type,
            )

        has_more = len(added_nodes) >= request.options.max_results
        unique_chains = list({n.chain for n in added_nodes} | {chain})
        unique_assets = list(
            {e.asset_symbol for e in added_edges if e.asset_symbol}
        )

        return ExpansionResponseV2(
            operation_id=new_operation_id(),
            operation_type=request.operation_type,
            session_id=session_id,
            seed_node_id=request.seed_node_id,
            seed_lineage_id=request.seed_lineage_id,
            branch_id=_branch,
            expansion_depth=request.options.depth,
            added_nodes=added_nodes,
            added_edges=added_edges,
            has_more=has_more,
            pagination=PaginationMeta(
                page_size=request.options.page_size,
                max_results=request.options.max_results,
                has_more=has_more,
            ),
            layout_hints=LayoutHints(
                suggested_layout="layered",
                anchor_node_ids=[request.seed_node_id],
                new_branch_root_id=added_nodes[0].node_id if added_nodes else None,
            ),
            chain_context=ChainContext(
                primary_chain=chain,
                chains_present=unique_chains,
            ),
            asset_context=AssetContext(
                assets_present=unique_assets,
            ),
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
