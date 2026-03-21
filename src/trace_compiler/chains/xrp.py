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
from typing import List
from typing import Optional
from typing import Tuple

from src.trace_compiler.chains._transfer_base import _GenericTransferChainCompiler
from src.trace_compiler.models import ExpandOptions
from src.trace_compiler.models import InvestigationEdge
from src.trace_compiler.models import InvestigationNode

logger = logging.getLogger(__name__)


class XRPChainCompiler(_GenericTransferChainCompiler):
    """Trace compiler for the XRP Ledger.

    Handles native XRP transfers and XRPL issued asset transfers from the
    PostgreSQL event store.  No Neo4j fallback.

    XRP addresses are base58check-encoded and case-sensitive — this compiler
    preserves address casing rather than lowercasing (see ``_normalize_address``).

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
