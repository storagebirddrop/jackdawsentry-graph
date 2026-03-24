"""
Bridge event log decoder for the 6 protocols that require intermediate IDs.

Fetches the transaction receipt via async HTTP JSON-RPC and decodes the
protocol-specific deposit/transfer/swap event to extract the ID required
by that protocol's status API.

Supported protocols and their event → ID mapping:
  Across    V3FundsDeposited → depositId (indexed topic2) + destinationChainId (topic1)
  Celer     Send             → transferId (indexed topic1) + dstChainId (log data word 2)
  Stargate  Swap             → destination LayerZero V1 chainId (indexed topic1)
  Chainflip SwapNative /
            SwapToken        → dstChain Chainflip ID (indexed topic1)

Note: Rango and Relay are handled in bridge_tracer.py by direct API lookup
(tx-hash-based endpoints) so this module is not invoked for those two protocols.
"""

from __future__ import annotations

import logging
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

try:
    import aiohttp
    _AIOHTTP_AVAILABLE = True
except ImportError:
    aiohttp = None  # type: ignore[assignment]
    _AIOHTTP_AVAILABLE = False

logger = logging.getLogger(__name__)

_RPC_TIMEOUT_S: float = 10.0


# ---------------------------------------------------------------------------
# Event signature constants (topic0 = keccak256 of canonical ABI sig string)
# ---------------------------------------------------------------------------

def _keccak_sig(abi_sig: str) -> str:
    """Return the lowercase 0x-prefixed keccak256 of an ABI event signature string.

    Tries pycryptodome first (available in all environments), then falls back
    to eth_hash (available in the Docker runtime via web3/eth-abi dependencies).
    """
    data = abi_sig.encode()
    try:
        from Crypto.Hash import keccak as _pycrypto_keccak  # type: ignore[import]
        k = _pycrypto_keccak.new(digest_bits=256)
        k.update(data)
        return "0x" + k.hexdigest()
    except ImportError:
        pass
    try:
        from eth_hash.auto import keccak  # type: ignore[import]
        return "0x" + keccak(data).hex()
    except ImportError:
        pass
    raise RuntimeError(
        "keccak256 not available: install pycryptodome or eth-hash"
    )


# Across V3 SpokePool — V3FundsDeposited
# indexed: destinationChainId (topic1), depositId (topic2), depositor (topic3)
ACROSS_V3_FUNDS_DEPOSITED: str = _keccak_sig(
    "V3FundsDeposited(address,address,uint256,uint256,"
    "uint256,uint32,uint32,uint32,uint32,address,address,address,bytes)"
)

# Celer cBridge — Send
# indexed: transferId (topic1), sender (topic2), receiver (topic3)
CELER_SEND: str = _keccak_sig(
    "Send(bytes32,address,address,address,uint256,uint64,uint64,uint32)"
)

# Stargate V1 Router — Swap
# indexed: chainId/uint16 (topic1), dstPoolId/uint256 (topic2)
STARGATE_SWAP: str = _keccak_sig(
    "Swap(uint16,uint256,address,uint256,uint256,uint256,uint256,uint256)"
)

# Chainflip Vault — SwapNative (native ETH/AVAX/etc.)
# indexed: dstChain/uint32 (topic1)
CHAINFLIP_SWAP_NATIVE: str = _keccak_sig(
    "SwapNative(uint32,bytes,uint32,uint256,address,bytes)"
)

# Chainflip Vault — SwapToken (ERC-20 swaps)
# indexed: dstChain/uint32 (topic1)
CHAINFLIP_SWAP_TOKEN: str = _keccak_sig(
    "SwapToken(uint32,bytes,uint32,address,uint256,address,bytes)"
)


# ---------------------------------------------------------------------------
# Chain ID maps
# ---------------------------------------------------------------------------

# LayerZero V1 numeric chain ID → internal chain name (used by Stargate V1)
_LZ_V1_CHAIN_MAP: Dict[int, str] = {
    101: "ethereum",
    102: "bsc",
    106: "avalanche",
    109: "polygon",
    110: "arbitrum",
    111: "optimism",
    112: "fantom",
    151: "metis",
    181: "mantle",
    183: "linea",
    184: "base",
    214: "scroll",
    257: "zksync",
}

