"""
Jackdaw Sentry - Tron Collector
Tron blockchain data collection
"""

import asyncio
import base64
import hashlib
import json
import logging
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

# Try to import aiohttp, but don't fail if not available
try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

# Try to import base58, but don't fail if not available
try:
    import base58

    BASE58_AVAILABLE = True
except ImportError:
    BASE58_AVAILABLE = False

from src.api.config import settings

from .base import Address
from .base import BaseCollector
from .base import Block
from .base import Transaction

logger = logging.getLogger(__name__)


class TronCollector(BaseCollector):
    """Tron blockchain collector"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__("tron", config)
        self.rpc_url = config.get("rpc_url", settings.TRON_RPC_URL)
        self.network = config.get("network", settings.TRON_NETWORK)

        # Tron-specific settings
        self.trc20_tracking = config.get("trc20_tracking", True)
        self.contract_tracking = config.get("contract_tracking", True)

        # Tron stablecoin contracts
        self.stablecoin_contracts = {"USDT": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"}

        self.session = None

    async def connect(self) -> bool:
        """Connect to Tron RPC"""
        if not AIOHTTP_AVAILABLE:
            logger.warning("aiohttp not available, skipping Tron connection")
            return False

        try:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )

            # PublicNode serves useful data on getnowblock but may return an
            # empty object for getnodeinfo, so use latest block as the
            # connectivity check.
            latest_block = await self.rpc_call("wallet/getnowblock")
            if latest_block and latest_block.get("block_header"):
                logger.info(f"Connected to Tron {self.network}")
                return True

        except Exception as e:
            logger.error(f"Failed to connect to Tron: {e}")

        return False

    async def disconnect(self):
        """Disconnect from Tron RPC"""
        if self.session:
            await self.session.close()

    async def rpc_call(self, method: str, params: Dict = None) -> Optional[Dict]:
        """Make a Tron REST API call.

        Tron nodes expose REST-style wallet endpoints rather than JSON-RPC 2.0.
        """
        if not self.session:
            return None

        try:
            method_path = method.lstrip("/")
            url = f"{self.rpc_url.rstrip('/')}/{method_path}"
            payload = params or {}
            use_get = method_path in {"wallet/getnodeinfo", "wallet/getnowblock"} and not payload

            if use_get:
                request = self.session.get(url)
            else:
                request = self.session.post(
                    url, json=payload, headers={"Content-Type": "application/json"}
                )

            async with request as response:
                if response.status == 200:
                    result = await response.json()
                    return result
                else:
                    logger.error(f"Tron RPC error: {response.status}")

        except Exception as e:
            logger.error(f"Tron RPC call failed: {e}")

        return None

    async def get_latest_block_number(self) -> int:
        """Get latest block number"""
        try:
            info = await self.rpc_call("wallet/getnowblock")
            return (
                info.get("block_header", {}).get("raw_data", {}).get("number", 0)
                if info
                else 0
            )

        except Exception as e:
            logger.error(f"Error getting latest Tron block: {e}")
            return 0

    async def get_block(self, block_number: int) -> Optional[Block]:
        """Get block by number"""
        try:
            block_data = await self.rpc_call(
                "wallet/getblockbynum", {"num": block_number}
            )

            if not block_data:
                return None

            block_header = block_data.get("block_header", {}).get("raw_data", {})
            transactions = block_data.get("transactions", [])

            return Block(
                hash=block_header.get("txTrieRoot", ""),
                blockchain=self.blockchain,
                number=block_number,
                timestamp=datetime.fromtimestamp(
                    block_header.get("timestamp", 0) / 1000, tz=timezone.utc
                ),
                transaction_count=len(transactions),
                parent_hash=block_header.get("parentHash"),
                miner=block_header.get("witness_address", ""),
                difficulty=None,
                size=len(str(block_data)),
            )

        except Exception as e:
            logger.error(f"Error getting Tron block {block_number}: {e}")

        return None

    async def get_transaction(self, tx_hash: str) -> Optional[Transaction]:
        """Get transaction by hash"""
        try:
            tx_data = await self.rpc_call(
                "wallet/gettransactionbyid", {"value": tx_hash}
            )

            if not tx_data:
                return None

            raw_data = tx_data.get("raw_data", {})
            contract_data = raw_data.get("contract", [])

            # Parse transaction based on contract type
            from_address = raw_data.get("owner_address", "")
            to_address = None
            value = 0
            contract_address = None

            if contract_data:
                contract_type = contract_data[0].get("type", "")

                if contract_type == "TransferContract":
                    # TRX transfer
                    transfer = contract_data[0].get("value", {}).get("amount", 0)
                    to_address = contract_data[0].get("value", {}).get("to_address", "")
                    value = transfer / 1_000_000  # Convert from sun to TRX

                elif contract_type == "TransferAssetContract":
                    # TRC10 token transfer
                    asset_transfer = contract_data[0].get("value", {})
                    to_address = asset_transfer.get("to_address", "")
                    value = asset_transfer.get("amount", 0)

                elif contract_type == "TriggerSmartContract":
                    # TRC20 token transfer or contract interaction
                    trigger = contract_data[0].get("value", {})
                    contract_address = trigger.get("contract_address", "")
                    parameter = trigger.get("data", "")

                    # Parse TRC20 transfer(address,uint256) ABI call.
                    # Selector: a9059cbb (4 bytes / 8 hex chars).
                    # Layout after selector: 32-byte padded recipient + 32-byte amount.
                    # Total minimum data length: 8 + 64 + 64 = 136 hex chars.
                    if parameter.startswith("a9059cbb") and len(parameter) >= 136:
                        # Recipient: last 20 bytes of the first 32-byte word (40 hex chars).
                        # Prepend Tron network prefix 41 to get a full Tron hex address.
                        to_hex_raw = parameter[8 + 24 : 8 + 64]  # chars 32-71
                        to_address_hex = "41" + to_hex_raw
                        to_address = self.hex_to_base58(to_address_hex)
                        # Amount: second 32-byte word as big-endian integer (sun units).
                        amount_hex = parameter[72:136]
                        amount_raw = int(amount_hex, 16)
                        
                        # Determine decimals based on known stablecoin contracts.
                        # Known Tron stablecoins (USDT, USDC) use 6 decimals.
                        # Unknown TRC20 tokens default to 18 (ERC20 convention).
                        _known_stablecoin_addrs = set(self.stablecoin_contracts.values())
                        if contract_address in _known_stablecoin_addrs:
                            decimals = 6
                        else:
                            decimals = 18
                        value = amount_raw / (10 ** decimals)  # Normalize to token units

            # Get block info
            ref_block = raw_data.get("ref_block_hash", "")
            block_number = None
            block_timestamp = None

            if ref_block:
                # Would need to query block by hash to get full info
                block_timestamp = datetime.fromtimestamp(
                    raw_data.get("timestamp", 0) / 1000, tz=timezone.utc
                )

            # Get token transfers
            token_transfers = []
            if self.trc20_tracking and contract_data:
                token_transfers = await self.parse_trc20_transfers(tx_data)

            return Transaction(
                hash=tx_hash,
                blockchain=self.blockchain,
                from_address=(
                    self.base58_to_hex(from_address) if from_address else "unknown"
                ),
                to_address=self.base58_to_hex(to_address) if to_address else None,
                value=value,
                timestamp=block_timestamp or datetime.now(timezone.utc),
                block_number=block_number,
                block_hash=ref_block,
                contract_address=(
                    self.base58_to_hex(contract_address) if contract_address else None
                ),
                token_transfers=token_transfers,
            )

        except Exception as e:
            logger.error(f"Error getting Tron transaction {tx_hash}: {e}")

        return None

    async def get_address_balance(self, address: str) -> float:
        """Get address balance in TRX"""
        try:
            account_data = await self.rpc_call(
                "wallet/getaccount", {"address": address}
            )

            if account_data:
                balance = account_data.get("balance", 0)
                return balance / 1_000_000  # Convert from sun to TRX

        except Exception as e:
            logger.error(f"Error getting Tron address balance for {address}: {e}")

        return 0.0

    async def get_address_transactions(
        self, address: str, limit: int = 100
    ) -> List[Transaction]:
        """Return confirmed transactions for *address* using TronGrid v1 REST API.

        Uses the ``/v1/accounts/{address}/transactions`` endpoint which is
        available on TronGrid-hosted nodes.  Self-hosted Tron full-nodes do
        not expose this endpoint; in that case the call returns HTTP 404 and
        an empty list is returned gracefully.

        Args:
            address: Base58 or hex Tron address to query.
            limit:   Maximum number of transactions to return (capped at 200
                     by TronGrid).

        Returns:
            List of parsed ``Transaction`` objects; empty on any error.
        """
        if not self.session:
            return []
        try:
            per_page = min(limit, 200)
            url = (
                f"{self.rpc_url.rstrip('/')}/v1/accounts/{address}/transactions"
                f"?limit={per_page}&only_confirmed=true"
            )
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    logger.debug(
                        "TronGrid v1 address transactions returned HTTP %s for %s",
                        resp.status,
                        address,
                    )
                    return []
                data = await resp.json()

            txs: List[Transaction] = []
            for tx_data in data.get("data", [])[:limit]:
                tx_hash = tx_data.get("txID") or tx_data.get("txid", "")
                if not tx_hash:
                    continue
                tx = await self.get_transaction(tx_hash)
                if tx is not None:
                    txs.append(tx)
            return txs

        except Exception as exc:
            logger.debug("get_address_transactions failed addr=%s: %s", address, exc)
            return []

    async def get_block_transactions(self, block_number: int) -> List[str]:
        """Get transaction hashes for a block"""
        try:
            block_data = await self.rpc_call(
                "wallet/getblockbynum", {"num": block_number}
            )

            if not block_data:
                return []

            transactions = block_data.get("transactions", [])
            return [tx.get("txID", "") for tx in transactions]

        except Exception as e:
            logger.error(
                f"Error getting Tron block transactions for {block_number}: {e}"
            )
            return []

    def base58_to_hex(self, address: str) -> str:
        """Convert Base58 address to hex"""
        try:
            if BASE58_AVAILABLE:
                decoded = base58.b58decode(address)
                return decoded.hex()
            else:
                return address
        except Exception:
            return address

    def hex_to_base58(self, hex_address: str) -> str:
        """Convert a Tron hex address (with 0x41 prefix) to Base58Check encoding.

        Tron Base58Check: raw_bytes → double-SHA256 → 4-byte checksum appended
        → base58 encoded.  The version byte (0x41) must already be the first
        byte of *hex_address* (i.e. the full 21-byte address in hex).
        """
        try:
            if BASE58_AVAILABLE:
                raw = bytes.fromhex(hex_address)
                checksum = hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
                return base58.b58encode(raw + checksum).decode()
            else:
                return hex_address
        except Exception:
            return hex_address

    async def parse_trc20_transfers(self, tx_data: Dict) -> List[Dict]:
        """Parse TRC20 token transfers from transaction"""
        transfers = []

        try:
            raw_data = tx_data.get("raw_data", {})
            contract_data = raw_data.get("contract", [])
            
            # Extract sender address from transaction owner_address
            from_address_hex = raw_data.get("owner_address", "")
            from_address = self.hex_to_base58(from_address_hex) if from_address_hex else ""

            for contract in contract_data:
                if contract.get("type") == "TriggerSmartContract":
                    trigger = contract.get("value", {})
                    contract_address = trigger.get("contract_address", "")
                    parameter = trigger.get("data", "")

                    # Check if this is a stablecoin contract
                    stablecoin_symbol = None
                    for symbol, address in self.stablecoin_contracts.items():
                        if contract_address == address:
                            stablecoin_symbol = symbol
                            break

                    if stablecoin_symbol and parameter.startswith("a9059cbb") and len(parameter) >= 136:
                        # Decode transfer(address,uint256) ABI call.
                        # Recipient: last 20 bytes of first 32-byte word; prepend 41 for Tron.
                        to_hex_raw = parameter[8 + 24 : 8 + 64]
                        to_addr_b58 = self.hex_to_base58("41" + to_hex_raw)
                        amount_raw = int(parameter[72:136], 16)
                        # Determine decimals: USDT/USDC on Tron use 6; default to 6.
                        decimals = 6
                        transfers.append(
                            {
                                "symbol": stablecoin_symbol,
                                "contract_address": contract_address,
                                "from_address": from_address,
                                "to_address": to_addr_b58,
                                "amount": amount_raw,
                                "amount_normalized": amount_raw / (10 ** decimals),
                                "decimals": decimals,
                            }
                        )

        except Exception as e:
            logger.error(f"Error parsing TRC20 transfers: {e}")

        return transfers

    async def get_trc20_balance(self, address: str, contract_address: str) -> float:
        """Get TRC20 token balance"""
        try:
            # This would call the balanceOf function on the contract
            # Simplified implementation
            return 0.0

        except Exception as e:
            logger.error(f"Error getting TRC20 balance: {e}")
            return 0.0

    async def get_network_stats(self) -> Dict[str, Any]:
        """Get Tron network statistics"""
        try:
            # Get node info
            node_info = await self.rpc_call("wallet/getnodeinfo")

            # Get latest block
            latest_block = await self.rpc_call("wallet/getnowblock")

            return {
                "blockchain": self.blockchain,
                "block_number": (
                    latest_block.get("block_header", {})
                    .get("raw_data", {})
                    .get("number", 0)
                    if latest_block
                    else 0
                ),
                "block_time": "3s",  # Tron block time
                "active_nodes": node_info.get("activeNodeCount", 0) if node_info else 0,
                "total_nodes": node_info.get("totalNodeCount", 0) if node_info else 0,
            }

        except Exception as e:
            logger.error(f"Error getting Tron network stats: {e}")
            return {}
