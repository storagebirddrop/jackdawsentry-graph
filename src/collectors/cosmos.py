"""
Jackdaw Sentry - Cosmos SDK Collector
Shared collector for Cosmos-family chains exposed through LCD/REST endpoints.
"""

import asyncio
import base64
import hashlib
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


class CosmosCollector(BaseCollector):
    """Collector for Cosmos-SDK chains via REST/LCD endpoints."""

    def __init__(self, blockchain: str, config: Dict[str, Any]):
        super().__init__(blockchain, config)
        self.rest_url = config.get("rest_url", "").rstrip("/")
        self.network = config.get("network", "mainnet")
        self.native_denom = config.get("native_denom", "")
        self.session = None

    async def connect(self) -> bool:
        if not AIOHTTP_AVAILABLE:
            logger.warning("aiohttp not available, skipping %s connection", self.blockchain)
            return False

        try:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
            latest = await self._get_json("/cosmos/base/tendermint/v1beta1/blocks/latest")
            if latest and latest.get("block"):
                logger.info("Connected to %s %s", self.blockchain, self.network)
                return True
        except Exception as exc:
            logger.error("Failed to connect to %s: %s", self.blockchain, exc)
        return False

    async def disconnect(self):
        if self.session:
            await self.session.close()

    async def _get_json(self, path: str) -> Optional[Dict[str, Any]]:
        if not self.session or not self.rest_url:
            return None
        url = f"{self.rest_url}{path}"
        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                logger.error("%s REST error %s for %s", self.blockchain, response.status, path)
        except Exception as exc:
            logger.error("%s REST request failed for %s: %s", self.blockchain, path, exc)
        return None

    def _tx_hash_from_base64(self, tx_b64: str) -> str:
        raw = base64.b64decode(tx_b64)
        return hashlib.sha256(raw).hexdigest().upper()

    def _parse_timestamp(self, timestamp: Optional[str]) -> datetime:
        if not timestamp:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)

    def _extract_value(self, message: Dict[str, Any]) -> float:
        amount = (
            message.get("amount")
            or message.get("token")
            or message.get("funds")
            or message.get("coins")
        )
        if isinstance(amount, dict):
            try:
                return float(amount.get("amount", 0))
            except (TypeError, ValueError):
                return 0.0
        if isinstance(amount, list) and amount:
            first = amount[0]
            if isinstance(first, dict):
                try:
                    return float(first.get("amount", 0))
                except (TypeError, ValueError):
                    return 0.0
        if isinstance(amount, (int, float, str)):
            try:
                return float(amount)
            except (TypeError, ValueError):
                return 0.0
        return 0.0

    def _extract_from_address(self, message: Dict[str, Any]) -> Optional[str]:
        for key in (
            "from_address",
            "sender",
            "delegator_address",
            "creator",
            "trader",
            "signer",
        ):
            if message.get(key):
                return message[key]
        return None

    def _extract_to_address(self, message: Dict[str, Any]) -> Optional[str]:
        for key in (
            "to_address",
            "receiver",
            "validator_address",
            "contract",
            "grantee",
        ):
            if message.get(key):
                return message[key]
        return None

    async def get_latest_block_number(self) -> int:
        latest = await self._get_json("/cosmos/base/tendermint/v1beta1/blocks/latest")
        if not latest:
            return 0
        header = (latest.get("block") or {}).get("header") or {}
        try:
            return int(header.get("height", 0))
        except (TypeError, ValueError):
            return 0

    async def get_block(self, block_number: int) -> Optional[Block]:
        payload = await self._get_json(f"/cosmos/base/tendermint/v1beta1/blocks/{block_number}")
        if not payload:
            return None

        block = payload.get("block") or {}
        header = block.get("header") or {}
        data = block.get("data") or {}
        txs = data.get("txs") or []

        return Block(
            hash=(payload.get("block_id") or {}).get("hash", ""),
            blockchain=self.blockchain,
            number=int(header.get("height", block_number)),
            timestamp=self._parse_timestamp(header.get("time")),
            transaction_count=len(txs),
            parent_hash=(header.get("last_block_id") or {}).get("hash"),
            miner=header.get("proposer_address"),
            difficulty=None,
            size=len(str(payload)),
        )

    async def get_block_transactions(self, block_number: int) -> List[str]:
        payload = await self._get_json(f"/cosmos/base/tendermint/v1beta1/blocks/{block_number}")
        if not payload:
            return []
        txs = ((payload.get("block") or {}).get("data") or {}).get("txs") or []
        return [self._tx_hash_from_base64(tx_b64) for tx_b64 in txs]

    async def get_transaction(self, tx_hash: str) -> Optional[Transaction]:
        payload = await self._get_json(f"/cosmos/tx/v1beta1/txs/{tx_hash}")
        if not payload:
            return None

        tx = payload.get("tx") or {}
        tx_response = payload.get("tx_response") or {}
        body = tx.get("body") or {}
        messages = body.get("messages") or []
        first_message = messages[0] if messages else {}
        auth_info = tx.get("auth_info") or {}
        fee_amounts = ((auth_info.get("fee") or {}).get("amount") or [])
        fee_value = 0.0
        if fee_amounts:
            try:
                fee_value = float((fee_amounts[0] or {}).get("amount", 0))
            except (TypeError, ValueError):
                fee_value = 0.0

        # Extract the short message type name from the first message's @type
        # field (e.g. "/osmosis.gamm.v1beta1.MsgSwapExactAmountIn" →
        # "MsgSwapExactAmountIn").  Used by CosmosChainCompiler for swap
        # detection without needing a separate RPC round-trip.
        raw_type = first_message.get("@type", "")
        msg_type = raw_type.rsplit(".", 1)[-1] if raw_type else None

        return Transaction(
            hash=tx_response.get("txhash", tx_hash),
            blockchain=self.blockchain,
            from_address=self._extract_from_address(first_message),
            to_address=self._extract_to_address(first_message),
            value=self._extract_value(first_message),
            timestamp=self._parse_timestamp(tx_response.get("timestamp")),
            block_number=int(tx_response.get("height", 0) or 0),
            fee=fee_value,
            status="confirmed" if str(tx_response.get("code", 0)) == "0" else "failed",
            tx_type=msg_type or None,
        )

    async def get_address_balance(self, address: str) -> float:
        payload = await self._get_json(f"/cosmos/bank/v1beta1/balances/{address}")
        if not payload:
            return 0.0

        balances = payload.get("balances") or []
        if not balances:
            return 0.0

        target = None
        if self.native_denom:
            target = next((b for b in balances if b.get("denom") == self.native_denom), None)
        if target is None:
            target = balances[0]
        try:
            return float(target.get("amount", 0))
        except (TypeError, ValueError):
            return 0.0

    async def get_address_transactions(
        self, address: str, limit: int = 100
    ) -> List[Transaction]:
        payload = await self._get_json(
            f"/cosmos/tx/v1beta1/txs?events=message.sender='{address}'&pagination.limit={limit}"
        )
        if not payload:
            return []

        txs = payload.get("txs") or []
        tx_responses = payload.get("tx_responses") or []
        parsed: List[Transaction] = []

        for tx, tx_response in zip(txs, tx_responses):
            body = tx.get("body") or {}
            messages = body.get("messages") or []
            first_message = messages[0] if messages else {}
            parsed.append(
                Transaction(
                    hash=tx_response.get("txhash", ""),
                    blockchain=self.blockchain,
                    from_address=self._extract_from_address(first_message),
                    to_address=self._extract_to_address(first_message),
                    value=self._extract_value(first_message),
                    timestamp=self._parse_timestamp(tx_response.get("timestamp")),
                    block_number=int(tx_response.get("height", 0) or 0),
                    fee=0.0,
                    status="confirmed" if str(tx_response.get("code", 0)) == "0" else "failed",
                )
            )

        return parsed
