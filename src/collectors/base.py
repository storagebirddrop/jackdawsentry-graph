"""
Jackdaw Sentry - Base Blockchain Collector
Abstract base class for all blockchain collectors
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Union
from datetime import datetime, timedelta
from dataclasses import dataclass
import json
import hashlib

from src.api.database import get_neo4j_session, get_redis_connection
from src.api.config import settings

logger = logging.getLogger(__name__)


@dataclass
class Transaction:
    """Standardized transaction structure"""
    hash: str
    blockchain: str
    from_address: str
    to_address: Optional[str]
    value: Union[float, str]
    timestamp: datetime
    block_number: Optional[int] = None
    block_hash: Optional[str] = None
    gas_used: Optional[int] = None
    gas_price: Optional[int] = None
    fee: Optional[float] = None
    status: str = "confirmed"
    confirmations: int = 0
    memo: Optional[str] = None
    contract_address: Optional[str] = None
    token_transfers: List[Dict] = None
    
    def __post_init__(self):
        if self.token_transfers is None:
            self.token_transfers = []


@dataclass
class Block:
    """Standardized block structure"""
    hash: str
    blockchain: str
    number: int
    timestamp: datetime
    transaction_count: int
    parent_hash: Optional[str] = None
    miner: Optional[str] = None
    difficulty: Optional[str] = None
    size: Optional[int] = None


@dataclass
class Address:
    """Standardized address structure"""
    address: str
    blockchain: str
    balance: Union[float, str] = 0
    transaction_count: int = 0
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    type: str = "unknown"  # eoa, contract, exchange, mixer, etc.
    risk_score: float = 0.0
    labels: List[str] = None
    
    def __post_init__(self):
        if self.labels is None:
            self.labels = []


class BaseCollector(ABC):
    """Abstract base class for blockchain collectors"""
    
    def __init__(self, blockchain: str, config: Dict[str, Any]):
        self.blockchain = blockchain
        self.config = config
        self.is_running = False
        self.last_block_processed = 0
        self.collection_interval = config.get('collection_interval', 60)  # seconds
        
        # Performance metrics
        self.metrics = {
            'transactions_collected': 0,
            'blocks_processed': 0,
            'errors': 0,
            'last_collection': None,
            'collection_rate': 0.0
        }
    
    @abstractmethod
    async def connect(self) -> bool:
        """Connect to blockchain node/rpc"""
        pass
    
    @abstractmethod
    async def disconnect(self):
        """Disconnect from blockchain node/rpc"""
        pass
    
    @abstractmethod
    async def get_latest_block_number(self) -> int:
        """Get latest block number"""
        pass
    
    @abstractmethod
    async def get_block(self, block_number: int) -> Optional[Block]:
        """Get block by number"""
        pass
    
    @abstractmethod
    async def get_transaction(self, tx_hash: str) -> Optional[Transaction]:
        """Get transaction by hash"""
        pass
    
    @abstractmethod
    async def get_address_balance(self, address: str) -> Union[float, str]:
        """Get address balance"""
        pass
    
    @abstractmethod
    async def get_address_transactions(self, address: str, limit: int = 100) -> List[Transaction]:
        """Get address transaction history"""
        pass
    
    async def start(self):
        """Start the collector"""
        logger.info(f"Starting {self.blockchain} collector...")
        
        if not await self.connect():
            logger.error(f"Failed to connect to {self.blockchain}")
            return
        
        self.is_running = True
        
        try:
            # Load last processed block
            await self.load_last_processed_block()
            
            # Start collection loop
            await self.collection_loop()
            
        except Exception as e:
            logger.error(f"Error in {self.blockchain} collector: {e}")
        finally:
            await self.stop()
    
    async def stop(self):
        """Stop the collector"""
        logger.info(f"Stopping {self.blockchain} collector...")
        self.is_running = False
        await self.disconnect()
    
    async def collection_loop(self):
        """Main collection loop"""
        while self.is_running:
            try:
                await self.collect_new_blocks()
                await asyncio.sleep(self.collection_interval)
            except Exception as e:
                logger.error(f"Error in collection loop for {self.blockchain}: {e}")
                self.metrics['errors'] += 1
                await asyncio.sleep(10)  # Wait before retrying
    
    async def collect_new_blocks(self):
        """Collect and process new blocks"""
        try:
            latest_block = await self.get_latest_block_number()
            
            if latest_block <= self.last_block_processed:
                return
            
            # Process blocks in batches
            batch_size = self.config.get('batch_size', 10)
            start_block = self.last_block_processed + 1
            end_block = min(latest_block, start_block + batch_size - 1)
            
            for block_num in range(start_block, end_block + 1):
                await self.process_block(block_num)
                self.last_block_processed = block_num
            
            # Save progress
            await self.save_last_processed_block()
            
            # Update metrics
            self.metrics['blocks_processed'] += (end_block - start_block + 1)
            self.metrics['last_collection'] = datetime.now(timezone.utc)
            
            logger.info(f"Processed blocks {start_block}-{end_block} for {self.blockchain}")
            
        except Exception as e:
            logger.error(f"Error collecting blocks for {self.blockchain}: {e}")
            self.metrics['errors'] += 1
    
    async def process_block(self, block_number: int):
        """Process a single block"""
        try:
            block = await self.get_block(block_number)
            if not block:
                return
            
            # Store block in Neo4j
            await self.store_block(block)
            
            # Process transactions
            for tx_hash in await self.get_block_transactions(block_number):
                await self.process_transaction(tx_hash)
                
        except Exception as e:
            logger.error(f"Error processing block {block_number} for {self.blockchain}: {e}")
    
    async def process_transaction(self, tx_hash: str):
        """Process a single transaction"""
        try:
            tx = await self.get_transaction(tx_hash)
            if not tx:
                return
            
            # Store transaction in Neo4j
            await self.store_transaction(tx)
            
            # Update address information
            await self.update_address_info(tx.from_address, tx)
            if tx.to_address:
                await self.update_address_info(tx.to_address, tx)
            
            # Check for stablecoin transfers
            await self.process_stablecoin_transfers(tx)
            
            # Update metrics
            self.metrics['transactions_collected'] += 1
            
        except Exception as e:
            logger.error(f"Error processing transaction {tx_hash} for {self.blockchain}: {e}")
    
    async def store_block(self, block: Block):
        """Store block in Neo4j"""
        query = """
        MERGE (b:Block {hash: $hash, blockchain: $blockchain})
        SET b.number = $number,
            b.timestamp = $timestamp,
            b.transaction_count = $transaction_count,
            b.parent_hash = $parent_hash,
            b.miner = $miner,
            b.difficulty = $difficulty,
            b.size = $size,
            b.processed_at = timestamp()
        """
        
        async with get_neo4j_session() as session:
            await session.run(query, 
                hash=block.hash,
                blockchain=block.blockchain,
                number=block.number,
                timestamp=block.timestamp,
                transaction_count=block.transaction_count,
                parent_hash=block.parent_hash,
                miner=block.miner,
                difficulty=block.difficulty,
                size=block.size
            )
    
    async def store_transaction(self, tx: Transaction):
        """Store transaction in Neo4j"""
        query = """
        MERGE (t:Transaction {hash: $hash, blockchain: $blockchain})
        SET t.from_address = $from_address,
            t.to_address = $to_address,
            t.value = $value,
            t.timestamp = $timestamp,
            t.block_number = $block_number,
            t.block_hash = $block_hash,
            t.gas_used = $gas_used,
            t.gas_price = $gas_price,
            t.fee = $fee,
            t.status = $status,
            t.confirmations = $confirmations,
            t.memo = $memo,
            t.contract_address = $contract_address,
            t.processed_at = timestamp()
        
        // Create or update from address
        MERGE (from_addr:Address {address: $from_address, blockchain: $blockchain})
        ON CREATE SET from_addr.first_seen = $timestamp
        ON MATCH SET from_addr.last_seen = $timestamp,
                     from_addr.transaction_count = from_addr.transaction_count + 1
        
        // Create or update to address if exists
        """
        
        if tx.to_address:
            query += """
        MERGE (to_addr:Address {address: $to_address, blockchain: $blockchain})
        ON CREATE SET to_addr.first_seen = $timestamp
        ON MATCH SET to_addr.last_seen = $timestamp,
                     to_addr.transaction_count = to_addr.transaction_count + 1
        
        // Create relationships
        MERGE (from_addr)-[r:SENT]->(to_addr)
        SET r.transaction_hash = $hash,
            r.value = $value,
            r.timestamp = $timestamp,
            r.blockchain = $blockchain,
            r.gas_used = $gas_used,
            r.fee = $fee,
            r.status = $status
            """
        else:
            query += """
        // Create self-loop for contract creation or mining rewards
        MERGE (from_addr)-[r:SENT]->(from_addr)
        SET r.transaction_hash = $hash,
            r.value = $value,
            r.timestamp = $timestamp,
            r.blockchain = $blockchain,
            r.gas_used = $gas_used,
            r.fee = $fee,
            r.status = $status
            """
        
        async with get_neo4j_session() as session:
            await session.run(query,
                hash=tx.hash,
                blockchain=tx.blockchain,
                from_address=tx.from_address,
                to_address=tx.to_address,
                value=tx.value,
                timestamp=tx.timestamp,
                block_number=tx.block_number,
                block_hash=tx.block_hash,
                gas_used=tx.gas_used,
                gas_price=tx.gas_price,
                fee=tx.fee,
                status=tx.status,
                confirmations=tx.confirmations,
                memo=tx.memo,
                contract_address=tx.contract_address
            )
    
    async def update_address_info(self, address: str, tx: Transaction):
        """Update address information"""
        # Cache address info in Redis for performance
        cache_key = f"address:{self.blockchain}:{address}"
        
        async with get_redis_connection() as redis:
            cached_info = await redis.get(cache_key)
            
            if cached_info:
                info = json.loads(cached_info)
                info['last_seen'] = tx.timestamp.isoformat()
                info['transaction_count'] += 1
            else:
                balance = await self.get_address_balance(address)
                info = {
                    'address': address,
                    'blockchain': self.blockchain,
                    'balance': str(balance),
                    'transaction_count': 1,
                    'first_seen': tx.timestamp.isoformat(),
                    'last_seen': tx.timestamp.isoformat(),
                    'type': 'unknown',
                    'risk_score': 0.0,
                    'labels': []
                }
            
            await redis.setex(cache_key, 3600, json.dumps(info))  # Cache for 1 hour
    
    async def process_stablecoin_transfers(self, tx: Transaction):
        """Process stablecoin transfers and create cross-chain relationships"""
        if not tx.token_transfers:
            return
        
        for transfer in tx.token_transfers:
            stablecoin_symbol = transfer.get('symbol')
            if not stablecoin_symbol:
                continue
            
            # Check if this is a supported stablecoin
            supported_stablecoins = get_supported_stablecoins()
            if stablecoin_symbol not in supported_stablecoins:
                continue
            
            # Create stablecoin transfer relationship
            query = """
            MATCH (t:Transaction {hash: $tx_hash})
            MATCH (s:Stablecoin {symbol: $symbol, blockchain: $blockchain})
            MERGE (t)-[r:STABLECOIN_TRANSFER]->(s)
            SET r.amount = $amount,
                r.from_address = $from_address,
                r.to_address = $to_address,
                r.decimals = $decimals
            """
            
            async with get_neo4j_session() as session:
                await session.run(query,
                    tx_hash=tx.hash,
                    symbol=stablecoin_symbol,
                    blockchain=self.blockchain,
                    amount=transfer.get('amount'),
                    from_address=transfer.get('from_address'),
                    to_address=transfer.get('to_address'),
                    decimals=transfer.get('decimals', 18)
                )
    
    async def load_last_processed_block(self):
        """Load last processed block from Redis"""
        cache_key = f"last_block:{self.blockchain}"
        
        async with get_redis_connection() as redis:
            last_block = await redis.get(cache_key)
            if last_block:
                self.last_block_processed = int(last_block)
            else:
                # Start from latest block minus 100 for safety
                self.last_block_processed = await self.get_latest_block_number() - 100
    
    async def save_last_processed_block(self):
        """Save last processed block to Redis"""
        cache_key = f"last_block:{self.blockchain}"
        
        async with get_redis_connection() as redis:
            await redis.set(cache_key, self.last_block_processed)
    
    async def get_metrics(self) -> Dict[str, Any]:
        """Get collector metrics"""
        return {
            'blockchain': self.blockchain,
            'is_running': self.is_running,
            'last_block_processed': self.last_block_processed,
            'collection_interval': self.collection_interval,
            **self.metrics
        }
    
    @abstractmethod
    async def get_block_transactions(self, block_number: int) -> List[str]:
        """Get transaction hashes for a block"""
        pass


def get_supported_stablecoins() -> List[str]:
    """Get list of supported stablecoins"""
    return [
        "USDT", "USDC", "RLUSD", "USDe", "USDS", "USD1",
        "BUSD", "A7A5", "EURC", "EURT", "BRZ", "EURS"
    ]


def hash_address(address: str) -> str:
    """Hash address for GDPR compliance"""
    return hashlib.sha256(address.encode()).hexdigest()
