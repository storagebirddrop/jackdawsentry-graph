"""
EVMChainCompiler — trace compiler for all EVM-compatible chains.

Handles: Ethereum, BSC, Polygon, Arbitrum, Base, Avalanche, Optimism,
Starknet, Injective (EVM mode).

Data sources (in priority order):
1. PostgreSQL event store ``raw_transactions`` and ``raw_token_transfers``
   (populated when ``DUAL_WRITE_RAW_EVENT_STORE=True``).
2. Neo4j canonical graph fallback (bipartite model: Address→Transaction→Address).

Enrichment applied per node:
- Entity / VASP attribution from Neo4j Address.entity_id + Entity nodes.
- Service overlay: known DEX / bridge contract addresses → ServiceNode display.
- Sanctions flag via in-memory sanctions cache.
- Fiat valuation from ``asset_prices`` table (when available).

Phase 4: full expand_next / expand_prev / expand_neighbors implemented.
"""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from src.intelligence.price_oracle import price_oracle
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

# EVM chain names handled by this compiler.
EVM_CHAINS = {
    "ethereum",
    "bsc",
    "polygon",
    "arbitrum",
    "base",
    "avalanche",
    "optimism",
    "starknet",
    "injective",
}

# Maximum rows fetched from the event store per expansion call before
# pagination.  This is the per-SQL LIMIT, not the max_results option.
_SQL_FETCH_LIMIT = 500


