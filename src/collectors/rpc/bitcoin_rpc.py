"""
Jackdaw Sentry - Bitcoin JSON-RPC Client
Lightweight async client for Bitcoin Core RPC and Blockstream API fallback.
Uses only aiohttp — no python-bitcoinlib dependency.
"""

import logging
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

import aiohttp

from src.api.config import settings
from src.collectors.base import Address
from src.collectors.base import Block
from src.collectors.base import Transaction
from src.collectors.rpc.base_rpc import BaseRPCClient
from src.collectors.rpc.base_rpc import RPCError

logger = logging.getLogger(__name__)

# Satoshi conversion
SATS_PER_BTC = 100_000_000


class BitcoinRpcClient(BaseRPCClient):
    """Bitcoin JSON-RPC client with Blockstream API fallback.

    Bitcoin Core RPC requires authentication (``user:password``).  If the
    configured RPC URL uses a local node, auth credentials from config are
    injected.  If the node is unreachable, the client automatically falls
    back to the public Blockstream REST API for read-only lookups.
    """

    def __init__(
        self,
        rpc_url: str,
        blockchain: str = "bitcoin",
        *,
        rpc_user: Optional[str] = None,
        rpc_password: Optional[str] = None,
        blockstream_url: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(rpc_url, blockchain, **kwargs)
        self.rpc_user = rpc_user
        self.rpc_password = rpc_password
        self.blockstream_url = (blockstream_url or settings.BLOCKSTREAM_API_URL).rstrip(
            "/"
        )
        self._use_blockstream = False

    # ------------------------------------------------------------------
    # Override transport for Bitcoin Core (basic-auth)
    # ------------------------------------------------------------------

    async def _json_rpc(
        self, method: str, params: Any = None, *, retries: int = 2
    ) -> Any:
        """Bitcoin Core JSON-RPC with basic auth."""
        if self._use_blockstream:
            raise RPCError(
                "Blockstream mode — use REST helpers instead",
                blockchain=self.blockchain,
            )

        payload = {
            "jsonrpc": "1.0",
            "id": self._next_id(),
            "method": method,
            "params": params if params is not None else [],
        }

        await self._wait_for_rate_limit()
        session = await self._ensure_session()
        auth = None
        if self.rpc_user and self.rpc_password:
            auth = aiohttp.BasicAuth(self.rpc_user, self.rpc_password)

        last_exc: Optional[Exception] = None
        for attempt in range(1, retries + 2):
            try:
                async with session.post(self.rpc_url, json=payload, auth=auth) as resp:
                    if resp.status == 401:
                        raise RPCError(
                            "Authentication failed — check BITCOIN_RPC_USER/PASSWORD",
                            code=401,
                            blockchain=self.blockchain,
                        )
                    try:
                        body = await resp.json(content_type=None)
                    except Exception:
                        text = await resp.text()
                        raise RPCError(
                            f"Non-JSON response (HTTP {resp.status}): {text[:200]}",
                            code=resp.status,
                            blockchain=self.blockchain,
                        )
                    if "error" in body and body["error"]:
                        err = body["error"]
                        raise RPCError(
                            err.get("message", str(err)),
                            code=err.get("code", -1),
                            blockchain=self.blockchain,
                        )
                    self.metrics["requests_sent"] += 1
                    self.metrics["last_request"] = datetime.now(
                        timezone.utc
                    ).isoformat()
                    return body.get("result")
            except RPCError:
                raise
            except (aiohttp.ClientError, Exception) as exc:
                self.metrics["requests_failed"] += 1
                last_exc = exc
                if attempt <= retries:
                    import asyncio

                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))

        # Fallback to Blockstream
        logger.warning(
            f"[bitcoin] Core RPC unreachable after {retries + 1} attempts, "
            f"switching to Blockstream API"
        )
        self._use_blockstream = True
        raise RPCError(
            f"Bitcoin Core RPC failed: {last_exc}",
            blockchain=self.blockchain,
        )

    # ------------------------------------------------------------------
    # Blockstream REST helpers
    # ------------------------------------------------------------------

    async def _blockstream_get(self, path: str) -> Any:
        """GET from Blockstream API, return parsed JSON."""
        await self._wait_for_rate_limit()
        session = await self._ensure_session()
        url = f"{self.blockstream_url}{path}"
        try:
            async with session.get(url) as resp:
                if resp.status == 404:
                    return None
                if resp.status != 200:
                    text = await resp.text()
                    raise RPCError(
                        f"Blockstream HTTP {resp.status}: {text[:200]}",
                        code=resp.status,
                        blockchain=self.blockchain,
                    )
                self.metrics["requests_sent"] += 1
                self.metrics["last_request"] = datetime.now(timezone.utc).isoformat()
                return await resp.json(content_type=None)
        except RPCError:
            raise
        except (aiohttp.ClientError, Exception) as exc:
            self.metrics["requests_failed"] += 1
            raise RPCError(
                f"Blockstream request failed: {exc}",
                blockchain=self.blockchain,
            )

    async def _blockstream_get_text(self, path: str) -> Optional[str]:
        """GET from Blockstream API, return plain text response."""
        await self._wait_for_rate_limit()
        session = await self._ensure_session()
        url = f"{self.blockstream_url}{path}"
        try:
            async with session.get(url) as resp:
                if resp.status == 404:
                    return None
                if resp.status != 200:
                    text = await resp.text()
                    raise RPCError(
                        f"Blockstream HTTP {resp.status}: {text[:200]}",
                        code=resp.status,
                        blockchain=self.blockchain,
                    )
                self.metrics["requests_sent"] += 1
                self.metrics["last_request"] = datetime.now(timezone.utc).isoformat()
                return await resp.text()
        except RPCError:
            raise
        except (aiohttp.ClientError, Exception) as exc:
            self.metrics["requests_failed"] += 1
            raise RPCError(
                f"Blockstream request failed: {exc}",
                blockchain=self.blockchain,
            )

    # ------------------------------------------------------------------
    # Transaction
    # ------------------------------------------------------------------

    async def get_transaction(self, tx_hash: str) -> Optional[Transaction]:
        """Fetch a Bitcoin transaction by txid."""
        # Try Bitcoin Core RPC first
        if not self._use_blockstream:
            try:
                raw = await self._json_rpc("getrawtransaction", [tx_hash, True])
                if raw:
                    return await self._parse_core_tx(raw)
            except RPCError:
                pass

        # Blockstream fallback
        data = await self._blockstream_get(f"/tx/{tx_hash}")
        if data is None:
            return None
        return await self._parse_blockstream_tx(data)

    async def _parse_core_tx(self, raw: Dict[str, Any]) -> Transaction:
        """Parse Bitcoin Core ``getrawtransaction`` verbose output."""
        timestamp = datetime.now(timezone.utc)
        if raw.get("time"):
            timestamp = datetime.fromtimestamp(raw["time"], tz=timezone.utc)

        # Sum outputs for value (Bitcoin Core returns values in BTC)
        total_value = sum(vout.get("value", 0) for vout in raw.get("vout", []))

        # Extract first input address and first output address
        from_addr = ""
        vin = raw.get("vin", [])
        if vin and vin[0].get("prevout", {}).get("scriptpubkey_address"):
            from_addr = vin[0]["prevout"]["scriptpubkey_address"]
        elif vin and "txid" in vin[0] and "vout" in vin[0]:
            # Fallback: fetch the previous output to resolve the sender
            try:
                prev_tx = await self._json_rpc(
                    "getrawtransaction", [vin[0]["txid"], True]
                )
                if prev_tx:
                    prev_vout = prev_tx.get("vout", [])
                    idx = vin[0]["vout"]
                    if idx < len(prev_vout):
                        spk = prev_vout[idx].get("scriptPubKey", {})
                        addrs = spk.get("addresses", []) or spk.get("address", "")
                        if isinstance(addrs, list) and addrs:
                            from_addr = addrs[0]
                        elif isinstance(addrs, str):
                            from_addr = addrs
            except Exception as exc:
                logger.debug(f"prevout fallback failed for {vin[0].get('txid')}: {exc}")

        to_addr = ""
        vout = raw.get("vout", [])
        if vout:
            spk = vout[0].get("scriptPubKey", {})
            addrs = spk.get("addresses", []) or spk.get("address", "")
            if isinstance(addrs, list) and addrs:
                to_addr = addrs[0]
            elif isinstance(addrs, str):
                to_addr = addrs

        confirmations = raw.get("confirmations", 0)
        status = "confirmed" if confirmations > 0 else "pending"

        return Transaction(
            hash=raw["txid"],
            blockchain="bitcoin",
            from_address=from_addr,
            to_address=to_addr or None,
            value=total_value,
            timestamp=timestamp,
            block_number=None,
            block_hash=raw.get("blockhash"),
            fee=None,
            status=status,
            confirmations=confirmations,
        )

    async def _parse_blockstream_tx(self, data: Dict[str, Any]) -> Transaction:
        """Parse Blockstream ``/tx/{txid}`` response."""
        status_obj = data.get("status", {})
        confirmed = status_obj.get("confirmed", False)

        timestamp = datetime.now(timezone.utc)
        if status_obj.get("block_time"):
            timestamp = datetime.fromtimestamp(
                status_obj["block_time"], tz=timezone.utc
            )

        # Sum outputs (Blockstream returns values in satoshis; convert to BTC)
        total_value = (
            sum(vout.get("value", 0) for vout in data.get("vout", [])) / SATS_PER_BTC
        )

        # First input address
        from_addr = ""
        vin = data.get("vin", [])
        if vin and vin[0].get("prevout", {}).get("scriptpubkey_address"):
            from_addr = vin[0]["prevout"]["scriptpubkey_address"]

        # First output address
        to_addr = ""
        vout = data.get("vout", [])
        if vout and vout[0].get("scriptpubkey_address"):
            to_addr = vout[0]["scriptpubkey_address"]

        fee = data.get("fee", 0) / SATS_PER_BTC if data.get("fee") else None

        # Compute confirmations from current tip if block height is known
        block_height = status_obj.get("block_height")
        confirmations = None
        if confirmed and block_height is not None:
            tip = await self._get_current_block_height()
            if tip is not None:
                confirmations = max(tip - block_height + 1, 0)

        return Transaction(
            hash=data["txid"],
            blockchain="bitcoin",
            from_address=from_addr,
            to_address=to_addr or None,
            value=total_value,
            timestamp=timestamp,
            block_number=block_height,
            block_hash=status_obj.get("block_hash"),
            fee=fee,
            status="confirmed" if confirmed else "pending",
            confirmations=confirmations,
        )

    async def _get_current_block_height(self) -> Optional[int]:
        """Get current blockchain tip height via Blockstream API.

        The ``/blocks/tip/height`` endpoint returns plain text, not JSON.
        """
        try:
            data = await self._blockstream_get_text("/blocks/tip/height")
            if data is not None:
                return int(data.strip())
        except (ValueError, TypeError) as exc:
            logger.debug(f"Failed to parse block height: {exc}")
        except Exception as exc:
            logger.debug(f"Failed to fetch current block height: {exc}")
        return None

    # ------------------------------------------------------------------
    # Address
    # ------------------------------------------------------------------

    async def get_address_info(self, address: str) -> Optional[Address]:
        """Fetch address balance and tx count via Blockstream API."""
        data = await self._blockstream_get(f"/address/{address}")
        if data is None:
            return None

        chain_stats = data.get("chain_stats", {})
        mempool_stats = data.get("mempool_stats", {})

        funded = chain_stats.get("funded_txo_sum", 0)
        spent = chain_stats.get("spent_txo_sum", 0)
        balance = (funded - spent) / SATS_PER_BTC

        tx_count = chain_stats.get("tx_count", 0) + mempool_stats.get("tx_count", 0)

        return Address(
            address=address,
            blockchain="bitcoin",
            balance=balance,
            transaction_count=tx_count,
            type="unknown",
        )

    async def get_address_transactions(
        self, address: str, *, limit: int = 25, last_seen_txid: Optional[str] = None
    ) -> List[Transaction]:
        """Fetch recent transactions for an address via Blockstream API.

        Returns one Transaction per output (UTXO) so all fund flows are
        visible in the graph — change addresses, multi-output sends, fees, etc.
        """
        path = f"/address/{address}/txs"
        if last_seen_txid:
            path += f"/chain/{last_seen_txid}"
        data = await self._blockstream_get(path)
        if not data:
            return []

        txs = []
        for item in data[:limit]:
            txs.extend(await self._parse_blockstream_tx_utxos(item, address))
        return txs

    async def _parse_blockstream_tx_utxos(
        self, data: Dict[str, Any], focus_address: str = ""
    ) -> List[Transaction]:
        """Expand a Blockstream tx into one Transaction per output.

        Each output becomes its own edge in the graph, giving a full UTXO
        picture: change address, send-to-many, OP_RETURN, etc.
        Fee is attached to the first output record as metadata only.
        """
        status_obj = data.get("status", {})
        confirmed = status_obj.get("confirmed", False)
        block_height = status_obj.get("block_height")

        timestamp = datetime.now(timezone.utc)
        if status_obj.get("block_time"):
            timestamp = datetime.fromtimestamp(
                status_obj["block_time"], tz=timezone.utc
            )

        fee_btc = data.get("fee", 0) / SATS_PER_BTC if data.get("fee") else None

        confirmations = None
        if confirmed and block_height is not None:
            tip = await self._get_current_block_height()
            if tip is not None:
                confirmations = max(tip - block_height + 1, 0)

        # Collect all input addresses (de-duplicated)
        from_addrs: List[str] = []
        for vin in data.get("vin", []):
            a = vin.get("prevout", {}).get("scriptpubkey_address", "")
            if a and a not in from_addrs:
                from_addrs.append(a)

        # Use primary sender as the canonical from_address
        primary_from = from_addrs[0] if from_addrs else ""

        # One Transaction per output
        result: List[Transaction] = []
        for vout in data.get("vout", []):
            to_addr = vout.get("scriptpubkey_address", "")
            if not to_addr:
                continue  # OP_RETURN or unspendable — skip

            value_btc = vout.get("value", 0) / SATS_PER_BTC

            result.append(
                Transaction(
                    hash=data["txid"],
                    blockchain="bitcoin",
                    from_address=primary_from,
                    to_address=to_addr,
                    value=value_btc,
                    timestamp=timestamp,
                    block_number=block_height,
                    block_hash=status_obj.get("block_hash"),
                    fee=fee_btc,
                    status="confirmed" if confirmed else "pending",
                    confirmations=confirmations,
                )
            )

        # Fallback: if no spendable outputs, return the old single-output form
        if not result:
            result = [await self._parse_blockstream_tx(data)]

        return result

    # ------------------------------------------------------------------
    # Block
    # ------------------------------------------------------------------

    async def get_block(self, block_id: Union[int, str]) -> Optional[Block]:
        """Fetch a block by height (int) or hash (str)."""
        # Resolve height → hash if needed
        if isinstance(block_id, int):
            # Blockstream returns the hash as plain text for this endpoint
            block_hash = await self._blockstream_get_text(f"/block-height/{block_id}")
            if block_hash is None:
                return None
            block_id = block_hash.strip()

        data = await self._blockstream_get(f"/block/{block_id}")
        if data is None:
            return None

        timestamp = datetime.fromtimestamp(data.get("timestamp", 0), tz=timezone.utc)

        return Block(
            hash=data.get("id", str(block_id)),
            blockchain="bitcoin",
            number=data.get("height", 0),
            timestamp=timestamp,
            transaction_count=data.get("tx_count", 0),
            parent_hash=data.get("previousblockhash"),
            miner=None,
            difficulty=str(data.get("difficulty", 0)),
            size=data.get("size"),
        )

    # ------------------------------------------------------------------
    # Chain tip
    # ------------------------------------------------------------------

    async def get_latest_block_number(self) -> int:
        """Return the current Bitcoin block height."""
        if not self._use_blockstream:
            try:
                info = await self._json_rpc("getblockchaininfo")
                return info.get("blocks", 0)
            except RPCError:
                pass

        data = await self._blockstream_get("/blocks/tip/height")
        if data is None:
            raise RPCError(
                "Could not determine block height",
                blockchain=self.blockchain,
            )
        return int(data)
