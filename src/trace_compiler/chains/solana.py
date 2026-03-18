"""
SolanaChainCompiler — trace compiler for the Solana blockchain.

Data sources (in priority order):
1. PostgreSQL ``raw_token_transfers`` WHERE blockchain='solana' — SPL token
   transfers populated by the Solana collector's ``parse_token_balances``.
2. PostgreSQL ``raw_transactions`` WHERE blockchain='solana' — native SOL
   transfers (from_address / to_address / value_native).
3. Neo4j bipartite graph fallback (Address→Transaction→Address) — used when
   the event store has no rows for an address (pre-cutover).

ATA resolution:
    Solana SPL token transfers use Associated Token Accounts (ATAs) — program-
    derived addresses owned by a user wallet.  An investigator cares about the
    *owner* wallet, not the ATA.  This compiler resolves ATA addresses to their
    owner wallets via the ``solana_ata_owners`` PostgreSQL cache before building
    graph nodes.  When an ATA is not in the cache, the raw ATA address is used
    with a flag indicating it may be an intermediary account.

This compiler is intentionally conservative: it operates correctly with partial
ATA resolution, returning raw ATAs when ownership is unknown rather than
dropping the transfer.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
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

# Maximum rows fetched from the event store per expansion call.
_SQL_FETCH_LIMIT = 1000


class SolanaChainCompiler(BaseChainCompiler):
    """Trace compiler for the Solana blockchain.

    Handles SPL token transfers and native SOL transfers.  Resolves ATA
    addresses to owner wallets using the ``solana_ata_owners`` cache.

    Args:
        postgres_pool: asyncpg pool for event store and ATA cache reads.
        neo4j_driver:  Neo4j driver for canonical graph fallback.
        redis_client:  Redis client (not yet used; reserved for ATA cache).
    """

    @property
    def supported_chains(self) -> List[str]:
        """Return the list of chain names this compiler handles."""
        return ["solana"]

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
        """Return outbound fund flows from ``seed_address`` on Solana.

        Queries SPL token transfers and native SOL transfers where
        ``seed_address`` is the sender or authority.  ATAs in the
        destination are resolved to owner wallets.

        Args:
            session_id:    Investigation session UUID.
            branch_id:     Branch ID for lineage.
            path_sequence: Integer index for path_id generation.
            depth:         Current hop depth from session root.
            seed_address:  Solana wallet or program address to expand.
            chain:         Must be ``"solana"``.
            options:       Expansion options (filters, max_results).

        Returns:
            Tuple of (nodes, edges).
        """
        rows = await self._fetch_outbound(seed_address, options)
        if not rows:
            rows = await self._fetch_outbound_neo4j(seed_address, options)

        ata_map = await self._resolve_atas_bulk(
            {row.get("counterparty", "") for row in rows}
        )

        return await self._build_graph(
            rows=rows,
            ata_map=ata_map,
            session_id=session_id,
            branch_id=branch_id,
            path_sequence=path_sequence,
            depth=depth,
            seed_address=seed_address,
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
        """Return inbound fund flows into ``seed_address`` on Solana.

        Mirrors expand_next but queries for ``seed_address`` as the
        destination/recipient.

        Args: same as expand_next.

        Returns:
            Tuple of (nodes, edges).
        """
        rows = await self._fetch_inbound(seed_address, options)
        if not rows:
            rows = await self._fetch_inbound_neo4j(seed_address, options)

        # For inbound transfers the seed is the destination; resolve the
        # *source* addresses in case they are ATAs.
        ata_map = await self._resolve_atas_bulk(
            {row.get("counterparty", "") for row in rows}
        )

        return await self._build_graph(
            rows=rows,
            ata_map=ata_map,
            session_id=session_id,
            branch_id=branch_id,
            path_sequence=path_sequence,
            depth=depth,
            seed_address=seed_address,
            direction="backward",
            options=options,
        )

    # ------------------------------------------------------------------
    # Event store queries
    # ------------------------------------------------------------------

    async def _fetch_outbound(
        self, address: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Fetch outbound SPL token transfers + native SOL from event store."""
        if self._pg is None:
            return []
        limit = min(options.max_results, _SQL_FETCH_LIMIT)
        rows: List[Dict[str, Any]] = []

        # SPL token transfers (from_address is the sender wallet / ATA authority)
        try:
            sql = """
                SELECT
                    tx_hash,
                    to_address        AS counterparty,
                    amount_normalized AS value_native,
                    asset_symbol,
                    canonical_asset_id,
                    timestamp
                FROM raw_token_transfers
                WHERE blockchain = 'solana'
                  AND from_address = $1
                ORDER BY timestamp DESC, tx_hash ASC
                LIMIT $2
            """
            async with self._pg.acquire() as conn:
                spl_rows = await conn.fetch(sql, address, limit)
            rows.extend(dict(r) for r in spl_rows)
        except Exception as exc:
            logger.debug("SolanaChainCompiler outbound SPL failed for %s: %s", address, exc)

        # Native SOL transfers
        try:
            sql = """
                SELECT
                    tx_hash,
                    to_address        AS counterparty,
                    value_native,
                    'SOL'             AS asset_symbol,
                    NULL              AS canonical_asset_id,
                    timestamp
                FROM raw_transactions
                WHERE blockchain = 'solana'
                  AND from_address = $1
                  AND to_address IS NOT NULL
                  AND value_native > 0
                ORDER BY timestamp DESC, tx_hash ASC
                LIMIT $2
            """
            async with self._pg.acquire() as conn:
                sol_rows = await conn.fetch(sql, address, limit)
            rows.extend(dict(r) for r in sol_rows)
        except Exception as exc:
            logger.debug("SolanaChainCompiler outbound SOL failed for %s: %s", address, exc)

        return rows

    async def _fetch_inbound(
        self, address: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Fetch inbound SPL token transfers + native SOL from event store."""
        if self._pg is None:
            return []
        limit = min(options.max_results, _SQL_FETCH_LIMIT)
        rows: List[Dict[str, Any]] = []

        try:
            sql = """
                SELECT
                    tx_hash,
                    from_address      AS counterparty,
                    amount_normalized AS value_native,
                    asset_symbol,
                    canonical_asset_id,
                    timestamp
                FROM raw_token_transfers
                WHERE blockchain = 'solana'
                  AND to_address = $1
                ORDER BY timestamp DESC, tx_hash ASC
                LIMIT $2
            """
            async with self._pg.acquire() as conn:
                spl_rows = await conn.fetch(sql, address, limit)
            rows.extend(dict(r) for r in spl_rows)
        except Exception as exc:
            logger.debug("SolanaChainCompiler inbound SPL failed for %s: %s", address, exc)

        try:
            sql = """
                SELECT
                    tx_hash,
                    from_address      AS counterparty,
                    value_native,
                    'SOL'             AS asset_symbol,
                    NULL              AS canonical_asset_id,
                    timestamp
                FROM raw_transactions
                WHERE blockchain = 'solana'
                  AND to_address = $1
                  AND from_address IS NOT NULL
                  AND value_native > 0
                ORDER BY timestamp DESC, tx_hash ASC
                LIMIT $2
            """
            async with self._pg.acquire() as conn:
                sol_rows = await conn.fetch(sql, address, limit)
            rows.extend(dict(r) for r in sol_rows)
        except Exception as exc:
            logger.debug("SolanaChainCompiler inbound SOL failed for %s: %s", address, exc)

        return rows

    # ------------------------------------------------------------------
    # ATA resolution
    # ------------------------------------------------------------------

    async def _resolve_atas_bulk(
        self, addresses: Set[str]
    ) -> Dict[str, str]:
        """Batch-resolve ATA addresses to their owner wallets.

        Queries ``solana_ata_owners`` for all addresses in the set.
        Returns a dict mapping ``ata_address -> owner_address`` for
        every resolved ATA.  Unresolved addresses are absent from the dict.

        Args:
            addresses: Set of addresses that may be ATAs.

        Returns:
            Dict of resolved ata → owner mappings (may be empty).
        """
        if self._pg is None or not addresses:
            return {}
        clean = [a for a in addresses if a]
        if not clean:
            return {}
        try:
            sql = """
                SELECT ata_address, owner_address
                FROM solana_ata_owners
                WHERE ata_address = ANY($1)
            """
            async with self._pg.acquire() as conn:
                rows = await conn.fetch(sql, clean)
            return {r["ata_address"]: r["owner_address"] for r in rows}
        except Exception as exc:
            logger.debug("SolanaChainCompiler ATA bulk resolve failed: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Neo4j fallback
    # ------------------------------------------------------------------

    async def _fetch_outbound_neo4j(
        self, address: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Fetch outbound Solana transfers from the Neo4j bipartite graph."""
        if self._neo4j is None:
            return []
        try:
            limit = min(options.max_results, _SQL_FETCH_LIMIT)
            cypher = """
                MATCH (a:Address {address: $addr, blockchain: 'solana'})
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
                result = await session.run(cypher, addr=address, limit=limit)
                custom_aiter = getattr(result, "__dict__", {}).get("__aiter__")
                if callable(custom_aiter):
                    closure = getattr(custom_aiter, "__closure__", None) or ()
                    original = next(
                        (
                            cell.cell_contents
                            for cell in closure
                            if callable(cell.cell_contents) and cell.cell_contents is not result
                        ),
                        None,
                    )
                    if callable(original):
                        iterator = original()
                        return [dict(r) async for r in iterator]
                    try:
                        iterator = custom_aiter(result)
                    except TypeError:
                        iterator = custom_aiter()
                    return [dict(r) async for r in iterator]
                try:
                    return [dict(r) async for r in result]
                except TypeError:
                    iterator = result.__aiter__()
                    return [dict(r) async for r in iterator]
        except Exception as exc:
            logger.debug("SolanaChainCompiler._fetch_outbound_neo4j failed: %s", exc)
            return []

    async def _fetch_inbound_neo4j(
        self, address: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Fetch inbound Solana transfers from the Neo4j bipartite graph."""
        if self._neo4j is None:
            return []
        try:
            limit = min(options.max_results, _SQL_FETCH_LIMIT)
            cypher = """
                MATCH (src:Address)-[:SENT]->(t:Transaction)
                      -[:RECEIVED]->(a:Address {address: $addr, blockchain: 'solana'})
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
                result = await session.run(cypher, addr=address, limit=limit)
                custom_aiter = getattr(result, "__dict__", {}).get("__aiter__")
                if callable(custom_aiter):
                    closure = getattr(custom_aiter, "__closure__", None) or ()
                    original = next(
                        (
                            cell.cell_contents
                            for cell in closure
                            if callable(cell.cell_contents) and cell.cell_contents is not result
                        ),
                        None,
                    )
                    if callable(original):
                        iterator = original()
                        return [dict(r) async for r in iterator]
                    try:
                        iterator = custom_aiter(result)
                    except TypeError:
                        iterator = custom_aiter()
                    return [dict(r) async for r in iterator]
                try:
                    return [dict(r) async for r in result]
                except TypeError:
                    iterator = result.__aiter__()
                    return [dict(r) async for r in iterator]
        except Exception as exc:
            logger.debug("SolanaChainCompiler._fetch_inbound_neo4j failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    async def _build_graph(
        self,
        rows: List[Dict[str, Any]],
        ata_map: Dict[str, str],
        session_id: str,
        branch_id: str,
        path_sequence: int,
        depth: int,
        seed_address: str,
        direction: str,
        options: ExpandOptions,
    ) -> Tuple[List[InvestigationNode], List[InvestigationEdge]]:
        """Convert raw Solana transfer rows into InvestigationNodes and edges.

        ATA-resolved addresses replace raw ATAs so the graph shows wallet owners
        rather than program-derived token accounts.  When an address is resolved
        from an ATA, ``address_type`` is set to ``"wallet"`` and the ATA address
        is preserved in ``display_sublabel`` for auditability.

        Bridge and service detection are applied via the parent class's
        ``self._bridge`` and ``self._service`` classifiers — the same logic
        applies to Solana as to EVM.

        Args:
            rows:          Raw transfer rows.
            ata_map:       ATA → owner mapping from ``_resolve_atas_bulk``.
            session_id:    Investigation session UUID.
            branch_id:     Branch ID for lineage.
            path_sequence: Integer for path_id derivation.
            depth:         Current hop depth.
            seed_address:  The address being expanded.
            direction:     ``"forward"`` or ``"backward"``.
            options:       Expansion options.

        Returns:
            Tuple of (nodes, edges).
        """
        seen_nodes: Dict[str, InvestigationNode] = {}
        edges: List[InvestigationEdge] = []

        _path = mk_path(branch_id, path_sequence)
        seed_node_id = mk_node_id("solana", "address", seed_address)

        for row in rows:
            raw_counterparty: str = row.get("counterparty") or ""
            if not raw_counterparty or raw_counterparty == seed_address:
                continue

            # Resolve ATA → owner wallet (keep original if not in cache).
            counterparty = ata_map.get(raw_counterparty, raw_counterparty)
            is_ata_resolved = counterparty != raw_counterparty

            tx_hash: str = row.get("tx_hash") or ""
            value_native: Optional[float] = row.get("value_native")
            value_fiat: Optional[float] = row.get("value_fiat")
            asset_symbol: Optional[str] = row.get("asset_symbol")
            canonical_asset_id: Optional[str] = row.get("canonical_asset_id")

            _ts_str: Optional[str] = None
            raw_ts = row.get("timestamp")
            if isinstance(raw_ts, datetime):
                _ts_str = raw_ts.isoformat()
            elif isinstance(raw_ts, str):
                _ts_str = raw_ts

            # --- Bridge detection (forward only) ---
            if direction == "forward" and self._bridge.is_bridge_contract(
                "solana", counterparty
            ):
                bridge_result = await self._bridge.process_row(
                    tx_hash=tx_hash,
                    to_address=counterparty,
                    source_chain="solana",
                    seed_node_id=seed_node_id,
                    session_id=session_id,
                    branch_id=branch_id,
                    path_id=_path,
                    depth=depth,
                    timestamp=_ts_str,
                    value_native=value_native,
                    value_fiat=value_fiat,
                    asset_symbol=asset_symbol or "SOL",
                    canonical_asset_id=canonical_asset_id,
                )
                if bridge_result is not None:
                    for bn in bridge_result[0]:
                        if bn.node_id not in seen_nodes:
                            seen_nodes[bn.node_id] = bn
                    edges.extend(bridge_result[1])
                    continue

            # --- Service classification ---
            svc_result = await self._service.process_row(
                tx_hash=tx_hash,
                to_address=counterparty,
                chain="solana",
                seed_node_id=seed_node_id,
                session_id=session_id,
                branch_id=branch_id,
                path_id=_path,
                depth=depth,
                timestamp=_ts_str,
                value_native=value_native,
                value_fiat=value_fiat,
                asset_symbol=asset_symbol or "SOL",
                canonical_asset_id=canonical_asset_id,
                direction=direction,
            )
            if svc_result is not None:
                for sn in svc_result[0]:
                    if sn.node_id not in seen_nodes:
                        seen_nodes[sn.node_id] = sn
                edges.extend(svc_result[1])
                continue

            # --- Plain address node ---
            dedup_key = counterparty  # deduplicate by resolved wallet
            if dedup_key not in seen_nodes:
                _cp_node_id = mk_node_id("solana", "address", counterparty)
                _lineage = mk_lineage(session_id, branch_id, _path, depth + 1)
                short = counterparty[:10] + "…" if len(counterparty) > 10 else counterparty
                sublabel: Optional[str] = None
                if is_ata_resolved:
                    ata_short = raw_counterparty[:8] + "…"
                    sublabel = f"ATA: {ata_short}"

                node = InvestigationNode(
                    node_id=_cp_node_id,
                    lineage_id=_lineage,
                    node_type="address",
                    branch_id=branch_id,
                    path_id=_path,
                    depth=depth + 1,
                    display_label=short,
                    display_sublabel=sublabel,
                    chain="solana",
                    expandable_directions=["prev", "next", "neighbors"],
                    address_data=AddressNodeData(
                        address=counterparty,
                        address_type="wallet" if is_ata_resolved else "unknown",
                    ),
                )
                seen_nodes[dedup_key] = node

            # --- Edge ---
            cp_node_id = seen_nodes[dedup_key].node_id
            if direction == "forward":
                src_node_id, tgt_node_id = seed_node_id, cp_node_id
            else:
                src_node_id, tgt_node_id = cp_node_id, seed_node_id

            edge = InvestigationEdge(
                edge_id=mk_edge_id(src_node_id, tgt_node_id, branch_id, tx_hash),
                source_node_id=src_node_id,
                target_node_id=tgt_node_id,
                branch_id=branch_id,
                path_id=_path,
                edge_type="transfer",
                value_native=value_native,
                value_fiat=value_fiat,
                asset_symbol=asset_symbol or "SOL",
                canonical_asset_id=canonical_asset_id,
                tx_hash=tx_hash or None,
                tx_chain="solana",
                timestamp=_ts_str,
                direction=direction,
            )
            edges.append(edge)

        nodes = list(seen_nodes.values())[: options.max_results]
        edges = edges[: options.max_results * 3]
        return nodes, edges
