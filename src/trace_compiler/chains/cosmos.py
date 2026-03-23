"""
CosmosChainCompiler — trace compiler for Cosmos-based chains.

Data sources:
1. PostgreSQL event store ``raw_transactions`` WHERE blockchain='cosmos' — native
   ATOM transfers (from_address / to_address / value_native).
2. PostgreSQL event store ``raw_token_transfers`` WHERE blockchain='cosmos' — IBC
   token transfers (USDC via Noble, OSMO, etc.).

No Neo4j fallback: the Cosmos collector only writes to the event store.

Address normalization: Cosmos bech32 addresses (e.g. ``cosmos1...``) are
lowercase by spec; lowercasing is safe and idempotent.
"""

from __future__ import annotations

import logging
from typing import List
from typing import Optional
from typing import Tuple

from src.trace_compiler.chains._transfer_base import _GenericTransferChainCompiler
from src.trace_compiler.lineage import edge_id as mk_edge_id
from src.trace_compiler.lineage import lineage_id as mk_lineage
from src.trace_compiler.lineage import node_id as mk_node_id
from src.trace_compiler.models import ActivitySummary
from src.trace_compiler.models import ExpandOptions
from src.trace_compiler.models import InvestigationEdge
from src.trace_compiler.models import InvestigationNode
from src.trace_compiler.models import ServiceNodeData

logger = logging.getLogger(__name__)

# Cosmos SDK message types that indicate a DEX swap interaction.
# Short names only (the @type suffix after the last dot).
# Osmosis gamm/poolmanager swap messages are the most common.
# MsgExecuteContract covers CosmWasm DEX calls (e.g. Astroport on Cosmos Hub).
_COSMOS_SWAP_MSG_TYPES = frozenset({
    "MsgSwapExactAmountIn",   # Osmosis gamm v1beta1 / poolmanager
    "MsgSwapExactAmountOut",  # Osmosis gamm v1beta1 / poolmanager
    "MsgSplitRouteSwapExactAmountIn",   # Osmosis poolmanager
    "MsgSplitRouteSwapExactAmountOut",  # Osmosis poolmanager
    "MsgExecuteContract",     # CosmWasm DEX (Astroport, etc.) — checked below
})


class CosmosChainCompiler(_GenericTransferChainCompiler):
    """Trace compiler for Cosmos-based blockchains.

    Queries native ATOM transfers and IBC token transfers from the PostgreSQL
    event store.  Returns empty lists when the event store has no data for an
    address (triggering on-demand ingest via the ingest_pending signal).

    Args:
        postgres_pool: asyncpg pool for event store reads.
        neo4j_driver:  Not used; kept for interface compatibility.
        redis_client:  Not used; kept for interface compatibility.
    """

    @property
    def supported_chains(self) -> List[str]:
        """Return the list of chain names this compiler handles."""
        return ["cosmos"]

    def _native_symbol(self, chain: str) -> str:
        """Return the native asset ticker for Cosmos.

        Args:
            chain: Must be ``"cosmos"``.

        Returns:
            ``"ATOM"``
        """
        return "ATOM"

    def _native_canonical_asset_id(self, chain: str) -> Optional[str]:
        """Return the CoinGecko asset ID for the Cosmos native token.

        Args:
            chain: Must be ``"cosmos"``.

        Returns:
            ``"cosmos"`` (CoinGecko ID for ATOM).
        """
        return "cosmos"

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
        """Promote Cosmos DEX / AMM interactions into swap_event nodes.

        Detects Osmosis gamm/poolmanager swap message types and CosmWasm DEX
        interactions stored in ``tx_type`` by the Cosmos collector.  First
        attempts a full ``swap_event`` via ``_maybe_build_swap_event``; falls
        back to a labelled ``dex`` service node when token-transfer legs are
        unavailable.

        Note: ``MsgExecuteContract`` is included in the detection set because
        CosmWasm-based DEXes (e.g. Astroport, White Whale) use it for swap
        execution.  However, not every ``MsgExecuteContract`` is a swap — if
        token-transfer leg inference yields nothing, the fallback service node
        uses the generic label ``"Cosmos DEX"`` rather than inventing semantics.

        Args:
            tx_type: Short message type name stored by the Cosmos collector.
            (other args: see base class)

        Returns:
            (nodes, edges) on success, or None to fall through.
        """
        if tx_type not in _COSMOS_SWAP_MSG_TYPES:
            return None

        # Use a more specific label for known Osmosis message types.
        if tx_type.startswith("MsgSwap") or tx_type.startswith("MsgSplitRoute"):
            protocol_id = "osmosis_dex"
            protocol_label = "Osmosis DEX"
        else:
            protocol_id = "cosmos_dex"
            protocol_label = "Cosmos DEX"

        # Attempt full swap_event via token-transfer leg inference.
        swap_result = await self._maybe_build_swap_event(
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
            protocol_id=protocol_id,
            protocol_label=protocol_label,
            protocol_type="dex",
        )
        if swap_result is not None:
            return swap_result

        # Fallback: labelled service node so the DEX hop is not invisible.
        lineage = mk_lineage(session_id, branch_id, path_id, depth)
        node_id = mk_node_id(chain, "service", counterparty)
        edge_id = mk_edge_id(chain, tx_hash, seed_node_id, node_id)

        svc_node = InvestigationNode(
            node_id=node_id,
            lineage_id=lineage,
            node_type="service",
            branch_id=branch_id,
            path_id=path_id,
            depth=depth + 1,
            display_label=protocol_label,
            display_sublabel="DEX interaction",
            chain=chain,
            expandable_directions=[],
            service_node_data=ServiceNodeData(
                service_id=counterparty,
                protocol_id=protocol_id,
                display_name=protocol_label,
                service_type="dex",
                chain=chain,
                address=counterparty,
            ),
            activity_summary=ActivitySummary(
                activity_type="dex_interaction",
                title=f"{protocol_label} swap",
                protocol_id=protocol_id,
            ),
        )
        edge = InvestigationEdge(
            edge_id=edge_id,
            lineage_id=lineage,
            source_node_id=seed_node_id,
            target_node_id=node_id,
            edge_type="interacts_with",
            branch_id=branch_id,
            path_id=path_id,
            depth=depth,
            tx_hash=tx_hash,
            timestamp=timestamp,
        )
        return [svc_node], [edge]

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
        """Return outbound fund flows from ``seed_address`` on Cosmos.

        Queries native ATOM and IBC token transfers where ``seed_address``
        is the sender.

        Args:
            session_id:    Investigation session UUID.
            branch_id:     Branch ID for lineage.
            path_sequence: Integer index for path_id generation.
            depth:         Current hop depth from session root.
            seed_address:  Cosmos bech32 address to expand from.
            chain:         Must be ``"cosmos"``.
            options:       Expansion options (filters, max_results).

        Returns:
            Tuple of (nodes, edges).
        """
        addr = self._normalize_address(seed_address)
        rows = await self._fetch_outbound_event_store(addr, chain, options)
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
        """Return inbound fund flows into ``seed_address`` on Cosmos.

        Mirrors expand_next but queries for ``seed_address`` as the recipient.

        Args: same as expand_next.

        Returns:
            Tuple of (nodes, edges).
        """
        addr = self._normalize_address(seed_address)
        rows = await self._fetch_inbound_event_store(addr, chain, options)
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
