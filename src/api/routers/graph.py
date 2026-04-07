"""
Jackdaw Sentry graph router.

This module serves the session-based investigation graph APIs that make up the
public graph product surface.

Some legacy graph handlers remain in this module as code-level holdovers for
private-repo compatibility work, but they are not routed in the public repo.
"""

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime
from datetime import timezone
from email.utils import format_datetime
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
import uuid as _uuid
from uuid import uuid4

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Response
from pydantic import BaseModel
from pydantic import field_validator

from src.api.auth import PERMISSIONS
from src.api.auth import User
from src.api.auth import check_permissions
from src.api.config import get_supported_blockchains
from src.api.config import settings
from src.api.graph_dependencies import get_edge_price_oracle
from src.api.graph_dependencies import get_known_bridge_addresses as load_known_bridge_addresses
from src.api.graph_dependencies import get_known_dex_addresses as load_known_dex_addresses
from src.api.graph_dependencies import get_known_mixer_addresses as load_known_mixer_addresses
from src.api.graph_dependencies import lookup_addresses_bulk
from src.api.graph_dependencies import screen_address
from src.api.middleware import get_graph_latency_stats

from src.api.database import cache_get
from src.api.database import cache_set
from src.api.database import get_neo4j_read_session
from src.api.database import get_neo4j_session
from src.api.database import get_postgres_pool
from src.collectors.rpc.factory import get_rpc_client
from src.services.canonical_assets import build_asset_selector_key
from src.services.canonical_assets import native_asset_identity
from src.services.canonical_assets import resolve_canonical_asset_identity
from src.services.graph_sessions import GraphSessionStore
from src.services.graph_sessions import SnapshotRevisionConflictError

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_GRAPH_NODES = 500
MAX_DEPTH = 5


def _get_graph_session_store() -> GraphSessionStore:
    return GraphSessionStore(get_postgres_pool())


