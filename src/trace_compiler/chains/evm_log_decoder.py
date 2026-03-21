"""EVM DEX event log decoder for swap_event enrichment.

Decodes Uniswap V2, V3, and V4 Swap events from raw log data stored in
``raw_evm_logs``.  The decoded amounts provide ground-truth swap sizes,
replacing the token-transfer-leg inference used when logs are unavailable.

Supported event signatures (keccak256 of the ABI signature):

- Uniswap V2 Swap:
  ``0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822``
  Emitted by every Uniswap V2 pair on each swap. Non-indexed data encodes
  (amount0In, amount1In, amount0Out, amount1Out) as four uint256 values.

- Uniswap V3 Swap:
  ``0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67``
  Emitted by each Uniswap V3 pool. Non-indexed data encodes
  (amount0, amount1, sqrtPriceX96, liquidity, tick) where amount0/amount1
  are signed int256 values (positive = pool receives, negative = pool sends).

- Uniswap V4 Swap:
  ``0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83``
  Emitted on the PoolManager's Swap hook. Data: (amount0, amount1,
  sqrtPriceX96, liquidity, tick, fee). Structure similar to V3.

All decoded values are normalised to ``float`` using the standard ERC-20
decimal divisor (``10**decimals``).  Since we don't store token decimals in
raw_evm_logs, we use ``1e18`` as the default for unknown tokens and rely on
the token-transfer-based ``amount_normalized`` when decimals are uncertain.

For compliance investigations the direction and relative amounts matter more
than exact decimal-precision amounts, so the 1e18 fallback is acceptable.
Token-specific corrections can be applied when ``raw_token_transfers`` data
is available to cross-reference.
"""

from __future__ import annotations

import logging
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event signature constants (keccak256 of ABI signature string)
# ---------------------------------------------------------------------------

#: Uniswap V2 Pair — ``Swap(address indexed sender, uint amount0In,
#: uint amount1In, uint amount0Out, uint amount1Out, address indexed to)``
UNISWAP_V2_SWAP_SIG = (
    "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
)

#: Uniswap V3 Pool — ``Swap(address indexed sender, address indexed recipient,
#: int256 amount0, int256 amount1, uint160 sqrtPriceX96, uint128 liquidity,
#: int24 tick)``
UNISWAP_V3_SWAP_SIG = (
    "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
)

#: Uniswap V4 PoolManager — ``Swap(PoolId indexed id, address indexed sender,
#: int128 amount0, int128 amount1, uint160 sqrtPriceX96, uint128 liquidity,
#: int24 tick, uint24 fee)``
UNISWAP_V4_SWAP_SIG = (
    "0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83"
)

#: Set of all known DEX Swap event signatures.
DEX_SWAP_SIGS = frozenset({
    UNISWAP_V2_SWAP_SIG,
    UNISWAP_V3_SWAP_SIG,
    UNISWAP_V4_SWAP_SIG,
})

# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------

_I256_SIGN = 1 << 255


def _u256(data: bytes, offset: int) -> int:
    """Read a big-endian uint256 from 32 bytes at ``offset``."""
    return int.from_bytes(data[offset : offset + 32], "big")


def _i256(data: bytes, offset: int) -> int:
    """Read a big-endian int256 from 32 bytes at ``offset`` (two's complement)."""
    raw = int.from_bytes(data[offset : offset + 32], "big")
    if raw >= _I256_SIGN:
        raw -= 1 << 256
    return raw


def _hex_to_bytes(hex_str: str) -> bytes:
    """Decode a hex string (with or without 0x prefix) to bytes."""
    if hex_str.startswith("0x") or hex_str.startswith("0X"):
        hex_str = hex_str[2:]
    return bytes.fromhex(hex_str)


def decode_v2_swap(data_hex: str) -> Optional[Dict[str, Any]]:
    """Decode Uniswap V2 Swap event non-indexed data.

    The data field encodes four uint256 values in order:
    amount0In, amount1In, amount0Out, amount1Out.

    Args:
        data_hex: Hex-encoded non-indexed log data (with or without 0x prefix).

    Returns:
        Dict with keys ``amount0In``, ``amount1In``, ``amount0Out``,
        ``amount1Out`` (all int), or None on decode failure.
    """
    try:
        data = _hex_to_bytes(data_hex)
        if len(data) < 128:
            return None
        return {
            "amount0In":  _u256(data, 0),
            "amount1In":  _u256(data, 32),
            "amount0Out": _u256(data, 64),
            "amount1Out": _u256(data, 96),
        }
    except Exception as exc:
        logger.debug("decode_v2_swap failed: %s", exc)
        return None


def decode_v3_swap(data_hex: str) -> Optional[Dict[str, Any]]:
    """Decode Uniswap V3 Swap event non-indexed data.

    The data field encodes (in order):
    amount0 (int256), amount1 (int256), sqrtPriceX96 (uint160),
    liquidity (uint128), tick (int24).

    Positive amounts indicate the pool receives that token (user sends);
    negative amounts indicate the pool sends that token (user receives).

    Args:
        data_hex: Hex-encoded non-indexed log data.

    Returns:
        Dict with keys ``amount0``, ``amount1`` (signed int), plus
        ``token0_is_input`` (bool: True when token0 is the input token),
        or None on decode failure.
    """
    try:
        data = _hex_to_bytes(data_hex)
        if len(data) < 160:
            return None
        amount0 = _i256(data, 0)
        amount1 = _i256(data, 32)
        return {
            "amount0": amount0,
            "amount1": amount1,
            # token0 is input when pool receives token0 (positive amount0)
            "token0_is_input": amount0 > 0,
        }
    except Exception as exc:
        logger.debug("decode_v3_swap failed: %s", exc)
        return None


