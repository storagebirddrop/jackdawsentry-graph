"""Solana instruction data decoder for cross-chain bridge destination extraction.

Some Solana bridge programs encode the destination address directly inside the
instruction payload (after the 8-byte Anchor discriminator).  This module
extracts those addresses using heuristic pattern scanning — no IDL required.

Two patterns are recognised:

1. **Padded EVM address**: 12 zero bytes immediately followed by 20 non-zero
   bytes (ABI/EVM-style ``bytes32`` padding).  Used by Wormhole and similar
   bridges when the destination is an EVM chain address.

2. **Tron address**: byte ``0x41`` immediately followed by 20 bytes.  The
   full base58check-encoded Tron address is reconstructed from the 21 raw
   bytes plus a double-SHA256 checksum.

Note: Bridges that store the destination in an on-chain account (e.g.
Allbridge Core, which uses an ephemeral ``swapAndBridgeData`` PDA) cannot be
decoded from instruction bytes alone.  For those protocols the BridgeTracer
API-based resolver is the appropriate path.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Anchor programs prefix every instruction with an 8-byte discriminator
# (sha256("global:<instruction_name>")[:8]).
_ANCHOR_DISCRIMINATOR_LEN = 8

# Minimal inline base58 implementation (Solana/Bitcoin alphabet).
# Avoids the external ``base58`` library dependency.
_B58_ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_MAP: dict[int, int] = {c: i for i, c in enumerate(_B58_ALPHABET)}


def b58decode(s: str) -> bytes:
    """Decode a base58-encoded string to bytes (Solana / Bitcoin alphabet).

    The Solana JSON-RPC ``jsonParsed`` encoding returns raw instruction data
    as base58 strings.  This function decodes them back to raw bytes.

    Args:
        s: Base58-encoded string.

    Returns:
        Decoded bytes.
    """
    n = 0
    for c in s.encode():
        n = n * 58 + _B58_MAP[c]
    result: list[int] = []
    while n:
        result.append(n & 0xFF)
        n >>= 8
    pad = len(s) - len(s.lstrip("1"))
    return bytes(pad) + bytes(reversed(result))


def decode_solana_bridge_destination(
    instruction_data: bytes,
    program_id: str,
) -> Optional["CrossChainDestination"]:
    """Extract a cross-chain destination address from Solana instruction data.

    Skips the 8-byte Anchor discriminator, then scans the remaining payload
    for padded EVM addresses (12 zero bytes + 20 non-zero bytes) and Tron
    addresses (byte 0x41 + 20 bytes).

    Works for any bridge that encodes the recipient address inline in the
    instruction payload.  Returns ``None`` for bridges that store the
    destination in a separate on-chain account (ephemeral PDAs).

    Args:
        instruction_data: Raw instruction bytes (already decoded from base58).
        program_id:       Solana program address — used only for logging.

    Returns:
        ``CrossChainDestination`` if a destination address is found, else None.
    """
    from src.trace_compiler.calldata.decoder import CrossChainDestination  # avoid circular

    if not instruction_data or len(instruction_data) < _ANCHOR_DISCRIMINATOR_LEN + 20:
        return None

    # Skip the 8-byte Anchor discriminator prefix.
    payload = instruction_data[_ANCHOR_DISCRIMINATOR_LEN:]

    dest = _scan_evm_address(payload, CrossChainDestination)
    if dest:
        logger.info(
            "solana_decoder: EVM destination %s found in program %s",
            dest.destination_address,
            program_id[:16],
        )
        return dest

    dest = _scan_tron_address(payload, CrossChainDestination)
    if dest:
        logger.info(
            "solana_decoder: Tron destination %s found in program %s",
            dest.destination_address,
            program_id[:16],
        )
        return dest

    return None


def _scan_evm_address(payload: bytes, CrossChainDestination: type) -> Optional[object]:
    """Scan payload for ABI-padded EVM addresses (12 zero bytes + 20 non-zero bytes).

    Bridges that target EVM chains often store the recipient as a 32-byte
    ``bytes32`` value padded with 12 leading zero bytes — matching the ABI
    encoding for an Ethereum address.  This pattern is searched at every
    byte offset (not just 32-byte boundaries, since Borsh does not align
    fields like ABI does).

    Args:
        payload:              Instruction bytes after the 8-byte discriminator.
        CrossChainDestination: Imported dataclass (passed to avoid circular import).

    Returns:
        ``CrossChainDestination`` if a match is found, else ``None``.
    """
    for i in range(len(payload) - 31):
        window = payload[i: i + 32]
        if window[:12] != b"\x00" * 12:
            continue
        addr_bytes = window[12:]
        if not any(addr_bytes):
            continue
        evm_addr = "0x" + addr_bytes.hex()
        return CrossChainDestination(
            destination_address=evm_addr,
            destination_chain=None,    # any EVM chain — refined from context
            source_function="solana_heuristic",
            parameter_name="recipient",
            confidence=0.75,
        )
    return None


def _scan_tron_address(payload: bytes, CrossChainDestination: type) -> Optional[object]:
    """Scan payload for a Tron address encoded as byte 0x41 + 20 bytes.

    Tron's mainnet address prefix is 0x41.  When a Solana bridge targets Tron,
    the recipient is stored as 21 raw bytes.  The full base58check address is
    reconstructed by appending a 4-byte double-SHA256 checksum and encoding
    the result in base58.

    Args:
        payload:              Instruction bytes after the 8-byte discriminator.
        CrossChainDestination: Imported dataclass (passed to avoid circular import).

    Returns:
        ``CrossChainDestination`` if a valid Tron address is found, else ``None``.
    """
    for i in range(len(payload) - 20):
        if payload[i] != 0x41:
            continue
        raw21 = payload[i: i + 21]
        checksum = hashlib.sha256(hashlib.sha256(raw21).digest()).digest()[:4]
        encoded = raw21 + checksum
        # Base58-encode the 25-byte result.
        n = int.from_bytes(encoded, "big")
        chars: list[bytes] = []
        while n:
            n, r = divmod(n, 58)
            chars.append(_B58_ALPHABET[r: r + 1])
        pad = len(encoded) - len(encoded.lstrip(b"\x00"))
        tron_addr = (b"1" * pad + b"".join(reversed(chars))).decode()
        if tron_addr.startswith("T") and len(tron_addr) == 34:
            return CrossChainDestination(
                destination_address=tron_addr,
                destination_chain="tron",
                source_function="solana_heuristic",
                parameter_name="recipient",
                confidence=0.75,
            )
    return None
