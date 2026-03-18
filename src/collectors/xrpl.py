"""
Jackdaw Sentry - XRPL Collector
XRP Ledger data collection via lightweight JSON-RPC.
"""

import logging
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from src.api.config import settings

from .base import BaseCollector
from .base import Block
from .base import Transaction
from .rpc.xrpl_rpc import XrplRpcClient

logger = logging.getLogger(__name__)


class XrplCollector(BaseCollector):
    """XRPL collector backed by the lightweight XRPL RPC client.

    The collector writes ``blockchain='xrp'`` so event-store partitions and the
    parity script stay consistent, while the manager may still expose it under
    the human-friendly ``xrpl`` key.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__("xrp", config)
        self.rpc_url = config.get("rpc_url", settings.XRPL_RPC_URL)
        self.network = config.get("network", settings.XRPL_NETWORK)
        self.client = XrplRpcClient(self.rpc_url, blockchain=self.blockchain)

    async def connect(self) -> bool:
        """Connect to the XRPL RPC endpoint."""
        try:
            healthy = await self.client.health_check()
            if healthy:
                logger.info("Connected to XRPL %s", self.network)
                return True
        except Exception as exc:
            logger.error("Failed to connect to XRPL: %s", exc)
        return False

    async def disconnect(self):
        """Close the XRPL client session."""
        await self.client.close()

    async def get_latest_block_number(self) -> int:
        """Get latest validated ledger index."""
        try:
            return await self.client.get_latest_block_number()
        except Exception as exc:
            logger.error("Error getting latest XRPL ledger: %s", exc)
            return 0

    async def get_block(self, block_number: int) -> Optional[Block]:
        """Get ledger by index."""
        try:
            return await self.client.get_block(block_number)
        except Exception as exc:
            logger.error("Error getting XRPL ledger %s: %s", block_number, exc)
            return None

    async def get_transaction(self, tx_hash: str) -> Optional[Transaction]:
        """Get transaction by hash."""
        try:
            return await self.client.get_transaction(tx_hash)
        except Exception as exc:
            logger.error("Error getting XRPL transaction %s: %s", tx_hash, exc)
            return None

    async def get_address_balance(self, address: str) -> float:
        """Get XRP balance for an account."""
        try:
            info = await self.client.get_address_info(address)
            if info is None:
                return 0.0
            return float(info.balance or 0.0)
        except Exception as exc:
            logger.error("Error getting XRPL balance for %s: %s", address, exc)
            return 0.0

    async def get_address_transactions(
        self, address: str, limit: int = 100
    ) -> List[Transaction]:
        """Get recent XRPL account transactions."""
        try:
            return await self.client.get_address_transactions(address, limit=limit)
        except Exception as exc:
            logger.error("Error getting XRPL transactions for %s: %s", address, exc)
            return []

    async def get_block_transactions(self, block_number: int) -> List[str]:
        """Get transaction hashes included in a ledger."""
        try:
            result = await self.client._xrpl_rpc(
                "ledger",
                {
                    "ledger_index": int(block_number),
                    "transactions": True,
                    "expand": False,
                },
            )
        except Exception as exc:
            logger.error("Error getting XRPL ledger transactions for %s: %s", block_number, exc)
            return []

        ledger = result.get("ledger") or {}
        transactions = ledger.get("transactions") or []
        return [tx for tx in transactions if isinstance(tx, str)]
