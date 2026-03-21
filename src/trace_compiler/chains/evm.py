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

from dataclasses import dataclass
import logging
from datetime import datetime
from datetime import timezone
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
from src.trace_compiler.lineage import swap_event_id as mk_swap_event_id
from src.trace_compiler.models import ActivitySummary
from src.trace_compiler.models import AddressNodeData
from src.trace_compiler.models import ExpandOptions
from src.trace_compiler.models import InvestigationEdge
from src.trace_compiler.models import InvestigationNode
from src.trace_compiler.models import SwapEventData
from src.trace_compiler.price_oracle import price_oracle

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


@dataclass(frozen=True)
class _SwapLeg:
    """Minimal value leg used to infer a seed-centric swap event."""

    address: str
    asset_symbol: str
    canonical_asset_id: Optional[str]
    amount: float


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
        if chain not in EVM_CHAINS:
            raise ValueError(f"EVMChainCompiler does not support chain '{chain}'")
        addr = seed_address.lower()
        rows = await self._fetch_outbound_event_store(addr, chain, options)
        if not rows:
            rows = await self._fetch_outbound_neo4j(addr, chain, options)

        prices = await self._prefetch_prices(rows)
        return await self._build_graph(
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
        if chain not in EVM_CHAINS:
            raise ValueError(f"EVMChainCompiler does not support chain '{chain}'")
        addr = seed_address.lower()
        rows = await self._fetch_inbound_event_store(addr, chain, options)
        if not rows:
            rows = await self._fetch_inbound_neo4j(addr, chain, options)

        prices = await self._prefetch_prices(rows)
        return await self._build_graph(
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
                ORDER BY timestamp DESC, tx_hash ASC
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
                ORDER BY timestamp DESC, tx_hash ASC
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
                ORDER BY timestamp DESC, tx_hash ASC
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
            asset_filter = options.asset_filter or []
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
                  AND ($3::text[] IS NULL OR asset_symbol = ANY($3))
                ORDER BY timestamp DESC, tx_hash ASC
                LIMIT $4
            """
            async with self._pg.acquire() as conn:
                rows = await conn.fetch(sql, chain, address, asset_filter or None, limit)
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.debug("_fetch_inbound_token_transfers failed: %s", exc)
            return []

    async def _fetch_tx_token_transfers(
        self,
        chain: str,
        tx_hash: str,
    ) -> List[Dict[str, Any]]:
        """Return all persisted token-transfer legs for a single transaction."""
        if self._pg is None:
            return []
        try:
            sql = """
                SELECT
                    transfer_index,
                    asset_symbol,
                    canonical_asset_id,
                    from_address,
                    to_address,
                    amount_normalized
                FROM raw_token_transfers
                WHERE blockchain = $1
                  AND tx_hash = $2
                ORDER BY transfer_index ASC
            """
            async with self._pg.acquire() as conn:
                rows = await conn.fetch(sql, chain, tx_hash)
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.debug("_fetch_tx_token_transfers failed for %s/%s: %s", chain, tx_hash, exc)
            return []

    async def _fetch_tx_native_leg(
        self,
        chain: str,
        tx_hash: str,
    ) -> Optional[Dict[str, Any]]:
        """Return the native-value leg for a transaction when one exists."""
        if self._pg is None:
            return None
        try:
            sql = """
                SELECT
                    from_address,
                    to_address,
                    value_native,
                    timestamp
                FROM raw_transactions
                WHERE blockchain = $1
                  AND tx_hash = $2
                LIMIT 1
            """
            async with self._pg.acquire() as conn:
                row = await conn.fetchrow(sql, chain, tx_hash)
            return dict(row) if row else None
        except Exception as exc:
            logger.debug("_fetch_tx_native_leg failed for %s/%s: %s", chain, tx_hash, exc)
            return None

    @staticmethod
    def _pick_swap_leg(
        legs: List[_SwapLeg],
        preferred_counterparty: str,
    ) -> Optional[_SwapLeg]:
        """Pick the strongest swap leg, preferring the matched service contract."""
        if not legs:
            return None
        preferred = preferred_counterparty.lower()
        return sorted(
            legs,
            key=lambda leg: (
                0 if leg.address == preferred else 1,
                -(leg.amount or 0.0),
                leg.asset_symbol,
            ),
        )[0]

    async def _maybe_build_swap_event(
        self,
        *,
        tx_hash: str,
        seed_node_id: str,
        seed_address: str,
        counterparty: str,
        chain: str,
        session_id: str,
        branch_id: str,
        path_id: str,
        depth: int,
        direction: str,
        timestamp: Optional[str],
        protocol_id: str,
        protocol_label: str,
        protocol_type: str,
    ) -> Optional[Tuple[List[InvestigationNode], List[InvestigationEdge]]]:
        """Promote a DEX / aggregator interaction into a first-class swap event.

        The current raw store does not persist a full generic EVM log stream, so
        this infers a swap from the same transaction's native leg plus ERC-20
        transfer events already extracted into ``raw_token_transfers``.
        """
        token_legs = await self._fetch_tx_token_transfers(chain, tx_hash)
        native_leg = await self._fetch_tx_native_leg(chain, tx_hash)

        outgoing: List[_SwapLeg] = []
        incoming: List[_SwapLeg] = []

        if native_leg:
            native_from = (native_leg.get("from_address") or "").lower()
            native_to = (native_leg.get("to_address") or "").lower()
            native_value = native_leg.get("value_native")
            if native_value:
                native_amount = float(native_value)
                native_symbol = _native_symbol(chain)
                native_asset_id = _native_canonical_asset_id(chain)
                if native_from == seed_address and native_to:
                    outgoing.append(
                        _SwapLeg(
                            address=native_to,
                            asset_symbol=native_symbol,
                            canonical_asset_id=native_asset_id,
                            amount=native_amount,
                        )
                    )
                if native_to == seed_address and native_from:
                    incoming.append(
                        _SwapLeg(
                            address=native_from,
                            asset_symbol=native_symbol,
                            canonical_asset_id=native_asset_id,
                            amount=native_amount,
                        )
                    )
                if timestamp is None:
                    native_ts = native_leg.get("timestamp")
                    if isinstance(native_ts, datetime):
                        timestamp = native_ts.isoformat()
                    elif isinstance(native_ts, str):
                        timestamp = native_ts

        for leg in token_legs:
            from_addr = (leg.get("from_address") or "").lower()
            to_addr = (leg.get("to_address") or "").lower()
            amount = leg.get("amount_normalized")
            if amount in (None, 0):
                continue

            symbol = leg.get("asset_symbol") or leg.get("canonical_asset_id")
            if not symbol:
                continue

            swap_leg = _SwapLeg(
                address=to_addr if from_addr == seed_address else from_addr,
                asset_symbol=str(symbol).upper(),
                canonical_asset_id=leg.get("canonical_asset_id"),
                amount=float(amount),
            )
            if from_addr == seed_address:
                outgoing.append(swap_leg)
            if to_addr == seed_address:
                incoming.append(swap_leg)

        input_leg = self._pick_swap_leg(outgoing, counterparty)
        output_leg = self._pick_swap_leg(incoming, counterparty)
        if input_leg is None or output_leg is None:
            return None

        if (
            input_leg.asset_symbol == output_leg.asset_symbol
            and abs(input_leg.amount - output_leg.amount) < 1e-12
        ):
            return None

        swap_id = mk_swap_event_id(chain, tx_hash, 0)
        swap_node_id = mk_node_id(chain, "swap_event", swap_id)
        lineage = mk_lineage(session_id, branch_id, path_id, depth)
        route_summary = f"{input_leg.asset_symbol} -> {output_leg.asset_symbol}"
        exchange_rate = (
            output_leg.amount / input_leg.amount
            if input_leg.amount not in (0, None)
            else None
        )

        swap_node = InvestigationNode(
            node_id=swap_node_id,
            lineage_id=lineage,
            node_type="swap_event",
            branch_id=branch_id,
            path_id=path_id,
            depth=depth + 1,
            display_label=protocol_label,
            display_sublabel=route_summary,
            chain=chain,
            expandable_directions=[],
            swap_event_data=SwapEventData(
                swap_id=swap_id,
                protocol_id=protocol_id,
                chain=chain,
                input_asset=input_leg.asset_symbol,
                input_amount=input_leg.amount,
                output_asset=output_leg.asset_symbol,
                output_amount=output_leg.amount,
                exchange_rate=exchange_rate,
                route_summary=route_summary,
                tx_hash=tx_hash,
                timestamp=timestamp,
            ),
            activity_summary=ActivitySummary(
                activity_type=(
                    "router_interaction"
                    if protocol_type == "aggregator"
                    else "dex_interaction"
                ),
                title=f"{protocol_label} swap",
                protocol_id=protocol_id,
                protocol_type=protocol_type,
                tx_hash=tx_hash,
                tx_chain=chain,
                timestamp=timestamp,
                direction=direction,
                contract_address=counterparty,
                asset_symbol=input_leg.asset_symbol,
                canonical_asset_id=input_leg.canonical_asset_id,
                value_native=input_leg.amount,
                source_asset=input_leg.asset_symbol,
                destination_asset=output_leg.asset_symbol,
                source_amount=input_leg.amount,
                destination_amount=output_leg.amount,
                route_summary=route_summary,
            ),
        )

        swap_input_edge = InvestigationEdge(
            edge_id=mk_edge_id(seed_node_id, swap_node_id, branch_id, f"{tx_hash}:swap_input"),
            source_node_id=seed_node_id,
            target_node_id=swap_node_id,
            branch_id=branch_id,
            path_id=path_id,
            edge_type="swap_input",
            value_native=input_leg.amount,
            asset_symbol=input_leg.asset_symbol,
            canonical_asset_id=input_leg.canonical_asset_id,
            tx_hash=tx_hash,
            tx_chain=chain,
            timestamp=timestamp,
            direction=direction,
        )
        swap_output_edge = InvestigationEdge(
            edge_id=mk_edge_id(swap_node_id, seed_node_id, branch_id, f"{tx_hash}:swap_output"),
            source_node_id=swap_node_id,
            target_node_id=seed_node_id,
            branch_id=branch_id,
            path_id=path_id,
            edge_type="swap_output",
            value_native=output_leg.amount,
            asset_symbol=output_leg.asset_symbol,
            canonical_asset_id=output_leg.canonical_asset_id,
            tx_hash=tx_hash,
            tx_chain=chain,
            timestamp=timestamp,
            direction=direction,
        )
        return [swap_node], [swap_input_edge, swap_output_edge]

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

    async def _build_graph(
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

        For each row, checks whether the counterparty address is a known bridge
        contract.  If so, delegates to ``BridgeHopCompiler.process_row()`` to
        produce a semantically-correct ``bridge_hop`` node instead of a raw
        address node.  Non-bridge transfers are handled as before.

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
        handled_swap_txs: set[str] = set()

        _path = mk_path(branch_id, path_sequence)
        seed_node_id = mk_node_id(chain, "address", seed_address)

        for row in rows:
            counterparty = (row.get("counterparty") or "").lower()
            if not counterparty or counterparty == seed_address:
                continue

            tx_hash = row.get("tx_hash") or ""
            if tx_hash in handled_swap_txs:
                continue
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
            # Intentional: edges with value_fiat=None (price unavailable) always
            # pass through the min_value_fiat filter.  Silently dropping them
            # would hide transfers from investigators simply because we lack a
            # price for that asset.  Investigators must scroll past them; they
            # are not filtered by threshold.
            if options.min_value_fiat is not None and value_fiat is not None:
                if value_fiat < options.min_value_fiat:
                    continue  # Skip transfers confirmed below the fiat threshold.

            _ts_str: Optional[str] = None
            raw_ts = row.get("timestamp")
            if isinstance(raw_ts, datetime):
                _ts_str = raw_ts.isoformat()
            elif isinstance(raw_ts, str):
                _ts_str = raw_ts

            # --- Bridge hop detection (forward only) ---
            # Bridge contracts take priority over service classification because
            # they produce richer nodes (correlation status, destination chain).
            if direction == "forward" and self._bridge.is_bridge_contract(
                chain, counterparty
            ):
                bridge_result = await self._bridge.process_row(
                    tx_hash=tx_hash,
                    to_address=counterparty,
                    source_chain=chain,
                    seed_node_id=seed_node_id,
                    session_id=session_id,
                    branch_id=branch_id,
                    path_id=_path,
                    depth=depth,
                    timestamp=_ts_str,
                    value_native=value_native,
                    value_fiat=value_fiat,
                    asset_symbol=asset_symbol or _native_symbol(chain),
                    canonical_asset_id=canonical_asset_id,
                )
                if bridge_result is not None:
                    bridge_nodes, bridge_edges = bridge_result
                    for bn in bridge_nodes:
                        if bn.node_id not in seen_nodes:
                            seen_nodes[bn.node_id] = bn
                    edges.extend(bridge_edges)
                    continue  # Do not also create a plain address node.

            service_record = self._service.get_record(chain, counterparty)

            # --- Semantic swap promotion (DEX / aggregator only) ---
            if service_record is not None and service_record.service_type in {"dex", "aggregator"}:
                swap_result = await self._maybe_build_swap_event(
                    tx_hash=tx_hash,
                    seed_node_id=seed_node_id,
                    seed_address=seed_address,
                    counterparty=counterparty,
                    chain=chain,
                    session_id=session_id,
                    branch_id=branch_id,
                    path_id=_path,
                    depth=depth,
                    direction=direction,
                    timestamp=_ts_str,
                    protocol_id=service_record.protocol_id,
                    protocol_label=service_record.display_name,
                    protocol_type=service_record.service_type,
                )
                if swap_result is not None:
                    swap_nodes, swap_edges = swap_result
                    for swap_node in swap_nodes:
                        seen_nodes.setdefault(swap_node.node_id, swap_node)
                    edges.extend(swap_edges)
                    handled_swap_txs.add(tx_hash)
                    continue

            # --- Service classification (both directions) ---
            # Non-bridge protocol contracts (DEX, aggregator, mixer, lending)
            # are reclassified here so investigators see a named service node
            # rather than an anonymous contract address.
            svc_result = await self._service.process_row(
                tx_hash=tx_hash,
                to_address=counterparty,
                chain=chain,
                seed_node_id=seed_node_id,
                session_id=session_id,
                branch_id=branch_id,
                path_id=_path,
                depth=depth,
                timestamp=_ts_str,
                value_native=value_native,
                value_fiat=value_fiat,
                asset_symbol=asset_symbol or (_native_symbol(chain) if value_native else None),
                canonical_asset_id=canonical_asset_id,
                direction=direction,
            )
            if svc_result is not None:
                svc_nodes, svc_edges = svc_result
                for sn in svc_nodes:
                    # Service nodes deduplicate by protocol_id across multiple
                    # transfers to the same contract.
                    if sn.node_id not in seen_nodes:
                        seen_nodes[sn.node_id] = sn
                edges.extend(svc_edges)
                continue  # Do not also create a plain address node.

            # --- Plain address node (non-bridge, non-service path) ---
            _cp_node_id = mk_node_id(chain, "address", counterparty)
            if _cp_node_id not in seen_nodes:
                _lineage = mk_lineage(session_id, branch_id, _path, depth + 1)
                node = InvestigationNode(
                    node_id=_cp_node_id,
                    lineage_id=_lineage,
                    node_type="address",
                    branch_id=branch_id,
                    path_id=_path,
                    depth=depth + 1,
                    display_label=(
                        counterparty[:10] + "…"
                        if len(counterparty) > 10
                        else counterparty
                    ),
                    chain=chain,
                    expandable_directions=["prev", "next", "neighbors"],
                    address_data=AddressNodeData(
                        address=counterparty,
                        address_type="unknown",
                    ),
                )
                seen_nodes[_cp_node_id] = node

            # --- Edge ---
            if direction == "forward":
                src_node_id = seed_node_id
                tgt_node_id = _cp_node_id
            else:
                src_node_id = _cp_node_id
                tgt_node_id = seed_node_id

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


def _native_canonical_asset_id(chain: str) -> Optional[str]:
    """Return a stable canonical asset identifier for a chain's native token."""
    _MAP = {
        "ethereum": "ethereum",
        "bsc": "binancecoin",
        "polygon": "matic-network",
        "arbitrum": "ethereum",
        "base": "ethereum",
        "avalanche": "avalanche-2",
        "optimism": "ethereum",
        "injective": "injective-protocol",
        "starknet": "ethereum",
    }
    return _MAP.get(chain)
