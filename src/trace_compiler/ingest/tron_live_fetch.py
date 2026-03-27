"""Live on-demand Tron address history fetcher for standalone graph runtime.

Uses the TronGrid v1 REST API (no API key required for basic usage) to fetch
native TRX transfers and TRC-20 token transfers, then persists them into
``raw_transactions`` / ``raw_token_transfers`` in the same address format
as the full TronCollector (25-byte lowercase hex with checksum).

No external dependencies beyond aiohttp — base58check decode is implemented
inline using only the standard library.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_TRONGRID_DEFAULT = "https://api.trongrid.io"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
_MAX_RECORDS = 200  # TronGrid v1 hard cap per request

# 1 TRX = 1,000,000 SUN
_TRX_SUN = 1_000_000

# Base58 alphabet used by Tron (same as Bitcoin)
_B58_ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

# Canonical asset IDs for common TRC-20 tokens
_CANONICAL_ASSET: Dict[str, str] = {
    "USDT": "usdt",
    "USDC": "usdc",
    "TRX": "tron",
    "BTT": "bittorrent",
    "JST": "just",
    "SUN": "sun-token",
    "WIN": "wink",
}


# ---------------------------------------------------------------------------
# Address helpers
# ---------------------------------------------------------------------------


def _b58decode(s: str) -> bytes:
    """Minimal base58 decode — no external library required."""
    num = 0
    for char in s.encode("ascii"):
        num = num * 58 + _B58_ALPHABET.index(char)
    result = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + result


def _tron_hex(address: str) -> Optional[str]:
    """Normalise any Tron address representation to 25-byte lowercase hex.

    The event store and TronChainCompiler use the 25-byte hex format
    (version byte + 20-byte payload + 4-byte double-SHA256 checksum),
    lowercased — matching the output of ``TronCollector.base58_to_hex``.

    Handles:
    - Base58check T-addresses (decode → 25 bytes directly)
    - 42-char hex (41-prefix + 20 bytes) — checksum is appended
    - 50-char hex (already 25 bytes) — passthrough

    Args:
        address: Tron address in any of the above formats.

    Returns:
        50-char lowercase hex string, or None if unrecognised.
    """
    addr = (address or "").strip()
    if not addr:
        return None

    if addr.startswith("T") and len(addr) == 34:
        # Base58check T-address: decode gives 25 bytes (version + payload + checksum)
        try:
            raw = _b58decode(addr)
            if len(raw) == 25:
                return raw.hex().lower()
        except Exception:
            pass
        return None

    if addr.startswith("41") and len(addr) == 42:
        # 21-byte hex (version byte + 20-byte payload) — append checksum
        try:
            raw = bytes.fromhex(addr)
            checksum = hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
            return (raw + checksum).hex().lower()
        except Exception:
            return None

    if len(addr) == 50:
        # 25-byte hex with checksum — validate hex characters and embedded checksum
        if not all(c in "0123456789abcdefABCDEF" for c in addr):
            return None
        try:
            raw = bytes.fromhex(addr)
            payload = raw[:21]
            checksum_stored = raw[21:]
            checksum_expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
            if checksum_stored != checksum_expected:
                return None
        except Exception:
            return None
        return addr.lower()

    return None


# ---------------------------------------------------------------------------
# TronGrid API calls
# ---------------------------------------------------------------------------


async def _trongrid_get(
    url: str,
    session: aiohttp.ClientSession,
    api_key: Optional[str],
) -> Optional[List[Dict]]:
    """Fetch a TronGrid v1 endpoint and return the ``data`` list.

    Args:
        url:     Full TronGrid endpoint URL with query parameters.
        session: Shared aiohttp session.
        api_key: Optional TronGrid Pro API key (higher rate limits when set).

    Returns:
        List of result dicts, empty list for no results, or None on error.
    """
    headers: Dict[str, str] = {}
    if api_key:
        headers["TRON-PRO-API-KEY"] = api_key
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                logger.debug("TronGrid HTTP %s for %s", resp.status, url)
                return None
            data = await resp.json(content_type=None)
            items = data.get("data")
            return items if isinstance(items, list) else []
    except Exception as exc:
        logger.debug("TronGrid request failed for %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def fetch_tron_address_history(
    address: str,
    pg_pool: Any,
    rpc_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> bool:
    """Fetch Tron address history from TronGrid and populate the event store.

    Runs two TronGrid v1 calls in parallel (native TRX transfers and TRC-20
    token transfers), then bulk-inserts results into ``raw_transactions`` and
    ``raw_token_transfers``.  Successful fetches mark the
    ``address_ingest_queue`` entry as ``completed``.

    Args:
        address:  Tron address in any format (base58 T-address, hex).
        pg_pool:  asyncpg connection pool.
        rpc_url:  TronGrid base URL (defaults to api.trongrid.io).
        api_key:  Optional TronGrid Pro API key.

    Returns:
        True if at least one record was stored; False otherwise.
    """
    base = (rpc_url or _TRONGRID_DEFAULT).rstrip("/")

    native_url = (
        f"{base}/v1/accounts/{address}/transactions"
        f"?limit={_MAX_RECORDS}&only_confirmed=true&order_by=block_timestamp,desc"
    )
    trc20_url = (
        f"{base}/v1/accounts/{address}/transactions/trc20"
        f"?limit={_MAX_RECORDS}&only_confirmed=true&order_by=block_timestamp,desc"
    )

    logger.info("tron_live_fetch: fetching %s via TronGrid", address)

    async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
        native_rows, trc20_rows = await asyncio.gather(
            _trongrid_get(native_url, session, api_key),
            _trongrid_get(trc20_url, session, api_key),
        )

    if native_rows is None and trc20_rows is None:
        logger.warning(
            "tron_live_fetch: TronGrid errors for %s; deferring to worker fallback",
            address,
        )
        return False

    native_rows = native_rows or []
    trc20_rows = trc20_rows or []

    if not native_rows and not trc20_rows:
        logger.info("tron_live_fetch: no history found for %s", address)
        await _mark_queue(address, pg_pool, "completed", None, tx_count=0)
        return False

    stored = await _persist(address, native_rows, trc20_rows, pg_pool)

    if stored > 0:
        logger.info(
            "tron_live_fetch: stored %d records for %s (%d native, %d trc20)",
            stored,
            address,
            len(native_rows),
            len(trc20_rows),
        )
        await _mark_queue(address, pg_pool, "completed", None, tx_count=stored)
        return True

    logger.warning(
        "tron_live_fetch: fetched rows for %s but stored none; leaving pending",
        address,
    )
    return False


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def _persist(
    address: str,
    native_rows: List[Dict],
    trc20_rows: List[Dict],
    pg_pool: Any,
) -> int:
    """Insert TronGrid rows into the event store.

    Addresses are converted to 25-byte lowercase hex to match the format
    used by TronCollector and expected by TronChainCompiler queries.

    Args:
        address:     Queried address (used only for logging).
        native_rows: Rows from the TronGrid ``/transactions`` endpoint.
        trc20_rows:  Rows from the TronGrid ``/transactions/trc20`` endpoint.
        pg_pool:     asyncpg pool.

    Returns:
        Total number of rows successfully inserted.
    """
    stored = 0
    try:
        async with pg_pool.acquire() as conn:
            tx_stored = 0
            async with conn.transaction():

                # --- Native TRX transfers (TransferContract only) -----------------
                for row in native_rows:
                    try:
                        tx_hash = (
                            row.get("txID") or row.get("txid") or ""
                        ).lower()
                        if not tx_hash:
                            continue

                        contracts = row.get("raw_data", {}).get("contract", [{}])
                        contract = contracts[0] if contracts else {}
                        if contract.get("type") != "TransferContract":
                            # Only pure TRX transfers; skip contract calls.
                            continue

                        value_data = contract.get("parameter", {}).get("value", {})
                        from_addr = _tron_hex(value_data.get("owner_address", ""))
                        to_addr = _tron_hex(value_data.get("to_address", ""))
                        amount_sun = int(value_data.get("amount", 0) or 0)
                        value_trx = amount_sun / _TRX_SUN

                        ts_ms = row.get("block_timestamp") or 0
                        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

                        ret = row.get("ret") or [{}]
                        status = (
                            "success"
                            if ret[0].get("contractRet") == "SUCCESS"
                            else "failed"
                        )

                        result = await conn.execute(
                            """
                            INSERT INTO raw_transactions
                                (blockchain, tx_hash, timestamp,
                                 from_address, to_address,
                                 value_raw, value_native, status)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                            ON CONFLICT (blockchain, tx_hash) DO NOTHING
                            """,
                            "tron",
                            tx_hash,
                            ts,
                            from_addr,
                            to_addr,
                            amount_sun,
                            value_trx,
                            status,
                        )
                        if result != "INSERT 0 0":
                            tx_stored += 1
                    except Exception as exc:
                        logger.debug(
                            "tron_live_fetch: skipping native tx %s: %s",
                            row.get("txID"),
                            exc,
                        )

                # --- TRC-20 token transfers ----------------------------------------
                tx_transfer_counters: Dict[str, int] = {}
                for row in trc20_rows:
                    try:
                        tx_hash = (row.get("transaction_id") or "").lower()
                        if not tx_hash:
                            continue

                        idx = tx_transfer_counters.get(tx_hash, 0)
                        tx_transfer_counters[tx_hash] = idx + 1

                        ts_ms = row.get("block_timestamp") or 0
                        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

                        from_addr = _tron_hex(row.get("from") or "")
                        to_addr = _tron_hex(row.get("to") or "")

                        token_info = row.get("token_info") or {}
                        symbol = (token_info.get("symbol") or "").upper() or None
                        try:
                            decimals = int(token_info.get("decimals", 6) or 6)
                        except (ValueError, TypeError):
                            decimals = 6
                        decimals = max(0, min(decimals, 18))
                        contract = _tron_hex(token_info.get("address") or "")
                        canonical = _CANONICAL_ASSET.get(symbol) if symbol else None

                        amount_raw = int(row.get("value", 0) or 0)
                        amount_norm = (
                            amount_raw / (10 ** decimals) if amount_raw else 0.0
                        )

                        result = await conn.execute(
                            """
                            INSERT INTO raw_token_transfers
                                (blockchain, tx_hash, transfer_index,
                                 asset_symbol, asset_contract, canonical_asset_id,
                                 from_address, to_address,
                                 amount_raw, amount_normalized, timestamp)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                            ON CONFLICT (blockchain, tx_hash, transfer_index)
                            DO NOTHING
                            """,
                            "tron",
                            tx_hash,
                            idx,
                            symbol,
                            contract,
                            canonical,
                            from_addr,
                            to_addr,
                            amount_raw,
                            amount_norm,
                            ts,
                        )
                        if result != "INSERT 0 0":
                            tx_stored += 1
                    except Exception as exc:
                        logger.debug(
                            "tron_live_fetch: skipping trc20 tx %s: %s",
                            row.get("transaction_id"),
                            exc,
                        )

            stored += tx_stored

    except Exception as exc:
        logger.warning(
            "tron_live_fetch: transaction block failed for %s: %s", address, exc
        )

    return stored


async def _mark_queue(
    address: str,
    pg_pool: Any,
    status: str,
    error: Optional[str],
    tx_count: Optional[int] = None,
) -> None:
    """Update the ``address_ingest_queue`` row after a Tron live fetch attempt."""
    if pg_pool is None:
        return
    try:
        async with pg_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE address_ingest_queue
                SET status       = $1,
                    completed_at = NOW(),
                    tx_count     = COALESCE($2, tx_count),
                    error        = $3
                WHERE address    = $4
                  AND blockchain = 'tron'
                  AND status IN ('pending', 'running')
                """,
                status,
                tx_count,
                error,
                address,
            )
    except Exception as exc:
        logger.warning(
            "tron_live_fetch: failed to update queue for %s: %s", address, exc
        )
