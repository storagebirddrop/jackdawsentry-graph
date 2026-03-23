"""
Jackdaw Sentry — CEX / VASP Entity Attribution

Maps blockchain addresses to known exchange or service entities so that graph
visualisations and risk reports can label nodes with human-readable names
(e.g. "Binance", "Coinbase") and consistent risk metadata.

Two data sources are combined, with the hardcoded seed taking priority:

1. **Hardcoded seed** — a curated, high-confidence set of hot-wallet and
   contract addresses for major CEXs, DeFi protocols, and staking services.
   Available immediately with no network dependency.

   - EVM chains (ethereum, bsc, polygon, arbitrum, base, optimism, avalanche)
     share the 0x address namespace — a single EVM seed covers all of them.
   - Tron addresses (T-prefix, base58) are tracked separately.
   - Bitcoin addresses (1/3/bc1 prefix) are tracked separately.
   - Solana addresses (base58, ~44 chars) are tracked separately.
   - XRP Ledger addresses (r-prefix, base58, ~34 chars) are tracked separately.
   - Cosmos and Sui seeds are pending — JS-gated explorers prevented
     verification of candidate addresses; omitted per false-positive policy.

2. **Etherscan labels** (best-effort, EVM only) — if the ``ETHERSCAN_API_KEY``
   environment variable is set, any address not found in the seed is enriched
   via the Etherscan v2 ``getaddresslabel`` endpoint.  Failures are swallowed
   gracefully; the caller always receives whatever the seed alone provides.

Design decisions:
- Per-chain seed tables built once at import time; no lock is required.
- Only high-confidence addresses are included; when in doubt, omit rather
  than mislabel — a false positive on an investigation platform is worse than
  a false negative.
- Etherscan requests are issued concurrently (``asyncio.gather``).
- Results are returned only for addresses that resolved to a known entity;
  unknown addresses are omitted so callers can use a simple ``in`` check.
- Case-insensitive matching: inputs are normalised to lowercase; seed keys
  are stored lowercase.

Usage::

    results = await lookup_addresses_bulk(
        ["0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be"],
        "ethereum",
    )
    # {"0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be": {
    #     "entity_name": "Binance", "entity_type": "cex",
    #     "category": "exchange", "risk_level": "low"}}
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

try:
    import aiohttp
    _AIOHTTP_AVAILABLE = True
except ImportError:  # pragma: no cover
    aiohttp = None  # type: ignore[assignment]
    _AIOHTTP_AVAILABLE = False

logger = logging.getLogger(__name__)

_ETHERSCAN_LABEL_URL = (
    "https://api.etherscan.io/v2/api"
    "?chainid=1&module=account&action=getaddresslabel"
    "&address={address}&apiKey={api_key}"
)
_ETHERSCAN_TIMEOUT = 5.0  # seconds per request

# EVM chains that share the 0x address namespace and therefore benefit from
# the seed data and Etherscan enrichment.
_EVM_CHAINS = frozenset(
    {
        "ethereum",
        "bsc",
        "polygon",
        "arbitrum",
        "base",
        "optimism",
        "avalanche",
    }
)

# ---------------------------------------------------------------------------
# Hardcoded seed
# ---------------------------------------------------------------------------
# Each entry is a tuple:
#   (entity_name, entity_type, category, risk_level)
#
# Sources: public blockchain explorers, Etherscan labels, official exchange
# transparency pages.  Only high-confidence addresses are included; when in
# doubt, omit rather than mislabel.
#
# Tornado Cash is intentionally excluded — it is already handled by the
# service_classifier module and duplicating it here would create conflicting
# attribution records.

_SeedEntry = Dict[str, str]

def _build_seed(raw: List[tuple]) -> Dict[str, "_SeedEntry"]:
    """Build a lowercase-keyed lookup dict from a raw seed list.

    Keys are always stored lowercase so callers need only ``addr.lower()``
    before lookup regardless of the address format (0x EVM, Tron base58,
    Bitcoin bech32/base58).
    """
    return {
        addr.lower(): {
            "entity_name": name,
            "entity_type": etype,
            "category": category,
            "risk_level": risk,
        }
        for addr, name, etype, category, risk in raw
    }


# ---------------------------------------------------------------------------
# EVM seed (ethereum, bsc, polygon, arbitrum, base, optimism, avalanche)
# ---------------------------------------------------------------------------
# Addresses stored lowercase; a single seed covers all EVM-compatible chains
# because they share the 0x address namespace.

_SEED_EVM_RAW: List[tuple] = [
    # address, entity_name, entity_type, category, risk_level
    # --- Binance ---
    ("0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be", "Binance", "cex", "exchange", "low"),  # Binance 1
    ("0xd551234ae421e3bcba99a0da6d736074f22192ff", "Binance", "cex", "exchange", "low"),  # Binance 2
    ("0x564286362092d8e7936f0549571a803b203aaced", "Binance", "cex", "exchange", "low"),  # Binance 3
    ("0xf977814e90da44bfa03b6295a0616a897441acec", "Binance", "cex", "exchange", "low"),  # Binance 8
    ("0xbe0eb53f46cd790cd13851d5eff43d12404d33e8", "Binance", "cex", "exchange", "low"),  # Binance Cold
    ("0x28c6c06298d514db089934071355e5743bf21d60", "Binance", "cex", "exchange", "low"),  # Binance 14
    ("0x21a31ee1afc51d94c2efccaa2092ad1028285549", "Binance", "cex", "exchange", "low"),  # Binance 15
    # --- Coinbase ---
    ("0x71660c4005ba85c37ccec55d0c4493e66fe775d3", "Coinbase", "cex", "exchange", "low"),  # Coinbase 1
    ("0xa090e606e30bd747d4e6245a1517ebe430f0057e", "Coinbase", "cex", "exchange", "low"),  # Coinbase 2
    ("0x503828976d22510aad0201ac7ec88293211d23da", "Coinbase", "cex", "exchange", "low"),  # Coinbase 3
    ("0x77696bb39917c91a0c3908d577d5e322095425ca", "Coinbase", "cex", "exchange", "low"),  # Coinbase 4
    # --- Kraken ---
    ("0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0", "Kraken", "cex", "exchange", "low"),  # Kraken 1
    ("0xae2d4617c862309a3d75a0ffb358c7a5009c673f", "Kraken", "cex", "exchange", "low"),  # Kraken 2
    ("0x0a869d79a7052c7f1b55a8ebabbea3420f0d1e13", "Kraken", "cex", "exchange", "low"),  # Kraken 3
    # --- OKX ---
    ("0x6cc5f688a315f3dc28a7781717a9a798a59fda7b", "OKX", "cex", "exchange", "low"),  # OKX 1
    ("0x236f9f97e0e62388479bf9e5ba4889e46b0273c3", "OKX", "cex", "exchange", "low"),  # OKX 2
    # --- Bybit ---
    ("0xf89d7b9c864f589bbf53a82105107622b35ea40", "Bybit", "cex", "exchange", "low"),  # Bybit 1
    # --- Staking protocols ---
    # Lido stETH — official Lido staking contract
    ("0xae7ab96520de3a18e5e111b5eaab095312d7fe84", "Lido stETH", "staking", "staking", "low"),
    # RocketPool deposit pool
    ("0xdd3f50f8a6cafbe9b31a427582963f465e745af8", "RocketPool", "staking", "staking", "low"),
    # --- DeFi lending / service ---
    # Compound V3 cUSDCv3 market
    ("0xc3d688b66703497daa19211eedff47f25384cdc3", "Compound V3", "defi", "lending", "low"),
    # MakerDAO DSS / DAI proxy
    ("0x9759a6ac90977b93b58547b4a71c78317f391a28", "MakerDAO", "defi", "service", "low"),
]

# ---------------------------------------------------------------------------
# Tron seed (T-prefix base58 addresses)
# ---------------------------------------------------------------------------
# Sources: public TronScan explorer labels and exchange transparency pages.
# TRX address case is preserved in base58 — seeds are stored as-is (no lower).

_SEED_TRON_RAW: List[tuple] = [
    # --- Binance ---
    ("TJDENsfBJs4RFETt1X1W8wMDc8M5XnJhCe", "Binance", "cex", "exchange", "low"),  # Binance Tron hot
    ("TF5Bn4cJCT6GVeUgyCN4rBhDg42KBrmuRt", "Binance", "cex", "exchange", "low"),  # Binance Tron 2
    # --- OKX ---
    ("TKVtFppeXnmNGUkKFJrn1MgMgMigV8s1YD", "OKX", "cex", "exchange", "low"),  # OKX Tron hot
    # --- Huobi / HTX ---
    ("TUEYcyPAqc4gjnCYFCHSFHTDSCTFBEkTgR", "Huobi/HTX", "cex", "exchange", "low"),  # Huobi Tron 1
    ("TVjsyZ7fYF3qLF6BQgPmTEZy1xrNNyVAAA", "Huobi/HTX", "cex", "exchange", "low"),  # Huobi Tron 2
    # --- Bybit ---
    ("TJCo98saj6WND61g1uuKwJ9GMWMT9WkJFo", "Bybit", "cex", "exchange", "low"),  # Bybit Tron hot
]

# ---------------------------------------------------------------------------
# Bitcoin seed (1/3/bc1 addresses)
# ---------------------------------------------------------------------------
# Sources: public block explorer cluster labels and exchange announcements.
# Bitcoin addresses are case-sensitive (bech32 is lowercase; base58 preserves
# mixed case); seeds are stored in their canonical on-chain form.

_SEED_BITCOIN_RAW: List[tuple] = [
    # --- Binance ---
    ("34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo", "Binance", "cex", "exchange", "low"),  # Binance cold 1
    ("bc1qm34lsc65zpw79lxes69zkqmk6ee3ewf0j77s3h", "Binance", "cex", "exchange", "low"),  # Binance cold bech32
    # --- Coinbase ---
    ("3NiD4qDTEPuFBETHuSeGKB8cjEGE1cjB3V", "Coinbase", "cex", "exchange", "low"),  # Coinbase cold
    # --- Kraken ---
    ("3QCzvfL4ZRvmJFiWWBVwxfdaNBT8EtxB5y", "Kraken", "cex", "exchange", "low"),  # Kraken cold
]

# ---------------------------------------------------------------------------
# Solana seed (base58 addresses, ~44 chars)
# ---------------------------------------------------------------------------
# Sources: Solscan labeled accounts (verified exchange labels).
# Solana base58 addresses are case-sensitive; stored as-is, then lowercased
# by _build_seed for consistent lookup.

_SEED_SOLANA_RAW: List[tuple] = [
    # --- Binance ---
    ("5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9", "Binance", "cex", "exchange", "low"),   # Binance 2
    ("DNWwqgkLZfRXgCJ2B3HE5SJKSJACu4jWNfDrhQecAkiQ", "Binance", "cex", "exchange", "low"),   # Binance 3
    ("53unSgGWqEWANcPYRF35B2Bgf8BkszUtcccKiXwGGLyr", "Binance.US", "cex", "exchange", "low"), # Binance.US hot
    # --- Coinbase ---
    ("GJRs4FwHtemZ5ZE9x3FNvJ8TMwitKTh21yxdRPqn7npE", "Coinbase", "cex", "exchange", "low"),  # Coinbase 1
    ("H8sMJSCQxfKiFTCfDR3DUMLPwcRbM61LGFJ8N4dK3WjS", "Coinbase", "cex", "exchange", "low"),  # Coinbase 2
    ("D89hHJT5Aqyx1trP6EnGY9jJUB3whgnq3aUvvCqedvzf", "Coinbase", "cex", "exchange", "low"),  # Coinbase 3
    # --- Kraken ---
    ("FWznbcNXWQuHTawe9RxvQ2LdCENssh12dsznf4RiouN5", "Kraken", "cex", "exchange", "low"),     # Kraken hot
    # --- OKX ---
    ("is6MTRHEgyFLNTfYcuV4QBWLjrZBfmhVNYR6ccgr8KV", "OKX", "cex", "exchange", "low"),        # OKX 1
    ("C68a6RCGLiPskbPYtAcsCjhG8tfTWYcoB4JjCrXFdqyo", "OKX", "cex", "exchange", "low"),       # OKX 2
    ("5VCwKtCXgCJ6kit5FybXjvriW3xELsFDhYrPSqtJNmcD", "OKX", "cex", "exchange", "low"),       # OKX 3
    # --- Bybit ---
    ("AC5RDfQFmDS1deWZos921JfqscXdByf8BKHs5ACWjtW2", "Bybit", "cex", "exchange", "low"),      # Bybit hot
]

# ---------------------------------------------------------------------------
# XRP Ledger seed (r-prefix base58 addresses, ~34 chars)
# ---------------------------------------------------------------------------
# Sources: Bithomp and XRPSCAN verified exchange labels.
# Exchanges typically use a single deposit address with destination tags to
# route to individual customer accounts.
# Note: inactive Binance XRP address (rEb8TK3gBgk5auZkwc6sHnwrGVJH8DuaLh)
# excluded — marked high-risk/deleted on Bithomp; unverified Binance addresses
# omitted per false-positive policy.

_SEED_XRP_RAW: List[tuple] = [
    # --- Kraken ---
    ("rLHzPsX6oXkzU2qL12kHCH8G8cnZv1rBJh", "Kraken", "cex", "exchange", "low"),   # Kraken main (since 2014)
    # --- Coinbase ---
    ("rw2ciyaNshpHe7bCHo4bRWq6pqqynnWKQg", "Coinbase", "cex", "exchange", "low"), # Coinbase hot
    # --- Bitstamp ---
    ("rvYAfWj5gh67oV6fW32ZzP3Aw4Eubs59B", "Bitstamp", "cex", "exchange", "low"),  # Bitstamp hot
]

# ---------------------------------------------------------------------------
# Module-level seed dicts — built once at import time.
# ---------------------------------------------------------------------------

# All seeds normalise keys to lowercase so callers only strip+lower once.
# _build_seed lowercases the address field from each raw tuple.
_SEED_EVM: Dict[str, _SeedEntry] = _build_seed(_SEED_EVM_RAW)
_SEED_TRON: Dict[str, _SeedEntry] = _build_seed(_SEED_TRON_RAW)
_SEED_BITCOIN: Dict[str, _SeedEntry] = _build_seed(_SEED_BITCOIN_RAW)
_SEED_SOLANA: Dict[str, _SeedEntry] = _build_seed(_SEED_SOLANA_RAW)
_SEED_XRP: Dict[str, _SeedEntry] = _build_seed(_SEED_XRP_RAW)

# Map chain name → seed dict.  EVM chains all resolve to _SEED_EVM.
# Cosmos and Sui seeds are pending (no verifiable data yet).
_CHAIN_SEEDS: Dict[str, Dict[str, _SeedEntry]] = {
    **{chain: _SEED_EVM for chain in _EVM_CHAINS},
    "tron": _SEED_TRON,
    "bitcoin": _SEED_BITCOIN,
    "solana": _SEED_SOLANA,
    "xrp": _SEED_XRP,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _etherscan_lookup(
    address: str,
    api_key: str,
    session: "aiohttp.ClientSession",
) -> Optional[_SeedEntry]:
    """Query Etherscan for a human-readable address label.

    Returns a minimal attribution entry if Etherscan returns a non-empty label,
    otherwise ``None``.  All HTTP and JSON errors are caught and logged at
    DEBUG level; failures are never propagated to callers.

    Args:
        address:  Lowercase EVM address to query.
        api_key:  Etherscan API key (must be non-empty).
        session:  An active ``aiohttp.ClientSession`` shared by the caller.

    Returns:
        A dict with keys ``entity_name``, ``entity_type``, ``category``, and
        ``risk_level``, or ``None`` if the address is unlabelled or the request
        fails.
    """
    url = _ETHERSCAN_LABEL_URL.format(address=address, api_key=api_key)
    try:
        async with session.get(url) as resp:
            resp.raise_for_status()
            payload = await resp.json(content_type=None)

        # Etherscan v2 response: {"status": "1", "message": "OK", "result": "<label>"}
        if not isinstance(payload, dict):
            return None
        if payload.get("status") != "1":
            return None
        label = payload.get("result", "")
        if not isinstance(label, str) or not label.strip():
            return None

        return {
            "entity_name": label.strip(),
            "entity_type": "vasp",
            "category": "service",
            "risk_level": "medium",
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("Etherscan label lookup failed for %s: %s", address, exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def lookup_addresses_bulk(
    addresses: List[str],
    blockchain: str,
) -> Dict[str, Dict[str, Any]]:
    """Attribute a list of blockchain addresses to known CEX / VASP entities.

    Two sources are consulted in order:

    1. **Hardcoded seed** — instant, no network; covers EVM (all chains sharing
       the 0x namespace), Tron, and Bitcoin addresses.
    2. **Etherscan labels** (EVM only, best-effort) — if ``ETHERSCAN_API_KEY``
       is set in the environment, any seed-miss EVM address is queried against
       the Etherscan v2 ``getaddresslabel`` endpoint with a 5-second timeout.
       Failures fall back to the seed-only result silently.

    Only addresses that could be attributed are included in the returned dict.
    Addresses with no known attribution are omitted (not returned as
    ``{"matched": False}`` — simply absent from the dict).

    Args:
        addresses:  List of blockchain address strings (any case).
        blockchain: The chain the addresses belong to (e.g. ``"ethereum"``,
                    ``"tron"``, ``"bitcoin"``).  Chains not present in
                    ``_CHAIN_SEEDS`` return an empty dict.

    Returns:
        A dict mapping each attributed address (in the original case provided
        by the caller) to a metadata dict with keys:

        - ``entity_name`` (str): Human-readable name, e.g. ``"Binance"``.
        - ``entity_type`` (str): One of ``"cex"``, ``"vasp"``, ``"defi"``,
          ``"lending"``, ``"staking"``, ``"mixer"``, ``"dao"``.
        - ``category`` (str): One of ``"exchange"``, ``"service"``,
          ``"mixer"``, ``"lending"``, ``"staking"``, ``"bridge"``.
        - ``risk_level`` (str): One of ``"low"``, ``"medium"``, ``"high"``.

    Example::

        results = await lookup_addresses_bulk(
            ["0x3f5CE5FBFe3E9af3971dD833D26bA9b5C936f0bE"],
            "ethereum",
        )
        # {
        #   "0x3f5CE5FBFe3E9af3971dD833D26bA9b5C936f0bE": {
        #       "entity_name": "Binance",
        #       "entity_type": "cex",
        #       "category": "exchange",
        #       "risk_level": "low",
        #   }
        # }
    """
    chain_lower = blockchain.strip().lower()
    seed = _CHAIN_SEEDS.get(chain_lower)
    is_evm = chain_lower in _EVM_CHAINS

    results: Dict[str, Dict[str, Any]] = {}
    etherscan_misses: List[str] = []  # original-case addresses not found in seed

    # --- Pass 1: seed lookup ---
    for addr in addresses:
        normalised = addr.strip().lower()
        if seed is None:
            # No seed data for this chain.
            continue
        entry = seed.get(normalised)
        if entry is not None:
            results[addr] = dict(entry)
        elif is_evm:
            etherscan_misses.append(addr)

    # --- Pass 2: Etherscan enrichment (EVM only, best-effort) ---
    if not is_evm or not etherscan_misses:
        return results

    api_key = os.environ.get("ETHERSCAN_API_KEY", "").strip()
    if not api_key or not _AIOHTTP_AVAILABLE:
        return results

    try:
        timeout = aiohttp.ClientTimeout(total=_ETHERSCAN_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            tasks = [
                _etherscan_lookup(addr.strip().lower(), api_key, session)
                for addr in etherscan_misses
            ]
            etherscan_results = await asyncio.gather(*tasks, return_exceptions=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Etherscan bulk lookup failed: %s", exc)
        return results

    for addr, entry in zip(etherscan_misses, etherscan_results):
        if entry is not None:
            results[addr] = entry

    return results
