"""
Jackdaw Sentry - EVM JSON-RPC Client
Lightweight async client for Ethereum and EVM-compatible chains.
Uses only aiohttp — no Web3.py dependency.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from src.collectors.base import Transaction, Block, Address
from src.collectors.rpc.base_rpc import BaseRPCClient, RPCError

logger = logging.getLogger(__name__)

# Wei conversion constants
WEI_PER_ETH = 10 ** 18
WEI_PER_GWEI = 10 ** 9

# Native currency symbols per chain
NATIVE_SYMBOL: Dict[str, str] = {
    "ethereum": "ETH",
    "bsc": "BNB",
    "polygon": "MATIC",
    "arbitrum": "ETH",
    "base": "ETH",
    "avalanche": "AVAX",
    "sei": "SEI",
    "plasma": "PLASMA",
}


def _hex_to_int(val: Optional[str]) -> int:
    """Convert a hex string (``0x...``) to int. Returns 0 on None/empty."""
    if not val:
        return 0
    return int(val, 16)


def _wei_to_native(wei_hex: Optional[str]) -> float:
    """Convert hex-encoded wei to native currency (ETH, BNB, etc.)."""
    return _hex_to_int(wei_hex) / WEI_PER_ETH


class EvmRpcClient(BaseRPCClient):
    """EVM JSON-RPC client for Ethereum, BSC, Polygon, Arbitrum, Base, Avalanche, etc."""

    def __init__(self, rpc_url: str, blockchain: str, **kwargs):
        super().__init__(rpc_url, blockchain, **kwargs)
        self.native_symbol = NATIVE_SYMBOL.get(blockchain, "ETH")

    # ------------------------------------------------------------------
    # Transaction
    # ------------------------------------------------------------------

    async def get_transaction(self, tx_hash: str) -> Optional[Transaction]:
        """Fetch a transaction by hash, including receipt for gas/status."""
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash

        tx_data = await self._json_rpc("eth_getTransactionByHash", [tx_hash])
        if tx_data is None:
            return None

        receipt = await self._json_rpc("eth_getTransactionReceipt", [tx_hash])

        block_number = _hex_to_int(tx_data.get("blockNumber"))
        block_hash = tx_data.get("blockHash")

        # Resolve timestamp from block
        timestamp = datetime.now(timezone.utc)
        if block_hash and block_hash != "0x" + "0" * 64:
            try:
                block_data = await self._json_rpc(
                    "eth_getBlockByHash", [block_hash, False]
                )
                if block_data and block_data.get("timestamp"):
                    timestamp = datetime.fromtimestamp(
                        _hex_to_int(block_data["timestamp"]), tz=timezone.utc
                    )
            except RPCError:
                pass

        gas_used = _hex_to_int(receipt.get("gasUsed")) if receipt else None
        gas_price = _hex_to_int(tx_data.get("gasPrice"))
        fee = (gas_used * gas_price / WEI_PER_ETH) if gas_used and gas_price else None

        status = "confirmed"
        if receipt:
            status = "confirmed" if _hex_to_int(receipt.get("status")) == 1 else "failed"
        elif block_number == 0:
            status = "pending"

        # Current block for confirmations
        confirmations = 0
        if block_number > 0:
            try:
                latest = await self.get_latest_block_number()
                confirmations = max(0, latest - block_number)
            except RPCError:
                pass

        # Parse token transfers from receipt logs (ERC-20 Transfer topic)
        token_transfers = []
        if receipt and receipt.get("logs"):
            token_transfers = self._parse_erc20_transfers(receipt["logs"])

        return Transaction(
            hash=tx_hash,
            blockchain=self.blockchain,
            from_address=(tx_data.get("from") or "").lower(),
            to_address=(tx_data.get("to") or "").lower() if tx_data.get("to") else None,
            value=_wei_to_native(tx_data.get("value")),
            timestamp=timestamp,
            block_number=block_number if block_number > 0 else None,
            block_hash=block_hash,
            gas_used=gas_used,
            gas_price=gas_price,
            fee=fee,
            status=status,
            confirmations=confirmations,
            contract_address=(
                receipt.get("contractAddress", "").lower()
                if receipt and receipt.get("contractAddress")
                else None
            ),
            token_transfers=token_transfers,
        )

    @staticmethod
    def _parse_erc20_transfers(logs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract ERC-20 Transfer events from receipt logs.

        Transfer(address indexed from, address indexed to, uint256 value)
        topic0 = 0xddf252ad...
        """
        transfer_topic = (
            "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
        )
        transfers = []
        for log in logs:
            topics = log.get("topics", [])
            if len(topics) >= 3 and topics[0] == transfer_topic:
                try:
                    from_addr = "0x" + topics[1][-40:]
                    to_addr = "0x" + topics[2][-40:]
                    raw_value = _hex_to_int(log.get("data", "0x0"))
                    transfers.append(
                        {
                            "contract": log.get("address", "").lower(),
                            "from": from_addr.lower(),
                            "to": to_addr.lower(),
                            "raw_value": str(raw_value),
                        }
                    )
                except (IndexError, ValueError):
                    continue
        return transfers

    # ------------------------------------------------------------------
    # Address
    # ------------------------------------------------------------------

    async def get_address_info(self, address: str) -> Optional[Address]:
        """Fetch address balance and detect contract vs. EOA."""
        if not address.startswith("0x"):
            address = "0x" + address
        address = address.lower()

        balance_hex = await self._json_rpc("eth_getBalance", [address, "latest"])
        balance = _wei_to_native(balance_hex)

        # Detect contract
        code = await self._json_rpc("eth_getCode", [address, "latest"])
        addr_type = "contract" if code and code != "0x" else "eoa"

        tx_count_hex = await self._json_rpc(
            "eth_getTransactionCount", [address, "latest"]
        )
        tx_count = _hex_to_int(tx_count_hex)

        return Address(
            address=address,
            blockchain=self.blockchain,
            balance=balance,
            transaction_count=tx_count,
            type=addr_type,
        )

    async def get_address_transactions(
        self, address: str, *, limit: int = 25, offset: int = 0
    ) -> List[Transaction]:
        """EVM JSON-RPC has no native 'list transactions by address'.

        Returns empty list — the caller should fall back to Neo4j or an
        indexed explorer API (Etherscan, etc.) for address history.
        """
        return []

    # ------------------------------------------------------------------
    # Block
    # ------------------------------------------------------------------

    async def get_block(self, block_id: Union[int, str]) -> Optional[Block]:
        """Fetch a block by number (int) or hash (hex string)."""
        if isinstance(block_id, int):
            block_data = await self._json_rpc(
                "eth_getBlockByNumber", [hex(block_id), False]
            )
        else:
            if not str(block_id).startswith("0x"):
                block_id = "0x" + str(block_id)
            block_data = await self._json_rpc(
                "eth_getBlockByHash", [block_id, False]
            )

        if block_data is None:
            return None

        timestamp = datetime.fromtimestamp(
            _hex_to_int(block_data.get("timestamp")), tz=timezone.utc
        )

        return Block(
            hash=block_data.get("hash", ""),
            blockchain=self.blockchain,
            number=_hex_to_int(block_data.get("number")),
            timestamp=timestamp,
            transaction_count=len(block_data.get("transactions", [])),
            parent_hash=block_data.get("parentHash"),
            miner=block_data.get("miner", ""),
            difficulty=str(_hex_to_int(block_data.get("difficulty"))),
            size=_hex_to_int(block_data.get("size")),
        )

    # ------------------------------------------------------------------
    # Chain tip
    # ------------------------------------------------------------------

    async def get_latest_block_number(self) -> int:
        """Return the latest block number."""
        result = await self._json_rpc("eth_blockNumber")
        return _hex_to_int(result)
