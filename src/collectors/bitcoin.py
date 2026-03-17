"""
Jackdaw Sentry - Bitcoin Collector
Bitcoin blockchain data collection with Lightning Network support
"""

import asyncio
import binascii
import json
import logging
import struct
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from src.api.config import settings

from .base import Address
from .base import BaseCollector
from .base import Block
from .base import Transaction

logger = logging.getLogger(__name__)

# External UTXO indexers — no local Bitcoin node required for arbitrary address tracing.
# Primary: mempool.space (esplora API). Fallback: blockstream.info (same API format).
_MEMPOOL_SPACE_BASE = "https://mempool.space/api"
_BLOCKSTREAM_BASE = "https://blockstream.info/api"

# CoinJoin detection thresholds.
# A transaction is flagged when ≥ this fraction of outputs share the same value.
_COINJOIN_EQUAL_OUTPUT_THRESHOLD = 0.8
# Minimum distinct input addresses to consider a CoinJoin candidate.
_COINJOIN_MIN_INPUTS = 2


def _detect_coinjoin(
    tx_hash: str,
    input_addresses: List[str],
    output_values: List[float],
) -> bool:
    """Heuristically detect a CoinJoin transaction.

    Confidence: HIGH (~0.90).  Failure mode: batch-payment processors and
    some exchange sweeps also produce equal-denomination outputs.

    A transaction is flagged when:
    - At least ``_COINJOIN_MIN_INPUTS`` distinct input addresses, AND
    - At least ``_COINJOIN_EQUAL_OUTPUT_THRESHOLD`` fraction of outputs
      share the same value (equal-denomination pattern).

    The caller must treat a positive result as AMBIGUOUS — never propagate
    taint silently through a CoinJoin.

    Args:
        tx_hash: Transaction ID (for logging).
        input_addresses: All input addresses extracted from vin.
        output_values: All vout values in BTC.

    Returns:
        True if the transaction matches the CoinJoin heuristic.
    """
    if len(set(input_addresses)) < _COINJOIN_MIN_INPUTS:
        return False
    if not output_values:
        return False

    # Count outputs by rounded value (8 dp to absorb floating-point noise).
    value_counts: Dict[str, int] = {}
    for v in output_values:
        key = f"{v:.8f}"
        value_counts[key] = value_counts.get(key, 0) + 1

    modal_count = max(value_counts.values())
    equal_fraction = modal_count / len(output_values)

    if equal_fraction >= _COINJOIN_EQUAL_OUTPUT_THRESHOLD:
        logger.debug(
            "_detect_coinjoin: txid=%s distinct_inputs=%d equal_output_fraction=%.2f → flagged",
            tx_hash,
            len(set(input_addresses)),
            equal_fraction,
        )
        return True

    return False


