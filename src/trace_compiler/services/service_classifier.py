"""
ServiceClassifier — reclassifies known protocol contract addresses as named
service nodes during trace compiler expansion.

Without this layer, every interaction with Uniswap, Tornado Cash, or a
bridge router renders as an anonymous contract address.  With it, the
investigation graph shows "Uniswap V3 Router" or "Tornado Cash 0.1 ETH"
as semantically-typed service nodes.

The registry is built from two sources:
1. Bridge protocol contracts from ``src.tracing.bridge_registry`` (bridges
   are services with ``service_type="bridge"``).
2. A hardcoded seed list of well-known DEX, mixer, and aggregator contracts
   covering the highest-traffic EVM protocols.

The seed list is intentionally kept minimal — it captures the most important
protocols for compliance investigation (Uniswap, 1inch, Tornado Cash, etc.)
rather than attempting exhaustive coverage.  The Neo4j ``ServiceNode`` records
that back the full registry can be populated separately via the seed script
and then loaded at compiler startup.

``process_row()`` is the unified entry point for chain compilers.  It returns
``None`` when the address is not a known service contract, or (nodes, edges)
when it is — in which case the caller must skip creating a plain address node.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from src.trace_compiler.lineage import edge_id as mk_edge_id
from src.trace_compiler.lineage import lineage_id as mk_lineage
from src.trace_compiler.lineage import node_id as mk_node_id
from src.trace_compiler.models import ActivitySummary
from src.trace_compiler.models import InvestigationEdge
from src.trace_compiler.models import InvestigationNode
from src.trace_compiler.models import ServiceNodeData

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal service record
# ---------------------------------------------------------------------------


@dataclass
class _ServiceRecord:
    """Minimal registry entry for a named on-chain service."""

    protocol_id: str
    display_name: str
    service_type: str  # bridge | dex | mixer | aggregator | router | cex | lending
    # All chains this protocol operates on (informational; not used for lookup).
    chains: List[str] = field(default_factory=list)
    # Contract addresses per chain: chain -> [lowercase addr, ...]
    contracts: Dict[str, List[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Hardcoded seed list of well-known EVM service contracts
# Bridges are intentionally excluded here — they are handled by BridgeHopCompiler.
# ---------------------------------------------------------------------------

_SEED_SERVICES: List[_ServiceRecord] = [
    # ---- Uniswap ----
    _ServiceRecord(
        protocol_id="uniswap_v2",
        display_name="Uniswap V2",
        service_type="dex",
        chains=["ethereum"],
        contracts={
            "ethereum": ["0x7a250d5630b4cf539739df2c5dacb4c659f2488d"],
        },
    ),
    _ServiceRecord(
        protocol_id="uniswap_v3",
        display_name="Uniswap V3",
        service_type="dex",
        chains=["ethereum", "arbitrum", "optimism", "polygon", "base"],
        contracts={
            "ethereum":  ["0xe592427a0aece92de3edee1f18e0157c05861564",
                          "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45"],
            "arbitrum":  ["0xe592427a0aece92de3edee1f18e0157c05861564",
                          "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45"],
            "optimism":  ["0xe592427a0aece92de3edee1f18e0157c05861564",
                          "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45"],
            "polygon":   ["0xe592427a0aece92de3edee1f18e0157c05861564",
                          "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45"],
            "base":      ["0x2626664c2603336e57b271c5c0b26f421741e481"],
        },
    ),
    # ---- SushiSwap ----
    _ServiceRecord(
        protocol_id="sushiswap",
        display_name="SushiSwap",
        service_type="dex",
        chains=["ethereum", "bsc", "polygon", "arbitrum"],
        contracts={
            "ethereum":  ["0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f"],
            "bsc":       ["0x1b02da8cb0d097eb8d57a175b88c7d8b47997506"],
            "polygon":   ["0x1b02da8cb0d097eb8d57a175b88c7d8b47997506"],
            "arbitrum":  ["0x1b02da8cb0d097eb8d57a175b88c7d8b47997506"],
        },
    ),
    # ---- PancakeSwap ----
    _ServiceRecord(
        protocol_id="pancakeswap",
        display_name="PancakeSwap",
        service_type="dex",
        chains=["bsc", "ethereum"],
        contracts={
            "bsc":       ["0x10ed43c718714eb63d5aa57b78b54704e256024e"],
            "ethereum":  ["0xeff92a263d31888d860bd50809a8d171709b7b1c"],
        },
    ),
    # ---- Curve Finance ----
    _ServiceRecord(
        protocol_id="curve",
        display_name="Curve Finance",
        service_type="dex",
        chains=["ethereum", "arbitrum", "optimism", "polygon"],
        contracts={
            "ethereum": [
                "0x99a58482bd75cbab83b27ec03ca68ff489b5788f",  # Router
                "0xd9e68a3f5f4b3b8b5db9f9a3e0f8e1d0a5b3c7d4",  # Dispatcher
            ],
        },
    ),
    # ---- 1inch ----
    _ServiceRecord(
        protocol_id="1inch",
        display_name="1inch Aggregator",
        service_type="aggregator",
        chains=["ethereum", "bsc", "polygon", "arbitrum", "optimism"],
        contracts={
            "ethereum": [
                "0x1111111254eeb25477b68fb85ed929f73a960582",  # v5 Router
                "0x111111125421ca6dc452d289314280a0f8842a65",  # v6 Router
            ],
            "bsc":       ["0x1111111254eeb25477b68fb85ed929f73a960582"],
            "polygon":   ["0x1111111254eeb25477b68fb85ed929f73a960582"],
            "arbitrum":  ["0x1111111254eeb25477b68fb85ed929f73a960582"],
            "optimism":  ["0x1111111254eeb25477b68fb85ed929f73a960582"],
        },
    ),
    # ---- Tornado Cash (mixer — high compliance relevance) ----
    _ServiceRecord(
        protocol_id="tornado_cash",
        display_name="Tornado Cash",
        service_type="mixer",
        chains=["ethereum"],
        contracts={
            "ethereum": [
                # ETH pools
                "0x12d66f87a04a9e220c9d1306ece5fd55a1b48b87",  # 0.1 ETH
                "0x47ce0c6ed5b0ce3d3a51fdb1c52dc66a7c3c2936",  # 1 ETH
                "0x910cbd523d972eb0a6f4cae4618ad62622b39dbf",  # 10 ETH
                "0xa160cdab225685da1d56aa342ad8841c3b53f291",  # 100 ETH
                # USDC pools
                "0xd4b88df4d29f5cedd6857912842cff3b20c8cfa3",  # 100 USDC
                "0xfd8610d20aa15b7b2e3be39b396a1bc3516c7144",  # 1000 USDC
                "0x07687e702b410fa43f4cb4af7fa097918ffd2730",  # 10000 USDC
                "0x23773e65ed146a459667be7d4ef92b00507df436",  # 100000 USDC
                # USDT pools
                "0x6acdfba02d0b97a4d7c3a7c9bc27d3de0527c9f6",  # 100 USDT
                # DAI pools
                "0x4736dcf1b7a3d580672cce6e7c65cd5cc9cfba9d",  # 100 DAI
                "0xaf4c0b70b2ea9fb7487c7cbb37ada259579fe040",  # 1000 DAI
                "0xd96f2b1c14db8458374d9aca76e26c3950113464",  # 10000 DAI
                # Router
                "0x905b63fff465b9ffbf41dea908ceb12478ec7601",
            ],
        },
    ),
    # ---- Balancer ----
    _ServiceRecord(
        protocol_id="balancer",
        display_name="Balancer",
        service_type="dex",
        chains=["ethereum", "polygon", "arbitrum"],
        contracts={
            "ethereum": ["0xba12222222228d8ba445958a75a0704d566bf2c8"],  # Vault
            "polygon":  ["0xba12222222228d8ba445958a75a0704d566bf2c8"],
            "arbitrum": ["0xba12222222228d8ba445958a75a0704d566bf2c8"],
        },
    ),
    # ---- Aave (lending — relevant for DeFi tracing) ----
    _ServiceRecord(
        protocol_id="aave_v3",
        display_name="Aave V3",
        service_type="lending",
        chains=["ethereum", "polygon", "arbitrum", "optimism", "avalanche"],
        contracts={
            "ethereum":  ["0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2"],  # Pool
            "polygon":   ["0x794a61358d6845594f94dc1db02a252b5b4814ad"],
            "arbitrum":  ["0x794a61358d6845594f94dc1db02a252b5b4814ad"],
            "optimism":  ["0x794a61358d6845594f94dc1db02a252b5b4814ad"],
            "avalanche": ["0x794a61358d6845594f94dc1db02a252b5b4814ad"],
        },
    ),
    # =========================================================================
    # Solana DEX / aggregator programs
    # Program IDs are case-sensitive base58 strings; stored as-is (not lowercased).
    # The lookup at classify time must match the program ID exactly.
    # =========================================================================
    # ---- Raydium ----
    _ServiceRecord(
        protocol_id="raydium_amm",
        display_name="Raydium AMM",
        service_type="dex",
        chains=["solana"],
        contracts={
            "solana": [
                "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # AMM v4
                "5quBtoiQqxF9Jv6KYKctB59NT3gtJD2Y65kdnB1Uev3h",  # AMM stable
            ],
        },
    ),
    # ---- Raydium CLMM ----
    _ServiceRecord(
        protocol_id="raydium_clmm",
        display_name="Raydium CLMM",
        service_type="dex",
        chains=["solana"],
        contracts={
            "solana": [
                "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",  # CLMM
            ],
        },
    ),
    # ---- Orca ----
    _ServiceRecord(
        protocol_id="orca_whirlpool",
        display_name="Orca Whirlpool",
        service_type="dex",
        chains=["solana"],
        contracts={
            "solana": [
                "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",  # Whirlpool
            ],
        },
    ),
    _ServiceRecord(
        protocol_id="orca_v2",
        display_name="Orca V2",
        service_type="dex",
        chains=["solana"],
        contracts={
            "solana": [
                "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP",  # Orca v2
            ],
        },
    ),
    # ---- Jupiter ----
    _ServiceRecord(
        protocol_id="jupiter",
        display_name="Jupiter Aggregator",
        service_type="aggregator",
        chains=["solana"],
        contracts={
            "solana": [
                "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",  # v6
                "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB",  # v4
                "JUP3c2Uh3WA4Ng34tw6kPd2G4LFfwhV3IwZ9JHfKq4e",  # v3
            ],
        },
    ),
    # ---- OpenBook (Serum successor) ----
    _ServiceRecord(
        protocol_id="openbook",
        display_name="OpenBook DEX",
        service_type="dex",
        chains=["solana"],
        contracts={
            "solana": [
                "srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX",  # Serum DEX v3
                "opnb2LAfJYbRMAHHvqjCwQxanZn7ReEHp1k81EohpZb",  # OpenBook v2
            ],
        },
    ),
    # ---- Meteora ----
    _ServiceRecord(
        protocol_id="meteora",
        display_name="Meteora",
        service_type="dex",
        chains=["solana"],
        contracts={
            "solana": [
                "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB",  # DLMM
                "M2mx93ekt1fmXSVkTrUL9xVFHkmME8HTUi5Cyc5aF7K",  # Meteora Pools
            ],
        },
    ),
    # ---- Phoenix ----
    _ServiceRecord(
        protocol_id="phoenix",
        display_name="Phoenix DEX",
        service_type="dex",
        chains=["solana"],
        contracts={
            "solana": [
                "PhoeNiXZ8ByJGLkxNfZRnkUfjvmuYqLR89jjFHGqdXY",
            ],
        },
    ),
    # =========================================================================
    # Tron DEX / aggregator contracts
    # Addresses are stored in the event store as 25-byte hex strings produced by
    # TronCollector.base58_to_hex() — base58check decoded bytes including the
    # 4-byte checksum, lowercased.  Format: 41<20-byte-addr><4-byte-checksum>.
    # =========================================================================
    # ---- JustSwap / SunSwap V1 (Uniswap V2 fork, highest USDT volume on Tron) ----
    _ServiceRecord(
        protocol_id="justswap_v1",
        display_name="JustSwap (SunSwap V1)",
        service_type="dex",
        chains=["tron"],
        contracts={
            # TXF1xDbVGdxFGbovmmmXvBGu8ZiE3Lq4mR — JustSwap Router
            "tron": ["41e95812d8d5b5412d2b9f3a4d5a87ca15c5c51f33366bfa2c"],
        },
    ),
    # ---- SunSwap V2 ----
    _ServiceRecord(
        protocol_id="sunswap_v2",
        display_name="SunSwap V2",
        service_type="dex",
        chains=["tron"],
        contracts={
            # TKzxdSv2FZKQrEqkKVgp5DcwEXBEKMg2Ax — SunSwap V2 Router
            "tron": ["416e0617948fe030a7e4970f8389d4ad295f249b7ee9ecb03d"],
        },
    ),
    # ---- SunSwap V3 / StableSwap ----
    _ServiceRecord(
        protocol_id="sunswap_v3",
        display_name="SunSwap V3",
        service_type="dex",
        chains=["tron"],
        contracts={
            # TSy7jXKKpckJ8zqUiUECG9U7LjkdJxnNEb — SunSwap V3 Router
            "tron": ["41ba75bdae5ae107596be3e36f0bae72f21b608ec92cba7aa0"],
        },
    ),
]


# ---------------------------------------------------------------------------
# ServiceClassifier
# ---------------------------------------------------------------------------


class ServiceClassifier:
    """Reclassifies known protocol contracts as ServiceNode investigation nodes.

    The registry is built lazily on first use.  Bridge contracts from the
    bridge registry are explicitly excluded because ``BridgeHopCompiler``
    handles those with richer semantics (correlation, pending/confirmed status,
    destination chain).

    Args:
        postgres_pool: Reserved for future Neo4j / PostgreSQL-backed service
                       lookup.  Not used in the current in-memory implementation.
    """

    def __init__(self, postgres_pool=None, neo4j_driver=None):
        # chain -> lowercase_addr -> _ServiceRecord
        self._lookup: Dict[str, Dict[str, _ServiceRecord]] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Registry bootstrap
    # ------------------------------------------------------------------

    def _ensure_registry(self) -> None:
        """Populate the in-memory lookup from the seed list.

        Bridge contracts are intentionally excluded — they are handled by
        BridgeHopCompiler which produces richer BridgeHop nodes.
        """
        if self._loaded:
            return

        # First, collect bridge contract addresses to exclude.
        bridge_addrs: Dict[str, set] = {}
        try:
            from src.tracing.bridge_registry import BRIDGE_REGISTRY
            for protocol in BRIDGE_REGISTRY.values():
                for chain, addrs in protocol.known_contract_addresses.items():
                    bucket = bridge_addrs.setdefault(chain, set())
                    bucket.update(a.lower() for a in addrs)
        except Exception as exc:
            logger.debug("ServiceClassifier: could not load bridge registry: %s", exc)

        for record in _SEED_SERVICES:
            for chain, addrs in record.contracts.items():
                chain_bucket = self._lookup.setdefault(chain, {})
                excl = bridge_addrs.get(chain, set())
                for addr in addrs:
                    addr_lc = addr.lower()
                    if addr_lc not in excl:
                        chain_bucket[addr_lc] = record

        self._loaded = True

    def is_service_contract(self, chain: str, address: str) -> bool:
        """Return True when ``address`` is a known service contract on ``chain``."""
        self._ensure_registry()
        return address.lower() in self._lookup.get(chain, {})

    def get_record(self, chain: str, address: str) -> Optional[_ServiceRecord]:
        """Return the ``_ServiceRecord`` for ``address`` on ``chain``, or None."""
        self._ensure_registry()
        return self._lookup.get(chain, {}).get(address.lower())

    # ------------------------------------------------------------------
    # Node / edge construction
    # ------------------------------------------------------------------

    def build_service_node(
        self,
        record: _ServiceRecord,
        contract_address: str,
        chain: str,
        tx_hash: str,
        session_id: str,
        branch_id: str,
        path_id: str,
        depth: int,
        timestamp: Optional[str] = None,
        value_native: Optional[float] = None,
        value_fiat: Optional[float] = None,
        asset_symbol: Optional[str] = None,
        canonical_asset_id: Optional[str] = None,
        direction: Optional[str] = None,
    ) -> InvestigationNode:
        """Build a ServiceNode InvestigationNode for a known protocol contract.

        Args:
            record:           Service registry entry.
            contract_address: The specific contract address that was matched.
            chain:            Blockchain name.
            session_id:       Investigation session UUID.
            branch_id:        Branch ID for lineage.
            path_id:          Path ID for lineage.
            depth:            Hop depth from the session root.

        Returns:
            InvestigationNode with ``node_type="service"``.
        """
        node_id = mk_node_id(chain, "service", f"{record.protocol_id}:{tx_hash}")
        lineage = mk_lineage(session_id, branch_id, path_id, depth)

        # Aggregate all known contract addresses for this protocol on this chain.
        known = record.contracts.get(chain, [])
        if contract_address.lower() not in known:
            known = [contract_address.lower()] + known

        return InvestigationNode(
            node_id=node_id,
            lineage_id=lineage,
            node_type="service",
            branch_id=branch_id,
            path_id=path_id,
            depth=depth,
            display_label=record.display_name,
            display_sublabel=f"{record.service_type.upper()} · {tx_hash[:10]}…",
            chain=chain,
            expandable_directions=[],  # Service nodes are not expanded further.
            service_data=ServiceNodeData(
                protocol_id=record.protocol_id,
                service_type=record.service_type,
                known_contracts=known,
            ),
            activity_summary=ActivitySummary(
                activity_type=_activity_type_for_service(record.service_type),
                title=f"{record.display_name} interaction",
                protocol_id=record.protocol_id,
                protocol_type=record.service_type,
                tx_hash=tx_hash,
                tx_chain=chain,
                timestamp=timestamp,
                direction=direction,
                contract_address=contract_address.lower(),
                asset_symbol=asset_symbol,
                canonical_asset_id=canonical_asset_id,
                value_native=value_native,
                value_fiat=value_fiat,
                route_summary=f"{record.display_name} {record.service_type} contract interaction",
            ),
        )

    # ------------------------------------------------------------------
    # Unified entry point used by chain compiler _build_graph()
    # ------------------------------------------------------------------

    async def process_row(
        self,
        *,
        tx_hash: str,
        to_address: str,
        chain: str,
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
        direction: str,
    ) -> Optional[Tuple[List[InvestigationNode], List[InvestigationEdge]]]:
        """Process a single expansion row and return service nodes + edge if detected.

        Returns None when the address is not a known service contract.
        Returns (nodes, edges) when it is — the caller must skip creating a
        plain address node for ``to_address``.

        Args:
            tx_hash:           Transaction hash.
            to_address:        Counterparty address being classified.
            chain:             Blockchain name.
            seed_node_id:      Node ID of the expanding seed address.
            session_id:        Investigation session UUID.
            branch_id:         Branch ID for lineage.
            path_id:           Path ID for lineage.
            depth:             Current hop depth.
            timestamp:         ISO-8601 timestamp string, or None.
            value_native:      Transfer value in native currency.
            value_fiat:        Transfer value in USD, or None.
            asset_symbol:      Asset symbol, or None.
            canonical_asset_id: Cross-chain canonical asset ID, or None.
            direction:         ``"forward"`` or ``"backward"``.

        Returns:
            None or (nodes, edges) tuple.
        """
        record = self.get_record(chain, to_address)
        if record is None:
            return None

        service_node = self.build_service_node(
            record=record,
            contract_address=to_address,
            chain=chain,
            tx_hash=tx_hash,
            session_id=session_id,
            branch_id=branch_id,
            path_id=path_id,
            depth=depth + 1,
            timestamp=timestamp,
            value_native=value_native,
            value_fiat=value_fiat,
            asset_symbol=asset_symbol,
            canonical_asset_id=canonical_asset_id,
            direction=direction,
        )

        if direction == "forward":
            src_node_id = seed_node_id
            tgt_node_id = service_node.node_id
        else:
            src_node_id = service_node.node_id
            tgt_node_id = seed_node_id

        edge = InvestigationEdge(
            edge_id=mk_edge_id(src_node_id, tgt_node_id, branch_id, tx_hash),
            source_node_id=src_node_id,
            target_node_id=tgt_node_id,
            branch_id=branch_id,
            path_id=path_id,
            edge_type="service_deposit" if direction == "forward" else "service_receipt",
            value_native=value_native,
            value_fiat=value_fiat,
            asset_symbol=asset_symbol,
            canonical_asset_id=canonical_asset_id,
            tx_hash=tx_hash or None,
            tx_chain=chain,
            timestamp=timestamp,
            direction=direction,
            activity_summary=service_node.activity_summary,
        )

        short_hash = (tx_hash[:16] if tx_hash else "None")

        logger.debug(
            "ServiceClassifier: %s (%s) classified for tx %s on %s",
            record.display_name,
            record.service_type,
            short_hash,
            chain,
        )

        return [service_node], [edge]


def _activity_type_for_service(service_type: str) -> str:
    mapping = {
        "dex": "dex_interaction",
        "router": "router_interaction",
        "mixer": "mixer_interaction",
        "cex": "cex_interaction",
    }
    return mapping.get(service_type, "service_interaction")
