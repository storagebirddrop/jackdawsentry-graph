"""Shared helpers for chain-accurate asset selection and option labels."""

from __future__ import annotations

from typing import Iterable
from typing import Optional

from src.trace_compiler.models import AssetOption
from src.trace_compiler.models import AssetSelector
from src.trace_compiler.models import AssetSelectorMode
from src.trace_compiler.models import ExpandOptions

_EVM_LIKE_CHAINS = {
    "ethereum",
    "bsc",
    "polygon",
    "arbitrum",
    "base",
    "avalanche",
    "optimism",
    "starknet",
    "injective",
}

_NATIVE_SYMBOLS = {
    "bitcoin": "BTC",
    "solana": "SOL",
    "ethereum": "ETH",
    "bsc": "BNB",
    "polygon": "MATIC",
    "arbitrum": "ETH",
    "base": "ETH",
    "avalanche": "AVAX",
    "optimism": "ETH",
    "tron": "TRX",
}


def native_symbol_for_chain(chain: str) -> Optional[str]:
    return _NATIVE_SYMBOLS.get((chain or "").strip().lower())


def normalize_chain_asset_id(chain: str, chain_asset_id: Optional[str]) -> Optional[str]:
    value = (chain_asset_id or "").strip()
    if not value:
        return None
    normalized_chain = (chain or "").strip().lower()
    if normalized_chain in _EVM_LIKE_CHAINS or value.startswith("0x"):
        return value.lower()
    return value


def normalize_asset_selector(
    selector: Optional[AssetSelector],
    *,
    chain: Optional[str] = None,
) -> Optional[AssetSelector]:
    if selector is None:
        return None

    normalized_chain = ((chain or selector.chain) or "").strip().lower()
    if not normalized_chain:
        return None

    asset_symbol = (selector.asset_symbol or "").strip() or None
    canonical_asset_id = (selector.canonical_asset_id or "").strip().lower() or None
    normalized = AssetSelector(
        mode=selector.mode,
        chain=normalized_chain,
        chain_asset_id=normalize_chain_asset_id(normalized_chain, selector.chain_asset_id),
        asset_symbol=asset_symbol,
        canonical_asset_id=canonical_asset_id,
    )
    if normalized.mode == "native" and normalized.asset_symbol is None:
        normalized.asset_symbol = native_symbol_for_chain(normalized_chain)
    return normalized


def normalize_legacy_asset_filter(asset_filter: Iterable[str]) -> list[str]:
    return sorted(
        {
            asset.strip()
            for asset in asset_filter
            if isinstance(asset, str) and asset.strip()
        }
    )


def _selector_from_single_legacy_filter(value: str, *, chain: str) -> AssetSelector:
    normalized_chain = (chain or "").strip().lower()
    native_symbol = native_symbol_for_chain(normalized_chain)
    lowered = value.lower()

    if lowered.startswith("native:"):
        selector_chain = value.split(":", 1)[1].strip().lower()
        if selector_chain == normalized_chain:
            return AssetSelector(
                mode="native",
                chain=normalized_chain,
                asset_symbol=native_symbol,
            )
        return AssetSelector(mode="all", chain=normalized_chain, asset_symbol=native_symbol)

    if lowered.startswith("canonical:"):
        return AssetSelector(
            mode="asset",
            chain=normalized_chain,
            canonical_asset_id=value.split(":", 1)[1].strip().lower() or None,
        )

    if lowered.startswith("asset:"):
        parts = value.split(":", 2)
        if len(parts) == 3 and parts[1].strip().lower() == normalized_chain:
            return AssetSelector(
                mode="asset",
                chain=normalized_chain,
                chain_asset_id=normalize_chain_asset_id(normalized_chain, parts[2].strip()),
            )
        return AssetSelector(mode="all", chain=normalized_chain, asset_symbol=native_symbol)

    if lowered.startswith("symbol:"):
        return AssetSelector(
            mode="asset",
            chain=normalized_chain,
            asset_symbol=value.split(":", 1)[1].strip() or None,
        )

    return AssetSelector(mode="asset", chain=normalized_chain, asset_symbol=value)


def _asset_selector_identity(selector: AssetSelector) -> tuple[str, ...]:
    """Return the dedupe/sort identity for a normalized selector."""
    chain = (selector.chain or "").strip().lower()

    if selector.mode == "native":
        return ("native", chain)

    if selector.mode == "asset":
        chain_asset_id = normalize_chain_asset_id(chain, selector.chain_asset_id)
        if chain_asset_id:
            return ("asset", chain, "chain_asset_id", chain_asset_id)

        canonical_asset_id = (selector.canonical_asset_id or "").strip().lower()
        if canonical_asset_id:
            return ("asset", chain, "canonical_asset_id", canonical_asset_id)

        asset_symbol = (selector.asset_symbol or "").strip().upper()
        if asset_symbol:
            return ("asset", chain, "asset_symbol", asset_symbol)

        return ("asset", chain, "empty")

    return ("all", chain)


def _asset_selector_sort_key(selector: AssetSelector) -> tuple[str, ...]:
    return (
        *_asset_selector_identity(selector),
        (selector.asset_symbol or "").strip().upper(),
        (selector.asset_symbol or "").strip(),
        selector.canonical_asset_id or "",
        selector.chain_asset_id or "",
    )


def normalize_asset_selectors(
    selectors: Iterable[AssetSelector],
    *,
    chain: Optional[str] = None,
) -> list[AssetSelector]:
    """Normalize, dedupe, and deterministically sort plural asset selectors.

    The empty list represents unfiltered/all-assets expansion.  A single
    ``mode='all'`` selector collapses the whole request to that unfiltered state.
    """
    normalized: list[AssetSelector] = []
    for selector in selectors:
        normalized_selector = normalize_asset_selector(selector, chain=chain)
        if normalized_selector is None:
            continue
        if normalized_selector.mode == "all":
            return []
        normalized.append(normalized_selector)

    deduped: list[AssetSelector] = []
    seen: set[tuple[str, ...]] = set()
    for selector in sorted(normalized, key=_asset_selector_sort_key):
        identity = _asset_selector_identity(selector)
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(selector)
    return deduped


