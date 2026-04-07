"""
BridgeHopCompiler — converts bridge contract transactions into first-class
BridgeHop investigation nodes during trace compiler expansion.

Called from chain compiler ``_build_graph()`` methods when a counterparty
address matches a known bridge protocol contract.  Replaces the raw address
node with a semantically rich BridgeHop node that surfaces the protocol name,
mechanism, destination chain, asset transformation, and correlation status.

The lookup path is:
1. Check the bridge contract registry (in-memory, loaded once at startup).
2. Query the ``bridge_correlations`` PostgreSQL table for an existing record.
3. If no record exists, return a ``status="pending"`` BridgeHop node.
   The CorrelationWorker will resolve it asynchronously.
"""

from __future__ import annotations

import logging
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple

from src.trace_compiler.lineage import edge_id as mk_edge_id
from src.trace_compiler.lineage import lineage_id as mk_lineage
from src.trace_compiler.lineage import node_id as mk_node_id

try:
    from src.tracing.bridge_tracer import BridgeTracer
except ImportError:
    BridgeTracer = None  # type: ignore[assignment]
from src.trace_compiler.models import ActivitySummary
from src.trace_compiler.models import BridgeHopData
from src.trace_compiler.models import InvestigationEdge
from src.trace_compiler.models import InvestigationNode

logger = logging.getLogger(__name__)


