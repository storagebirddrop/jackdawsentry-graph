"""
_GenericTransferChainCompiler — shared base for transfer-style chain compilers.

Provides all common event-store SQL query methods, price prefetching, and the
main graph construction loop (_build_graph) shared by EVMChainCompiler,
TronChainCompiler, XRPChainCompiler, CosmosChainCompiler, and SuiChainCompiler.

Chain-specific subclasses implement:
- ``_native_symbol(chain)`` — native asset ticker (e.g. "ETH", "TRX", "XRP").
- ``_native_canonical_asset_id(chain)`` — CoinGecko-stable ID or None.
- ``_normalize_address(addr)`` — defaults to lower(); XRP overrides to noop.
- ``_try_swap_promotion(...)`` — hook for DEX swap node promotion; default
  returns None (no-op).  EVM and Tron override this to call
  ``_maybe_build_swap_event``.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from src.trace_compiler.asset_selection import effective_asset_selector
from src.trace_compiler.asset_selection import normalize_chain_asset_id
from src.trace_compiler.chains.base import BaseChainCompiler
from src.trace_compiler.chains.evm_log_decoder import DEX_SWAP_SIGS
from src.trace_compiler.chains.evm_log_decoder import decode_swap_log
from src.trace_compiler.chains.evm_log_decoder import extract_swap_amounts
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

    def _normalized_asset_filters(
        self,
        chain: str,
        options: ExpandOptions,
    ) -> tuple[list[str], list[str], list[str], bool]:
        """Return normalized symbol, canonical-id, address, and native filters."""
        selector = effective_asset_selector(options, chain=chain)
        if options.asset_selector is not None:
            symbol_filters: set[str] = set()
            canonical_filters: set[str] = set()
            asset_address_filters: set[str] = set()
            native_selected = False

            if selector.mode == "native":
                native_selected = True
            elif selector.mode == "asset":
                if selector.asset_symbol:
                    symbol_filters.add(selector.asset_symbol.upper())
                if selector.canonical_asset_id:
                    canonical_filters.add(selector.canonical_asset_id.lower())
                normalized_chain_asset_id = normalize_chain_asset_id(
                    chain,
                    selector.chain_asset_id,
                )
                if normalized_chain_asset_id:
                    asset_address_filters.add(normalized_chain_asset_id)

            return (
                sorted(symbol_filters),
                sorted(canonical_filters),
                sorted(asset_address_filters),
                native_selected,
            )

        raw_values = [
            str(value).strip()
            for value in (options.asset_filter or [])
            if str(value).strip()
        ]
        symbol_filters: set[str] = set()
        canonical_filters: set[str] = set()
        asset_address_filters: set[str] = set()
        native_selected = False
        normalized_chain = (chain or "").strip().lower()

        for value in raw_values:
            lowered = value.lower()
            if lowered.startswith("symbol:"):
                symbol_filters.add(value.split(":", 1)[1].strip().upper())
                continue
            if lowered.startswith("canonical:"):
                canonical_filters.add(value.split(":", 1)[1].strip().lower())
                continue
            if lowered.startswith("native:"):
                selector_chain = value.split(":", 1)[1].strip().lower()
                if selector_chain == normalized_chain:
                    native_selected = True
                continue
            if lowered.startswith("asset:"):
                parts = value.split(":", 2)
                if len(parts) == 3 and parts[1].strip().lower() == normalized_chain:
                    asset_address_filters.add(parts[2].strip().lower())
                continue
            symbol_filters.add(value.upper())
            canonical_filters.add(lowered)

        return (
            sorted(symbol_filters),
            sorted(canonical_filters),
            sorted(asset_address_filters),
            native_selected,
        )

    def _include_native_asset(self, chain: str, options: ExpandOptions) -> bool:
        """Return True when the native asset should be included for a query."""
        if not options.asset_filter:
            return True
        symbol_filters, canonical_filters, _, native_selected = self._normalized_asset_filters(
            chain,
            options,
        )
        native_symbol = self._native_symbol(chain).upper()
        native_asset_id = self._native_canonical_asset_id(chain)
        return native_selected or native_symbol in symbol_filters or (
            native_asset_id is not None and native_asset_id.lower() in canonical_filters
        )

    def _include_token_assets(self, chain: str, options: ExpandOptions) -> bool:
        """Return True when non-native token transfers should be queried."""
        if not options.asset_filter:
            return True
        (
            symbol_filters,
            canonical_filters,
            asset_address_filters,
            _native_selected,
        ) = self._normalized_asset_filters(chain, options)
        native_symbol = self._native_symbol(chain).upper()
        native_asset_id = self._native_canonical_asset_id(chain)
        symbol_only = {value for value in symbol_filters if value != native_symbol}
        canonical_only = {
            value
            for value in canonical_filters
            if native_asset_id is None or value != native_asset_id.lower()
        }
        return bool(symbol_only or canonical_only or asset_address_filters)

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

    async def _try_tx_type_swap_promotion(
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
        tx_type: Optional[str],
    ) -> Optional[Tuple[List[InvestigationNode], List[InvestigationEdge]]]:
        """Attempt swap promotion based on the transaction's native type field.

        Fires before the service-classifier-based ``_try_swap_promotion`` hook,
        allowing chains where DEX activity is identified by transaction type
        rather than contract address (XRP AMM ``AMMSwap``, Cosmos
        ``MsgSwapExactAmountIn`` / ``MsgSwapExactAmountOut``) to promote the
        row to a ``swap_event`` node without a service registry entry.

        Default implementation returns None (no promotion).  XRP and Cosmos
        compilers override this hook.

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
            tx_type:        Chain-native transaction type string, or None.

        Returns:
            (nodes, edges) on success, or None to fall through.
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
            result = []

            if self._include_native_asset(chain, options):
                sql = """
                    SELECT
                        tx_hash,
                        to_address    AS counterparty,
                        value_native,
                        NULL          AS asset_symbol,
                        NULL          AS canonical_asset_id,
                        NULL          AS asset_address,
                        timestamp,
                        tx_type
                    FROM raw_transactions
                    WHERE blockchain = $1
                      AND from_address = $2
                      AND to_address IS NOT NULL
                      AND value_native > 0
                      AND ($4::timestamptz IS NULL OR timestamp >= $4)
                      AND ($5::timestamptz IS NULL OR timestamp <= $5)
                    ORDER BY timestamp DESC, tx_hash ASC
                    LIMIT $3
                """
                async with self._pg.acquire() as conn:
                    rows = await conn.fetch(
                        sql, chain, address, limit,
                        options.time_from, options.time_to,
                    )
                result = [dict(r) for r in rows]

            # Merge token transfers for the same addresses.
            if self._include_token_assets(chain, options):
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
            result = []

            if self._include_native_asset(chain, options):
                sql = """
                    SELECT
                        tx_hash,
                        from_address  AS counterparty,
                        value_native,
                        NULL          AS asset_symbol,
                        NULL          AS canonical_asset_id,
                        NULL          AS asset_address,
                        timestamp,
                        tx_type
                    FROM raw_transactions
                    WHERE blockchain = $1
                      AND to_address = $2
                      AND from_address IS NOT NULL
                      AND value_native > 0
                      AND ($4::timestamptz IS NULL OR timestamp >= $4)
                      AND ($5::timestamptz IS NULL OR timestamp <= $5)
                    ORDER BY timestamp DESC, tx_hash ASC
                    LIMIT $3
                """
                async with self._pg.acquire() as conn:
                    rows = await conn.fetch(
                        sql, chain, address, limit,
                        options.time_from, options.time_to,
                    )
                result = [dict(r) for r in rows]

            # Merge token transfers for the same addresses.
            if self._include_token_assets(chain, options):
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
            (
                symbol_filters,
                canonical_filters,
                asset_address_filters,
                _native_selected,
            ) = self._normalized_asset_filters(chain, options)
            params: list = [
                chain,
                address,
                limit,
                symbol_filters or None,
                canonical_filters or None,
                asset_address_filters or None,
                options.time_from,
                options.time_to,
            ]

            sql = """
                SELECT
                    rtt.tx_hash,
                    rtt.to_address       AS counterparty,
                    rtt.amount_normalized AS value_native,
                    COALESCE(
                        NULLIF(tmc.symbol, ''),
                        NULLIF(rtt.asset_symbol, ''),
                        rtt.asset_contract
                    ) AS asset_symbol,
                    COALESCE(
                        NULLIF(tmc.canonical_asset_id, ''),
                        NULLIF(rtt.canonical_asset_id, '')
                    ) AS canonical_asset_id,
                    rtt.asset_contract AS asset_address,
                    rtt.timestamp
                FROM raw_token_transfers rtt
                LEFT JOIN token_metadata_cache tmc
                  ON tmc.blockchain = rtt.blockchain
                 AND tmc.asset_address = rtt.asset_contract
                WHERE rtt.blockchain = $1
                  AND rtt.from_address = $2
                  AND (
                    ($4::text[] IS NULL AND $5::text[] IS NULL AND $6::text[] IS NULL)
                    OR (
                        $4::text[] IS NOT NULL
                        AND UPPER(
                            COALESCE(
                                NULLIF(tmc.symbol, ''),
                                NULLIF(rtt.asset_symbol, '')
                            )
                        ) = ANY($4)
                    )
                    OR (
                        $5::text[] IS NOT NULL
                        AND LOWER(
                            COALESCE(
                                NULLIF(tmc.canonical_asset_id, ''),
                                NULLIF(rtt.canonical_asset_id, '')
                            )
                        ) = ANY($5)
                    )
                    OR (
                        $6::text[] IS NOT NULL
                        AND LOWER(COALESCE(rtt.asset_contract, '')) = ANY($6)
                    )
                  )
                  AND ($7::timestamptz IS NULL OR rtt.timestamp >= $7)
                  AND ($8::timestamptz IS NULL OR rtt.timestamp <= $8)
                ORDER BY timestamp DESC, tx_hash ASC
                LIMIT $3
            """
            async with self._pg.acquire() as conn:
                rows = await conn.fetch(sql, *params)
            return [dict(r) for r in rows]
        except Exception as exc:
            # nosemgrep: python-logger-credential-disclosure - logging exception, not credentials
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
            (
                symbol_filters,
                canonical_filters,
                asset_address_filters,
                _native_selected,
            ) = self._normalized_asset_filters(chain, options)
            sql = """
                SELECT
                    rtt.tx_hash,
                    rtt.from_address      AS counterparty,
                    rtt.amount_normalized AS value_native,
                    COALESCE(
                        NULLIF(tmc.symbol, ''),
                        NULLIF(rtt.asset_symbol, ''),
                        rtt.asset_contract
                    ) AS asset_symbol,
                    COALESCE(
                        NULLIF(tmc.canonical_asset_id, ''),
                        NULLIF(rtt.canonical_asset_id, '')
                    ) AS canonical_asset_id,
                    rtt.asset_contract AS asset_address,
                    rtt.timestamp
                FROM raw_token_transfers rtt
                LEFT JOIN token_metadata_cache tmc
                  ON tmc.blockchain = rtt.blockchain
                 AND tmc.asset_address = rtt.asset_contract
                WHERE rtt.blockchain = $1
                  AND rtt.to_address = $2
                  AND (
                    ($3::text[] IS NULL AND $4::text[] IS NULL AND $5::text[] IS NULL)
                    OR (
                        $3::text[] IS NOT NULL
                        AND UPPER(
                            COALESCE(
                                NULLIF(tmc.symbol, ''),
                                NULLIF(rtt.asset_symbol, '')
                            )
                        ) = ANY($3)
                    )
                    OR (
                        $4::text[] IS NOT NULL
                        AND LOWER(
                            COALESCE(
                                NULLIF(tmc.canonical_asset_id, ''),
                                NULLIF(rtt.canonical_asset_id, '')
                            )
                        ) = ANY($4)
                    )
                    OR (
                        $5::text[] IS NOT NULL
                        AND LOWER(COALESCE(rtt.asset_contract, '')) = ANY($5)
                    )
                  )
                  AND ($7::timestamptz IS NULL OR rtt.timestamp >= $7)
                  AND ($8::timestamptz IS NULL OR rtt.timestamp <= $8)
                ORDER BY timestamp DESC, tx_hash ASC
                LIMIT $6
            """
            async with self._pg.acquire() as conn:
                rows = await conn.fetch(
                    sql,
                    chain,
                    address,
                    symbol_filters or None,
                    canonical_filters or None,
                    asset_address_filters or None,
                    limit,
                    options.time_from,
                    options.time_to,
                )
            return [dict(r) for r in rows]
        except Exception as exc:
            # nosemgrep: python-logger-credential-disclosure - logging exception, not credentials
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
                    asset_contract AS chain_asset_id,
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
            # nosemgrep: python-logger-credential-disclosure - logging exception, not credentials
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
            chain_asset_id = normalize_chain_asset_id(
                chain,
                row.get("chain_asset_id") or row.get("asset_address"),
            )

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
                    chain_asset_id=chain_asset_id,
                )
                if bridge_result is not None:
                    bridge_nodes, bridge_edges = bridge_result
                    for bn in bridge_nodes:
                        if bn.node_id not in seen_nodes:
                            seen_nodes[bn.node_id] = bn
                    edges.extend(bridge_edges)
                    continue

            tx_type: Optional[str] = row.get("tx_type")

            # --- Tx-type swap promotion (XRP AMM / Cosmos DEX; default no-op) ---
            if tx_type is not None:
                tx_type_swap_result = await self._try_tx_type_swap_promotion(
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
                    tx_type=tx_type,
                )
                if tx_type_swap_result is not None:
                    tt_nodes, tt_edges = tx_type_swap_result
                    for tt_node in tt_nodes:
                        seen_nodes.setdefault(tt_node.node_id, tt_node)
                    edges.extend(tt_edges)
                    handled_swap_txs.add(tx_hash)
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
                chain_asset_id=chain_asset_id,
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
                chain_asset_id=chain_asset_id,
                asset_address=row.get("asset_address"),
                asset_chain=chain,
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

    # ------------------------------------------------------------------
    # DEX swap event helpers (shared by EVM and Tron)
    # ------------------------------------------------------------------

    async def _fetch_dex_swap_log(
        self,
        chain: str,
        tx_hash: str,
        contract: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch and decode a DEX Swap event log for a transaction.

        Queries ``raw_evm_logs`` for a Swap event emitted by ``contract``
        in ``tx_hash``.  Returns the decoded field values (amounts, direction)
        so ``_maybe_build_swap_event`` can use ground-truth log data instead
        of token-transfer inference.

        Supports any blockchain partition in ``raw_evm_logs``, including
        ``tron`` (added in migration 013).

        Args:
            chain:    Blockchain name.
            tx_hash:  Transaction hash.
            contract: The DEX contract address (pool or router).

        Returns:
            Dict with ``event_sig`` and ``decoded`` keys, or None.
        """
        if self._pg is None:
            return None
        try:
            sql = """
                SELECT event_sig, decoded, data
                FROM raw_evm_logs
                WHERE blockchain = $1
                  AND tx_hash    = $2
                  AND contract   = $3
                  AND event_sig  = ANY($4)
                ORDER BY log_index ASC
                LIMIT 1
            """
            async with self._pg.acquire() as conn:
                row = await conn.fetchrow(
                    sql,
                    chain,
                    tx_hash,
                    contract.lower(),
                    list(DEX_SWAP_SIGS),
                )
            if row is None:
                return None
            decoded = row["decoded"]
            if decoded is None and row["data"]:
                decoded = decode_swap_log(row["event_sig"], row["data"])
            if decoded is None:
                return None
            return {"event_sig": row["event_sig"], "decoded": decoded}
        except Exception as exc:
            logger.debug(
                "_fetch_dex_swap_log failed chain=%s tx=%s: %s", chain, tx_hash, exc
            )
            return None

    @staticmethod
    def _pick_swap_leg(
        legs: List["_SwapLeg"],
        preferred_counterparty: str,
    ) -> Optional["_SwapLeg"]:
        """Pick the strongest swap leg, preferring the matched service contract.

        Args:
            legs:                  Candidate swap legs (outgoing or incoming).
            preferred_counterparty: Contract address to prefer in tie-breaks.

        Returns:
            The best matching ``_SwapLeg``, or None when legs is empty.
        """
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

        Infers a swap from the transaction's native leg and token-transfer
        events already in the event store.  When a raw DEX Swap log is
        available in ``raw_evm_logs``, its amounts are used as ground truth.

        Shared by EVMChainCompiler and TronChainCompiler.  Returns None when
        swap evidence is insufficient (missing input or output leg), deferring
        to a plain service node.

        Args:
            tx_hash:       Transaction hash.
            seed_node_id:  Node ID of the address being expanded.
            seed_address:  Normalized address being expanded.
            counterparty:  Normalized DEX contract address.
            chain:         Blockchain name.
            session_id:    Investigation session UUID.
            branch_id:     Branch ID for lineage.
            path_id:       Path ID for lineage.
            depth:         Current hop depth.
            direction:     ``"forward"`` or ``"backward"``.
            timestamp:     ISO-8601 string or None.
            protocol_id:   Service classifier protocol identifier.
            protocol_label: Human-readable protocol name.
            protocol_type: ``"dex"`` or ``"aggregator"``.

        Returns:
            (nodes, edges) on success, or None to fall through to plain service node.
        """
        token_legs = await self._fetch_tx_token_transfers(chain, tx_hash)
        native_leg = await self._fetch_tx_native_leg(chain, tx_hash)

        _log_input_amount: Optional[float] = None
        _log_output_amount: Optional[float] = None
        dex_log = await self._fetch_dex_swap_log(chain, tx_hash, counterparty)
        if dex_log is not None:
            amounts = extract_swap_amounts(
                dex_log["decoded"],
                dex_log["event_sig"],
            )
            if amounts is not None:
                _log_input_amount, _log_output_amount, _ = amounts

        outgoing: List[_SwapLeg] = []
        incoming: List[_SwapLeg] = []

        if native_leg:
            native_from = (native_leg.get("from_address") or "").lower()
            native_to = (native_leg.get("to_address") or "").lower()
            native_value = native_leg.get("value_native")
            if native_value:
                native_amount = float(native_value)
                native_symbol = self._native_symbol(chain)
                native_asset_id = self._native_canonical_asset_id(chain)
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
                chain_asset_id=normalize_chain_asset_id(chain, leg.get("chain_asset_id")),
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

        final_input_amount = (
            _log_input_amount if _log_input_amount is not None else input_leg.amount
        )
        final_output_amount = (
            _log_output_amount if _log_output_amount is not None else output_leg.amount
        )

        swap_id = mk_swap_event_id(chain, tx_hash, 0)
        swap_node_id = mk_node_id(chain, "swap_event", swap_id)
        lineage = mk_lineage(session_id, branch_id, path_id, depth)
        route_summary = f"{input_leg.asset_symbol} -> {output_leg.asset_symbol}"
        exchange_rate = (
            final_output_amount / final_input_amount
            if final_input_amount not in (0, None)
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
                input_amount=final_input_amount,
                output_asset=output_leg.asset_symbol,
                output_amount=final_output_amount,
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
                value_native=final_input_amount,
                source_asset=input_leg.asset_symbol,
                destination_asset=output_leg.asset_symbol,
                source_amount=final_input_amount,
                destination_amount=final_output_amount,
                route_summary=route_summary,
            ),
        )
        swap_input_edge = InvestigationEdge(
            edge_id=mk_edge_id(
                seed_node_id, swap_node_id, branch_id, f"{tx_hash}:swap_input"
            ),
            source_node_id=seed_node_id,
            target_node_id=swap_node_id,
            branch_id=branch_id,
            path_id=path_id,
            edge_type="swap_input",
            value_native=final_input_amount,
            asset_symbol=input_leg.asset_symbol,
            canonical_asset_id=input_leg.canonical_asset_id,
            chain_asset_id=input_leg.chain_asset_id,
            asset_chain=chain,
            tx_hash=tx_hash,
            tx_chain=chain,
            timestamp=timestamp,
            direction=direction,
        )
        swap_output_edge = InvestigationEdge(
            edge_id=mk_edge_id(
                swap_node_id, seed_node_id, branch_id, f"{tx_hash}:swap_output"
            ),
            source_node_id=swap_node_id,
            target_node_id=seed_node_id,
            branch_id=branch_id,
            path_id=path_id,
            edge_type="swap_output",
            value_native=final_output_amount,
            asset_symbol=output_leg.asset_symbol,
            canonical_asset_id=output_leg.canonical_asset_id,
            chain_asset_id=output_leg.chain_asset_id,
            asset_chain=chain,
            tx_hash=tx_hash,
            tx_chain=chain,
            timestamp=timestamp,
            direction=direction,
        )
        return [swap_node], [swap_input_edge, swap_output_edge]


@dataclass(frozen=True)
class _SwapLeg:
    """Minimal value leg used to infer a seed-centric swap event.

    Shared between EVMChainCompiler and TronChainCompiler via
    ``_GenericTransferChainCompiler._maybe_build_swap_event``.
    """

    address: str
    asset_symbol: str
    canonical_asset_id: Optional[str]
    chain_asset_id: Optional[str]
    amount: float
