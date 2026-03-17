"""
UTXOChainCompiler — trace compiler for Bitcoin and Bitcoin-derived chains.

UTXO chain tracing semantics (from tasks/memory.md Section 3 / ADR-001):
- Each UTXO output is a discrete spendable coin; taint analysis must track
  individual outputs, not address-level aggregates.
- CoinJoin detection: if ``is_coinjoin=True`` on a transaction, expansion
  MUST return a CoinJoin halt node rather than propagating taint.  The
  frontend displays this as an analysis boundary and must not silently
  continue.
- Probable change output detection: a single-recipient, low-value second
  output is flagged ``is_probable_change=True`` and rendered with a dashed
  border in the frontend.
- Multi-input aggregation: when N inputs fund one transaction, all N input
  addresses are "co-spenders" and are a co-spend clustering signal.

Data sources:
1. PostgreSQL event store (``raw_utxo_inputs`` / ``raw_utxo_outputs``).
2. Neo4j bipartite fallback (``Address-[:SENT]->Transaction-[:RECEIVED]->Address``).

Phase 4: expand_next / expand_prev implemented with CoinJoin halt semantics.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from src.trace_compiler.chains.base import BaseChainCompiler
from src.trace_compiler.lineage import edge_id as mk_edge_id
from src.trace_compiler.lineage import lineage_id as mk_lineage
from src.trace_compiler.lineage import node_id as mk_node_id
from src.trace_compiler.lineage import path_id as mk_path
from src.trace_compiler.models import AddressNodeData
from src.trace_compiler.models import ExpandOptions
from src.trace_compiler.models import InvestigationEdge
from src.trace_compiler.models import InvestigationNode

logger = logging.getLogger(__name__)

_BITCOIN_CHAINS = {"bitcoin", "litecoin", "bitcoin_cash", "dogecoin"}
_SQL_FETCH_LIMIT = 500


class UTXOChainCompiler(BaseChainCompiler):
    """Trace compiler for Bitcoin and UTXO-based chains.

    Handles CoinJoin halt semantics, change output flagging, and co-spend
    aggregation in accordance with UTXO tracing rules in tasks/memory.md.

    Args:
        postgres_pool: asyncpg pool for event store reads.
        neo4j_driver:  Neo4j driver for fallback queries.
        redis_client:  Redis client (reserved for future co-spend cache).
    """

    @property
    def supported_chains(self) -> List[str]:
        """Return the Bitcoin-family chain names handled by this compiler."""
        return list(_BITCOIN_CHAINS)

    async def expand_next(
        self,
        session_id: str,
        branch_id: str,
        path_sequence: int,
        depth: int,
        seed_address: str,
        chain: str,
        options: ExpandOptions,
    ) -> Tuple[List[InvestigationNode], List[InvestigationEdge]]:
        """Follow funds forward: find transactions funded by ``seed_address``.

        Looks up UTXO outputs of transactions where ``seed_address`` appears
        as an input.  CoinJoin transactions produce a single halt node rather
        than expanding counterparty addresses.

        Args:
            session_id:       Investigation session UUID.
            branch_id:        Branch ID for lineage assignment.
            path_sequence:    Integer index for path_id generation.
            depth:            Current hop depth from the session root.
            seed_address:     Bitcoin address (Base58, case-sensitive).
            chain:            Chain name (e.g. ``"bitcoin"``).
            options:          Expansion options.

        Returns:
            Tuple of (nodes, edges).
        """
        rows = await self._fetch_outbound_event_store(seed_address, chain, options)
        if not rows:
            rows = await self._fetch_outbound_neo4j(seed_address, chain, options)

        return self._build_graph(
            rows=rows,
            session_id=session_id,
            branch_id=branch_id,
            path_sequence=path_sequence,
            depth=depth,
            seed_address=seed_address,
            chain=chain,
            direction="forward",
            options=options,
        )

    async def expand_prev(
        self,
        session_id: str,
        branch_id: str,
        path_sequence: int,
        depth: int,
        seed_address: str,
        chain: str,
        options: ExpandOptions,
    ) -> Tuple[List[InvestigationNode], List[InvestigationEdge]]:
        """Follow funds backward: find transactions that sent to ``seed_address``.

        Looks up UTXO outputs where the output address is ``seed_address``.

        Args: same as expand_next.

        Returns:
            Tuple of (nodes, edges).
        """
        rows = await self._fetch_inbound_event_store(seed_address, chain, options)
        if not rows:
            rows = await self._fetch_inbound_neo4j(seed_address, chain, options)

        return self._build_graph(
            rows=rows,
            session_id=session_id,
            branch_id=branch_id,
            path_sequence=path_sequence,
            depth=depth,
            seed_address=seed_address,
            chain=chain,
            direction="backward",
            options=options,
        )

    # ------------------------------------------------------------------
    # Event store queries
    # ------------------------------------------------------------------

    async def _fetch_outbound_event_store(
        self, address: str, chain: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Find transactions funded by address, return their outputs.

        Query pattern:
        1. Find all tx_hashes in raw_utxo_inputs where input address = seed.
        2. For each tx_hash, fetch all raw_utxo_outputs (counterparties).
        3. Tag each row with is_coinjoin (join: raw_transactions.is_coinjoin).
        """
        if self._pg is None:
            return []
        try:
            limit = min(options.max_results, _SQL_FETCH_LIMIT)
            sql = """
                SELECT
                    o.tx_hash,
                    o.address           AS counterparty,
                    o.value_satoshis,
                    o.output_index,
                    o.script_type,
                    o.is_probable_change,
                    o.is_spent,
                    o.timestamp,
                    COALESCE(t.is_coinjoin, FALSE) AS is_coinjoin
                FROM raw_utxo_inputs  i
                JOIN raw_utxo_outputs o ON o.blockchain = i.blockchain
                                      AND o.tx_hash    = i.tx_hash
                LEFT JOIN raw_transactions t ON t.blockchain = i.blockchain
                                           AND t.tx_hash    = i.tx_hash
                WHERE i.blockchain = $1
                  AND i.address    = $2
                  AND o.address IS NOT NULL
                  AND o.address <> $2
                ORDER BY o.timestamp DESC
                LIMIT $3
            """
            async with self._pg.acquire() as conn:
                rows = await conn.fetch(sql, chain, address, limit)
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.debug(
                "UTXOChainCompiler._fetch_outbound_event_store failed %s/%s: %s",
                chain, address, exc,
            )
            return []

    async def _fetch_inbound_event_store(
        self, address: str, chain: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Find UTXO outputs sent to address, return their input addresses."""
        if self._pg is None:
            return []
        try:
            limit = min(options.max_results, _SQL_FETCH_LIMIT)
            sql = """
                SELECT
                    i.tx_hash,
                    i.address           AS counterparty,
                    o.value_satoshis,
                    o.output_index,
                    o.script_type,
                    o.is_probable_change,
                    o.is_spent,
                    o.timestamp,
                    COALESCE(t.is_coinjoin, FALSE) AS is_coinjoin
                FROM raw_utxo_outputs o
                JOIN raw_utxo_inputs  i ON i.blockchain = o.blockchain
                                      AND i.tx_hash    = o.tx_hash
                LEFT JOIN raw_transactions t ON t.blockchain = o.blockchain
                                           AND t.tx_hash    = o.tx_hash
                WHERE o.blockchain = $1
                  AND o.address    = $2
                  AND i.address IS NOT NULL
                  AND i.address <> $2
                ORDER BY o.timestamp DESC
                LIMIT $3
            """
            async with self._pg.acquire() as conn:
                rows = await conn.fetch(sql, chain, address, limit)
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.debug(
                "UTXOChainCompiler._fetch_inbound_event_store failed %s/%s: %s",
                chain, address, exc,
            )
            return []

    # ------------------------------------------------------------------
    # Neo4j fallback queries
    # ------------------------------------------------------------------

    async def _fetch_outbound_neo4j(
        self, address: str, chain: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Fallback: query Neo4j bipartite graph for outbound UTXO flows."""
        if self._neo4j is None:
            return []
        try:
            limit = min(options.max_results, _SQL_FETCH_LIMIT)
            cypher = """
                MATCH (a:Address {address: $addr, blockchain: $chain})
                      -[:SENT]->(t:Transaction)
                      -[r:RECEIVED]->(tgt:Address)
                WHERE tgt.address <> $addr
                RETURN tgt.address        AS counterparty,
                       t.hash             AS tx_hash,
                       r.value_satoshis   AS value_satoshis,
                       r.output_index     AS output_index,
                       r.script_type      AS script_type,
                       r.is_probable_change AS is_probable_change,
                       FALSE              AS is_spent,
                       t.timestamp        AS timestamp,
                       t.is_coinjoin      AS is_coinjoin
                LIMIT $limit
            """
            async with self._neo4j.session() as session:
                result = await session.run(
                    cypher, addr=address, chain=chain, limit=limit
                )
                return [dict(r) async for r in result]
        except Exception as exc:
            logger.debug("UTXOChainCompiler._fetch_outbound_neo4j failed: %s", exc)
            return []

    async def _fetch_inbound_neo4j(
        self, address: str, chain: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Fallback: query Neo4j for inbound UTXO flows."""
        if self._neo4j is None:
            return []
        try:
            limit = min(options.max_results, _SQL_FETCH_LIMIT)
            cypher = """
                MATCH (src:Address)-[:SENT]->(t:Transaction)
                      -[r:RECEIVED]->(a:Address {address: $addr, blockchain: $chain})
                WHERE src.address <> $addr
                RETURN src.address        AS counterparty,
                       t.hash             AS tx_hash,
                       r.value_satoshis   AS value_satoshis,
                       r.output_index     AS output_index,
                       r.script_type      AS script_type,
                       r.is_probable_change AS is_probable_change,
                       FALSE              AS is_spent,
                       t.timestamp        AS timestamp,
                       t.is_coinjoin      AS is_coinjoin
                LIMIT $limit
            """
            async with self._neo4j.session() as session:
                result = await session.run(
                    cypher, addr=address, chain=chain, limit=limit
                )
                return [dict(r) async for r in result]
        except Exception as exc:
            logger.debug("UTXOChainCompiler._fetch_inbound_neo4j failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Node / edge construction with CoinJoin halt semantics
    # ------------------------------------------------------------------

    def _build_graph(
        self,
        rows: List[Dict[str, Any]],
        session_id: str,
        branch_id: str,
        path_sequence: int,
        depth: int,
        seed_address: str,
        chain: str,
        direction: str,
        options: ExpandOptions,
    ) -> Tuple[List[InvestigationNode], List[InvestigationEdge]]:
        """Convert UTXO transfer rows into InvestigationNodes and edges.

        **CoinJoin halt rule** (tasks/memory.md Section 3): if any row has
        ``is_coinjoin=True``, the entire expansion returns a single opaque
        CoinJoin halt node marked ``is_coinjoin_halt=True``.  Taint analysis
        must not propagate through this node.  The frontend renders it as an
        analysis boundary with a warning indicator.

        Args:
            rows:           List of dicts from event store or Neo4j fallback.
            session_id:     Investigation session UUID.
            branch_id:      Branch ID for lineage assignment.
            path_sequence:  Integer index for path_id generation.
            depth:          Current hop depth from session root.
            seed_address:   Bitcoin address being expanded.
            chain:          Chain name.
            direction:      ``"forward"`` or ``"backward"``.
            options:        Expansion options.

        Returns:
            Tuple of (nodes, edges).
        """
        if not rows:
            return [], []

        _path = mk_path(branch_id, path_sequence)
        seed_node_id = mk_node_id(chain, "address", seed_address)

        # CoinJoin halt: any CoinJoin transaction terminates the expansion.
        coinjoin_txs = {r["tx_hash"] for r in rows if r.get("is_coinjoin")}
        if coinjoin_txs:
            _lineage = mk_lineage(session_id, branch_id, _path, depth + 1)
            halt_node_id = mk_node_id(chain, "address", f"coinjoin_halt:{seed_address}")
            halt_node = InvestigationNode(
                node_id=halt_node_id,
                lineage_id=_lineage,
                node_type="address",
                branch_id=branch_id,
                path_id=_path,
                depth=depth + 1,
                display_label="CoinJoin (taint boundary)",
                display_sublabel="Expansion halted — CoinJoin transaction detected",
                chain=chain,
                expandable_directions=[],  # not expandable
                is_highlighted=True,
                address_data=AddressNodeData(
                    address=seed_address,
                    address_type="utxo_p2pkh",
                    is_coinjoin_halt=True,
                ),
            )
            halt_edge = InvestigationEdge(
                edge_id=mk_edge_id(seed_node_id, halt_node_id, branch_id, None),
                source_node_id=seed_node_id if direction == "forward" else halt_node_id,
                target_node_id=halt_node_id if direction == "forward" else seed_node_id,
                branch_id=branch_id,
                path_id=_path,
                edge_type="transfer",
                direction=direction,
            )
            return [halt_node], [halt_edge]

        # Normal expansion: build one node per unique counterparty address.
        seen_nodes: Dict[str, InvestigationNode] = {}
        edges: List[InvestigationEdge] = []

        for row in rows:
            counterparty = row.get("counterparty") or ""
            if not counterparty or counterparty == seed_address:
                continue

            tx_hash = row.get("tx_hash") or ""
            value_sats: Optional[int] = row.get("value_satoshis")
            value_btc: Optional[float] = (
                value_sats / 1e8 if value_sats is not None else None
            )
            script_type: str = row.get("script_type") or "unknown"
            is_probable_change: bool = bool(row.get("is_probable_change"))

            if counterparty not in seen_nodes:
                _cp_node_id = mk_node_id(chain, "address", counterparty)
                _lineage = mk_lineage(session_id, branch_id, _path, depth + 1)
                node = InvestigationNode(
                    node_id=_cp_node_id,
                    lineage_id=_lineage,
                    node_type="address",
                    branch_id=branch_id,
                    path_id=_path,
                    depth=depth + 1,
                    display_label=counterparty[:12] + "…" if len(counterparty) > 12 else counterparty,
                    chain=chain,
                    expandable_directions=["prev", "next", "neighbors"],
                    address_data=AddressNodeData(
                        address=counterparty,
                        address_type=_script_type_to_address_type(script_type),
                    ),
                )
                seen_nodes[counterparty] = node

            if direction == "forward":
                src_node_id = seed_node_id
                tgt_node_id = seen_nodes[counterparty].node_id
            else:
                src_node_id = seen_nodes[counterparty].node_id
                tgt_node_id = seed_node_id

            raw_ts = row.get("timestamp")
            _ts_str: Optional[str] = None
            if isinstance(raw_ts, datetime):
                _ts_str = raw_ts.isoformat()
            elif isinstance(raw_ts, str):
                _ts_str = raw_ts

            edge = InvestigationEdge(
                edge_id=mk_edge_id(src_node_id, tgt_node_id, branch_id, tx_hash),
                source_node_id=src_node_id,
                target_node_id=tgt_node_id,
                branch_id=branch_id,
                path_id=_path,
                edge_type="transfer",
                value_native=value_btc,
                asset_symbol="BTC",
                canonical_asset_id="btc",
                tx_hash=tx_hash or None,
                tx_chain=chain,
                timestamp=_ts_str,
                is_suspected_change=is_probable_change,
                direction=direction,
            )
            edges.append(edge)

        nodes = list(seen_nodes.values())[: options.max_results]
        edges = edges[: options.max_results * 3]
        return nodes, edges


def _script_type_to_address_type(script_type: str) -> str:
    """Map a UTXO script type string to an InvestigationNode address_type."""
    _MAP = {
        "p2pkh": "utxo_p2pkh",
        "p2sh": "utxo_p2sh",
        "p2wpkh": "utxo_p2wpkh",
        "p2wsh": "utxo_p2wsh",
        "p2tr": "utxo_p2tr",
        "op_return": "utxo_op_return",
    }
    return _MAP.get(script_type.lower(), "utxo_p2pkh")
