"""
Jackdaw Sentry - Ethereum Collector
Ethereum and EVM-compatible blockchain data collection
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

# Try to import Web3 dependencies, but don't fail if not available
try:
    from eth_utils import from_wei
    from eth_utils import to_checksum_address
    from web3 import Web3

    # geth_poa_middleware was removed in web3.py v7 (renamed / restructured).
    # Import it conditionally so we stay compatible with both v6 and v7.
    try:
        from web3.middleware import geth_poa_middleware  # web3 < 7
    except ImportError:
        try:
            from web3.middleware import ExtraDataToPOAMiddleware as geth_poa_middleware  # web3 >= 7
        except ImportError:
            geth_poa_middleware = None  # not needed for mainnet

    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False

    # Fallback functions
    def to_checksum_address(address: str) -> str:
        return address.lower()

    def from_wei(amount: int, unit: str = "ether") -> float:
        if unit == "ether":
            return float(amount) / 1e18
        elif unit == "gwei":
            return float(amount) / 1e9
        return float(amount)


from src.api.config import settings

from .base import Address
from .base import BaseCollector
from .base import Block
from .base import Transaction

logger = logging.getLogger(__name__)


class EthereumCollector(BaseCollector):
    """Ethereum and EVM-compatible blockchain collector"""

    def __init__(self, blockchain: str, config: Dict[str, Any]):
        super().__init__(blockchain, config)
        self.rpc_url = config.get("rpc_url")
        self.network = config.get("network")

        # EVM-specific settings
        self.erc20_tracking = config.get("erc20_tracking", True)
        self.contract_tracking = config.get("contract_tracking", True)
        self.event_tracking = config.get("event_tracking", True)

        # Stablecoin contracts for this blockchain
        self.stablecoin_contracts = self.get_stablecoin_contracts()

        self.w3 = None
        self.latest_block_cache = None
        self.cache_timeout = 30  # seconds

    def get_stablecoin_contracts(self) -> Dict[str, str]:
        """Get stablecoin contracts for this blockchain"""
        contracts = {}

        if self.blockchain == "ethereum":
            contracts.update(
                {
                    "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
                    "USDC": "0xA0b86a33E6441b6e8F9c2c2c4c4c4c4c4c4c4c4c",
                    "EURC": "0x2A325e6831B0AD69618ebC6adD6f3B8c3C5d6B5f",
                    "EURT": "0x0C10bF8FbC34C309b9F6D3394b5D1F5D6E7F8A9B",
                }
            )
        elif self.blockchain == "bsc":
            contracts.update(
                {
                    "USDT": "0x55d398326f99059fF775485246999027B3197955",
                    "USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
                    "BUSD": "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56",
                }
            )
        elif self.blockchain == "polygon":
            contracts.update(
                {
                    "USDT": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
                    "USDC": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
                }
            )
        elif self.blockchain == "arbitrum":
            contracts.update(
                {
                    "USDT": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
                    "USDC": "0xA0b86a33E6441b6e8F9c2c2c4c4c4c4c4c4c4c4c",
                }
            )
        elif self.blockchain == "base":
            contracts.update({"USDC": "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531770b969"})
        elif self.blockchain == "avalanche":
            contracts.update(
                {
                    "USDT": "0x9702230A8Ea53632f8Ee31f33D8d9B7644d6b7b",
                    "USDC": "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
                }
            )

        return contracts

    async def connect(self) -> bool:
        """Connect to Ethereum RPC"""
        if not WEB3_AVAILABLE:
            logger.warning("Web3 dependencies not available, skipping connection")
            return False

        try:
            # Configure Web3
            self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))

            # Add POA middleware for networks like BSC, Polygon (only if available).
            if self.blockchain in ["bsc", "polygon", "arbitrum", "base", "avalanche"] \
                    and geth_poa_middleware is not None:
                self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)

            # Test connection
            if self.w3.is_connected():
                chain_id = self.w3.eth.chain_id
                logger.info(f"Connected to {self.blockchain} (chain_id: {chain_id})")
                return True
            else:
                logger.error(f"Failed to connect to {self.blockchain}")

        except Exception as e:
            logger.error(f"Error connecting to {self.blockchain}: {e}")

        return False

    async def disconnect(self):
        """Disconnect from Ethereum RPC"""
        if self.w3:
            self.w3 = None

    async def get_latest_block_number(self) -> int:
        """Get latest block number"""
        try:
            if self.w3:
                return self.w3.eth.block_number
        except Exception as e:
            logger.error(f"Error getting latest block for {self.blockchain}: {e}")
        return 0

    async def get_block(self, block_number: int) -> Optional[Block]:
        """Get block by number"""
        try:
            if not self.w3:
                return None

            block_data = self.w3.eth.get_block(block_number, full_transactions=True)
            if not block_data:
                return None

            return Block(
                hash=block_data["hash"].hex(),
                blockchain=self.blockchain,
                number=block_data["number"],
                timestamp=datetime.fromtimestamp(block_data["timestamp"]),
                transaction_count=len(block_data["transactions"]),
                parent_hash=block_data["parentHash"].hex(),
                miner=block_data["miner"],
                difficulty=str(block_data["difficulty"]),
                size=block_data["size"],
            )

        except Exception as e:
            logger.error(f"Error getting {self.blockchain} block {block_number}: {e}")

        return None

    async def get_transaction(self, tx_hash: str) -> Optional[Transaction]:
        """Get transaction by hash"""
        try:
            if not self.w3:
                return None

            tx_data = self.w3.eth.get_transaction(tx_hash)
            if not tx_data:
                return None

            # Get receipt for status and gas info
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)

            # Get block info
            block_number = tx_data["blockNumber"]
            block_timestamp = None
            if block_number:
                block_data = self.w3.eth.get_block(block_number)
                if block_data:
                    block_timestamp = datetime.fromtimestamp(block_data["timestamp"])

            # Determine addresses
            from_address = tx_data["from"]
            to_address = tx_data["to"]

            # Handle contract creation
            if to_address is None:
                to_address = from_address  # Self-loop for contract creation

            # Convert value from wei
            value = from_wei(tx_data["value"], "ether")

            # Calculate fee
            gas_used = receipt["gasUsed"] if receipt else 0
            gas_price = tx_data["gasPrice"]
            fee = (
                from_wei(gas_used * gas_price, "ether") if gas_used and gas_price else 0
            )

            # Get token transfers
            token_transfers = []
            dex_logs = []
            if self.erc20_tracking and receipt:
                token_transfers = await self.get_token_transfers(tx_hash, receipt)
                dex_logs = self._extract_dex_logs(receipt)

            return Transaction(
                hash=tx_hash,
                blockchain=self.blockchain,
                from_address=from_address,
                to_address=to_address,
                value=value,
                timestamp=block_timestamp or datetime.now(timezone.utc),
                block_number=block_number,
                block_hash=tx_data["blockHash"].hex() if tx_data["blockHash"] else None,
                gas_used=gas_used,
                gas_price=gas_price,
                fee=fee,
                status="confirmed" if receipt and receipt["status"] == 1 else "failed",
                # web3.py v7 no longer includes "confirmations" in receipts.
                confirmations=receipt.get("confirmations", 0) if receipt else 0,
                contract_address=(
                    receipt["contractAddress"].hex()
                    if receipt and receipt["contractAddress"]
                    else None
                ),
                token_transfers=token_transfers,
                dex_logs=dex_logs,
            )

        except Exception as e:
            logger.error(f"Error getting {self.blockchain} transaction {tx_hash}: {e}")

        return None

    async def get_address_balance(self, address: str) -> float:
        """Get address balance in ETH"""
        try:
            if not self.w3 or not WEB3_AVAILABLE:
                return 0.0

            checksum_address = to_checksum_address(address)
            balance_wei = self.w3.eth.get_balance(checksum_address)
            return from_wei(balance_wei, "ether")

        except Exception as e:
            logger.error(
                f"Error getting {self.blockchain} address balance for {address}: {e}"
            )
            return 0.0

    async def get_address_transactions(
        self, address: str, limit: int = 100
    ) -> List[Transaction]:
        """Get address transaction history via Etherscan API (falls back to block scan).

        Uses the Etherscan ``txlist`` endpoint which returns paginated history
        instantly, avoiding the multi-minute block-scan approach.  Works without
        an API key (public rate limit applies).
        """
        # Primary: Ethplorer (free, no key required)
        transactions = await self._get_address_transactions_ethplorer(address, limit)
        if transactions:
            return transactions

        # Secondary: Etherscan V2 (requires ETHERSCAN_API_KEY)
        transactions = await self._get_address_transactions_etherscan(address, limit)
        if transactions:
            return transactions

        # Tertiary: eth_getLogs for recent ERC-20 activity (no key, last 2k blocks)
        transactions = await self._get_address_transactions_logs(address, limit)
        if transactions:
            return transactions

        # Quaternary: block-scan fallback (last 1k blocks, slow)
        transactions = await self._get_address_transactions_scan(address, limit)
        if transactions:
            return transactions

        logger.info(
            "No transactions found for %s/%s via any lookup path",
            self.blockchain,
            address,
        )
        return []

    async def _get_address_transactions_ethplorer(
        self, address: str, limit: int
    ) -> List[Transaction]:
        """Fetch transaction history from Ethplorer (free, no key required).

        Uses the public ``freekey`` which returns up to 100 recent transactions
        with full ETH value, from/to, timestamp, and success flag — no per-tx
        RPC calls needed.
        """
        import aiohttp

        url = (
            f"https://api.ethplorer.io/getAddressTransactions/{address}"
            f"?apiKey=freekey&limit={min(limit, 100)}"
        )
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            ) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json(content_type=None)

            if not isinstance(data, list):
                return []

            transactions: List[Transaction] = []
            for item in data:
                tx_hash = item.get("hash")
                if not tx_hash:
                    continue
                try:
                    ts_val = item.get("timestamp")
                    if ts_val is None:
                        logger.debug("Skipping transaction %s: missing timestamp", tx_hash)
                        continue
                    
                    ts = datetime.fromtimestamp(ts_val, tz=timezone.utc)
                    transactions.append(Transaction(
                        hash=tx_hash,
                        blockchain=self.blockchain,
                        timestamp=ts,
                        from_address=(item.get("from") or "").lower() or None,
                        to_address=(item.get("to") or "").lower() or None,
                        value=float(item.get("value", 0)),
                        status="confirmed" if item.get("success", True) else "failed",
                    ))
                except Exception as exc:
                    logger.debug("Ethplorer tx parse error %s: %s", tx_hash, exc)

            logger.info(
                "Ethplorer: found %d txs for %s/%s",
                len(transactions), self.blockchain, address,
            )
            return transactions
        except Exception as exc:
            logger.debug("Ethplorer lookup failed for %s: %s", address, exc)
            return []

    async def _get_address_transactions_etherscan(
        self, address: str, limit: int
    ) -> List[Transaction]:
        """Fetch up to ``limit`` transactions from Etherscan V2 API.

        Requires ETHERSCAN_API_KEY in settings.  Returns [] when no key is
        configured so the caller falls through to the eth_getLogs path.
        """
        import aiohttp

        api_key = getattr(settings, "ETHERSCAN_API_KEY", None)
        if not api_key:
            return []

        # Map blockchain names to Etherscan chain IDs
        chain_to_id = {
            "ethereum": 1,
            "bsc": 56,
            "polygon": 137,
            "arbitrum": 42161,
            "base": 8453,
            "avalanche": 43114,
            "optimism": 10,
            "fantom": 250,
            "cronos": 25,
        }
        chainid = chain_to_id.get(self.blockchain, 1)  # default to Ethereum mainnet

        url = (
            "https://api.etherscan.io/v2/api"
            f"?chainid={chainid}"
            "&module=account&action=txlist"
            f"&address={address}"
            "&startblock=0&endblock=99999999"
            f"&page=1&offset={min(limit, 100)}"
            "&sort=desc"
            f"&apikey={api_key}"
        )
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            ) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
            if data.get("status") != "1" or not isinstance(data.get("result"), list):
                return []
            transactions: List[Transaction] = []
            for item in data["result"][:limit]:
                tx_hash = item.get("hash")
                if not tx_hash:
                    continue
                try:
                    ts = int(item.get("timeStamp", 0))
                    gas_used_raw = item.get("gasUsed")
                    gas_price_raw = item.get("gasPrice")
                    gas_used = int(gas_used_raw) if gas_used_raw not in (None, "", "0x0") else None
                    gas_price = int(gas_price_raw) if gas_price_raw not in (None, "", "0x0") else None
                    fee = (
                        from_wei(gas_used * gas_price, "ether")
                        if gas_used and gas_price
                        else None
                    )
                    transactions.append(Transaction(
                        hash=tx_hash,
                        blockchain=self.blockchain,
                        timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
                        from_address=(item.get("from") or "").lower() or None,
                        to_address=(item.get("to") or "").lower() or None,
                        value=from_wei(int(item.get("value", 0)), "ether"),
                        gas_used=gas_used,
                        gas_price=gas_price,
                        fee=fee,
                        block_number=int(item["blockNumber"]) if item.get("blockNumber") else None,
                        status="confirmed" if item.get("isError", "0") == "0" else "failed",
                    ))
                except Exception as parse_exc:
                    logger.debug("Etherscan tx parse error %s: %s", tx_hash, parse_exc)
            return transactions
        except Exception as exc:
            logger.debug("Etherscan txlist failed for %s: %s", address, exc)
            return []

    async def _get_address_transactions_logs(
        self, address: str, limit: int
    ) -> List[Transaction]:
        """Discover transactions via eth_getLogs (ERC-20 Transfer events).

        Uses direct async HTTP JSON-RPC calls (via aiohttp) against the
        fallback HTTP endpoint so we avoid sharing the WebSocket w3 instance
        with the concurrent backfill worker.  Works without any API key.
        Scans the last 2 000 blocks for Transfer events where the address
        appears as ERC-20 sender or recipient.
        """
        import aiohttp

        rpc_url = getattr(settings, "ETHEREUM_RPC_FALLBACK", None)
        if not rpc_url or not rpc_url.startswith("http"):
            return []

        TRANSFER_SIG = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
        _SCAN_BLOCKS = 2_000
        _TIMEOUT = aiohttp.ClientTimeout(total=20)

        # Pad address to 32-byte topic
        addr = address.removeprefix("0x").lower() if address.lower().startswith("0x") else address.lower()
        padded = "0x" + "0" * 24 + addr

        async def _rpc(session, method, params, req_id=1):
            async with session.post(
                rpc_url,
                json={"jsonrpc": "2.0", "method": method, "params": params, "id": req_id},
                timeout=_TIMEOUT,
            ) as resp:
                data = await resp.json()
                return data.get("result")

        try:
            async with aiohttp.ClientSession() as session:
                # Get latest block number
                latest_hex = await _rpc(session, "eth_blockNumber", [])
                if not latest_hex:
                    return []
                latest_block = int(latest_hex, 16)
                from_block = max(0, latest_block - _SCAN_BLOCKS)
                from_block_hex = hex(from_block)

                # Fetch Transfer logs where address is sender (topic1) or recipient (topic2)
                logs_from_task = _rpc(session, "eth_getLogs", [{
                    "fromBlock": from_block_hex, "toBlock": "latest",
                    "topics": [TRANSFER_SIG, padded],
                }], req_id=2)
                logs_to_task = _rpc(session, "eth_getLogs", [{
                    "fromBlock": from_block_hex, "toBlock": "latest",
                    "topics": [TRANSFER_SIG, None, padded],
                }], req_id=3)
                logs_from, logs_to = await asyncio.gather(logs_from_task, logs_to_task)

            all_logs = list(logs_from or []) + list(logs_to or [])
            if not all_logs:
                return []

            # Collect unique tx hashes, keep highest block number seen per hash
            seen: dict = {}
            for log in all_logs:
                tx_hash = log.get("transactionHash", "")
                blk = int(log.get("blockNumber", "0x0"), 16)
                if tx_hash:
                    seen[tx_hash] = max(seen.get(tx_hash, 0), blk)

            ordered_hashes = [
                h for h, _ in sorted(seen.items(), key=lambda kv: kv[1], reverse=True)
            ][:limit]

            if not ordered_hashes:
                return []

            # Fetch tx data and block timestamps in parallel (one session, many requests)
            async with aiohttp.ClientSession() as session:
                tx_tasks = [
                    _rpc(session, "eth_getTransactionByHash", [h], req_id=i)
                    for i, h in enumerate(ordered_hashes, start=10)
                ]
                raw_txs = await asyncio.gather(*tx_tasks, return_exceptions=True)

                unique_blocks = list({seen[h] for h in ordered_hashes})
                blk_tasks = [
                    _rpc(session, "eth_getBlockByNumber", [hex(bn), False], req_id=i)
                    for i, bn in enumerate(unique_blocks, start=1000)
                ]
                blk_results = await asyncio.gather(*blk_tasks, return_exceptions=True)

            block_ts: dict = {}
            for res in blk_results:
                if isinstance(res, dict) and res:
                    try:
                        bn = int(res["number"], 16)
                        ts = datetime.fromtimestamp(int(res["timestamp"], 16), tz=timezone.utc)
                        block_ts[bn] = ts
                    except Exception:
                        pass

            transactions: List[Transaction] = []
            for tx_hash, tx_data in zip(ordered_hashes, raw_txs):
                if isinstance(tx_data, Exception) or not tx_data:
                    continue
                try:
                    blk = int(tx_data.get("blockNumber") or "0x0", 16) or None
                    ts = block_ts.get(blk, datetime.now(tz=timezone.utc))
                    gas_price_hex = tx_data.get("gasPrice")
                    gas_price = int(gas_price_hex, 16) if gas_price_hex else None
                    value_hex = tx_data.get("value", "0x0")
                    value_wei = int(value_hex, 16) if value_hex else 0
                    # Fetch transaction receipt to get actual status
                    status = "unknown"  # default fallback
                    try:
                        if self.w3 and WEB3_AVAILABLE:
                            receipt = await asyncio.get_event_loop().run_in_executor(
                                None, self.w3.eth.get_transaction_receipt, tx_hash
                            )
                            if receipt:
                                status = "confirmed" if receipt.status == 1 else "failed"
                    except Exception as receipt_exc:
                        logger.debug("Failed to fetch receipt for %s: %s", tx_hash, receipt_exc)
                    
                    transactions.append(Transaction(
                        hash=tx_hash,
                        blockchain=self.blockchain,
                        timestamp=ts,
                        from_address=(tx_data.get("from") or "").lower() or None,
                        to_address=(tx_data.get("to") or "").lower() or None,
                        value=from_wei(value_wei, "ether"),
                        gas_price=gas_price,
                        block_number=blk,
                        status=status,
                    ))
                except Exception as exc:
                    logger.debug("Log-based tx build error %s: %s", tx_hash, exc)

            return transactions

        except Exception as exc:
            logger.warning("eth_getLogs address scan failed for %s: %s", address, exc)
            return []

    async def _get_address_transactions_scan(
        self, address: str, limit: int
    ) -> List[Transaction]:
        """Narrow block-scan fallback (last 1 000 blocks only)."""
        try:
            if not self.w3 or not WEB3_AVAILABLE:
                return []
            checksum_address = to_checksum_address(address)
            transactions: List[Transaction] = []
            latest_block = self.w3.eth.block_number
            start_block = max(0, latest_block - 1000)
            for block_num in range(latest_block, start_block, -1):
                if len(transactions) >= limit:
                    break
                block = self.w3.eth.get_block(block_num, full_transactions=True)
                if not block:
                    continue
                for tx in block["transactions"]:
                    if tx["from"] == checksum_address or (
                        tx["to"] and tx["to"] == checksum_address
                    ):
                        tx_obj = await self.get_transaction(tx["hash"].hex())
                        if tx_obj:
                            transactions.append(tx_obj)
            return transactions[:limit]
        except Exception as exc:
            logger.error(
                "Error in block-scan fallback for %s/%s: %s",
                self.blockchain,
                address,
                exc,
            )
            return []

    async def get_block_transactions(self, block_number: int) -> List[str]:
        """Get transaction hashes for a block"""
        try:
            if not self.w3:
                return []

            block = self.w3.eth.get_block(block_number)
            if not block:
                return []

            # web3.py v7: transactions are HexBytes when full_transactions=False;
            # v6: they are dicts with a "hash" key.  Handle both.
            return [
                tx.hex() if isinstance(tx, bytes) else tx["hash"].hex()
                for tx in block["transactions"]
            ]

        except Exception as e:
            logger.error(
                f"Error getting {self.blockchain} block transactions for {block_number}: {e}"
            )
            return []

    async def get_token_transfers(self, tx_hash: str, receipt: Dict) -> List[Dict]:
        """Get ERC20 token transfers from transaction receipt"""
        transfers = []

        try:
            for log in receipt.get("logs", []):
                # Check if it's a Transfer event (topic0 = keccak256("Transfer(address,address,uint256)"))
                if (
                    len(log["topics"]) == 3
                    and log["topics"][0].hex()
                    == "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
                ):

                    # Parse Transfer event
                    from_address = "0x" + log["topics"][1].hex()[-40:]
                    to_address = "0x" + log["topics"][2].hex()[-40:]

                    # Decode amount (first 32 bytes of data)
                    amount = int(log["data"].hex(), 16)

                    # Check if this is a stablecoin
                    contract_address = log["address"].hex()
                    stablecoin_symbol = self.get_stablecoin_symbol(contract_address)

                    if stablecoin_symbol:
                        # Get decimals for the token
                        decimals = await self.get_token_decimals(contract_address)
                        amount_adjusted = amount / (10**decimals)

                        transfers.append(
                            {
                                "symbol": stablecoin_symbol,
                                "contract_address": contract_address,
                                "from_address": from_address,
                                "to_address": to_address,
                                "amount": amount_adjusted,
                                "decimals": decimals,
                            }
                        )

        except Exception as e:
            logger.error(f"Error parsing token transfers for {tx_hash}: {e}")

        return transfers

    # keccak256 event signatures stored to raw_evm_logs.
    # Includes DEX Swap events (sync'd with evm_log_decoder.py) and bridge
    # deposit events (used by bridge_log_decoder.py for intermediate-ID resolution).
    # Bridge sigs are computed lazily so the import cost is paid at first use.
    @staticmethod
    def _build_relevant_log_sigs() -> frozenset:
        """Build the combined set of relevant event signatures for log storage."""
        dex_sigs = {
            "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822",  # Uniswap V2
            "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67",  # Uniswap V3
            "0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83",  # Uniswap V4
        }
        try:
            from src.tracing.bridge_log_decoder import (
                ACROSS_V3_FUNDS_DEPOSITED,
                CELER_SEND,
                STARGATE_SWAP,
                CHAINFLIP_SWAP_NATIVE,
                CHAINFLIP_SWAP_TOKEN,
            )
            bridge_sigs = {
                ACROSS_V3_FUNDS_DEPOSITED,
                CELER_SEND,
                STARGATE_SWAP,
                CHAINFLIP_SWAP_NATIVE,
                CHAINFLIP_SWAP_TOKEN,
            }
        except Exception:
            bridge_sigs = set()
        return frozenset(dex_sigs | bridge_sigs)

    # Populated once on first call to _extract_dex_logs.
    _DEX_SWAP_SIGS: frozenset = frozenset()

    def _extract_dex_logs(self, receipt: Dict) -> List[Dict]:
        """Extract relevant event logs from a transaction receipt for dual-write.

        Matches DEX Swap events (Uniswap V2/V3/V4) and bridge deposit events
        (Across V3FundsDeposited, Celer Send, Stargate Swap, Chainflip Swap*).
        The returned dicts are written to ``raw_evm_logs`` via
        ``base.py._insert_raw_evm_logs()``.

        The ``decoded`` column is left null and populated on-demand in
        ``EVMChainCompiler._fetch_dex_swap_log``.

        Args:
            receipt: Transaction receipt dict as returned by
                ``web3.eth.get_transaction_receipt()``.

        Returns:
            List of dicts with keys: ``log_index``, ``contract``,
            ``event_sig``, ``topic1``, ``topic2``, ``topic3``, ``data``.
            Empty list if no relevant logs are found.
        """
        # Lazily build the signature set on first call (avoids import at class-def time).
        if not self._DEX_SWAP_SIGS:
            EthereumCollector._DEX_SWAP_SIGS = self._build_relevant_log_sigs()

        result = []
        try:
            for log in receipt.get("logs", []):
                topics = log.get("topics", [])
                if not topics:
                    continue
                # topics[0] is a HexBytes object from web3.py — convert to hex str.
                topic0 = (
                    "0x" + topics[0].hex()
                    if hasattr(topics[0], "hex")
                    else str(topics[0])
                )
                if topic0 not in self._DEX_SWAP_SIGS:
                    continue

                def _topic_hex(t) -> Optional[str]:
                    if t is None:
                        return None
                    return "0x" + t.hex() if hasattr(t, "hex") else str(t)

                contract = log.get("address", "")
                if hasattr(contract, "lower"):
                    contract = contract.lower()

                data_raw = log.get("data", b"")
                data_hex = (
                    "0x" + data_raw.hex()
                    if hasattr(data_raw, "hex")
                    else str(data_raw) if data_raw else None
                )

                result.append({
                    "log_index": log.get("logIndex", 0),
                    "contract": contract,
                    "event_sig": topic0,
                    "topic1": _topic_hex(topics[1]) if len(topics) > 1 else None,
                    "topic2": _topic_hex(topics[2]) if len(topics) > 2 else None,
                    "topic3": _topic_hex(topics[3]) if len(topics) > 3 else None,
                    "data": data_hex,
                })
        except Exception as exc:
            logger.debug("_extract_dex_logs failed: %s", exc)
        return result

    def get_stablecoin_symbol(self, contract_address: str) -> Optional[str]:
        """Get stablecoin symbol from contract address"""
        for symbol, address in self.stablecoin_contracts.items():
            if address.lower() == contract_address.lower():
                return symbol
        return None

    async def get_token_decimals(self, contract_address: str) -> int:
        """Get token decimals from contract"""
        try:
            if not self.w3:
                return 18  # Default to 18

            # ERC20 decimals function signature
            decimals_function = self.w3.sha3(text="decimals()").hex()[:10]

            result = self.w3.eth.call(
                {"to": contract_address, "data": decimals_function}
            )

            if result:
                return int(result.hex(), 16)

        except Exception as e:
            logger.error(f"Error getting decimals for {contract_address}: {e}")

        return 18  # Default to 18

    async def monitor_pending_transactions(self):
        """Monitor pending transactions"""
        try:
            if not self.w3:
                return

            pending_filter = self.w3.eth.filter("pending")

            while self.is_running:
                try:
                    for tx_hash in pending_filter.get_new_entries():
                        await self.process_pending_transaction(tx_hash.hex())

                    await asyncio.sleep(5)  # Check every 5 seconds

                except Exception as e:
                    logger.error(f"Error in pending transaction monitoring: {e}")
                    await asyncio.sleep(10)

        except Exception as e:
            logger.error(f"Error setting up pending transaction filter: {e}")

    async def process_pending_transaction(self, tx_hash: str):
        """Process pending transaction"""
        try:
            tx = await self.get_transaction(tx_hash)
            if tx:
                tx.status = "pending"
                await self.store_transaction(tx)

                # Check for large stablecoin transfers
                for transfer in tx.token_transfers:
                    if transfer["amount"] > 100000:  # > 100k USD equivalent
                        await self.alert_large_stablecoin_transfer(tx, transfer)

        except Exception as e:
            logger.error(f"Error processing pending transaction {tx_hash}: {e}")

    async def alert_large_stablecoin_transfer(self, tx: Transaction, transfer: Dict):
        """Alert on large stablecoin transfers"""
        logger.warning(
            f"Large stablecoin transfer detected: {transfer['symbol']} {transfer['amount']} - {tx.hash}"
        )

        # Store alert in database
        query = """
        MATCH (t:Transaction {hash: $hash, blockchain: $blockchain})
        MERGE (a:Alert {type: 'large_stablecoin_transfer'})
        MERGE (t)-[:TRIGGERED]->(a)
        SET a.symbol = $symbol,
            a.amount = $amount,
            a.threshold = $threshold,
            a.created_at = timestamp()
        """

        from src.api.database import get_neo4j_session

        async with get_neo4j_session() as session:
            await session.run(
                query,
                hash=tx.hash,
                blockchain=self.blockchain,
                symbol=transfer["symbol"],
                amount=transfer["amount"],
                threshold=100000,
            )

    async def get_contract_info(self, contract_address: str) -> Optional[Dict]:
        """Get contract information"""
        try:
            if not self.w3:
                return None

            checksum_address = to_checksum_address(contract_address)

            # Get contract code
            code = self.w3.eth.get_code(checksum_address)
            if not code:
                return None  # Not a contract

            # Try to get basic contract info
            info = {
                "address": contract_address,
                "has_code": True,
                "bytecode_size": len(code),
            }

            # Try to get ERC20 token info
            try:
                # name() function
                name_function = self.w3.sha3(text="name()").hex()[:10]
                name_result = self.w3.eth.call(
                    {"to": checksum_address, "data": name_function}
                )
                if name_result:
                    info["name"] = (
                        self.w3.eth.contract(address=checksum_address)
                        .functions.name()
                        .call()
                    )

                # symbol() function
                symbol_function = self.w3.sha3(text="symbol()").hex()[:10]
                symbol_result = self.w3.eth.call(
                    {"to": checksum_address, "data": symbol_function}
                )
                if symbol_result:
                    info["symbol"] = (
                        self.w3.eth.contract(address=checksum_address)
                        .functions.symbol()
                        .call()
                    )

                # totalSupply() function
                supply_function = self.w3.sha3(text="totalSupply()").hex()[:10]
                supply_result = self.w3.eth.call(
                    {"to": checksum_address, "data": supply_function}
                )
                if supply_result:
                    info["total_supply"] = (
                        self.w3.eth.contract(address=checksum_address)
                        .functions.totalSupply()
                        .call()
                    )

            except Exception:
                pass  # Not an ERC20 token

            return info

        except Exception as e:
            logger.error(f"Error getting contract info for {contract_address}: {e}")

        return None

    async def start(self):
        """Start Ethereum collector with additional monitoring"""
        await super().start()

        # Start pending transaction monitoring
        if self.erc20_tracking:
            asyncio.create_task(self.monitor_pending_transactions())

    async def get_network_stats(self) -> Dict[str, Any]:
        """Get Ethereum network statistics"""
        try:
            if not self.w3:
                return {}

            latest_block = self.w3.eth.get_block("latest")
            gas_price = self.w3.eth.gas_price

            return {
                "blockchain": self.blockchain,
                "block_number": latest_block["number"],
                "gas_price": from_wei(gas_price, "gwei") if gas_price else 0,
                "difficulty": str(latest_block["difficulty"]),
                "total_difficulty": str(latest_block["totalDifficulty"]),
                "block_time": "12s",  # Ethereum block time
                "chain_id": self.w3.eth.chain_id,
            }

        except Exception as e:
            logger.error(f"Error getting {self.blockchain} network stats: {e}")
            return {}
