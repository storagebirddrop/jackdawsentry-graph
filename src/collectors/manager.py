"""
Jackdaw Sentry - Collector Manager
Manages all blockchain collectors and coordinates data collection
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
import json

from .bitcoin import BitcoinCollector
from .ethereum import EthereumCollector
from .solana import SolanaCollector
from .tron import TronCollector
from .base import BaseCollector
from src.api.config import settings
from src.api.database import get_redis_connection

logger = logging.getLogger(__name__)


class CollectorManager:
    """Manages all blockchain collectors"""
    
    def __init__(self):
        self.collectors: Dict[str, BaseCollector] = {}
        self.is_running = False
        self.metrics = {
            'total_collectors': 0,
            'running_collectors': 0,
            'total_transactions': 0,
            'total_blocks': 0,
            'last_update': None
        }
    
    async def initialize(self):
        """Initialize all collectors"""
        logger.info("Initializing blockchain collectors...")
        
        # Initialize Bitcoin collector
        if settings.BITCOIN_RPC_URL:
            bitcoin_config = {
                'rpc_url': settings.BITCOIN_RPC_URL,
                'rpc_user': settings.BITCOIN_RPC_USER,
                'rpc_password': settings.BITCOIN_RPC_PASSWORD,
                'network': settings.BITCOIN_NETWORK,
                'mempool_monitoring': True,
                'utxo_tracking': True,
                'lightning_enabled': True,
                'collection_interval': 60,
                'batch_size': 10
            }
            self.collectors['bitcoin'] = BitcoinCollector(bitcoin_config)
        
        # Initialize Ethereum collector
        if settings.ETHEREUM_RPC_URL:
            ethereum_config = {
                'rpc_url': settings.ETHEREUM_RPC_URL,
                'network': settings.ETHEREUM_NETWORK,
                'erc20_tracking': True,
                'contract_tracking': True,
                'event_tracking': True,
                'collection_interval': 30,
                'batch_size': 20
            }
            self.collectors['ethereum'] = EthereumCollector('ethereum', ethereum_config)
        
        # Initialize BSC collector
        if settings.BSC_RPC_URL:
            bsc_config = {
                'rpc_url': settings.BSC_RPC_URL,
                'network': settings.BSC_NETWORK,
                'erc20_tracking': True,
                'contract_tracking': True,
                'event_tracking': True,
                'collection_interval': 30,
                'batch_size': 20
            }
            self.collectors['bsc'] = EthereumCollector('bsc', bsc_config)
        
        # Initialize Polygon collector
        if settings.POLYGON_RPC_URL:
            polygon_config = {
                'rpc_url': settings.POLYGON_RPC_URL,
                'network': settings.POLYGON_NETWORK,
                'erc20_tracking': True,
                'contract_tracking': True,
                'event_tracking': True,
                'collection_interval': 30,
                'batch_size': 20
            }
            self.collectors['polygon'] = EthereumCollector('polygon', polygon_config)
        
        # Initialize Arbitrum collector
        if settings.ARBITRUM_RPC_URL:
            arbitrum_config = {
                'rpc_url': settings.ARBITRUM_RPC_URL,
                'network': settings.ARBITRUM_NETWORK,
                'erc20_tracking': True,
                'contract_tracking': True,
                'event_tracking': True,
                'collection_interval': 30,
                'batch_size': 20
            }
            self.collectors['arbitrum'] = EthereumCollector('arbitrum', arbitrum_config)
        
        # Initialize Base collector
        if settings.BASE_RPC_URL:
            base_config = {
                'rpc_url': settings.BASE_RPC_URL,
                'network': settings.BASE_NETWORK,
                'erc20_tracking': True,
                'contract_tracking': True,
                'event_tracking': True,
                'collection_interval': 30,
                'batch_size': 20
            }
            self.collectors['base'] = EthereumCollector('base', base_config)
        
        # Initialize Avalanche collector
        if settings.AVALANCHE_RPC_URL:
            avalanche_config = {
                'rpc_url': settings.AVALANCHE_RPC_URL,
                'network': settings.AVALANCHE_NETWORK,
                'erc20_tracking': True,
                'contract_tracking': True,
                'event_tracking': True,
                'collection_interval': 30,
                'batch_size': 20
            }
            self.collectors['avalanche'] = EthereumCollector('avalanche', avalanche_config)
        
        # Initialize Solana collector
        if settings.SOLANA_RPC_URL:
            solana_config = {
                'rpc_url': settings.SOLANA_RPC_URL,
                'network': settings.SOLANA_NETWORK,
                'collection_interval': 30,
                'batch_size': 50
            }
            self.collectors['solana'] = SolanaCollector(solana_config)
        
        # Initialize Tron collector
        if settings.TRON_RPC_URL:
            tron_config = {
                'rpc_url': settings.TRON_RPC_URL,
                'network': settings.TRON_NETWORK,
                'collection_interval': 60,
                'batch_size': 20
            }
            self.collectors['tron'] = TronCollector(tron_config)
        
        self.metrics['total_collectors'] = len(self.collectors)
        logger.info(f"Initialized {len(self.collectors)} blockchain collectors")
    
    async def start_all(self):
        """Start all collectors"""
        if self.is_running:
            logger.warning("Collector manager is already running")
            return
        
        logger.info("Starting all blockchain collectors...")
        self.is_running = True
        
        # Start all collectors
        tasks = []
        for blockchain, collector in self.collectors.items():
            task = asyncio.create_task(self.start_collector(blockchain, collector))
            tasks.append(task)
        
        # Start metrics collection
        tasks.append(asyncio.create_task(self.collect_metrics()))
        
        # Start health monitoring
        tasks.append(asyncio.create_task(self.monitor_health()))
        
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
        
        # Stop all collectors
        tasks = []
        for blockchain, collector in self.collectors.items():
            task = asyncio.create_task(self.stop_collector(blockchain, collector))
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
            'manager': {
                'is_running': self.is_running,
                'total_collectors': len(self.collectors),
                'running_collectors': sum(1 for c in self.collectors.values() if c.is_running)
            },
            'collectors': {},
            'metrics': self.metrics
        }
        
        # Get individual collector status
        for blockchain, collector in self.collectors.items():
            try:
                status['collectors'][blockchain] = await collector.get_metrics()
            except Exception as e:
                status['collectors'][blockchain] = {
                    'error': str(e),
                    'is_running': False
                }
        
        return status
    
    async def collect_metrics(self):
        """Collect and aggregate metrics from all collectors"""
        while self.is_running:
            try:
                total_transactions = 0
                total_blocks = 0
                running_collectors = 0
                
                for collector in self.collectors.values():
                    metrics = await collector.get_metrics()
                    total_transactions += metrics.get('transactions_collected', 0)
                    total_blocks += metrics.get('blocks_processed', 0)
                    if metrics.get('is_running', False):
                        running_collectors += 1
                
                self.metrics.update({
                    'total_transactions': total_transactions,
                    'total_blocks': total_blocks,
                    'running_collectors': running_collectors,
                    'last_update': datetime.now(timezone.utc).isoformat()
                })
                
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
                    'collector_metrics',
                    300,  # 5 minutes
                    json.dumps(self.metrics)
                )
        except Exception as e:
            logger.error(f"Error caching metrics: {e}")
    
    async def monitor_health(self):
        """Monitor health of all collectors"""
        while self.is_running:
            try:
                for blockchain, collector in self.collectors.items():
                    if not collector.is_running:
                        logger.warning(f"Collector {blockchain} is not running, attempting restart...")
                        asyncio.create_task(self.restart_collector(blockchain))
                
                await asyncio.sleep(300)  # Check every 5 minutes
                
            except Exception as e:
                logger.error(f"Error in health monitoring: {e}")
                await asyncio.sleep(60)
    
    async def get_network_stats(self) -> Dict[str, Any]:
        """Get network statistics from all collectors"""
        stats = {}
        
        for blockchain, collector in self.collectors.items():
            try:
                if hasattr(collector, 'get_network_stats'):
                    stats[blockchain] = await collector.get_network_stats()
            except Exception as e:
                logger.error(f"Error getting network stats for {blockchain}: {e}")
                stats[blockchain] = {'error': str(e)}
        
        return stats
    
    async def search_transactions(self, query: str, blockchains: List[str] = None) -> List[Dict]:
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
                transactions = await self.search_collector_transactions(collector, query)
                results.extend(transactions)
            except Exception as e:
                logger.error(f"Error searching transactions in {blockchain}: {e}")
        
        return results
    
    async def search_collector_transactions(self, collector: BaseCollector, query: str) -> List[Dict]:
        """Search transactions in a specific collector"""
        # This is a placeholder - implement actual search logic
        # In production, you'd use database indexes or search engines
        return []
    
    async def get_address_info(self, address: str, blockchain: str = None) -> Dict[str, Any]:
        """Get address information from specific blockchain or all"""
        if blockchain:
            if blockchain not in self.collectors:
                return {'error': f'Blockchain {blockchain} not supported'}
            
            collector = self.collectors[blockchain]
            try:
                balance = await collector.get_address_balance(address)
                transactions = await collector.get_address_transactions(address, limit=50)
                
                return {
                    'blockchain': blockchain,
                    'address': address,
                    'balance': balance,
                    'recent_transactions': len(transactions),
                    'transactions': [tx.__dict__ for tx in transactions[:10]]  # Last 10
                }
            except Exception as e:
                return {'error': str(e)}
        else:
            # Search across all blockchains
            results = {}
            for bc, collector in self.collectors.items():
                try:
                    balance = await collector.get_address_balance(address)
                    if balance > 0:  # Only include if balance > 0
                        results[bc] = {
                            'balance': balance,
                            'blockchain': bc
                        }
                except Exception:
                    pass
            
            return results
    
    async def get_stablecoin_transfers(self, symbol: str = None, 
                                    time_range: int = 24) -> List[Dict]:
        """Get stablecoin transfers across all blockchains"""
        transfers = []
        
        for blockchain, collector in self.collectors.items():
            try:
                # This would query the database for stablecoin transfers
                # For now, return empty list
                pass
            except Exception as e:
                logger.error(f"Error getting stablecoin transfers from {blockchain}: {e}")
        
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