class BitcoinCollector(BaseCollector):
    """Bitcoin blockchain collector with Lightning Network support"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__("bitcoin", config)
        self.rpc_url = config.get("rpc_url", settings.BITCOIN_RPC_URL)
        self.rpc_user = config.get("rpc_user", settings.BITCOIN_RPC_USER)
        self.rpc_password = config.get("rpc_password", settings.BITCOIN_RPC_PASSWORD)
        self.network = config.get("network", settings.BITCOIN_NETWORK)

        # Bitcoin-specific settings
        self.mempool_monitoring = config.get("mempool_monitoring", True)
        self.utxo_tracking = config.get("utxo_tracking", True)
        self.lightning_enabled = config.get("lightning_enabled", True)

        self.rpc_session = None
        self.mempool = set()

    async def connect(self) -> bool:
        """Connect to Bitcoin RPC"""
        try:
            import aiohttp

            # aiohttp.BasicAuth raises ValueError for None credentials.
            # Public nodes (e.g. publicnode.com) require no auth; handle gracefully.
            auth = None
            if self.rpc_user or self.rpc_password:
                auth = aiohttp.BasicAuth(
                    self.rpc_user or "", self.rpc_password or ""
                )

            self.rpc_session = aiohttp.ClientSession(
                auth=auth,
                timeout=aiohttp.ClientTimeout(total=30),
            )

            # Test connection
            info = await self.rpc_call("getblockchaininfo")
            if info:
                logger.info(
                    f"Connected to Bitcoin {self.network} (chain: {info.get('chain')})"
                )
                return True

        except Exception as e:
            logger.error(f"Failed to connect to Bitcoin RPC: {e}")

        return False

    async def disconnect(self):
        """Disconnect from Bitcoin RPC"""
        if self.rpc_session:
            await self.rpc_session.close()

    async def rpc_call(self, method: str, params: List = None) -> Optional[Dict]:
        """Make Bitcoin RPC call"""
        if not self.rpc_session:
            return None

        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params or [],
            }

            async with self.rpc_session.post(
                self.rpc_url, json=payload, headers={"Content-Type": "application/json"}
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get("result")
                else:
                    logger.error(f"Bitcoin RPC error: {response.status}")

        except Exception as e:
            logger.error(f"Bitcoin RPC call failed: {e}")

        return None

    async def get_latest_block_number(self) -> int:
        """Get latest block number"""
        info = await self.rpc_call("getblockchaininfo")
        return info.get("blocks", 0) if info else 0

    async def get_block(self, block_number: int) -> Optional[Block]:
        """Get block by number"""
        try:
            # Get block hash first
            block_hash = await self.rpc_call("getblockhash", [block_number])
            if not block_hash:
                return None

            # Get full block
            block_data = await self.rpc_call(
                "getblock", [block_hash, 2]
            )  # verbosity 2 for full tx data
            if not block_data:
                return None

            return Block(
                hash=block_data["hash"],
                blockchain=self.blockchain,
                number=block_data["height"],
                timestamp=datetime.fromtimestamp(block_data["time"], tz=timezone.utc),
                transaction_count=len(block_data["tx"]),
                parent_hash=block_data.get("previousblockhash"),
                miner=block_data.get("miner"),
                difficulty=str(block_data.get("difficulty", 0)),
                size=block_data.get("size"),
            )

        except Exception as e:
            logger.error(f"Error getting Bitcoin block {block_number}: {e}")

        return None

    async def get_transaction(self, tx_hash: str) -> Optional[Transaction]:
        """Get transaction by hash"""
        try:
            tx_data = await self.rpc_call("getrawtransaction", [tx_hash, True])
            if not tx_data:
                return None

            # Get block info
            block_hash = tx_data.get("blockhash")
            block_number = None
            block_timestamp = None

            if block_hash:
                block_data = await self.rpc_call("getblock", [block_hash])
                if block_data:
                    block_number = block_data.get("height")
                    block_timestamp = datetime.fromtimestamp(
                        block_data.get("time"), tz=timezone.utc
                    )

            # Calculate inputs and outputs
            total_input = 0
            from_address = None
            to_address = None
            value = 0
            # Collect all distinct input addresses for CoinJoin detection.
            input_addresses: list = []

            # Process inputs
            if "vin" in tx_data:
                for vin in tx_data["vin"]:
                    if "coinbase" in vin:
                        # Coinbase transaction
                        from_address = "coinbase"
                    else:
                        # Regular input - get previous output
                        prev_tx = await self.rpc_call(
                            "getrawtransaction", [vin["txid"], True]
                        )
                        if prev_tx and "vout" in prev_tx:
                            prev_out = prev_tx["vout"][vin["vout"]]
                            total_input += prev_out.get("value", 0)

                            if (
                                "scriptPubKey" in prev_out
                                and "addresses" in prev_out["scriptPubKey"]
                            ):
                                addresses = prev_out["scriptPubKey"]["addresses"]
                                if addresses:
                                    from_address = addresses[0]
                                    input_addresses.append(addresses[0])

            # Process outputs — collect values for CoinJoin detection.
            output_values: list = []
            if "vout" in tx_data:
                for vout in tx_data["vout"]:
                    output_values.append(vout.get("value", 0))
                    if "scriptPubKey" in vout and "addresses" in vout["scriptPubKey"]:
                        addresses = vout["scriptPubKey"]["addresses"]
                        if addresses:
                            if not to_address:
                                to_address = addresses[0]
                            value += vout.get("value", 0)

            # CoinJoin heuristic: equal-denomination outputs + multiple distinct
            # input addresses.  Confidence HIGH (0.90+) but not deterministic.
            # Failure mode: some non-CoinJoin batch payments also have equal outputs.
            is_coinjoin = _detect_coinjoin(
                tx_hash=tx_data["txid"],
                input_addresses=input_addresses,
                output_values=output_values,
            )
            if is_coinjoin:
                logger.warning(
                    "CoinJoin candidate detected: txid=%s — taint analysis "
                    "will halt at this transaction (AMBIGUOUS).",
                    tx_data["txid"],
                )

            # Calculate fee
            fee = total_input - value if from_address != "coinbase" else 0

            return Transaction(
                hash=tx_data["txid"],
                blockchain=self.blockchain,
                from_address=from_address or "unknown",
                to_address=to_address,
                value=value,
                timestamp=block_timestamp or datetime.now(timezone.utc),
                block_number=block_number,
                block_hash=block_hash,
                fee=fee,
                status="confirmed" if block_number else "mempool",
                confirmations=tx_data.get("confirmations", 0),
                is_coinjoin=is_coinjoin,
            )

        except Exception as e:
            logger.error(f"Error getting Bitcoin transaction {tx_hash}: {e}")

        return None

    async def _fetch_utxos_from_indexer(self, address: str) -> Optional[List[Dict]]:
        """Fetch UTXOs from mempool.space (primary) with blockstream.info fallback.

        Uses public esplora-compatible REST APIs so arbitrary addresses can be
        traced without a locally synced Bitcoin node or watched-wallet setup.

        Args:
            address: Bitcoin address (any script type).

        Returns:
            List of UTXO dicts with keys ``txid``, ``vout``, ``value`` (satoshis),
            and ``status`` sub-dict, or ``None`` if both indexers fail.
        """
        import aiohttp

        endpoints = [
            f"{_MEMPOOL_SPACE_BASE}/address/{address}/utxo",
            f"{_BLOCKSTREAM_BASE}/address/{address}/utxo",
        ]
        timeout = aiohttp.ClientTimeout(total=15)

        for url in endpoints:
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            return await resp.json()
                        logger.debug("UTXO indexer %s returned %s", url, resp.status)
            except Exception as exc:
                logger.debug("UTXO indexer %s unreachable: %s", url, exc)

        logger.error("All UTXO indexers failed for address %s", address)
        return None

    async def get_address_balance(self, address: str) -> float:
        """Get address balance via external UTXO indexer (mempool.space / blockstream).

        Returns confirmed + unconfirmed UTXO sum in BTC.
        Falls back to 0.0 if both indexers are unreachable.
        """
        try:
            utxos = await self._fetch_utxos_from_indexer(address)
            if not utxos:
                return 0.0
            # value field is in satoshis
            balance_sat = sum(utxo.get("value", 0) for utxo in utxos)
            return balance_sat / 1e8
        except Exception as e:
            logger.error(f"Error getting Bitcoin address balance for {address}: {e}")
            return 0.0

    async def get_address_transactions(
        self, address: str, limit: int = 100
    ) -> List[Transaction]:
        """Get address transaction history via external UTXO indexer.

        Retrieves all UTXOs for the address via mempool.space (primary) or
        blockstream.info (fallback), then fetches full transaction data for each.
        This supports arbitrary Bitcoin addresses — not just locally watched wallets.

        Args:
            address: Bitcoin address to query.
            limit: Maximum number of transactions to return.

        Returns:
            List of Transaction objects.
        """
        try:
            utxos = await self._fetch_utxos_from_indexer(address)
            if not utxos:
                return []

            transactions = []
            for utxo in utxos[:limit]:
                tx = await self.get_transaction(utxo["txid"])
                if tx:
                    transactions.append(tx)

            return transactions

        except Exception as e:
            logger.error(
                f"Error getting Bitcoin address transactions for {address}: {e}"
            )
            return []

    async def get_block_transactions(self, block_number: int) -> List[str]:
        """Get transaction hashes for a block"""
        try:
            block_hash = await self.rpc_call("getblockhash", [block_number])
            if not block_hash:
                return []

            block_data = await self.rpc_call("getblock", [block_hash])
            if not block_data:
                return []

            return block_data.get("tx", [])

        except Exception as e:
            logger.error(
                f"Error getting Bitcoin block transactions for {block_number}: {e}"
            )
            return []

    async def monitor_mempool(self):
        """Monitor Bitcoin mempool for new transactions"""
        if not self.mempool_monitoring:
            return

        logger.info("Starting Bitcoin mempool monitoring...")

        while self.is_running:
            try:
                # Get current mempool
                mempool_info = await self.rpc_call(
                    "getrawmempool", [False]
                )  # verbose = False
                if mempool_info:
                    current_mempool = set(mempool_info)

                    # Process new transactions
                    new_txs = current_mempool - self.mempool
                    for tx_hash in new_txs:
                        await self.process_mempool_transaction(tx_hash)

                    # Process removed transactions
                    removed_txs = self.mempool - current_mempool
                    for tx_hash in removed_txs:
                        await self.process_removed_transaction(tx_hash)

                    self.mempool = current_mempool

                await asyncio.sleep(10)  # Check every 10 seconds

            except Exception as e:
                logger.error(f"Error in Bitcoin mempool monitoring: {e}")
                await asyncio.sleep(30)

    async def process_mempool_transaction(self, tx_hash: str):
        """Process new mempool transaction"""
        try:
            tx = await self.get_transaction(tx_hash)
            if tx:
                tx.status = "mempool"
                await self.store_transaction(tx)

                # Check for high-value transactions
                if tx.value > 10:  # > 10 BTC
                    await self.alert_high_value_transaction(tx)

        except Exception as e:
            logger.error(f"Error processing mempool transaction {tx_hash}: {e}")

    async def process_removed_transaction(self, tx_hash: str):
        """Process transaction removed from mempool"""
        try:
            # Update transaction status in database
            query = """
            MATCH (t:Transaction {hash: $hash, blockchain: $blockchain})
            SET t.status = 'removed_from_mempool',
                t.removed_at = timestamp()
            """

            from src.api.database import get_neo4j_session

            async with get_neo4j_session() as session:
                await session.run(query, hash=tx_hash, blockchain=self.blockchain)

        except Exception as e:
            logger.error(f"Error processing removed transaction {tx_hash}: {e}")

    async def alert_high_value_transaction(self, tx: Transaction):
        """Alert on high-value transactions"""
        logger.warning(
            f"High-value Bitcoin transaction detected: {tx.hash} - {tx.value} BTC"
        )

        # Store alert in database
        query = """
        MATCH (t:Transaction {hash: $hash, blockchain: $blockchain})
        MERGE (a:Alert {type: 'high_value_transaction'})
        MERGE (t)-[:TRIGGERED]->(a)
        SET a.value = $value,
            a.threshold = $threshold,
            a.created_at = timestamp()
        """

        from src.api.database import get_neo4j_session

        async with get_neo4j_session() as session:
            await session.run(
                query,
                hash=tx.hash,
                blockchain=self.blockchain,
                value=tx.value,
                threshold=10,
            )

    async def track_utxos(self, address: str):
        """Track UTXOs for an address via external indexer and cache in Redis."""
        if not self.utxo_tracking:
            return

        try:
            unspent = await self._fetch_utxos_from_indexer(address)
            if not unspent:
                return

            # Store UTXO information
            cache_key = f"utxos:bitcoin:{address}"

            from src.api.database import get_redis_connection

            async with get_redis_connection() as redis:
                utxo_data = {
                    "address": address,
                    "utxos": unspent,
                    # value is in satoshis from the indexer API
                    "total_value_sat": sum(utxo.get("value", 0) for utxo in unspent),
                    "total_value_btc": sum(utxo.get("value", 0) for utxo in unspent) / 1e8,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                await redis.setex(
                    cache_key, 300, json.dumps(utxo_data)
                )  # Cache for 5 minutes

        except Exception as e:
            logger.error(f"Error tracking UTXOs for address {address}: {e}")

    async def start_lightning_monitoring(self):
        """Start Lightning Network monitoring if enabled"""
        if not self.lightning_enabled:
            return

        try:
            from .lightning import LightningMonitor

            lightning_config = {
                "rpc_url": self.config.get("lnd_rpc_url", settings.LND_RPC_URL),
                "macaroon_path": self.config.get(
                    "lnd_macaroon_path", settings.LND_MACAROON_PATH
                ),
                "tls_cert_path": self.config.get(
                    "lnd_tls_cert_path", settings.LND_TLS_CERT_PATH
                ),
            }

            lightning_monitor = LightningMonitor(lightning_config)
            await lightning_monitor.start()

        except Exception as e:
            logger.error(f"Error starting Lightning monitoring: {e}")

    async def start(self):
        """Start Bitcoin collector with additional monitoring"""
        await super().start()

        # Start additional monitoring tasks
        if self.mempool_monitoring:
            asyncio.create_task(self.monitor_mempool())

        if self.lightning_enabled:
            asyncio.create_task(self.start_lightning_monitoring())

    async def get_network_stats(self) -> Dict[str, Any]:
        """Get Bitcoin network statistics"""
        try:
            blockchain_info = await self.rpc_call("getblockchaininfo")
            mining_info = await self.rpc_call("getmininginfo")
            network_info = await self.rpc_call("getnetworkinfo")

            return {
                "blockchain": self.blockchain,
                "blocks": blockchain_info.get("blocks"),
                "difficulty": blockchain_info.get("difficulty"),
                "network_hashps": mining_info.get("networkhashps"),
                "connections": network_info.get("connections"),
                "mempool_size": blockchain_info.get("mempoolinfo", {}).get("size", 0),
                "chain": blockchain_info.get("chain"),
                "warnings": blockchain_info.get("warnings", ""),
            }

        except Exception as e:
            logger.error(f"Error getting Bitcoin network stats: {e}")
            return {}
