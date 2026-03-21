"""
TronChainCompiler — trace compiler for the Tron blockchain.

Data sources:
1. PostgreSQL event store ``raw_transactions`` WHERE blockchain='tron' — native
   TRX transfers (from_address / to_address / value_native).
2. PostgreSQL event store ``raw_token_transfers`` WHERE blockchain='tron' — TRC-20
   token transfers (e.g. USDT, USDC on Tron).

No Neo4j fallback: the Tron collector only writes to the event store.  If the
event store is empty for an address, the compiler returns empty lists.

Address normalization: Tron addresses are stored in the event store as hex
(base58check → hex conversion applied by the Tron collector).  Lowercasing is
safe and consistent with the event store schema.

No swap detection in this initial version.

Phase 4 (Tron): expand_next / expand_prev wired through
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


class TronChainCompiler(_GenericTransferChainCompiler):
    """Trace compiler for the Tron blockchain.

    Queries native TRX transfers and TRC-20 token transfers from the PostgreSQL
    event store.  No Neo4j fallback is provided — returns empty lists when the
    event store has no data for an address.

    Args:
        postgres_pool: asyncpg pool for event store reads.
        neo4j_driver:  Not used; kept for interface compatibility.
        redis_client:  Not used; kept for interface compatibility.
    """

    @property
    def supported_chains(self) -> List[str]:
        """Return the list of chain names this compiler handles."""
        return ["tron"]

    def _native_symbol(self, chain: str) -> str:
        """Return the native asset ticker for Tron.

        Args:
            chain: Must be ``"tron"``.

        Returns:
            ``"TRX"``
        """
        return "TRX"

    def _native_canonical_asset_id(self, chain: str) -> Optional[str]:
        """Return the CoinGecko asset ID for the Tron native token.

        Args:
            chain: Must be ``"tron"``.

        Returns:
            ``"tron"`` (CoinGecko ID for TRX).
        """
        return "tron"

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
        """Return outbound fund flows from ``seed_address`` on Tron.

        Queries native TRX transfers and TRC-20 token transfers where
        ``seed_address`` is the sender.

        Args:
            session_id:    Investigation session UUID.
            branch_id:     Branch ID for lineage.
            path_sequence: Integer index for path_id generation.
            depth:         Current hop depth from session root.
            seed_address:  Tron address (hex, lowercase) to expand from.
            chain:         Must be ``"tron"``.
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
        """Return inbound fund flows into ``seed_address`` on Tron.

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
