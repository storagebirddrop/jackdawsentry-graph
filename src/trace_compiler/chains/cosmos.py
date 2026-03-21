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
from src.trace_compiler.models import ExpandOptions
from src.trace_compiler.models import InvestigationEdge
from src.trace_compiler.models import InvestigationNode

logger = logging.getLogger(__name__)


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
