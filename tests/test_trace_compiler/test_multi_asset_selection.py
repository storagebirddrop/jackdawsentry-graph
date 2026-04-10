"""Backend contract tests for multi-asset selector normalization."""

import pytest
from pydantic import ValidationError

from src.trace_compiler.asset_selection import effective_asset_selectors
from src.trace_compiler.asset_selection import normalize_asset_selectors
from src.trace_compiler.compiler import _expansion_cache_key
from src.trace_compiler.models import AssetSelector
from src.trace_compiler.models import ExpandOptions
from src.trace_compiler.models import ExpandRequest


def _selector(
    *,
    mode: str = "asset",
    chain: str = "ethereum",
    chain_asset_id: str | None = None,
    asset_symbol: str | None = None,
    canonical_asset_id: str | None = None,
) -> AssetSelector:
    return AssetSelector(
        mode=mode,
        chain=chain,
        chain_asset_id=chain_asset_id,
        asset_symbol=asset_symbol,
        canonical_asset_id=canonical_asset_id,
    )


def _expand_request(options: ExpandOptions) -> ExpandRequest:
    return ExpandRequest(
        operation_type="expand_next",
        seed_node_id="ethereum:address:0xseed",
        options=options,
    )


def test_singular_asset_selector_coerces_to_plural_list():
    selector = _selector(chain_asset_id="0xabc", asset_symbol="USDC")

    options = ExpandOptions(asset_selector=selector)

    assert options.asset_selector == selector
    assert options.asset_selectors == [selector]


def test_rejects_singular_and_plural_asset_selectors_together():
    selector = _selector(chain_asset_id="0xabc", asset_symbol="USDC")

    with pytest.raises(ValidationError, match="mutually exclusive"):
        ExpandOptions(asset_selector=selector, asset_selectors=[])


def test_empty_selectors_normalize_to_unfiltered():
    assert effective_asset_selectors(ExpandOptions(), chain="ethereum") == []


def test_all_selector_collapses_to_unfiltered():
    options = ExpandOptions(
        asset_selectors=[
            _selector(mode="all", chain="ethereum"),
            _selector(chain_asset_id="0xabc", asset_symbol="USDC"),
        ],
    )

    assert effective_asset_selectors(options, chain="ethereum") == []


def test_legacy_multi_value_asset_filter_collapses_to_unfiltered():
    options = ExpandOptions(asset_filter=["USDC", "ETH"])

    assert effective_asset_selectors(options, chain="ethereum") == []


def test_normalize_asset_selectors_dedupes_by_strongest_identity():
    selectors = normalize_asset_selectors(
        [
            _selector(chain_asset_id="0xABC", asset_symbol="usdc"),
            _selector(chain_asset_id="0xabc", asset_symbol="USDC"),
        ],
        chain="ethereum",
    )

    assert len(selectors) == 1
    assert selectors[0].chain_asset_id == "0xabc"
    assert selectors[0].asset_symbol == "USDC"


def test_normalize_asset_selectors_sorts_deterministically():
    input_a = [
        _selector(chain_asset_id="0xbbb", asset_symbol="BBB"),
        _selector(mode="native", chain="ethereum"),
        _selector(chain_asset_id="0xaaa", asset_symbol="AAA"),
    ]
    input_b = list(reversed(input_a))

    normalized_a = normalize_asset_selectors(input_a, chain="ethereum")
    normalized_b = normalize_asset_selectors(input_b, chain="ethereum")

    assert normalized_a == normalized_b
    assert [(item.mode, item.chain_asset_id) for item in normalized_a] == [
        ("asset", "0xaaa"),
        ("asset", "0xbbb"),
        ("native", None),
    ]


def test_cache_key_is_order_independent_for_asset_selectors():
    selector_a = _selector(chain_asset_id="0xaaa", asset_symbol="AAA")
    selector_b = _selector(chain_asset_id="0xbbb", asset_symbol="BBB")

    request_a = _expand_request(
        ExpandOptions(asset_selectors=[selector_a, selector_b]),
    )
    request_b = _expand_request(
        ExpandOptions(asset_selectors=[selector_b, selector_a]),
    )

    assert _expansion_cache_key("session-a", request_a) == _expansion_cache_key(
        "session-a",
        request_b,
    )


def test_cache_key_treats_singular_and_equivalent_plural_as_identical():
    selector = _selector(chain_asset_id="0xaaa", asset_symbol="AAA")

    singular_request = _expand_request(ExpandOptions(asset_selector=selector))
    plural_request = _expand_request(ExpandOptions(asset_selectors=[selector]))

    assert _expansion_cache_key(
        "session-a",
        singular_request,
    ) == _expansion_cache_key("session-a", plural_request)


def test_cache_key_treats_all_selector_and_empty_selector_as_unfiltered():
    empty_request = _expand_request(ExpandOptions())
    all_request = _expand_request(
        ExpandOptions(asset_selectors=[_selector(mode="all", chain="ethereum")]),
    )

    assert _expansion_cache_key("session-a", empty_request) == _expansion_cache_key(
        "session-a",
        all_request,
    )
