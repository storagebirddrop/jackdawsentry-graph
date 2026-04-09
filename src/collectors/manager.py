"""
Jackdaw Sentry - Collector Manager
Manages all blockchain collectors and coordinates data collection
"""

import asyncio
import json
import logging
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from src.api.config import settings
from src.api.database import get_redis_connection

from .base import BaseCollector
from .address_ingest_worker import AddressIngestWorker
from .backfill import EventStoreBackfillWorker
from .bitcoin import BitcoinCollector
from .cosmos import CosmosCollector
from .ethereum import EthereumCollector
from .solana import SolanaCollector
from .starknet import StarknetCollector
from .token_metadata_backfill import TokenMetadataBackfillWorker
from .sui import SuiCollector
from .tron import TronCollector
from .xrpl import XrplCollector

logger = logging.getLogger(__name__)


class CollectorManager:
    """Manages all blockchain collectors"""

    def _resolve_blockchain(self, blockchain: str) -> str:
        """Resolve blockchain name to canonical key (handle aliases)."""
        return self._collector_aliases.get(blockchain, blockchain)

    def __init__(self):
        self.collectors: Dict[str, BaseCollector] = {}
        self._collector_aliases: Dict[str, str] = {"xrpl": "xrp"}  # alias -> canonical mapping
        self.is_running = False
        self.backfill_worker: Optional[EventStoreBackfillWorker] = None
        self.address_ingest_worker: Optional[AddressIngestWorker] = None
        self.token_metadata_backfill_worker: Optional[TokenMetadataBackfillWorker] = None
        self.health_check_interval = 300
        self.health_startup_grace_period = 30
        self.metrics = {
            "total_collectors": 0,
            "running_collectors": 0,
            "total_transactions": 0,
            "total_blocks": 0,
            "last_update": None,
        }

    @staticmethod
    def _http_url(primary: str, fallback: str) -> str:
        """Return the first HTTP(S) URL from primary/fallback pair.

        Web3.HTTPProvider cannot handle wss:// URLs.  If the primary is a
        WebSocket URL, fall back to the HTTP endpoint.
        """
        if primary and not primary.startswith("wss://"):
            return primary
        if fallback and not fallback.startswith("wss://"):
            return fallback
        return primary  # no HTTP option; caller handles the connection error

    async def initialize(self):
        """Initialize all collectors"""
        logger.info("Initializing blockchain collectors...")

        # Initialize Bitcoin collector
        if settings.BITCOIN_RPC_URL:
            bitcoin_config = {
                "rpc_url": settings.BITCOIN_RPC_URL,
                "rpc_user": settings.BITCOIN_RPC_USER,
                "rpc_password": settings.BITCOIN_RPC_PASSWORD,
                "network": settings.BITCOIN_NETWORK,
                "mempool_monitoring": True,
                "utxo_tracking": True,
                "lightning_enabled": True,
                "collection_interval": 60,
                "batch_size": 10,
            }
            self.collectors["bitcoin"] = BitcoinCollector(bitcoin_config)

        # Initialize Ethereum collector
        if settings.ETHEREUM_RPC_URL:
            ethereum_config = {
                "rpc_url": self._http_url(settings.ETHEREUM_RPC_URL, settings.ETHEREUM_RPC_FALLBACK),
                "network": settings.ETHEREUM_NETWORK,
                "erc20_tracking": True,
                "contract_tracking": True,
                "event_tracking": True,
                "collection_interval": 30,
                "batch_size": 20,
            }
            self.collectors["ethereum"] = EthereumCollector("ethereum", ethereum_config)

        # Initialize BSC collector
        if settings.BSC_RPC_URL:
            bsc_config = {
                "rpc_url": settings.BSC_RPC_URL,
                "network": settings.BSC_NETWORK,
                "erc20_tracking": True,
                "contract_tracking": True,
                "event_tracking": True,
                "collection_interval": 30,
                "batch_size": 20,
            }
            self.collectors["bsc"] = EthereumCollector("bsc", bsc_config)

        # Initialize Polygon collector
        if settings.POLYGON_RPC_URL:
            polygon_config = {
                "rpc_url": self._http_url(settings.POLYGON_RPC_URL, settings.POLYGON_RPC_FALLBACK),
                "network": settings.POLYGON_NETWORK,
                "erc20_tracking": True,
                "contract_tracking": True,
                "event_tracking": True,
                "collection_interval": 30,
                "batch_size": 20,
            }
            self.collectors["polygon"] = EthereumCollector("polygon", polygon_config)

        # Initialize Arbitrum collector
        if settings.ARBITRUM_RPC_URL:
            arbitrum_config = {
                "rpc_url": self._http_url(settings.ARBITRUM_RPC_URL, settings.ARBITRUM_RPC_FALLBACK),
                "network": settings.ARBITRUM_NETWORK,
                "erc20_tracking": True,
                "contract_tracking": True,
                "event_tracking": True,
                "collection_interval": 30,
                "batch_size": 20,
            }
            self.collectors["arbitrum"] = EthereumCollector("arbitrum", arbitrum_config)

        # Initialize Base collector
        if settings.BASE_RPC_URL:
            base_config = {
                "rpc_url": self._http_url(settings.BASE_RPC_URL, settings.BASE_RPC_FALLBACK),
                "network": settings.BASE_NETWORK,
                "erc20_tracking": True,
                "contract_tracking": True,
                "event_tracking": True,
                "collection_interval": 30,
                "batch_size": 20,
            }
            self.collectors["base"] = EthereumCollector("base", base_config)

        # Initialize Avalanche collector
        if settings.AVALANCHE_RPC_URL:
            avalanche_config = {
                "rpc_url": self._http_url(settings.AVALANCHE_RPC_URL, settings.AVALANCHE_RPC_FALLBACK),
                "network": settings.AVALANCHE_NETWORK,
                "erc20_tracking": True,
                "contract_tracking": True,
                "event_tracking": True,
                "collection_interval": 30,
                "batch_size": 20,
            }
            self.collectors["avalanche"] = EthereumCollector(
                "avalanche", avalanche_config
            )

        # Initialize Optimism collector
        if settings.OPTIMISM_RPC_URL:
            optimism_config = {
                "rpc_url": self._http_url(settings.OPTIMISM_RPC_URL, settings.OPTIMISM_RPC_FALLBACK),
                "network": settings.OPTIMISM_NETWORK,
                "erc20_tracking": True,
                "contract_tracking": True,
                "event_tracking": True,
                "collection_interval": 30,
                "batch_size": 20,
            }
            self.collectors["optimism"] = EthereumCollector("optimism", optimism_config)

        # Initialize Solana collector
        if settings.SOLANA_RPC_URL:
            solana_config = {
                "rpc_url": self._http_url(settings.SOLANA_RPC_URL, settings.SOLANA_RPC_FALLBACK),
                "network": settings.SOLANA_NETWORK,
                "collection_interval": 30,
                "batch_size": 50,
            }
            self.collectors["solana"] = SolanaCollector(solana_config)

        # Initialize Sui collector
        if settings.SUI_RPC_URL:
            sui_config = {
                "rpc_url": self._http_url(settings.SUI_RPC_URL, settings.SUI_RPC_FALLBACK),
                "network": settings.SUI_NETWORK,
                "collection_interval": 20,
                "batch_size": 20,
            }
            self.collectors["sui"] = SuiCollector(sui_config)

        # Initialize Starknet collector
        if settings.STARKNET_RPC_URL:
            starknet_config = {
                "rpc_url": self._http_url(settings.STARKNET_RPC_URL, settings.STARKNET_RPC_FALLBACK),
                "network": settings.STARKNET_NETWORK,
                "collection_interval": 20,
                "batch_size": 20,
            }
            self.collectors["starknet"] = StarknetCollector(starknet_config)

        # Initialize Tron collector
        if settings.TRON_RPC_URL:
            tron_config = {
                "rpc_url": settings.TRON_RPC_URL,
                "network": settings.TRON_NETWORK,
                "collection_interval": 60,
                "batch_size": 20,
            }
            self.collectors["tron"] = TronCollector(tron_config)

        # Initialize XRPL collector
        if settings.XRPL_RPC_URL:
            xrpl_config = {
                "rpc_url": settings.XRPL_RPC_URL,
                "network": settings.XRPL_NETWORK,
                "collection_interval": 15,
                "batch_size": 20,
            }
            self.collectors["xrp"] = XrplCollector(xrpl_config)  # canonical key only

        # Initialize Cosmos collector
        if settings.COSMOS_REST_URL:
            cosmos_config = {
                "rest_url": settings.COSMOS_REST_URL,
                "network": settings.COSMOS_NETWORK,
                "native_denom": "uatom",
                "collection_interval": 20,
                "batch_size": 10,
            }
            self.collectors["cosmos"] = CosmosCollector("cosmos", cosmos_config)

        # Initialize Injective collector
        if settings.INJECTIVE_REST_URL:
            injective_config = {
                "rest_url": settings.INJECTIVE_REST_URL,
                "network": settings.INJECTIVE_NETWORK,
                "native_denom": "inj",
                "collection_interval": 20,
                "batch_size": 10,
            }
            self.collectors["injective"] = CosmosCollector("injective", injective_config)

        self.metrics["total_collectors"] = len(set(self._resolve_blockchain(b) for b in self.collectors.keys()))
        logger.info(f"Initialized {len(self.collectors)} blockchain collectors")

    async def start_all(self):
        """Start all collectors"""
        if self.is_running:
            logger.warning("Collector manager is already running")
            return

        logger.info("Starting all blockchain collectors...")
        self.is_running = True

        # Start all collectors (dedupe to avoid double-starting aliased collectors)
        tasks = []
        seen_collectors = set()
        for blockchain, collector in self.collectors.items():
            canonical_blockchain = self._resolve_blockchain(blockchain)
            if canonical_blockchain not in seen_collectors:
                seen_collectors.add(canonical_blockchain)
                task = asyncio.create_task(self.start_collector(canonical_blockchain, collector))
                tasks.append(task)

        # Start metrics collection
        tasks.append(asyncio.create_task(self.collect_metrics()))

        # Start health monitoring
        tasks.append(asyncio.create_task(self.monitor_health()))

        if settings.AUTO_BACKFILL_RAW_EVENT_STORE:
            self.backfill_worker = EventStoreBackfillWorker(self.collectors)
            tasks.append(asyncio.create_task(self.backfill_worker.start()))

        # Always start address ingest worker — it processes investigator-driven
        # on-demand requests from address_ingest_queue regardless of backfill setting.
        self.address_ingest_worker = AddressIngestWorker(self.collectors)
        tasks.append(asyncio.create_task(self.address_ingest_worker.start()))

        if settings.TOKEN_METADATA_BACKFILL_ENABLED:
            self.token_metadata_backfill_worker = TokenMetadataBackfillWorker(self.collectors)
            tasks.append(asyncio.create_task(self.token_metadata_backfill_worker.start()))

        # Wait for all tasks
        await asyncio.gather(*tasks, return_exceptions=True)

    async def start_collector(self, blockchain: str, collector: BaseCollector):
        """Start individual collector"""
        try:
            logger.info(f"Starting {blockchain} collector...")
            await collector.start()
        except Exception as e:
            logger.error(f"Failed to start {blockchain} collector: {e}")

    async def stop_all(self):
        """Stop all collectors"""
        logger.info("Stopping all blockchain collectors...")
        self.is_running = False

        if self.backfill_worker is not None:
            await self.backfill_worker.stop()

        if self.address_ingest_worker is not None:
            await self.address_ingest_worker.stop()

        if self.token_metadata_backfill_worker is not None:
            await self.token_metadata_backfill_worker.stop()

        # Stop all collectors (dedupe to avoid double-stopping aliased collectors)
        tasks = []
        seen_collectors = set()
        for blockchain, collector in self.collectors.items():
            canonical_blockchain = self._resolve_blockchain(blockchain)
            if canonical_blockchain not in seen_collectors:
                seen_collectors.add(canonical_blockchain)
                task = asyncio.create_task(self.stop_collector(canonical_blockchain, collector))
                tasks.append(task)

        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info("All collectors stopped")

    async def stop_collector(self, blockchain: str, collector: BaseCollector):
        """Stop individual collector"""
        try:
            logger.info(f"Stopping {blockchain} collector...")
            await collector.stop()
        except Exception as e:
            logger.error(f"Failed to stop {blockchain} collector: {e}")

    async def restart_collector(self, blockchain: str):
        """Restart a specific collector"""
        if blockchain not in self.collectors:
            logger.error(f"Collector {blockchain} not found")
            return

        collector = self.collectors[blockchain]
        logger.info(f"Restarting {blockchain} collector...")

        try:
            await collector.stop()
            await asyncio.sleep(5)  # Wait before restart
            await collector.start()
            logger.info(f"Successfully restarted {blockchain} collector")
        except Exception as e:
            logger.error(f"Failed to restart {blockchain} collector: {e}")

    async def get_collector_status(self, blockchain: str) -> Optional[Dict]:
        """Get status of specific collector"""
        if blockchain not in self.collectors:
            return None

        collector = self.collectors[blockchain]
        return await collector.get_metrics()

    async def get_all_status(self) -> Dict[str, Any]:
        """Get status of all collectors"""
        status = {
            "manager": {
                "is_running": self.is_running,
                "total_collectors": len(self.collectors),
                "running_collectors": sum(
                    1 for c in self.collectors.values() if c.is_running
                ),
            },
            "collectors": {},
            "metrics": self.metrics,
        }

        # Get individual collector status
        for blockchain, collector in self.collectors.items():
            try:
                status["collectors"][blockchain] = await collector.get_metrics()
            except Exception as e:
                status["collectors"][blockchain] = {
                    "error": str(e),
                    "is_running": False,
                }

        return status

    async def collect_metrics(self):
        """Collect and aggregate metrics from all collectors"""
        while self.is_running:
            try:
                total_transactions = 0
                total_blocks = 0
                running_collectors = 0

                # Aggregate metrics from unique collectors only
                seen_collectors = set()
                for blockchain, collector in self.collectors.items():
                    canonical_blockchain = self._resolve_blockchain(blockchain)
                    if canonical_blockchain not in seen_collectors:
                        seen_collectors.add(canonical_blockchain)
                        metrics = await collector.get_metrics()
                        total_transactions += metrics.get("transactions_collected", 0)
                        total_blocks += metrics.get("blocks_processed", 0)
                        if metrics.get("is_running", False):
                            running_collectors += 1

                self.metrics.update(
                    {
                        "total_transactions": total_transactions,
                        "total_blocks": total_blocks,
                        "running_collectors": running_collectors,
                        "last_update": datetime.now(timezone.utc).isoformat(),
                    }
                )

                # Cache metrics in Redis
                await self.cache_metrics()

                await asyncio.sleep(60)  # Update every minute

            except Exception as e:
                logger.error(f"Error collecting metrics: {e}")
                await asyncio.sleep(30)

    async def cache_metrics(self):
        """Cache metrics in Redis"""
        try:
            async with get_redis_connection() as redis:
                await redis.setex(
                    "collector_metrics", 300, json.dumps(self.metrics)  # 5 minutes
                )
        except Exception as e:
            logger.error(f"Error caching metrics: {e}")

    async def monitor_health(self):
        """Monitor health of all collectors"""
        if self.health_startup_grace_period > 0:
            await asyncio.sleep(self.health_startup_grace_period)

        while self.is_running:
            try:
                # Monitor health of unique collectors only
                seen_collectors = set()
                for blockchain, collector in self.collectors.items():
                    canonical_blockchain = self._resolve_blockchain(blockchain)
                    if canonical_blockchain not in seen_collectors:
                        seen_collectors.add(canonical_blockchain)
                        if not collector.is_running:
                            logger.warning(
                                f"Collector {canonical_blockchain} is not running, attempting restart..."
                            )
                            asyncio.create_task(self.restart_collector(canonical_blockchain))

                await asyncio.sleep(self.health_check_interval)

            except Exception as e:
                logger.error(f"Error in health monitoring: {e}")
                await asyncio.sleep(60)

    async def get_network_stats(self) -> Dict[str, Any]:
        """Get network statistics from all collectors"""
        stats = {}

        # Get network stats from unique collectors only
        seen_collectors = set()
        for blockchain, collector in self.collectors.items():
            canonical_blockchain = self._resolve_blockchain(blockchain)
            if canonical_blockchain not in seen_collectors:
                seen_collectors.add(canonical_blockchain)
                try:
                    if hasattr(collector, "get_network_stats"):
                        stats[canonical_blockchain] = await collector.get_network_stats()
                except Exception as e:
                    logger.error(f"Error getting network stats for {canonical_blockchain}: {e}")

        return stats

    async def search_transactions(
        self, query: str, blockchains: List[str] = None
    ) -> List[Dict]:
        """Search for transactions across blockchains"""
        results = []

        # If no blockchains specified, search all
        if not blockchains:
            blockchains = list(self.collectors.keys())

        for blockchain in blockchains:
            if blockchain not in self.collectors:
                continue

            collector = self.collectors[blockchain]
            try:
                # This is a simplified search - in production, you'd use indexed search
                # For now, we'll search recent transactions
                transactions = await self.search_collector_transactions(
                    collector, query
                )
                results.extend(transactions)
            except Exception as e:
                logger.error(f"Error searching transactions in {blockchain}: {e}")

        return results

    async def search_collector_transactions(
        self, collector: BaseCollector, query: str
    ) -> List[Dict]:
        """Search transactions in a specific collector"""
        # This is a placeholder - implement actual search logic
        # In production, you'd use database indexes or search engines
        return []

    async def get_address_info(
        self, address: str, blockchain: str = None
    ) -> Dict[str, Any]:
        """Get address information from specific blockchain or all"""
        if blockchain:
            if blockchain not in self.collectors:
                return {"error": f"Blockchain {blockchain} not supported"}

            collector = self.collectors[blockchain]
            try:
                balance = await collector.get_address_balance(address)
                transactions = await collector.get_address_transactions(
                    address, limit=50
                )

                return {
                    "blockchain": blockchain,
                    "address": address,
                    "balance": balance,
                    "recent_transactions": len(transactions),
                    "transactions": [
                        tx.__dict__ for tx in transactions[:10]
                    ],  # Last 10
                }
            except Exception as e:
                return {"error": str(e)}
        else:
            # Search across all blockchains
            results = {}
            for bc, collector in self.collectors.items():
                try:
                    balance = await collector.get_address_balance(address)
                    if balance > 0:  # Only include if balance > 0
                        results[bc] = {"balance": balance, "blockchain": bc}
                except Exception:
                    pass

            return results

    async def get_stablecoin_transfers(
        self, symbol: str = None, time_range: int = 24
    ) -> List[Dict]:
        """Get stablecoin transfers across all blockchains"""
        transfers = []

        for blockchain, collector in self.collectors.items():
            try:
                # This would query the database for stablecoin transfers
                # For now, return empty list
                pass
            except Exception as e:
                logger.error(
                    f"Error getting stablecoin transfers from {blockchain}: {e}"
                )

        return transfers

    async def start(self):
        """Start the collector manager"""
        await self.initialize()
        await self.start_all()

    async def stop(self):
        """Stop the collector manager"""
        await self.stop_all()


# Global collector manager instance
_collector_manager: Optional[CollectorManager] = None


def get_collector_manager() -> CollectorManager:
    """Get global collector manager instance"""
    global _collector_manager
    if _collector_manager is None:
        _collector_manager = CollectorManager()
    return _collector_manager
