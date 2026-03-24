"""Unit tests for the EVM DEX event log decoder.

Covers:
- decode_v2_swap: happy paths (both directions), short/invalid hex
- decode_v3_swap: signed int256 decoding, direction flag, short data
- decode_v4_swap: signed int128 ABI-encoded decoding, direction flag, short data
- decode_balancer_v2_swap: amountIn/amountOut decoding, short data
- decode_curve_token_exchange: sold_id/tokens_sold/bought_id/tokens_bought, short data
- decode_swap_log: dispatcher for V2/V3/V4/Balancer/Curve and unknown signature
- extract_swap_amounts: V2 token0→token1, V2 token1→token0, V2 ambiguous,
  V3 token0-is-input, V3 token1-is-input, Balancer V2, Curve TokenExchange
- DEX_SWAP_SIGS frozenset membership
"""

from __future__ import annotations

import struct

import pytest

from src.trace_compiler.chains.evm_log_decoder import (
    BALANCER_V2_SWAP_SIG,
    CURVE_TOKEN_EXCHANGE_SIG,
    CURVE_TOKEN_EXCHANGE_UNDERLYING_SIG,
    DEX_SWAP_SIGS,
    SOLIDLY_SWAP_SIG,
    UNISWAP_V2_SWAP_SIG,
    UNISWAP_V3_SWAP_SIG,
    UNISWAP_V4_SWAP_SIG,
    decode_balancer_v2_swap,
    decode_curve_token_exchange,
    decode_solidly_swap,
    decode_swap_log,
    decode_v2_swap,
    decode_v3_swap,
    decode_v4_swap,
    extract_swap_amounts,
)


# ---------------------------------------------------------------------------
# Byte builders
# ---------------------------------------------------------------------------


def _v2_data(a0in: int, a1in: int, a0out: int, a1out: int) -> str:
    """Encode four uint256 values as a V2 Swap event data hex string."""
    return "0x" + (
        a0in.to_bytes(32, "big")
        + a1in.to_bytes(32, "big")
        + a0out.to_bytes(32, "big")
        + a1out.to_bytes(32, "big")
    ).hex()


def _encode_i256(n: int) -> bytes:
    """Encode a signed integer as a 32-byte big-endian two's complement value."""
    if n >= 0:
        return n.to_bytes(32, "big")
    return (n & ((1 << 256) - 1)).to_bytes(32, "big")


def _v3_data(amount0: int, amount1: int) -> str:
    """Encode a V3 Swap event data hex string (160 bytes)."""
    return "0x" + (
        _encode_i256(amount0)   # int256 amount0
        + _encode_i256(amount1) # int256 amount1
        + b"\x00" * 32          # sqrtPriceX96
        + b"\x00" * 32          # liquidity
        + b"\x00" * 32          # tick
    ).hex()


def _encode_i128_abi(n: int) -> bytes:
    """Encode a signed int128 into 32 bytes matching the EVM ABI encoding.

    The EVM ABI sign-extends all integer types to 32 bytes (256 bits).
    For negative int128 values the upper 16 bytes are 0xFF (sign extension),
    not 0x00 (zero-padding).

    The V4 decoder uses ``int.from_bytes(slot, "big", signed=True)`` which
    correctly decodes this sign-extended representation.
    """
    return n.to_bytes(32, "big", signed=True)


def _v4_data(amount0: int, amount1: int, fee: int = 0) -> str:
    """Encode a V4 Swap event data hex string (192 bytes)."""
    return "0x" + (
        _encode_i128_abi(amount0)  # int128 amount0 (ABI-padded to 32 bytes)
        + _encode_i128_abi(amount1) # int128 amount1 (ABI-padded to 32 bytes)
        + b"\x00" * 32              # sqrtPriceX96
        + b"\x00" * 32              # liquidity
        + b"\x00" * 32              # tick
        + fee.to_bytes(32, "big")   # fee (uint24, ABI-padded to 32 bytes)
    ).hex()


