"""
Jackdaw Sentry - Solana JSON-RPC Client
Lightweight async client for Solana using JSON-RPC 2.0 via aiohttp.
"""

import logging
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from src.collectors.base import Address
from src.collectors.base import Block
from src.collectors.base import Transaction
from src.collectors.rpc.base_rpc import BaseRPCClient
from src.collectors.rpc.base_rpc import RPCError

logger = logging.getLogger(__name__)

# Lamport conversion: 1 SOL = 1e9 lamports
LAMPORTS_PER_SOL = 1_000_000_000


class SolanaRpcClient(BaseRPCClient):
    """Solana JSON-RPC 2.0 client using only aiohttp."""

    def __init__(self, rpc_url: str, blockchain: str = "solana", **kwargs):
        super().__init__(rpc_url, blockchain, **kwargs)

    # ------------------------------------------------------------------
    # Transaction
    # ------------------------------------------------------------------

    async def get_transaction(self, tx_hash: str) -> Optional[Transaction]:
        """Fetch a transaction by signature."""
        result = await self._json_rpc(
            "getTransaction",
            [tx_hash, {"encoding": "json", "maxSupportedTransactionVersion": 0}],
        )
        if result is None:
            return None

        meta = result.get("meta") or {}
        tx = result.get("transaction") or {}
        message = tx.get("message") or {}
        account_keys = message.get("accountKeys") or []

        # Extract sender/receiver from account keys (index 0 = fee payer/sender)
        from_address = account_keys[0] if account_keys else None
        to_address = account_keys[1] if len(account_keys) > 1 else None

        # Compute SOL transfer value from pre/post balances diff
        pre_balances = meta.get("preBalances") or []
        post_balances = meta.get("postBalances") or []
        value_lamports = 0
        if pre_balances and post_balances and len(pre_balances) > 1:
            value_lamports = max(0, pre_balances[0] - post_balances[0])

        block_time = result.get("blockTime")
        timestamp = (
            datetime.fromtimestamp(block_time, tz=timezone.utc)
            if block_time
            else datetime.now(timezone.utc)
        )

        fee_lamports = meta.get("fee", 0)
        slot = result.get("slot", 0)
        err = meta.get("err")

        return Transaction(
            hash=tx_hash,
            blockchain=self.blockchain,
            from_address=from_address,
            to_address=to_address,
            value=value_lamports / LAMPORTS_PER_SOL,
            timestamp=timestamp,
            block_number=slot,
            fee=fee_lamports / LAMPORTS_PER_SOL,
            status="failed" if err else "confirmed",
        )

    # ------------------------------------------------------------------
    # Address
    # ------------------------------------------------------------------

    async def get_address_info(self, address: str) -> Optional[Address]:
        """Fetch SOL balance for an account."""
        result = await self._json_rpc("getBalance", [address])
        if result is None:
            return None

        balance_lamports = (
            result.get("value", 0) if isinstance(result, dict) else result
        )

        # Get transaction count via getSignaturesForAddress
        tx_count = 0
        try:
            sigs = await self._json_rpc(
                "getSignaturesForAddress", [address, {"limit": 1000}]
            )
            tx_count = len(sigs) if sigs else 0
        except Exception:
            pass

        return Address(
            address=address,
            blockchain=self.blockchain,
            balance=balance_lamports / LAMPORTS_PER_SOL,
            transaction_count=tx_count,
            type="account",
        )

    # ------------------------------------------------------------------
    # Address transactions
    # ------------------------------------------------------------------

    async def get_address_transactions(
        self, address: str, *, limit: int = 25, offset: int = 0
    ) -> List[Transaction]:
        """Fetch recent transaction signatures for an address, then hydrate each."""
        sigs_result = await self._json_rpc(
            "getSignaturesForAddress",
            [address, {"limit": min(limit + offset, 1000)}],
        )
        if not sigs_result:
            return []

        sigs = [item["signature"] for item in sigs_result if item.get("signature")]
        sigs = sigs[offset : offset + limit]

        transactions = []
        for sig in sigs:
            try:
                tx = await self.get_transaction(sig)
                if tx:
                    transactions.append(tx)
            except Exception as exc:
                logger.debug(f"[solana] Failed to fetch tx {sig}: {exc}")

        return transactions

    # ------------------------------------------------------------------
    # Block
    # ------------------------------------------------------------------

    async def get_block(self, block_id: Union[int, str]) -> Optional[Block]:
        """Fetch a block by slot number."""
        slot = int(block_id)
        result = await self._json_rpc(
            "getBlock",
            [
                slot,
                {
                    "encoding": "json",
                    "maxSupportedTransactionVersion": 0,
                    "transactionDetails": "signatures",
                },
            ],
        )
        if result is None:
            return None

        block_time = result.get("blockTime")
        timestamp = (
            datetime.fromtimestamp(block_time, tz=timezone.utc)
            if block_time
            else datetime.now(timezone.utc)
        )
        signatures = result.get("signatures") or []

        return Block(
            number=slot,
            hash=result.get("blockhash", ""),
            blockchain=self.blockchain,
            timestamp=timestamp,
            transaction_count=len(signatures),
            parent_hash=result.get("previousBlockhash"),
        )

    # ------------------------------------------------------------------
    # Latest block number (slot)
    # ------------------------------------------------------------------

    async def get_latest_block_number(self) -> int:
        """Return the current slot number."""
        result = await self._json_rpc("getSlot", [])
        return int(result) if result is not None else 0
