"""Unit tests for the EVM DEX event log decoder.

Covers:
- decode_v2_swap: happy paths (both directions), short/invalid hex
- decode_v3_swap: signed int256 decoding, direction flag, short data
- decode_v4_swap: signed int128 ABI-encoded decoding, direction flag, short data
- decode_swap_log: dispatcher for V2/V3/V4 and unknown signature
- extract_swap_amounts: V2 token0→token1, V2 token1→token0, V2 ambiguous,
  V3 token0-is-input, V3 token1-is-input
- DEX_SWAP_SIGS frozenset membership
"""

from __future__ import annotations

import struct

import pytest

from src.trace_compiler.chains.evm_log_decoder import (
    DEX_SWAP_SIGS,
    UNISWAP_V2_SWAP_SIG,
    UNISWAP_V3_SWAP_SIG,
    UNISWAP_V4_SWAP_SIG,
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
    """Encode a signed int128 into 32 bytes matching the V4 decoder's expectation.

    The V4 decoder (_i128 inner function) reads 32 bytes as a big-endian
    unsigned integer and applies: if raw >= (1<<127): raw -= (1<<128).
    This is equivalent to taking the 128-bit two's complement value and
    zero-padding it to 32 bytes (upper 16 bytes are zero, lower 16 bytes
    hold the int128 two's complement).
    """
    # Represent n as 128-bit two's complement, then zero-pad to 32 bytes.
    val = n & ((1 << 128) - 1)
    return val.to_bytes(32, "big")


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

    def test_frozenset_has_exactly_three_members(self):
        """DEX_SWAP_SIGS contains exactly the three known sigs."""
        assert len(DEX_SWAP_SIGS) == 3