# ---------------------------------------------------------------------------
# decode_v2_swap tests
# ---------------------------------------------------------------------------


class TestDecodeV2Swap:
    """Tests for decode_v2_swap."""

    def test_token0_to_token1_amounts(self):
        """Token0→Token1 swap encodes correctly (a0in non-zero, a1out non-zero)."""
        data = _v2_data(int(1e18), 0, 0, int(2000e6))
        result = decode_v2_swap(data)
        assert result is not None
        assert result["amount0In"] == int(1e18)
        assert result["amount1In"] == 0
        assert result["amount0Out"] == 0
        assert result["amount1Out"] == int(2000e6)

    def test_token1_to_token0_amounts(self):
        """Token1→Token0 swap encodes correctly (a1in non-zero, a0out non-zero)."""
        data = _v2_data(0, int(1000e6), int(0.5e18), 0)
        result = decode_v2_swap(data)
        assert result is not None
        assert result["amount0In"] == 0
        assert result["amount1In"] == int(1000e6)
        assert result["amount0Out"] == int(0.5e18)
        assert result["amount1Out"] == 0

    def test_returns_all_four_keys(self):
        """Result dict always contains all four expected keys."""
        result = decode_v2_swap(_v2_data(1, 0, 0, 2))
        assert result is not None
        assert set(result.keys()) == {"amount0In", "amount1In", "amount0Out", "amount1Out"}

    def test_empty_hex_returns_none(self):
        """Empty string → None (no crash)."""
        assert decode_v2_swap("") is None

    def test_too_short_hex_returns_none(self):
        """Hex string shorter than 128 bytes of data → None."""
        short = "0x" + "ab" * 64  # only 64 bytes, need 128
        assert decode_v2_swap(short) is None

    def test_invalid_hex_returns_none(self):
        """Non-hex garbage string → None (no crash)."""
        assert decode_v2_swap("not_valid_hex!!!") is None

    def test_no_0x_prefix_accepted(self):
        """Hex string without 0x prefix is also accepted."""
        data = _v2_data(100, 0, 0, 200)
        data_no_prefix = data[2:]  # strip '0x'
        result = decode_v2_swap(data_no_prefix)
        assert result is not None
        assert result["amount0In"] == 100

    def test_large_uint256_values(self):
        """Decoding works correctly for very large uint256 amounts."""
        big = (1 << 128) - 1
        data = _v2_data(big, 0, 0, big)
        result = decode_v2_swap(data)
        assert result is not None
        assert result["amount0In"] == big


# ---------------------------------------------------------------------------
# decode_v3_swap tests
# ---------------------------------------------------------------------------


class TestDecodeV3Swap:
    """Tests for decode_v3_swap."""

    def test_token0_is_input_when_amount0_positive(self):
        """Positive amount0 → token0_is_input==True."""
        data = _v3_data(int(1e18), -int(2000e6))
        result = decode_v3_swap(data)
        assert result is not None
        assert result["token0_is_input"] is True

    def test_amount0_value_decoded(self):
        """amount0 decoded correctly (positive, user sends token0)."""
        data = _v3_data(int(1e18), -int(2000e6))
        result = decode_v3_swap(data)
        assert result["amount0"] == int(1e18)

    def test_amount1_negative_decoded(self):
        """amount1 decoded correctly (negative, user receives token1)."""
        data = _v3_data(int(1e18), -int(2000e6))
        result = decode_v3_swap(data)
        assert result["amount1"] == -int(2000e6)

    def test_token1_is_input_when_amount0_negative(self):
        """Negative amount0 → token0_is_input==False (token1 is the input)."""
        data = _v3_data(-int(0.5e18), int(1000e6))
        result = decode_v3_swap(data)
        assert result is not None
        assert result["token0_is_input"] is False

    def test_token1_input_amounts_decoded(self):
        """Both signed amounts decoded correctly for token1-is-input direction."""
        data = _v3_data(-int(0.5e18), int(1000e6))
        result = decode_v3_swap(data)
        assert result["amount0"] == -int(0.5e18)
        assert result["amount1"] == int(1000e6)

    def test_too_short_returns_none(self):
        """Data shorter than 160 bytes → None."""
        short = "0x" + "00" * 128  # only 128 bytes, need 160
        assert decode_v3_swap(short) is None

    def test_empty_hex_returns_none(self):
        """Empty string → None."""
        assert decode_v3_swap("") is None

    def test_returns_expected_keys(self):
        """Result dict contains amount0, amount1, and token0_is_input."""
        result = decode_v3_swap(_v3_data(100, -200))
        assert result is not None
        assert "amount0" in result
        assert "amount1" in result
        assert "token0_is_input" in result


