"""
Jackdaw Sentry - Bitcoin JSON-RPC Client
Lightweight async client for Bitcoin Core RPC and Blockstream API fallback.
Uses only aiohttp — no python-bitcoinlib dependency.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

import aiohttp

from src.api.config import settings
from src.collectors.base import Transaction, Block, Address
from src.collectors.rpc.base_rpc import BaseRPCClient, RPCError

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
        self.blockstream_url = (
            blockstream_url or settings.BLOCKSTREAM_API_URL
        ).rstrip("/")
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
                async with session.post(
                    self.rpc_url, json=payload, auth=auth
                ) as resp:
                    body = await resp.json(content_type=None)
                    if resp.status == 401:
                        raise RPCError(
                            "Authentication failed — check BITCOIN_RPC_USER/PASSWORD",
                            code=401,
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
                    self.metrics["last_request"] = datetime.now(timezone.utc).isoformat()
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
                    return self._parse_core_tx(raw)
            except RPCError:
                pass

        # Blockstream fallback
        data = await self._blockstream_get(f"/tx/{tx_hash}")
        if data is None:
            return None
        return self._parse_blockstream_tx(data)

    def _parse_core_tx(self, raw: Dict[str, Any]) -> Transaction:
        """Parse Bitcoin Core ``getrawtransaction`` verbose output."""
        timestamp = datetime.now(timezone.utc)
        if raw.get("time"):
            timestamp = datetime.fromtimestamp(raw["time"], tz=timezone.utc)

        # Sum outputs for value
        total_value = sum(
            vout.get("value", 0) for vout in raw.get("vout", [])
        )

        # Extract first input address and first output address
        from_addr = ""
        vin = raw.get("vin", [])
        if vin and vin[0].get("prevout", {}).get("scriptpubkey_address"):
            from_addr = vin[0]["prevout"]["scriptpubkey_address"]

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

    def _parse_blockstream_tx(self, data: Dict[str, Any]) -> Transaction:
        """Parse Blockstream ``/tx/{txid}`` response."""
        status_obj = data.get("status", {})
        confirmed = status_obj.get("confirmed", False)

        timestamp = datetime.now(timezone.utc)
        if status_obj.get("block_time"):
            timestamp = datetime.fromtimestamp(
                status_obj["block_time"], tz=timezone.utc
            )

        # Sum outputs
        total_value = sum(
            vout.get("value", 0) for vout in data.get("vout", [])
        ) / SATS_PER_BTC

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

        return Transaction(
            hash=data["txid"],
            blockchain="bitcoin",
            from_address=from_addr,
            to_address=to_addr or None,
            value=total_value,
            timestamp=timestamp,
            block_number=status_obj.get("block_height"),
            block_hash=status_obj.get("block_hash"),
            fee=fee,
            status="confirmed" if confirmed else "pending",
            confirmations=0,
        )

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

        tx_count = (
            chain_stats.get("tx_count", 0) + mempool_stats.get("tx_count", 0)
        )

        return Address(
            address=address,
            blockchain="bitcoin",
            balance=balance,
            transaction_count=tx_count,
            type="unknown",
        )

    async def get_address_transactions(
        self, address: str, *, limit: int = 25, offset: int = 0
    ) -> List[Transaction]:
        """Fetch recent transactions for an address via Blockstream API."""
        data = await self._blockstream_get(f"/address/{address}/txs")
        if not data:
            return []

        txs = []
        for item in data[:limit]:
            txs.append(self._parse_blockstream_tx(item))
        return txs

    # ------------------------------------------------------------------
    # Block
    # ------------------------------------------------------------------

    async def get_block(self, block_id: Union[int, str]) -> Optional[Block]:
        """Fetch a block by height (int) or hash (str)."""
        # Resolve height → hash if needed
        if isinstance(block_id, int):
            block_hash = await self._blockstream_get(
                f"/block-height/{block_id}"
            )
            if block_hash is None:
                return None
            # Blockstream returns the hash as plain text for this endpoint
            if isinstance(block_hash, str):
                block_id = block_hash
            else:
                return None

        data = await self._blockstream_get(f"/block/{block_id}")
        if data is None:
            return None

        timestamp = datetime.fromtimestamp(
            data.get("timestamp", 0), tz=timezone.utc
        )

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