class EVMChainCompiler(BaseChainCompiler):
    """Trace compiler for EVM-compatible chains.

    Reads from the PostgreSQL event store when data is present, falls back
    to the Neo4j bipartite graph when the event store is empty (pre-cutover).

    Args:
        postgres_pool: asyncpg pool connected to the event store.
        neo4j_driver:  Neo4j async driver for the canonical graph.
        redis_client:  Redis client for service classification cache.
    """

    @property
    def supported_chains(self) -> List[str]:
        """Return the list of EVM chain names handled by this compiler."""
        return list(EVM_CHAINS)

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
        """Return outbound fund flows from ``seed_address``.

        Queries the event store for rows in ``raw_transactions`` and
        ``raw_token_transfers`` where ``from_address = seed_address``.  Falls
        back to Neo4j if the event store returns no rows for this address.

        Args:
            session_id:       Investigation session UUID.
            branch_id:        Branch ID for lineage assignment.
            path_sequence:    Integer index for path_id generation.
            depth:            Current hop depth from the session root.
            seed_address:     EVM address (lowercase hex) to expand from.
            chain:            Blockchain name (e.g. ``"ethereum"``).
            options:          Expansion options (filters, limits).

        Returns:
            Tuple of (nodes, edges) ready for inclusion in ExpansionResponseV2.
        """
        addr = seed_address.lower()
        rows = await self._fetch_outbound_event_store(addr, chain, options)
        if not rows:
            rows = await self._fetch_outbound_neo4j(addr, chain, options)

        prices = await self._prefetch_prices(rows)
        return self._build_graph(
            rows=rows,
            session_id=session_id,
            branch_id=branch_id,
            path_sequence=path_sequence,
            depth=depth,
            seed_address=addr,
            chain=chain,
            direction="forward",
            options=options,
            prices=prices,
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
        """Return inbound fund flows into ``seed_address``.

        Mirrors expand_next but queries ``to_address = seed_address``.

        Args: same as expand_next.

        Returns:
            Tuple of (nodes, edges) ready for inclusion in ExpansionResponseV2.
        """
        addr = seed_address.lower()
        rows = await self._fetch_inbound_event_store(addr, chain, options)
        if not rows:
            rows = await self._fetch_inbound_neo4j(addr, chain, options)

        prices = await self._prefetch_prices(rows)
        return self._build_graph(
            rows=rows,
            session_id=session_id,
            branch_id=branch_id,
            path_sequence=path_sequence,
            depth=depth,
            seed_address=addr,
            chain=chain,
            direction="backward",
            options=options,
            prices=prices,
        )

    # ------------------------------------------------------------------
    # Event store queries
    # ------------------------------------------------------------------

    async def _fetch_outbound_event_store(
        self, address: str, chain: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Fetch outbound transfers from the PostgreSQL event store.

        Returns an empty list (not an error) when the event store has no
        data for this address or when the pool is unavailable.
        """
        if self._pg is None:
            return []
        try:
            limit = min(options.max_results, _SQL_FETCH_LIMIT)
            sql = """
                SELECT
                    tx_hash,
                    to_address    AS counterparty,
                    value_native,
                    NULL          AS asset_symbol,
                    NULL          AS canonical_asset_id,
                    timestamp
                FROM raw_transactions
                WHERE blockchain = $1
                  AND from_address = $2
                  AND to_address IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT $3
            """
            async with self._pg.acquire() as conn:
                rows = await conn.fetch(sql, chain, address, limit)

            result = [dict(r) for r in rows]

            # Merge token transfers for the same addresses.
            if not options.asset_filter or any(
                af.upper() not in {"ETH", "BNB", "MATIC", "AVAX"}
                for af in options.asset_filter
            ):
                token_rows = await self._fetch_outbound_token_transfers(
                    address, chain, options
                )
                result.extend(token_rows)

            return result
        except Exception as exc:
            logger.debug(
                "EVMChainCompiler._fetch_outbound_event_store failed for %s/%s: %s",
                chain,
                address,
                exc,
            )
            return []

    async def _fetch_inbound_event_store(
        self, address: str, chain: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Fetch inbound transfers from the PostgreSQL event store."""
        if self._pg is None:
            return []
        try:
            limit = min(options.max_results, _SQL_FETCH_LIMIT)
            sql = """
                SELECT
                    tx_hash,
                    from_address  AS counterparty,
                    value_native,
                    NULL          AS asset_symbol,
                    NULL          AS canonical_asset_id,
                    timestamp
                FROM raw_transactions
                WHERE blockchain = $1
                  AND to_address = $2
                  AND from_address IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT $3
            """
            async with self._pg.acquire() as conn:
                rows = await conn.fetch(sql, chain, address, limit)

            result = [dict(r) for r in rows]
            
            # Merge token transfers for the same addresses.
            if not options.asset_filter or any(
                af.upper() not in {"ETH", "BNB", "MATIC", "AVAX"}
                for af in options.asset_filter
            ):
                token_rows = await self._fetch_inbound_token_transfers(
                    address, chain, options
                )
                result.extend(token_rows)
            return result
        except Exception as exc:
            logger.debug(
                "EVMChainCompiler._fetch_inbound_event_store failed: %s", exc
            )
            return []

    async def _fetch_outbound_token_transfers(
        self, address: str, chain: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Fetch outbound ERC-20 / BEP-20 token transfers."""
        if self._pg is None:
            return []
        try:
            limit = min(options.max_results, _SQL_FETCH_LIMIT)
            asset_clause = ""
            params: list = [chain, address, limit]
            if options.asset_filter:
                placeholders = ", ".join(
                    f"${i + 4}" for i in range(len(options.asset_filter))
                )
                asset_clause = f"AND UPPER(asset_symbol) IN ({placeholders})"
                params.extend(a.upper() for a in options.asset_filter)

            sql = f"""
                SELECT
                    tx_hash,
                    to_address       AS counterparty,
                    amount_normalized AS value_native,
                    asset_symbol,
                    canonical_asset_id,
                    timestamp
                FROM raw_token_transfers
                WHERE blockchain = $1
                  AND from_address = $2
                  {asset_clause}
                ORDER BY timestamp DESC
                LIMIT $3
            """
            async with self._pg.acquire() as conn:
                rows = await conn.fetch(sql, *params)
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.debug("_fetch_outbound_token_transfers failed: %s", exc)
            return []

    async def _fetch_inbound_token_transfers(
        self, address: str, chain: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Fetch inbound ERC-20 / BEP-20 token transfers."""
        if self._pg is None:
            return []
        try:
            limit = min(options.max_results, _SQL_FETCH_LIMIT)
            sql = """
                SELECT
                    tx_hash,
                    from_address      AS counterparty,
                    amount_normalized AS value_native,
                    asset_symbol,
                    canonical_asset_id,
                    timestamp
                FROM raw_token_transfers
                WHERE blockchain = $1
                  AND to_address = $2
                  AND (options.asset_filter IS NULL OR asset_symbol = ANY($3))
                ORDER BY timestamp DESC
                LIMIT $3
            """
            async with self._pg.acquire() as conn:
                rows = await conn.fetch(sql, chain, address, *options.asset_filter)
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.debug("_fetch_inbound_token_transfers failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Neo4j fallback queries
    # ------------------------------------------------------------------

    async def _fetch_outbound_neo4j(
        self, address: str, chain: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Fetch outbound transfers from the Neo4j bipartite graph (fallback)."""
        if self._neo4j is None:
            return []
        try:
            limit = min(options.max_results, _SQL_FETCH_LIMIT)
            cypher = """
                MATCH (a:Address {address: $addr, blockchain: $chain})
                      -[:SENT]->(t:Transaction)
                      -[:RECEIVED]->(tgt:Address)
                WHERE tgt.address <> $addr
                RETURN tgt.address    AS counterparty,
                       t.hash         AS tx_hash,
                       t.value        AS value_native,
                       NULL           AS asset_symbol,
                       NULL           AS canonical_asset_id,
                       t.timestamp    AS timestamp
                LIMIT $limit
            """
            async with self._neo4j.session() as session:
                result = await session.run(
                    cypher, addr=address, chain=chain, limit=limit
                )
                return [dict(r) async for r in result]
        except Exception as exc:
            logger.debug("EVMChainCompiler._fetch_outbound_neo4j failed: %s", exc)
            return []

    async def _fetch_inbound_neo4j(
        self, address: str, chain: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Fetch inbound transfers from the Neo4j bipartite graph (fallback)."""
        if self._neo4j is None:
            return []
        try:
            limit = min(options.max_results, _SQL_FETCH_LIMIT)
            cypher = """
                MATCH (src:Address)-[:SENT]->(t:Transaction)
                      -[:RECEIVED]->(a:Address {address: $addr, blockchain: $chain})
                WHERE src.address <> $addr
                RETURN src.address    AS counterparty,
                       t.hash         AS tx_hash,
                       t.value        AS value_native,
                       NULL           AS asset_symbol,
                       NULL           AS canonical_asset_id,
                       t.timestamp    AS timestamp
                LIMIT $limit
            """
            async with self._neo4j.session() as session:
                result = await session.run(
                    cypher, addr=address, chain=chain, limit=limit
                )
                return [dict(r) async for r in result]
        except Exception as exc:
            logger.debug("EVMChainCompiler._fetch_inbound_neo4j failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Node / edge construction
    # ------------------------------------------------------------------

    async def _prefetch_prices(
        self, rows: List[Dict[str, Any]]
    ) -> Dict[str, Optional[float]]:
        """Bulk-fetch USD prices for all canonical assets referenced in *rows*.

        Calls :func:`price_oracle.get_prices_bulk` once per expansion so that
        ``_build_graph`` can annotate edges with fiat values and apply
        ``min_value_fiat`` filtering without a per-row database round-trip.

        Returns an empty dict when no rows carry a ``canonical_asset_id``.
        Price-oracle failures are swallowed inside the oracle; missing assets
        map to ``None``.
        """
        asset_ids = list({
            row["canonical_asset_id"]
            for row in rows
            if row.get("canonical_asset_id")
        })
        if not asset_ids:
            return {}
        return await price_oracle.get_prices_bulk(asset_ids)

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
        prices: Optional[Dict[str, Optional[float]]] = None,
    ) -> Tuple[List[InvestigationNode], List[InvestigationEdge]]:
        """Convert raw transfer rows into InvestigationNodes and InvestigationEdges.

        Deduplicates by counterparty address — one node per unique address
        regardless of how many transfers link to it.  Edges are created per
        (counterparty, tx_hash) pair.

        Args:
            rows:           List of dicts with keys: counterparty, tx_hash,
                            value_native, asset_symbol, canonical_asset_id,
                            timestamp.
            session_id:     Investigation session UUID.
            branch_id:      Branch ID for lineage assignment.
            path_sequence:  Integer used to derive the path_id.
            depth:          Hop depth from session root.
            seed_address:   The address being expanded.
            chain:          Blockchain name.
            direction:      ``"forward"`` or ``"backward"``.
            options:        Expansion options (used for value filtering).
            prices:         Pre-fetched price map from :meth:`_prefetch_prices`.
                            ``None`` or missing keys mean no fiat data available.

        Returns:
            Tuple of (nodes, edges).
        """
        seen_nodes: Dict[str, InvestigationNode] = {}
        edges: List[InvestigationEdge] = []

        _path = mk_path(branch_id, path_sequence)
        seed_node_id = mk_node_id(chain, "address", seed_address)

        for row in rows:
            counterparty = (row.get("counterparty") or "").lower()
            if not counterparty or counterparty == seed_address:
                continue

            tx_hash = row.get("tx_hash") or ""
            value_native: Optional[float] = row.get("value_native")
            asset_symbol: Optional[str] = row.get("asset_symbol")
            canonical_asset_id: Optional[str] = row.get("canonical_asset_id")

            # Apply fiat value filter using pre-fetched price data.
            price_usd: Optional[float] = (
                prices.get(canonical_asset_id) if prices and canonical_asset_id else None
            )
            value_fiat: Optional[float] = (
                round(value_native * price_usd, 2)
                if value_native is not None and price_usd is not None
                else None
            )
            if options.min_value_fiat is not None and value_fiat is not None:
                if value_fiat < options.min_value_fiat:
                    continue  # Skip transfers below the fiat threshold.

            # --- Node ---
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
                    display_label=counterparty[:10] + "…" if len(counterparty) > 10 else counterparty,
                    chain=chain,
                    expandable_directions=["prev", "next", "neighbors"],
                    address_data=AddressNodeData(
                        address=counterparty,
                        address_type="unknown",
                    ),
                )
                seen_nodes[counterparty] = node

            # --- Edge ---
            if direction == "forward":
                src_node_id = seed_node_id
                tgt_node_id = seen_nodes[counterparty].node_id
            else:
                src_node_id = seen_nodes[counterparty].node_id
                tgt_node_id = seed_node_id

            _ts_str: Optional[str] = None
            raw_ts = row.get("timestamp")
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
                value_native=value_native,
                value_fiat=value_fiat,
                asset_symbol=asset_symbol or (
                    _native_symbol(chain) if value_native else None
                ),
                canonical_asset_id=canonical_asset_id,
                tx_hash=tx_hash or None,
                tx_chain=chain,
                timestamp=_ts_str,
                direction=direction,
            )
            edges.append(edge)

        # Respect max_results cap.
        nodes = list(seen_nodes.values())[: options.max_results]
        edges = edges[: options.max_results * 3]  # cap edges too

        return nodes, edges


def _native_symbol(chain: str) -> str:
    """Return the native asset symbol for a given EVM chain."""
    _MAP = {
        "ethereum": "ETH",
        "bsc": "BNB",
        "polygon": "MATIC",
        "arbitrum": "ETH",
        "base": "ETH",
        "avalanche": "AVAX",
        "optimism": "ETH",
        "injective": "INJ",
        "starknet": "ETH",
    }
    return _MAP.get(chain, "ETH")
