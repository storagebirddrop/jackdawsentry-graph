"""
Jackdaw Sentry - XRPL JSON-RPC Client
Lightweight async client for XRP Ledger using its JSON-RPC-like API.
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

# Drop conversion: 1 XRP = 1e6 drops
DROPS_PER_XRP = 1_000_000

# XRPL epoch offset: XRPL timestamps are seconds since 2000-01-01T00:00:00Z
XRPL_EPOCH_OFFSET = 946684800


def _xrpl_ts_to_datetime(xrpl_ts: Optional[int]) -> datetime:
    """Convert XRPL epoch timestamp to UTC datetime."""
    if xrpl_ts is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(xrpl_ts + XRPL_EPOCH_OFFSET, tz=timezone.utc)


class XrplRpcClient(BaseRPCClient):
    """XRPL JSON-RPC client using only aiohttp."""

    def __init__(self, rpc_url: str, blockchain: str = "xrpl", **kwargs):
        super().__init__(rpc_url, blockchain, **kwargs)

    # ------------------------------------------------------------------
    # XRPL-specific RPC helper
    # ------------------------------------------------------------------

    async def _xrpl_rpc(self, method: str, params: Dict[str, Any]) -> Any:
        """Send an XRPL JSON-RPC request and return the result field."""
        payload = {"method": method, "params": [params]}
        raw = await self._post(payload)
        # XRPL wraps result in {"result": {"status": "success", ...}}
        if isinstance(raw, dict):
            result = raw.get("result") or raw
            if isinstance(result, dict) and result.get("status") == "error":
                raise RPCError(
                    result.get("error_message", result.get("error", "unknown error")),
                    blockchain=self.blockchain,
                )
            return result
        return raw

    # Override _post to not expect JSON-RPC 2.0 "result" field extraction
    async def _json_rpc(
        self, method: str, params: Any = None, *, retries: int = 2
    ) -> Any:
        """Not used directly; XRPL uses _xrpl_rpc instead."""
        raise NotImplementedError("Use _xrpl_rpc for XRPL")

    # ------------------------------------------------------------------
    # Transaction
    # ------------------------------------------------------------------

    async def get_transaction(self, tx_hash: str) -> Optional[Transaction]:
        """Fetch a transaction by hash via XRPL 'tx' command."""
        try:
            result = await self._xrpl_rpc(
                "tx", {"transaction": tx_hash, "binary": False}
            )
        except Exception as exc:
            logger.warning(f"[xrpl] get_transaction failed: {exc}")
            return None

        if not result or result.get("status") == "error":
            return None

        from_address = result.get("Account")
        to_address = result.get("Destination")
        amount_raw = result.get("Amount")

        # Amount can be a string (drops) or dict (IOU)
        value_xrp = 0.0
        if isinstance(amount_raw, str):
            value_xrp = int(amount_raw) / DROPS_PER_XRP
        elif isinstance(amount_raw, dict):
            # IOU token transfer
            try:
                value_xrp = float(amount_raw.get("value", 0))
            except (ValueError, TypeError):
                value_xrp = 0.0

        date_xrpl = result.get("date")
        timestamp = _xrpl_ts_to_datetime(date_xrpl)

        fee_drops = result.get("Fee", "0")
        try:
            fee_xrp = int(fee_drops) / DROPS_PER_XRP
        except (ValueError, TypeError):
            fee_xrp = 0.0

        validated = result.get("validated", False)
        meta = result.get("meta") or {}
        tx_result = meta.get("TransactionResult", "")
        status = "confirmed" if (validated and tx_result == "tesSUCCESS") else "failed"

        ledger_index = result.get("ledger_index")

        return Transaction(
            hash=tx_hash,
            blockchain=self.blockchain,
            from_address=from_address,
            to_address=to_address,
            value=value_xrp,
            timestamp=timestamp,
            block_number=ledger_index,
            fee=fee_xrp,
            status=status,
        )

    # ------------------------------------------------------------------
    # Address
    # ------------------------------------------------------------------

    async def get_address_info(self, address: str) -> Optional[Address]:
        """Fetch XRP balance via account_info."""
        try:
            result = await self._xrpl_rpc(
                "account_info", {"account": address, "ledger_index": "validated"}
            )
        except Exception as exc:
            logger.warning(f"[xrpl] get_address_info failed: {exc}")
            return None

        if not result:
            return None

        account_data = result.get("account_data") or {}
        balance_drops = account_data.get("Balance", "0")
        try:
            balance_xrp = int(balance_drops) / DROPS_PER_XRP
        except (ValueError, TypeError):
            balance_xrp = 0.0

        tx_seq = account_data.get("Sequence", 0)

        return Address(
            address=address,
            blockchain=self.blockchain,
            balance=balance_xrp,
            transaction_count=tx_seq,
            type="account",
        )

    # ------------------------------------------------------------------
    # Address transactions
    # ------------------------------------------------------------------

    async def get_address_transactions(
        self, address: str, *, limit: int = 25, offset: int = 0
    ) -> List[Transaction]:
        """Fetch recent transactions for an address via account_tx."""
        try:
            result = await self._xrpl_rpc(
                "account_tx",
                {
                    "account": address,
                    "limit": min(limit + offset, 200),
                    "ledger_index_min": -1,
                },
            )
        except Exception as exc:
            logger.warning(f"[xrpl] get_address_transactions failed: {exc}")
            return []

        if not result:
            return []

        tx_list = result.get("transactions") or []
        tx_list = tx_list[offset : offset + limit]

        transactions = []
        for entry in tx_list:
            tx_data = entry.get("tx") or {}
            meta = entry.get("meta") or {}
            tx_hash = tx_data.get("hash", "")
            if not tx_hash:
                continue

            from_address = tx_data.get("Account")
            to_address = tx_data.get("Destination")
            amount_raw = tx_data.get("Amount")
            value_xrp = 0.0
            if isinstance(amount_raw, str):
                try:
                    value_xrp = int(amount_raw) / DROPS_PER_XRP
                except (ValueError, TypeError):
                    pass

            date_xrpl = tx_data.get("date")
            timestamp = _xrpl_ts_to_datetime(date_xrpl)
            fee_drops = tx_data.get("Fee", "0")
            try:
                fee_xrp = int(fee_drops) / DROPS_PER_XRP
            except (ValueError, TypeError):
                fee_xrp = 0.0

            tx_result = meta.get("TransactionResult", "")
            status = "confirmed" if tx_result == "tesSUCCESS" else "failed"

            transactions.append(
                Transaction(
                    hash=tx_hash,
                    blockchain=self.blockchain,
                    from_address=from_address,
                    to_address=to_address,
                    value=value_xrp,
                    timestamp=timestamp,
                    block_number=tx_data.get("ledger_index"),
                    fee=fee_xrp,
                    status=status,
                )
            )

        return transactions

    # ------------------------------------------------------------------
    # Block (ledger)
    # ------------------------------------------------------------------

    async def get_block(self, block_id: Union[int, str]) -> Optional[Block]:
        """Fetch a ledger by index."""
        try:
            result = await self._xrpl_rpc(
                "ledger",
                {"ledger_index": int(block_id), "transactions": False, "expand": False},
            )
        except Exception as exc:
            logger.warning(f"[xrpl] get_block failed: {exc}")
            return None

        if not result:
            return None

        ledger = result.get("ledger") or {}
        ledger_index = int(ledger.get("ledger_index", block_id))
        close_time = ledger.get("close_time")
        timestamp = _xrpl_ts_to_datetime(close_time)
        tx_count = ledger.get("transaction_count") or len(
            ledger.get("transactions") or []
        )

        return Block(
            number=ledger_index,
            hash=ledger.get("ledger_hash", ""),
            blockchain=self.blockchain,
            timestamp=timestamp,
            transaction_count=tx_count,
            parent_hash=ledger.get("parent_hash"),
        )

    # ------------------------------------------------------------------
    # Latest block number (ledger index)
    # ------------------------------------------------------------------

    async def get_latest_block_number(self) -> int:
        """Return the latest validated ledger index."""
        try:
            result = await self._xrpl_rpc("ledger", {"ledger_index": "validated"})
        except Exception as exc:
            raise RPCError(
                f"get_latest_block_number failed: {exc}", blockchain=self.blockchain
            )

        ledger = result.get("ledger") or result.get("ledger_hash") or {}
        if isinstance(ledger, dict):
            return int(ledger.get("ledger_index", 0))
        return int(result.get("ledger_index", 0))