# ---------------------------------------------------------------------------
# decode_v4_swap tests
# ---------------------------------------------------------------------------


class TestDecodeV4Swap:
    """Tests for decode_v4_swap."""

    def test_basic_happy_path_token0_is_input(self):
        """Positive amount0 → token0_is_input==True."""
        data = _v4_data(100, -200)
        result = decode_v4_swap(data)
        assert result is not None
        assert result["token0_is_input"] is True
        assert result["amount0"] == 100
        assert result["amount1"] == -200

    def test_token1_is_input(self):
        """Negative amount0 → token0_is_input==False."""
        data = _v4_data(-300, 400)
        result = decode_v4_swap(data)
        assert result is not None
        assert result["token0_is_input"] is False

    def test_fee_decoded(self):
        """fee field is decoded correctly from the data."""
        data = _v4_data(100, -200, fee=3000)
        result = decode_v4_swap(data)
        assert result is not None
        assert result["fee"] == 3000

    def test_too_short_returns_none(self):
        """Data shorter than 192 bytes → None."""
        short = "0x" + "00" * 160  # only 160 bytes, need 192
        assert decode_v4_swap(short) is None

    def test_empty_hex_returns_none(self):
        """Empty string → None."""
        assert decode_v4_swap("") is None

    def test_returns_expected_keys(self):
        """Result dict contains amount0, amount1, token0_is_input, fee."""
        result = decode_v4_swap(_v4_data(50, -60))
        assert result is not None
        assert "amount0" in result
        assert "amount1" in result
        assert "token0_is_input" in result
        assert "fee" in result


# ---------------------------------------------------------------------------
# decode_swap_log dispatcher tests
# ---------------------------------------------------------------------------


class TestDecodeSwapLog:
    """Tests for the decode_swap_log dispatcher."""

    def test_v2_sig_dispatches_to_v2(self):
        """V2 event signature dispatches to decode_v2_swap."""
        data = _v2_data(int(1e18), 0, 0, int(2000e6))
        result = decode_swap_log(UNISWAP_V2_SWAP_SIG, data)
        assert result is not None
        # V2 result has these keys
        assert "amount0In" in result

    def test_v3_sig_dispatches_to_v3(self):
        """V3 event signature dispatches to decode_v3_swap."""
        data = _v3_data(int(1e18), -int(2000e6))
        result = decode_swap_log(UNISWAP_V3_SWAP_SIG, data)
        assert result is not None
        assert "amount0" in result
        assert result["token0_is_input"] is True

    def test_v4_sig_dispatches_to_v4(self):
        """V4 event signature dispatches to decode_v4_swap."""
        data = _v4_data(100, -200)
        result = decode_swap_log(UNISWAP_V4_SWAP_SIG, data)
        assert result is not None
        assert "fee" in result

    def test_unknown_sig_returns_none(self):
        """Unknown event signature → None (no crash)."""
        unknown_sig = "0x" + "ab" * 32
        data = _v2_data(1, 0, 0, 2)
        result = decode_swap_log(unknown_sig, data)
        assert result is None

    def test_sig_case_insensitive(self):
        """Signature comparison is case-insensitive (upper-cased sig still matches)."""
        data = _v2_data(int(1e18), 0, 0, int(2000e6))
        result = decode_swap_log(UNISWAP_V2_SWAP_SIG.upper(), data)
        assert result is not None


