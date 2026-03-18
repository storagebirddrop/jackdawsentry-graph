"""
Jackdaw Sentry - Sui Collector
Sui blockchain data collection via JSON-RPC over HTTP.
"""

import asyncio
import logging
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

from src.api.config import settings

from .base import Address
from .base import BaseCollector
from .base import Block
from .base import Transaction

logger = logging.getLogger(__name__)

MIST_PER_SUI = 1_000_000_000


class SuiCollector(BaseCollector):
    """Sui collector using the public JSON-RPC endpoint."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__("sui", config)
        self.rpc_url = config.get("rpc_url", settings.SUI_RPC_URL)
        self.network = config.get("network", settings.SUI_NETWORK)
        self.session = None
        self._request_id = 0

    async def connect(self) -> bool:
        if not AIOHTTP_AVAILABLE:
            logger.warning("aiohttp not available, skipping Sui connection")
            return False

        try:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"Content-Type": "application/json"},
            )
            latest = await self.rpc_call("sui_getLatestCheckpointSequenceNumber")
            if latest is not None:
                logger.info("Connected to Sui %s", self.network)
                return True
        except Exception as exc:
            logger.error("Failed to connect to Sui: %s", exc)
        return False

    async def disconnect(self):
        if self.session:
            await self.session.close()

    async def rpc_call(self, method: str, params: Optional[List[Any]] = None) -> Any:
        """Perform a Sui JSON-RPC call."""
        if not self.session:
            return None

        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or [],
        }

        try:
            async with self.session.post(self.rpc_url, json=payload) as response:
                if response.status != 200:
                    logger.error("Sui RPC error %s for %s", response.status, method)
                    return None

                body = await response.json(content_type=None)
                if body.get("error"):
                    logger.error("Sui RPC %s failed: %s", method, body["error"])
                    return None
                return body.get("result")
        except Exception as exc:
            logger.error("Sui RPC call failed for %s: %s", method, exc)
            return None

    def _parse_timestamp_ms(self, raw: Optional[Union[str, int]]) -> datetime:
        if raw is None:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
        except (TypeError, ValueError):
            return datetime.now(timezone.utc)

    def _mist_to_sui(self, raw: Optional[Union[str, int]]) -> float:
        try:
            return int(raw or 0) / MIST_PER_SUI
        except (TypeError, ValueError):
            return 0.0

    async def get_latest_block_number(self) -> int:
        latest = await self.rpc_call("sui_getLatestCheckpointSequenceNumber")
        try:
            return int(latest or 0)
        except (TypeError, ValueError):
            return 0

    async def get_block(self, block_number: int) -> Optional[Block]:
        checkpoint = await self.rpc_call("sui_getCheckpoint", [str(block_number)])
        if not checkpoint:
            return None

        txs = checkpoint.get("transactions") or []
        return Block(
            hash=checkpoint.get("digest", ""),
            blockchain=self.blockchain,
            number=int(checkpoint.get("sequenceNumber", block_number)),
            timestamp=self._parse_timestamp_ms(checkpoint.get("timestampMs")),
            transaction_count=len(txs),
            parent_hash=checkpoint.get("previousDigest"),
            miner=checkpoint.get("validatorSignature"),
            difficulty=None,
            size=len(str(checkpoint)),
        )

    async def get_block_transactions(self, block_number: int) -> List[str]:
        checkpoint = await self.rpc_call("sui_getCheckpoint", [str(block_number)])
        if not checkpoint:
            return []
        return [tx for tx in checkpoint.get("transactions") or [] if isinstance(tx, str)]

    async def get_transaction(self, tx_hash: str) -> Optional[Transaction]:
        tx = await self.rpc_call(
            "sui_getTransactionBlock",
            [
                tx_hash,
                {
                    "showInput": True,
                    "showEffects": True,
                    "showBalanceChanges": True,
                    "showEvents": True,
                },
            ],
        )
        if not tx:
            return None

        transaction_data = (tx.get("transaction") or {}).get("data") or {}
        sender = transaction_data.get("sender")
        gas_data = transaction_data.get("gasData") or {}
        payments = gas_data.get("payment") or []
        recipient = payments[0].get("owner") if payments else None

        value = 0.0
        balance_changes = tx.get("balanceChanges") or []
        if balance_changes:
            value = self._mist_to_sui(balance_changes[0].get("amount"))

        effects = tx.get("effects") or {}
        checkpoint = effects.get("checkpoint")
        status = ((effects.get("status") or {}).get("status") or "").lower()

        return Transaction(
            hash=tx_hash,
            blockchain=self.blockchain,
            from_address=sender,
            to_address=recipient,
            value=value,
            timestamp=self._parse_timestamp_ms(tx.get("timestampMs")),
            block_number=int(checkpoint) if checkpoint is not None else None,
            fee=self._mist_to_sui(((effects.get("gasUsed") or {}).get("computationCost"))),
            status="confirmed" if status == "success" else "failed",
        )

    async def get_address_balance(self, address: str) -> float:
        result = await self.rpc_call("suix_getBalance", [address])
        if not result:
            return 0.0
        return self._mist_to_sui(result.get("totalBalance"))

    async def get_address_transactions(
        self, address: str, limit: int = 100
    ) -> List[Transaction]:
        result = await self.rpc_call(
            "suix_queryTransactionBlocks",
            [
                {"FromAddress": address},
                None,
                limit,
                True,
            ],
        )
        if not result:
            return []

        txs = []
        for item in result.get("data") or []:
            digest = item.get("digest")
            if not digest:
                continue
            tx = await self.get_transaction(digest)
            if tx:
                txs.append(tx)
        return txs
