"""Address enrichment for InvestigationNode objects.

Applies sanctions screening and entity attribution to address nodes
produced by chain compilers.  All calls are best-effort — failures are
swallowed so enrichment never blocks an expansion response.

Design choices:
- Groups nodes by chain to support multi-chain expansions (bridge hops).
- Entity lookup is batched per-chain (one round-trip per chain present).
- Sanctions screening is per-address but failures are individually absorbed.
- Only ``node_type == "address"`` nodes are enriched; swap_event, service,
  bridge_hop, and UTXO nodes are passed through unchanged.
- Enrichment is always applied on serve (cache hit or miss) so sanctions
  data never goes stale due to the expansion cache TTL.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict
from typing import List

from src.trace_compiler.models import InvestigationNode

logger = logging.getLogger(__name__)

# Maps risk_level strings returned by the entity service to numeric scores.
_ENTITY_RISK_MAP: Dict[str, float] = {
    "low": 0.2,
    "medium": 0.4,
    "high": 0.7,
    "critical": 0.9,
}


async def enrich_nodes(nodes: List[InvestigationNode]) -> List[InvestigationNode]:
    """Apply sanctions and entity enrichment to address nodes in-place.

    Non-address nodes are returned unchanged.  All external calls are
    wrapped in try/except so a service outage degrades gracefully to
    zero-enrichment rather than raising.

    Args:
        nodes: List of InvestigationNodes from a chain compiler or cache.

    Returns:
        The same list with address nodes enriched in-place via model_copy.
    """
    address_nodes = [n for n in nodes if n.node_type == "address"]
    if not address_nodes:
        return nodes

    # Group address nodes by chain for efficient bulk lookups.
    by_chain: Dict[str, List[InvestigationNode]] = defaultdict(list)
    for node in address_nodes:
        by_chain[node.chain].append(node)

    # Build lookup tables: node_id → mutable enrichment dict.
    updates: Dict[str, Dict] = {}

    # --- Bulk entity attribution (one call per chain) ----------------------
    try:
        from src.api.graph_dependencies import lookup_addresses_bulk

        for chain, chain_nodes in by_chain.items():
            addresses = [
                n.address_data.address
                for n in chain_nodes
                if n.address_data is not None
            ]
            if not addresses:
                continue

            try:
                results = await lookup_addresses_bulk(addresses, chain)
            except Exception as exc:
                logger.debug("Entity lookup failed chain=%s: %s", chain, exc)
                continue

            for node in chain_nodes:
                if node.address_data is None:
                    continue
                addr = node.address_data.address
                info = results.get(addr)
                if not info:
                    continue
                patch = updates.setdefault(node.node_id, {})
                if info.get("entity_name"):
                    patch["entity_name"] = info["entity_name"]
                    patch.setdefault("display_sublabel", info["entity_name"])
                if info.get("entity_type"):
                    patch["entity_type"] = info["entity_type"]
                if info.get("category"):
                    patch["entity_category"] = info["category"]
                risk_val = _ENTITY_RISK_MAP.get(info.get("risk_level", ""), 0.0)
                if risk_val > patch.get("risk_score", 0.0):
                    patch["risk_score"] = risk_val
    except ImportError:
        pass  # graph_dependencies absent — running without enrichment

    # --- Sanctions screening (per-address) ---------------------------------
    try:
        from src.api.graph_dependencies import screen_address

        for chain, chain_nodes in by_chain.items():
            for node in chain_nodes:
                if node.address_data is None:
                    continue
                addr = node.address_data.address
                try:
                    result = await screen_address(addr, chain)
                except Exception as exc:
                    logger.debug(
                        "Sanctions screen failed addr=%s chain=%s: %s", addr, chain, exc
                    )
                    continue
                if result and result.get("matched"):
                    patch = updates.setdefault(node.node_id, {})
                    patch["sanctioned"] = True
                    patch["risk_score"] = max(patch.get("risk_score", 0.0), 0.95)
                    if result.get("list_name"):
                        patch["sanctions_list"] = result["list_name"]
                    patch.setdefault("risk_factors", [])
                    if "sanctions" not in patch["risk_factors"]:
                        patch["risk_factors"].append("sanctions")
    except ImportError:
        pass

    if not updates:
        return nodes

    # Apply collected patches via immutable model_copy.
    node_index = {n.node_id: i for i, n in enumerate(nodes)}
    result_list = list(nodes)
    for node_id, patch in updates.items():
        idx = node_index.get(node_id)
        if idx is None:
            continue
        node = result_list[idx]
        # Merge risk_factors with existing list.
        if "risk_factors" in patch:
            merged = list(node.risk_factors) + [
                f for f in patch.pop("risk_factors") if f not in node.risk_factors
            ]
            patch["risk_factors"] = merged
        # Only raise risk_score, never lower it (compiler may have set one already).
        if "risk_score" in patch:
            patch["risk_score"] = max(node.risk_score, patch["risk_score"])
        result_list[idx] = node.model_copy(update=patch)

    return result_list
