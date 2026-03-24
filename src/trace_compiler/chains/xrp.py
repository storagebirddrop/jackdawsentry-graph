"""
XRPChainCompiler — trace compiler for the XRP Ledger.

Data sources:
1. PostgreSQL event store ``raw_transactions`` WHERE blockchain='xrp' — native
   XRP transfers (from_address / to_address / value_native).
2. PostgreSQL event store ``raw_token_transfers`` WHERE blockchain='xrp' — XRPL
   issued asset transfers (e.g. USDC, USDT, RLUSD, EURC bridged on XRPL).

No Neo4j fallback: the XRP collector only writes to the event store.  If the
event store is empty for an address, the compiler returns empty lists.

Address normalization: XRP Ledger addresses use base58check encoding and are
case-sensitive (the checksum includes case information).  This compiler overrides
``_normalize_address`` to return addresses unchanged (no lowercasing).

No swap detection in this initial version.

Phase 4 (XRP): expand_next / expand_prev wired through
``_GenericTransferChainCompiler._build_graph``.
"""

from __future__ import annotations

import logging
from typing import Any
from typing import Dict
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

# XRP transaction types that represent DEX / AMM activity on the XRPL.
# OfferCreate is the classic order-book DEX interaction.
# AMMSwap was added with the XLS-30d amendment (2024).
_XRP_SWAP_TX_TYPES = frozenset({"AMMSwap", "OfferCreate"})


class XRPChainCompiler(_GenericTransferChainCompiler):
    """Trace compiler for the XRP Ledger.

    Handles native XRP transfers and XRPL issued asset transfers from the
    PostgreSQL event store.  No Neo4j fallback.

    Detects DEX/AMM activity via transaction type (AMMSwap, OfferCreate) and
    promotes to swap_event nodes.  XRP addresses are base58check-encoded and
    case-sensitive — this compiler preserves address casing rather than 
    lowercasing (see ``_normalize_address``).

    Args:
        postgres_pool: asyncpg pool for event store reads.
        neo4j_driver:  Not used; kept for interface compatibility.
        redis_client:  Not used; kept for interface compatibility.
    """

    @property
    def supported_chains(self) -> List[str]:
        """Return the list of chain names this compiler handles."""
        return ["xrp"]

    def _native_symbol(self, chain: str) -> str:
        """Return the native asset ticker for the XRP Ledger.

        Args:
            chain: Must be ``"xrp"``.

        Returns:
            ``"XRP"``
        """
        return "XRP"

    def _native_canonical_asset_id(self, chain: str) -> Optional[str]:
        """Return the CoinGecko asset ID for the XRP Ledger native token.

        Args:
            chain: Must be ``"xrp"``.

        Returns:
            ``"ripple"`` (CoinGecko ID for XRP).
        """
        return "ripple"

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
        """Promote XRP Ledger DEX / AMM interactions into swap_event nodes.

        Detects ``AMMSwap`` (XLS-30d AMM) and ``OfferCreate`` (classic
        order-book) transaction types.  First attempts a full swap_event via
        ``_maybe_build_swap_event``; if token-transfer legs are unavailable in
        the event store, falls back to a labelled ``dex`` service node so the
        activity is still visible on the graph.

        Args:
            tx_type: XRPL ``TransactionType`` field stored in ``raw_transactions``.
            (other args: see base class)

        Returns:
            (nodes, edges) on success, or None to fall through.
        """
        if tx_type not in _XRP_SWAP_TX_TYPES:
            return None

        protocol_id = "xrp_amm" if tx_type == "AMMSwap" else "xrp_dex"
        protocol_label = "XRP AMM" if tx_type == "AMMSwap" else "XRP DEX"

        # Attempt full swap_event — requires both asset legs in raw_token_transfers.
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

        # Fallback: emit a labelled service node so the activity is not lost.
        return self._build_xrp_dex_service_node(
            tx_hash=tx_hash,
            seed_node_id=seed_node_id,
            counterparty=counterparty,
            chain=chain,
            session_id=session_id,
            branch_id=branch_id,
            path_id=path_id,
            depth=depth,
            timestamp=timestamp,
            protocol_id=protocol_id,
            protocol_label=protocol_label,
        )

    def _build_xrp_dex_service_node(
        self,
        *,
        tx_hash: str,
        seed_node_id: str,
        counterparty: str,
        chain: str,
        session_id: str,
        branch_id: str,
        path_id: str,
        depth: int,
        timestamp: Optional[str],
        protocol_id: str,
        protocol_label: str,
    ) -> Tuple[List[InvestigationNode], List[InvestigationEdge]]:
        """Build a labelled ``dex`` service node for an XRP DEX interaction.

        Used when token-transfer legs are unavailable and a full swap_event
        cannot be constructed.  The node is still semantically typed as a DEX
        interaction so it renders distinctly from a plain address node.

        Args:
            counterparty:   AMM pool or offer owner address.
            protocol_id:    ``"xrp_amm"`` or ``"xrp_dex"``.
            protocol_label: ``"XRP AMM"`` or ``"XRP DEX"``.
            (other args: standard lineage fields)

        Returns:
            (nodes, edges) tuple — always non-empty.
        """
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
                title=f"{protocol_label} interaction",
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

    def _normalize_address(self, addr: str) -> str:
        """Return the address unchanged.

        XRP Ledger addresses are base58check-encoded and case-sensitive —
        lowercasing would corrupt the checksum.  The event store stores them
        in their original mixed-case form as emitted by the collector.

        Args:
            addr: Raw XRP address string.

        Returns:
            The address unchanged.
        """
        return addr

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
        """Return outbound fund flows from ``seed_address`` on the XRP Ledger.

        Queries native XRP transfers and issued-asset transfers where
        ``seed_address`` is the sender.

        Args:
            session_id:    Investigation session UUID.
            branch_id:     Branch ID for lineage.
            path_sequence: Integer index for path_id generation.
            depth:         Current hop depth from session root.
            seed_address:  XRP address (original casing) to expand from.
            chain:         Must be ``"xrp"``.
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
        """Return inbound fund flows into ``seed_address`` on the XRP Ledger.

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
