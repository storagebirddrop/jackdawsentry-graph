"""
Jackdaw Sentry - Tron REST API Client
Lightweight async client for the Tron network using the TronGrid REST API.
Tron uses a REST-style API (not JSON-RPC 2.0).
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

# Tron unit conversion: 1 TRX = 1e6 SUN
SUN_PER_TRX = 1_000_000


class TronRpcClient(BaseRPCClient):
    """Tron REST API client using TronGrid endpoints."""

    def __init__(self, rpc_url: str, blockchain: str = "tron", **kwargs):
        super().__init__(rpc_url, blockchain, **kwargs)

    # ------------------------------------------------------------------
    # Internal REST helper (override _json_rpc for Tron's REST format)
    # ------------------------------------------------------------------

    async def _rest_get(self, path: str) -> Optional[Dict[str, Any]]:
        """Perform a GET request to the Tron REST API."""
        await self._wait_for_rate_limit()
        session = await self._ensure_session()
        url = f"{self.rpc_url}/{path.lstrip('/')}"
        try:
            async with session.get(url) as resp:
                body = await resp.json(content_type=None)
                if resp.status != 200:
                    raise RPCError(
                        f"HTTP {resp.status}",
                        code=resp.status,
                        blockchain=self.blockchain,
                    )
                return body
        except RPCError:
            raise
        except Exception as exc:
            raise RPCError(str(exc), blockchain=self.blockchain)

    async def _rest_post(
        self, path: str, payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Perform a POST request to the Tron REST API."""
        await self._wait_for_rate_limit()
        session = await self._ensure_session()
        url = f"{self.rpc_url}/{path.lstrip('/')}"
        try:
            async with session.post(url, json=payload) as resp:
                body = await resp.json(content_type=None)
                if resp.status != 200:
                    raise RPCError(
                        f"HTTP {resp.status}",
                        code=resp.status,
                        blockchain=self.blockchain,
                    )
                return body
        except RPCError:
            raise
        except Exception as exc:
            raise RPCError(str(exc), blockchain=self.blockchain)

    # ------------------------------------------------------------------
    # Transaction
    # ------------------------------------------------------------------

    async def get_transaction(self, tx_hash: str) -> Optional[Transaction]:
        """Fetch a transaction by ID from /wallet/gettransactionbyid."""
        try:
            result = await self._rest_post(
                "wallet/gettransactionbyid", {"value": tx_hash}
            )
        except Exception as exc:
            logger.warning(f"[tron] get_transaction failed: {exc}")
            return None

        if not result or not result.get("txID"):
            return None

        raw = result.get("raw_data") or {}
        contract = (raw.get("contract") or [{}])[0]
        contract_value = (contract.get("parameter") or {}).get("value") or {}

        from_address = contract_value.get("owner_address", "")
        to_address = contract_value.get("to_address", "")
        amount_sun = contract_value.get("amount", 0)

        # Tron timestamp is in milliseconds
        ts_ms = raw.get("timestamp", 0)
        timestamp = (
            datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            if ts_ms
            else datetime.now(timezone.utc)
        )

        ret = (result.get("ret") or [{}])[0]
        status = "confirmed" if ret.get("contractRet") == "SUCCESS" else "failed"
        fee_sun = result.get("fee", 0) or 0

        return Transaction(
            hash=tx_hash,
            blockchain=self.blockchain,
            from_address=from_address or None,
            to_address=to_address or None,
            value=amount_sun / SUN_PER_TRX,
            timestamp=timestamp,
            fee=fee_sun / SUN_PER_TRX,
            status=status,
        )

    # ------------------------------------------------------------------
    # Address
    # ------------------------------------------------------------------

    async def get_address_info(self, address: str) -> Optional[Address]:
        """Fetch TRX balance via /wallet/getaccount."""
        try:
            result = await self._rest_post(
                "wallet/getaccount", {"address": address, "visible": True}
            )
        except Exception as exc:
            logger.warning(f"[tron] get_address_info failed: {exc}")
            return None

        if not result:
            return None

        balance_sun = result.get("balance", 0) or 0

        return Address(
            address=address,
            blockchain=self.blockchain,
            balance=balance_sun / SUN_PER_TRX,
            transaction_count=0,
            type="account",
        )

    # ------------------------------------------------------------------
    # Address transactions (not natively supported via basic REST)
    # ------------------------------------------------------------------

    async def get_address_transactions(
        self, address: str, *, limit: int = 25, offset: int = 0
    ) -> List[Transaction]:
        """Tron basic REST API does not support tx history; returns empty list."""
        return []

    # ------------------------------------------------------------------
    # Block
    # ------------------------------------------------------------------

    async def get_block(self, block_id: Union[int, str]) -> Optional[Block]:
        """Fetch a block by number via /wallet/getblockbynum."""
        try:
            result = await self._rest_post(
                "wallet/getblockbynum", {"num": int(block_id)}
            )
        except Exception as exc:
            logger.warning(f"[tron] get_block failed: {exc}")
            return None

        if not result:
            return None

        header = (result.get("block_header") or {}).get("raw_data") or {}
        ts_ms = header.get("timestamp", 0)
        timestamp = (
            datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            if ts_ms
            else datetime.now(timezone.utc)
        )
        block_num = header.get("number", int(block_id))
        txs = result.get("transactions") or []

        return Block(
            number=block_num,
            hash=result.get("blockID", ""),
            blockchain=self.blockchain,
            timestamp=timestamp,
            transaction_count=len(txs),
            parent_hash=header.get("parentHash"),
        )

    # ------------------------------------------------------------------
    # Latest block number
    # ------------------------------------------------------------------

    async def get_latest_block_number(self) -> int:
        """Return the latest block number via /wallet/getnowblock."""
        try:
            result = await self._rest_get("wallet/getnowblock")
        except Exception as exc:
            raise RPCError(
                f"get_latest_block_number failed: {exc}", blockchain=self.blockchain
            )

        if not result:
            raise RPCError(
                "Empty response from getnowblock", blockchain=self.blockchain
            )

        header = (result.get("block_header") or {}).get("raw_data") or {}
        return int(header.get("number", 0))