class BridgeHopCompiler:
    """Detects bridge transactions and materialises BridgeHop investigation nodes.

    Args:
        postgres_pool: asyncpg pool for ``bridge_correlations`` lookups.
                       When ``None``, all lookups are skipped and every detected
                       bridge hop node carries ``status="pending"``.
    """

    def __init__(self, postgres_pool=None, redis_client=None):
        self._pg = postgres_pool
        self._redis = redis_client
        # Per-chain cache: chain -> frozenset of lowercase contract addresses.
        self._contracts: Dict[str, Set[str]] = {}
        self._protocol_map: Dict[str, Dict[str, Any]] = {}
        self._registry_loaded = False

    # ------------------------------------------------------------------
    # Registry bootstrap
    # ------------------------------------------------------------------

    def _ensure_registry(self) -> None:
        """Populate the in-memory contract-address lookup structures.

        Called lazily on first use so the import cost is deferred and
        circular-import risk is avoided at module load time.
        """
        if self._registry_loaded:
            return
        from src.tracing.bridge_registry import BRIDGE_REGISTRY
        for protocol in BRIDGE_REGISTRY.values():
            for chain, addrs in protocol.known_contract_addresses.items():
                bucket = self._contracts.setdefault(chain, set())
                for addr in addrs:
                    addr_lc = addr.lower()
                    bucket.add(addr_lc)
                    self._protocol_map.setdefault(chain, {})[addr_lc] = protocol
        self._registry_loaded = True

    def is_bridge_contract(self, chain: str, address: str) -> bool:
        """Return True if ``address`` is a known bridge contract on ``chain``."""
        self._ensure_registry()
        return address.lower() in self._contracts.get(chain, set())

    def get_protocol(self, chain: str, address: str) -> Optional[Any]:
        """Return the BridgeProtocol for ``address`` on ``chain``, or None."""
        self._ensure_registry()
        return self._protocol_map.get(chain, {}).get(address.lower())

    # ------------------------------------------------------------------
    # PostgreSQL correlation lookup
    # ------------------------------------------------------------------

    async def lookup_correlation(
        self, chain: str, tx_hash: str
    ) -> Optional[Dict[str, Any]]:
        """Query ``bridge_correlations`` for a pre-computed hop record.

        Returns a plain dict of the first matching row, or None when
        no record exists or the pool is unavailable.
        """
        if self._pg is None:
            return None
        sql = """
            SELECT
                protocol_id AS protocol,
                mechanism,
                source_chain,
                destination_chain,
                source_asset,
                destination_asset,
                CAST(source_amount  AS FLOAT)  AS source_amount,
                CAST(destination_amount AS FLOAT) AS destination_amount,
                time_delta_seconds,
                correlation_confidence,
                status,
                order_id,
                destination_tx_hash,
                destination_address
            FROM bridge_correlations
            WHERE source_chain = $1
              AND source_tx_hash = $2
            LIMIT 1
        """
        try:
            async with self._pg.acquire() as conn:
                row = await conn.fetchrow(sql, chain, tx_hash)
            return dict(row) if row else None
        except Exception as exc:
            logger.debug(
                "BridgeHopCompiler.lookup_correlation failed for %s/%s: %s",
                chain,
                tx_hash,
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # Node / edge construction
    # ------------------------------------------------------------------

    def build_hop_node(
        self,
        protocol: Any,
        correlation: Optional[Dict[str, Any]],
        tx_hash: str,
        source_chain: str,
        session_id: str,
        branch_id: str,
        path_id: str,
        depth: int,
    ) -> InvestigationNode:
        """Build a BridgeHop InvestigationNode from detection and correlation data.

        Args:
            protocol:      BridgeProtocol instance from the registry.
            correlation:   Row dict from ``bridge_correlations``, or None when
                           the hop has not yet been resolved.
            tx_hash:       Source-chain transaction hash (used to derive node_id).
            source_chain:  Chain the ingress transaction occurred on.
            session_id:    Investigation session UUID.
            branch_id:     Branch ID for lineage.
            path_id:       Path ID for lineage.
            depth:         Hop depth in the investigation graph.

        Returns:
            InvestigationNode with ``node_type="bridge_hop"``.
        """
        if correlation:
            status = correlation.get("status", "pending")
            dest_chain = correlation.get("destination_chain")
            source_asset = correlation.get("source_asset") or ""
            dest_asset = correlation.get("destination_asset") or source_asset
            source_amount = float(correlation.get("source_amount") or 0.0)
            dest_amount = correlation.get("destination_amount")
            dest_amount = float(dest_amount) if dest_amount is not None else None
            time_delta = correlation.get("time_delta_seconds")
            conf = float(correlation.get("correlation_confidence") or 1.0)
            is_same = source_asset == dest_asset
            destination_tx_hash = correlation.get("destination_tx_hash")
            order_id = correlation.get("order_id")
        else:
            status = "pending"
            dest_chain = None
            source_asset = ""
            dest_asset = ""
            source_amount = 0.0
            dest_amount = None
            time_delta = None
            conf = 0.5
            is_same = False
            destination_tx_hash = None
            order_id = None

        dest_label = f"→{dest_chain.upper()}" if dest_chain else "→pending"
        display_label = f"{protocol.display_name} {dest_label}"
        display_sublabel = f"{status.upper()} · conf {conf:.0%}"

        node_id = mk_node_id(source_chain, "bridge_hop", tx_hash)
        lineage = mk_lineage(session_id, branch_id, path_id, depth)

        # Completed hops with a known destination chain can be expanded further.
        expandable = ["next"] if status == "completed" and dest_chain else []

        return InvestigationNode(
            node_id=node_id,
            lineage_id=lineage,
            node_type="bridge_hop",
            branch_id=branch_id,
            path_id=path_id,
            depth=depth,
            display_label=display_label,
            display_sublabel=display_sublabel,
            chain=source_chain,
            expandable_directions=expandable,
            bridge_hop_data=BridgeHopData(
                hop_id=tx_hash,
                protocol_id=protocol.protocol_id,
                mechanism=protocol.mechanism,
                source_chain=source_chain,
                destination_chain=dest_chain,
                source_asset=source_asset,
                destination_asset=dest_asset,
                source_amount=source_amount,
                destination_amount=dest_amount,
                time_delta_seconds=float(time_delta) if time_delta is not None else None,
                correlation_confidence=conf,
                status=status,
                is_same_asset=is_same,
            ),
            activity_summary=ActivitySummary(
                activity_type="bridge",
                title=f"{protocol.display_name} bridge hop",
                protocol_id=protocol.protocol_id,
                protocol_type="bridge",
                tx_hash=tx_hash,
                tx_chain=source_chain,
                status=status,
                source_chain=source_chain,
                destination_chain=dest_chain,
                source_tx_hash=tx_hash,
                destination_tx_hash=destination_tx_hash,
                order_id=order_id,
                source_asset=source_asset,
                destination_asset=dest_asset,
                source_amount=source_amount,
                destination_amount=dest_amount,
                route_summary=f"{source_chain} -> {dest_chain}" if dest_chain else f"{source_chain} -> pending",
            ),
        )

    def build_dest_node(
        self,
        correlation: Dict[str, Any],
        session_id: str,
        branch_id: str,
        path_id: str,
        depth: int,
    ) -> Optional[InvestigationNode]:
        """Build a destination-side address node from a completed correlation.

        Returns None when the destination address is unknown.
        """
        dest_addr = correlation.get("destination_address")
        dest_chain = correlation.get("destination_chain")
        if not dest_addr or not dest_chain:
            return None

        from src.trace_compiler.models import AddressNodeData

        node_id = mk_node_id(dest_chain, "address", dest_addr)
        lineage = mk_lineage(session_id, branch_id, path_id, depth + 1)
        short = dest_addr[:10] + "…" if len(dest_addr) > 10 else dest_addr
        return InvestigationNode(
            node_id=node_id,
            lineage_id=lineage,
            node_type="address",
            branch_id=branch_id,
            path_id=path_id,
            depth=depth + 1,
            display_label=short,
            display_sublabel=dest_chain.upper(),
            chain=dest_chain,
            expandable_directions=["prev", "next", "neighbors"],
            address_data=AddressNodeData(
                address=dest_addr,
                address_type="unknown",
            ),
        )

    def build_edges(
        self,
        source_node_id: str,
        hop_node: InvestigationNode,
        dest_node: Optional[InvestigationNode],
        branch_id: str,
        path_id: str,
        tx_hash: str,
        source_chain: str,
        timestamp: Optional[str],
        value_native: Optional[float],
        value_fiat: Optional[float],
        asset_symbol: Optional[str],
        canonical_asset_id: Optional[str],
        chain_asset_id: Optional[str],
    ) -> List[InvestigationEdge]:
        """Build source → hop and (if resolved) hop → destination edges.

        Args:
            source_node_id:    Node ID of the expanding seed address.
            hop_node:          BridgeHop node carrying the resolved activity summary.
            dest_node:         Destination-side address node, or None.
            branch_id:         Branch ID for lineage.
            path_id:           Path ID for lineage.
            tx_hash:           Source-chain transaction hash.
            source_chain:      Chain the transaction occurred on.
            timestamp:         ISO-8601 timestamp string, or None.
            value_native:      Transfer value in native currency units.
            value_fiat:        Transfer value in fiat (USD), or None.
            asset_symbol:      Asset symbol (e.g. "ETH"), or None.
            canonical_asset_id: Cross-chain canonical asset identifier, or None.

        Returns:
            List of InvestigationEdge objects (1 or 2 elements).
        """
        edges: List[InvestigationEdge] = []

        # Source → BridgeHop
        edges.append(
            InvestigationEdge(
                edge_id=mk_edge_id(source_node_id, hop_node.node_id, branch_id, tx_hash),
                source_node_id=source_node_id,
                target_node_id=hop_node.node_id,
                branch_id=branch_id,
                path_id=path_id,
                edge_type="bridge_source",
                value_native=value_native,
                value_fiat=value_fiat,
                asset_symbol=asset_symbol,
                canonical_asset_id=canonical_asset_id,
                chain_asset_id=chain_asset_id,
                tx_hash=tx_hash or None,
                tx_chain=source_chain,
                timestamp=timestamp,
                direction="forward",
                activity_summary=ActivitySummary(
                    activity_type="bridge",
                    title="Bridge ingress",
                    protocol_id=hop_node.activity_summary.protocol_id if hop_node.activity_summary else None,
                    protocol_type="bridge",
                    tx_hash=tx_hash or None,
                    tx_chain=source_chain,
                    timestamp=timestamp,
                    direction="forward",
                    source_chain=source_chain,
                    destination_chain=dest_node.chain if dest_node else None,
                    source_tx_hash=tx_hash or None,
                    destination_tx_hash=hop_node.activity_summary.destination_tx_hash if hop_node.activity_summary else None,
                    order_id=hop_node.activity_summary.order_id if hop_node.activity_summary else None,
                    asset_symbol=asset_symbol,
                    canonical_asset_id=canonical_asset_id,
                    chain_asset_id=chain_asset_id,
                    value_native=value_native,
                    value_fiat=value_fiat,
                ),
            )
        )

        # BridgeHop → Destination (only when destination address is known)
        if dest_node:
            edges.append(
                InvestigationEdge(
                    edge_id=mk_edge_id(
                        hop_node.node_id, dest_node.node_id, branch_id, tx_hash
                    ),
                    source_node_id=hop_node.node_id,
                    target_node_id=dest_node.node_id,
                    branch_id=branch_id,
                    path_id=path_id,
                    edge_type="bridge_dest",
                    value_native=None,
                    value_fiat=None,
                    asset_symbol=None,
                    canonical_asset_id=None,
                    tx_hash=tx_hash or None,
                    tx_chain=dest_node.chain,
                    timestamp=timestamp,
                    direction="forward",
                    activity_summary=ActivitySummary(
                        activity_type="bridge",
                        title="Bridge egress",
                        protocol_type="bridge",
                        tx_hash=None,
                        tx_chain=dest_node.chain,
                        timestamp=timestamp,
                        direction="forward",
                        source_chain=source_chain,
                        destination_chain=dest_node.chain,
                        source_tx_hash=tx_hash or None,
                        destination_tx_hash=hop_node.activity_summary.destination_tx_hash if hop_node.activity_summary else None,
                        order_id=hop_node.activity_summary.order_id if hop_node.activity_summary else None,
                    ),
                )
            )

        return edges

    # ------------------------------------------------------------------
    # Calldata-based destination extraction
    # ------------------------------------------------------------------

    async def _calldata_destination(
        self,
        tx_hash: str,
        contract_address: str,
        source_chain: str,
    ) -> Optional[Dict[str, Any]]:
        """Attempt to extract a cross-chain destination from bridge calldata.

        Fetches ``input_data`` for ``tx_hash`` from ``raw_transactions``, then
        delegates to the calldata decoder which first tries ABI-based decoding
        (Etherscan-verified ABI, cached in Redis) and falls back to heuristic
        pattern scanning.

        Args:
            tx_hash:          Source-chain transaction hash.
            contract_address: Bridge contract address (used for ABI lookup).
            source_chain:     Chain the transaction occurred on.

        Returns:
            Synthetic correlation dict compatible with ``build_hop_node`` /
            ``build_dest_node``, or None if no cross-chain destination was found.
        """
        if self._pg is None:
            return None

        # Solana path: query raw_solana_instructions and apply the heuristic
        # instruction decoder.  Solana tx signatures are base58 (mixed case) so
        # they must NOT be lowercased.
        if source_chain == "solana":
            return await self._calldata_destination_solana(tx_hash, contract_address)

        # Fetch raw calldata from the event store.
        try:
            async with self._pg.acquire() as conn:
                input_data: Optional[bytes] = await conn.fetchval(
                    """
                    SELECT input_data
                    FROM raw_transactions
                    WHERE blockchain = $1
                      AND tx_hash = $2
                    LIMIT 1
                    """,
                    source_chain,
                    tx_hash.lower(),
                )
        except Exception as exc:
            logger.debug(
                "BridgeHopCompiler: input_data fetch failed for %s/%s: %s",
                source_chain,
                tx_hash[:16],
                exc,
            )
            return None

        if not input_data:
            return None

        try:
            from src.trace_compiler.calldata.decoder import decode_bridge_destination

            dest = await decode_bridge_destination(
                input_data=input_data,
                contract_address=contract_address,
                chain=source_chain,
                redis_client=self._redis,
            )
        except Exception as exc:
            logger.debug(
                "BridgeHopCompiler: calldata decode failed for %s/%s: %s",
                source_chain,
                tx_hash[:16],
                exc,
            )
            return None

        if dest is None:
            return None

        logger.info(
            "BridgeHopCompiler: calldata decoded destination %s (%s) for tx %s "
            "(function=%s, confidence=%.2f)",
            dest.destination_address,
            dest.destination_chain,
            tx_hash[:16],
            dest.source_function,
            dest.confidence,
        )

        return {
            "protocol": None,
            "mechanism": None,
            "source_chain": source_chain,
            "destination_chain": dest.destination_chain,
            "destination_address": dest.destination_address,
            "source_asset": None,
            "destination_asset": None,
            "source_amount": 0.0,
            "destination_amount": None,
            "time_delta_seconds": None,
            "correlation_confidence": dest.confidence,
            "status": "completed",
            "order_id": None,
            "destination_tx_hash": None,
        }

    async def _calldata_destination_solana(
        self,
        tx_hash: str,
        program_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Extract a cross-chain destination from a Solana bridge instruction.

        Queries ``raw_solana_instructions`` for instruction data stored during
        live-fetch ingest, then applies the heuristic Solana decoder which
        scans for ABI-padded EVM addresses and Tron addresses encoded inline
        in the instruction payload.

        Solana tx signatures are base58 (mixed-case) and must NOT be
        lowercased when used as a DB key.

        Args:
            tx_hash:    Solana transaction signature (base58).
            program_id: Bridge program address (used for logging / decoder).

        Returns:
            Synthetic correlation dict or None if no destination found.
        """
        # Fetch the raw instruction bytes stored as hex in decoded_args during
        # live-fetch ingest.  The outermost instruction (lowest ix_index) for
        # the given program is used — bridges encode the destination there.
        # raw_transactions cannot be used here because it has a UNIQUE constraint
        # on (blockchain, tx_hash) and is reserved for real SOL transfer rows.
        try:
            async with self._pg.acquire() as conn:
                raw_hex: Optional[str] = await conn.fetchval(
                    """
                    SELECT decoded_args->>'raw_data'
                    FROM raw_solana_instructions
                    WHERE tx_signature = $1
                      AND program_id   = $2
                      AND decode_status = 'raw'
                      AND decoded_args ? 'raw_data'
                    ORDER BY ix_index ASC
                    LIMIT 1
                    """,
                    tx_hash,
                    program_id,
                )
        except Exception as exc:
            logger.debug(
                "BridgeHopCompiler: solana ix fetch failed for %s/%s: %s",
                tx_hash[:16], program_id[:16], exc,
            )
            return None

        if not raw_hex:
            return None

        try:
            input_data = bytes.fromhex(raw_hex)
        except ValueError:
            logger.debug(
                "BridgeHopCompiler: invalid hex in decoded_args for %s/%s",
                tx_hash[:16], program_id[:16],
            )
            return None

        try:
            from src.trace_compiler.calldata.solana_decoder import (
                decode_solana_bridge_destination,
            )

            dest = decode_solana_bridge_destination(input_data, program_id)
        except Exception as exc:
            logger.debug(
                "BridgeHopCompiler: solana decode failed %s/%s: %s",
                tx_hash[:16], program_id[:16], exc,
            )
            return None

        if dest is None:
            return None

        logger.info(
            "BridgeHopCompiler: solana heuristic decoded destination %s (%s) "
            "for tx %s (confidence=%.2f)",
            dest.destination_address,
            dest.destination_chain,
            tx_hash[:16],
            dest.confidence,
        )

        return {
            "protocol": None,
            "mechanism": None,
            "source_chain": "solana",
            "destination_chain": dest.destination_chain,
            "destination_address": dest.destination_address,
            "source_asset": None,
            "destination_asset": None,
            "source_amount": 0.0,
            "destination_amount": None,
            "time_delta_seconds": None,
            "correlation_confidence": dest.confidence,
            "status": "completed",
            "order_id": None,
            "destination_tx_hash": None,
        }

    # ------------------------------------------------------------------
    # Atomiq Exchange public API lookup
    # ------------------------------------------------------------------

    async def _atomiq_api_lookup(
        self,
        tx_hash: str,
        source_chain: str,
    ) -> Optional[Dict[str, Any]]:
        """Query the Atomiq Exchange public API to resolve both legs of an atomic swap.

        Atomiq connects Bitcoin (L1 + Lightning) to Solana via trustless HTLCs
        and PrTLCs.  Their public API requires no authentication and can be
        queried by Solana tx signature or Bitcoin tx hash, returning the full
        cross-chain swap record including both-side amounts, addresses, and status.

        Args:
            tx_hash:      Solana tx signature (base58) or Bitcoin tx hash (hex).
            source_chain: ``"solana"`` or ``"bitcoin"`` — determines query parameter.

        Returns:
            Synthetic correlation dict compatible with ``build_hop_node`` /
            ``build_dest_node``, or None on any failure or no-match.
        """
        try:
            import aiohttp
        except ImportError:
            logger.debug("BridgeHopCompiler: aiohttp unavailable, skipping Atomiq lookup")
            return None

        if source_chain == "solana":
            params: Dict[str, Any] = {"txInit": tx_hash, "chain": "SOLANA", "limit": 10}
        elif source_chain == "bitcoin":
            params = {"btcTxId": tx_hash, "limit": 10}
        else:
            return None

        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    "https://api.atomiq.exchange/api/GetSwapList",
                    params=params,
                ) as resp:
                    if resp.status != 200:
                        logger.debug(
                            "BridgeHopCompiler: Atomiq API HTTP %s for %s",
                            resp.status,
                            tx_hash[:16],
                        )
                        return None
                    data = await resp.json()
        except Exception as exc:
            logger.debug(
                "BridgeHopCompiler: Atomiq API request failed for %s: %s",
                tx_hash[:16],
                exc,
            )
            return None

        items = data.get("data") or []
        if not items:
            return None

        try:
            item = items[0]
            direction = item.get("direction", "")   # "ToBTC" or "FromBTC"
            swap_type = item.get("type", "CHAIN")   # "CHAIN" or "LN"
            finished = bool(item.get("finished"))
            success = bool(item.get("success"))

            if finished and success:
                status = "completed"
            elif finished:
                status = "failed"
            else:
                status = "pending"

            btc_chain = "lightning" if swap_type == "LN" else "bitcoin"

            if direction == "ToBTC":
                dest_chain = btc_chain
                src_asset = item.get("tokenName") or ""
                dest_asset = "BTC"
                src_amount = float(item.get("tokenAmount") or 0.0)
                dest_amount_raw = item.get("btcAmount")
                dest_amount = float(dest_amount_raw) if dest_amount_raw else None
                dest_address = item.get("btcAddress")
                dest_tx = item.get("btcTx")
            else:
                # FromBTC: Bitcoin → Solana
                dest_chain = "solana"
                src_asset = "BTC"
                dest_asset = item.get("tokenName") or ""
                btc_amt = item.get("btcAmount")
                src_amount = float(btc_amt) if btc_amt else 0.0
                tok_amt = item.get("tokenAmount")
                dest_amount = float(tok_amt) if tok_amt else None
                dest_address = item.get("clientWallet")
                dest_tx = item.get("txFinish") or item.get("txInit")
        except (ValueError, TypeError) as exc:
            logger.error(
                "BridgeHopCompiler: failed to parse Atomiq response for %s on %s: %s",
                tx_hash[:16],
                source_chain,
                exc,
            )
            return None

        logger.info(
            "BridgeHopCompiler: Atomiq API resolved %s→%s for tx %s "
            "(status=%s, dest_addr=%s)",
            source_chain,
            dest_chain,
            tx_hash[:16],
            status,
            (dest_address or "")[:16],
        )

        return {
            "protocol": "atomiq",
            "mechanism": "atomic_swap",
            "source_chain": source_chain,
            "destination_chain": dest_chain,
            "destination_address": dest_address,
            "source_asset": src_asset,
            "destination_asset": dest_asset,
            "source_amount": src_amount,
            "destination_amount": dest_amount,
            "time_delta_seconds": None,
            "correlation_confidence": 1.0,
            "status": status,
            "order_id": item.get("id"),
            "destination_tx_hash": dest_tx,
        }

    # ------------------------------------------------------------------
    # Unified entry point used by chain compiler _build_graph()
    # ------------------------------------------------------------------

    async def process_row(
        self,
        *,
        tx_hash: str,
        to_address: str,
        source_chain: str,
        seed_node_id: str,
        session_id: str,
        branch_id: str,
        path_id: str,
        depth: int,
        timestamp: Optional[str],
        value_native: Optional[float],
        value_fiat: Optional[float],
        asset_symbol: Optional[str],
        canonical_asset_id: Optional[str],
        chain_asset_id: Optional[str],
    ) -> Optional[Tuple[List[InvestigationNode], List[InvestigationEdge]]]:
        """Process a single expansion row and return bridge nodes + edges if detected.

        Returns None when the ``to_address`` is not a known bridge contract.
        Returns (nodes, edges) when a bridge hop is detected.

        Callers should skip creating a plain address node for ``to_address``
        when this method returns a non-None result.
        """
        protocol = self.get_protocol(source_chain, to_address)
        if protocol is None:
            return None

        correlation = await self.lookup_correlation(source_chain, tx_hash)

        # If no cached correlation exists, attempt live resolution via BridgeTracer.
        # This covers protocols resolvable by tx_hash alone (THORChain, Wormhole,
        # Allbridge, Synapse, and solver protocols like LI.FI / Squid / Mayan /
        # deBridge / Symbiosis).  Protocols that require an intermediate ID from
        # decoded event logs remain pending.
        if correlation is None and BridgeTracer is not None:
            try:
                tracer = BridgeTracer()
                live = await tracer.detect_bridge_hop(tx_hash, source_chain, to_address)
                if live is not None:
                    await tracer.store_correlation(live)
                    # Re-fetch from DB so the dict shape matches lookup_correlation output.
                    correlation = await self.lookup_correlation(source_chain, tx_hash)
            except Exception as exc:
                logger.debug(
                    "BridgeHopCompiler: live resolution failed for %s/%s: %s",
                    source_chain,
                    tx_hash[:16],
                    exc,
                )

        # If still unresolved, attempt calldata-based destination extraction.
        # This decodes the destination cross-chain address directly from the
        # transaction input field using the bridge contract's verified ABI or
        # heuristic pattern scanning — covering custodial bridges like Bridgers
        # that embed the destination address as a calldata parameter.
        if correlation is None:
            correlation = await self._calldata_destination(
                tx_hash=tx_hash,
                contract_address=to_address,
                source_chain=source_chain,
            )

        # Atomiq atomic swap: query the public API to resolve both legs.
        # The API is authoritative (confidence=1.0), unauthenticated, and
        # returns the Bitcoin tx hash, destination address, amounts, and status
        # directly from Atomiq's swap database.  Covers Solana→BTC and BTC→Solana.
        if correlation is None and protocol.protocol_id == "atomiq":
            correlation = await self._atomiq_api_lookup(tx_hash, source_chain)

        hop_node = self.build_hop_node(
            protocol=protocol,
            correlation=correlation,
            tx_hash=tx_hash,
            source_chain=source_chain,
            session_id=session_id,
            branch_id=branch_id,
            path_id=path_id,
            depth=depth + 1,
        )

        dest_node = (
            self.build_dest_node(
                correlation=correlation,
                session_id=session_id,
                branch_id=branch_id,
                path_id=path_id,
                depth=depth + 1,
            )
            if correlation and correlation.get("status") == "completed"
            else None
        )

        nodes: List[InvestigationNode] = [hop_node]
        if dest_node:
            nodes.append(dest_node)

        edges = self.build_edges(
            source_node_id=seed_node_id,
            hop_node=hop_node,
            dest_node=dest_node,
            branch_id=branch_id,
            path_id=path_id,
            tx_hash=tx_hash,
            source_chain=source_chain,
            timestamp=timestamp,
            value_native=value_native,
            value_fiat=value_fiat,
            asset_symbol=asset_symbol,
            canonical_asset_id=canonical_asset_id,
            chain_asset_id=chain_asset_id,
        )

        logger.debug(
            "BridgeHopCompiler: %s bridge hop detected for tx %s on %s "
            "(status=%s, dest=%s)",
            protocol.protocol_id,
            tx_hash[:16],
            source_chain,
            hop_node.bridge_hop_data.status if hop_node.bridge_hop_data else "?",
            dest_node.node_id if dest_node else None,
        )

        return nodes, edges
