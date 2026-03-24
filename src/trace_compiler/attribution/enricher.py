"""Address enrichment for InvestigationNode objects.

Applies sanctions screening, entity attribution, and risk taint propagation
to nodes produced by chain compilers.  All calls are best-effort — failures
are swallowed so enrichment never blocks an expansion response.

Design choices:
- Groups nodes by chain to support multi-chain expansions (bridge hops).
- Entity lookup is batched per-chain (one round-trip per chain present).
- Sanctions screening is per-address but failures are individually absorbed.
- Only ``node_type == "address"`` nodes are enriched via external calls;
  swap_event, bridge_hop, and UTXO nodes are passed through unchanged.
- Service nodes receive risk signals derived from the service classifier
  (mixer, sanctioned) — these are set at build time, not here.
- Taint propagation: after external enrichment, the edge topology is used to
  propagate risk signals from high-risk nodes to connected address nodes:
    * mixer service node  → connected address: ``mixer_interaction`` risk factor,
      risk_score floored at 0.75.
    * sanctioned node     → connected address: ``sanctioned_counterparty`` risk
      factor, risk_score floored at 0.65.
- Enrichment is always applied on serve (cache hit or miss) so sanctions
  data never goes stale due to the expansion cache TTL.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict
from typing import List
from typing import Optional

from src.trace_compiler.models import InvestigationEdge
from src.trace_compiler.models import InvestigationNode

logger = logging.getLogger(__name__)

# Maps risk_level strings returned by the entity service to numeric scores.
_ENTITY_RISK_MAP: Dict[str, float] = {
    "low": 0.2,
    "medium": 0.4,
    "high": 0.7,
    "critical": 0.9,
}

# Minimum risk_score applied to an address node that directly interacted with
# a mixer service, regardless of its own entity risk level.
_MIXER_TAINT_FLOOR: float = 0.75

# Minimum risk_score applied to an address node that is directly connected to
# a sanctioned node (address or service).
_SANCTIONED_COUNTERPARTY_FLOOR: float = 0.65


def _propagate_service_risk(
    updates: Dict[str, Dict],
    nodes: List[InvestigationNode],
    edges: List[InvestigationEdge],
) -> None:
    """Propagate risk from high-risk service/address nodes to their neighbours.

    Two taint rules are applied based on edge topology:

    1. **Mixer taint**: any address node directly connected (in either
       direction) to a ``service_type="mixer"`` node receives the
       ``mixer_interaction`` risk factor and has its ``risk_score`` floored
       at ``_MIXER_TAINT_FLOOR``.

    2. **Sanctioned-counterparty taint**: any address node directly connected
       to a node with ``sanctioned=True`` (whether an address or a service
       node, e.g. a Tornado Cash pool) receives ``sanctioned_counterparty``
       and has its ``risk_score`` floored at ``_SANCTIONED_COUNTERPARTY_FLOOR``.

    Taint is intentionally **one hop only** — propagating further would flag
    innocent intermediaries.  Multi-hop taint analysis requires a dedicated
    investigation-level risk engine that is out of scope here.

    Args:
        updates: Mutable dict of ``{node_id: patch_dict}`` accumulated by
                 the caller.  This function adds to it in-place.
        nodes:   All nodes in the current expansion result.
        edges:   All edges in the current expansion result.
    """
    if not edges:
        return

    node_map: Dict[str, InvestigationNode] = {n.node_id: n for n in nodes}

    # Build adjacency: node_id → set of neighbour node_ids (undirected).
    neighbours: Dict[str, List[str]] = defaultdict(list)
    for edge in edges:
        neighbours[edge.source_node_id].append(edge.target_node_id)
        neighbours[edge.target_node_id].append(edge.source_node_id)

    for node in nodes:
        # ---- Mixer service taint ----
        if (
            node.node_type == "service"
            and node.service_data is not None
            and node.service_data.service_type == "mixer"
        ):
            for neighbour_id in neighbours.get(node.node_id, []):
                neighbour = node_map.get(neighbour_id)
                if neighbour is None or neighbour.node_type != "address":
                    continue
                patch = updates.setdefault(neighbour_id, {})
                patch.setdefault("risk_factors", [])
                if "mixer_interaction" not in patch["risk_factors"]:
                    patch["risk_factors"].append("mixer_interaction")
                patch["risk_score"] = max(
                    patch.get("risk_score", 0.0), _MIXER_TAINT_FLOOR
                )

        # ---- Sanctioned node counterparty taint ----
        # Covers both sanctioned address nodes (e.g. OFAC-listed wallet) and
        # sanctioned service nodes (e.g. Tornado Cash pool).
        if node.sanctioned:
            for neighbour_id in neighbours.get(node.node_id, []):
                neighbour = node_map.get(neighbour_id)
                if neighbour is None or neighbour.node_type != "address":
                    continue
                patch = updates.setdefault(neighbour_id, {})
                patch.setdefault("risk_factors", [])
                if "sanctioned_counterparty" not in patch["risk_factors"]:
                    patch["risk_factors"].append("sanctioned_counterparty")
                patch["risk_score"] = max(
                    patch.get("risk_score", 0.0), _SANCTIONED_COUNTERPARTY_FLOOR
                )


async def enrich_nodes(
    nodes: List[InvestigationNode],
    edges: Optional[List[InvestigationEdge]] = None,
) -> List[InvestigationNode]:
    """Apply sanctions, entity enrichment, and risk taint to address nodes.

    Non-address nodes are returned unchanged (service, bridge_hop, swap_event,
    UTXO nodes receive no external lookups).  All external calls are wrapped in
    try/except so a service outage degrades gracefully to zero-enrichment rather
    than raising.

    When ``edges`` is provided, a taint propagation pass runs after external
    enrichment: mixer and sanctioned nodes elevate the risk scores of directly
    connected address nodes.

    Args:
        nodes: List of InvestigationNodes from a chain compiler or cache.
        edges: Optional list of InvestigationEdges from the same expansion.
               Required for taint propagation; omit for backwards-compatible
               single-node enrichment (e.g. seed node at session create time).

    Returns:
        A new list with address nodes enriched via model_copy. The original
        list and nodes are not mutated.
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

    # --- Taint propagation from high-risk nodes to connected addresses ------
    # Runs after external enrichment so that newly-flagged sanctioned addresses
    # (discovered above) also contribute to the taint pass.
    # Requires edges to determine topology; safe to skip when not provided.
    if edges:
        # Re-apply any sanction patches discovered above so _propagate_service_risk
        # sees the updated sanctioned=True state when walking neighbours.
        _apply_patches_to_node_list = {n.node_id: n for n in nodes}
        patched_for_taint = list(nodes)
        for node_id, patch in updates.items():
            idx_map = {n.node_id: i for i, n in enumerate(patched_for_taint)}
            idx = idx_map.get(node_id)
            if idx is not None and "sanctioned" in patch:
                patched_for_taint[idx] = patched_for_taint[idx].model_copy(
                    update={"sanctioned": patch["sanctioned"]}
                )
        _propagate_service_risk(updates, patched_for_taint, edges)

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
