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
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from src.trace_compiler.asset_selection import build_asset_option
from src.trace_compiler.asset_selection import selector_requires_event_store_only
from src.trace_compiler.chains._transfer_base import _GenericTransferChainCompiler
from src.trace_compiler.chains._transfer_base import _SwapLeg
from src.trace_compiler.lineage import edge_id as mk_edge_id
from src.trace_compiler.lineage import lineage_id as mk_lineage
from src.trace_compiler.lineage import node_id as mk_node_id
from src.trace_compiler.models import AssetOption
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

# SQL_FETCH_LIMIT is defined in _transfer_base and used by inherited methods.
# Keep a local alias for backward compatibility and for any remaining local uses.
from src.trace_compiler.chains._transfer_base import SQL_FETCH_LIMIT as _SQL_FETCH_LIMIT  # noqa: E402


class EVMChainCompiler(_GenericTransferChainCompiler):
    """Trace compiler for EVM-compatible chains.

    Inherits common SQL query layer and graph construction from
    ``_GenericTransferChainCompiler``.  Adds Neo4j fallback queries,
    EVM-specific swap event promotion, and DEX log decoding.

    Args:
        postgres_pool: asyncpg pool connected to the event store.
        neo4j_driver:  Neo4j async driver for the canonical graph.
        redis_client:  Redis client for service classification cache.
    """

    @property
    def supported_chains(self) -> List[str]:
        """Return the list of EVM chain names handled by this compiler."""
        return list(EVM_CHAINS)

    def _native_symbol(self, chain: str) -> str:
        """Return the native asset ticker for an EVM chain.

        Args:
            chain: Lowercase EVM chain name.

        Returns:
            Uppercase ticker (e.g. ``"ETH"``, ``"BNB"``).
        """
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

    def _native_canonical_asset_id(self, chain: str) -> Optional[str]:
        """Return a stable CoinGecko asset ID for the chain's native token.

        Args:
            chain: Lowercase EVM chain name.

        Returns:
            CoinGecko asset ID string, or None when not mapped.
        """
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

    async def list_asset_options(
        self,
        *,
        seed_address: str,
        chain: str,
    ) -> List[AssetOption]:
        if self._pg is None:
            return []

        address = seed_address.lower()
        native_symbol = self._native_symbol(chain)
        try:
            async with self._pg.acquire() as conn:
                native_exists = await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM raw_transactions
                        WHERE blockchain = $1
                          AND (from_address = $2 OR to_address = $2)
                          AND value_native > 0
                    )
                    """,
                    chain,
                    address,
                )
                token_rows = await conn.fetch(
                    """
                    SELECT
                        asset_contract AS chain_asset_id,
                        MAX(NULLIF(asset_symbol, '')) AS asset_symbol,
                        MAX(canonical_asset_id) AS canonical_asset_id,
                        MAX(timestamp) AS last_seen
                    FROM raw_token_transfers
                    WHERE blockchain = $1
                      AND (from_address = $2 OR to_address = $2)
                      AND asset_contract IS NOT NULL
                    GROUP BY asset_contract
                    ORDER BY MAX(timestamp) DESC NULLS LAST
                    LIMIT 40
                    """,
                    chain,
                    address,
                )
        except Exception as exc:
            logger.debug("EVMChainCompiler.list_asset_options failed for %s/%s: %s", chain, address, exc)
            return []

        options: List[AssetOption] = []
        if native_exists:
            options.append(build_asset_option(mode="native", chain=chain, asset_symbol=native_symbol))
        for row in token_rows:
            record = dict(row)
            options.append(
                build_asset_option(
                    mode="asset",
                    chain=chain,
                    asset_symbol=record.get("asset_symbol") or "Token",
                    chain_asset_id=record.get("chain_asset_id"),
                    canonical_asset_id=record.get("canonical_asset_id"),
                )
            )
        return options

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
        """Promote a DEX / aggregator interaction into a swap_event node.

        Delegates to ``_maybe_build_swap_event`` when the service record
        identifies a DEX or aggregator contract.

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
            service_record: ServiceRecord from the service classifier.

        Returns:
            (nodes, edges) on success, or None to fall through to plain service node.
        """
        if service_record.service_type not in {"dex", "aggregator"}:
            return None
        return await self._maybe_build_swap_event(
            tx_hash=tx_hash,
            seed_node_id=seed_node_id,
            seed_address=seed_address,
            counterparty=counterparty,
            chain=chain,
            session_id=session_id,
            branch_id=branch_id,
            path_id=path_id,
            depth=depth,
            direction=direction,
            timestamp=timestamp,
            protocol_id=service_record.protocol_id,
            protocol_label=service_record.display_name,
            protocol_type=service_record.service_type,
        )

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
        if chain not in self.supported_chains:
            raise ValueError(f"EVMChainCompiler does not support chain '{chain}'")
        addr = seed_address.lower()
        rows = await self._fetch_outbound_event_store(addr, chain, options)
        if rows:
            self._set_expansion_data_sources("event_store")
        elif not selector_requires_event_store_only(options, chain=chain):
            rows = await self._fetch_outbound_neo4j(addr, chain, options)
            if rows:
                self._set_expansion_data_sources("neo4j_fallback")
            else:
                self._set_expansion_data_sources()
        else:
            self._set_expansion_data_sources()

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
        if chain not in self.supported_chains:
            raise ValueError(f"EVMChainCompiler does not support chain '{chain}'")
        addr = seed_address.lower()
        rows = await self._fetch_inbound_event_store(addr, chain, options)
        if rows:
            self._set_expansion_data_sources("event_store")
        elif not selector_requires_event_store_only(options, chain=chain):
            rows = await self._fetch_inbound_neo4j(addr, chain, options)
            if rows:
                self._set_expansion_data_sources("neo4j_fallback")
            else:
                self._set_expansion_data_sources()
        else:
            self._set_expansion_data_sources()

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
            time_filter = ""
            if options.time_from:
                time_filter += " AND t.timestamp >= $time_from"
            if options.time_to:
                time_filter += " AND t.timestamp <= $time_to"
            cypher = f"""
                MATCH (a:Address {{address: $addr, blockchain: $chain}})
                      -[:SENT]->(t:Transaction)
                      -[:RECEIVED]->(tgt:Address)
                WHERE tgt.address <> $addr{time_filter}
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
                    cypher,
                    addr=address,
                    chain=chain,
                    limit=limit,
                    time_from=options.time_from,
                    time_to=options.time_to,
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
            time_filter = ""
            if options.time_from:
                time_filter += " AND t.timestamp >= $time_from"
            if options.time_to:
                time_filter += " AND t.timestamp <= $time_to"
            cypher = f"""
                MATCH (src:Address)-[:SENT]->(t:Transaction)
                      -[:RECEIVED]->(a:Address {{address: $addr, blockchain: $chain}})
                WHERE src.address <> $addr{time_filter}
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
                    cypher,
                    addr=address,
                    chain=chain,
                    limit=limit,
                    time_from=options.time_from,
                    time_to=options.time_to,
                )
                return [dict(r) async for r in result]
        except Exception as exc:
            logger.debug("EVMChainCompiler._fetch_inbound_neo4j failed: %s", exc)
            return []


# ---------------------------------------------------------------------------
# Module-level compatibility helpers — used by existing tests and any callers
# that imported the old module-level functions before they became instance
# methods.  Do not remove without updating all call sites.
# ---------------------------------------------------------------------------

_EVM_NATIVE_SYMBOL_MAP = {
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

_EVM_NATIVE_ASSET_ID_MAP = {
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


def _native_symbol(chain: str) -> str:
    """Return the native asset symbol for a given EVM chain.

    Module-level compatibility shim; the canonical implementation is
    ``EVMChainCompiler._native_symbol``.

    Args:
        chain: Lowercase EVM chain name.

    Returns:
        Uppercase ticker string.
    """
    return _EVM_NATIVE_SYMBOL_MAP.get(chain, "ETH")


def _native_canonical_asset_id(chain: str) -> Optional[str]:
    """Return a stable CoinGecko asset ID for the chain's native token.

    Module-level compatibility shim; the canonical implementation is
    ``EVMChainCompiler._native_canonical_asset_id``.

    Args:
        chain: Lowercase EVM chain name.

    Returns:
        CoinGecko asset ID string, or None when not mapped.
    """
    return _EVM_NATIVE_ASSET_ID_MAP.get(chain)
