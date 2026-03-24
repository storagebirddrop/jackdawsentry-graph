"""EVM DEX event log decoder for swap_event enrichment.

Decodes Uniswap V2/V3/V4, Balancer V2, and Curve Swap events from raw log
data stored in ``raw_evm_logs``.  The decoded amounts provide ground-truth
swap sizes, replacing the token-transfer-leg inference used when logs are
unavailable.

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

- Balancer V2 Vault Swap:
  ``0x2170c741c41531aec20e7c107c24eecfdd15e69c9bb0a8dd37b1840b9e0b207b``
  Emitted by the Balancer V2 Vault for every single-hop swap. Indexed
  topics carry poolId, tokenIn, tokenOut; non-indexed data encodes
  (amountIn, amountOut) as two uint256 values.

- Curve TokenExchange:
  ``0x8b3e96f2b889fa771c53c981b40daf005f63f637f1869f707052d15a3dd97140``
  Emitted by Curve plain and factory pools on token exchanges. Non-indexed
  data encodes (sold_id int128, tokens_sold uint256, bought_id int128,
  tokens_bought uint256). sold_id/bought_id are pool-internal coin indices.

- Curve TokenExchangeUnderlying:
  ``0xd013ca23e77a65003c2c659c5442c00c805371b7fc1ebd4c206c41d1536bd90b``
  Emitted by Curve meta-pools on underlying-token exchanges. Same data
  layout as TokenExchange (sold_id, tokens_sold, bought_id, tokens_bought).

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

#: Balancer V2 Vault — ``Swap(bytes32 indexed poolId,
#: address indexed tokenIn, address indexed tokenOut,
#: uint256 amountIn, uint256 amountOut)``
#: All swaps routed through the single Vault contract.
BALANCER_V2_SWAP_SIG = (
    "0x2170c741c41531aec20e7c107c24eecfdd15e69c9bb0a8dd37b1840b9e0b207b"
)

#: Curve plain/factory pool — ``TokenExchange(address indexed buyer,
#: int128 sold_id, uint256 tokens_sold, int128 bought_id,
#: uint256 tokens_bought)``
#: sold_id / bought_id are the pool's internal coin indices (0, 1, 2, …).
CURVE_TOKEN_EXCHANGE_SIG = (
    "0x8b3e96f2b889fa771c53c981b40daf005f63f637f1869f707052d15a3dd97140"
)

#: Curve meta-pool — ``TokenExchangeUnderlying(address indexed buyer,
#: int128 sold_id, uint256 tokens_sold, int128 bought_id,
#: uint256 tokens_bought)``
#: Same data layout as TokenExchange; underlying coin indices may differ
#: from the base-pool coin indices.
CURVE_TOKEN_EXCHANGE_UNDERLYING_SIG = (
    "0xd013ca23e77a65003c2c659c5442c00c805371b7fc1ebd4c206c41d1536bd90b"
)

#: Frozenset of all known DEX Swap event signatures used to filter
#: ``raw_evm_logs`` during collection and query.  Keep in sync with
#: ``EthereumCollector._build_relevant_log_sigs()``.
DEX_SWAP_SIGS = frozenset({
    UNISWAP_V2_SWAP_SIG,
    UNISWAP_V3_SWAP_SIG,
    UNISWAP_V4_SWAP_SIG,
    BALANCER_V2_SWAP_SIG,
    CURVE_TOKEN_EXCHANGE_SIG,
    CURVE_TOKEN_EXCHANGE_UNDERLYING_SIG,
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


def decode_balancer_v2_swap(data_hex: str) -> Optional[Dict[str, Any]]:
    """Decode Balancer V2 Vault Swap event non-indexed data.

    The non-indexed data field encodes two uint256 values:
    amountIn, amountOut.

    Note: tokenIn and tokenOut addresses are in indexed topics[2] and
    topics[3], not in the data field.  The caller must retrieve them from
    the ``raw_evm_logs.topic2`` / ``topic3`` columns when token identity
    is required.

    Args:
        data_hex: Hex-encoded non-indexed log data (with or without 0x prefix).

    Returns:
        Dict with keys ``amount_in``, ``amount_out`` (both int), or None on
        decode failure.
    """
    try:
        data = _hex_to_bytes(data_hex)
        if len(data) < 64:
            return None
        return {
            "amount_in":  _u256(data, 0),
            "amount_out": _u256(data, 32),
        }
    except Exception as exc:
        logger.debug("decode_balancer_v2_swap failed: %s", exc)
        return None


def decode_curve_token_exchange(data_hex: str) -> Optional[Dict[str, Any]]:
    """Decode Curve TokenExchange / TokenExchangeUnderlying non-indexed data.

    Both events share the same non-indexed data layout:
    sold_id (int128), tokens_sold (uint256), bought_id (int128),
    tokens_bought (uint256).

    ``sold_id`` and ``bought_id`` are the pool's internal coin indices
    (0, 1, 2, …).  Callers must consult the pool contract or a coin-index
    registry to resolve the actual token addresses.

    Args:
        data_hex: Hex-encoded non-indexed log data (with or without 0x prefix).

    Returns:
        Dict with keys ``sold_id`` (int), ``tokens_sold`` (int),
        ``bought_id`` (int), ``tokens_bought`` (int), or None on decode
        failure.
    """
    try:
        data = _hex_to_bytes(data_hex)
        if len(data) < 128:
            return None
        # int128 is ABI-encoded as a 32-byte slot; read as int256 then clip.
        # sold_id / bought_id are always small non-negative indices in practice,
        # but we honour the signed type to match the ABI exactly.
        def _i128_from_slot(d: bytes, off: int) -> int:
            # Lower 16 bytes hold the value; upper 16 are sign-extension padding.
            raw_bytes = d[off + 16 : off + 32]
            raw = int.from_bytes(raw_bytes, "big")
            if raw >= (1 << 127):
                raw -= 1 << 128
            return raw

        return {
            "sold_id":      _i128_from_slot(data, 0),
            "tokens_sold":  _u256(data, 32),
            "bought_id":    _i128_from_slot(data, 64),
            "tokens_bought": _u256(data, 96),
        }
    except Exception as exc:
        logger.debug("decode_curve_token_exchange failed: %s", exc)
        return None


def decode_swap_log(event_sig: str, data_hex: str) -> Optional[Dict[str, Any]]:
    """Dispatch to the correct decoder based on event signature.

    Args:
        event_sig: topics[0] (keccak256 of ABI event signature) - already lowercase.
        data_hex:  Hex-encoded non-indexed log data.

    Returns:
        Decoded dict, or None when the signature is not recognised or
        decoding fails.
    """
    # Normalize to lowercase for consistency (keccak256 hashes are lowercase)
    sig = event_sig.lower()
    if sig == UNISWAP_V2_SWAP_SIG:
        return decode_v2_swap(data_hex)
    if sig == UNISWAP_V3_SWAP_SIG:
        return decode_v3_swap(data_hex)
    if sig == UNISWAP_V4_SWAP_SIG:
        return decode_v4_swap(data_hex)
    if sig == BALANCER_V2_SWAP_SIG:
        return decode_balancer_v2_swap(data_hex)
    if sig in (CURVE_TOKEN_EXCHANGE_SIG, CURVE_TOKEN_EXCHANGE_UNDERLYING_SIG):
        return decode_curve_token_exchange(data_hex)
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
    # Normalize to lowercase for consistency (keccak256 hashes are lowercase)
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

        elif sig == BALANCER_V2_SWAP_SIG:
            # amountIn/amountOut are unambiguous — the Vault event names them
            # explicitly unlike Uniswap's pool-relative amount0/amount1.
            # We define tokenIn as "token0" by convention so the return type
            # stays consistent.
            return (
                decoded["amount_in"] / (10 ** decimals0),
                decoded["amount_out"] / (10 ** decimals1),
                True,
            )

        elif sig in (CURVE_TOKEN_EXCHANGE_SIG, CURVE_TOKEN_EXCHANGE_UNDERLYING_SIG):
            # tokens_sold is always the input; tokens_bought is always the
            # output.  sold_id / bought_id are coin indices, not in amounts.
            return (
                decoded["tokens_sold"] / (10 ** decimals0),
                decoded["tokens_bought"] / (10 ** decimals1),
                True,
            )

    except (KeyError, ZeroDivisionError) as exc:
        logger.debug("extract_swap_amounts failed: %s", exc)
    return None
