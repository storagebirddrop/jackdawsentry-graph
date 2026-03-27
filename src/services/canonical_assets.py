"""Shared canonical asset identity helpers for token metadata and picker UX.

This module separates two related questions:

1. What is the economic underlying asset for pricing / cross-chain grouping?
2. Is the observed token contract the canonical issuer, a wrapped variant,
   a bridged representation, or only a heuristic symbol match?

The answer is returned as ``CanonicalAssetIdentity`` and reused by collectors,
the session asset catalog, and future UI surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

_HEX_ADDRESS_CHAINS = {
    "ethereum",
    "bsc",
    "polygon",
    "arbitrum",
    "base",
    "avalanche",
    "optimism",
    "starknet",
    "plasma",
}


@dataclass(frozen=True)
class CanonicalAssetIdentity:
    """Resolved asset identity for one chain-specific token or native asset."""

    canonical_asset_id: Optional[str] = None
    canonical_symbol: Optional[str] = None
    canonical_name: Optional[str] = None
    identity_status: str = "unknown"   # verified | heuristic | unknown
    variant_kind: str = "unknown"      # native | canonical | wrapped | bridged | unknown


@dataclass(frozen=True)
class _KnownAsset:
    canonical_asset_id: str
    canonical_symbol: str
    canonical_name: str
    variant_kind: str


_NATIVE_ASSETS: dict[str, _KnownAsset] = {
    "ethereum": _KnownAsset("ethereum", "ETH", "Ethereum", "native"),
    "bsc": _KnownAsset("binancecoin", "BNB", "BNB", "native"),
    "polygon": _KnownAsset("matic-network", "MATIC", "Polygon", "native"),
    "arbitrum": _KnownAsset("ethereum", "ETH", "Ethereum", "native"),
    "base": _KnownAsset("ethereum", "ETH", "Ethereum", "native"),
    "avalanche": _KnownAsset("avalanche-2", "AVAX", "Avalanche", "native"),
    "optimism": _KnownAsset("ethereum", "ETH", "Ethereum", "native"),
    "starknet": _KnownAsset("ethereum", "ETH", "Ethereum", "native"),
    "injective": _KnownAsset("injective-protocol", "INJ", "Injective", "native"),
    "tron": _KnownAsset("tron", "TRX", "TRON", "native"),
    "solana": _KnownAsset("solana", "SOL", "Solana", "native"),
    "xrp": _KnownAsset("ripple", "XRP", "XRP", "native"),
    "cosmos": _KnownAsset("cosmos", "ATOM", "Cosmos", "native"),
    "sui": _KnownAsset("sui", "SUI", "Sui", "native"),
    "bitcoin": _KnownAsset("bitcoin", "BTC", "Bitcoin", "native"),
    "lightning": _KnownAsset("bitcoin", "BTC", "Bitcoin", "native"),
}

_SYMBOL_ALIASES: dict[str, _KnownAsset] = {
    "USDT": _KnownAsset("tether", "USDT", "Tether USD", "canonical"),
    "USDC": _KnownAsset("usd-coin", "USDC", "USD Coin", "canonical"),
    "DAI": _KnownAsset("dai", "DAI", "Dai", "canonical"),
    "BUSD": _KnownAsset("binance-usd", "BUSD", "Binance USD", "canonical"),
    "ETH": _KnownAsset("ethereum", "ETH", "Ethereum", "canonical"),
    "WETH": _KnownAsset("ethereum", "ETH", "Ethereum", "wrapped"),
    "BTC": _KnownAsset("bitcoin", "BTC", "Bitcoin", "canonical"),
    "WBTC": _KnownAsset("bitcoin", "BTC", "Bitcoin", "wrapped"),
    "SOL": _KnownAsset("solana", "SOL", "Solana", "canonical"),
    "WSOL": _KnownAsset("solana", "SOL", "Solana", "wrapped"),
    "BNB": _KnownAsset("binancecoin", "BNB", "BNB", "canonical"),
    "WBNB": _KnownAsset("binancecoin", "BNB", "BNB", "wrapped"),
    "TRX": _KnownAsset("tron", "TRX", "TRON", "canonical"),
}

_VERIFIED_ASSET_ADDRESSES: dict[tuple[str, str], _KnownAsset] = {
    ("ethereum", "0xdac17f958d2ee523a2206206994597c13d831ec7"): _KnownAsset(
        "tether", "USDT", "Tether USD", "canonical"
    ),
    ("ethereum", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"): _KnownAsset(
        "usd-coin", "USDC", "USD Coin", "canonical"
    ),
    ("ethereum", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"): _KnownAsset(
        "ethereum", "ETH", "Ethereum", "wrapped"
    ),
    ("ethereum", "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"): _KnownAsset(
        "bitcoin", "BTC", "Bitcoin", "wrapped"
    ),
    ("bsc", "0x55d398326f99059ff775485246999027b3197955"): _KnownAsset(
        "tether", "USDT", "Tether USD", "bridged"
    ),
    ("bsc", "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"): _KnownAsset(
        "usd-coin", "USDC", "USD Coin", "bridged"
    ),
    ("bsc", "0xe9e7cea3dedca5984780bafc599bd69add087d56"): _KnownAsset(
        "binance-usd", "BUSD", "Binance USD", "canonical"
    ),
    ("polygon", "0xc2132d05d31c914a87c6611c10748aeb04b58e8f"): _KnownAsset(
        "tether", "USDT", "Tether USD", "bridged"
    ),
    ("polygon", "0x2791bca1f2de4661ed88a30c99a7a9449aa84174"): _KnownAsset(
        "usd-coin", "USDC", "USD Coin", "bridged"
    ),
    ("avalanche", "0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7"): _KnownAsset(
        "tether", "USDT", "Tether USD", "bridged"
    ),
    ("avalanche", "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e"): _KnownAsset(
        "usd-coin", "USDC", "USD Coin", "canonical"
    ),
    ("tron", "tr7nhqjekqxgtci8q8zy4pl8otszgjlj6t"): _KnownAsset(
        "tether", "USDT", "Tether USD", "canonical"
    ),
    ("solana", "epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v"): _KnownAsset(
        "usd-coin", "USDC", "USD Coin", "canonical"
    ),
    ("solana", "es9vmfrzacermjfrf4h2fyd4kconky11mcce8benwnyb"): _KnownAsset(
        "tether", "USDT", "Tether USD", "canonical"
    ),
    ("solana", "so11111111111111111111111111111111111111112"): _KnownAsset(
        "solana", "SOL", "Solana", "wrapped"
    ),
}

_BRIDGED_MARKERS = (
    "bridged",
    "bridge",
    "wormhole",
    "portal",
    "axelar",
    "multichain",
)


def normalize_asset_address(blockchain: str, asset_address: Optional[str]) -> Optional[str]:
    """Normalize a chain-specific asset address or mint for matching."""
    if asset_address is None:
        return None
    value = str(asset_address).strip()
    if not value:
        return None
    chain = (blockchain or "").strip().lower()
    if chain in _HEX_ADDRESS_CHAINS or value.startswith("0x"):
        return value.lower()
    return value


def native_asset_identity(blockchain: str) -> CanonicalAssetIdentity:
    """Return canonical identity for a chain's native asset when known."""
    asset = _NATIVE_ASSETS.get((blockchain or "").strip().lower())
    if asset is None:
        return CanonicalAssetIdentity()
    return CanonicalAssetIdentity(
        canonical_asset_id=asset.canonical_asset_id,
        canonical_symbol=asset.canonical_symbol,
        canonical_name=asset.canonical_name,
        identity_status="verified",
        variant_kind=asset.variant_kind,
    )