# ---------------------------------------------------------------------------
# extract_swap_amounts tests
# ---------------------------------------------------------------------------


class TestExtractSwapAmounts:
    """Tests for extract_swap_amounts."""

    # --- V2 path ---

    def test_v2_token0_to_token1(self):
        """V2 token0→token1: returns (input, output, True) normalised by decimals."""
        data = _v2_data(int(1e18), 0, 0, int(2000e6))
        decoded = decode_v2_swap(data)
        result = extract_swap_amounts(decoded, UNISWAP_V2_SWAP_SIG, decimals0=18, decimals1=6)
        assert result is not None
        inp, out, token0_is_input = result
        assert token0_is_input is True
        assert abs(inp - 1.0) < 1e-9
        assert abs(out - 2000.0) < 1e-6

    def test_v2_token1_to_token0(self):
        """V2 token1→token0: returns (input, output, False) normalised by decimals."""
        data = _v2_data(0, int(1000e6), int(0.5e18), 0)
        decoded = decode_v2_swap(data)
        result = extract_swap_amounts(decoded, UNISWAP_V2_SWAP_SIG, decimals0=18, decimals1=6)
        assert result is not None
        inp, out, token0_is_input = result
        assert token0_is_input is False
        assert abs(inp - 1000.0) < 1e-6
        assert abs(out - 0.5) < 1e-9

    def test_v2_both_in_zero_returns_none(self):
        """V2 where both in amounts are 0 → None (direction ambiguous)."""
        data = _v2_data(0, 0, int(1e18), int(2000e6))
        decoded = decode_v2_swap(data)
        result = extract_swap_amounts(decoded, UNISWAP_V2_SWAP_SIG, decimals0=18, decimals1=6)
        assert result is None

    # --- V3 path ---

    def test_v3_token0_is_input(self):
        """V3 token0-is-input: input from |amount0|, output from |amount1|."""
        data = _v3_data(int(1e18), -int(2000e6))
        decoded = decode_v3_swap(data)
        result = extract_swap_amounts(decoded, UNISWAP_V3_SWAP_SIG, decimals0=18, decimals1=6)
        assert result is not None
        inp, out, token0_is_input = result
        assert token0_is_input is True
        assert abs(inp - 1.0) < 1e-9
        assert abs(out - 2000.0) < 1e-6

    def test_v3_token1_is_input(self):
        """V3 token1-is-input: input from |amount1|, output from |amount0|."""
        data = _v3_data(-int(0.5e18), int(1000e6))
        decoded = decode_v3_swap(data)
        result = extract_swap_amounts(decoded, UNISWAP_V3_SWAP_SIG, decimals0=18, decimals1=6)
        assert result is not None
        inp, out, token0_is_input = result
        assert token0_is_input is False
        assert abs(inp - 1000.0) < 1e-6
        assert abs(out - 0.5) < 1e-9

    def test_v3_uses_v4_sig_correctly(self):
        """V4 signature also routes through the V3/V4 branch of extract_swap_amounts."""
        data = _v4_data(int(1e18), -int(2000e6))
        decoded = decode_v4_swap(data)
        result = extract_swap_amounts(decoded, UNISWAP_V4_SWAP_SIG, decimals0=18, decimals1=6)
        assert result is not None
        inp, out, token0_is_input = result
        assert token0_is_input is True

    def test_unknown_sig_returns_none(self):
        """Unknown event signature → None from extract_swap_amounts."""
        data = _v2_data(int(1e18), 0, 0, int(2000e6))
        decoded = decode_v2_swap(data)
        result = extract_swap_amounts(decoded, "0x" + "cc" * 32)
        assert result is None


