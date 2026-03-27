"""Live on-demand Solana address history fetcher for standalone graph runtime.

When the event store has no data for a queried Solana address, this module
fetches recent transaction history directly from the Solana JSON-RPC API and
persists the results into ``raw_token_transfers`` / ``raw_transactions``.

Approach:
1. ``getSignaturesForAddress`` — list recent confirmed signatures.
2. ``getTransaction`` (jsonParsed) for each signature, batched in parallel.
3. Parse token balance changes (pre/post) into ``raw_token_transfers``.
   Both legs of a swap are stored so that ``_maybe_build_solana_swap_event``
   can identify the full in/out picture from a single tx_hash query.
4. Parse system-program ``transfer`` instructions into ``raw_transactions``
   for native SOL flows.
5. Populate ``solana_ata_owners`` from the ``owner`` field in token balances
   so the chain compiler can resolve ATAs to wallet addresses immediately.

The ``to_address`` / ``from_address`` columns in ``raw_token_transfers`` are
stored as WALLET addresses (ATA owner), never as raw ATA addresses — this
matches the Solana chain compiler's expectation after ATA resolution.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

import json

import aiohttp

from src.services.canonical_assets import resolve_canonical_asset_identity

logger = logging.getLogger(__name__)

_DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
# Fallback RPCs tried in order when the primary returns empty results.
# publicnode and other shared endpoints may not index all addresses.
_FALLBACK_RPCS = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-api.projectserum.com",
]
_MAX_SIGNATURES = 150   # signatures per address fetch
_TX_BATCH_SIZE = 1      # concurrent getTransaction calls per batch; 1 = fully sequential
_TX_BATCH_DELAY = 2.0   # seconds between batches to respect public RPC rate limits
_TX_RETRY_DELAY = 1.0   # unused; kept for compatibility (retry is handled inside _rpc_post)
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
_RATE_LIMIT_BACKOFF = 12.0  # seconds to wait when a 429 is received

# Known token symbols by mint address (common assets)
_MINT_SYMBOLS: Dict[str, str] = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
    "So11111111111111111111111111111111111111112": "WSOL",
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": "ETH",
    "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E": "BTC",
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": "MSOL",
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": "BONK",
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": "JUP",
    "WENWENvqqNya429ubCdR81ZmD69brwQaaBYY6p3LCpk": "WEN",
    "SNSNkV9zfG5ZKWQs6x4hxvBRV6s8SqMfSGCtECDvdMd": "SNS",
}

_SYSTEM_PROGRAM = "11111111111111111111111111111111"


def _mint_label(mint: str) -> str:
    """Return a readable fallback label for a Solana mint."""
    symbol = _MINT_SYMBOLS.get(mint)
    if symbol:
        return symbol
    if len(mint) > 12:
        return f"{mint[:6]}...{mint[-4:]}"
    return mint or "SPL"


async def _rpc_post(
    session: aiohttp.ClientSession,
    rpc_url: str,
    method: str,
    params: list,
    *,
    _retries: int = 3,
) -> Any:
    """Make a single Solana JSON-RPC call with automatic 429 back-off.

    Public Solana RPC endpoints enforce per-IP rate limits, particularly on
    ``getTransaction``.  When a 429 response is received the request is
    retried up to ``_retries`` times with a fixed back-off of
    ``_RATE_LIMIT_BACKOFF`` seconds before each retry.

    Args:
        session:   Shared aiohttp session.
        rpc_url:   Solana RPC endpoint URL.
        method:    JSON-RPC method name.
        params:    Method parameters.
        _retries:  Maximum number of retry attempts on 429.

    Returns:
        ``result`` value from the response, or None after all retries fail.
    """
    for attempt in range(_retries + 1):
        try:
            async with session.post(rpc_url, json={
                "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
            }) as resp:
                if resp.status == 429:
                    if attempt < _retries:
                        logger.debug(
                            "Solana RPC %s HTTP 429, back-off %.0fs (attempt %d/%d)",
                            method, _RATE_LIMIT_BACKOFF, attempt + 1, _retries,
                        )
                        await asyncio.sleep(_RATE_LIMIT_BACKOFF)
                        continue
                    logger.debug("Solana RPC %s HTTP 429, exhausted retries", method)
                    return None
                if resp.status != 200:
                    logger.debug("Solana RPC %s HTTP %s", method, resp.status)
                    return None
                data = await resp.json(content_type=None)
                err = data.get("error")
                if err:
                    # JSON-RPC 429 is returned as an error object by some endpoints.
                    code = err.get("code") if isinstance(err, dict) else None
                    if code == 429 and attempt < _retries:
                        logger.debug(
                            "Solana RPC %s JSON 429, back-off %.0fs (attempt %d/%d)",
                            method, _RATE_LIMIT_BACKOFF, attempt + 1, _retries,
                        )
                        await asyncio.sleep(_RATE_LIMIT_BACKOFF)
                        continue
                    logger.debug("Solana RPC %s error: %s", method, err)
                    return None
                return data.get("result")
        except Exception as exc:
            logger.debug("Solana RPC %s failed: %s", method, exc)
            return None
    return None


async def fetch_solana_address_history(
    address: str,
    pg_pool: Any,
    rpc_url: Optional[str] = None,
) -> bool:
    """Fetch Solana address history from JSON-RPC and populate the event store.

    Runs ``getSignaturesForAddress`` then batch-fetches each transaction,
    parses SPL token transfer legs and native SOL transfers, and bulk-inserts
    the results.  Marks the ``address_ingest_queue`` entry ``completed`` or
    ``failed`` when done.

    Args:
        address:  Base58 Solana wallet address.
        pg_pool:  asyncpg connection pool.
        rpc_url:  Solana RPC endpoint; defaults to ``SOLANA_RPC_URL`` env var
                  or the public mainnet endpoint.

    Returns:
        True if at least one record was written; False otherwise.
    """
    rpc_url = rpc_url or os.environ.get("SOLANA_RPC_URL", _DEFAULT_RPC)
    # WebSocket URLs are not usable for HTTP JSON-RPC POST requests.
    # Convert wss:// → https:// and ws:// → http:// so that publicnode-style
    # endpoints that share the same hostname for both protocols work correctly.
    if rpc_url.startswith("wss://"):
        rpc_url = "https://" + rpc_url[6:]
    elif rpc_url.startswith("ws://"):
        rpc_url = "http://" + rpc_url[5:]
    logger.info("solana_live_fetch: fetching %s (rpc=%s)", address, rpc_url)

    async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
        # --- Step 1: get recent confirmed signatures ---
        # Try the configured RPC first; if it returns nothing (some shared
        # endpoints don't index all addresses), fall through to fallbacks.
        sigs_result = await _rpc_post(
            session, rpc_url, "getSignaturesForAddress",
            [address, {"limit": _MAX_SIGNATURES, "commitment": "finalized"}],
        )
        if not sigs_result and rpc_url not in _FALLBACK_RPCS:
            for fallback in _FALLBACK_RPCS:
                logger.debug(
                    "solana_live_fetch: primary RPC empty for %s, trying %s",
                    address, fallback,
                )
                sigs_result = await _rpc_post(
                    session, fallback, "getSignaturesForAddress",
                    [address, {"limit": _MAX_SIGNATURES, "commitment": "finalized"}],
                )
                if sigs_result:
                    rpc_url = fallback
                    break

        if not sigs_result:
            logger.info("solana_live_fetch: no signatures found for %s", address)
            await _mark_queue(address, pg_pool, "completed", None, tx_count=0)
            return False

        signatures = [r["signature"] for r in sigs_result if not r.get("err")]
        logger.info(
            "solana_live_fetch: %d confirmed signatures for %s", len(signatures), address
        )

        # --- Step 2: batch-fetch transactions ---
        all_token_rows: List[Dict] = []
        all_native_rows: List[Dict] = []
        all_ata_entries: List[Tuple[str, str, str]] = []  # (ata, owner, mint)

        all_ix_rows: List[Dict] = []

        for i in range(0, len(signatures), _TX_BATCH_SIZE):
            batch = signatures[i: i + _TX_BATCH_SIZE]
            results = await asyncio.gather(*[
                _rpc_post(
                    session, rpc_url, "getTransaction",
                    [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                )
                for sig in batch
            ])
            for sig, tx in zip(batch, results):
                if tx is None:
                    logger.debug("solana_live_fetch: skipping %s (no response)", sig[:20])
                    continue
                t_rows, n_rows, ata_entries, ix_rows = _parse_transaction(sig, tx)
                all_token_rows.extend(t_rows)
                all_native_rows.extend(n_rows)
                all_ata_entries.extend(ata_entries)
                all_ix_rows.extend(ix_rows)

            # Pace requests between batches to respect public RPC rate limits.
            if i + _TX_BATCH_SIZE < len(signatures):
                await asyncio.sleep(_TX_BATCH_DELAY)

    if not all_token_rows and not all_native_rows:
        logger.info("solana_live_fetch: no transfers parsed for %s", address)
        await _mark_queue(address, pg_pool, "completed", None, tx_count=0)
        return False

    stored = 0
    if pg_pool is not None:
        stored = await _persist(
            address, all_token_rows, all_native_rows,
            all_ata_entries, all_ix_rows, pg_pool,
        )

    if stored > 0:
        logger.info(
            "solana_live_fetch: stored %d records for %s (%d token, %d native)",
            stored, address, len(all_token_rows), len(all_native_rows),
        )
        await _mark_queue(address, pg_pool, "completed", None, tx_count=stored)
        return True

    await _mark_queue(address, pg_pool, "failed", "no records inserted")
    return False


def _parse_transaction(
    sig: str,
    tx: Dict,
) -> Tuple[List[Dict], List[Dict], List[Tuple[str, str, str]], List[Dict]]:
    """Parse a single transaction into transfer rows, ATA cache entries, and instruction data.

    Token transfer legs are derived from ``preTokenBalances`` /
    ``postTokenBalances`` diffs rather than instruction parsing.  This
    approach is program-agnostic and handles swaps, bridges, and plain
    transfers equally — both legs of a swap are included so that
    ``_maybe_build_solana_swap_event`` can reconstruct the full picture.

    Instruction data for unrecognised programs is extracted from the
    ``jsonParsed`` response (base58-decoded) and returned so callers can store
    it for bridge destination decoding.  This enables the bridge hop compiler
    to extract cross-chain destination addresses from instruction bytes for
    bridges that encode the recipient inline (as opposed to in an account).

    Addresses are stored as WALLET owner addresses, not raw ATAs.

    Args:
        sig: Transaction signature (used as tx_hash).
        tx:  Parsed transaction object from ``getTransaction``.

    Returns:
        Tuple of (token_rows, native_rows, ata_entries, ix_rows) where
        ``ix_rows`` is a list of dicts with keys ``program_id``,
        ``ix_index``, ``data_bytes``, and ``timestamp``.
    """
    token_rows: List[Dict] = []
    native_rows: List[Dict] = []
    ata_entries: List[Tuple[str, str, str]] = []
    ix_rows: List[Dict] = []

    meta = tx.get("meta") or {}
    message = (tx.get("transaction") or {}).get("message") or {}

    # Skip failed transactions.
    if meta.get("err"):
        return token_rows, native_rows, ata_entries, ix_rows

    block_time = tx.get("blockTime")
    ts = datetime.fromtimestamp(block_time, tz=timezone.utc) if block_time else None

    account_keys = message.get("accountKeys") or []

    def addr_at(idx: int) -> Optional[str]:
        if 0 <= idx < len(account_keys):
            a = account_keys[idx]
            return a.get("pubkey") if isinstance(a, dict) else str(a)
        return None

    # --- Build ATA → owner map from token balance metadata ---
    pre_bal = {b["accountIndex"]: b for b in (meta.get("preTokenBalances") or [])}
    post_bal = {b["accountIndex"]: b for b in (meta.get("postTokenBalances") or [])}

    for bal in list(pre_bal.values()) + list(post_bal.values()):
        owner = bal.get("owner")
        ata_addr = addr_at(bal["accountIndex"])
        mint = bal.get("mint", "")
        if owner and ata_addr:
            ata_entries.append((ata_addr, owner, mint))

    # --- Compute per-mint balance changes ---
    all_indices = set(list(pre_bal.keys()) + list(post_bal.keys()))
    # mint → list of {ata, owner, delta, decimals}
    mint_changes: Dict[str, List[Dict]] = {}

    for idx in all_indices:
        pre = pre_bal.get(idx, {})
        post = post_bal.get(idx, {})
        mint = post.get("mint") or pre.get("mint")
        if not mint:
            continue
        owner = post.get("owner") or pre.get("owner")
        ata_addr = addr_at(idx)

        pre_amt = int((pre.get("uiTokenAmount") or {}).get("amount") or 0)
        post_amt = int((post.get("uiTokenAmount") or {}).get("amount") or 0)
        delta = post_amt - pre_amt
        if delta == 0:
            continue

        decimals = int(
            ((post.get("uiTokenAmount") or pre.get("uiTokenAmount") or {})).get("decimals") or 0
        )
        mint_changes.setdefault(mint, []).append(
            {"ata": ata_addr, "owner": owner, "delta": delta, "decimals": decimals}
        )

    # --- Pair decreases with increases per mint (transfer legs) ---
    for mint, changes in mint_changes.items():
        senders = [c for c in changes if c["delta"] < 0]
        receivers = [c for c in changes if c["delta"] > 0]
        symbol = _mint_label(mint)
        identity = resolve_canonical_asset_identity(
            blockchain="solana",
            asset_address=mint,
            symbol=symbol,
            token_standard="spl",
        )
        canonical = identity.canonical_asset_id

        # Pair senders and receivers to avoid cartesian inflation.
        # 1:1 by index when counts match; otherwise map each receiver to the
        # first (or sole) sender and use the receiver's own delta as amount.
        for i, receiver in enumerate(receivers):
            if not senders:
                continue
            amount_raw = receiver["delta"]
            decimals = receiver["decimals"]
            amount_norm = amount_raw / (10 ** decimals) if decimals else float(amount_raw)

            sender = senders[i] if len(senders) == len(receivers) else senders[0]
            from_addr = sender["owner"] or sender["ata"]
            to_addr = receiver["owner"] or receiver["ata"]
            if not from_addr or not to_addr:
                continue

            token_rows.append({
                "tx_hash": sig,
                "from_address": from_addr,
                "to_address": to_addr,
                "asset_symbol": symbol,
                "asset_contract": mint,
                "canonical_asset_id": canonical,
                "amount_raw": amount_raw,
                "amount_normalized": amount_norm,
                "timestamp": ts,
            })

    # --- Native SOL + instruction data extraction ---
    # Walk outer instructions first, then inner instruction groups.
    # For each instruction: collect native SOL transfers and raw instruction
    # data bytes for unrecognised programs (those that return a ``data``
    # field rather than a ``parsed`` object in jsonParsed mode).
    outer_ixs: List[Dict] = list(message.get("instructions") or [])
    flat_ixs: List[Tuple[int, Dict]] = [(i, ix) for i, ix in enumerate(outer_ixs)]
    for inner in (meta.get("innerInstructions") or []):
        base_idx = inner.get("index", 0)
        for j, ix in enumerate(inner.get("instructions") or []):
            # Use a composite index: outer_index * 1000 + inner_position
            flat_ixs.append((base_idx * 1000 + j + 1, ix))

    for ix_index, ix in flat_ixs:
        program_id = ix.get("programId", "")

        # Native SOL transfers (system program).
        if program_id == _SYSTEM_PROGRAM:
            parsed = ix.get("parsed") or {}
            if isinstance(parsed, dict) and parsed.get("type") == "transfer":
                info = parsed.get("info") or {}
                source = info.get("source")
                dest = info.get("destination")
                lamports = info.get("lamports", 0)
                if source and dest and lamports > 0:
                    native_rows.append({
                        "tx_hash": sig,
                        "from_address": source,
                        "to_address": dest,
                        "value_raw": lamports,
                        "value_native": lamports / 1e9,
                        "timestamp": ts,
                        "transfer_index": len([r for r in native_rows if r["tx_hash"] == sig]),
                    })
            continue

        # Extract raw instruction bytes for unrecognised programs.
        # In jsonParsed mode, recognised programs return ``parsed``; others
        # return the raw instruction as a base58-encoded ``data`` string.
        raw_b58 = ix.get("data")
        if raw_b58 and not ix.get("parsed") and program_id:
            try:
                from src.trace_compiler.calldata.solana_decoder import b58decode
                data_bytes = b58decode(raw_b58)
                if len(data_bytes) > 8:   # must have more than just the discriminator
                    ix_rows.append({
                        "tx_signature": sig,
                        "ix_index": ix_index,
                        "program_id": program_id,
                        "data_bytes": data_bytes,
                        "timestamp": ts,
                    })
            except Exception:
                pass

    return token_rows, native_rows, ata_entries, ix_rows


async def _persist(
    address: str,
    token_rows: List[Dict],
    native_rows: List[Dict],
    ata_entries: List[Tuple[str, str, str]],
    ix_rows: List[Dict],
    pg_pool: Any,
) -> int:
    """Insert fetched rows into the event store tables.

    Args:
        address:     Queried address (used only for logging).
        token_rows:  Parsed SPL token transfer rows.
        native_rows: Parsed native SOL transfer rows.
        ata_entries: (ata_address, owner_address, mint_address) triples.
        ix_rows:     Raw instruction data rows for unrecognised programs.
                     Stored in ``raw_solana_instructions`` with
                     ``decode_status='raw'`` so the bridge hop compiler can
                     apply heuristic destination extraction later.
        pg_pool:     asyncpg pool.

    Returns:
        Total number of rows successfully inserted.
    """
    stored = 0
    try:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():

                # --- ATA owner cache ---
                for ata_addr, owner, mint in ata_entries:
                    try:
                        await conn.execute(
                            """
                            INSERT INTO solana_ata_owners
                                (ata_address, owner_address, mint_address, resolved_at)
                            VALUES ($1, $2, $3, NOW())
                            ON CONFLICT (ata_address) DO NOTHING
                            """,
                            ata_addr, owner, mint or "",
                        )
                    except Exception:
                        pass

                # --- SPL token transfers ---
                # Assign stable per-tx transfer indices for the conflict key.
                tx_transfer_counters: Dict[str, int] = {}
                for row in token_rows:
                    tx_hash = row["tx_hash"]
                    idx = tx_transfer_counters.get(tx_hash, 0)
                    tx_transfer_counters[tx_hash] = idx + 1
                    try:
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
                            "solana",
                            tx_hash,
                            idx,
                            row["asset_symbol"],
                            row["asset_contract"],
                            row["canonical_asset_id"],
                            row["from_address"],
                            row["to_address"],
                            row["amount_raw"],
                            row["amount_normalized"],
                            row["timestamp"],
                        )
                        if result != "INSERT 0 0":
                            stored += 1
                    except Exception as exc:
                        logger.debug(
                            "solana_live_fetch: skip token transfer %s: %s",
                            tx_hash[:20], exc,
                        )

                # --- Raw instruction data (for bridge destination decoding) ---
                # Store raw bytes as hex inside decoded_args JSONB so the bridge
                # hop compiler can retrieve them later without touching raw_transactions
                # (which has a UNIQUE constraint on (blockchain, tx_hash) and would
                # conflict with real SOL transfer rows for the same tx_hash).
                for row in ix_rows:
                    sig = row["tx_signature"]
                    prog = row["program_id"]
                    ts = row["timestamp"]
                    idx = row["ix_index"]
                    data = row["data_bytes"]
                    args_json = json.dumps({"raw_data": data.hex()})
                    try:
                        await conn.execute(
                            """
                            INSERT INTO raw_solana_instructions
                                (tx_signature, ix_index, program_id,
                                 decoded_args, decode_status, timestamp)
                            VALUES ($1, $2, $3, $4::jsonb, 'raw', $5)
                            ON CONFLICT (tx_signature, ix_index) DO NOTHING
                            """,
                            sig, idx, prog, args_json, ts,
                        )
                    except Exception as exc:
                        logger.debug(
                            "solana_live_fetch: skip ix row %s[%s]: %s",
                            sig[:20], idx, exc,
                        )

                # --- Native SOL ---
                for row in native_rows:
                    try:
                        result = await conn.execute(
                            """
                            INSERT INTO raw_transactions
                                (blockchain, tx_hash, transfer_index, block_number, timestamp,
                                 from_address, to_address,
                                 value_raw, value_native, status)
                            VALUES ($1, $2, $3, NULL, $4, $5, $6, $7, $8, 'success')
                            ON CONFLICT (blockchain, tx_hash, transfer_index) DO NOTHING
                            """,
                            "solana",
                            row["tx_hash"],
                            row.get("transfer_index", 0),
                            row["timestamp"],
                            row["from_address"],
                            row["to_address"],
                            row["value_raw"],
                            row["value_native"],
                        )
                        if result != "INSERT 0 0":
                            stored += 1
                    except Exception as exc:
                        logger.debug(
                            "solana_live_fetch: skip native tx %s: %s",
                            row["tx_hash"][:20], exc,
                        )

    except Exception as exc:
        logger.warning(
            "solana_live_fetch: transaction block failed for %s: %s", address, exc
        )

    return stored


async def _mark_queue(
    address: str,
    pg_pool: Any,
    status: str,
    error: Optional[str],
    tx_count: Optional[int] = None,
) -> None:
    """Update the ``address_ingest_queue`` row after a live fetch attempt.

    Args:
        address:  Solana address being ingested.
        pg_pool:  asyncpg pool (no-op if None).
        status:   New status — ``'completed'`` or ``'failed'``.
        error:    Error message for failed rows; None on success.
        tx_count: Number of records stored.
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
                WHERE address    = $4
                  AND blockchain = 'solana'
                  AND status IN ('pending', 'running')
                """,
                status,
                tx_count,
                error,
                address,
            )
    except Exception as exc:
        logger.warning(
            "solana_live_fetch: failed to update queue for %s: %s", address, exc
        )


def supported_chain(chain: str) -> bool:
    """Return True if this module supports the given chain."""
    return chain == "solana"
