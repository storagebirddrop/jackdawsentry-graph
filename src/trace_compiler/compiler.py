"""
TraceCompiler — orchestrates chain-specific compilers to produce
``ExpansionResponse v2`` payloads from the raw event store.

This is the semantic boundary between raw blockchain facts (PostgreSQL event
store, Neo4j canonical graph) and the investigation-view graph served to the
frontend.

Current state (Phase 4): EVM, UTXO, Solana, and Tron chain compilers are implemented.
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

from src.collectors.rpc.factory import get_rpc_client
from src.trace_compiler.chains.bitcoin import UTXOChainCompiler
from src.trace_compiler.chains.evm import EVM_CHAINS
from src.trace_compiler.chains.evm import EVMChainCompiler
from src.trace_compiler.chains.solana import SolanaChainCompiler
from src.trace_compiler.chains.tron import TronChainCompiler
from src.trace_compiler.lineage import edge_id as mk_edge_id
from src.trace_compiler.lineage import lineage_id as mk_lineage_id
from src.trace_compiler.lineage import new_operation_id
from src.trace_compiler.lineage import path_id as mk_path_id
from src.trace_compiler.models import AssetContext
from src.trace_compiler.models import AddressNodeData
from src.trace_compiler.models import BridgeHopStatusResponse
from src.trace_compiler.models import ChainContext
from src.trace_compiler.models import ExpandRequest
from src.trace_compiler.models import ExpansionEmptyState
from src.trace_compiler.models import ExpansionResponseV2
from src.trace_compiler.models import InvestigationEdge
from src.trace_compiler.models import InvestigationNode
from src.trace_compiler.models import LayoutHints
from src.trace_compiler.models import PaginationMeta
from src.trace_compiler.models import SessionCreateRequest
from src.trace_compiler.models import SessionCreateResponse
from src.trace_compiler.services.address_exposure import AddressExposureEnricher
from src.trace_compiler.lineage import node_id as mk_node_id

logger = logging.getLogger(__name__)

_EXPANSION_CACHE_TTL = 900  # 15 minutes
_HOP_ALLOWLIST_TTL_SECONDS = 24 * 60 * 60
_DIRECT_LIVE_ADDRESS_HISTORY_CHAINS = {"bitcoin", "solana"}
_ON_DEMAND_HISTORY_CHAINS = set(EVM_CHAINS) | {"tron", "solana", "bitcoin"}
_NATIVE_ASSET_SYMBOLS = {
    "bitcoin": "BTC",
    "solana": "SOL",
    "ethereum": "ETH",
    "bsc": "BNB",
    "polygon": "MATIC",
    "arbitrum": "ETH",
    "base": "ETH",
    "avalanche": "AVAX",
    "optimism": "ETH",
    "tron": "TRX",
}


def _operation_phrase(operation_type: str) -> str:
    if operation_type == "expand_next":
        return "next"
    if operation_type in {"expand_prev", "expand_previous"}:
        return "previous"
    if operation_type == "expand_neighbors":
        return "neighbor"
    return "related"


def _truncate_identifier(value: str, *, head: int = 10, tail: int = 8) -> str:
    if not value or len(value) <= head + tail + 3:
        return value
    return f"{value[:head]}...{value[-tail:]}"


def _canonical_node_identifier(chain: str, node_type: str, identifier: str) -> str:
    """Return the canonical identifier used inside stable graph node IDs."""
    if not isinstance(identifier, str):
        return identifier

    value = identifier.strip()
    if not value:
        return value

    if node_type == "address" and chain in EVM_CHAINS and value.startswith("0x"):
        return value.lower()

    if node_type in {"transaction", "bridge_hop", "swap_event"} and value.startswith("0x"):
        return value.lower()

    return value


def _canonical_node_id(node_id: str) -> str:
    """Normalize a node_id string to the canonical identifier form."""
    if not isinstance(node_id, str):
        return node_id

    parts = node_id.split(":", 2)
    if len(parts) != 3:
        return node_id

    chain, node_type, identifier = parts
    canonical_identifier = _canonical_node_identifier(chain, node_type, identifier)
    if canonical_identifier == identifier:
        return node_id
    return f"{chain}:{node_type}:{canonical_identifier}"


def _supports_direct_live_address_history(chain: str) -> bool:
    """Return True when the compiler can query recent history immediately."""
    return chain in _DIRECT_LIVE_ADDRESS_HISTORY_CHAINS


def _supports_on_demand_address_history(chain: str) -> bool:
    """Return True when empty frontiers can trigger background ingest."""
    return chain in _ON_DEMAND_HISTORY_CHAINS


def _supports_live_address_history(chain: str) -> bool:
    """Return True when either direct lookup or on-demand ingest exists."""
    return _supports_direct_live_address_history(chain) or _supports_on_demand_address_history(chain)


def _expansion_cache_key(
    session_id: str,
    request: ExpandRequest,
) -> str:
    """Deterministic Redis cache key for an expansion result.

    The key is scoped to the session and to the effective expansion options
    so cached responses cannot bleed across investigators or across requests
    that ask for materially different result sets.
    """
    normalized_asset_filter = sorted(
        {
            asset.strip().lower()
            for asset in request.options.asset_filter
            if isinstance(asset, str) and asset.strip()
        }
    )
    normalized_tx_hashes = sorted(
        {
            tx_hash.strip().lower()
            for tx_hash in request.options.tx_hashes
            if isinstance(tx_hash, str) and tx_hash.strip()
        }
    )
    fingerprint = {
        "version": 2,
        "session_id": session_id,
        "seed_node_id": _canonical_node_id(request.seed_node_id),
        "operation_type": request.operation_type,
        "depth": request.options.depth,
        "page_size": request.options.page_size,
        "max_results": request.options.max_results,
        "asset_filter": normalized_asset_filter,
        "tx_hashes": normalized_tx_hashes,
        "min_value_fiat": request.options.min_value_fiat,
        "include_services": request.options.include_services,
        "follow_bridges": request.options.follow_bridges,
    }
    raw = json.dumps(fingerprint, sort_keys=True, separators=(",", ":"))
    return "tc:" + hashlib.sha256(raw.encode()).hexdigest()


def _restamp_cached_path_ids(old_path_ids: List[str], branch_id: str) -> Dict[str, str]:
    """Map cached path IDs onto deterministic path IDs for the current branch."""
    path_map: Dict[str, str] = {}
    for sequence, old_path_id in enumerate(old_path_ids):
        path_map.setdefault(old_path_id, mk_path_id(branch_id, sequence))
    return path_map


def _restamp_cached_entities(
    session_id: str,
    branch_id: str,
    nodes: List[InvestigationNode],
    edges: List[InvestigationEdge],
) -> tuple[List[InvestigationNode], List[InvestigationEdge]]:
    """Recompute session-local lineage metadata for cache-hit entities."""
    old_path_ids: List[str] = []
    for node in nodes:
        if node.path_id not in old_path_ids:
            old_path_ids.append(node.path_id)
    for edge in edges:
        if edge.path_id not in old_path_ids:
            old_path_ids.append(edge.path_id)

    path_map = _restamp_cached_path_ids(old_path_ids, branch_id)

    restamped_nodes = [
        node.model_copy(
            update={
                "branch_id": branch_id,
                "path_id": path_map[node.path_id],
                "lineage_id": mk_lineage_id(
                    session_id,
                    branch_id,
                    path_map[node.path_id],
                    node.depth,
                ),
            }
        )
        for node in nodes
    ]
    restamped_edges = [
        edge.model_copy(
            update={
                "branch_id": branch_id,
                "path_id": path_map[edge.path_id],
                "edge_id": mk_edge_id(
                    edge.source_node_id,
                    edge.target_node_id,
                    branch_id,
                    edge.tx_hash,
                ),
            }
        )
        for edge in edges
    ]
    return restamped_nodes, restamped_edges


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
        _sol = SolanaChainCompiler(postgres_pool, neo4j_driver, redis_client)
        _tron = TronChainCompiler(postgres_pool, neo4j_driver, redis_client)
        self._address_exposure = AddressExposureEnricher(
            postgres_pool=postgres_pool,
            redis_client=redis_client,
        )
        self._chain_compilers: Dict[str, Any] = {
            chain: _evm for chain in _evm.supported_chains
        }
        self._chain_compilers.update(
            {chain: _btc for chain in _btc.supported_chains}
        )
        self._chain_compilers.update(
            {chain: _sol for chain in _sol.supported_chains}
        )
        self._chain_compilers.update(
            {chain: _tron for chain in _tron.supported_chains}
        )

    async def create_session(
        self,
        request: SessionCreateRequest,
        owner_user_id: Optional[str] = None,
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
        canonical_seed_address = _canonical_node_identifier(
            request.seed_chain,
            "address",
            request.seed_address,
        )
        _node_id = mk_node(request.seed_chain, "address", canonical_seed_address)
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
                chain=request.seed_chain,
                address_type="unknown",
            ),
        )
        root_node = await self._address_exposure.enrich_address_node(root_node)

        created_at = datetime.now(timezone.utc)

        # Persist session to PostgreSQL so it survives browser refresh.
        if self._pg is not None:
            try:
                async with self._pg.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO graph_sessions
                            (
                                session_id,
                                seed_address,
                                seed_chain,
                                case_id,
                                created_by,
                                created_at,
                                updated_at
                            )
                        VALUES ($1, $2, $3, $4, $5, $6, $6)
                        ON CONFLICT (session_id) DO NOTHING
                        """,
                        uuid.UUID(session_id),
                        request.seed_address,
                        request.seed_chain,
                        getattr(request, "case_id", None),
                        owner_user_id,
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

        # Compute branch_id first — needed both in the cache-hit path and in
        # the normal compilation path below.
        canonical_seed_node_id = _canonical_node_id(request.seed_node_id)
        if canonical_seed_node_id != request.seed_node_id:
            request = request.model_copy(update={"seed_node_id": canonical_seed_node_id})

        _branch = mk_branch(session_id, request.seed_node_id, 0)

        # Check Redis cache for a previous identical expansion (15-min TTL).
        if self._redis is not None:
            try:
                cache_key = _expansion_cache_key(
                    session_id,
                    request,
                )
                cached = await self._redis.get(cache_key)
                if cached:
                    data = json.loads(cached)
                    cached_nodes = [InvestigationNode(**n) for n in data["nodes"]]
                    cached_edges = [InvestigationEdge(**e) for e in data["edges"]]
                    added_nodes, added_edges = _restamp_cached_entities(
                        session_id=session_id,
                        branch_id=_branch,
                        nodes=cached_nodes,
                        edges=cached_edges,
                    )
                    await self._register_bridge_hops(session_id, added_nodes)
                    # Override session-scoped fields so the caller receives
                    # IDs that match their current session, not the session
                    # that originally populated the cache.
                    return ExpansionResponseV2(
                        operation_id=new_operation_id(),
                        operation_type=data["operation_type"],
                        session_id=session_id,
                        seed_node_id=data["seed_node_id"],
                        seed_lineage_id=request.seed_lineage_id,
                        branch_id=_branch,
                        expansion_depth=data["expansion_depth"],
                        added_nodes=added_nodes,
                        added_edges=added_edges,
                        has_more=data["has_more"],
                        pagination=PaginationMeta(**data["pagination"]),
                        layout_hints=LayoutHints(**data["layout_hints"]),
                        chain_context=ChainContext(**data["chain_context"]),
                        asset_context=AssetContext(**data["asset_context"]),
                        timestamp=datetime.now(timezone.utc),
                    )
            except Exception as cache_exc:
                logger.debug("Redis cache read failed: %s", cache_exc)

        # Derive chain from the canonical node_id format: "{chain}:{type}:{id}".
        parts = request.seed_node_id.split(":", 2)
        chain = parts[0] if parts else "unknown"
        node_type = parts[1] if len(parts) > 1 else "address"
        identifier = parts[2] if len(parts) > 2 else request.seed_node_id

        added_nodes: List[InvestigationNode] = []
        added_edges: List[InvestigationEdge] = []
        empty_state: Optional[ExpansionEmptyState] = None
        ingest_pending = False

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
                        tx_hashes=request.options.tx_hashes,
                        min_value_fiat=request.options.min_value_fiat,
                        include_services=request.options.include_services,
                        follow_bridges=request.options.follow_bridges,
                    )
                    bwd_options = ExpandOptions(
                        max_results=max_bwd,
                        page_size=request.options.page_size,
                        depth=request.options.depth,
                        asset_filter=request.options.asset_filter,
                        tx_hashes=request.options.tx_hashes,
                        min_value_fiat=request.options.min_value_fiat,
                        include_services=request.options.include_services,
                        follow_bridges=request.options.follow_bridges,
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

        if node_type == "address" and not added_nodes and not added_edges:
            live_nodes, live_edges = await self._expand_from_live_history(
                session_id=session_id,
                branch_id=_branch,
                request=request,
                chain=chain,
                seed_address=identifier,
                chain_compiler=compiler,
            )
            if live_nodes or live_edges:
                added_nodes = live_nodes
                added_edges = live_edges
            else:
                if _supports_on_demand_address_history(chain):
                    try:
                        from src.trace_compiler.ingest.trigger import maybe_trigger_address_ingest

                        ingest_pending = await maybe_trigger_address_ingest(
                            address=_canonical_node_identifier(chain, "address", identifier),
                            chain=chain,
                            pg_pool=self._pg,
                        )
                    except Exception as exc:
                        logger.debug(
                            "On-demand ingest trigger failed for %s/%s: %s",
                            chain,
                            identifier,
                            exc,
                        )

                if not ingest_pending:
                    empty_state = await self._build_empty_state(
                        chain=chain,
                        address=identifier,
                        operation_type=request.operation_type,
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
            empty_state=empty_state,
            ingest_pending=ingest_pending,
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

        await self._register_bridge_hops(session_id, added_nodes)

        # Cache successful non-empty results in Redis (15-minute TTL).
        if added_nodes and self._redis is not None:
            try:
                cache_key = _expansion_cache_key(
                    session_id,
                    request,
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

    async def _expand_from_live_history(
        self,
        *,
        session_id: str,
        branch_id: str,
        request: ExpandRequest,
        chain: str,
        seed_address: str,
        chain_compiler: Any = None,
    ) -> tuple[List[InvestigationNode], List[InvestigationEdge]]:
        """Fallback to live address history where the lightweight RPC supports it."""
        if not _supports_direct_live_address_history(chain):
            return [], []

        client = get_rpc_client(chain)
        if client is None:
            return [], []

        try:
            transactions = await client.get_address_transactions(
                seed_address,
                limit=min(request.options.max_results, 25),
            )
        except Exception as exc:
            logger.debug(
                "Live address-history lookup failed for %s/%s: %s",
                chain,
                seed_address,
                exc,
            )
            return [], []

        if not transactions:
            return [], []

        seed_node_id = mk_node_id(chain, "address", seed_address)
        path_id = mk_path_id(branch_id, 0)
        added_nodes: Dict[str, InvestigationNode] = {}
        added_edges: Dict[str, InvestigationEdge] = {}
        native_symbol = _NATIVE_ASSET_SYMBOLS.get(chain)
        canonical_seed = _canonical_node_identifier(chain, "address", seed_address)

        for tx in transactions:
            normalized_from = _canonical_node_identifier(
                chain, "address", tx.from_address or ""
            )
            normalized_to = _canonical_node_identifier(
                chain, "address", tx.to_address or ""
            )
            include_forward = (
                request.operation_type in {"expand_next", "expand_neighbors"}
                and normalized_from == canonical_seed
                and normalized_to
                and normalized_to != canonical_seed
            )
            include_backward = (
                request.operation_type in {"expand_prev", "expand_previous", "expand_neighbors"}
                and normalized_to == canonical_seed
                and normalized_from
                and normalized_from != canonical_seed
            )

            if include_forward:
                counterparty = normalized_to
                source_node_id = seed_node_id
                target_node_id = mk_node_id(chain, "address", counterparty)
                direction = "forward"
            elif include_backward:
                counterparty = normalized_from
                source_node_id = mk_node_id(chain, "address", counterparty)
                target_node_id = seed_node_id
                direction = "backward"
            else:
                continue

            node_id = mk_node_id(chain, "address", counterparty)
            if node_id not in added_nodes:
                node = InvestigationNode(
                    node_id=node_id,
                    lineage_id=mk_lineage_id(session_id, branch_id, path_id, 1),
                    node_type="address",
                    branch_id=branch_id,
                    path_id=path_id,
                    depth=1,
                    display_label=_truncate_identifier(counterparty),
                    display_sublabel=f"{chain.upper()} live lookup",
                    chain=chain,
                    expandable_directions=["prev", "next", "neighbors"],
                    address_data=AddressNodeData(
                        address=counterparty,
                        address_type="account",
                        chain=chain,
                    ),
                )
                if chain_compiler is not None and hasattr(chain_compiler, "_address_exposure"):
                    try:
                        node = await chain_compiler._address_exposure.enrich_node(node)
                    except Exception as exc:
                        logger.debug(
                            "Address exposure enrichment skipped for live node %s: %s",
                            counterparty,
                            exc,
                        )
                added_nodes[node_id] = node

            tx_hash = getattr(tx, "hash", None) or f"live:{chain}:{counterparty}"
            edge_key = f"{source_node_id}:{target_node_id}:{tx_hash}"
            if edge_key in added_edges:
                continue

            timestamp = tx.timestamp.isoformat() if getattr(tx, "timestamp", None) else None
            value_native = None
            raw_value = getattr(tx, "value", None)
            if raw_value is not None:
                try:
                    value_native = float(raw_value)
                except (TypeError, ValueError):
                    value_native = None

            added_edges[edge_key] = InvestigationEdge(
                edge_id=mk_edge_id(source_node_id, target_node_id, branch_id, tx_hash),
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                branch_id=branch_id,
                path_id=path_id,
                edge_type="transfer",
                value_native=value_native,
                asset_symbol=native_symbol,
                asset_chain=chain,
                tx_hash=tx_hash,
                tx_chain=chain,
                block_number=getattr(tx, "block_number", None),
                timestamp=timestamp,
                direction=direction,
            )

        return list(added_nodes.values()), list(added_edges.values())

    async def _build_empty_state(
        self,
        *,
        chain: str,
        address: str,
        operation_type: str,
    ) -> ExpansionEmptyState:
        """Return a frontend-friendly explanation for an empty expansion."""
        phrase = _operation_phrase(operation_type)
        live_lookup_supported = _supports_live_address_history(chain)
        observed_on_chain: Optional[bool] = None
        known_tx_count: Optional[int] = None

        client = get_rpc_client(chain)
        address_info = None
        if client is not None:
            try:
                address_info = await client.get_address_info(address)
            except Exception as exc:
                logger.debug(
                    "Address info lookup failed for empty expansion %s/%s: %s",
                    chain,
                    address,
                    exc,
                )

        if address_info is not None:
            known_tx_count = getattr(address_info, "transaction_count", None)
            balance_native = getattr(address_info, "balance", None) or 0.0
            address_type = getattr(address_info, "type", None)
            if chain in EVM_CHAINS:
                observed_on_chain = bool(
                    (known_tx_count or 0) > 0
                    or balance_native > 0
                    or address_type == "contract"
                )
            elif chain == "tron":
                observed_on_chain = balance_native > 0
            else:
                observed_on_chain = bool((known_tx_count or 0) > 0 or balance_native > 0)

        if live_lookup_supported:
            reason = "live_lookup_returned_empty"
            message = (
                f"No indexed {chain.upper()} {phrase} activity was found, and a live "
                "address-history fallback did not surface additional transactions."
            )
        elif observed_on_chain:
            reason = "dataset_missing_activity"
            message = (
                f"This {chain.upper()} address appears on-chain, but its {phrase} "
                "activity is not indexed in the current graph dataset and live "
                "address-history lookup is not configured for this chain yet."
            )
        elif observed_on_chain is False:
            reason = "no_observed_activity"
            message = (
                f"No indexed {chain.upper()} {phrase} activity was found for this "
                "address, and the lightweight on-chain lookup did not show "
                "observable activity either."
            )
        else:
            reason = "no_indexed_activity"
            message = (
                f"No indexed {chain.upper()} {phrase} activity was found for this "
                "address in the current graph dataset."
            )

        return ExpansionEmptyState(
            reason=reason,
            message=message,
            chain=chain,
            address=address,
            operation_type=operation_type,
            live_lookup_supported=live_lookup_supported,
            observed_on_chain=observed_on_chain,
            known_tx_count=known_tx_count,
        )

    @staticmethod
    def _bridge_hop_allowlist_key(session_id: str) -> str:
        return f"tc:session:{session_id}:bridge_hops"

    async def _register_bridge_hops(
        self,
        session_id: str,
        nodes: List[InvestigationNode],
    ) -> None:
        """Allow only bridge hops that were emitted to this session."""
        if self._redis is None:
            return

        hop_ids = [
            node.bridge_hop_data.hop_id
            for node in nodes
            if node.bridge_hop_data and node.bridge_hop_data.hop_id
        ]
        if not hop_ids:
            return

        try:
            allowlist_key = self._bridge_hop_allowlist_key(session_id)
            await self._redis.sadd(allowlist_key, *hop_ids)
            await self._redis.expire(allowlist_key, _HOP_ALLOWLIST_TTL_SECONDS)
        except Exception as exc:
            logger.debug("Bridge hop allowlist write failed: %s", exc)

    async def is_bridge_hop_allowed(self, session_id: str, hop_id: str) -> bool:
        """Return True only when the hop was materialized in this session."""
        if self._redis is None:
            logger.warning(
                "Bridge hop allowlist unavailable because Redis is not configured"
            )
            return False

        try:
            return bool(
                await self._redis.sismember(
                    self._bridge_hop_allowlist_key(session_id),
                    hop_id,
                )
            )
        except Exception as exc:
            logger.warning("Bridge hop allowlist lookup failed: %s", exc)
            return False

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
                result = await session.run(cypher, assets=params)
                await result.consume()  # Consume result to complete transaction
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
                       correlation_confidence,
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
                        correlation_confidence=row.get("correlation_confidence"),
                        updated_at=row.get("updated_at") or datetime.now(timezone.utc),
                    )
            except Exception as exc:
                logger.debug("get_bridge_hop_status DB lookup failed: %s", exc)

        return BridgeHopStatusResponse(
            hop_id=hop_id,
            status="pending",
            updated_at=datetime.now(timezone.utc),
        )