def effective_asset_selectors(options: ExpandOptions, *, chain: str) -> list[AssetSelector]:
    """Return the normalized, deduplicated list of active asset selectors.

    Semantics:
    - Empty list  → no filter (all assets).
    - Non-empty   → return only rows matching any selector in the list.
    - If any selector has ``mode='all'``, the whole list collapses to [] (no filter).

    When ``options.asset_selectors`` is non-empty it is used as the source of
    truth.  When it is empty the function falls back to legacy behaviour so that
    callers that still build ``ExpandOptions(asset_filter=[...])`` without the
    new field continue to work unchanged.
    """
    if options.asset_selectors:
        return normalize_asset_selectors(options.asset_selectors, chain=chain)

    selector = normalize_asset_selector(options.asset_selector, chain=chain)
    if selector is not None:
        return normalize_asset_selectors([selector], chain=chain)

    legacy_filters = normalize_legacy_asset_filter(options.asset_filter)
    if not legacy_filters:
        return []

    native_symbol = native_symbol_for_chain(chain)
    upper_filters = {asset.upper() for asset in legacy_filters}
    if native_symbol and upper_filters == {native_symbol.upper()}:
        return normalize_asset_selectors(
            [AssetSelector(mode="native", chain=chain, asset_symbol=native_symbol)],
            chain=chain,
        )

    if len(legacy_filters) == 1:
        return normalize_asset_selectors(
            [_selector_from_single_legacy_filter(legacy_filters[0], chain=chain)],
            chain=chain,
        )

    return []


def effective_asset_selector(options: ExpandOptions, *, chain: str) -> AssetSelector:
    selectors = effective_asset_selectors(options, chain=chain)
    if len(selectors) == 1:
        return selectors[0]

    native_symbol = native_symbol_for_chain(chain)
    return AssetSelector(mode="all", chain=chain, asset_symbol=native_symbol)


def selector_is_specific_asset(selector: Optional[AssetSelector]) -> bool:
    return selector is not None and selector.mode == "asset"


def selector_is_native_only(selector: Optional[AssetSelector]) -> bool:
    return selector is not None and selector.mode == "native"


def selector_requires_event_store_only(options: ExpandOptions, *, chain: str) -> bool:
    if options.tx_hashes:
        return True
    selectors = effective_asset_selectors(options, chain=chain)
    return bool(selectors) and any(s.mode == "asset" for s in selectors)


def shorten_chain_asset_id(
    chain_asset_id: Optional[str],
    *,
    head: int = 10,
    tail: int = 6,
) -> str:
    value = (chain_asset_id or "").strip()
    if not value or len(value) <= head + tail + 3:
        return value
    return f"{value[:head]}...{value[-tail:]}"


def format_asset_option_label(option: AssetOption) -> str:
    if option.mode == "all":
        return "All assets"
    if option.mode == "native":
        native_symbol = option.asset_symbol or native_symbol_for_chain(option.chain) or option.chain.upper()
        return f"Native {native_symbol}"
    if option.asset_symbol and option.chain_asset_id:
        return f"{option.asset_symbol} · {shorten_chain_asset_id(option.chain_asset_id)}"
    if option.asset_symbol:
        return option.asset_symbol
    if option.chain_asset_id:
        return f"Asset · {shorten_chain_asset_id(option.chain_asset_id)}"
    return "Asset"


def _asset_option_identity(option: AssetOption) -> tuple[str, ...]:
    chain = (option.chain or "").strip().lower()

    if option.mode in {"all", "native"}:
        return (option.mode, chain)

    normalized_chain_asset_id = normalize_chain_asset_id(chain, option.chain_asset_id)
    if normalized_chain_asset_id:
        return (option.mode, chain, "chain_asset_id", normalized_chain_asset_id)

    canonical_asset_id = (option.canonical_asset_id or "").strip().lower()
    if canonical_asset_id:
        return (option.mode, chain, "canonical_asset_id", canonical_asset_id)

    asset_symbol = (option.asset_symbol or "").strip().upper()
    return (option.mode, chain, "asset_symbol", asset_symbol)


def dedupe_asset_options(options: Iterable[AssetOption]) -> list[AssetOption]:
    """Return asset options with stable first-seen ordering and normalized dedup.

    The frontend selector contract should not surface duplicate choices even if
    upstream rows vary only by casing or repeated joins. Preserve the first
    occurrence so chain compilers keep their newest-first ordering.
    """

    deduped: list[AssetOption] = []
    seen: set[tuple[str, ...]] = set()
    for option in options:
        identity = _asset_option_identity(option)
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(option)
    return deduped


def build_asset_option(
    *,
    mode: AssetSelectorMode,
    chain: str,
    asset_symbol: Optional[str] = None,
    chain_asset_id: Optional[str] = None,
    canonical_asset_id: Optional[str] = None,
) -> AssetOption:
    option = AssetOption(
        mode=mode,
        chain=(chain or "").strip().lower(),
        asset_symbol=(asset_symbol or "").strip() or None,
        chain_asset_id=normalize_chain_asset_id(chain, chain_asset_id),
        canonical_asset_id=(canonical_asset_id or "").strip().lower() or None,
        display_label="",
    )
    if option.mode == "native" and option.asset_symbol is None:
        option.asset_symbol = native_symbol_for_chain(option.chain)
    option.display_label = format_asset_option_label(option)
    return option
