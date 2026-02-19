"""
Jackdaw Sentry - Bitcoin Collector
Bitcoin blockchain data collection with Lightning Network support
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any, Union
from datetime import datetime, timezone
import json
import struct
import binascii

from .base import BaseCollector, Transaction, Block, Address
from src.api.config import settings

logger = logging.getLogger(__name__)


class BitcoinCollector(BaseCollector):
    """Bitcoin blockchain collector with Lightning Network support"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__("bitcoin", config)
        self.rpc_url = config.get('rpc_url', settings.BITCOIN_RPC_URL)
        self.rpc_user = config.get('rpc_user', settings.BITCOIN_RPC_USER)
        self.rpc_password = config.get('rpc_password', settings.BITCOIN_RPC_PASSWORD)
        self.network = config.get('network', settings.BITCOIN_NETWORK)
        
        # Bitcoin-specific settings
        self.mempool_monitoring = config.get('mempool_monitoring', True)
        self.utxo_tracking = config.get('utxo_tracking', True)
        self.lightning_enabled = config.get('lightning_enabled', True)
        
        self.rpc_session = None
        self.mempool = set()
    
    async def connect(self) -> bool:
        """Connect to Bitcoin RPC"""
        try:
            import aiohttp
            
            self.rpc_session = aiohttp.ClientSession(
                auth=aiohttp.BasicAuth(self.rpc_user, self.rpc_password),
                timeout=aiohttp.ClientTimeout(total=30)
            )
            
            # Test connection
            info = await self.rpc_call("getblockchaininfo")
            if info:
                logger.info(f"Connected to Bitcoin {self.network} (chain: {info.get('chain')})")
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
                "params": params or []
            }
            
            async with self.rpc_session.post(
                self.rpc_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get('result')
                else:
                    logger.error(f"Bitcoin RPC error: {response.status}")
                    
        except Exception as e:
            logger.error(f"Bitcoin RPC call failed: {e}")
        
        return None
    
    async def get_latest_block_number(self) -> int:
        """Get latest block number"""
        info = await self.rpc_call("getblockchaininfo")
        return info.get('blocks', 0) if info else 0
    
    async def get_block(self, block_number: int) -> Optional[Block]:
        """Get block by number"""
        try:
            # Get block hash first
            block_hash = await self.rpc_call("getblockhash", [block_number])
            if not block_hash:
                return None
            
            # Get full block
            block_data = await self.rpc_call("getblock", [block_hash, 2])  # verbosity 2 for full tx data
            if not block_data:
                return None
            
            return Block(
                hash=block_data['hash'],
                blockchain=self.blockchain,
                number=block_data['height'],
                timestamp=datetime.fromtimestamp(block_data['time'], tz=timezone.utc),
                transaction_count=len(block_data['tx']),
                parent_hash=block_data.get('previousblockhash'),
                miner=block_data.get('miner'),
                difficulty=str(block_data.get('difficulty', 0)),
                size=block_data.get('size')
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
            block_hash = tx_data.get('blockhash')
            block_number = None
            block_timestamp = None
            
            if block_hash:
                block_data = await self.rpc_call("getblock", [block_hash])
                if block_data:
                    block_number = block_data.get('height')
                    block_timestamp = datetime.fromtimestamp(block_data.get('time'), tz=timezone.utc)
            
            # Calculate inputs and outputs
            total_input = 0
            from_address = None
            to_address = None
            value = 0
            
            # Process inputs
            if 'vin' in tx_data:
                for vin in tx_data['vin']:
                    if 'coinbase' in vin:
                        # Coinbase transaction
                        from_address = "coinbase"
                    else:
                        # Regular input - get previous output
                        prev_tx = await self.rpc_call("getrawtransaction", [vin['txid'], True])
                        if prev_tx and 'vout' in prev_tx:
                            prev_out = prev_tx['vout'][vin['vout']]
                            total_input += prev_out.get('value', 0)
                            
                            if 'scriptPubKey' in prev_out and 'addresses' in prev_out['scriptPubKey']:
                                addresses = prev_out['scriptPubKey']['addresses']
                                if addresses:
                                    from_address = addresses[0]
            
            # Process outputs
            if 'vout' in tx_data:
                for vout in tx_data['vout']:
                    if 'scriptPubKey' in vout and 'addresses' in vout['scriptPubKey']:
                        addresses = vout['scriptPubKey']['addresses']
                        if addresses:
                            if not to_address:
                                to_address = addresses[0]
                            value += vout.get('value', 0)
            
            # Calculate fee
            fee = total_input - value if from_address != "coinbase" else 0
            
            return Transaction(
                hash=tx_data['txid'],
                blockchain=self.blockchain,
                from_address=from_address or "unknown",
                to_address=to_address,
                value=value,
                timestamp=block_timestamp or datetime.now(timezone.utc),
                block_number=block_number,
                block_hash=block_hash,
                fee=fee,
                status="confirmed" if block_number else "mempool",
                confirmations=tx_data.get('confirmations', 0)
            )
            
        except Exception as e:
            logger.error(f"Error getting Bitcoin transaction {tx_hash}: {e}")
        
        return None
    
    async def get_address_balance(self, address: str) -> float:
        """Get address balance"""
        try:
            unspent = await self.rpc_call("listunspent", [0, 9999999, [address]])
            balance = sum(utxo.get('amount', 0) for utxo in unspent or [])
            return balance
        except Exception as e:
            logger.error(f"Error getting Bitcoin address balance for {address}: {e}")
            return 0.0
    
    async def get_address_transactions(self, address: str, limit: int = 100) -> List[Transaction]:
        """Get address transaction history"""
        try:
            # Use listunspent to get transaction history
            unspent = await self.rpc_call("listunspent", [0, 9999999, [address]])
            if not unspent:
                return []
            
            transactions = []
            for utxo in unspent[:limit]:
                tx = await self.get_transaction(utxo['txid'])
                if tx:
                    transactions.append(tx)
            
            return transactions
            
        except Exception as e:
            logger.error(f"Error getting Bitcoin address transactions for {address}: {e}")
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
            
            return block_data.get('tx', [])
            
        except Exception as e:
            logger.error(f"Error getting Bitcoin block transactions for {block_number}: {e}")
            return []
    
    async def monitor_mempool(self):
        """Monitor Bitcoin mempool for new transactions"""
        if not self.mempool_monitoring:
            return
        
        logger.info("Starting Bitcoin mempool monitoring...")
        
        while self.is_running:
            try:
                # Get current mempool
                mempool_info = await self.rpc_call("getrawmempool", [False])  # verbose = False
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
        logger.warning(f"High-value Bitcoin transaction detected: {tx.hash} - {tx.value} BTC")
        
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
            await session.run(query,
                hash=tx.hash,
                blockchain=self.blockchain,
                value=tx.value,
                threshold=10
            )
    
    async def track_utxos(self, address: str):
        """Track UTXOs for an address"""
        if not self.utxo_tracking:
            return
        
        try:
            unspent = await self.rpc_call("listunspent", [0, 9999999, [address]])
            if not unspent:
                return
            
            # Store UTXO information
            cache_key = f"utxos:bitcoin:{address}"
            
            from src.api.database import get_redis_connection
            async with get_redis_connection() as redis:
                utxo_data = {
                    'address': address,
                    'utxos': unspent,
                    'total_value': sum(utxo.get('amount', 0) for utxo in unspent),
                    'updated_at': datetime.now(timezone.utc).isoformat()
                }
                await redis.setex(cache_key, 300, json.dumps(utxo_data))  # Cache for 5 minutes
                
        except Exception as e:
            logger.error(f"Error tracking UTXOs for address {address}: {e}")
    
    async def start_lightning_monitoring(self):
        """Start Lightning Network monitoring if enabled"""
        if not self.lightning_enabled:
            return
        
        try:
            from .lightning import LightningMonitor
            lightning_config = {
                'rpc_url': self.config.get('lnd_rpc_url', settings.LND_RPC_URL),
                'macaroon_path': self.config.get('lnd_macaroon_path', settings.LND_MACAROON_PATH),
                'tls_cert_path': self.config.get('lnd_tls_cert_path', settings.LND_TLS_CERT_PATH)
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
                'blockchain': self.blockchain,
                'blocks': blockchain_info.get('blocks'),
                'difficulty': blockchain_info.get('difficulty'),
                'network_hashps': mining_info.get('networkhashps'),
                'connections': network_info.get('connections'),
                'mempool_size': blockchain_info.get('mempoolinfo', {}).get('size', 0),
                'chain': blockchain_info.get('chain'),
                'warnings': blockchain_info.get('warnings', '')
            }
            
        except Exception as e:
            logger.error(f"Error getting Bitcoin network stats: {e}")
            return {}
