"""
SuiChainCompiler — trace compiler for the Sui blockchain.

Data sources:
1. PostgreSQL event store ``raw_transactions`` WHERE blockchain='sui' — native
   SUI transfers (from_address / to_address / value_native).
2. PostgreSQL event store ``raw_token_transfers`` WHERE blockchain='sui' — Sui
   token transfers (USDC on Sui, etc.).

No Neo4j fallback: the Sui collector only writes to the event store.

Address normalization: Sui addresses are 0x-prefixed 32-byte hex strings
(e.g. ``0x0000...abcd``).  Lowercasing is safe and consistent.
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


class SuiChainCompiler(_GenericTransferChainCompiler):
    """Trace compiler for the Sui blockchain.

    Queries native SUI transfers and Sui token transfers from the PostgreSQL
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
        return ["sui"]

    def _native_symbol(self, chain: str) -> str:
        """Return the native asset ticker for Sui.

        Args:
            chain: Must be ``"sui"``.

        Returns:
            ``"SUI"``
        """
        return "SUI"

    def _native_canonical_asset_id(self, chain: str) -> Optional[str]:
        """Return the CoinGecko asset ID for the Sui native token.

        Args:
            chain: Must be ``"sui"``.

        Returns:
            ``"sui"`` (CoinGecko ID for SUI).
        """
        return "sui"

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
        """Return outbound fund flows from ``seed_address`` on Sui.

        Queries native SUI and token transfers where ``seed_address``
        is the sender.

        Args:
            session_id:    Investigation session UUID.
            branch_id:     Branch ID for lineage.
            path_sequence: Integer index for path_id generation.
            depth:         Current hop depth from session root.
            seed_address:  Sui 0x-prefixed hex address to expand from.
            chain:         Must be ``"sui"``.
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
        """Return inbound fund flows into ``seed_address`` on Sui.

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
