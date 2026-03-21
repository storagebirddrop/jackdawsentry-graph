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
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from src.trace_compiler.chains._transfer_base import _GenericTransferChainCompiler
from src.trace_compiler.chains.evm_log_decoder import (
    DEX_SWAP_SIGS,
    decode_swap_log,
    extract_swap_amounts,
)
from src.trace_compiler.lineage import edge_id as mk_edge_id
from src.trace_compiler.lineage import lineage_id as mk_lineage
from src.trace_compiler.lineage import node_id as mk_node_id
from src.trace_compiler.lineage import swap_event_id as mk_swap_event_id
from src.trace_compiler.models import ActivitySummary
from src.trace_compiler.models import ExpandOptions
from src.trace_compiler.models import InvestigationEdge
from src.trace_compiler.models import InvestigationNode
from src.trace_compiler.models import SwapEventData

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


@dataclass(frozen=True)
class _SwapLeg:
    """Minimal value leg used to infer a seed-centric swap event."""

    address: str
    asset_symbol: str
    canonical_asset_id: Optional[str]
    amount: float


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
        if chain not in self.supported_chains:
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

        Only returns rows with a recognised DEX Swap event signature.
        Returns None when ``raw_evm_logs`` is unavailable, the table does not
        exist yet, or no matching log is found.

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
                # Decode on the fly if the collector did not pre-decode.
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

        # --- Log-aware swap amounts (Uniswap V2/V3/V4) ----------------------
        # When a Swap event log is available for this contract, use it to
        # determine the canonical input/output amounts.  The log provides the
        # pool's recorded amounts which are more accurate than token-transfer
        # leg inference (they include fee deduction and handle wrapped assets).
        _log_input_amount: Optional[float] = None
        _log_output_amount: Optional[float] = None
        _log_token0_is_input: Optional[bool] = None
        dex_log = await self._fetch_dex_swap_log(chain, tx_hash, counterparty)
        if dex_log is not None:
            amounts = extract_swap_amounts(
                dex_log["decoded"],
                dex_log["event_sig"],
            )
            if amounts is not None:
                _log_input_amount, _log_output_amount, _log_token0_is_input = amounts

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

        # Use log-decoded amounts as ground truth when available.
        final_input_amount = _log_input_amount if _log_input_amount is not None else input_leg.amount
        final_output_amount = _log_output_amount if _log_output_amount is not None else output_leg.amount

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
            edge_id=mk_edge_id(seed_node_id, swap_node_id, branch_id, f"{tx_hash}:swap_input"),
            source_node_id=seed_node_id,
            target_node_id=swap_node_id,
            branch_id=branch_id,
            path_id=path_id,
            edge_type="swap_input",
            value_native=final_input_amount,
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
            value_native=final_output_amount,
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
