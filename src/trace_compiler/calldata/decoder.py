"""EVM calldata decoder for cross-chain bridge destination extraction.

Bridges encode the destination chain address inside the transaction calldata.
Rather than hardcoding contract-address → destination mappings, this module
decodes the calldata directly:

1. Fetch the verified ABI from Etherscan (cached in Redis, 7-day TTL).
2. Decode the calldata using ``eth_abi`` with the matched function signature.
3. Walk the decoded parameters looking for cross-chain address strings —
   values that look like a Tron (T…), Solana (base58), Bitcoin (1/3/bc1…),
   or another EVM address.

If no ABI is available (unverified contract), fall back to heuristic scanning:
scan the raw calldata for 32-byte address slots and string/bytes parameters
that match known cross-chain address patterns.

The result is an optional ``CrossChainDestination`` that the bridge hop
compiler can attach to a pending bridge hop node, turning an opaque custodial
hop into a directly-traversable cross-chain link.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

_ETHERSCAN_API = "https://api.etherscan.io/v2/api"
_ABI_CACHE_TTL = 60 * 60 * 24 * 7   # 7 days
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)

# Map blockchain name → Etherscan chain ID (same as live_fetch)
_CHAIN_IDS: Dict[str, int] = {
    "ethereum": 1,
    "bsc": 56,
    "polygon": 137,
    "arbitrum": 42161,
    "base": 8453,
    "optimism": 10,
    "avalanche": 43114,
}

# Cross-chain address patterns — used to identify destination addresses in
# decoded string parameters regardless of parameter name.
_TRON_PATTERN = re.compile(r'^T[1-9A-HJ-NP-Za-km-z]{33}$')
_SOLANA_PATTERN = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')
_BTC_PATTERN = re.compile(r'^(1|3)[1-9A-HJ-NP-Za-km-z]{25,34}$|^bc1[a-z0-9]{6,87}$')
_EVM_PATTERN = re.compile(r'^0x[0-9a-fA-F]{40}$')

# Map detected destination address to chain name
_DEST_CHAIN_HINTS: List[Tuple[re.Pattern, str]] = [
    (_TRON_PATTERN, "tron"),
    (_BTC_PATTERN, "bitcoin"),
    (_EVM_PATTERN, "ethereum"),  # could be any EVM — caller refines from context
]


@dataclass
class CrossChainDestination:
    """A decoded cross-chain destination extracted from bridge calldata."""

    destination_address: str
    destination_chain: Optional[str]  # best-guess chain name, or None
    source_function: str              # ABI function name or "heuristic"
    parameter_name: Optional[str]     # ABI parameter name if available
    confidence: float                 # 0.0–1.0


async def decode_bridge_destination(
    input_data: bytes,
    contract_address: str,
    chain: str,
    redis_client: Optional[Any] = None,
) -> Optional[CrossChainDestination]:
    """Decode the cross-chain destination address from bridge calldata.

    Tries ABI-based decoding first (using Etherscan-verified ABI), then falls
    back to heuristic pattern scanning of the raw bytes.

    Args:
        input_data:        Raw calldata bytes from the transaction.
        contract_address:  Bridge contract address (for ABI lookup).
        chain:             Source chain name.
        redis_client:      Optional async Redis client for ABI caching.

    Returns:
        ``CrossChainDestination`` if a cross-chain address is found, else None.
    """
    if not input_data or len(input_data) < 4:
        return None

    api_key = os.environ.get("ETHERSCAN_API_KEY", "").strip()
    abi = None

    if api_key and chain in _CHAIN_IDS:
        abi = await _get_contract_abi(
            contract_address.lower(), chain, api_key, redis_client
        )

    if abi:
        result = _decode_with_abi(input_data, abi)
        if result:
            return result

    # ABI unavailable or no cross-chain address found — try heuristics.
    return _decode_heuristic(input_data)


async def _get_contract_abi(
    contract_address: str,
    chain: str,
    api_key: str,
    redis_client: Optional[Any],
) -> Optional[List[Dict]]:
    """Fetch and cache the verified ABI for a contract.

    Args:
        contract_address: Lowercase hex contract address.
        chain:            Blockchain name.
        api_key:          Etherscan v2 API key.
        redis_client:     Async Redis client for caching (optional).

    Returns:
        Parsed ABI list, or None if the contract is not verified.
    """
    cache_key = f"abi:{chain}:{contract_address}"

    if redis_client is not None:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                if isinstance(cached, (bytes, bytearray)):
                    cached = cached.decode('utf-8')
                return json.loads(cached)
        except Exception:
            pass

    chain_id = _CHAIN_IDS[chain]
    try:
        async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
            params = {
                "chainid": chain_id,
                "module": "contract",
                "action": "getabi",
                "address": contract_address,
                "apikey": api_key,
            }
            async with session.get(_ETHERSCAN_API, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                if data.get("status") != "1":
                    return None
                abi = json.loads(data["result"])
    except Exception as exc:
        logger.debug("ABI fetch failed for %s/%s: %s", chain, contract_address, exc)
        return None

    if redis_client is not None:
        try:
            await redis_client.set(cache_key, json.dumps(abi), ex=_ABI_CACHE_TTL)
        except Exception:
            pass

    return abi


def _decode_with_abi(
    input_data: bytes,
    abi: List[Dict],
) -> Optional[CrossChainDestination]:
    """Decode calldata using a verified ABI and find cross-chain destinations.

    Args:
        input_data: Raw calldata bytes (includes 4-byte selector prefix).
        abi:        Parsed contract ABI.

    Returns:
        ``CrossChainDestination`` if a cross-chain address parameter is found.
    """
    try:
        from eth_abi import decode as abi_decode
        from eth_utils import function_abi_to_4byte_selector
    except ImportError:
        logger.debug("eth_abi / eth_utils not available — skipping ABI decode")
        return None

    selector = input_data[:4]
    calldata = input_data[4:]

    for item in abi:
        if item.get("type") != "function":
            continue
        try:
            item_selector = function_abi_to_4byte_selector(item)
        except Exception:
            continue
        if item_selector != selector:
            continue

        # Matched function — decode the parameters.
        inputs = item.get("inputs", [])
        if not inputs:
            return None

        types = [inp["type"] for inp in inputs]
        try:
            decoded = abi_decode(types, calldata)
        except Exception as exc:
            logger.debug("ABI decode failed for %s: %s", item.get("name"), exc)
            return None

        # Walk decoded values looking for cross-chain address strings.
        for param, value in zip(inputs, decoded):
            dest = _extract_cross_chain_addr(value)
            if dest:
                dest_chain = _infer_chain(dest)
                return CrossChainDestination(
                    destination_address=dest,
                    destination_chain=dest_chain,
                    source_function=item.get("name", "unknown"),
                    parameter_name=param.get("name"),
                    confidence=0.95,
                )

        break  # Selector matched — stop searching even if no dest found.

    return None


def _decode_heuristic(input_data: bytes) -> Optional[CrossChainDestination]:
    """Heuristic scan of raw calldata for cross-chain destination addresses.

    ABI-encoded strings are prefixed by a uint256 length followed by the
    UTF-8 content zero-padded to 32-byte boundaries.  This scanner looks for
    string-like payloads that match known cross-chain address formats without
    needing the ABI.

    Args:
        input_data: Raw calldata bytes.

    Returns:
        ``CrossChainDestination`` with lower confidence if a pattern matches.
    """
    # Strategy: scan every aligned 32-byte word for EVM addresses (20-byte
    # right-padded), then scan the whole buffer for string patterns (Tron/BTC/
    # Solana addresses are ASCII and appear as readable substrings).
    raw = input_data[4:]   # skip 4-byte selector

    # 1. Look for printable ASCII substrings that match cross-chain patterns.
    try:
        text = raw.decode("latin-1")
    except Exception:
        text = ""

    # Extract ASCII runs of ≥25 chars (minimum Tron/BTC address length)
    for match in re.finditer(r'[1-9A-HJ-NP-Za-km-z]{25,88}', text):
        candidate = match.group()
        dest_chain = _infer_chain(candidate)
        if dest_chain and dest_chain != "ethereum":  # EVM → EVM covered by normal expansion
            return CrossChainDestination(
                destination_address=candidate,
                destination_chain=dest_chain,
                source_function="heuristic",
                parameter_name=None,
                confidence=0.7,
            )

    # 2. Check 32-byte slots for EVM address padding (12 zero bytes + 20 addr bytes).
    # Store the first padded EVM address found and return it as a low-confidence
    # fallback only when no non-EVM address was found above.
    fallback_addr = None
    for i in range(0, len(raw) - 31, 32):
        word = raw[i:i + 32]
        if word[:12] == b'\x00' * 12 and word[12:] != b'\x00' * 20:
            addr = "0x" + word[12:].hex()
            if addr != "0x" + "00" * 20:
                fallback_addr = addr
                break

    if fallback_addr:
        return CrossChainDestination(
            destination_address=fallback_addr,
            destination_chain="ethereum",
            source_function="heuristic",
            parameter_name=None,
            confidence=0.5,
        )

    return None


def _extract_cross_chain_addr(value: Any) -> Optional[str]:
    """Recursively search a decoded ABI value for a cross-chain address string.

    Handles str, bytes, nested tuples/lists.

    Args:
        value: Decoded parameter value from eth_abi.

    Returns:
        The cross-chain address string if found, else None.
    """
    if isinstance(value, str):
        chain = _infer_chain(value)
        if chain and chain != "ethereum":
            return value
    elif isinstance(value, bytes):
        try:
            s = value.decode("utf-8").strip("\x00")
            chain = _infer_chain(s)
            if chain and chain != "ethereum":
                return s
        except Exception:
            pass
    elif isinstance(value, (list, tuple)):
        for item in value:
            result = _extract_cross_chain_addr(item)
            if result:
                return result
    return None


def _infer_chain(address: str) -> Optional[str]:
    """Infer the destination chain from an address string format.

    Args:
        address: Candidate address string.

    Returns:
        Chain name string, or None if not recognized.
    """
    if not address or len(address) < 25:
        return None
    if _TRON_PATTERN.match(address):
        return "tron"
    if _BTC_PATTERN.match(address):
        return "bitcoin"
    if _EVM_PATTERN.match(address):
        return "ethereum"
    if _SOLANA_PATTERN.match(address) and 32 <= len(address) <= 44:
        return "solana"
    return None