# ---------------------------------------------------------------------------
# Balancer V2 Vault Swap decoder tests
# ---------------------------------------------------------------------------


def _balancer_data(amount_in: int, amount_out: int) -> str:
    """Encode Balancer V2 non-indexed data: amountIn (uint256) | amountOut (uint256)."""
    return "0x" + amount_in.to_bytes(32, "big").hex() + amount_out.to_bytes(32, "big").hex()


class TestDecodeBalancerV2Swap:
    """Tests for decode_balancer_v2_swap()."""

    def test_happy_path(self):
        """Correctly decodes amountIn and amountOut."""
        data = _balancer_data(int(1000e18), int(995e18))
        result = decode_balancer_v2_swap(data)
        assert result is not None
        assert result["amount_in"] == int(1000e18)
        assert result["amount_out"] == int(995e18)

    def test_without_0x_prefix(self):
        """Accepts data without 0x prefix."""
        raw = _balancer_data(int(500e6), int(499e18))[2:]  # strip 0x
        result = decode_balancer_v2_swap(raw)
        assert result is not None
        assert result["amount_in"] == int(500e6)

    def test_short_data_returns_none(self):
        """Less than 64 bytes → None."""
        assert decode_balancer_v2_swap("0x" + "aa" * 32) is None

    def test_empty_data_returns_none(self):
        """Empty string → None."""
        assert decode_balancer_v2_swap("") is None


class TestExtractSwapAmountsBalancer:
    """Tests for extract_swap_amounts with Balancer V2 events."""

    def test_balancer_amounts(self):
        """Returns (amount_in, amount_out, True) for Balancer V2."""
        data = _balancer_data(int(1000e18), int(995e18))
        decoded = decode_balancer_v2_swap(data)
        result = extract_swap_amounts(decoded, BALANCER_V2_SWAP_SIG)
        assert result is not None
        inp, out, token0_is_input = result
        assert abs(inp - 1000.0) < 1e-6
        assert abs(out - 995.0) < 1e-6
        assert token0_is_input is True

    def test_balancer_stablecoin_decimals(self):
        """Correctly normalises USDC (6 decimals) amountIn."""
        data = _balancer_data(int(500e6), int(499e18))
        decoded = decode_balancer_v2_swap(data)
        result = extract_swap_amounts(decoded, BALANCER_V2_SWAP_SIG, decimals0=6, decimals1=18)
        assert result is not None
        inp, out, _ = result
        assert abs(inp - 500.0) < 1e-3
        assert abs(out - 499.0) < 1e-9


# ---------------------------------------------------------------------------
# Curve TokenExchange / TokenExchangeUnderlying decoder tests
# ---------------------------------------------------------------------------


def _curve_data(sold_id: int, tokens_sold: int, bought_id: int, tokens_bought: int) -> str:
    """Encode Curve non-indexed data: sold_id | tokens_sold | bought_id | tokens_bought."""

    def _i128_slot(v: int) -> bytes:
        if v < 0:
            v = v + (1 << 128)
        return v.to_bytes(16, "big").rjust(32, b"\x00")

    return "0x" + (
        _i128_slot(sold_id)
        + tokens_sold.to_bytes(32, "big")
        + _i128_slot(bought_id)
        + tokens_bought.to_bytes(32, "big")
    ).hex()


class TestDecodeCurveTokenExchange:
    """Tests for decode_curve_token_exchange()."""

    def test_happy_path(self):
        """Correctly decodes sold_id, tokens_sold, bought_id, tokens_bought."""
        data = _curve_data(0, int(500e6), 1, int(499e18))
        result = decode_curve_token_exchange(data)
        assert result is not None
        assert result["sold_id"] == 0
        assert result["tokens_sold"] == int(500e6)
        assert result["bought_id"] == 1
        assert result["tokens_bought"] == int(499e18)

    def test_short_data_returns_none(self):
        """Less than 128 bytes → None."""
        assert decode_curve_token_exchange("0x" + "aa" * 64) is None

    def test_large_coin_index(self):
        """Coin index 2 for a 3-pool is decoded correctly."""
        data = _curve_data(2, int(1e18), 0, int(999e6))
        result = decode_curve_token_exchange(data)
        assert result is not None
        assert result["sold_id"] == 2
        assert result["bought_id"] == 0


