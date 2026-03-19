"""
Jackdaw Sentry - Starknet Collector
Starknet data collection via JSON-RPC over HTTP.
"""

import logging
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

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

WEI_PER_ETH = 10**18


class StarknetCollector(BaseCollector):
    """Minimal Starknet collector using HTTP JSON-RPC."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__("starknet", config)
        self.rpc_url = config.get("rpc_url", settings.STARKNET_RPC_URL)
        self.network = config.get("network", settings.STARKNET_NETWORK)
        self.session = None
        self._request_id = 0

    async def connect(self) -> bool:
        if not AIOHTTP_AVAILABLE:
            logger.warning("aiohttp not available, skipping Starknet connection")
            return False

        try:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"Content-Type": "application/json"},
            )
            latest = await self.rpc_call("starknet_blockNumber")
            if latest is not None:
                logger.info("Connected to Starknet %s", self.network)
                return True
        except Exception as exc:
            logger.error("Failed to connect to Starknet: %s", exc)
        return False

    async def disconnect(self):
        if self.session:
            await self.session.close()

    async def rpc_call(self, method: str, params: Optional[List[Any]] = None) -> Any:
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
                    logger.error("Starknet RPC error %s for %s", response.status, method)
                    return None

                body = await response.json(content_type=None)
                if body.get("error"):
                    logger.error("Starknet RPC %s failed: %s", method, body["error"])
                    return None
                return body.get("result")
        except Exception as exc:
            logger.error("Starknet RPC call failed for %s: %s", method, exc)
            return None

    def _parse_timestamp(self, raw: Optional[int]) -> datetime:
        if raw is None:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromtimestamp(int(raw), tz=timezone.utc)
        except (TypeError, ValueError):
            return datetime.now(timezone.utc)

    def _hex_to_int(self, value: Optional[str]) -> int:
        if not value:
            return 0
        try:
            return int(value, 16)
        except (TypeError, ValueError):
            return 0

    async def get_latest_block_number(self) -> int:
        latest = await self.rpc_call("starknet_blockNumber")
        try:
            return int(latest or 0)
        except (TypeError, ValueError):
            return 0

    async def get_block(self, block_number: int) -> Optional[Block]:
        block = await self.rpc_call(
            "starknet_getBlockWithTxHashes",
            [{"block_number": block_number}],
        )
        if not block:
            return None

        txs = block.get("transactions") or []
        return Block(
            hash=block.get("block_hash", ""),
            blockchain=self.blockchain,
            number=int(block.get("block_number", block_number)),
            timestamp=self._parse_timestamp(block.get("timestamp")),
            transaction_count=len(txs),
            parent_hash=block.get("parent_hash"),
            miner=block.get("sequencer_address"),
            difficulty=None,
            size=len(str(block)),
        )

    async def get_block_transactions(self, block_number: int) -> List[str]:
        block = await self.rpc_call(
            "starknet_getBlockWithTxHashes",
            [{"block_number": block_number}],
        )
        if not block:
            return []
        return [tx for tx in block.get("transactions") or [] if isinstance(tx, str)]

    async def get_transaction(self, tx_hash: str) -> Optional[Transaction]:
        tx = await self.rpc_call("starknet_getTransactionByHash", [tx_hash])
        if not tx:
            return None

        receipt = await self.rpc_call("starknet_getTransactionReceipt", [tx_hash]) or {}
        
        # Safely parse calldata - don't assume transfer structure
        calldata = tx.get("calldata") or []
        to_address = None
        amount = 0.0
        
        # Only attempt to parse as transfer if calldata has sufficient length
        # and transaction type is INVOKE (type 1)
        tx_type = tx.get("type") or tx.get("transaction_type")
        if tx_type in ("INVOKE", "INVOKE_FUNCTION", 1, "1") and len(calldata) >= 2:
            # Note: calldata[0] and calldata[1] are NOT guaranteed to be address/amount
            # This is a heuristic that may not work for all contracts
            # For safety, validate the first element looks like an address (hex string)
            potential_addr = calldata[0]
            if isinstance(potential_addr, str) and potential_addr.startswith("0x"):
                to_address = potential_addr
                # Only parse amount if second element exists and is numeric
                try:
                    amount = self._hex_to_int(calldata[1]) / WEI_PER_ETH
                except (TypeError, ValueError, IndexError):
                    amount = 0.0
        
        status = (receipt.get("execution_status") or "").upper()

        # Safely parse fee - handle both dict (new RPC) and hex string (legacy RPC)
        fee_data = receipt.get("actual_fee")
        fee_amount = 0.0
        if isinstance(fee_data, dict):
            fee_amount = self._hex_to_int(fee_data.get("amount")) / WEI_PER_ETH
        elif isinstance(fee_data, (str, bytes)):
            # Legacy RPC returns fee as hex string directly
            fee_amount = self._hex_to_int(fee_data) / WEI_PER_ETH

        return Transaction(
            hash=tx_hash,
            blockchain=self.blockchain,
            from_address=tx.get("sender_address") or tx.get("contract_address"),
            to_address=to_address,
            value=amount,
            timestamp=self._parse_timestamp(receipt.get("timestamp")),
            block_number=receipt.get("block_number"),
            block_hash=receipt.get("block_hash"),
            fee=fee_amount,
            status="confirmed" if status == "SUCCEEDED" else "failed",
        )

    async def get_address_balance(self, address: str) -> float:
        # Native balance requires a contract call against the ETH token contract.
        # Keep this lightweight collector focused on transaction ingestion first.
        return 0.0

    async def get_address_transactions(
        self, address: str, limit: int = 100
    ) -> List[Transaction]:
        # Starknet JSON-RPC does not provide address history without an indexer.
        return []
