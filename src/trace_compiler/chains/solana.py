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
from src.trace_compiler.lineage import swap_event_id as mk_swap_event_id
from src.trace_compiler.models import ActivitySummary
from src.trace_compiler.models import AddressNodeData
from src.trace_compiler.models import ExpandOptions
from src.trace_compiler.models import InvestigationEdge
from src.trace_compiler.models import InvestigationNode
from src.trace_compiler.models import SwapEventData

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
        asset_filter = [asset.upper() for asset in (options.asset_filter or [])]
        include_native = not asset_filter or "SOL" in asset_filter

        # SPL token transfers (from_address is the sender wallet / ATA authority)
        try:
            params: List[Any] = [address, limit]
            asset_clause = ""
            if asset_filter:
                asset_clause = "AND UPPER(asset_symbol) = ANY($3)"
                params.append(asset_filter)

            sql = f"""
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
                  {asset_clause}
                ORDER BY timestamp DESC, tx_hash ASC
                LIMIT $2
            """
            async with self._pg.acquire() as conn:
                spl_rows = await conn.fetch(sql, *params)
            rows.extend(dict(r) for r in spl_rows)
        except Exception as exc:
            logger.debug("SolanaChainCompiler outbound SPL failed for %s: %s", address, exc)

        # Native SOL transfers
        try:
            if not include_native:
                return rows
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
        asset_filter = [asset.upper() for asset in (options.asset_filter or [])]
        include_native = not asset_filter or "SOL" in asset_filter

        try:
            params: List[Any] = [address, limit]
            asset_clause = ""
            if asset_filter:
                asset_clause = "AND UPPER(asset_symbol) = ANY($3)"
                params.append(asset_filter)

            sql = f"""
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
                  {asset_clause}
                ORDER BY timestamp DESC, tx_hash ASC
                LIMIT $2
            """
            async with self._pg.acquire() as conn:
                spl_rows = await conn.fetch(sql, *params)
            rows.extend(dict(r) for r in spl_rows)
        except Exception as exc:
            logger.debug("SolanaChainCompiler inbound SPL failed for %s: %s", address, exc)

        try:
            if not include_native:
                return rows
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
    # Swap event helpers
    # ------------------------------------------------------------------

    async def _fetch_tx_spl_transfers(
        self, tx_hash: str
    ) -> List[Dict[str, Any]]:
        """Fetch all SPL token transfers for a transaction from the event store.

        Used by ``_maybe_build_solana_swap_event`` to find the input and
        output legs of a DEX swap — both sides are SPL transfers (or wSOL)
        in the same transaction.

        Returns an empty list when the pool is unavailable or the query fails.
        """
        if self._pg is None:
            return []
        try:
            sql = """
                SELECT
                    from_address,
                    to_address,
                    amount_normalized,
                    asset_symbol,
                    canonical_asset_id
                FROM raw_token_transfers
                WHERE blockchain = 'solana'
                  AND tx_hash   = $1
            """
            async with self._pg.acquire() as conn:
                rows = await conn.fetch(sql, tx_hash)
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.debug(
                "SolanaChainCompiler._fetch_tx_spl_transfers failed for %s: %s",
                tx_hash, exc,
            )
            return []

    async def _fetch_swap_instruction(
        self,
        tx_hash: str,
        program_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Return a successfully decoded swap instruction for a transaction.

        Queries ``raw_solana_instructions`` for the given (tx_signature,
        program_id) pair.  Only rows with ``decode_status = 'success'`` and a
        recognised swap instruction_type are returned — partial or raw rows are
        ignored so callers receive either a trustworthy result or None.

        Args:
            tx_hash:    Transaction signature.
            program_id: DEX program ID (e.g. Raydium, Jupiter).

        Returns:
            Dict with ``instruction_type`` and ``decoded_args`` keys, or None
            when no successfully-decoded swap instruction is found.
        """
        if self._pg is None:
            return None
        # Instruction types that represent a direct swap at the user level.
        _SWAP_TYPES = frozenset({
            "swapBaseIn", "swapBaseOut",         # Raydium AMM v4
            "route", "sharedAccountsRoute",      # Jupiter v6
            "swap", "twoHopSwap",                # Orca Whirlpool
            "placeTakeOrder",                    # OpenBook v2
        })
        try:
            sql = """
                SELECT instruction_type, decoded_args
                FROM raw_solana_instructions
                WHERE tx_signature = $1
                  AND program_id    = $2
                  AND decode_status = 'success'
                ORDER BY ix_index ASC
                LIMIT 1
            """
            async with self._pg.acquire() as conn:
                row = await conn.fetchrow(sql, tx_hash, program_id)
            if row and row["instruction_type"] in _SWAP_TYPES:
                return {
                    "instruction_type": row["instruction_type"],
                    "decoded_args": row["decoded_args"] or {},
                }
        except Exception as exc:
            logger.debug(
                "_fetch_swap_instruction failed tx=%s prog=%s: %s",
                tx_hash, program_id, exc,
            )
        return None

    async def _maybe_build_solana_swap_event(
        self,
        *,
        tx_hash: str,
        seed_address: str,
        seed_node_id: str,
        program_address: str,
        protocol_id: str,
        protocol_label: str,
        protocol_type: str,
        session_id: str,
        branch_id: str,
        path_id: str,
        depth: int,
        timestamp: Optional[str],
        ata_map: Dict[str, str],
    ) -> Optional[Tuple[List[InvestigationNode], List[InvestigationEdge]]]:
        """Promote a Solana DEX program interaction into a swap_event node.

        Fetches all SPL token transfers in the transaction, resolves ATAs to
        their owner wallets, and identifies the outgoing leg (seed sends a
        token) and the incoming leg (seed receives a token).  Returns None
        when both legs cannot be confirmed from the event store — in that
        case the caller falls back to a generic service node.

        The canonical SOL mint (wSOL) is treated like any other SPL token;
        native SOL transfers appear in ``raw_transactions`` but are not
        considered here because the SPL transfer legs are sufficient to
        characterise the swap direction and amounts.

        Args:
            tx_hash:         Solana transaction signature.
            seed_address:    The wallet address being expanded.
            seed_node_id:    Node ID for the seed address.
            program_address: DEX / AMM program address that triggered this row.
            protocol_id:     Stable protocol identifier from the service registry.
            protocol_label:  Human-readable protocol name.
            protocol_type:   ``"dex"`` or ``"aggregator"``.
            session_id:      Investigation session UUID.
            branch_id:       Branch ID for lineage.
            path_id:         Path ID for lineage.
            depth:           Current hop depth.
            timestamp:       ISO-8601 timestamp string, or None.
            ata_map:         ATA → owner wallet mapping from the current expansion.

        Returns:
            (nodes, edges) on success, or None when both swap legs are absent.
        """
        spl_legs = await self._fetch_tx_spl_transfers(tx_hash)
        if not spl_legs:
            return None

        # --- Instruction-level ATA refinement (when available) ----------------
        # For protocols where we have a fully-decoded instruction, use the
        # explicit user ATA addresses from the instruction to filter SPL legs
        # precisely.  This prevents multi-hop or pool-internal transfers from
        # being misidentified as the user's swap legs.
        swap_ix = await self._fetch_swap_instruction(tx_hash, program_address)
        user_atas: Set[str] = set()
        if swap_ix is not None:
            args = swap_ix.get("decoded_args") or {}
            # Collect all ATA fields that indicate user-controlled token accounts
            for field in (
                "input_token_account",        # Jupiter route
                "output_token_account",       # Jupiter route
                "user_source_token_account",  # Raydium AMM v4
                "user_destination_token_account",  # Raydium AMM v4
                "token_owner_account_a",      # Orca Whirlpool
                "token_owner_account_b",      # Orca Whirlpool
                "user_token_in",              # Meteora DLMM
                "user_token_out",             # Meteora DLMM
                "user_base_account",          # OpenBook v2
                "user_quote_account",         # OpenBook v2
                "base_account",               # Phoenix
                "quote_account",              # Phoenix
            ):
                val = args.get(field)
                if val:
                    user_atas.add(val.lower())

        # Resolve ATAs in the transfer legs so comparisons are against owner wallets.
        seed_lc = seed_address.lower()

        outgoing_asset: Optional[str] = None
        outgoing_amount: float = 0.0
        outgoing_canonical_id: Optional[str] = None
        incoming_asset: Optional[str] = None
        incoming_amount: float = 0.0
        incoming_canonical_id: Optional[str] = None

        for leg in spl_legs:
            raw_from = leg.get("from_address", "")
            raw_to = leg.get("to_address", "")
            from_addr = (
                ata_map.get(raw_from, raw_from)
            ).lower()
            to_addr = (
                ata_map.get(raw_to, raw_to)
            ).lower()
            raw_from_lc = raw_from.lower()
            raw_to_lc = raw_to.lower()
            amount = leg.get("amount_normalized")
            if not amount:
                continue
            try:
                amount = float(amount)
            except (ValueError, TypeError) as conv_exc:
                logger.debug("Failed to convert amount to float: %s", conv_exc)
                continue
            canonical_id = leg.get("canonical_asset_id")
            symbol = (leg.get("asset_symbol") or canonical_id or "").upper()
            if not symbol:
                continue

            # When instruction ATA data is available, additionally require that
            # the raw ATA address is in the confirmed user ATA set.  This
            # prevents intermediate pool transfers in multi-hop routes from
            # being counted as the user's swap legs.
            if user_atas:
                from_ata_ok = raw_from_lc in user_atas
                to_ata_ok = raw_to_lc in user_atas
            else:
                from_ata_ok = True
                to_ata_ok = True

            if from_addr == seed_lc and amount > 0 and from_ata_ok:
                # Pick the largest outgoing leg (ignore dust/fee legs).
                if amount > outgoing_amount:
                    outgoing_asset = symbol
                    outgoing_amount = amount
                    outgoing_canonical_id = canonical_id

            if to_addr == seed_lc and amount > 0 and to_ata_ok:
                if amount > incoming_amount:
                    incoming_asset = symbol
                    incoming_amount = amount
                    incoming_canonical_id = canonical_id

        if outgoing_asset is None or incoming_asset is None:
            return None

        # Reject identity swaps (same asset in and out, same amount — not a swap).
        if (
            outgoing_asset == incoming_asset
            and abs(outgoing_amount - incoming_amount) < 1e-9
        ):
            return None

        swap_id = mk_swap_event_id("solana", tx_hash, 0)
        swap_node_id = mk_node_id("solana", "swap_event", swap_id)
        lineage = mk_lineage(session_id, branch_id, path_id, depth)
        route_summary = f"{outgoing_asset} → {incoming_asset}"
        exchange_rate = (
            incoming_amount / outgoing_amount if outgoing_amount > 0 else None
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
            chain="solana",
            expandable_directions=[],
            swap_event_data=SwapEventData(
                swap_id=swap_id,
                protocol_id=protocol_id,
                chain="solana",
                input_asset=outgoing_asset,
                input_amount=outgoing_amount,
                output_asset=incoming_asset,
                output_amount=incoming_amount,
                exchange_rate=exchange_rate,
                route_summary=route_summary,
                tx_hash=tx_hash,
                timestamp=timestamp,
            ),
            activity_summary=ActivitySummary(
                activity_type=(
                    "router_interaction" if protocol_type == "aggregator"
                    else "dex_interaction"
                ),
                title=f"{protocol_label} swap",
                protocol_id=protocol_id,
                protocol_type=protocol_type,
                tx_hash=tx_hash,
                tx_chain="solana",
                timestamp=timestamp,
                source_asset=outgoing_asset,
                destination_asset=incoming_asset,
                source_amount=outgoing_amount,
                destination_amount=incoming_amount,
                route_summary=route_summary,
            ),
        )

        ingress_edge = InvestigationEdge(
            edge_id=mk_edge_id(seed_node_id, swap_node_id, branch_id, tx_hash),
            source_node_id=seed_node_id,
            target_node_id=swap_node_id,
            branch_id=branch_id,
            path_id=path_id,
            edge_type="swap_input",
            value_native=outgoing_amount,
            value_fiat=None,
            asset_symbol=outgoing_asset,
            canonical_asset_id=outgoing_canonical_id,
            tx_hash=tx_hash,
            tx_chain="solana",
            timestamp=timestamp,
            direction="forward",
        )
        egress_edge = InvestigationEdge(
            edge_id=mk_edge_id(swap_node_id, seed_node_id, branch_id, tx_hash + ":out"),
            source_node_id=swap_node_id,
            target_node_id=seed_node_id,
            branch_id=branch_id,
            path_id=path_id,
            edge_type="swap_output",
            value_native=incoming_amount,
            value_fiat=None,
            asset_symbol=incoming_asset,
            canonical_asset_id=incoming_canonical_id,
            tx_hash=tx_hash,
            tx_chain="solana",
            timestamp=timestamp,
            direction="forward",
        )

        return [swap_node], [ingress_edge, egress_edge]

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
        # Local dedup set for generic swap attempts — prevents redundant DB queries
        # when multiple rows share the same (tx_hash, counterparty) within this call.
        # Must be local (not self) so it resets between expand() invocations.
        generic_swap_seen: set = set()

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

            # --- Service classification + swap_event promotion ---
            service_record = self._service.get_record("solana", counterparty)

            if service_record is not None and service_record.service_type in {
                "dex", "aggregator"
            }:
                # Attempt swap_event promotion first (ADR-017 / ADR-020 parity).
                # Only honoured when both SPL legs are present in the event store.
                swap_result = await self._maybe_build_solana_swap_event(
                    tx_hash=tx_hash,
                    seed_address=seed_address,
                    seed_node_id=seed_node_id,
                    program_address=counterparty,
                    protocol_id=service_record.protocol_id,
                    protocol_label=service_record.display_name,
                    protocol_type=service_record.service_type,
                    session_id=session_id,
                    branch_id=branch_id,
                    path_id=_path,
                    depth=depth,
                    timestamp=_ts_str,
                    ata_map=ata_map,
                )
                if swap_result is not None:
                    swap_nodes, swap_edges = swap_result
                    for sn in swap_nodes:
                        seen_nodes.setdefault(sn.node_id, sn)
                    edges.extend(swap_edges)
                    continue
                # Fall through to generic service node when legs are missing.

            # --- Generic swap promotion for unknown DEX programs ---
            # When the counterparty is not in the service classifier, still
            # attempt swap_event promotion if the tx has two SPL balance-change
            # legs (a give and a receive).  This catches any Solana DEX or AMM
            # that isn't in our registry — new programs, forks, aggregators.
            # Cache discovered program IDs to avoid repeated DB calls for the
            # same tx in a single expansion pass.
            elif service_record is None:
                # Guard: only try once per (tx_hash, counterparty) within this call.
                _generic_swap_key = (tx_hash, counterparty)
                if _generic_swap_key not in generic_swap_seen:
                    swap_result = await self._maybe_build_solana_swap_event(
                        tx_hash=tx_hash,
                        seed_address=seed_address,
                        seed_node_id=seed_node_id,
                        program_address=counterparty,
                        protocol_id="solana_dex",
                        protocol_label="DEX Swap",
                        protocol_type="dex",
                        session_id=session_id,
                        branch_id=branch_id,
                        path_id=_path,
                        depth=depth,
                        timestamp=_ts_str,
                        ata_map=ata_map,
                    )
                    if swap_result is not None:
                        logger.info(
                            "solana._build_graph: generic swap_event for unknown "
                            "program %s (tx=%s)",
                            counterparty[:16], tx_hash[:16],
                        )
                        generic_swap_seen.add(_generic_swap_key)
                        swap_nodes, swap_edges = swap_result
                        for sn in swap_nodes:
                            seen_nodes.setdefault(sn.node_id, sn)
                        edges.extend(swap_edges)
                        continue
                    # No swap built — fall through to service/transfer handling.

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