def decode_v4_swap(data_hex: str) -> Optional[Dict[str, Any]]:
    """Decode Uniswap V4 Swap event non-indexed data.

    The data field encodes (in order):
    amount0 (int128), amount1 (int128), sqrtPriceX96 (uint160),
    liquidity (uint128), tick (int24), fee (uint24).

    Sign convention identical to V3.

    Args:
        data_hex: Hex-encoded non-indexed log data.

    Returns:
        Dict with keys ``amount0``, ``amount1``, ``fee`` (uint24),
        ``token0_is_input``, or None on decode failure.
    """
    try:
        data = _hex_to_bytes(data_hex)
        if len(data) < 192:
            return None
        # int128 — stored in 32 bytes but only lower 16 bytes are meaningful
        # (ABI encoding always pads to 32 bytes)
        def _i128(d: bytes, off: int) -> int:
            # Extract only the lower 16 bytes (128 bits) that contain the actual value
            # ABI pads to 32 bytes, but int128 only uses the lower 16 bytes
            raw_bytes = d[off + 16 : off + 32]  # skip upper 16 padding bytes
            raw = int.from_bytes(raw_bytes, "big")
            if raw >= (1 << 127):
                raw -= 1 << 128
            return raw

        amount0 = _i128(data, 0)
        amount1 = _i128(data, 32)
        fee = _u256(data, 160) & 0xFFFFFF
        return {
            "amount0": amount0,
            "amount1": amount1,
            "fee": fee,
            "token0_is_input": amount0 > 0,
        }
    except Exception as exc:
        logger.debug("decode_v4_swap failed: %s", exc)
        return None


def decode_swap_log(event_sig: str, data_hex: str) -> Optional[Dict[str, Any]]:
    """Dispatch to the correct decoder based on event signature.

    Args:
        event_sig: topics[0] (keccak256 of ABI event signature).
        data_hex:  Hex-encoded non-indexed log data.

    Returns:
        Decoded dict, or None when the signature is not recognised or
        decoding fails.
    """
    sig = event_sig.lower()
    if sig == UNISWAP_V2_SWAP_SIG:
        return decode_v2_swap(data_hex)
    if sig == UNISWAP_V3_SWAP_SIG:
        return decode_v3_swap(data_hex)
    if sig == UNISWAP_V4_SWAP_SIG:
        return decode_v4_swap(data_hex)
    return None


def extract_swap_amounts(
    decoded: Dict[str, Any],
    event_sig: str,
    *,
    decimals0: int = 18,
    decimals1: int = 18,
) -> Optional[Tuple[float, float, bool]]:
    """Extract (input_amount, output_amount, token0_is_input) from decoded log.

    Normalises raw integer amounts using the provided decimal counts.  When
    decimals are unknown, 18 is used as a conservative default.

    Args:
        decoded:         Output of ``decode_swap_log()``.
        event_sig:       Event signature, used to determine decode format.
        decimals0:       Decimal places for token0.
        decimals1:       Decimal places for token1.

    Returns:
        Tuple ``(input_amount, output_amount, token0_is_input)`` or None when
        amounts cannot be determined.
    """
    sig = event_sig.lower()
    try:
        if sig == UNISWAP_V2_SWAP_SIG:
            # One of (amount0In, amount0Out) or (amount1In, amount1Out) must be
            # zero; the non-zero pair identifies the input and output legs.
            a0_in = decoded["amount0In"]
            a1_in = decoded["amount1In"]
            a0_out = decoded["amount0Out"]
            a1_out = decoded["amount1Out"]
            if a0_in > 0 and a1_out > 0:
                # token0 → token1
                return (
                    a0_in / (10 ** decimals0),
                    a1_out / (10 ** decimals1),
                    True,
                )
            if a1_in > 0 and a0_out > 0:
                # token1 → token0
                return (
                    a1_in / (10 ** decimals1),
                    a0_out / (10 ** decimals0),
                    False,
                )

        elif sig in (UNISWAP_V3_SWAP_SIG, UNISWAP_V4_SWAP_SIG):
            amount0 = decoded["amount0"]
            amount1 = decoded["amount1"]
            token0_is_input = decoded.get("token0_is_input", amount0 > 0)
            if token0_is_input:
                # Pool receives token0 (user sends token0), pool sends token1
                return (
                    abs(amount0) / (10 ** decimals0),
                    abs(amount1) / (10 ** decimals1),
                    True,
                )
            else:
                # Pool receives token1 (user sends token1), pool sends token0
                return (
                    abs(amount1) / (10 ** decimals1),
                    abs(amount0) / (10 ** decimals0),
                    False,
                )
    except (KeyError, ZeroDivisionError) as exc:
        logger.debug("extract_swap_amounts failed: %s", exc)
    return None