class TestExtractSwapAmountsCurve:
    """Tests for extract_swap_amounts with Curve events."""

    def test_curve_token_exchange(self):
        """Returns (tokens_sold, tokens_bought, True) for Curve TokenExchange."""
        data = _curve_data(0, int(500e6), 1, int(499e18))
        decoded = decode_curve_token_exchange(data)
        result = extract_swap_amounts(decoded, CURVE_TOKEN_EXCHANGE_SIG, decimals0=6, decimals1=18)
        assert result is not None
        inp, out, token0_is_input = result
        assert abs(inp - 500.0) < 1e-3
        assert abs(out - 499.0) < 1e-9
        assert token0_is_input is True

    def test_curve_token_exchange_underlying(self):
        """Same decode for TokenExchangeUnderlying."""
        data = _curve_data(0, int(100e18), 1, int(99e18))
        decoded = decode_curve_token_exchange(data)
        result = extract_swap_amounts(decoded, CURVE_TOKEN_EXCHANGE_UNDERLYING_SIG)
        assert result is not None
        inp, out, _ = result
        assert abs(inp - 100.0) < 1e-9
        assert abs(out - 99.0) < 1e-9


class TestDecodeSolidlySwap:
    """Tests for decode_solidly_swap() — shares data layout with Uniswap V2."""

    def test_token0_to_token1(self):
        """amount0In > 0, amount1Out > 0 → standard forward swap."""
        data = _v2_data(int(1e18), 0, 0, int(2000e6))
        result = decode_solidly_swap(data)
        assert result is not None
        assert result["amount0In"] == int(1e18)
        assert result["amount1Out"] == int(2000e6)
        assert result["amount1In"] == 0
        assert result["amount0Out"] == 0

    def test_token1_to_token0(self):
        """amount1In > 0, amount0Out > 0 → reverse swap direction."""
        data = _v2_data(0, int(2000e6), int(1e18), 0)
        result = decode_solidly_swap(data)
        assert result is not None
        assert result["amount1In"] == int(2000e6)
        assert result["amount0Out"] == int(1e18)

    def test_short_data_returns_none(self):
        """Less than 128 bytes → None."""
        assert decode_solidly_swap("0x" + "aa" * 64) is None


class TestExtractSwapAmountsSolidly:
    """Tests for extract_swap_amounts with Solidly events."""

    def test_solidly_token0_to_token1(self):
        """Returns amounts and token0_is_input=True for solidly forward swap."""
        data = _v2_data(int(1e18), 0, 0, int(2000e6))
        decoded = decode_solidly_swap(data)
        result = extract_swap_amounts(decoded, SOLIDLY_SWAP_SIG, decimals0=18, decimals1=6)
        assert result is not None
        inp, out, token0_is_input = result
        assert abs(inp - 1.0) < 1e-9
        assert abs(out - 2000.0) < 1e-3
        assert token0_is_input is True

    def test_solidly_token1_to_token0(self):
        """Returns token0_is_input=False for reverse direction."""
        data = _v2_data(0, int(2000e6), int(1e18), 0)
        decoded = decode_solidly_swap(data)
        result = extract_swap_amounts(decoded, SOLIDLY_SWAP_SIG, decimals0=18, decimals1=6)
        assert result is not None
        inp, out, token0_is_input = result
        assert abs(inp - 2000.0) < 1e-3
        assert abs(out - 1.0) < 1e-9
        assert token0_is_input is False