async def _get_owned_session_row(session_id: str, current_user: User) -> Dict[str, Any]:
    """Return the session row only when it belongs to the authenticated user."""
    try:
        session_uuid = str(_uuid.UUID(session_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid session_id: must be a UUID",
        ) from exc

    try:
        pg = get_postgres_pool()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    session_id,
                    seed_address,
                    seed_chain,
                    case_id,
                    created_by,
                    snapshot,
                    snapshot_saved_at,
                    created_at,
                    updated_at
                FROM graph_sessions
                WHERE session_id = $1::uuid
                  AND created_by = $2
                LIMIT 1
                """,
                session_uuid,
                str(current_user.id),
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "Failed to look up graph session %s for %s: %s",
            session_id,
            current_user.username,
            exc,
        )
        raise HTTPException(status_code=503, detail="Session store unavailable") from exc

    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    return dict(row)


def _validate_expand_request(request: "ExpandRequest") -> None:
    """Reject expansion controls that are not implemented server-side."""
    if request.options.chain_filter:
        raise HTTPException(
            status_code=400,
            detail="chain_filter is not supported by this deployment",
        )

    if request.options.continuation_token:
        raise HTTPException(
            status_code=400,
            detail="continuation_token is not supported by this deployment",
        )


# =============================================================================
# Pydantic request/response models
# =============================================================================


class GraphExpandRequest(BaseModel):
    address: str
    blockchain: str
    depth: int = 1
    direction: str = "both"  # in, out, both
    min_value: Optional[float] = None
    time_from: Optional[str] = None
    time_to: Optional[str] = None

    @field_validator("blockchain")
    @classmethod
    def validate_blockchain(cls, v):
        if v.lower() not in get_supported_blockchains():
            raise ValueError(f"Unsupported blockchain: {v}")
        return v.lower()

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v):
        if v not in ("in", "out", "both"):
            raise ValueError("Direction must be 'in', 'out', or 'both'")
        return v


class GraphTraceRequest(BaseModel):
    tx_hash: str
    blockchain: str
    follow_hops: int = 3

    @field_validator("blockchain")
    @classmethod
    def validate_blockchain(cls, v):
        if v.lower() not in get_supported_blockchains():
            raise ValueError(f"Unsupported blockchain: {v}")
        return v.lower()

    @field_validator("follow_hops")
    @classmethod
    def validate_hops(cls, v):
        if v < 1 or v > MAX_DEPTH:
            raise ValueError(f"follow_hops must be between 1 and {MAX_DEPTH}")
        return v


class GraphSearchRequest(BaseModel):
    query: str
    blockchain: Optional[str] = None

    @field_validator("blockchain", mode="before")
    @classmethod
    def validate_blockchain(cls, v):
        if v and v.lower() not in get_supported_blockchains():
            raise ValueError(f"Unsupported blockchain: {v}")
        return v.lower() if v else None


class GraphClusterRequest(BaseModel):
    addresses: List[str]
    blockchain: str

    @field_validator("blockchain")
    @classmethod
    def validate_blockchain(cls, v):
        if v.lower() not in get_supported_blockchains():
            raise ValueError(f"Unsupported blockchain: {v}")
        return v.lower()

    @field_validator("addresses")
    @classmethod
    def validate_addresses(cls, v):
        cleaned = [a.strip().lower() for a in (v or []) if a.strip()]
        if len(cleaned) < 2:
            raise ValueError("At least 2 non-empty addresses required")
        if len(cleaned) > 50:
            raise ValueError("Maximum 50 addresses per cluster request")
        return cleaned


class GraphNode(BaseModel):
    id: str
    type: str  # address, transaction
    chain: str
    label: Optional[str] = None
    risk: float = 0.0
    sanctioned: bool = False
    balance: Optional[float] = None
    tx_count: Optional[int] = None
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    value: float = 0.0
    token: Optional[str] = None
    chain: str
    timestamp: Optional[str] = None
    tx_hash: Optional[str] = None
    block_number: Optional[int] = None


class GraphResponse(BaseModel):
    """Legacy flat graph response (search, cluster, trace endpoints)."""

    success: bool
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    timestamp: datetime


class ExpansionResponse(BaseModel):
    """Investigation-grade expansion response.

    Every node and edge returned by an expand operation carries lineage
    metadata (``branch_id``, ``parent_id``, ``depth``, ``path_id``) so the
    frontend can insert new nodes into the correct position in the existing
    graph rather than appending them as disconnected orphan blocks.

    See Phase 7 plan Section 7 for the full data contract specification.
    """

    operation_id: str
    operation_type: str  # expand_next | expand_previous | expand_bridge | expand_all
    parent_node_id: str
    branch_id: str
    insertion_depth: int
    new_nodes: List[Dict[str, Any]]
    new_edges: List[Dict[str, Any]]
    removed_node_ids: List[str] = []
    updated_nodes: List[Dict[str, Any]] = []
    layout_hints: Dict[str, Any] = {}
    expansion_metadata: Dict[str, Any] = {}
    asset_context: Dict[str, Any] = {}
    timestamp: datetime


# =============================================================================
# Endpoints
# =============================================================================


async def expand_address(
    request: GraphExpandRequest,
    response: Response,
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Expand an address node: return its direct neighbors and connecting edges.

    .. deprecated::
        Use ``POST /sessions/{session_id}/expand`` (ExpansionResponseV2) instead.
        This endpoint returns lineage-free flat data and will be removed after T1.15
        event-store cutover is complete (ADR-004).

    Uses Neo4j variable-length path queries bounded by depth.
    Falls back to live RPC if address is not in Neo4j.
    """
    response.headers["Deprecation"] = "true"
    sunset_date = datetime(2026, 6, 30, tzinfo=timezone.utc)
    response.headers["Sunset"] = format_datetime(sunset_date, usegmt=True)
    response.headers["Link"] = (
        '</api/v1/graph/sessions/{session_id}/expand>; rel="successor-version"'
    )
    logger.warning(
        "Deprecated endpoint POST /graph/expand called by user %s — "
        "migrate to POST /graph/sessions/{session_id}/expand (ADR-004)",
        current_user.username,
    )
    start = time.monotonic()
    # Bitcoin addresses are Base58 (case-sensitive) — preserve case.
    _bc = (request.blockchain or "").lower()
    addr = request.address if _bc == "bitcoin" else request.address.lower()
    nodes_map: Dict[str, Dict[str, Any]] = {}
    edges_list: List[Dict[str, Any]] = []

    # Build direction-specific Cypher using the bipartite graph model:
    #   (Address)-[:SENT]->(Transaction)-[:RECEIVED]->(Address)
    # Each expansion returns one hop from the seed address.  Multi-hop
    # traversal is handled by the frontend issuing sequential expand calls.
    # This avoids variable-length patterns that broke under the old schema
    # where :SENT was incorrectly defined as a direct Address→Address edge.
    #
    # All queries return the same four columns:
    #   from_addr, to_addr, t (Transaction node), b_chain
    value_filter = "AND toFloat(t.value) >= $min_val" if request.min_value is not None else ""
    time_filter = ""
    if request.time_from:
        time_filter += " AND t.timestamp >= $t_from"
    if request.time_to:
        time_filter += " AND t.timestamp <= $t_to"
    extra_where = value_filter + time_filter

    if request.direction == "out":
        # Outbound: seed sends → transaction → recipient
        cypher = f"""
    MATCH (a:Address {{address: $addr, blockchain: $bc}})-[:SENT]->(t:Transaction)-[:RECEIVED]->(tgt:Address)
    WHERE tgt.address <> $addr {extra_where}
    RETURN $addr AS from_addr,
           tgt.address AS to_addr,
           COALESCE(tgt.blockchain, $bc) AS b_chain,
           t.hash AS tx_hash,
           t.value AS tx_value,
           t.timestamp AS tx_ts,
           t.block_number AS tx_block
    LIMIT $node_limit
    """
    elif request.direction == "in":
        # Inbound: sender → transaction → seed
        cypher = f"""
    MATCH (src:Address)-[:SENT]->(t:Transaction)-[:RECEIVED]->(a:Address {{address: $addr, blockchain: $bc}})
    WHERE src.address <> $addr {extra_where}
    RETURN src.address AS from_addr,
           $addr AS to_addr,
           COALESCE(src.blockchain, $bc) AS b_chain,
           t.hash AS tx_hash,
           t.value AS tx_value,
           t.timestamp AS tx_ts,
           t.block_number AS tx_block
    LIMIT $node_limit
    """
    else:
        # Both: union of outbound and inbound single-hop neighbors.
        cypher = f"""
    MATCH (a:Address {{address: $addr, blockchain: $bc}})-[:SENT]->(t:Transaction)-[:RECEIVED]->(tgt:Address)
    WHERE tgt.address <> $addr {extra_where}
    RETURN $addr AS from_addr,
           tgt.address AS to_addr,
           COALESCE(tgt.blockchain, $bc) AS b_chain,
           t.hash AS tx_hash,
           t.value AS tx_value,
           t.timestamp AS tx_ts,
           t.block_number AS tx_block
    UNION
    MATCH (src:Address)-[:SENT]->(t:Transaction)-[:RECEIVED]->(a:Address {{address: $addr, blockchain: $bc}})
    WHERE src.address <> $addr {extra_where}
    RETURN src.address AS from_addr,
           $addr AS to_addr,
           COALESCE(src.blockchain, $bc) AS b_chain,
           t.hash AS tx_hash,
           t.value AS tx_value,
           t.timestamp AS tx_ts,
           t.block_number AS tx_block
    LIMIT $node_limit
    """

    params = {
        "addr": addr,
        "bc": request.blockchain,
        "node_limit": MAX_GRAPH_NODES,
        "min_val": request.min_value,
        "t_from": request.time_from,
        "t_to": request.time_to,
    }

    async with get_neo4j_session() as session:
        result = await session.run(cypher, **params)
        records = await result.data()

    # Build the seed node
    nodes_map[addr] = _make_address_node(addr, request.blockchain)

    for rec in records:
        from_addr_rec = rec.get("from_addr", addr)
        to_addr_rec = rec.get("to_addr", "")
        if not to_addr_rec:
            continue
        neighbor_chain = rec.get("b_chain", request.blockchain)

        # Ensure both endpoint nodes exist in the map.
        if from_addr_rec not in nodes_map:
            nodes_map[from_addr_rec] = _make_address_node(from_addr_rec, neighbor_chain)
        if to_addr_rec not in nodes_map:
            nodes_map[to_addr_rec] = _make_address_node(to_addr_rec, neighbor_chain)

        # Add flow edge.
        tx_hash = rec.get("tx_hash", "")
        if tx_hash:
            edge_id = tx_hash
        else:
            # Use disambiguator to prevent collisions
            if rec.get("tx_block") is not None:
                disambiguator = rec.get("tx_block")
            elif rec.get("tx_ts") is not None:
                disambiguator = rec.get("tx_ts")
            else:
                disambiguator = str(uuid4())
            edge_id = f"{from_addr_rec}-{to_addr_rec}-{disambiguator}"
        edges_list.append(
            {
                "id": edge_id,
                "source": from_addr_rec,
                "target": to_addr_rec,
                "value": _safe_float(rec.get("tx_value")),
                "chain": request.blockchain,
                "timestamp": rec.get("tx_ts"),
                "tx_hash": tx_hash,
                "block_number": rec.get("tx_block"),
                "edge_type": _classify_edge(from_addr_rec, to_addr_rec),
            }
        )

    # If nothing found in Neo4j, try live RPC for the seed address
    if len(nodes_map) == 1 and not edges_list:
        client = get_rpc_client(request.blockchain)
        if client:
            try:
                addr_info = await client.get_address_info(addr)
                if addr_info:
                    nodes_map[addr].update(
                        {
                            "balance": (
                                float(addr_info.balance) if addr_info.balance else 0.0
                            ),
                            "tx_count": addr_info.transaction_count,
                            "type": addr_info.type,
                        }
                    )
                # Attempt to fetch recent txs to build edges
                txs = await client.get_address_transactions(addr, limit=25)
                for tx in txs:
                    # Preserve case for Bitcoin (Base58); lowercase EVM/Solana
                    _norm = (lambda s: s) if _bc == "bitcoin" else (lambda s: s.lower())
                    from_a = _norm(tx.from_address or "")
                    to_a = _norm(tx.to_address or "")
                    if from_a == addr:
                        peer = to_a
                    elif to_a == addr:
                        peer = from_a
                    else:
                        continue  # tx doesn't involve addr
                    if peer and peer not in nodes_map:
                        nodes_map[peer] = _make_address_node(peer, request.blockchain)
                    if peer and from_a and to_a:
                        edges_list.append(
                            {
                                "id": tx.hash,
                                "source": from_a,
                                "target": to_a,
                                "value": float(tx.value) if tx.value else 0.0,
                                "chain": request.blockchain,
                                "timestamp": (
                                    tx.timestamp.isoformat() if tx.timestamp else None
                                ),
                                "tx_hash": tx.hash,
                                "block_number": tx.block_number,
                                "edge_type": _classify_edge(from_a, to_a),
                            }
                        )
            except (OSError, ValueError, TimeoutError) as exc:
                logger.warning(f"Graph expand RPC fallback failed: {exc}")

    # Tag sanctioned addresses and entity attributions
    await _enrich_sanctions(nodes_map, request.blockchain)
    await _enrich_entities(nodes_map, request.blockchain)

    # Enrich with fiat values using edge timestamps
    try:
        oracle = get_edge_price_oracle()
        # Use each edge's timestamp for historical pricing
        await oracle.enrich_edge_fiat_values(
            edges_list, request.blockchain, None  # Use edge timestamps
        )
    except Exception as exc:
        logger.warning(f"[graph] Price oracle enrichment failed: {exc}")
        # Continue without fiat enrichment

    elapsed_ms = int((time.monotonic() - start) * 1000)
    return GraphResponse(
        success=True,
        nodes=list(nodes_map.values()),
        edges=edges_list,
        metadata={
            "seed_address": addr,
            "blockchain": request.blockchain,
            "direction": request.direction,
            "node_count": len(nodes_map),
            "edge_count": len(edges_list),
            "processing_time_ms": elapsed_ms,
        },
        timestamp=datetime.now(timezone.utc),
    )


async def trace_transaction(
    request: GraphTraceRequest,
    response: Response,
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Trace a transaction: return the full flow from source through hops to destination.

    .. deprecated::
        Use ``POST /sessions/{session_id}/expand`` (ExpansionResponseV2) instead.
        This endpoint returns lineage-free flat data and will be removed after T1.15
        event-store cutover is complete (ADR-004).

    Uses Neo4j variable-length paths to follow fund flow.
    """
    response.headers["Deprecation"] = "true"
    sunset_date = datetime(2026, 6, 30, tzinfo=timezone.utc)
    response.headers["Sunset"] = format_datetime(sunset_date, usegmt=True)
    response.headers["Link"] = (
        '</api/v1/graph/sessions/{session_id}/expand>; rel="successor-version"'
    )
    logger.warning(
        "Deprecated endpoint POST /graph/trace called by user %s — "
        "migrate to POST /graph/sessions/{session_id}/expand (ADR-004)",
        current_user.username,
    )
    start = time.monotonic()
    nodes_map: Dict[str, Dict[str, Any]] = {}
    edges_list: List[Dict[str, Any]] = []

    # First, find the transaction and its direct participants
    cypher_seed = """
    MATCH (from_a:Address)-[:SENT]->(t:Transaction {hash: $tx_hash, blockchain: $bc})-[:RECEIVED]->(to_a:Address)
    RETURN from_a.address AS from_addr, to_a.address AS to_addr,
           t.value AS value, t.timestamp AS ts, t.block_number AS block_num
    """

    async with get_neo4j_session() as session:
        result = await session.run(
            cypher_seed, tx_hash=request.tx_hash, bc=request.blockchain
        )
        seed = await result.single()

    if not seed:
        # Try live RPC for the transaction
        client = get_rpc_client(request.blockchain)
        if client:
            try:
                tx = await client.get_transaction(request.tx_hash)
                if tx:
                    from_addr = (tx.from_address or "").lower()
                    to_addr = (tx.to_address or "").lower()
                    if from_addr:
                        nodes_map[from_addr] = _make_address_node(
                            from_addr, request.blockchain
                        )
                    if to_addr:
                        nodes_map[to_addr] = _make_address_node(
                            to_addr, request.blockchain
                        )
                    edges_list.append(
                        {
                            "id": tx.hash,
                            "source": from_addr,
                            "target": to_addr,
                            "value": float(tx.value) if tx.value else 0.0,
                            "chain": request.blockchain,
                            "timestamp": (
                                tx.timestamp.isoformat() if tx.timestamp else None
                            ),
                            "tx_hash": tx.hash,
                            "block_number": tx.block_number,
                            "edge_type": _classify_edge(from_addr, to_addr),
                        }
                    )
            except (OSError, ValueError, TimeoutError) as exc:
                logger.warning(f"Graph trace RPC fallback failed: {exc}")

        if not nodes_map:
            raise HTTPException(status_code=404, detail="Transaction not found")

        await _enrich_sanctions(nodes_map, request.blockchain)
        await _enrich_entities(nodes_map, request.blockchain)

        elapsed_ms = int((time.monotonic() - start) * 1000)
        return GraphResponse(
            success=True,
            nodes=list(nodes_map.values()),
            edges=edges_list,
            metadata={
                "tx_hash": request.tx_hash,
                "blockchain": request.blockchain,
                "data_source": "live_rpc",
                "hops": 0,
                "node_count": len(nodes_map),
                "edge_count": len(edges_list),
                "processing_time_ms": elapsed_ms,
            },
            timestamp=datetime.now(timezone.utc),
        )

    from_addr = seed["from_addr"]
    to_addr = seed["to_addr"]
    nodes_map[from_addr] = _make_address_node(from_addr, request.blockchain)
    nodes_map[to_addr] = _make_address_node(to_addr, request.blockchain)
    edges_list.append(
        {
            "id": request.tx_hash,
            "source": from_addr,
            "target": to_addr,
            "value": _safe_float(seed["value"]),
            "chain": request.blockchain,
            "timestamp": seed["ts"],
            "tx_hash": request.tx_hash,
            "block_number": seed.get("block_num"),
            "edge_type": _classify_edge(from_addr, to_addr),
        }
    )

    # Follow hops from the destination address using variable-length path
    if request.follow_hops > 0:
        cypher_hops = """
        MATCH path = (start:Address {address: $addr, blockchain: $bc})
              (-[:SENT]->(:Transaction)-[:RECEIVED]->(:Address)){1,$hops}
        WITH nodes(path) AS ns, relationships(path) AS rels
        UNWIND range(0, size(rels)-1) AS i
        WITH ns, rels, i
        WHERE i % 2 = 0
        WITH ns[i] AS src_node, ns[i+2] AS tgt_node, rels[i+1] AS tx_rel, rels[i] AS sent_rel
        RETURN src_node.address AS hop_addr,
               tgt_node.address AS next_addr,
               tx_rel.hash AS tx_hash,
               tx_rel.value AS value,
               tx_rel.timestamp AS ts,
               tx_rel.block_number AS block_num
        LIMIT $limit
        """
        async with get_neo4j_session() as session:
            result = await session.run(
                cypher_hops,
                addr=to_addr,
                bc=request.blockchain,
                hops=request.follow_hops,
                limit=MAX_GRAPH_NODES,
            )
            hop_records = await result.data()

        for rec in hop_records:
            hop = rec.get("hop_addr", "")
            nxt = rec.get("next_addr", "")
            if hop and hop not in nodes_map:
                nodes_map[hop] = _make_address_node(hop, request.blockchain)
            if nxt and nxt not in nodes_map:
                nodes_map[nxt] = _make_address_node(nxt, request.blockchain)
            if hop and nxt:
                edges_list.append(
                    {
                        "id": rec.get("tx_hash", f"{hop}-{nxt}"),
                        "source": hop,
                        "target": nxt,
                        "value": _safe_float(rec.get("value")),
                        "chain": request.blockchain,
                        "timestamp": rec.get("ts"),
                        "tx_hash": rec.get("tx_hash"),
                        "block_number": rec.get("block_num"),
                    }
                )

    await _enrich_sanctions(nodes_map, request.blockchain)
    await _enrich_entities(nodes_map, request.blockchain)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    return GraphResponse(
        success=True,
        nodes=list(nodes_map.values()),
        edges=edges_list,
        metadata={
            "tx_hash": request.tx_hash,
            "blockchain": request.blockchain,
            "data_source": "neo4j",
            "follow_hops": request.follow_hops,
            "node_count": len(nodes_map),
            "edge_count": len(edges_list),
            "processing_time_ms": elapsed_ms,
        },
        timestamp=datetime.now(timezone.utc),
    )


async def graph_search(
    request: GraphSearchRequest,
    response: Response,
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Search for an address or transaction hash; return the initial graph node(s).

    .. deprecated::
        Use ``POST /sessions`` to create a session and seed it with an address instead.
        This endpoint returns lineage-free flat data and will be removed after T1.15
        event-store cutover is complete (ADR-004).
    """
    response.headers["Deprecation"] = "true"
    sunset_date = datetime(2026, 6, 30, tzinfo=timezone.utc)
    response.headers["Sunset"] = format_datetime(sunset_date, usegmt=True)
    response.headers["Link"] = (
        '</api/v1/graph/sessions>; rel="successor-version"'
    )
    logger.warning(
        "Deprecated endpoint POST /graph/search called by user %s — "
        "migrate to POST /graph/sessions (ADR-004)",
        current_user.username,
    )
    start = time.monotonic()
    # Bitcoin addresses are Base58 (case-sensitive) — don't lowercase them.
    # EVM/Solana addresses are case-insensitive; lowercase those for Neo4j consistency.
    _bc = (request.blockchain or "").lower()
    q = request.query.strip() if _bc == "bitcoin" else request.query.strip().lower()
    nodes_map: Dict[str, Dict[str, Any]] = {}
    edges_list: List[Dict[str, Any]] = []

    bc_filter = "AND a.blockchain = $bc" if request.blockchain else ""
    bc_filter_t = "AND t.blockchain = $bc" if request.blockchain else ""

    # Try as address first
    async with get_neo4j_session() as session:
        result = await session.run(
            f"""
            OPTIONAL MATCH (a:Address {{address: $q}})
            WHERE true {bc_filter}
            WITH a
            OPTIONAL MATCH (a)-[:SENT]->(t:Transaction)
            WITH a, count(t) AS sent_count
            OPTIONAL MATCH (r:Transaction)-[:RECEIVED]->(a)
            WITH a, sent_count, count(r) AS recv_count
            RETURN a, sent_count + recv_count AS tx_count
            """,
            q=q,
            bc=request.blockchain,
        )
        rec = await result.single()

    if rec and rec["a"]:
        props = dict(rec["a"])
        chain = props.get("blockchain", request.blockchain or "unknown")
        node = _make_address_node(q, chain)
        node["tx_count"] = rec["tx_count"]
        nodes_map[q] = node
    else:
        # Try as transaction hash
        async with get_neo4j_session() as session:
            result = await session.run(
                f"""
                OPTIONAL MATCH (from_a:Address)-[:SENT]->(t:Transaction {{hash: $q}})-[:RECEIVED]->(to_a:Address)
                WHERE true {bc_filter_t}
                RETURN t, from_a.address AS from_addr, to_a.address AS to_addr
                """,
                q=q,
                bc=request.blockchain,
            )
            rec = await result.single()

        if rec and rec["t"]:
            t = dict(rec["t"])
            chain = t.get("blockchain", request.blockchain or "unknown")
            from_a = rec["from_addr"] or ""
            to_a = rec["to_addr"] or ""
            if from_a:
                nodes_map[from_a] = _make_address_node(from_a, chain)
            if to_a:
                nodes_map[to_a] = _make_address_node(to_a, chain)
            edges_list.append(
                {
                    "id": q,
                    "source": from_a,
                    "target": to_a,
                    "value": _safe_float(t.get("value")),
                    "chain": chain,
                    "timestamp": t.get("timestamp"),
                    "tx_hash": q,
                    "block_number": t.get("block_number"),
                }
            )
        else:
            # Live RPC fallback — try as address then tx
            chain = request.blockchain or "ethereum"
            client = get_rpc_client(chain)
            if client:
                try:
                    # Try address
                    addr_info = await client.get_address_info(q)
                    if addr_info:
                        node = _make_address_node(q, chain)
                        node.update(
                            {
                                "balance": (
                                    float(addr_info.balance)
                                    if addr_info.balance
                                    else 0.0
                                ),
                                "tx_count": addr_info.transaction_count,
                                "type": addr_info.type,
                            }
                        )
                        nodes_map[q] = node
                except Exception as exc:
                    logger.warning(f"Graph search RPC address fallback failed: {exc}")

            if not nodes_map and client:
                try:
                    tx = await client.get_transaction(q)
                    if tx:
                        fa = (tx.from_address or "").lower()
                        ta = (tx.to_address or "").lower()
                        if fa:
                            nodes_map[fa] = _make_address_node(fa, chain)
                        if ta:
                            nodes_map[ta] = _make_address_node(ta, chain)
                        if fa and ta:
                            edges_list.append(
                                {
                                    "id": tx.hash,
                                    "source": fa,
                                    "target": ta,
                                    "value": float(tx.value) if tx.value else 0.0,
                                    "chain": chain,
                                    "timestamp": (
                                        tx.timestamp.isoformat()
                                        if tx.timestamp
                                        else None
                                    ),
                                    "tx_hash": tx.hash,
                                    "block_number": tx.block_number,
                                }
                            )
                except Exception as exc:
                    logger.warning(f"Graph search RPC tx fallback failed: {exc}")

    if not nodes_map and not edges_list:
        raise HTTPException(status_code=404, detail="No results found")

    bc = request.blockchain or "ethereum"
    await _enrich_sanctions(nodes_map, bc)
    await _enrich_entities(nodes_map, bc)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    return GraphResponse(
        success=True,
        nodes=list(nodes_map.values()),
        edges=edges_list,
        metadata={
            "query": q,
            "blockchain": request.blockchain,
            "node_count": len(nodes_map),
            "edge_count": len(edges_list),
            "processing_time_ms": elapsed_ms,
        },
        timestamp=datetime.now(timezone.utc),
    )


_ADDRESS_SUMMARY_TTL = 300  # 5 minutes (T7.3)


def _address_summary_cache_key(addr: str, bc: str) -> str:
    """Deterministic Redis key for address-summary cache entries."""
    raw = f"addr_summary:{bc}:{addr}"
    return "as:" + hashlib.sha256(raw.encode()).hexdigest()


@router.get("/address/{address}/summary")
async def address_summary(
    address: str,
    blockchain: str = Query(default="ethereum"),
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Node metadata: balance, tx count, risk score, labels, sanctions status, first/last seen.

    Results are cached in Redis for 5 minutes (T7.3).
    Neo4j reads are routed to the read replica when configured (T7.1).
    """
    start = time.monotonic()
    addr = address.lower()
    bc = blockchain.lower()

    if bc not in get_supported_blockchains():
        raise HTTPException(status_code=400, detail=f"Unsupported blockchain: {bc}")

    # --- T7.3: Redis address summary cache ---
    cache_key = _address_summary_cache_key(addr, bc)
    cached = await cache_get(cache_key)
    if cached:
        try:
            payload = json.loads(cached)
            if not isinstance(payload, dict):
                raise ValueError("Cache payload is not a dict")
            payload.setdefault("metadata", {})
            elapsed_ms = int((time.monotonic() - start) * 1000)
            payload["metadata"]["processing_time_ms"] = elapsed_ms
            payload["metadata"]["cache_hit"] = True
            return payload
        except Exception as exc:
            logger.warning(f"Cache read failed for {cache_key}: {exc}")
            # corrupt cache entry — fall through to live query

    data: Dict[str, Any] = {"address": addr, "blockchain": bc}

    # Neo4j lookup — uses read replica when configured (T7.1)
    async with get_neo4j_read_session() as session:
        result = await session.run(
            """
            OPTIONAL MATCH (a:Address {address: $addr, blockchain: $bc})
            OPTIONAL MATCH (a)-[:SENT]->(sent_t:Transaction)
            OPTIONAL MATCH (recv_t:Transaction)-[:RECEIVED]->(a)
            WITH a,
                 count(DISTINCT sent_t) AS sent_count,
                 count(DISTINCT recv_t) AS recv_count,
                 min(COALESCE(sent_t.timestamp, recv_t.timestamp)) AS first_activity,
                 max(COALESCE(recv_t.timestamp, sent_t.timestamp)) AS last_activity
            RETURN a, sent_count + recv_count AS tx_count, sent_count, recv_count,
                   first_activity, last_activity
            """,
            addr=addr,
            bc=bc,
        )
        rec = await result.single()

    if rec and rec["a"]:
        props = dict(rec["a"])
        data.update(
            {
                "balance": _safe_float(props.get("balance")),
                "tx_count": rec["tx_count"],
                "sent_count": rec["sent_count"],
                "recv_count": rec["recv_count"],
                "type": props.get("type", "unknown"),
                "risk_score": _safe_float(props.get("risk_score")),
                "labels": props.get("labels", []),
                "first_seen": rec.get("first_activity"),
                "last_seen": rec.get("last_activity"),
                "sanctioned": props.get("sanctioned", False),
                "data_source": "neo4j",
            }
        )
    else:
        # Live RPC fallback
        client = get_rpc_client(bc)
        if client:
            try:
                addr_info = await client.get_address_info(addr)
                if addr_info:
                    data.update(
                        {
                            "balance": (
                                float(addr_info.balance) if addr_info.balance else 0.0
                            ),
                            "tx_count": addr_info.transaction_count,
                            "type": addr_info.type,
                            "risk_score": 0.0,
                            "labels": [],
                            "sanctioned": False,
                            "data_source": "live_rpc",
                        }
                    )
            except (OSError, ValueError, TimeoutError) as exc:
                logger.warning(f"Address summary RPC fallback failed: {exc}")

        if "data_source" not in data:
            raise HTTPException(status_code=404, detail="Address not found")

    elapsed_ms = int((time.monotonic() - start) * 1000)
    response = {
        "success": True,
        "summary": data,
        "metadata": {"processing_time_ms": elapsed_ms, "cache_hit": False},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Store in Redis cache (failures are swallowed — T7.3)
    try:
        await cache_set(cache_key, json.dumps(response), ttl=_ADDRESS_SUMMARY_TTL)
    except Exception:
        pass

    return response


async def cluster_addresses(
    request: GraphClusterRequest,
    response: Response,
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Find common counterparties and shared transaction patterns for a set of addresses.

    .. deprecated::
        Use the attribution API instead, which applies entity clustering via Neo4j.
        This endpoint returns lineage-free flat data and will be removed after T1.15
        event-store cutover is complete (ADR-004).
    """
    response.headers["Deprecation"] = "true"
    sunset_date = datetime(2026, 6, 30, tzinfo=timezone.utc)
    response.headers["Sunset"] = format_datetime(sunset_date, usegmt=True)
    response.headers["Link"] = (
        '</api/v1/attribution>; rel="successor-version"'
    )
    logger.warning(
        "Deprecated endpoint POST /graph/cluster called by user %s — "
        "migrate to attribution API (ADR-004)",
        current_user.username,
    )
    start = time.monotonic()
    nodes_map: Dict[str, Dict[str, Any]] = {}
    edges_list: List[Dict[str, Any]] = []

    # Add all input addresses as nodes
    for addr in request.addresses:
        nodes_map[addr] = _make_address_node(addr, request.blockchain)

    # Find common counterparties — addresses that transacted with 2+ of the input addresses.
    # Use the bipartite model: outbound = (input)-[:SENT]->(tx)-[:RECEIVED]->(counterparty)
    # and inbound = (counterparty)-[:SENT]->(tx)-[:RECEIVED]->(input).
    cypher = """
    UNWIND $addrs AS input_addr
    MATCH (a:Address {address: input_addr, blockchain: $bc})-[:SENT]->(t:Transaction)-[:RECEIVED]->(counterparty:Address)
    WHERE NOT counterparty.address IN $addrs
    WITH counterparty, collect(DISTINCT input_addr) AS connected_inputs,
         collect(DISTINCT {input_addr: input_addr, tx_hash: t.hash, tx_value: t.value}) AS tx_data
    WHERE size(connected_inputs) >= 2
    RETURN counterparty.address AS cp_addr,
           connected_inputs,
           tx_data
    LIMIT $limit
    """

    async with get_neo4j_session() as session:
        result = await session.run(
            cypher,
            addrs=request.addresses,
            bc=request.blockchain,
            limit=MAX_GRAPH_NODES,
        )
        records = await result.data()

    for rec in records:
        cp = rec["cp_addr"]
        if cp not in nodes_map:
            node = _make_address_node(cp, request.blockchain)
            node["label"] = "common_counterparty"
            nodes_map[cp] = node

        # Add edges from each tx_data entry to the counterparty
        for entry in rec.get("tx_data", []):
            input_addr = entry.get("input_addr", "")
            tx_hash = entry.get("tx_hash") or f"{input_addr}-{cp}"
            value = _safe_float(entry.get("tx_value"))
            edges_list.append(
                {
                    "id": tx_hash,
                    "source": input_addr,
                    "target": cp,
                    "value": value,
                    "chain": request.blockchain,
                    "tx_hash": tx_hash if tx_hash != f"{input_addr}-{cp}" else None,
                }
            )

    # Also find direct transactions between input addresses
    cypher_direct = """
    UNWIND $addrs AS a1
    UNWIND $addrs AS a2
    WITH a1, a2 WHERE a1 <> a2
    MATCH (from_a:Address {address: a1, blockchain: $bc})-[:SENT]->(t:Transaction)-[:RECEIVED]->(to_a:Address {address: a2, blockchain: $bc})
    RETURN a1, a2, t.hash AS tx_hash, t.value AS value, t.timestamp AS ts
    LIMIT $limit
    """
    async with get_neo4j_session() as session:
        result = await session.run(
            cypher_direct,
            addrs=request.addresses,
            bc=request.blockchain,
            limit=MAX_GRAPH_NODES,
        )
        direct_records = await result.data()

    for rec in direct_records:
        edges_list.append(
            {
                "id": rec["tx_hash"],
                "source": rec["a1"],
                "target": rec["a2"],
                "value": _safe_float(rec.get("value")),
                "chain": request.blockchain,
                "timestamp": rec.get("ts"),
                "tx_hash": rec["tx_hash"],
            }
        )

    await _enrich_sanctions(nodes_map, request.blockchain)
    await _enrich_entities(nodes_map, request.blockchain)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    return GraphResponse(
        success=True,
        nodes=list(nodes_map.values()),
        edges=edges_list,
        metadata={
            "input_addresses": request.addresses,
            "blockchain": request.blockchain,
            "common_counterparties": len(nodes_map) - len(request.addresses),
            "direct_links": len(direct_records),
            "node_count": len(nodes_map),
            "edge_count": len(edges_list),
            "processing_time_ms": elapsed_ms,
        },
        timestamp=datetime.now(timezone.utc),
    )


# =============================================================================
# Bridge expansion (Phase 8)
# =============================================================================


class ExpandSolanaTxRequest(BaseModel):
    """Request to expand a Solana transaction as an instruction sub-graph.

    Returns one ``InstructionNode`` per instruction (outer and inner combined),
    carrying ``program_name``, ``instruction_type``, decoded arguments, and
    standard lineage metadata.
    """

    tx_signature: str
    include_inner_instructions: bool = True
    branch_id: Optional[str] = None
    insertion_depth: int = 1
    parent_node_id: Optional[str] = None


async def expand_solana_tx(
    request: ExpandSolanaTxRequest,
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Expand a Solana transaction to its instruction-level sub-graph.

    Fetches the transaction from the Solana RPC client and runs every
    instruction through :class:`~src.collectors.solana_instruction_parser.SolanaInstructionParser`.
    Supported programs: SPL Token, Jupiter v6, Raydium AMM v4, Wormhole Token
    Bridge, System Program.  Unknown programs are returned with
    ``instruction_type: "unknown"`` and ``decode_status: "raw"`` — they are
    never dropped.

    All returned nodes carry ``branch_id``, ``depth``, ``parent_id``, and
    ``path_id`` for correct frontend graph insertion.
    """
    from src.collectors.solana_instruction_parser import SolanaInstructionParser
    from src.collectors.rpc.solana_rpc import SolanaRpcClient

    branch_id = request.branch_id or str(uuid4())
    tx_sig = request.tx_signature
    parent_node_id = request.parent_node_id or f"solana:tx:{tx_sig}"
    depth = request.insertion_depth
    path_id = str(uuid4())

    # Fetch raw transaction data via the Solana RPC client
    client: Optional[SolanaRpcClient] = get_rpc_client("solana")  # type: ignore[assignment]
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="Solana RPC client is not configured",
        )

    raw_tx = await client.get_raw_transaction(tx_sig)
    if raw_tx is None:
        raise HTTPException(
            status_code=404,
            detail=f"Transaction {tx_sig} not found on Solana",
        )

    tx_message = raw_tx.get("transaction", {}).get("message", {})
    meta = raw_tx.get("meta", {})
    account_keys = tx_message.get("accountKeys", [])
    instructions = tx_message.get("instructions", [])
    inner_instructions = (
        meta.get("innerInstructions", [])
        if request.include_inner_instructions
        else []
    )

    parser = SolanaInstructionParser()
    parsed = parser.parse_transaction_instructions(
        instructions=instructions,
        account_keys=account_keys,
        inner_instructions=inner_instructions,
    )

    # Build transaction node (parent of all instruction nodes)
    slot = raw_tx.get("slot")
    block_time = raw_tx.get("blockTime")
    tx_node = {
        "node_id": parent_node_id,
        "node_type": "transaction",
        "tx_hash": tx_sig,
        "blockchain": "solana",
        "block_number": slot,
        "timestamp": (
            datetime.fromtimestamp(block_time, tz=timezone.utc).isoformat()
            if block_time
            else None
        ),
        "fee_lamports": meta.get("fee"),
        "status": "confirmed" if meta.get("err") is None else "failed",
        "instruction_count": len(parsed),
        "depth": depth,
        "parent_id": request.parent_node_id or parent_node_id,
        "branch_id": branch_id,
        "path_id": path_id,
    }

    new_nodes = [tx_node]
    new_edges = []

    program_counts: Dict[str, int] = {}
    swap_count = 0
    bridge_count = 0
    token_transfer_count = 0
    sol_transfer_count = 0

    for ix_idx, ix in enumerate(parsed):
        ix_node = parser.to_node_dict(
            ix,
            branch_id=branch_id,
            depth=depth + 1,
            parent_node_id=parent_node_id,
            path_id=path_id,
            ix_index=ix_idx,
        )
        new_nodes.append(ix_node)

        # Edge: tx → instruction
        new_edges.append(
            {
                "edge_id": f"ix_edge:{tx_sig}:{ix_idx}",
                "edge_type": "contains_instruction",
                "source_node_id": parent_node_id,
                "target_node_id": ix_node["node_id"],
                "depth": depth + 1,
                "branch_id": branch_id,
            }
        )

        # Aggregate metadata stats
        pname = ix.program_name
        program_counts[pname] = program_counts.get(pname, 0) + 1
        itype = ix.instruction_type
        if itype in ("route", "sharedAccountsRoute", "swapBaseIn", "swapBaseOut"):
            swap_count += 1
        elif pname == "wormhole_token_bridge" and itype in (
            "transferTokens", "transferTokensWithPayload"
        ):
            bridge_count += 1
        elif itype in ("transfer", "transferChecked"):
            if pname in ("spl_token", "spl_token_2022"):
                token_transfer_count += 1
            elif pname == "system_program":
                sol_transfer_count += 1

    # Enrich with fiat values
    try:
        oracle = get_edge_price_oracle()
        tx_ts = (
            datetime.fromtimestamp(block_time, tz=timezone.utc)
            if block_time
            else datetime.now(timezone.utc)
        )
        await oracle.enrich_edge_fiat_values(new_edges, "solana", tx_ts)
    except Exception as exc:
        logger.warning(f"[graph] Solana price oracle enrichment failed: {exc}, tx_ts={tx_ts}, edges={len(new_edges)}")
        # Continue without fiat enrichment

    return ExpansionResponse(
        operation_id=str(uuid4()),
        operation_type="expand_solana_tx",
        parent_node_id=parent_node_id,
        branch_id=branch_id,
        insertion_depth=depth,
        new_nodes=new_nodes,
        new_edges=new_edges,
        expansion_metadata={
            "tx_signature": tx_sig,
            "instruction_count": len(parsed),
            "program_counts": program_counts,
            "swap_count": swap_count,
            "bridge_count": bridge_count,
            "token_transfer_count": token_transfer_count,
            "sol_transfer_count": sol_transfer_count,
        },
        asset_context={
            "blockchain": "solana",
        },
        timestamp=datetime.now(timezone.utc),
    )


class ExpandBridgeRequest(BaseModel):
    """Request to follow a bridge hop from source chain to destination chain."""

    source_tx_hash: str
    source_blockchain: str
    bridge_protocol: Optional[str] = None  # auto-detected if omitted
    to_address: Optional[str] = None  # contract address for protocol detection
    branch_id: Optional[str] = None  # if None, a new UUID branch is created
    insertion_depth: int = 1
    parent_node_id: Optional[str] = None


async def expand_bridge(
    request: ExpandBridgeRequest,
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Follow a bridge hop from source chain to destination chain.

    Looks up the ``bridge_correlations`` table for a stored correlation.
    If none is found, triggers ``BridgeTracer.detect_bridge_hop()`` to query
    the relevant bridge API in real time.

    Returns an ``ExpansionResponse`` containing a ``BridgeNode`` and, when
    the egress has been confirmed, a destination-chain ``AddressNode``.
    Both nodes carry ``branch_id``, ``depth``, and ``parent_id`` for
    correct frontend graph insertion.
    """
    from src.tracing.bridge_tracer import BridgeCorrelation, BridgeTracer

    tracer = BridgeTracer()
    branch_id = request.branch_id or str(uuid4())
    parent_node_id = (
        request.parent_node_id
        or f"{request.source_blockchain}:{request.source_tx_hash}"
    )
    depth = request.insertion_depth

    # 1. Try stored correlation first (fast path)
    correlation = await tracer.lookup_correlation(
        request.source_blockchain, request.source_tx_hash
    )

    # 2. Fall back to live API detection
    if correlation is None:
        correlation = await tracer.detect_bridge_hop(
            tx_hash=request.source_tx_hash,
            blockchain=request.source_blockchain,
            to_address=request.to_address,
        )
        if correlation is not None:
            await tracer.store_correlation(correlation)

    # 3. If still nothing, return a generic pending node
    if correlation is None:
        protocol = request.bridge_protocol or "unknown"
        bridge_node_id = f"bridge:{protocol}:{request.source_tx_hash}"
        bridge_node = {
            "node_id": bridge_node_id,
            "node_type": "bridge",
            "bridge_protocol": protocol,
            "source_chain": request.source_blockchain,
            "source_tx_id": f"{request.source_blockchain}:tx:{request.source_tx_hash}",
            "status": "pending",
            "depth": depth,
            "parent_id": parent_node_id,
            "branch_id": branch_id,
            "path_id": str(uuid4()),
        }
        return ExpansionResponse(
            operation_id=str(uuid4()),
            operation_type="expand_bridge",
            parent_node_id=parent_node_id,
            branch_id=branch_id,
            insertion_depth=depth,
            new_nodes=[bridge_node],
            new_edges=[],
            expansion_metadata={"status": "pending", "note": "Bridge correlation not yet resolved"},
            timestamp=datetime.now(timezone.utc),
        )

    # 4. Build the BridgeNode from the stored/fetched correlation
    bridge_node_id = f"bridge:{correlation.protocol}:{request.source_tx_hash}"
    path_id = str(uuid4())
    bridge_node = {
        "node_id": bridge_node_id,
        "node_type": "bridge",
        "bridge_protocol": correlation.protocol,
        "bridge_mechanism": correlation.mechanism,
        "source_chain": correlation.source_chain,
        "destination_chain": correlation.destination_chain,
        "source_tx_id": f"{correlation.source_chain}:tx:{correlation.source_tx_hash}",
        "destination_tx_id": (
            f"{correlation.destination_chain}:tx:{correlation.destination_tx_hash}"
            if correlation.destination_tx_hash and correlation.destination_chain
            else None
        ),
        "source_asset": correlation.source_asset,
        "destination_asset": correlation.destination_asset,
        "source_amount": correlation.source_amount,
        "destination_amount": correlation.destination_amount,
        "time_delta_seconds": correlation.time_delta_seconds,
        "status": correlation.status,
        "correlation_confidence": correlation.correlation_confidence,
        "depth": depth,
        "parent_id": parent_node_id,
        "branch_id": branch_id,
        "path_id": path_id,
    }

    new_nodes = [bridge_node]
    new_edges = [
        {
            "edge_id": f"bridge_ingress:{request.source_tx_hash}",
            "edge_type": "bridge_ingress",
            "source_node_id": parent_node_id,
            "target_node_id": bridge_node_id,
            "asset_symbol": correlation.source_asset,
            "amount_native": correlation.source_amount,
            "fiat_value_at_transfer": correlation.source_fiat_value,
            "depth": depth,
            "branch_id": branch_id,
        }
    ]

    # 5. If egress is confirmed, add the destination address node
    if correlation.destination_address and correlation.destination_chain:
        dest_node_id = f"{correlation.destination_chain}:{correlation.destination_address}"
        dest_node = {
            "node_id": dest_node_id,
            "node_type": "address",
            "address": correlation.destination_address,
            "blockchain": correlation.destination_chain,
            "depth": depth + 1,
            "parent_id": bridge_node_id,
            "branch_id": branch_id,
            "path_id": path_id,
        }
        new_nodes.append(dest_node)
        new_edges.append(
            {
                "edge_id": (
                    f"bridge_egress:"
                    f"{correlation.destination_tx_hash or request.source_tx_hash}"
                ),
                "edge_type": "bridge_egress",
                "source_node_id": bridge_node_id,
                "target_node_id": dest_node_id,
                "asset_symbol": correlation.destination_asset,
                "amount_native": correlation.destination_amount,
                "fiat_value_at_transfer": correlation.destination_fiat_value,
                "depth": depth + 1,
                "branch_id": branch_id,
            }
        )

    return ExpansionResponse(
        operation_id=str(uuid4()),
        operation_type="expand_bridge",
        parent_node_id=parent_node_id,
        branch_id=branch_id,
        insertion_depth=depth,
        new_nodes=new_nodes,
        new_edges=new_edges,
        expansion_metadata={
            "protocol": correlation.protocol,
            "mechanism": correlation.mechanism,
            "status": correlation.status,
            "confidence": correlation.correlation_confidence,
            "has_destination": correlation.destination_address is not None,
        },
        asset_context={
            "source_asset": correlation.source_asset,
            "destination_asset": correlation.destination_asset,
            "source_amount": correlation.source_amount,
            "destination_amount": correlation.destination_amount,
        },
        timestamp=datetime.now(timezone.utc),
    )


class ExpandUTXORequest(BaseModel):
    """Request to expand a Bitcoin address as a UTXO sub-graph.

    Returns UTXONode objects (one per output) with change-output annotations
    and a CoinJoin halt node when the spending transaction is a CoinJoin.
    """

    address: str
    blockchain: str = "bitcoin"
    direction: str = "out"  # out | in | both
    min_value_satoshis: Optional[int] = None
    branch_id: Optional[str] = None
    insertion_depth: int = 1
    parent_node_id: Optional[str] = None

    @field_validator("blockchain")
    @classmethod
    def validate_blockchain(cls, v: str) -> str:
        """Only UTXO chains are supported."""
        if v.lower() != "bitcoin":
            raise ValueError("expand-utxo currently supports bitcoin only")
        return v.lower()

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str) -> str:
        """Validate expansion direction."""
        if v not in ("in", "out", "both"):
            raise ValueError("direction must be 'in', 'out', or 'both'")
        return v


async def expand_utxo(
    request: ExpandUTXORequest,
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Expand a Bitcoin address returning its UTXO sub-graph.

    Queries Neo4j for spending transactions (outbound) or funding transactions
    (inbound) linked to the address via the bipartite
    ``(Address)-[:SENT]->(Transaction)-[:RECEIVED]->(Address)`` model.
    UTXO metadata (``output_index``, ``value_satoshis``, ``script_type``,
    ``is_probable_change``) is carried on the ``:RECEIVED`` relationship
    properties and promoted to individual ``UTXONode`` dicts.

    CoinJoin transactions are returned as halt nodes (``is_coinjoin_halt:
    true``) — taint analysis must not propagate through them.

    Falls back to the live Bitcoin RPC client when no data exists in Neo4j.
    """
    branch_id = request.branch_id or str(uuid4())
    parent_node_id = request.parent_node_id or f"bitcoin:{request.address}"
    depth = request.insertion_depth
    path_id_base = str(uuid4())

    value_filter = (
        "AND r.value_satoshis >= $min_sat" if request.min_value_satoshis is not None else ""
    )

    # ------------------------------------------------------------------
    # Build Cypher queries for each direction.
    # We fetch the Transaction node and the :RECEIVED relationship
    # properties (output_index, value_satoshis, script_type,
    # is_probable_change) for every output of that spending tx.
    # ------------------------------------------------------------------
    records: List[Dict[str, Any]] = []

    async with get_neo4j_session() as session:
        if request.direction in ("out", "both"):
            cypher_out = f"""
            MATCH (a:Address {{address: $addr, blockchain: $bc}})-[:SENT]->(t:Transaction)
            MATCH (t)-[r:RECEIVED]->(out_addr:Address)
            WHERE out_addr.address <> $addr {value_filter}
            RETURN t.hash AS tx_hash,
                   t.block_number AS block_number,
                   t.timestamp AS tx_ts,
                   t.is_coinjoin AS is_coinjoin,
                   t.fee AS fee,
                   out_addr.address AS output_address,
                   r.output_index AS output_index,
                   r.value_satoshis AS value_satoshis,
                   r.script_type AS script_type,
                   r.is_probable_change AS is_probable_change,
                   'out' AS direction
            ORDER BY t.timestamp DESC
            LIMIT $lim
            """
            res = await session.run(
                cypher_out,
                addr=request.address,
                bc=request.blockchain,
                lim=MAX_GRAPH_NODES,
                min_sat=request.min_value_satoshis,
            )
            records.extend(await res.data())

        if request.direction in ("in", "both"):
            cypher_in = f"""
            MATCH (src:Address)-[:SENT]->(t:Transaction)-[:RECEIVED {{}}]->(a:Address {{address: $addr, blockchain: $bc}})
            MATCH (t)-[r:RECEIVED]->(out_addr:Address)
            WHERE 1=1 {value_filter}
            RETURN t.hash AS tx_hash,
                   t.block_number AS block_number,
                   t.timestamp AS tx_ts,
                   t.is_coinjoin AS is_coinjoin,
                   t.fee AS fee,
                   out_addr.address AS output_address,
                   r.output_index AS output_index,
                   r.value_satoshis AS value_satoshis,
                   r.script_type AS script_type,
                   r.is_probable_change AS is_probable_change,
                   'in' AS direction
            ORDER BY t.timestamp DESC
            LIMIT $lim
            """
            res = await session.run(
                cypher_in,
                addr=request.address,
                bc=request.blockchain,
                lim=MAX_GRAPH_NODES,
                min_sat=request.min_value_satoshis,
            )
            records.extend(await res.data())

    # ------------------------------------------------------------------
    # RPC fallback: if nothing in Neo4j, fetch live and synthesise nodes
    # ------------------------------------------------------------------
    if not records:
        client = get_rpc_client(request.blockchain)
        if client:
            try:
                txs = await client.get_address_transactions(request.address, limit=25)
                for _rpc_tx in txs:
                    # Build synthetic records from UTXOOutput objects
                    for out in _rpc_tx.outputs:
                        if out.is_op_return or not out.address:
                            continue
                        if (
                            request.min_value_satoshis is not None
                            and out.value_satoshis < request.min_value_satoshis
                        ):
                            continue
                        is_out_direction = (
                            any(inp.address == request.address for inp in _rpc_tx.inputs)
                        )
                        is_in_direction = out.address == request.address
                        relevant = (
                            (request.direction == "out" and is_out_direction)
                            or (request.direction == "in" and is_in_direction)
                            or request.direction == "both"
                        )
                        if not relevant:
                            continue
                        records.append(
                            {
                                "tx_hash": _rpc_tx.hash,
                                "block_number": _rpc_tx.block_number,
                                "tx_ts": (
                                    _rpc_tx.timestamp.isoformat()
                                    if _rpc_tx.timestamp
                                    else None
                                ),
                                "is_coinjoin": _rpc_tx.is_coinjoin,
                                "fee": _rpc_tx.fee,
                                "output_address": out.address,
                                "output_index": out.output_index,
                                "value_satoshis": out.value_satoshis,
                                "script_type": out.script_type,
                                "is_probable_change": out.is_probable_change,
                                "direction": "out" if is_out_direction else "in",
                            }
                        )
            except (OSError, ValueError, TimeoutError) as exc:
                logger.warning(f"UTXO expand RPC fallback failed: {exc}")

    # ------------------------------------------------------------------
    # Build response nodes and edges from records.
    # Group records by tx_hash so each transaction becomes one (or zero)
    # transaction node and N UTXONodes (one per output).
    # ------------------------------------------------------------------
    new_nodes: List[Dict[str, Any]] = []
    new_edges: List[Dict[str, Any]] = []
    seen_tx_hashes: set = set()
    seen_utxo_ids: set = set()
    total_value_sat: int = 0

    for rec in records:
        tx_hash = rec.get("tx_hash") or ""
        if not tx_hash:
            continue

        # Transaction node (one per tx_hash)
        if tx_hash not in seen_tx_hashes:
            seen_tx_hashes.add(tx_hash)
            is_coinjoin = bool(rec.get("is_coinjoin"))
            tx_node_id = f"bitcoin:tx:{tx_hash}"
            tx_node: Dict[str, Any] = {
                "node_id": tx_node_id,
                "node_type": "transaction",
                "tx_hash": tx_hash,
                "blockchain": "bitcoin",
                "block_number": rec.get("block_number"),
                "timestamp": rec.get("tx_ts"),
                "fee": rec.get("fee"),
                "is_coinjoin": is_coinjoin,
                "is_coinjoin_halt": is_coinjoin,
                "depth": depth,
                "parent_id": parent_node_id,
                "branch_id": branch_id,
                "path_id": path_id_base,
            }
            new_nodes.append(tx_node)

            # Edge: seed address → transaction
            new_edges.append(
                {
                    "edge_id": f"sent:{request.address}:{tx_hash}",
                    "edge_type": "sent",
                    "source_node_id": parent_node_id,
                    "target_node_id": tx_node_id,
                    "blockchain": "bitcoin",
                    "depth": depth,
                    "branch_id": branch_id,
                }
            )

        # UTXONode for this output (skip if CoinJoin — halt, don't expand)
        if bool(rec.get("is_coinjoin")):
            continue

        output_index = rec.get("output_index")
        output_address = rec.get("output_address") or ""
        value_sat = int(rec.get("value_satoshis") or 0)
        if output_index is None or not output_address:
            continue

        utxo_node_id = f"bitcoin:utxo:{tx_hash}:{output_index}"
        if utxo_node_id in seen_utxo_ids:
            continue
        seen_utxo_ids.add(utxo_node_id)

        is_probable_change = bool(rec.get("is_probable_change"))
        script_type = rec.get("script_type") or "unknown"
        total_value_sat += value_sat

        utxo_node: Dict[str, Any] = {
            "node_id": utxo_node_id,
            "node_type": "utxo",
            "tx_hash": tx_hash,
            "output_index": output_index,
            "blockchain": "bitcoin",
            "address": output_address,
            "value_satoshis": value_sat,
            "value_btc": round(value_sat / 1e8, 8),
            "script_type": script_type,
            "is_probable_change": is_probable_change,
            "depth": depth + 1,
            "parent_id": f"bitcoin:tx:{tx_hash}",
            "branch_id": branch_id,
            "path_id": path_id_base,
        }
        new_nodes.append(utxo_node)

        # Address node for the output recipient
        addr_node_id = f"bitcoin:{output_address}"
        new_nodes.append(
            {
                "node_id": addr_node_id,
                "node_type": "address",
                "address": output_address,
                "blockchain": "bitcoin",
                "depth": depth + 2,
                "parent_id": utxo_node_id,
                "branch_id": branch_id,
                "path_id": path_id_base,
                "is_probable_change_recipient": is_probable_change,
            }
        )

        # Edge: transaction → UTXO
        new_edges.append(
            {
                "edge_id": f"utxo_out:{tx_hash}:{output_index}",
                "edge_type": "utxo_output",
                "source_node_id": f"bitcoin:tx:{tx_hash}",
                "target_node_id": utxo_node_id,
                "value_satoshis": value_sat,
                "script_type": script_type,
                "is_probable_change": is_probable_change,
                "depth": depth + 1,
                "branch_id": branch_id,
            }
        )

        # Edge: UTXO → address
        new_edges.append(
            {
                "edge_id": f"utxo_addr:{tx_hash}:{output_index}:{output_address}",
                "edge_type": "utxo_to_address",
                "source_node_id": utxo_node_id,
                "target_node_id": addr_node_id,
                "value_satoshis": value_sat,
                "depth": depth + 2,
                "branch_id": branch_id,
            }
        )

    coinjoin_tx_count = sum(
        1 for n in new_nodes if n.get("node_type") == "transaction" and n.get("is_coinjoin")
    )

    # Enrich with fiat values — use the earliest record timestamp if available.
    _first_ts_str = next(
        (r.get("tx_ts") for r in records if r.get("tx_ts")), None
    )
    try:
        tx_timestamp = (
            datetime.fromisoformat(_first_ts_str) if _first_ts_str else datetime.now(timezone.utc)
        )
    except (ValueError, TypeError):
        tx_timestamp = datetime.now(timezone.utc)
    try:
        oracle = get_edge_price_oracle()
        await oracle.enrich_edge_fiat_values(
            new_edges, "bitcoin", tx_timestamp
        )
    except Exception as exc:
        logger.warning(f"[graph] Bitcoin price oracle enrichment failed: {exc}, tx_timestamp={tx_timestamp}, edges={len(new_edges)}")
        # Continue without fiat enrichment

    return ExpansionResponse(
        operation_id=str(uuid4()),
        operation_type="expand_utxo",
        parent_node_id=parent_node_id,
        branch_id=branch_id,
        insertion_depth=depth,
        new_nodes=new_nodes,
        new_edges=new_edges,
        expansion_metadata={
            "tx_count": len(seen_tx_hashes),
            "utxo_count": len(seen_utxo_ids),
            "coinjoin_halt_count": coinjoin_tx_count,
            "total_value_satoshis": total_value_sat,
            "total_value_btc": round(total_value_sat / 1e8, 8),
        },
        asset_context={
            "assets_present": ["BTC"],
            "dominant_asset": "BTC",
            "total_value_satoshis": total_value_sat,
        },
        timestamp=datetime.now(timezone.utc),
    )


# =============================================================================
# Helpers
# =============================================================================


def _make_address_node(address: str, blockchain: str) -> Dict[str, Any]:
    """Create a default address node dict."""
    return {
        "id": address,
        "type": "address",
        "chain": blockchain,
        "label": None,
        "risk": 0.0,
        "sanctioned": False,
        "entity_name": None,
        "entity_type": None,
        "entity_category": None,
        "balance": None,
        "tx_count": None,
    }


async def _enrich_entities(
    nodes_map: Dict[str, Dict[str, Any]], blockchain: str
) -> None:
    """Best-effort: tag nodes with entity attribution labels."""
    addresses = list(nodes_map.keys())
    if not addresses:
        return
    try:
        results = await lookup_addresses_bulk(addresses, blockchain)
        entity_risk = {"low": 0.2, "medium": 0.4, "high": 0.7, "critical": 0.9}
        for addr, info in results.items():
            if info and addr in nodes_map:
                node = nodes_map[addr]
                node["entity_name"] = info.get("entity_name")
                node["entity_type"] = info.get("entity_type")
                node["entity_category"] = info.get("category")
                if not node.get("label"):
                    node["label"] = info.get("entity_name")
                r = entity_risk.get(info.get("risk_level"), 0)
                if r > node.get("risk", 0):
                    node["risk"] = r
    except Exception:
        pass  # entity DB may not be initialised yet


async def _enrich_sanctions(
    nodes_map: Dict[str, Dict[str, Any]], blockchain: str
) -> None:
    """Best-effort: tag nodes that appear in the sanctions database."""
    for addr, node in nodes_map.items():
        try:
            result = await screen_address(addr, blockchain)
            if result and result.get("matched"):
                node["sanctioned"] = True
                node["label"] = node.get("label") or "sanctioned"
        except Exception:
            pass  # sanctions DB may not be initialised yet


def _safe_float(val: Any) -> float:
    """Convert a value to float, returning 0.0 on failure."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


# Lazily-initialised address sets for edge classification
_bridge_addresses: Optional[set] = None
_mixer_addresses: Optional[set] = None
_dex_addresses: Optional[set] = None


def _get_known_bridge_addresses() -> set:
    global _bridge_addresses
    if _bridge_addresses is None:
        _bridge_addresses = load_known_bridge_addresses()
    return _bridge_addresses


def _get_known_mixer_addresses() -> set:
    global _mixer_addresses
    if _mixer_addresses is None:
        _mixer_addresses = load_known_mixer_addresses()
    return _mixer_addresses


def _get_known_dex_addresses() -> set:
    global _dex_addresses
    if _dex_addresses is None:
        _dex_addresses = load_known_dex_addresses()
    return _dex_addresses


def _classify_edge(source: str, target: str) -> str:
    """Classify an edge as bridge, mixer, dex, or transfer.

    Returns one of: 'bridge', 'mixer', 'dex', 'transfer'
    """
    src = (source or "").lower()
    tgt = (target or "").lower()
    bridge_addrs = _get_known_bridge_addresses()
    mixer_addrs = _get_known_mixer_addresses()

    if src in bridge_addrs or tgt in bridge_addrs:
        return "bridge"
    if src in mixer_addrs or tgt in mixer_addrs:
        return "mixer"
    dex_addrs = _get_known_dex_addresses()
    if src in dex_addrs or tgt in dex_addrs:
        return "dex"
    return "transfer"


# =============================================================================
# Investigation session endpoints (ExpansionResponse v2 — Phase 3)
# =============================================================================

# TraceCompiler singleton — initialised lazily so tests can override it.
_trace_compiler = None
_trace_compiler_lock = asyncio.Lock()


async def _get_trace_compiler():
    """Return the singleton TraceCompiler, constructing it on first call.

    The compiler is injected with the Neo4j *read* driver (T7.1) so that
    investigation-graph expansions are routed to the read replica when
    ``NEO4J_READ_URI`` is configured.
    """
    global _trace_compiler
    if _trace_compiler is None:
        async with _trace_compiler_lock:
            if _trace_compiler is None:
                from src.trace_compiler.compiler import TraceCompiler
                from src.api.database import get_neo4j_read_driver, get_postgres_pool, get_redis_client
                try:
                    neo4j = get_neo4j_read_driver()
                    pg = get_postgres_pool()
                    redis = get_redis_client()
                except RuntimeError:
                    # Databases not yet initialised (e.g. test environment).
                    neo4j = None
                    pg = None
                    redis = None
                _trace_compiler = TraceCompiler(neo4j_driver=neo4j, postgres_pool=pg, redis_client=redis)
    return _trace_compiler


from src.trace_compiler.models import (  # noqa: E402
    AssetCatalogItem,
    AssetCatalogResponse,
    AssetOptionsRequest,
    AssetOptionsResponse,
    BridgeHopStatusResponse,
    ExpandRequest,
    ExpansionResponseV2,
    IngestStatusResponse,
    InvestigationSessionResponse,
    RecentSessionsResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionSnapshotRequest,
    SessionSnapshotResponse,
    TxResolveResponse,
    WorkspaceSnapshotV1,
)

# Native asset symbol per blockchain — used by the tx resolve endpoint.
_NATIVE_ASSET: dict[str, str] = {
    "ethereum": "ETH",
    "bsc": "BNB",
    "polygon": "MATIC",
    "arbitrum": "ETH",
    "base": "ETH",
    "avalanche": "AVAX",
    "optimism": "ETH",
    "starknet": "ETH",
    "injective": "INJ",
    "tron": "TRX",
    "solana": "SOL",
    "xrp": "XRP",
    "cosmos": "ATOM",
    "sui": "SUI",
    "bitcoin": "BTC",
    "litecoin": "LTC",
    "bitcoin_cash": "BCH",
    "dogecoin": "DOGE",
    "lightning": "BTC",
}

_NATIVE_CANONICAL_ASSET_ID: dict[str, str] = {
    "ethereum": "ethereum",
    "bsc": "binancecoin",
    "polygon": "matic-network",
    "arbitrum": "ethereum",
    "base": "ethereum",
    "avalanche": "avalanche-2",
    "optimism": "ethereum",
    "starknet": "ethereum",
    "injective": "injective-protocol",
    "tron": "tron",
    "solana": "solana",
    "xrp": "ripple",
    "cosmos": "cosmos",
    "sui": "sui",
    "bitcoin": "btc",
    "litecoin": "ltc",
    "bitcoin_cash": "bch",
    "dogecoin": "doge",
    "lightning": "btc",
}


def _normalize_asset_catalog_chains(
    requested_chains: list[str],
    *,
    seed_chain: str,
) -> list[str]:
    supported = set(get_supported_blockchains())
    normalized: list[str] = []
    for chain in requested_chains:
        value = (chain or "").strip().lower()
        if value and value in supported and value not in normalized:
            normalized.append(value)
    if not normalized and seed_chain in supported:
        normalized.append(seed_chain)
    return normalized


def _asset_catalog_key(
    *,
    symbol: Optional[str],
    canonical_asset_id: Optional[str],
    blockchain: str,
    asset_address: Optional[str],
    is_native: bool = False,
) -> str:
    if is_native and symbol:
        return symbol.upper()
    if canonical_asset_id:
        return canonical_asset_id.lower()
    if symbol:
        return symbol.upper()
    if asset_address:
        return f"{blockchain}:{asset_address}"
    return blockchain


def _identity_status_rank(value: Optional[str]) -> int:
    """Return a stable rank for canonical asset identity confidence."""
    if value == "verified":
        return 2
    if value == "heuristic":
        return 1
    return 0


def _asset_variant_rank(value: Optional[str]) -> int:
    """Return a stable display rank for asset variants."""
    order = {
        "native": 4,
        "canonical": 3,
        "wrapped": 2,
        "bridged": 1,
        "unknown": 0,
    }
    return order.get(str(value or "").strip().lower(), 0)


def _asset_catalog_sort_key(item: "AssetCatalogItem") -> tuple:
    """Prefer verified/core assets while pushing one-off unknowns to the back."""
    last_seen = item.last_seen_at
    if isinstance(last_seen, datetime):
        recency_score = last_seen.timestamp()
    else:
        recency_score = 0.0
    long_tail_unknown = (
        item.identity_status == "unknown"
        and item.observed_transfer_count <= 2
    )
    return (
        1 if long_tail_unknown else 0,
        -_asset_variant_rank(item.variant_kind),
        -_identity_status_rank(item.identity_status),
        -min(len(item.blockchains), 4),
        -item.observed_transfer_count,
        -recency_score,
        item.symbol.lower(),
        item.asset_key,
    )


@router.get("/resolve-tx", response_model=TxResolveResponse)
async def resolve_transaction(
    chain: str = Query(..., description="Blockchain name (e.g. 'ethereum', 'bsc')"),
    tx: str = Query(..., description="Transaction hash to resolve"),
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Resolve a transaction hash to its participant addresses.

    Useful for seeding an investigation from a transaction rather than an
    address.  The caller can then create a session from ``from_address`` or
    ``to_address`` depending on which party they want to investigate.

    Resolution order:
    1. ``raw_transactions`` table (instant, offline).
    2. Chain RPC client ``get_transaction()`` (live, may be slow).

    Returns ``found=False`` when neither source can locate the transaction.
    """
    # Normalise: strip whitespace, lowercase.
    tx_hash = tx.strip().lower()
    chain = chain.strip().lower()
    # EVM hashes are stored with a 0x prefix; UTXO/Solana hashes are bare hex,
    # so only add the prefix when the chain is an EVM-family chain.
    _EVM_CHAINS = {
        "ethereum", "polygon", "bsc", "arbitrum", "base",
        "avalanche", "optimism", "starknet", "injective",
    }
    if (
        chain in _EVM_CHAINS
        and len(tx_hash) == 64
        and all(c in '0123456789abcdef' for c in tx_hash)
    ):
        tx_hash = '0x' + tx_hash

    # Try the event store first — fast and works offline.
    row = None
    try:
        pg = get_postgres_pool()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT tx_hash, from_address, to_address,
                       value_native, timestamp, block_number, status
                FROM raw_transactions
                WHERE blockchain = $1 AND tx_hash = $2
                LIMIT 1
                """,
                chain,
                tx_hash,
            )
    except Exception as exc:
        logger.warning("resolve_transaction: DB lookup failed for %s/%s: %s", chain, tx_hash, exc)

    if row is not None:
        return TxResolveResponse(
            found=True,
            tx_hash=row["tx_hash"],
            blockchain=chain,
            from_address=row["from_address"],
            to_address=row["to_address"],
            value_native=row["value_native"],
            asset_symbol=_NATIVE_ASSET.get(chain),
            timestamp=row["timestamp"],
            block_number=row["block_number"],
            status=row["status"],
        )

    # Fall back to live RPC lookup.
    try:
        rpc = get_rpc_client(chain)
        if rpc is not None:
            tx_obj = await rpc.get_transaction(tx_hash)
            if tx_obj is not None:
                return TxResolveResponse(
                    found=True,
                    tx_hash=tx_obj.hash,
                    blockchain=chain,
                    from_address=tx_obj.from_address,
                    to_address=tx_obj.to_address,
                    value_native=float(tx_obj.value) if tx_obj.value is not None else None,
                    asset_symbol=_NATIVE_ASSET.get(chain),
                    timestamp=tx_obj.timestamp,
                    block_number=tx_obj.block_number,
                    status=tx_obj.status,
                )
    except Exception as exc:
        logger.warning("resolve_transaction: RPC lookup failed for %s/%s: %s", chain, tx_hash, exc)

    return TxResolveResponse(found=False, tx_hash=tx_hash, blockchain=chain)


@router.post("/sessions", response_model=SessionCreateResponse)
async def create_investigation_session(
    request: SessionCreateRequest,
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Create a new investigation session seeded from an address.

    Returns a session_id and the root ``InvestigationNode`` for the seed
    address.  All subsequent expansion calls must reference this session_id.
    """
    from src.trace_compiler.compiler import SessionPersistenceError

    compiler = await _get_trace_compiler()
    try:
        return await compiler.create_session(request, owner_user_id=str(current_user.id))
    except SessionPersistenceError as exc:
        raise HTTPException(status_code=503, detail="Session store unavailable") from exc


@router.get("/sessions/recent", response_model=RecentSessionsResponse)
async def list_recent_investigation_sessions(
    limit: int = Query(default=5, ge=1, le=10),
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """List recent backend-owned sessions for restore discovery."""
    session_store = _get_graph_session_store()
    try:
        items = await session_store.list_recent_sessions(
            owner_user_id=str(current_user.id),
            limit=limit,
        )
    except Exception as exc:
        logger.warning(
            "Failed to list recent sessions for %s: %s",
            current_user.username,
            exc,
        )
        raise HTTPException(status_code=503, detail="Session store unavailable") from exc

    return RecentSessionsResponse(items=items)


@router.get("/sessions/{session_id}", response_model=InvestigationSessionResponse)
async def get_investigation_session(
    session_id: str,
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Restore a saved investigation session snapshot.

    Returns the authoritative server-backed workspace snapshot when one exists.
    Legacy rows without a full workspace snapshot are normalized into a
    root-only bootstrap payload with ``restore_state=legacy_bootstrap``.
    """
    row = await _get_owned_session_row(session_id, current_user)
    session_store = _get_graph_session_store()
    workspace, restore_state, raw_snapshot = session_store.normalize_workspace(row)
    branch_map = {
        branch.branchId: branch
        for branch in (workspace.branches or [])
    }

    return {
        "session_id": str(row["session_id"]),
        "seed_address": row.get("seed_address"),
        "seed_chain": row.get("seed_chain"),
        "case_id": row.get("case_id"),
        "snapshot": raw_snapshot,
        "workspace": workspace,
        "restore_state": restore_state,
        "nodes": workspace.nodes,
        "edges": workspace.edges,
        "branch_map": branch_map,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "snapshot_saved_at": row.get("snapshot_saved_at"),
    }


@router.post("/sessions/{session_id}/snapshot", response_model=SessionSnapshotResponse)
async def save_session_snapshot(
    session_id: str,
    session_snapshot: SessionSnapshotRequest,
    current_user: User = Depends(check_permissions([PERMISSIONS["write_blockchain"]])),
):
    """Persist a frontend session snapshot (node positions, filters, UI state).

    Writes the serialised ``node_states`` list to the ``snapshot`` JSONB column
    in ``graph_sessions``.  The session row must already exist (created by
    ``POST /sessions``); storage failures are surfaced as ``503`` so the
    frontend never receives a false success signal.
    """
    row = await _get_owned_session_row(session_id, current_user)
    session_store = _get_graph_session_store()
    current_workspace, _, _ = session_store.normalize_workspace(row)

    saved_at = datetime.now(timezone.utc)
    snapshot_id = str(uuid4())

    try:
        if session_snapshot.has_workspace_payload():
            try:
                workspace = session_snapshot.to_workspace_snapshot()
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if workspace.sessionId != session_id:
                raise HTTPException(
                    status_code=400,
                    detail="Snapshot sessionId does not match session_id path parameter",
                )
            if workspace.revision <= current_workspace.revision:
                raise HTTPException(
                    status_code=409,
                    detail="Stale workspace snapshot revision",
                )
        else:
            workspace = session_store.merge_node_states(current_workspace, session_snapshot.node_states)
            workspace = workspace.model_copy(update={"revision": current_workspace.revision + 1})

        await session_store.save_workspace_snapshot(
            session_id=session_id,
            owner_user_id=str(current_user.id),
            workspace=workspace,
            saved_at=saved_at,
            expected_previous_revision=current_workspace.revision,
        )
        logger.debug("Snapshot saved for session %s (%d nodes)", session_id, len(workspace.nodes))
    except Exception as exc:
        if isinstance(exc, SnapshotRevisionConflictError):
            raise HTTPException(status_code=409, detail="Stale workspace snapshot revision") from exc
        if isinstance(exc, HTTPException):
            raise
        logger.warning("Failed to save snapshot for session %s: %s", session_id, exc)
        raise HTTPException(status_code=503, detail="Session store unavailable") from exc

    return SessionSnapshotResponse(
        snapshot_id=snapshot_id,
        saved_at=saved_at,
        revision=workspace.revision,
    )


@router.post("/sessions/{session_id}/expand", response_model=ExpansionResponseV2)
async def expand_session_node(
    session_id: str,
    request: ExpandRequest,
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Expand a node within an investigation session.

    All operation types (expand_next, expand_prev, expand_neighbors,
    expand_bridge, expand_utxo, expand_solana_tx) are routed through this
    single endpoint.  The trace compiler dispatches to the appropriate
    chain-specific compiler based on the seed node_id prefix.
    """
    await _get_owned_session_row(session_id, current_user)
    _validate_expand_request(request)
    compiler = await _get_trace_compiler()
    return await compiler.expand(session_id, request)


@router.post("/sessions/{session_id}/asset-options", response_model=AssetOptionsResponse)
async def list_session_asset_options(
    session_id: str,
    request: AssetOptionsRequest,
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Return address-level asset options for selective expansion."""
    await _get_owned_session_row(session_id, current_user)
    compiler = await _get_trace_compiler()
    return await compiler.get_asset_options(session_id, request)


@router.get(
    "/sessions/{session_id}/hops/{hop_id}/status",
    response_model=BridgeHopStatusResponse,
)
async def get_bridge_hop_status(
    session_id: str,
    hop_id: str,
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Poll the resolution status of a pending bridge hop.

    The frontend should call this every 30 seconds for any BridgeHop node
    with status="pending".  When status changes to "completed", the frontend
    can call expand on the BridgeHop node to follow funds to the destination
    chain.
    """
    await _get_owned_session_row(session_id, current_user)
    compiler = await _get_trace_compiler()
    if not await compiler.is_bridge_hop_allowed(session_id, hop_id):
        raise HTTPException(status_code=404, detail="Bridge hop not found")
    return await compiler.get_bridge_hop_status(session_id, hop_id)


@router.get(
    "/sessions/{session_id}/ingest/status",
    response_model=IngestStatusResponse,
)
async def get_session_ingest_status(
    session_id: str,
    address: str = Query(..., description="Blockchain address to check ingest status for"),
    chain: str = Query(..., description="Blockchain name (e.g. 'ethereum', 'tron')"),
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Poll the background ingest job status for a specific address.

    Called by the frontend every 5 seconds when expansion returns
    ``ingest_pending=True``.  When ``status`` transitions to ``"completed"``
    the frontend retries the expansion to load newly-ingested activity.

    The endpoint inherits session ownership auth via ``_get_owned_session_row``,
    so an address is only queryable within a session that belongs to the
    authenticated user.  Status ``"not_found"`` is returned when no queue row
    exists (yet) for the address, which is safe to treat as still-pending.
    """
    await _get_owned_session_row(session_id, current_user)

    try:
        pg = get_postgres_pool()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT address, blockchain, status, requested_at,
                       started_at, completed_at, tx_count, error
                FROM address_ingest_queue
                WHERE address = $1 AND blockchain = $2
                ORDER BY requested_at DESC
                LIMIT 1
                """,
                address,
                chain,
            )
    except Exception as exc:
        logger.warning(
            "Failed to query ingest status for %s/%s: %s",
            chain,
            address,
            exc,
        )
        raise HTTPException(status_code=503, detail="Ingest status lookup unavailable") from exc

    if row is None:
        return IngestStatusResponse(address=address, blockchain=chain, status="not_found")

    return IngestStatusResponse(
        address=row["address"],
        blockchain=row["blockchain"],
        status=row["status"],
        queued_at=row["requested_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        tx_count=row["tx_count"],
        error=row["error"],
    )


@router.get(
    "/sessions/{session_id}/assets",
    response_model=AssetCatalogResponse,
)
async def get_session_asset_catalog(
    session_id: str,
    chains: List[str] = Query(
        default=[],
        description="Optional chain scope for the asset picker. Defaults to the session seed chain.",
    ),
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Return a session-scoped asset catalog for the explorer filter panel."""
    row = await _get_owned_session_row(session_id, current_user)
    seed_chain = str(row.get("seed_chain") or "").strip().lower()
    target_chains = _normalize_asset_catalog_chains(chains, seed_chain=seed_chain)
    if not target_chains:
        return AssetCatalogResponse(
            session_id=session_id,
            seed_chain=seed_chain,
            chains_present=[],
            items=[],
            generated_at=datetime.now(timezone.utc),
        )

    try:
        pg = get_postgres_pool()
        async with pg.acquire() as conn:
            token_rows = await conn.fetch(
                """
                SELECT
                    rt.blockchain,
                    rt.asset_contract AS asset_address,
                    COALESCE(
                        NULLIF(tmc.symbol, ''),
                        NULLIF(rt.asset_symbol, ''),
                        rt.asset_contract
                    ) AS symbol,
                    COALESCE(
                        NULLIF(tmc.name, ''),
                        NULLIF(rt.asset_symbol, ''),
                        rt.asset_contract
                    ) AS display_name,
                    COALESCE(
                        NULLIF(tmc.canonical_asset_id, ''),
                        NULLIF(rt.canonical_asset_id, '')
                    ) AS canonical_asset_id,
                    COALESCE(
                        NULLIF(tmc.token_standard, ''),
                        'token'
                    ) AS token_standard,
                    COUNT(*)::BIGINT AS observed_transfer_count,
                    MAX(rt.timestamp) AS last_seen_at
                FROM raw_token_transfers rt
                LEFT JOIN token_metadata_cache tmc
                  ON tmc.blockchain = rt.blockchain
                 AND tmc.asset_address = rt.asset_contract
                WHERE rt.blockchain = ANY($1::text[])
                  AND rt.asset_contract IS NOT NULL
                  AND rt.asset_contract <> ''
                GROUP BY
                    rt.blockchain,
                    rt.asset_contract,
                    COALESCE(
                        NULLIF(tmc.symbol, ''),
                        NULLIF(rt.asset_symbol, ''),
                        rt.asset_contract
                    ),
                    COALESCE(
                        NULLIF(tmc.name, ''),
                        NULLIF(rt.asset_symbol, ''),
                        rt.asset_contract
                    ),
                    COALESCE(
                        NULLIF(tmc.canonical_asset_id, ''),
                        NULLIF(rt.canonical_asset_id, '')
                    ),
                    COALESCE(
                        NULLIF(tmc.token_standard, ''),
                        'token'
                    )
                ORDER BY observed_transfer_count DESC, last_seen_at DESC
                LIMIT $2
                """,
                target_chains,
                max(50, settings.TOKEN_METADATA_ASSET_CATALOG_LIMIT * 3),
            )
            native_rows = await conn.fetch(
                """
                SELECT
                    blockchain,
                    COUNT(*)::BIGINT AS observed_transfer_count,
                    MAX(timestamp) AS last_seen_at
                FROM raw_transactions
                WHERE blockchain = ANY($1::text[])
                  AND value_native IS NOT NULL
                  AND value_native > 0
                GROUP BY blockchain
                ORDER BY observed_transfer_count DESC, last_seen_at DESC
                """,
                target_chains,
            )
    except Exception as exc:
        logger.warning(
            "Failed to build asset catalog for session %s (%s): %s",
            session_id,
            target_chains,
            exc,
        )
        raise HTTPException(status_code=503, detail="Asset catalog unavailable") from exc

    items_by_key: dict[str, AssetCatalogItem] = {}
    asset_address_aliases: dict[str, str] = {}

    for raw in token_rows:
        blockchain = str(raw["blockchain"])
        symbol = str(raw["symbol"] or "").strip()
        canonical_asset_id = raw["canonical_asset_id"]
        asset_address = raw["asset_address"]
        identity = resolve_canonical_asset_identity(
            blockchain=blockchain,
            asset_address=asset_address,
            symbol=symbol,
            name=raw["display_name"],
            token_standard=raw["token_standard"],
        )
        resolved_canonical_asset_id = (
            identity.canonical_asset_id or canonical_asset_id
        )
        asset_address_key = (
            f"{blockchain}:{str(asset_address).lower()}"
            if asset_address is not None
            else None
        )
        asset_key = build_asset_selector_key(
            blockchain=blockchain,
            asset_address=asset_address,
            symbol=symbol or identity.canonical_symbol,
            canonical_asset_id=resolved_canonical_asset_id,
            identity_status=identity.identity_status,
            variant_kind=identity.variant_kind,
        )
        if asset_address_key and asset_address_key in asset_address_aliases:
            asset_key = asset_address_aliases[asset_address_key]
        existing = items_by_key.get(asset_key)
        if existing is None:
            existing = AssetCatalogItem(
                asset_key=asset_key,
                symbol=symbol or identity.canonical_symbol or str(asset_address),
                display_name=raw["display_name"] or identity.canonical_name,
                canonical_asset_id=resolved_canonical_asset_id,
                canonical_symbol=identity.canonical_symbol,
                identity_status=identity.identity_status,
                variant_kind=identity.variant_kind,
                blockchains=[],
                token_standards=[],
                observed_transfer_count=0,
                last_seen_at=raw["last_seen_at"],
                sample_asset_address=asset_address,
                is_native=False,
            )
            items_by_key[asset_key] = existing
        if asset_address_key:
            asset_address_aliases[asset_address_key] = asset_key

        if blockchain not in existing.blockchains:
            existing.blockchains.append(blockchain)
        token_standard = str(raw["token_standard"] or "").strip()
        if token_standard and token_standard not in existing.token_standards:
            existing.token_standards.append(token_standard)
        existing.observed_transfer_count += int(raw["observed_transfer_count"] or 0)
        if existing.display_name in (None, "", existing.sample_asset_address):
            existing.display_name = raw["display_name"] or identity.canonical_name
        if existing.sample_asset_address is None:
            existing.sample_asset_address = asset_address
        if existing.canonical_asset_id is None:
            existing.canonical_asset_id = resolved_canonical_asset_id
        if existing.canonical_symbol is None:
            existing.canonical_symbol = identity.canonical_symbol
        if _identity_status_rank(identity.identity_status) > _identity_status_rank(
            existing.identity_status
        ):
            existing.identity_status = identity.identity_status
            existing.variant_kind = identity.variant_kind
        if existing.last_seen_at is None or (
            raw["last_seen_at"] is not None and raw["last_seen_at"] > existing.last_seen_at
        ):
            existing.last_seen_at = raw["last_seen_at"]

    for raw in native_rows:
        blockchain = str(raw["blockchain"])
        identity = native_asset_identity(blockchain)
        symbol = identity.canonical_symbol or _NATIVE_ASSET.get(blockchain)
        if not symbol or identity.canonical_asset_id is None:
            continue
        asset_key = build_asset_selector_key(
            blockchain=blockchain,
            asset_address=None,
            symbol=symbol,
            canonical_asset_id=identity.canonical_asset_id,
            identity_status=identity.identity_status,
            variant_kind=identity.variant_kind,
            is_native=True,
        )
        existing = items_by_key.get(asset_key)
        if existing is None:
            existing = AssetCatalogItem(
                asset_key=asset_key,
                symbol=symbol,
                display_name=identity.canonical_name or f"{symbol} native asset",
                canonical_asset_id=identity.canonical_asset_id,
                canonical_symbol=identity.canonical_symbol,
                identity_status=identity.identity_status,
                variant_kind=identity.variant_kind,
                blockchains=[blockchain],
                token_standards=["native"],
                observed_transfer_count=int(raw["observed_transfer_count"] or 0),
                last_seen_at=raw["last_seen_at"],
                sample_asset_address=None,
                is_native=True,
            )
            items_by_key[asset_key] = existing
            continue

        if blockchain not in existing.blockchains:
            existing.blockchains.append(blockchain)
        if "native" not in existing.token_standards:
            existing.token_standards.append("native")
        existing.observed_transfer_count += int(raw["observed_transfer_count"] or 0)
        if existing.last_seen_at is None or (
            raw["last_seen_at"] is not None and raw["last_seen_at"] > existing.last_seen_at
        ):
            existing.last_seen_at = raw["last_seen_at"]
        if existing.canonical_asset_id is None:
            existing.canonical_asset_id = identity.canonical_asset_id
        if existing.canonical_symbol is None:
            existing.canonical_symbol = identity.canonical_symbol

    items = sorted(
        items_by_key.values(),
        key=_asset_catalog_sort_key,
    )[: settings.TOKEN_METADATA_ASSET_CATALOG_LIMIT]

    return AssetCatalogResponse(
        session_id=session_id,
        seed_chain=seed_chain,
        chains_present=target_chains,
        items=items,
        generated_at=datetime.now(timezone.utc),
    )


# =============================================================================
# Latency metrics endpoint (T0.1)
# =============================================================================


@router.get("/latency")
async def graph_latency_metrics(
    current_user: User = Depends(check_permissions([PERMISSIONS["read_analysis"]])),
):
    """Return p50/p95/p99 latency statistics for all /graph/* endpoints.

    Statistics are derived from a rolling 1-hour window of samples stored in
    Redis by ``GraphLatencyMiddleware``.  Each endpoint label maps to a dict
    containing ``p50_ms``, ``p95_ms``, ``p99_ms``, ``mean_ms``, and
    ``sample_count``.

    Returns an empty ``endpoints`` dict if no samples have been recorded yet
    (e.g., immediately after service startup or when Redis is unavailable).
    """
    if not settings.EXPOSE_METRICS:
        raise HTTPException(status_code=404, detail="Not found")

    stats = await get_graph_latency_stats()
    return {
        "success": True,
        "endpoints": stats,
        "window_seconds": 3600,
    }