# Chainflip internal chain ID → internal chain name
_CHAINFLIP_CHAIN_MAP: Dict[int, str] = {
    1: "bitcoin",
    2: "ethereum",
    3: "polkadot",
    4: "arbitrum",
    5: "solana",
    6: "base",
}

# Internal chain name → EVM numeric chain ID (used as Across originChainId param)
CHAIN_TO_EVM_ID: Dict[str, int] = {
    "ethereum": 1,
    "optimism": 10,
    "bsc": 56,
    "polygon": 137,
    "fantom": 250,
    "arbitrum": 42161,
    "avalanche": 43114,
    "base": 8453,
    "linea": 59144,
    "metis": 1088,
    "mantle": 5000,
    "scroll": 534352,
    "zksync": 324,
    "mode": 34443,
    "blast": 81457,
    "celo": 42220,
}


# ---------------------------------------------------------------------------
# RPC helper
# ---------------------------------------------------------------------------

async def fetch_tx_receipt(
    rpc_url: str,
    tx_hash: str,
) -> Optional[Dict[str, Any]]:
    """Fetch the transaction receipt via ``eth_getTransactionReceipt`` JSON-RPC.

    Args:
        rpc_url:  HTTP(S) JSON-RPC endpoint (WebSocket URLs are rejected).
        tx_hash:  0x-prefixed transaction hash.

    Returns:
        The ``result`` dict from the JSON-RPC response, or None on error / not found.
    """
    if rpc_url.startswith(("ws://", "wss://")):
        return None
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getTransactionReceipt",
        "params": [tx_hash],
        "id": 1,
    }
    timeout = aiohttp.ClientTimeout(total=_RPC_TIMEOUT_S)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(rpc_url, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
                return data.get("result")
    except Exception as exc:
        logger.debug("fetch_tx_receipt failed for %s: %s", tx_hash[:16], exc)
        return None


def _http_rpc_for_chain(chain: str) -> Optional[str]:
    """Return the configured HTTP JSON-RPC fallback URL for *chain*, or None."""
    try:
        from src.api.config import get_settings
        settings = get_settings()
    except Exception:
        return None
    chain_key = chain.upper().replace("-", "_")
    url = (
        getattr(settings, f"{chain_key}_RPC_FALLBACK", None)
        or getattr(settings, f"{chain_key}_RPC_URL", None)
    )
    if not url or url.startswith(("ws://", "wss://")):
        return None
    return url


# ---------------------------------------------------------------------------
# Low-level ABI topic / data decoders
# ---------------------------------------------------------------------------

def _topic_uint(topic_hex: str) -> int:
    """Decode a 32-byte hex topic string as a uint256 integer."""
    return int(topic_hex.lstrip("0x") or "0", 16)


def _topic_bytes32(topic_hex: str) -> str:
    """Return a 32-byte hex topic as a lowercase 0x-prefixed bytes32 string."""
    raw = topic_hex.lstrip("0x") if topic_hex else ""
    return "0x" + raw.zfill(64).lower()


def _data_uint_at(data_hex: str, word_index: int) -> int:
    """Decode the *word_index*-th 32-byte ABI-encoded word from *data_hex* as uint."""
    raw = data_hex.lstrip("0x")
    start = word_index * 64
    if len(raw) < start + 64:
        return 0
    return int(raw[start: start + 64], 16)


# ---------------------------------------------------------------------------
# Protocol-specific event decoders
# ---------------------------------------------------------------------------

def _decode_across(logs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Extract depositId + destinationChainId from an Across V3FundsDeposited log.

    Returns dict with keys ``deposit_id`` (int) and ``dest_chain_id_evm`` (int),
    or None when no matching log is found.
    """
    sig = ACROSS_V3_FUNDS_DEPOSITED.lower()
    for log in logs:
        topics = log.get("topics") or []
        if not topics or str(topics[0]).lower() != sig:
            continue
        if len(topics) < 3:
            continue
        return {
            "deposit_id": _topic_uint(str(topics[2])),       # uint32 depositId
            "dest_chain_id_evm": _topic_uint(str(topics[1])),  # uint256 destinationChainId
        }
    return None


def _decode_celer(logs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Extract transferId + dstChainId from a Celer cBridge Send log.

    Returns dict with keys ``transfer_id`` (0x-bytes32 str) and
    ``dst_chain_id_evm`` (int), or None when no matching log is found.
    """
    sig = CELER_SEND.lower()
    for log in logs:
        topics = log.get("topics") or []
        if not topics or str(topics[0]).lower() != sig:
            continue
        if len(topics) < 2:
            continue
        # ABI-encoded data: token (addr), amount (uint256), dstChainId (uint64),
        # nonce (uint64), maxSlippage (uint32) — dstChainId is at word index 2
        return {
            "transfer_id": _topic_bytes32(str(topics[1])),
            "dst_chain_id_evm": _data_uint_at(log.get("data", "0x"), 2),
        }
    return None


def _decode_stargate(logs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Extract destination chain from a Stargate V1 Router Swap log.

    Returns dict with keys ``lz_chain_id`` (int) and ``dest_chain`` (str or None),
    or None when no matching log is found.
    """
    sig = STARGATE_SWAP.lower()
    for log in logs:
        topics = log.get("topics") or []
        if not topics or str(topics[0]).lower() != sig:
            continue
        if len(topics) < 2:
            continue
        lz_id = _topic_uint(str(topics[1]))
        return {
            "lz_chain_id": lz_id,
            "dest_chain": _LZ_V1_CHAIN_MAP.get(lz_id),
        }
    return None


def _decode_chainflip(logs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Extract destination chain from a Chainflip Vault SwapNative or SwapToken log.

    Returns dict with keys ``cf_chain_id`` (int) and ``dest_chain`` (str or None),
    or None when no matching log is found.
    """
    for sig in (CHAINFLIP_SWAP_NATIVE.lower(), CHAINFLIP_SWAP_TOKEN.lower()):
        for log in logs:
            topics = log.get("topics") or []
            if not topics or str(topics[0]).lower() != sig:
                continue
            if len(topics) < 2:
                continue
            cf_id = _topic_uint(str(topics[1]))
            return {
                "cf_chain_id": cf_id,
                "dest_chain": _CHAINFLIP_CHAIN_MAP.get(cf_id),
            }
    return None


# Protocol → decoder function
_DECODERS = {
    "across": _decode_across,
    "celer": _decode_celer,
    "stargate": _decode_stargate,
    "chainflip": _decode_chainflip,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def decode_bridge_deposit(
    protocol_id: str,
    chain: str,
    tx_hash: str,
) -> Optional[Dict[str, Any]]:
    """Fetch tx receipt for *tx_hash* on *chain* and decode the bridge deposit event.

    Returns a protocol-specific dict of decoded fields on success, or None when:
    - the protocol has no log decoder (Rango, Relay — handled by API-first path),
    - the HTTP RPC URL is not configured for *chain*,
    - the receipt is unavailable,
    - no matching bridge event log is found.

    Args:
        protocol_id:  Bridge protocol identifier (e.g. ``"across"``, ``"celer"``).
        chain:        Internal chain name (e.g. ``"ethereum"``, ``"arbitrum"``).
        tx_hash:      0x-prefixed source-chain transaction hash.

    Returns:
        Decoded dict or None.
    """
    decoder = _DECODERS.get(protocol_id)
    if decoder is None:
        return None

    rpc_url = _http_rpc_for_chain(chain)
    if not rpc_url:
        logger.debug(
            "decode_bridge_deposit: no HTTP RPC for chain=%s protocol=%s",
            chain,
            protocol_id,
        )
        return None

    receipt = await fetch_tx_receipt(rpc_url, tx_hash)
    if receipt is None:
        logger.debug(
            "decode_bridge_deposit: receipt not found for %s/%s",
            chain,
            tx_hash[:16],
        )
        return None

    logs: List[Dict[str, Any]] = receipt.get("logs") or []
    result = decoder(logs)
    if result is None:
        logger.debug(
            "decode_bridge_deposit: no %s event in %d logs for %s",
            protocol_id,
            len(logs),
            tx_hash[:16],
        )
    return result
