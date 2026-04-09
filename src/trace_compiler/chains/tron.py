"""
TronChainCompiler — trace compiler for the Tron blockchain.

Data sources:
1. PostgreSQL event store ``raw_transactions`` WHERE blockchain='tron' — native
   TRX transfers (from_address / to_address / value_native).
2. PostgreSQL event store ``raw_token_transfers`` WHERE blockchain='tron' — TRC-20
   token transfers (e.g. USDT, USDC on Tron).
3. PostgreSQL event store ``raw_evm_logs`` WHERE blockchain='tron' — JustSwap /
   SunSwap V2/V3 Swap events (dual-written by TronCollector via migration 013).

No Neo4j fallback: the Tron collector only writes to the event store.

Address normalization: Tron addresses are stored as hex (base58check → hex via
TronCollector).  Lowercasing is safe and consistent with the event store schema.

Swap detection: JustSwap V1 and SunSwap V2/V3 interactions are promoted to
``swap_event`` nodes via ``_try_swap_promotion`` when raw DEX Swap logs are
available in ``raw_evm_logs_tron``.  Falls back to a plain DEX service node
when log data is absent (e.g. data not yet collected).
"""

from __future__ import annotations

import logging
from typing import Any
from typing import List
from typing import Optional
from typing import Tuple

from src.trace_compiler.asset_selection import build_asset_option
from src.trace_compiler.asset_selection import dedupe_asset_options
from src.trace_compiler.chains._transfer_base import _GenericTransferChainCompiler
from src.trace_compiler.models import AssetOption
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

    async def list_asset_options(
        self,
        *,
        seed_address: str,
        chain: str,
    ) -> List[AssetOption]:
        if self._pg is None:
            return []

        try:
            async with self._pg.acquire() as conn:
                native_exists = await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM raw_transactions
                        WHERE blockchain = 'tron'
                          AND (from_address = $1 OR to_address = $1)
                          AND value_native > 0
                    )
                    """,
                    seed_address,
                )
                token_rows = await conn.fetch(
                    """
                    SELECT
                        asset_contract AS chain_asset_id,
                        MAX(NULLIF(asset_symbol, '')) AS asset_symbol,
                        MAX(canonical_asset_id) AS canonical_asset_id,
                        MAX(timestamp) AS last_seen
                    FROM raw_token_transfers
                    WHERE blockchain = 'tron'
                      AND (from_address = $1 OR to_address = $1)
                      AND asset_contract IS NOT NULL
                    GROUP BY asset_contract
                    ORDER BY MAX(timestamp) DESC NULLS LAST
                    LIMIT 40
                    """,
                    seed_address,
                )
        except Exception as exc:
            logger.debug("TronChainCompiler.list_asset_options failed for %s: %s", seed_address, exc)
            return []

        options: List[AssetOption] = []
        if native_exists:
            options.append(build_asset_option(mode="native", chain=chain, asset_symbol="TRX"))
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
        return dedupe_asset_options(options)

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
        """Promote a JustSwap / SunSwap interaction into a swap_event node.

        Delegates to ``_maybe_build_swap_event`` (inherited from the shared base)
        when the service record identifies a DEX or aggregator contract.  Swap
        amounts are read from the ``raw_evm_logs_tron`` partition (migration 013)
        when available; falls back to token-transfer leg inference otherwise.

        Args:
            tx_hash:        Transaction hash.
            seed_node_id:   Node ID of the address being expanded.
            seed_address:   Normalized (lowercased hex) address being expanded.
            counterparty:   Normalized DEX contract address (25-byte hex).
            chain:          Must be ``"tron"``.
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