def resolve_canonical_asset_identity(
    *,
    blockchain: str,
    asset_address: Optional[str] = None,
    symbol: Optional[str] = None,
    name: Optional[str] = None,
    token_standard: Optional[str] = None,
    is_native: bool = False,
) -> CanonicalAssetIdentity:
    """Resolve canonical asset identity for a token or native asset.

    The result is intentionally conservative:
    - exact contract / mint matches are ``verified``
    - symbol or name heuristics are ``heuristic``
    - wrapped / bridged variants keep their own variant kind even when the
      underlying canonical asset is known
    """
    chain = (blockchain or "").strip().lower()
    normalized_address = normalize_asset_address(chain, asset_address)
    raw_symbol = (symbol or "").strip()
    normalized_symbol = raw_symbol.upper()
    name_text = (name or "").strip()
    lower_name = name_text.lower()
    lower_standard = (token_standard or "").strip().lower()

    if is_native:
        return native_asset_identity(chain)

    if normalized_address is not None:
        exact = _VERIFIED_ASSET_ADDRESSES.get((chain, normalized_address))
        if exact is not None:
            return CanonicalAssetIdentity(
                canonical_asset_id=exact.canonical_asset_id,
                canonical_symbol=exact.canonical_symbol,
                canonical_name=exact.canonical_name,
                identity_status="verified",
                variant_kind=exact.variant_kind,
            )

    alias_key = normalized_symbol
    variant_kind = "canonical"

    if alias_key.endswith(".E"):
        alias_key = alias_key[:-2]
        variant_kind = "bridged"
    elif alias_key.startswith("AXL") and alias_key[3:] in _SYMBOL_ALIASES:
        alias_key = alias_key[3:]
        variant_kind = "bridged"
    elif lower_name and any(marker in lower_name for marker in _BRIDGED_MARKERS):
        variant_kind = "bridged"
    elif lower_name.startswith("wrapped "):
        variant_kind = "wrapped"

    alias = _SYMBOL_ALIASES.get(alias_key)
    if alias is None and lower_standard == "native":
        return native_asset_identity(chain)
    if alias is None:
        return CanonicalAssetIdentity()

    resolved_variant = alias.variant_kind
    if variant_kind != "canonical":
        resolved_variant = variant_kind

    return CanonicalAssetIdentity(
        canonical_asset_id=alias.canonical_asset_id,
        canonical_symbol=alias.canonical_symbol,
        canonical_name=alias.canonical_name,
        identity_status="heuristic",
        variant_kind=resolved_variant,
    )


def build_asset_selector_key(
    *,
    blockchain: str,
    asset_address: Optional[str],
    symbol: Optional[str],
    canonical_asset_id: Optional[str],
    identity_status: str,
    variant_kind: str,
    is_native: bool = False,
) -> str:
    """Return the stable frontend/backend selector key for an asset item."""
    chain = (blockchain or "").strip().lower()
    normalized_address = normalize_asset_address(chain, asset_address)
    normalized_symbol = (symbol or "").strip().lower()
    normalized_canonical = (canonical_asset_id or "").strip().lower()

    if is_native:
        return f"native:{chain}"

    if (
        normalized_address
        and (
            identity_status != "verified"
            or variant_kind not in {"canonical", "native"}
            or not normalized_canonical
        )
    ):
        return f"asset:{chain}:{normalized_address}"

    if normalized_canonical:
        return f"canonical:{normalized_canonical}"
    if normalized_address:
        return f"asset:{chain}:{normalized_address}"
    if normalized_symbol:
        return f"symbol:{normalized_symbol}"
    return f"chain:{chain}"
