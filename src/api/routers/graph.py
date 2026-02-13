"""
Jackdaw Sentry - Transaction Graph Router (M9.2)
Returns {nodes, edges} JSON for the frontend Cytoscape.js graph renderer.
Supports address expansion, transaction tracing, search, and clustering.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, validator
import logging
import time

from src.api.auth import User, check_permissions, PERMISSIONS
from src.api.database import get_neo4j_session
from src.api.config import get_supported_blockchains
from src.collectors.rpc.factory import get_rpc_client
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

    @validator("blockchain")
    def validate_blockchain(cls, v):
        if v.lower() not in get_supported_blockchains():
            raise ValueError(f"Unsupported blockchain: {v}")
        return v.lower()

    @validator("depth")
    def validate_depth(cls, v):
        if v < 1 or v > MAX_DEPTH:
            raise ValueError(f"Depth must be between 1 and {MAX_DEPTH}")
        return v

    @validator("direction")
    def validate_direction(cls, v):
        if v not in ("in", "out", "both"):
            raise ValueError("Direction must be 'in', 'out', or 'both'")
        return v


class GraphTraceRequest(BaseModel):
    tx_hash: str
    blockchain: str
    follow_hops: int = 3

    @validator("blockchain")
    def validate_blockchain(cls, v):
        if v.lower() not in get_supported_blockchains():
            raise ValueError(f"Unsupported blockchain: {v}")
        return v.lower()

    @validator("follow_hops")
    def validate_hops(cls, v):
        if v < 1 or v > MAX_DEPTH:
            raise ValueError(f"follow_hops must be between 1 and {MAX_DEPTH}")
        return v


class GraphSearchRequest(BaseModel):
    query: str
    blockchain: Optional[str] = None

    @validator("blockchain", pre=True, always=True)
    def validate_blockchain(cls, v):
        if v and v.lower() not in get_supported_blockchains():
            raise ValueError(f"Unsupported blockchain: {v}")
        return v.lower() if v else None


class GraphClusterRequest(BaseModel):
    addresses: List[str]
    blockchain: str

    @validator("blockchain")
    def validate_blockchain(cls, v):
        if v.lower() not in get_supported_blockchains():
            raise ValueError(f"Unsupported blockchain: {v}")
        return v.lower()

    @validator("addresses")
    def validate_addresses(cls, v):
        if not v or len(v) < 2:
            raise ValueError("At least 2 addresses required")
        if len(v) > 50:
            raise ValueError("Maximum 50 addresses per cluster request")
        return [a.strip().lower() for a in v if a.strip()]


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
    success: bool
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    metadata: Dict[str, Any]
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
    addr = request.address.lower()
    nodes_map: Dict[str, Dict[str, Any]] = {}
    edges_list: List[Dict[str, Any]] = []

    # Build direction-specific Cypher pattern with variable-length depth
    if request.direction == "out":
        pattern = "path = (a:Address {address: $addr, blockchain: $bc})-[:SENT]->(:Transaction)-[:RECEIVED]->(b:Address)"
    elif request.direction == "in":
        pattern = "path = (b:Address)-[:SENT]->(:Transaction)-[:RECEIVED]->(a:Address {address: $addr, blockchain: $bc})"
    else:
        pattern = "path = (a:Address {address: $addr, blockchain: $bc})-[:SENT|RECEIVED]-(:Transaction)-[:SENT|RECEIVED]-(b:Address)"

    # Multi-hop expansion via variable-length paths bounded by depth
    cypher = f"""
    MATCH {pattern}
    WHERE b.address <> $addr AND length(path) <= $depth * 2
    WITH a, b, relationships(path) AS rels
    UNWIND rels AS r
    WITH a, b, startNode(r) AS sn, endNode(r) AS en, r
    WHERE 'Transaction' IN labels(en) OR 'Transaction' IN labels(sn)
    WITH a, b,
         CASE WHEN 'Transaction' IN labels(en) THEN en ELSE sn END AS t
    """

    if request.min_value is not None:
        cypher += " AND toFloat(t.value) >= $min_val"
    if request.time_from:
        cypher += " AND t.timestamp >= $t_from"
    if request.time_to:
        cypher += " AND t.timestamp <= $t_to"

    cypher += f"""
    RETURN DISTINCT
        a.address AS from_addr,
        b.address AS to_addr,
        b.blockchain AS b_chain,
        t.hash AS tx_hash,
        t.value AS tx_value,
        t.timestamp AS tx_ts,
        t.block_number AS tx_block,
        t.status AS tx_status
    LIMIT $node_limit
    """

    params = {
        "addr": addr,
        "bc": request.blockchain,
        "depth": request.depth,
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
        neighbor = rec.get("to_addr", "")
        if not neighbor:
            continue
        neighbor_chain = rec.get("b_chain", request.blockchain)

        # Add neighbor node
        if neighbor not in nodes_map:
            nodes_map[neighbor] = _make_address_node(neighbor, neighbor_chain)

        # Add edge
        tx_hash = rec.get("tx_hash", "")
        edge_id = tx_hash or f"{addr}-{neighbor}"
        edges_list.append({
            "id": edge_id,
            "source": addr,
            "target": neighbor,
            "value": _safe_float(rec.get("tx_value")),
            "chain": request.blockchain,
            "timestamp": rec.get("tx_ts"),
            "tx_hash": tx_hash,
            "block_number": rec.get("tx_block"),
        })

    # If nothing found in Neo4j, try live RPC for the seed address
    if len(nodes_map) == 1 and not edges_list:
        client = get_rpc_client(request.blockchain)
        if client:
            try:
                addr_info = await client.get_address_info(addr)
                if addr_info:
                    nodes_map[addr].update({
                        "balance": float(addr_info.balance) if addr_info.balance else 0.0,
                        "tx_count": addr_info.transaction_count,
                        "type": addr_info.type,
                    })
                # Attempt to fetch recent txs to build edges
                txs = await client.get_address_transactions(addr, limit=25)
                for tx in txs:
                    from_a = (tx.from_address or "").lower()
                    to_a = (tx.to_address or "").lower()
                    if from_a == addr:
                        peer = to_a
                    elif to_a == addr:
                        peer = from_a
                    else:
                        continue  # tx doesn't involve addr
                    if peer and peer not in nodes_map:
                        nodes_map[peer] = _make_address_node(peer, request.blockchain)
                    if peer:
                        edges_list.append({
                            "id": tx.hash,
                            "source": from_a or addr,
                            "target": to_a or "",
                            "value": float(tx.value) if tx.value else 0.0,
                            "chain": request.blockchain,
                            "timestamp": tx.timestamp.isoformat() if tx.timestamp else None,
                            "tx_hash": tx.hash,
                            "block_number": tx.block_number,
                        })
            except Exception as exc:
                logger.warning(f"Graph expand RPC fallback failed: {exc}")

    # Tag sanctioned addresses
    await _enrich_sanctions(nodes_map, request.blockchain)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    return GraphResponse(
        success=True,
        nodes=list(nodes_map.values()),
        edges=edges_list,
        metadata={
            "seed_address": addr,
            "blockchain": request.blockchain,
            "depth": request.depth,
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
                        nodes_map[from_addr] = _make_address_node(from_addr, request.blockchain)
                    if to_addr:
                        nodes_map[to_addr] = _make_address_node(to_addr, request.blockchain)
                    edges_list.append({
                        "id": tx.hash,
                        "source": from_addr,
                        "target": to_addr,
                        "value": float(tx.value) if tx.value else 0.0,
                        "chain": request.blockchain,
                        "timestamp": tx.timestamp.isoformat() if tx.timestamp else None,
                        "tx_hash": tx.hash,
                        "block_number": tx.block_number,
                    })
            except Exception as exc:
                logger.warning(f"Graph trace RPC fallback failed: {exc}")

        if not nodes_map:
            raise HTTPException(status_code=404, detail="Transaction not found")

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
    edges_list.append({
        "id": request.tx_hash,
        "source": from_addr,
        "target": to_addr,
        "value": _safe_float(seed["value"]),
        "chain": request.blockchain,
        "timestamp": seed["ts"],
        "tx_hash": request.tx_hash,
        "block_number": seed.get("block_num"),
    })

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
                edges_list.append({
                    "id": rec.get("tx_hash", f"{hop}-{nxt}"),
                    "source": hop,
                    "target": nxt,
                    "value": _safe_float(rec.get("value")),
                    "chain": request.blockchain,
                    "timestamp": rec.get("ts"),
                    "tx_hash": rec.get("tx_hash"),
                    "block_number": rec.get("block_num"),
                })

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
    q = request.query.strip().lower()
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
            OPTIONAL MATCH (a)-[r:SENT|RECEIVED]-()
            RETURN a, count(r) AS tx_count
            """,
            q=q, bc=request.blockchain,
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
                q=q, bc=request.blockchain,
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
            edges_list.append({
                "id": q,
                "source": from_a,
                "target": to_a,
                "value": _safe_float(t.get("value")),
                "chain": chain,
                "timestamp": t.get("timestamp"),
                "tx_hash": q,
                "block_number": t.get("block_number"),
            })
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
                        node.update({
                            "balance": float(addr_info.balance) if addr_info.balance else 0.0,
                            "tx_count": addr_info.transaction_count,
                            "type": addr_info.type,
                        })
                        nodes_map[q] = node
                except Exception:
                    pass

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
                        edges_list.append({
                            "id": tx.hash,
                            "source": fa,
                            "target": ta,
                            "value": float(tx.value) if tx.value else 0.0,
                            "chain": chain,
                            "timestamp": tx.timestamp.isoformat() if tx.timestamp else None,
                            "tx_hash": tx.hash,
                            "block_number": tx.block_number,
                        })
                except Exception:
                    pass

    if not nodes_map and not edges_list:
        raise HTTPException(status_code=404, detail="No results found")

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


