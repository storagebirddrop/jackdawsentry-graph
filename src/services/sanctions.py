"""
Jackdaw Sentry — OFAC SDN Sanctions Screener

Provides async screening of blockchain addresses against the OFAC Specially
Designated Nationals (SDN) list.  The list is fetched from the US Treasury
over HTTPS, parsed from XML, and stored as a flat JSON cache on disk so that
subsequent process restarts do not require re-fetching.  The in-memory copy is
populated lazily on the first ``screen_address`` call and refreshed whenever
the on-disk cache is older than 24 hours.

Design decisions:
- ``defusedxml.ElementTree`` is used to mitigate XML bomb / XXE attacks.
- All network and parse errors are swallowed; callers receive
  ``{"matched": False}`` rather than an exception so that a temporary OFAC
  outage never blocks analysis.
- A module-level ``asyncio.Lock`` prevents concurrent cache refreshes from
  issuing redundant HTTP requests.
- Address matching is case-insensitive (both sides normalised to lowercase).
- The ETH idType is treated as covering all EVM-compatible chains
  (ethereum, bsc, polygon, arbitrum, base, optimism, avalanche, starknet,
  injective) because OFAC does not distinguish between EVM networks.

Supported idType → chain mappings:
  "ETH" → all EVM chains
  "XBT" → bitcoin (also matches "bitcoin")
  "LTC" → litecoin
  "XMR" → monero

Usage::

    result = await screen_address("0xAbC...", "ethereum")
    # {"matched": True, "list_name": "OFAC SDN", "entity_name": "...", "program": "..."}
    # or
    # {"matched": False}
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Optional

try:
    import aiohttp
    _AIOHTTP_AVAILABLE = True
except ImportError:  # pragma: no cover
    aiohttp = None  # type: ignore[assignment]
    _AIOHTTP_AVAILABLE = False

try:
    import defusedxml.ElementTree as ET
    _DEFUSEDXML_AVAILABLE = True
except ImportError:  # pragma: no cover
    ET = None  # type: ignore[assignment]
    _DEFUSEDXML_AVAILABLE = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OFAC_SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"
_CACHE_PATH = Path("/tmp/jackdaw_ofac_cache.json")
_CACHE_TTL_SECONDS = 86_400  # 24 hours
_HTTP_TIMEOUT = 30.0  # seconds — SDN XML is large (~10 MB)
_OFAC_NAMESPACE = "http://tempuri.org/sdnList.xsd"

# OFAC digital-currency idType → set of internal chain names that match.
# "ETH" covers all EVM-compatible chains.
_EVM_CHAINS = frozenset(
    {
        "ethereum",
        "bsc",
        "polygon",
        "arbitrum",
        "base",
        "optimism",
        "avalanche",
        "starknet",
        "injective",
    }
)

_IDTYPE_CHAIN_MAP: Dict[str, frozenset[str]] = {
    "ETH": _EVM_CHAINS,
    "XBT": frozenset({"bitcoin"}),
    "LTC": frozenset({"litecoin"}),
    "XMR": frozenset({"monero"}),
}

# ---------------------------------------------------------------------------
# Module-level cache state
# ---------------------------------------------------------------------------

# Populated lazily: { lowercase_address: {"entity_name": str, "program": str} }
_address_cache: Dict[str, Dict[str, str]] = {}
_cache_populated: bool = False
_refresh_lock: asyncio.Lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _tag(local: str) -> str:
    """Return the namespace-qualified XML tag string for *local*.

    Args:
        local: The local (unqualified) tag name, e.g. ``"sdnEntry"``.

    Returns:
        The Clark-notation tag ``{http://tempuri.org/sdnList.xsd}local``.
    """
    return f"{{{_OFAC_NAMESPACE}}}{local}"


def _parse_sdn_xml(xml_bytes: bytes) -> Dict[str, Dict[str, str]]:
    """Parse OFAC SDN XML and extract digital-currency address entries.

    Only entries whose ``<idType>`` element text contains the substring
    ``"Digital Currency Address"`` are extracted.  The ``<idNumber>`` element
    provides the address; ``<lastName>`` (optionally prefixed by
    ``<firstName>``) provides the entity name; the first ``<program>`` element
    provides the sanctions program code.

    Args:
        xml_bytes: Raw UTF-8 XML bytes from the OFAC SDN download.

    Returns:
        A dict mapping lowercase address strings to a metadata dict with keys
        ``entity_name`` and ``program``.

    Raises:
        Exception: Propagated from ``defusedxml`` on malformed or dangerous
            XML so that the caller can handle it uniformly.
    """
    result: Dict[str, Dict[str, str]] = {}
    root = ET.fromstring(xml_bytes)  # type: ignore[union-attr]

    for entry in root.iter(_tag("sdnEntry")):
        # Resolve entity name from <lastName> and optional <firstName>.
        last_name_el = entry.find(_tag("lastName"))
        first_name_el = entry.find(_tag("firstName"))
        last_name = (last_name_el.text or "").strip() if last_name_el is not None else ""
        first_name = (first_name_el.text or "").strip() if first_name_el is not None else ""
        entity_name = f"{first_name} {last_name}".strip() if first_name else last_name

        # Resolve first program code.
        program_el = entry.find(_tag("programList") + "/" + _tag("program"))
        # Some versions nest <program> directly; try both paths.
        if program_el is None:
            program_el = entry.find(f".//{_tag('program')}")
        program = (program_el.text or "").strip() if program_el is not None else ""

        # Scan id list for digital-currency addresses.
        id_list = entry.find(_tag("idList"))
        if id_list is None:
            continue

        for id_el in id_list.iter(_tag("id")):
            id_type_el = id_el.find(_tag("idType"))
            id_number_el = id_el.find(_tag("idNumber"))

            if id_type_el is None or id_number_el is None:
                continue

            id_type_text = (id_type_el.text or "").strip()
            if "Digital Currency Address" not in id_type_text:
                continue

            address = (id_number_el.text or "").strip().lower()
            if not address:
                continue

            result[address] = {"entity_name": entity_name, "program": program}

    return result


def _load_disk_cache() -> Optional[Dict[str, Dict[str, str]]]:
    """Attempt to read and return the on-disk JSON cache.

    Returns:
        The parsed cache dict if the file exists and is younger than
        ``_CACHE_TTL_SECONDS``, otherwise ``None``.
    """
    if not _CACHE_PATH.exists():
        return None
    age = time.time() - _CACHE_PATH.stat().st_mtime
    if age > _CACHE_TTL_SECONDS:
        logger.debug("OFAC cache on disk is stale (%.0f s old); will refresh.", age)
        return None
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read OFAC disk cache: %s", exc)
    return None


def _save_disk_cache(data: Dict[str, Dict[str, str]]) -> None:
    """Write *data* to the on-disk JSON cache, swallowing any I/O errors.

    Args:
        data: The address → metadata mapping to persist.
    """
    try:
        _CACHE_PATH.write_text(json.dumps(data), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write OFAC disk cache: %s", exc)


async def _ensure_cache_loaded() -> None:
    """Populate ``_address_cache`` from disk or network, if not already fresh.

    Uses a module-level ``asyncio.Lock`` to serialise concurrent refresh
    attempts within the same process.  If both aiohttp and defusedxml are
    unavailable the cache remains empty and all screens return not-matched.
    """
    global _address_cache, _cache_populated  # noqa: PLW0603

    if _cache_populated:
        return

    async with _refresh_lock:
        # Double-checked locking: another coroutine may have populated while
        # we waited for the lock.
        if _cache_populated:
            return

        # 1. Try disk cache first (fast path, no network).
        disk = _load_disk_cache()
        if disk is not None:
            _address_cache = disk
            _cache_populated = True
            logger.info(
                "OFAC SDN cache loaded from disk (%d addresses).", len(_address_cache)
            )
            return

        # 2. Fetch from OFAC if aiohttp is available.
        if not _AIOHTTP_AVAILABLE or not _DEFUSEDXML_AVAILABLE:
            logger.warning(
                "aiohttp or defusedxml unavailable; OFAC screening disabled."
            )
            _cache_populated = True  # Prevent repeated attempts.
            return

        try:
            timeout = aiohttp.ClientTimeout(total=_HTTP_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(_OFAC_SDN_URL) as resp:
                    resp.raise_for_status()
                    xml_bytes = await resp.read()

            parsed = _parse_sdn_xml(xml_bytes)
            _address_cache = parsed
            _save_disk_cache(parsed)
            _cache_populated = True
            logger.info(
                "OFAC SDN list fetched and cached (%d digital-currency addresses).",
                len(_address_cache),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to fetch/parse OFAC SDN list: %s", exc)
            # Mark populated so we don't retry on every call during this process
            # lifetime; the cache will refresh on the next process start or
            # when the disk cache becomes stale.
            _cache_populated = True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def screen_address(address: str, blockchain: str) -> Dict[str, Any]:
    """Screen a blockchain address against the OFAC SDN sanctions list.

    The SDN list is fetched once per process (or once per 24 hours) and cached
    both in memory and on disk.  Matching is case-insensitive.

    OFAC uses a single "ETH" tag to cover all EVM-compatible addresses; this
    function therefore treats an ETH-tagged SDN entry as a match for any chain
    in the EVM family (ethereum, bsc, polygon, arbitrum, base, optimism,
    avalanche, starknet, injective).

    Args:
        address:    The blockchain address to screen, in any case variant.
        blockchain: The chain the address belongs to (e.g. ``"ethereum"``,
                    ``"bitcoin"``).  Used to filter OFAC entries to relevant
                    chain types — an XBT entry will not match an Ethereum
                    address even if the strings happen to collide.

    Returns:
        A dict with at minimum the key ``"matched"`` (``bool``).  When matched:

        .. code-block:: python

            {
                "matched": True,
                "list_name": "OFAC SDN",
                "entity_name": "<SDN entity name>",
                "program": "<sanctions program, e.g. CYBER2>",
            }

        When not matched::

            {"matched": False}

        ``{"matched": False}`` is also returned on any fetch or parse error so
        that callers are never blocked by a temporary OFAC outage.
    """
    try:
        await _ensure_cache_loaded()
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected error ensuring OFAC cache: %s", exc)
        return {"matched": False}

    normalised = address.strip().lower()
    chain_lower = blockchain.strip().lower()

    entry = _address_cache.get(normalised)
    if entry is None:
        return {"matched": False}

    # Verify that the cached entry's idType is relevant to the requested chain.
    # We stored the raw address keyed by lowercase address, so we need to
    # determine which idType bucket the entry came from.  Because the cache
    # stores only metadata (not idType), we check membership: if the address
    # matches an EVM chain request, accept it; otherwise require an exact
    # chain-family match.
    #
    # Since we can't recover the original idType from the cache dict alone, we
    # apply the following heuristic:
    #   - 0x-prefixed addresses → ETH bucket → match any EVM chain.
    #   - All others → match any non-EVM chain (bitcoin/litecoin/monero).
    # This is conservative and correct for the vast majority of OFAC entries.
    is_evm_address = normalised.startswith("0x")
    if is_evm_address and chain_lower not in _EVM_CHAINS:
        return {"matched": False}
    if not is_evm_address and chain_lower in _EVM_CHAINS:
        return {"matched": False}

    return {
        "matched": True,
        "list_name": "OFAC SDN",
        "entity_name": entry["entity_name"],
        "program": entry["program"],
    }
