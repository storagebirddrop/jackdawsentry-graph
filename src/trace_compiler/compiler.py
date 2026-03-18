"""
TraceCompiler — orchestrates chain-specific compilers to produce
``ExpansionResponse v2`` payloads from the raw event store.

This is the semantic boundary between raw blockchain facts (PostgreSQL event
store, Neo4j canonical graph) and the investigation-view graph served to the
frontend.

Current state (Phase 4): EVM and UTXO chain compilers are implemented.
Session creation and expansion are fully wired; bridge hop status polling
is supported via the PostgreSQL ``bridge_correlations`` table.

Reference: PHASE3_IMPLEMENTATION_SPEC.md Section 5 (Service 2 — Trace
Compiler).
"""

import hashlib
import json
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

_EXPANSION_CACHE_TTL = 900  # 15 minutes


def _expansion_cache_key(
    session_id: str,
    seed_node_id: str,
    operation_type: str,
    max_results: Optional[int],
) -> str:
    """Deterministic Redis cache key for an expansion result.

    Keyed on session + node + operation + result-size so different page sizes
    don't collide.  SHA-256 keeps the key short and safe for Redis.
    """
    raw = f"expand:{session_id}:{seed_node_id}:{operation_type}:{max_results}"
    return "tc:" + hashlib.sha256(raw.encode()).hexdigest()


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
        """Create a new investigation session and persist it to PostgreSQL.

        Generates a stable session_id, builds the seed root node, and writes
        a row to ``graph_sessions`` so the session survives a browser refresh.
        Persistence failures are swallowed — the session is still returned to
        the caller.

        Args:
            request: Session creation parameters (seed address, chain, optional case_id).

        Returns:
            SessionCreateResponse with session_id and root InvestigationNode.
        """
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

        label = request.seed_address
        display_label = label[:12] + "\u2026" if len(label) > 12 else label

        root_node = InvestigationNode(
            node_id=_node_id,
            lineage_id=_lineage,
            node_type="address",
            branch_id=_branch,
            path_id=_path,
            depth=0,
            display_label=display_label,
            chain=request.seed_chain,
            expandable_directions=["prev", "next", "neighbors"],
            address_data=AddressNodeData(
                address=request.seed_address,
                address_type="unknown",
            ),
        )

        created_at = datetime.now(timezone.utc)

        # Persist session to PostgreSQL so it survives browser refresh.
        if self._pg is not None:
            try:
                async with self._pg.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO graph_sessions
                            (session_id, seed_address, seed_chain, case_id, created_at, updated_at)
                        VALUES ($1, $2, $3, $4, $5, $5)
                        ON CONFLICT (session_id) DO NOTHING
                        """,
                        uuid.UUID(session_id),
                        request.seed_address,
                        request.seed_chain,
                        getattr(request, "case_id", None),
                        created_at,
                    )
                logger.debug("graph_sessions: persisted session %s", session_id)
            except Exception as exc:
                logger.warning(
                    "graph_sessions: failed to persist session %s: %s", session_id, exc
                )

        return SessionCreateResponse(
            session_id=session_id,
            root_node=root_node,
            created_at=created_at,
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

        # Check Redis cache for a previous identical expansion (15-min TTL).
        if self._redis is not None:
            try:
                cache_key = _expansion_cache_key(
                    session_id, request.seed_node_id, request.operation_type,
                    request.options.max_results,
                )
                cached = await self._redis.get(cache_key)
                if cached:
                    data = json.loads(cached)
                    return ExpansionResponseV2(
                        operation_id=data["operation_id"],
                        operation_type=data["operation_type"],
                        session_id=data["session_id"],
                        seed_node_id=data["seed_node_id"],
                        seed_lineage_id=data["seed_lineage_id"],
                        branch_id=data["branch_id"],
                        expansion_depth=data["expansion_depth"],
                        added_nodes=[InvestigationNode(**n) for n in data["nodes"]],
                        added_edges=[InvestigationEdge(**e) for e in data["edges"]],
                        has_more=data["has_more"],
                        pagination=PaginationMeta(**data["pagination"]),
                        layout_hints=LayoutHints(**data["layout_hints"]),
                        chain_context=ChainContext(**data["chain_context"]),
                        asset_context=AssetContext(**data["asset_context"]),
                        timestamp=datetime.fromisoformat(data["timestamp"]),
                    )
            except Exception as cache_exc:
                logger.debug("Redis cache read failed: %s", cache_exc)

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
                    # Split max_results between forward and backward expansion
                    max_total = request.options.max_results
                    max_fwd = (max_total + 1) // 2 if max_total is not None else None
                    max_bwd = max_total // 2 if max_total is not None else None
                    
                    # Create separate options for each direction
                    from src.trace_compiler.models import ExpandOptions
                    fwd_options = ExpandOptions(
                        max_results=max_fwd,
                        page_size=request.options.page_size,
                        depth=request.options.depth,
                        asset_filter=request.options.asset_filter,
                    )
                    bwd_options = ExpandOptions(
                        max_results=max_bwd,
                        page_size=request.options.page_size,
                        depth=request.options.depth,
                        asset_filter=request.options.asset_filter,
                    )
                    
                    fwd_n, fwd_e = await compiler.expand_next(
                        session_id=session_id,
                        branch_id=_branch,
                        path_sequence=0,
                        depth=0,
                        seed_address=identifier,
                        chain=chain,
                        options=fwd_options,
                    )
                    bwd_n, bwd_e = await compiler.expand_prev(
                        session_id=session_id,
                        branch_id=_branch,
                        path_sequence=1,
                        depth=0,
                        seed_address=identifier,
                        chain=chain,
                        options=bwd_options,
                    )
                    # Deduplicate nodes by node_id.
                    seen = {n.node_id for n in fwd_n}
                    added_nodes = fwd_n + [n for n in bwd_n if n.node_id not in seen]
                    
                    # Deduplicate edges by (src, dst, label) tuple.
                    seen_edges = set()
                    added_edges = []
                    for edge in fwd_e + bwd_e:
                        edge_key = (edge.source_node_id, edge.target_node_id, edge.edge_type)
                        if edge_key not in seen_edges:
                            seen_edges.add(edge_key)
                            added_edges.append(edge)
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
                # Swallow — return empty expansion rather than propagating.
        else:
            logger.debug(
                "TraceCompiler.expand: no compiler for chain=%s node_type=%s",
                chain,
                node_type,
            )

        # Build the response
        response = ExpansionResponseV2(
            operation_id=new_operation_id(),
            operation_type=request.operation_type,
            session_id=session_id,
            seed_node_id=request.seed_node_id,
            seed_lineage_id=request.seed_lineage_id,
            branch_id=_branch,
            expansion_depth=request.options.depth,
            added_nodes=added_nodes,
            added_edges=added_edges,
            has_more=(
                request.options.max_results is not None
                and request.options.max_results > 0
                and len(added_nodes) >= request.options.max_results
            ),
            pagination=PaginationMeta(
                page_size=request.options.page_size,
                max_results=request.options.max_results,
                has_more=(
                    request.options.max_results is not None
                    and request.options.max_results > 0
                    and len(added_nodes) >= request.options.max_results
                ),
            ),
            layout_hints=LayoutHints(
                suggested_layout="layered",
                anchor_node_ids=[request.seed_node_id],
                new_branch_root_id=added_nodes[0].node_id if added_nodes else None,
            ),
            chain_context=ChainContext(
                primary_chain=chain,
                chains_present=list({n.chain for n in added_nodes} | {chain}),
            ),
            asset_context=AssetContext(
                assets_present=list(
                    {e.asset_symbol for e in added_edges if e.asset_symbol}
                ),
            ),
            timestamp=datetime.now(timezone.utc),
        )

        # Write CanonicalAsset nodes to Neo4j for each unique asset seen in this
        # expansion (ADR-002 / invariant 5).  Fire-and-forget — failures are
        # swallowed so they never block the response.
        if added_edges and self._neo4j is not None:
            import asyncio as _asyncio
            _asyncio.create_task(
                self._upsert_canonical_assets(added_edges)
            )

        # Cache successful non-empty results in Redis (15-minute TTL).
        if added_nodes and self._redis is not None:
            try:
                cache_key = _expansion_cache_key(
                    session_id, request.seed_node_id, request.operation_type,
                    request.options.max_results,
                )
                payload = json.dumps({
                    "operation_id": response.operation_id,
                    "operation_type": response.operation_type,
                    "session_id": response.session_id,
                    "seed_node_id": response.seed_node_id,
                    "seed_lineage_id": response.seed_lineage_id,
                    "branch_id": response.branch_id,
                    "expansion_depth": response.expansion_depth,
                    "nodes": [n.model_dump(mode="json") for n in response.added_nodes],
                    "edges": [e.model_dump(mode="json") for e in response.added_edges],
                    "has_more": response.has_more,
                    "pagination": response.pagination.model_dump(mode="json"),
                    "layout_hints": response.layout_hints.model_dump(mode="json"),
                    "chain_context": response.chain_context.model_dump(mode="json"),
                    "asset_context": response.asset_context.model_dump(mode="json"),
                    "timestamp": response.timestamp.isoformat(),
                })
                await self._redis.setex(cache_key, 900, payload)  # 15 min TTL
            except Exception as cache_exc:
                logger.debug("Redis cache write failed: %s", cache_exc)

        return response

    async def _upsert_canonical_assets(
        self, edges: List[InvestigationEdge]
    ) -> None:
        """MERGE CanonicalAsset nodes into Neo4j for each unique asset in edges.

        Called fire-and-forget after every non-empty expansion so the
        investigation graph accumulates a CanonicalAsset node for every asset
        that flows through it.  Failures are swallowed — this write is
        best-effort and must never block the expansion response.

        Neo4j constraint: ``canonical_asset_symbol_unique`` on ``symbol``.
        Each CanonicalAsset carries ``coingecko_id`` (the canonical_asset_id
        from the event store) and the primary chain where it was first seen.
        """
        # Collect unique (canonical_asset_id, symbol, chain) tuples.
        seen: Dict[str, tuple] = {}
        for edge in edges:
            cid = edge.canonical_asset_id
            sym = edge.asset_symbol
            if cid and sym and sym not in seen:
                seen[sym] = (cid, sym, edge.asset_chain or edge.tx_chain or "unknown")

        if not seen:
            return

        cypher = """
        UNWIND $assets AS asset
        MERGE (c:CanonicalAsset {symbol: asset.symbol})
        ON CREATE SET
            c.coingecko_id  = asset.coingecko_id,
            c.primary_chain = asset.primary_chain,
            c.created_at    = datetime()
        ON MATCH SET
            c.coingecko_id  = coalesce(c.coingecko_id, asset.coingecko_id)
        """
        params = [
            {"symbol": sym, "coingecko_id": cid, "primary_chain": chain}
            for sym, (cid, _, chain) in seen.items()
        ]
        try:
            async with self._neo4j.session() as session:
                await session.run(cypher, assets=params)
        except Exception as exc:
            logger.debug("_upsert_canonical_assets failed: %s", exc)

    async def get_bridge_hop_status(
        self, session_id: str, hop_id: str
    ) -> BridgeHopStatusResponse:
        """Return the current resolution status of a bridge hop.

        Queries the ``bridge_correlations`` table by ``source_tx_hash``.
        Falls back to ``status="pending"`` if no record is found.

        Args:
            session_id: Investigation session UUID (accepted but not currently
                validated against the DB — callers must enforce authorization).
            hop_id:     BridgeHop.hop_id value (matched as ``source_tx_hash``).

        Returns:
            BridgeHopStatusResponse with current status fields.
        """
        if self._pg is not None:
            try:
                query = """
                SELECT status, destination_tx_hash, destination_chain,
                       destination_address, updated_at
                FROM bridge_correlations
                WHERE source_tx_hash = $1
                LIMIT 1
                """
                async with self._pg.acquire() as conn:
                    row = await conn.fetchrow(query, hop_id)
                if row:
                    return BridgeHopStatusResponse(
                        hop_id=hop_id,
                        status=row["status"],
                        destination_tx_hash=row.get("destination_tx_hash"),
                        destination_chain=row.get("destination_chain"),
                        destination_address=row.get("destination_address"),
                        updated_at=row.get("updated_at") or datetime.now(timezone.utc),
                    )
            except Exception as exc:
                logger.debug("get_bridge_hop_status DB lookup failed: %s", exc)

        return BridgeHopStatusResponse(
            hop_id=hop_id,
            status="pending",
            updated_at=datetime.now(timezone.utc),
        )