@router.get("/address/{address}/summary")
async def address_summary(
    address: str,
    blockchain: str = Query(default="ethereum"),
    current_user: User = Depends(check_permissions([PERMISSIONS["read_blockchain"]])),
):
    """Node metadata: balance, tx count, risk score, labels, sanctions status, first/last seen."""
    start = time.monotonic()
    addr = address.lower()
    bc = blockchain.lower()

    if bc not in get_supported_blockchains():
        raise HTTPException(status_code=400, detail=f"Unsupported blockchain: {bc}")

    data: Dict[str, Any] = {"address": addr, "blockchain": bc}

    # Neo4j lookup
    async with get_neo4j_session() as session:
        result = await session.run(
            """
            OPTIONAL MATCH (a:Address {address: $addr, blockchain: $bc})
            OPTIONAL MATCH (a)-[r:SENT|RECEIVED]-()
            OPTIONAL MATCH (a)-[:SENT]->(sent_t:Transaction)
            OPTIONAL MATCH (recv_t:Transaction)-[:RECEIVED]->(a)
            WITH a, count(DISTINCT r) AS tx_count,
                 count(DISTINCT sent_t) AS sent_count,
                 count(DISTINCT recv_t) AS recv_count,
                 min(sent_t.timestamp) AS first_sent,
                 max(recv_t.timestamp) AS last_recv
            RETURN a, tx_count, sent_count, recv_count, first_sent, last_recv
            """,
            addr=addr, bc=bc,
        )
        rec = await result.single()

    if rec and rec["a"]:
        props = dict(rec["a"])
        data.update({
            "balance": _safe_float(props.get("balance")),
            "tx_count": rec["tx_count"],
            "sent_count": rec["sent_count"],
            "recv_count": rec["recv_count"],
            "type": props.get("type", "unknown"),
            "risk_score": _safe_float(props.get("risk_score")),
            "labels": props.get("labels", []),
            "first_seen": rec.get("first_sent"),
            "last_seen": rec.get("last_recv"),
            "sanctioned": props.get("sanctioned", False),
            "data_source": "neo4j",
        })
    else:
        # Live RPC fallback
        client = get_rpc_client(bc)
        if client:
            try:
                addr_info = await client.get_address_info(addr)
                if addr_info:
                    data.update({
                        "balance": float(addr_info.balance) if addr_info.balance else 0.0,
                        "tx_count": addr_info.transaction_count,
                        "type": addr_info.type,
                        "risk_score": 0.0,
                        "labels": [],
                        "sanctioned": False,
                        "data_source": "live_rpc",
                    })
            except Exception as exc:
                logger.warning(f"Address summary RPC fallback failed: {exc}")

        if "data_source" not in data:
            raise HTTPException(status_code=404, detail="Address not found")

    elapsed_ms = int((time.monotonic() - start) * 1000)
    return {
        "success": True,
        "summary": data,
        "metadata": {"processing_time_ms": elapsed_ms},
        "timestamp": datetime.now(timezone.utc),
    }


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

    # Find common counterparties — addresses that transacted with 2+ of the input addresses
    cypher = """
    UNWIND $addrs AS input_addr
    MATCH (a:Address {address: input_addr, blockchain: $bc})-[:SENT|RECEIVED]-(t:Transaction)-[:SENT|RECEIVED]-(counterparty:Address)
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
            edges_list.append({
                "id": tx_hash,
                "source": input_addr,
                "target": cp,
                "value": value,
                "chain": request.blockchain,
                "tx_hash": tx_hash if tx_hash != f"{input_addr}-{cp}" else None,
            })

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
        edges_list.append({
            "id": rec["tx_hash"],
            "source": rec["a1"],
            "target": rec["a2"],
            "value": _safe_float(rec.get("value")),
            "chain": request.blockchain,
            "timestamp": rec.get("ts"),
            "tx_hash": rec["tx_hash"],
        })

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
        "balance": None,
        "tx_count": None,
    }


async def _enrich_sanctions(nodes_map: Dict[str, Dict[str, Any]], blockchain: str) -> None:
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
