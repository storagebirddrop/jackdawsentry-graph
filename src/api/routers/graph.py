# Copyright (c) 2024 DAWGUS. All rights reserved.
# This file is proprietary and confidential. Unauthorized use is prohibited.

"""
Jackdaw Sentry - Transaction Graph Router (M9.2)
Returns {nodes, edges} JSON for the frontend Cytoscape.js graph renderer.
Supports address expansion, transaction tracing, search, and clustering.
"""

import asyncio
import logging
import time
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from pydantic import BaseModel
from pydantic import field_validator

from src.analysis.bridge_tracker import BridgeTracker
from src.analysis.mixer_detection import MixerDetector
from src.api.auth import PERMISSIONS
from src.api.auth import User
from src.api.auth import check_permissions
from src.api.middleware import get_graph_latency_stats
from src.api.config import get_supported_blockchains
import hashlib
import json

from src.api.database import cache_get
from src.api.database import cache_set
from src.api.database import get_neo4j_read_session
from src.api.database import get_neo4j_session
from src.api.database import get_postgres_pool
from src.collectors.rpc.factory import get_rpc_client
from src.services.entity_attribution import lookup_addresses_bulk as _entity_lookup_bulk
from src.services.price_oracle import get_price_oracle
from src.services.sanctions import screen_address as _sanctions_screen

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_GRAPH_NODES = 500
MAX_DEPTH = 5


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


@router.post("/expand", response_model=GraphResponse)
async def expand_address(
    request: GraphExpandRequest,
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Expand an address node: return its direct neighbors and connecting edges.

    Uses Neo4j variable-length path queries bounded by depth.
    Falls back to live RPC if address is not in Neo4j.
    """
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
        oracle = get_price_oracle()
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


@router.post("/trace", response_model=GraphResponse)
async def trace_transaction(
    request: GraphTraceRequest,
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Trace a transaction: return the full flow from source through hops to destination.

    Uses Neo4j variable-length paths to follow fund flow.
    """
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


@router.post("/search", response_model=GraphResponse)
async def graph_search(
    request: GraphSearchRequest,
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Search for an address or transaction hash; return the initial graph node(s)."""
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


@router.post("/cluster", response_model=GraphResponse)
async def cluster_addresses(
    request: GraphClusterRequest,
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Find common counterparties and shared transaction patterns for a set of addresses."""
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


@router.post("/expand-solana-tx", response_model=ExpansionResponse)
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
        oracle = get_price_oracle()
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


@router.post("/expand-bridge", response_model=ExpansionResponse)
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


@router.post("/expand-utxo", response_model=ExpansionResponse)
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
        oracle = get_price_oracle()
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
        results = await _entity_lookup_bulk(addresses, blockchain)
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
            result = await _sanctions_screen(addr, blockchain)
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


def _get_known_bridge_addresses() -> set:
    global _bridge_addresses
    if _bridge_addresses is None:
        from src.analysis.protocol_registry import (
            get_known_bridge_addresses as _reg_bridges,
        )

        _bridge_addresses = _reg_bridges()
    return _bridge_addresses


def _get_known_mixer_addresses() -> set:
    global _mixer_addresses
    if _mixer_addresses is None:
        from src.analysis.protocol_registry import (
            get_known_mixer_addresses as _reg_mixers,
        )

        _mixer_addresses = _reg_mixers()
    return _mixer_addresses


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
    # DEX check via protocol registry
    from src.analysis.protocol_registry import get_known_dex_addresses

    dex_addrs = get_known_dex_addresses()
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
    BridgeHopStatusResponse,
    ExpandRequest,
    ExpansionResponseV2,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionSnapshotRequest,
    SessionSnapshotResponse,
)


@router.post("/sessions", response_model=SessionCreateResponse)
async def create_investigation_session(
    request: SessionCreateRequest,
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Create a new investigation session seeded from an address.

    Returns a session_id and the root ``InvestigationNode`` for the seed
    address.  All subsequent expansion calls must reference this session_id.

    Phase 3 status: stub — returns a minimal valid response.  Full canonical
    graph lookup and Neo4j session persistence is implemented in Phase 4.
    """
    compiler = await _get_trace_compiler()
    return await compiler.create_session(request)


@router.get("/sessions/{session_id}", response_model=dict)
async def get_investigation_session(
    session_id: str,
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Restore a saved investigation session snapshot.

    Returns the full node/edge set for the session at its last saved state.

    Phase 3 status: stub — returns an empty snapshot.
    """
    # TODO Phase 4: load session from Neo4j InvestigationAnnotation + Redis cache.
    return {
        "session_id": session_id,
        "nodes": [],
        "edges": [],
        "branch_map": {},
        "created_at": None,
        "updated_at": None,
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
    ``POST /sessions``); if it does not, the snapshot is silently ignored.
    """
    saved_at = datetime.now(timezone.utc)
    snapshot_id = str(uuid4())

    try:
        pg = get_postgres_pool()
        async with pg.acquire() as conn:
            await conn.execute(
                """
                UPDATE graph_sessions
                SET snapshot = $1::jsonb,
                    snapshot_saved_at = $2
                WHERE session_id = $3::uuid
                """,
                json.dumps([ns.model_dump(mode="json") for ns in session_snapshot.node_states]),
                saved_at,
                session_id,
            )
        logger.debug("Snapshot saved for session %s (%d nodes)", session_id, len(session_snapshot.node_states))
    except Exception as exc:
        logger.warning("Failed to save snapshot for session %s: %s", session_id, exc)

    return SessionSnapshotResponse(
        snapshot_id=snapshot_id,
        saved_at=saved_at,
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

    Phase 3 status: stub — returns an empty expansion with correct metadata.
    Full chain-specific compilation is implemented in Phase 4.
    """
    compiler = await _get_trace_compiler()
    return await compiler.expand(session_id, request)


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
    with status="pending".  When status changes to "confirmed", the frontend
    can call expand on the BridgeHop node to follow funds to the destination
    chain.

    Phase 3 status: stub — always returns status="pending".
    """
    compiler = await _get_trace_compiler()
    return await compiler.get_bridge_hop_status(session_id, hop_id)


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
    stats = await get_graph_latency_stats()
    return {
        "success": True,
        "endpoints": stats,
        "window_seconds": 3600,
    }
