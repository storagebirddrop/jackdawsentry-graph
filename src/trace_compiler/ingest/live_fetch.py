"""Live on-demand EVM address history fetcher for standalone graph runtime.

When the event store has no data for a queried address and an Etherscan API
key is configured, this module fetches recent token-transfer and native-
transaction history directly from the Etherscan v2 API and persists the
results into ``raw_token_transfers`` / ``raw_transactions``.

This is what makes the standalone graph actually usable without a separate
ingest worker: the first expansion of an unknown EVM address fires a
background Etherscan fetch, which completes within seconds and allows the
frontend's ingest-status poller to re-trigger expansion with live data.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

# Etherscan v2 multi-chain API (single key, chainid param selects network)
_ETHERSCAN_API = "https://api.etherscan.io/v2/api"

# Chains supported via Etherscan v2 and their chain IDs
_CHAIN_IDS: Dict[str, int] = {
    "ethereum": 1,
    "bsc": 56,
    "polygon": 137,
    "arbitrum": 42161,
    "base": 8453,
    "optimism": 10,
    "avalanche": 43114,
}

# Canonical asset IDs for common ERC-20 symbols
_CANONICAL_ASSET: Dict[str, str] = {
    "USDT": "usdt",
    "USDC": "usdc",
    "ETH": "eth",
    "WETH": "weth",
    "DAI": "dai",
    "WBTC": "wbtc",
    "BUSD": "busd",
    "MATIC": "matic",
    "BNB": "bnb",
}

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
# Max records per Etherscan API call; 500 is a reasonable cap for
# investigation purposes without hammering rate limits.
_MAX_RECORDS = 500


async def _etherscan_get(
    action: str,
    chain_id: int,
    address: str,
    api_key: str,
    session: aiohttp.ClientSession,
) -> Optional[List[Dict]]:
    """Perform a single Etherscan v2 account-module API call.

    Args:
        action:   Etherscan action string (``txlist`` or ``tokentx``).
        chain_id: Etherscan chain ID integer.
        address:  Address to query (hex string).
        api_key:  Etherscan API key.
        session:  Shared aiohttp session.

    Returns:
        List of result dicts on success, or None on any error.
    """
    params = {
        "chainid": chain_id,
        "module": "account",
        "action": action,
        "address": address,
        "sort": "desc",
        "page": 1,
        "offset": _MAX_RECORDS,
        "apikey": api_key,
    }
    try:
        async with session.get(_ETHERSCAN_API, params=params) as resp:
            if resp.status != 200:
                logger.debug("Etherscan %s returned HTTP %s", action, resp.status)
                return None
            data = await resp.json(content_type=None)
            if data.get("status") != "1":
                # status="0" can mean "no results" or a real API error.
                # Distinguish by inspecting the message field.
                msg = (data.get("message") or "").lower()
                if (
                    "no transactions found" in msg
                    or "no records found" in msg
                    or "no result" in msg
                    or data.get("result") == []
                ):
                    return []
                logger.warning(
                    "Etherscan %s API error: status=%s message=%s",
                    action, data.get("status"), data.get("message"),
                )
                return None
            result = data.get("result")
            return result if isinstance(result, list) else None
    except Exception as exc:
        logger.debug("Etherscan %s request failed: %s", action, exc)
        return None


async def fetch_evm_address_history(
    address: str,
    chain: str,
    pg_pool: Any,
    api_key: str,
) -> bool:
    """Fetch EVM address history from Etherscan and populate the event store.

    Runs two Etherscan calls in parallel (``txlist`` + ``tokentx``), then
    bulk-inserts the results into ``raw_transactions`` and
    ``raw_token_transfers``.  Marks the ``address_ingest_queue`` entry
    ``completed`` or ``failed`` when done.

    Args:
        address:  Lowercase EVM hex address (0x…).
        chain:    Blockchain name — must be a key in ``_CHAIN_IDS``.
        pg_pool:  asyncpg connection pool.
        api_key:  Etherscan v2 API key.

    Returns:
        True if at least one record was written; False otherwise.
    """
    chain_id = _CHAIN_IDS.get(chain)
    if chain_id is None:
        logger.debug("live_fetch: chain %s not supported via Etherscan v2", chain)
        await _mark_queue(address, chain, pg_pool, "failed", "chain not supported via Etherscan")
        return False

    logger.info("live_fetch: fetching %s on %s via Etherscan", address, chain)

    # Fetch native txs and token transfers in parallel.
    async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
        native_rows, token_rows = await asyncio.gather(
            _etherscan_get("txlist", chain_id, address, api_key, session),
            _etherscan_get("tokentx", chain_id, address, api_key, session),
        )

    if native_rows is None and token_rows is None:
        logger.warning("live_fetch: Etherscan returned errors for %s/%s", address, chain)
        await _mark_queue(address, chain, pg_pool, "failed", "Etherscan API error")
        return False

    native_rows = native_rows or []
    token_rows = token_rows or []

    if not native_rows and not token_rows:
        logger.info("live_fetch: Etherscan reports no history for %s/%s", address, chain)
        # Mark completed with tx_count=0 so the trigger doesn't re-queue endlessly.
        await _mark_queue(address, chain, pg_pool, "completed", None, tx_count=0)
        return False

    stored = 0
    if pg_pool is not None:
        stored = await _persist(address, chain, native_rows, token_rows, pg_pool)

    tx_count_total = stored
    if stored > 0:
        logger.info(
            "live_fetch: stored %d records for %s on %s (%d native, %d token)",
            stored,
            address,
            chain,
            len(native_rows),
            len(token_rows),
        )
        await _mark_queue(address, chain, pg_pool, "completed", None, tx_count=tx_count_total)
        return True

    await _mark_queue(address, chain, pg_pool, "failed", "no records inserted")
    return False


async def _persist(
    address: str,
    chain: str,
    native_rows: List[Dict],
    token_rows: List[Dict],
    pg_pool: Any,
) -> int:
    """Insert fetched rows into the event store tables.

    Args:
        address:     Queried address (used only for logging).
        chain:       Blockchain name.
        native_rows: Rows from Etherscan ``txlist``.
        token_rows:  Rows from Etherscan ``tokentx``.
        pg_pool:     asyncpg pool.

    Returns:
        Total number of rows successfully inserted.
    """
    stored = 0
    try:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                # --- Native transactions -----------------------------------------
                for row in native_rows:
                    try:
                        ts = datetime.fromtimestamp(int(row["timeStamp"]), tz=timezone.utc)
                        value_wei = int(row.get("value", 0) or 0)
                        value_eth = value_wei / 1e18

                        # Preserve calldata — critical for bridge destination decoding.
                        # Etherscan returns the hex-encoded input field; store as bytes.
                        raw_input = row.get("input", "") or ""
                        input_bytes: Optional[bytes] = None
                        if raw_input and raw_input != "0x":
                            try:
                                hex_body = raw_input[2:] if raw_input.startswith("0x") else raw_input
                                input_bytes = bytes.fromhex(hex_body)
                            except ValueError:
                                pass

                        result = await conn.execute(
                            """
                            INSERT INTO raw_transactions
                                (blockchain, tx_hash, block_number, timestamp,
                                 from_address, to_address,
                                 value_raw, value_native, status, input_data)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                            ON CONFLICT (blockchain, tx_hash) DO NOTHING
                            """,
                            chain,
                            row["hash"].lower(),
                            int(row["blockNumber"]) if row.get("blockNumber") else None,
                            ts,
                            row.get("from", "").lower(),
                            row.get("to", "").lower() if row.get("to") else None,
                            value_wei,
                            value_eth,
                            "success" if row.get("txreceipt_status") == "1" else "failed",
                            input_bytes,
                        )
                        if result != "INSERT 0 0":
                            stored += 1
                    except Exception as exc:
                        logger.debug(
                            "live_fetch: skipping native tx %s: %s",
                            row.get("hash"),
                            exc,
                        )

                # --- Token transfers (ERC-20) ------------------------------------
                # Group by tx_hash to assign stable per-tx transfer indices.
                tx_transfer_counters: Dict[str, int] = {}
                for row in token_rows:
                    try:
                        tx_hash = row["hash"].lower()
                        idx = tx_transfer_counters.get(tx_hash, 0)
                        tx_transfer_counters[tx_hash] = idx + 1

                        ts = datetime.fromtimestamp(int(row["timeStamp"]), tz=timezone.utc)
                        decimals = int(row.get("tokenDecimal", 18) or 18)
                        amount_raw = int(row.get("value", 0) or 0)
                        amount_norm = amount_raw / (10 ** decimals) if amount_raw else 0.0
                        symbol = (row.get("tokenSymbol") or "").upper() or None
                        canonical = _CANONICAL_ASSET.get(symbol) if symbol else None
                        contract = (row.get("contractAddress") or "").lower() or None

                        result = await conn.execute(
                            """
                            INSERT INTO raw_token_transfers
                                (blockchain, tx_hash, transfer_index,
                                 asset_symbol, asset_contract, canonical_asset_id,
                                 from_address, to_address,
                                 amount_raw, amount_normalized, timestamp)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                            ON CONFLICT (blockchain, tx_hash, transfer_index) DO NOTHING
                            """,
                            chain,
                            tx_hash,
                            idx,
                            symbol,
                            contract,
                            canonical,
                            (row.get("from") or "").lower() or None,
                            (row.get("to") or "").lower() or None,
                            amount_raw,
                            amount_norm,
                            ts,
                        )
                        if result != "INSERT 0 0":
                            stored += 1
                    except Exception as exc:
                        logger.debug(
                            "live_fetch: skipping token transfer %s: %s",
                            row.get("hash"),
                            exc,
                        )
    except Exception as exc:
        logger.warning("live_fetch: transaction block failed for %s/%s: %s", address, chain, exc)

    return stored


async def _mark_queue(
    address: str,
    chain: str,
    pg_pool: Any,
    status: str,
    error: Optional[str],
    tx_count: Optional[int] = None,
) -> None:
    """Update the ``address_ingest_queue`` row after a live fetch attempt.

    Args:
        address:  Address being ingested.
        chain:    Blockchain name.
        pg_pool:  asyncpg pool (no-op if None).
        status:   New status — ``'completed'`` or ``'failed'``.
        error:    Error message for failed rows; None on success.
        tx_count: Number of records stored (written to the queue row).
    """
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
                WHERE address = $4
                  AND blockchain = $5
                  AND status IN ('pending', 'running')
                """,
                status,
                tx_count,
                error,
                address,
                chain,
            )
    except Exception as exc:
        logger.warning("live_fetch: failed to update queue for %s/%s: %s", address, chain, exc)


def supported_chain(chain: str) -> bool:
    """Return True if Etherscan v2 supports this chain for address history."""
    return chain in _CHAIN_IDS
