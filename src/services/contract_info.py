"""Contract deployer/creator resolution service.

Resolves whether an address is a smart contract and, if so, retrieves the
deployer address and deployment transaction hash.  Results are cached in
Redis with a long TTL (7 days) because deployment data is immutable.

Supported chains
----------------
EVM chains (via Etherscan v2 unified API, single ``ETHERSCAN_API_KEY``):
    ethereum, bsc, polygon, arbitrum, base, avalanche, optimism

Solana (via JSON-RPC ``getAccountInfo``):
    Determines whether the account is an executable program and, if the
    program is upgradeable, retrieves the ``upgradeAuthority`` from the
    associated programData account.

Usage
-----
::

    from src.services.contract_info import get_contract_info, ContractInfo

    info: ContractInfo | None = await get_contract_info("0xabc...", "ethereum")
    if info and info.is_contract:
        print(info.deployer, info.deployment_tx)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Etherscan v2 chain-id map
# ---------------------------------------------------------------------------

# Maps chain identifiers (as used elsewhere in this codebase) to Etherscan v2
# chain-id integers.  A single ETHERSCAN_API_KEY covers all of these.
_ETHERSCAN_CHAIN_IDS: Dict[str, int] = {
    "ethereum": 1,
    "bsc": 56,
    "polygon": 137,
    "arbitrum": 42161,
    "base": 8453,
    "avalanche": 43114,
    "optimism": 10,
}

_ETHERSCAN_API_BASE = "https://api.etherscan.io/v2/api"

# Solana mainnet-beta public RPC (falls back to well-known public endpoint).
_SOLANA_RPC_URL = os.environ.get(
    "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
)

# BPF Loader Upgradeable program ID on Solana.  Programs deployed with this
# loader have an associated programData account containing upgrade authority.
_BPF_UPGRADEABLE_LOADER = "BPFLoaderUpgradeab1e11111111111111111111111"

# Redis key prefix and TTL (7 days — deployment data is immutable).
_CACHE_PREFIX = "contract_info:"
_CACHE_TTL_SECONDS = 7 * 24 * 3600


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ContractInfo:
    """Result of a contract deployer lookup.

    Attributes:
        is_contract:      True when the address is a deployed contract/program.
        deployer:         Address that deployed the contract (EVM) or the
                          upgrade authority / original deployer (Solana).
        deployment_tx:    Transaction hash of the deployment (EVM only).
        upgrade_authority: Solana upgrade authority address when the program
                          uses the upgradeable loader; ``None`` when immutable.
        chain:            Chain identifier this record belongs to.
    """

    is_contract: bool
    deployer: Optional[str] = None
    deployment_tx: Optional[str] = None
    upgrade_authority: Optional[str] = None
    chain: str = field(default="")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_evm_contract_info(
    address: str,
    chain: str,
    *,
    client: httpx.AsyncClient,
) -> ContractInfo:
    """Query Etherscan v2 to resolve EVM contract creator.

    Uses the ``getcontractcreation`` action which accepts a single address
    and returns ``contractCreator`` + ``txHash`` in one round-trip.

    Args:
        address: Checksummed or lower-case EVM address.
        chain:   Chain identifier (e.g. ``"ethereum"``, ``"bsc"``).
        client:  Shared ``httpx.AsyncClient`` for connection reuse.

    Returns:
        :class:`ContractInfo` with ``is_contract=True`` when the address is a
        contract; ``is_contract=False`` when it is an EOA or not found.
    """
    chain_id = _ETHERSCAN_CHAIN_IDS.get(chain)
    if chain_id is None:
        return ContractInfo(is_contract=False, chain=chain)

    api_key = os.environ.get("ETHERSCAN_API_KEY", "")
    params = {
        "chainid": chain_id,
        "module": "contract",
        "action": "getcontractcreation",
        "contractaddresses": address,
        "apikey": api_key,
    }

    try:
        resp = await client.get(_ETHERSCAN_API_BASE, params=params, timeout=8.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug("Etherscan contract lookup failed addr=%s chain=%s: %s", address, chain, exc)
        return ContractInfo(is_contract=False, chain=chain)

    if data.get("status") != "1" or not data.get("result"):
        # status "0" can mean EOA or rate-limited; check for rate-limit message
        message = (data.get("message") or "").lower()
        if "rate limit" in message or "max rate" in message:
            logger.debug("Etherscan rate-limited for addr=%s chain=%s", address, chain)
            return None  # Don't cache rate-limit responses
        return ContractInfo(is_contract=False, chain=chain)

    row = data["result"][0]
    return ContractInfo(
        is_contract=True,
        deployer=row.get("contractCreator"),
        deployment_tx=row.get("txHash"),
        chain=chain,
    )


async def _get_solana_contract_info(
    address: str,
    *,
    client: httpx.AsyncClient,
) -> ContractInfo:
    """Resolve Solana program executable status and upgrade authority.

    Two RPC calls are made:
    1. ``getAccountInfo`` on ``address`` — checks ``executable`` flag.
    2. If the loader is the upgradeable BPF loader, ``getAccountInfo`` on the
       derived programData account to extract the ``upgradeAuthority``.

    Args:
        address: Base58-encoded Solana public key.
        client:  Shared ``httpx.AsyncClient`` for connection reuse.

    Returns:
        :class:`ContractInfo`; ``is_contract=True`` when the account is an
        executable program.
    """
    payload: Dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [
            address,
            {"encoding": "jsonParsed"},
        ],
    }

    try:
        resp = await client.post(_SOLANA_RPC_URL, json=payload, timeout=8.0)
        resp.raise_for_status()
        rpc_result = resp.json()
    except Exception as exc:
        logger.debug("Solana RPC getAccountInfo failed addr=%s: %s", address, exc)
        return ContractInfo(is_contract=False, chain="solana")

    value = rpc_result.get("result", {}).get("value")
    if not value:
        return ContractInfo(is_contract=False, chain="solana")

    if not value.get("executable"):
        return ContractInfo(is_contract=False, chain="solana")

    # Executable account — determine upgrade authority via programData.
    owner = value.get("owner", "")
    upgrade_authority: Optional[str] = None

    if owner == _BPF_UPGRADEABLE_LOADER:
        # The parsed data contains programData account pubkey.
        parsed = (value.get("data") or {}).get("parsed", {})
        program_data_addr = (
            parsed.get("info", {}).get("programData")
            or parsed.get("programData")
        )
        if program_data_addr:
            pd_payload: Dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "getAccountInfo",
                "params": [
                    program_data_addr,
                    {"encoding": "jsonParsed"},
                ],
            }
            try:
                pd_resp = await client.post(_SOLANA_RPC_URL, json=pd_payload, timeout=8.0)
                pd_resp.raise_for_status()
                pd_result = pd_resp.json()
                pd_value = pd_result.get("result", {}).get("value") or {}
                pd_info = (
                    (pd_value.get("data") or {})
                    .get("parsed", {})
                    .get("info", {})
                )
                upgrade_authority = pd_info.get("authority") or pd_info.get("upgradeAuthority")
            except Exception as exc:
                logger.debug("Solana programData lookup failed addr=%s: %s", program_data_addr, exc)

    return ContractInfo(
        is_contract=True,
        deployer=upgrade_authority,  # For Solana programs, upgrade authority is the deployer
        upgrade_authority=upgrade_authority,
        chain="solana",
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def get_contract_info(
    address: str,
    chain: str,
    *,
    redis_client: Any = None,
) -> Optional[ContractInfo]:
    """Return contract deployer/creator info for *address* on *chain*.

    Results are cached in Redis with a 7-day TTL.  The function degrades
    gracefully: when Redis or the upstream API is unavailable, a live lookup
    is attempted (Redis miss) or ``None`` is returned (API failure).

    Args:
        address:      Address to look up (EVM hex or Solana base58).
        chain:        Chain identifier (``"ethereum"``, ``"bsc"``,
                      ``"polygon"``, ``"arbitrum"``, ``"base"``,
                      ``"avalanche"``, ``"optimism"``, or ``"solana"``).
        redis_client: Optional async Redis client for caching.  When omitted
                      the function performs a live lookup each call.

    Returns:
        :class:`ContractInfo` on success, or ``None`` when the chain is not
        supported.
    """
    # Normalise to lower-case for EVM addresses; Solana is case-sensitive.
    addr_key = address.lower() if chain != "solana" else address
    cache_key = f"{_CACHE_PREFIX}{chain}:{addr_key}"

    # --- Cache read ---
    if redis_client is not None:
        try:
            cached = await redis_client.get(cache_key)
            if cached is not None:
                d = json.loads(cached)
                return ContractInfo(**d)
        except Exception as exc:
            logger.debug("Redis cache read failed key=%s: %s", cache_key, exc)

    # --- Live lookup ---
    result: Optional[ContractInfo] = None
    async with httpx.AsyncClient() as client:
        if chain in _ETHERSCAN_CHAIN_IDS:
            result = await _get_evm_contract_info(addr_key, chain, client=client)
        elif chain == "solana":
            result = await _get_solana_contract_info(address, client=client)
        else:
            return None

    # --- Cache write ---
    if result is not None and redis_client is not None:
        try:
            payload = json.dumps({
                "is_contract": result.is_contract,
                "deployer": result.deployer,
                "deployment_tx": result.deployment_tx,
                "upgrade_authority": result.upgrade_authority,
                "chain": result.chain,
            })
            await redis_client.set(cache_key, payload, ex=_CACHE_TTL_SECONDS)
        except Exception as exc:
            logger.debug("Redis cache write failed key=%s: %s", cache_key, exc)

    return result