class TestDecodeSwapLogDispatcher:
    """Tests that decode_swap_log dispatches to the correct decoder."""

    def test_balancer_v2_dispatched(self):
        """BALANCER_V2_SWAP_SIG routes to decode_balancer_v2_swap."""
        data = _balancer_data(int(1e18), int(1e18))
        result = decode_swap_log(BALANCER_V2_SWAP_SIG, data)
        assert result is not None
        assert "amount_in" in result

    def test_curve_token_exchange_dispatched(self):
        """CURVE_TOKEN_EXCHANGE_SIG routes to decode_curve_token_exchange."""
        data = _curve_data(0, int(1e18), 1, int(1e18))
        result = decode_swap_log(CURVE_TOKEN_EXCHANGE_SIG, data)
        assert result is not None
        assert "tokens_sold" in result

    def test_curve_underlying_dispatched(self):
        """CURVE_TOKEN_EXCHANGE_UNDERLYING_SIG also routes to decode_curve_token_exchange."""
        data = _curve_data(0, int(1e18), 1, int(1e18))
        result = decode_swap_log(CURVE_TOKEN_EXCHANGE_UNDERLYING_SIG, data)
        assert result is not None
        assert "tokens_bought" in result

    def test_solidly_dispatched(self):
        """SOLIDLY_SWAP_SIG routes to decode_solidly_swap (V2 layout)."""
        data = _v2_data(int(1e18), 0, 0, int(2000e6))
        result = decode_swap_log(SOLIDLY_SWAP_SIG, data)
        assert result is not None
        assert "amount0In" in result


# ---------------------------------------------------------------------------
# DEX_SWAP_SIGS frozenset membership tests
# ---------------------------------------------------------------------------


class TestDexSwapSigs:
    """Tests for the DEX_SWAP_SIGS frozenset."""

    def test_v2_sig_in_frozenset(self):
        """UNISWAP_V2_SWAP_SIG is a member of DEX_SWAP_SIGS."""
        assert UNISWAP_V2_SWAP_SIG in DEX_SWAP_SIGS

    def test_v3_sig_in_frozenset(self):
        """UNISWAP_V3_SWAP_SIG is a member of DEX_SWAP_SIGS."""
        assert UNISWAP_V3_SWAP_SIG in DEX_SWAP_SIGS

    def test_v4_sig_in_frozenset(self):
        """UNISWAP_V4_SWAP_SIG is a member of DEX_SWAP_SIGS."""
        assert UNISWAP_V4_SWAP_SIG in DEX_SWAP_SIGS

    def test_unknown_sig_not_in_frozenset(self):
        """An arbitrary unknown signature is not a member of DEX_SWAP_SIGS."""
        unknown = "0x" + "deadbeef" * 8
        assert unknown not in DEX_SWAP_SIGS

    def test_balancer_v2_sig_in_frozenset(self):
        """BALANCER_V2_SWAP_SIG is a member of DEX_SWAP_SIGS."""
        assert BALANCER_V2_SWAP_SIG in DEX_SWAP_SIGS

    def test_curve_token_exchange_sig_in_frozenset(self):
        """CURVE_TOKEN_EXCHANGE_SIG is a member of DEX_SWAP_SIGS."""
        assert CURVE_TOKEN_EXCHANGE_SIG in DEX_SWAP_SIGS

    def test_curve_token_exchange_underlying_sig_in_frozenset(self):
        """CURVE_TOKEN_EXCHANGE_UNDERLYING_SIG is a member of DEX_SWAP_SIGS."""
        assert CURVE_TOKEN_EXCHANGE_UNDERLYING_SIG in DEX_SWAP_SIGS

    def test_solidly_sig_in_frozenset(self):
        """SOLIDLY_SWAP_SIG is a member of DEX_SWAP_SIGS."""
        assert SOLIDLY_SWAP_SIG in DEX_SWAP_SIGS

    def test_frozenset_has_exactly_seven_members(self):
        """DEX_SWAP_SIGS contains exactly the seven known sigs."""
        assert len(DEX_SWAP_SIGS) == 7
