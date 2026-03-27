from src.services.canonical_assets import build_asset_selector_key
from src.services.canonical_assets import native_asset_identity
from src.services.canonical_assets import resolve_canonical_asset_identity


def test_resolve_canonical_asset_identity_verifies_ethereum_usdt():
    identity = resolve_canonical_asset_identity(
        blockchain="ethereum",
        asset_address="0xdAC17F958D2ee523a2206206994597C13D831ec7",
        symbol="USDT",
        token_standard="erc20",
    )

    assert identity.canonical_asset_id == "tether"
    assert identity.canonical_symbol == "USDT"
    assert identity.identity_status == "verified"
    assert identity.variant_kind == "canonical"


def test_resolve_canonical_asset_identity_marks_bsc_usdt_as_bridged():
    identity = resolve_canonical_asset_identity(
        blockchain="bsc",
        asset_address="0x55d398326f99059fF775485246999027B3197955",
        symbol="USDT",
        token_standard="bep20",
    )

    assert identity.canonical_asset_id == "tether"
    assert identity.identity_status == "verified"
    assert identity.variant_kind == "bridged"


def test_resolve_canonical_asset_identity_verifies_solana_usdc_mint():
    identity = resolve_canonical_asset_identity(
        blockchain="solana",
        asset_address="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        symbol="USDC",
        token_standard="spl",
    )

    assert identity.canonical_asset_id == "usd-coin"
    assert identity.canonical_symbol == "USDC"
    assert identity.identity_status == "verified"
    assert identity.variant_kind == "canonical"


def test_resolve_canonical_asset_identity_uses_heuristics_for_unknown_usdt_contract():
    identity = resolve_canonical_asset_identity(
        blockchain="ethereum",
        asset_address="0xfake00000000000000000000000000000000beef",
        symbol="USDT",
        token_standard="erc20",
    )

    assert identity.canonical_asset_id == "tether"
    assert identity.identity_status == "heuristic"
    assert identity.variant_kind == "canonical"


def test_build_asset_selector_key_keeps_wrapped_and_unverified_assets_separate():
    wrapped_key = build_asset_selector_key(
        blockchain="ethereum",
        asset_address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        symbol="WETH",
        canonical_asset_id="ethereum",
        identity_status="verified",
        variant_kind="wrapped",
    )
    heuristic_key = build_asset_selector_key(
        blockchain="ethereum",
        asset_address="0xfake00000000000000000000000000000000beef",
        symbol="USDT",
        canonical_asset_id="tether",
        identity_status="heuristic",
        variant_kind="canonical",
    )
    native_key = build_asset_selector_key(
        blockchain="ethereum",
        asset_address=None,
        symbol="ETH",
        canonical_asset_id=native_asset_identity("ethereum").canonical_asset_id,
        identity_status="verified",
        variant_kind="native",
        is_native=True,
    )

    assert wrapped_key == "asset:ethereum:0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
    assert heuristic_key == "asset:ethereum:0xfake00000000000000000000000000000000beef"
    assert native_key == "native:ethereum"
