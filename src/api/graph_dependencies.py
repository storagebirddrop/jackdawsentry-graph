"""Graph-only wrappers for optional enrichments.

The standalone graph repo intentionally excludes several private/compliance
modules. These helpers let the graph surface import cleanly and degrade
gracefully when those optional enrichments are absent.
"""

from __future__ import annotations

from typing import Any
from typing import Dict
from typing import List
from typing import Set


class _NullEdgePriceOracle:
    """No-op price oracle used when the private price service is absent."""

    async def enrich_edge_fiat_values(
        self,
        edges: List[Dict[str, Any]],
        blockchain: str,
        default_timestamp: Any,
    ) -> None:
        return None


_NULL_EDGE_PRICE_ORACLE = _NullEdgePriceOracle()


async def lookup_addresses_bulk(
    addresses: List[str],
    blockchain: str,
) -> Dict[str, Dict[str, Any]]:
    """Return entity labels when the optional attribution service exists."""
    try:
        from src.services.entity_attribution import (
            lookup_addresses_bulk as entity_lookup_bulk,
        )
    except ImportError:
        return {}
    return await entity_lookup_bulk(addresses, blockchain)


async def screen_address(address: str, blockchain: str) -> Dict[str, Any]:
    """Return sanctions match info when the optional service exists."""
    try:
        from src.services.sanctions import screen_address as sanctions_screen
    except ImportError:
        return {"matched": False}
    return await sanctions_screen(address, blockchain)


def get_edge_price_oracle() -> Any:
    """Return the graph edge price oracle, or a no-op fallback."""
    try:
        from src.services.price_oracle import get_price_oracle
    except ImportError:
        return _NULL_EDGE_PRICE_ORACLE
    return get_price_oracle()


def get_known_bridge_addresses() -> Set[str]:
    """Return known bridge addresses when protocol metadata is available."""
    try:
        from src.analysis.protocol_registry import get_known_bridge_addresses
    except ImportError:
        return set()
    return set(get_known_bridge_addresses())


def get_known_mixer_addresses() -> Set[str]:
    """Return known mixer addresses when protocol metadata is available."""
    try:
        from src.analysis.protocol_registry import get_known_mixer_addresses
    except ImportError:
        return set()
    return set(get_known_mixer_addresses())


def get_known_dex_addresses() -> Set[str]:
    """Return known DEX addresses when protocol metadata is available."""
    try:
        from src.analysis.protocol_registry import get_known_dex_addresses
    except ImportError:
        return set()
    return set(get_known_dex_addresses())


async def get_contract_info(
    address: str,
    chain: str,
    *,
    redis_client: Any = None,
) -> Any:
    """Return contract deployer info when the contract_info service exists.

    Args:
        address:      Address to look up (EVM hex or Solana base58).
        chain:        Chain identifier (e.g. ``"ethereum"``, ``"bsc"``,
                      ``"solana"``).
        redis_client: Optional async Redis client for caching.

    Returns:
        :class:`~src.services.contract_info.ContractInfo` or ``None``.
    """
    try:
        from src.services.contract_info import (
            get_contract_info as _get_contract_info,
        )
    except ImportError:
        return None
    return await _get_contract_info(address, chain, redis_client=redis_client)
