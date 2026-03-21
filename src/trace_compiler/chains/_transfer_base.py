"""
_GenericTransferChainCompiler — shared base for transfer-style chain compilers.

Provides all common event-store SQL query methods, price prefetching, and the
main graph construction loop (_build_graph) shared by EVMChainCompiler,
TronChainCompiler, and XRPChainCompiler.

Chain-specific subclasses implement:
- ``_native_symbol(chain)`` — native asset ticker (e.g. "ETH", "TRX", "XRP").
- ``_native_canonical_asset_id(chain)`` — CoinGecko-stable ID or None.
- ``_normalize_address(addr)`` — defaults to lower(); XRP overrides to noop.
- ``_try_swap_promotion(...)`` — hook for DEX swap node promotion; default
  returns None (no-op).  EVM overrides this to call ``_maybe_build_swap_event``.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
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
from src.trace_compiler.price_oracle import price_oracle

logger = logging.getLogger(__name__)

# Maximum rows fetched from the event store per expansion call before
# pagination.  This is the per-SQL LIMIT, not the max_results option.
SQL_FETCH_LIMIT = 500


class _GenericTransferChainCompiler(BaseChainCompiler):
    """Shared base class for EVM, Tron, and XRP chain compilers.

    Supplies the common event-store query layer (raw_transactions +
    raw_token_transfers) and the ``_build_graph`` loop that converts raw rows
    into InvestigationNode / InvestigationEdge objects.

    Subclasses must implement:
        - ``supported_chains`` property
        - ``_native_symbol(chain)``
        - ``_native_canonical_asset_id(chain)``

    Subclasses may override:
        - ``_normalize_address(addr)`` — default is ``addr.lower()``
        - ``_try_swap_promotion(...)`` — default returns ``None``
    """

    # ------------------------------------------------------------------
    # Abstract methods subclasses must implement
    # ------------------------------------------------------------------

    @abstractmethod
    def _native_symbol(self, chain: str) -> str:
        """Return the native asset ticker for ``chain`` (e.g. ``"ETH"``).

        Args:
            chain: Lowercase blockchain name.

        Returns:
            Ticker string in uppercase.
        """

    @abstractmethod
    def _native_canonical_asset_id(self, chain: str) -> Optional[str]:
        """Return the CoinGecko-stable asset ID for the chain's native token.

        Args:
            chain: Lowercase blockchain name.

        Returns:
            CoinGecko asset ID string, or None when not mapped.
        """

    # ------------------------------------------------------------------
    # Overridable hooks
    # ------------------------------------------------------------------

    def _normalize_address(self, addr: str) -> str:
        """Normalize an address string for this chain.

        Default implementation lowercases.  XRP overrides to return unchanged.

        Args:
            addr: Raw address string from the event store.

        Returns:
            Normalized address.
        """
        return addr.lower()

    async def _try_swap_promotion(
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
        service_record: Any,
    ) -> Optional[Tuple[List[InvestigationNode], List[InvestigationEdge]]]:
        """Attempt to promote a DEX interaction into a swap_event node.

        Default implementation returns None (no promotion).  EVM subclass
        overrides this to call ``_maybe_build_swap_event`` when the service
        record indicates a DEX or aggregator.

        Args:
            tx_hash:        Transaction hash.
            seed_node_id:   Node ID of the address being expanded.
            seed_address:   Normalized address being expanded.
            counterparty:   Normalized counterparty address.
            chain:          Blockchain name.
            session_id:     Investigation session UUID.
            branch_id:      Branch ID for lineage.
            path_id:        Path ID for lineage.
            depth:          Current hop depth.
            direction:      ``"forward"`` or ``"backward"``.
            timestamp:      ISO-8601 string or None.
            service_record: ServiceRecord from the service classifier, or None.

        Returns:
            (nodes, edges) tuple on success, or None to fall through.
        """
        return None

    # ------------------------------------------------------------------
    # Event store queries — identical SQL to EVMChainCompiler
    # ------------------------------------------------------------------

    async def _fetch_outbound_event_store(
        self, address: str, chain: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Fetch outbound transfers from the PostgreSQL event store.

        Returns an empty list (not an error) when the event store has no
        data for this address or when the pool is unavailable.

        Args:
            address: Normalized sender address.
            chain:   Blockchain name.
            options: Expansion options (filters, max_results).

        Returns:
            List of row dicts with counterparty, tx_hash, value_native,
            asset_symbol, canonical_asset_id, timestamp keys.
        """
        if self._pg is None:
            return []
        try:
            limit = min(options.max_results, SQL_FETCH_LIMIT)
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
                "%s._fetch_outbound_event_store failed for %s/%s: %s",
                self.__class__.__name__,
                chain,
                address,
                exc,
            )
            return []

    async def _fetch_inbound_event_store(
        self, address: str, chain: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Fetch inbound transfers from the PostgreSQL event store.

        Args:
            address: Normalized recipient address.
            chain:   Blockchain name.
            options: Expansion options (filters, max_results).

        Returns:
            List of row dicts (see ``_fetch_outbound_event_store``).
        """
        if self._pg is None:
            return []
        try:
            limit = min(options.max_results, SQL_FETCH_LIMIT)
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
                "%s._fetch_inbound_event_store failed: %s",
                self.__class__.__name__,
                exc,
            )
            return []

    async def _fetch_outbound_token_transfers(
        self, address: str, chain: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Fetch outbound token transfers (ERC-20 / TRC-20 / XRP issued assets).

        Args:
            address: Normalized sender address.
            chain:   Blockchain name.
            options: Expansion options (filters, max_results).

        Returns:
            List of row dicts with token transfer fields.
        """
        if self._pg is None:
            return []
        try:
            limit = min(options.max_results, SQL_FETCH_LIMIT)
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
            logger.debug(
                "%s._fetch_outbound_token_transfers failed: %s",
                self.__class__.__name__,
                exc,
            )
            return []

    async def _fetch_inbound_token_transfers(
        self, address: str, chain: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Fetch inbound token transfers (ERC-20 / TRC-20 / XRP issued assets).

        Args:
            address: Normalized recipient address.
            chain:   Blockchain name.
            options: Expansion options (filters, max_results).

        Returns:
            List of row dicts with token transfer fields.
        """
        if self._pg is None:
            return []
        try:
            limit = min(options.max_results, SQL_FETCH_LIMIT)
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
            logger.debug(
                "%s._fetch_inbound_token_transfers failed: %s",
                self.__class__.__name__,
                exc,
            )
            return []

    async def _fetch_tx_token_transfers(
        self,
        chain: str,
        tx_hash: str,
    ) -> List[Dict[str, Any]]:
        """Return all persisted token-transfer legs for a single transaction.

        Args:
            chain:   Blockchain name.
            tx_hash: Transaction hash.

        Returns:
            List of row dicts with transfer_index, asset_symbol, from/to addresses,
            amount_normalized.
        """
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
            logger.debug(
                "%s._fetch_tx_token_transfers failed for %s/%s: %s",
                self.__class__.__name__,
                chain,
                tx_hash,
                exc,
            )
            return []

    async def _fetch_tx_native_leg(
        self,
        chain: str,
        tx_hash: str,
    ) -> Optional[Dict[str, Any]]:
        """Return the native-value leg for a transaction when one exists.

        Args:
            chain:   Blockchain name.
            tx_hash: Transaction hash.

        Returns:
            Dict with from_address, to_address, value_native, timestamp, or None.
        """
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
            logger.debug(
                "%s._fetch_tx_native_leg failed for %s/%s: %s",
                self.__class__.__name__,
                chain,
                tx_hash,
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # Price prefetching
    # ------------------------------------------------------------------

    async def _prefetch_prices(
        self, rows: List[Dict[str, Any]]
    ) -> Dict[str, Optional[float]]:
        """Bulk-fetch USD prices for all canonical assets referenced in *rows*.

        Calls ``price_oracle.get_prices_bulk`` once per expansion so that
        ``_build_graph`` can annotate edges with fiat values without a per-row
        database round-trip.

        Args:
            rows: Raw transfer rows that may contain ``canonical_asset_id``.

        Returns:
            Dict mapping canonical_asset_id → USD price (or None when unknown).
        """
        asset_ids = list({
            row["canonical_asset_id"]
            for row in rows
            if row.get("canonical_asset_id")
        })
        if not asset_ids:
            return {}
        return await price_oracle.get_prices_bulk(asset_ids)

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

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

        For each row, checks bridge detection → swap promotion hook → service
        classification → plain address node, in that priority order.

        Args:
            rows:           List of dicts: counterparty, tx_hash, value_native,
                            asset_symbol, canonical_asset_id, timestamp.
            session_id:     Investigation session UUID.
            branch_id:      Branch ID for lineage assignment.
            path_sequence:  Integer used to derive the path_id.
            depth:          Hop depth from session root.
            seed_address:   The address being expanded (already normalized).
            chain:          Blockchain name.
            direction:      ``"forward"`` or ``"backward"``.
            options:        Expansion options (used for value filtering).
            prices:         Pre-fetched price map from ``_prefetch_prices``.

        Returns:
            Tuple of (nodes, edges).
        """
        seen_nodes: Dict[str, InvestigationNode] = {}
        edges: List[InvestigationEdge] = []
        handled_swap_txs: set = set()

        _path = mk_path(branch_id, path_sequence)
        seed_node_id = mk_node_id(chain, "address", seed_address)

        for row in rows:
            raw_counterparty: str = row.get("counterparty") or ""
            if not raw_counterparty:
                continue
            counterparty = self._normalize_address(raw_counterparty)
            if counterparty == seed_address:
                continue

            tx_hash: str = row.get("tx_hash") or ""
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
            # price for that asset.
            if options.min_value_fiat is not None and value_fiat is not None:
                if value_fiat < options.min_value_fiat:
                    continue

            _ts_str: Optional[str] = None
            raw_ts = row.get("timestamp")
            if isinstance(raw_ts, datetime):
                _ts_str = raw_ts.isoformat()
            elif isinstance(raw_ts, str):
                _ts_str = raw_ts

            # --- Bridge hop detection (forward only) ---
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
                    asset_symbol=asset_symbol or self._native_symbol(chain),
                    canonical_asset_id=canonical_asset_id,
                )
                if bridge_result is not None:
                    bridge_nodes, bridge_edges = bridge_result
                    for bn in bridge_nodes:
                        if bn.node_id not in seen_nodes:
                            seen_nodes[bn.node_id] = bn
                    edges.extend(bridge_edges)
                    continue

            service_record = self._service.get_record(chain, counterparty)

            # --- Swap promotion hook (chain-specific; default is no-op) ---
            if service_record is not None and service_record.service_type in {
                "dex", "aggregator"
            }:
                swap_result = await self._try_swap_promotion(
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
                    service_record=service_record,
                )
                if swap_result is not None:
                    swap_nodes, swap_edges = swap_result
                    for swap_node in swap_nodes:
                        seen_nodes.setdefault(swap_node.node_id, swap_node)
                    edges.extend(swap_edges)
                    handled_swap_txs.add(tx_hash)
                    continue

            # --- Service classification (both directions) ---
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
                asset_symbol=asset_symbol or (
                    self._native_symbol(chain) if value_native else None
                ),
                canonical_asset_id=canonical_asset_id,
                direction=direction,
            )
            if svc_result is not None:
                svc_nodes, svc_edges = svc_result
                for sn in svc_nodes:
                    if sn.node_id not in seen_nodes:
                        seen_nodes[sn.node_id] = sn
                edges.extend(svc_edges)
                continue

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
                    self._native_symbol(chain) if value_native else None
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
        edges = edges[: options.max_results * 3]

        return nodes, edges
