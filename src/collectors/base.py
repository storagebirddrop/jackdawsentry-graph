"""
Jackdaw Sentry - Base Blockchain Collector
Abstract base class for all blockchain collectors
"""

import asyncio
import hashlib
import json
import logging
from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from src.api.config import settings
from src.api.database import get_neo4j_session
from src.api.database import get_postgres_connection
from src.api.database import get_redis_connection

logger = logging.getLogger(__name__)


@dataclass
class TokenTransfer:
    """A single token transfer event emitted by a transaction.

    Replaces the opaque ``List[Dict]`` that was previously stored on
    ``Transaction.token_transfers``.  Using a typed dataclass makes these
    movements first-class graph objects that can be stored as ``:TRANSFER``
    relationships in Neo4j and traversed in Cypher.
    """

    tx_hash: str
    blockchain: str
    transfer_index: int
    asset_type: str  # erc20 | native | spl | trc20 | bep20 | internal
    asset_symbol: str
    from_address: str
    to_address: str
    amount_raw: str  # raw integer string (no decimals applied)
    amount_normalized: float  # human-readable amount
    asset_contract: Optional[str] = None
    fiat_value_at_transfer: Optional[float] = None
    # Canonical cross-chain asset identity (e.g. "usdt", "usdc", "btc").
    # Preserved across bridge wraps so USDT on ETH and USDT on BSC are
    # recognisable as the same asset in the investigation view.
    canonical_asset_id: Optional[str] = None


@dataclass
class UTXOInput:
    """One input (spent UTXO) in a Bitcoin-style transaction."""

    prev_tx_hash: str
    prev_output_index: int
    address: str
    value_satoshis: int
    sequence: int = 0xFFFFFFFF


@dataclass
class UTXOOutput:
    """One output (new UTXO) created by a Bitcoin-style transaction."""

    output_index: int
    value_satoshis: int
    script_type: str  # p2pkh | p2sh | p2wpkh | p2wsh | p2tr | op_return
    address: Optional[str] = None  # None for OP_RETURN outputs
    is_op_return: bool = False
    # Heuristic flags set by the collector or analyser.
    is_probable_change: bool = False


@dataclass
class Transaction:
    """Standardized transaction structure.

    Supports both account-based chains (EVM, Solana, Tron) and UTXO chains
    (Bitcoin).

    **Graph model**: This dataclass drives the bipartite Neo4j graph:
    ``(Address)-[:SENT]->(Transaction)-[:RECEIVED]->(Address)``
    Token movements are stored as ``(Transaction)-[:TRANSFER]->(Address)``.
    Never model value flow as a direct ``(Address)-[:SENT]->(Address)`` edge —
    that loses execution context and makes multi-output transactions impossible.
    """

    hash: str
    blockchain: str
    timestamp: datetime
    # --- Account-based fields (EVM / Solana / Tron) ---
    # from_address is Optional because UTXO transactions have N inputs, not
    # a single sender.  Collectors for account-based chains always set this.
    from_address: Optional[str] = None
    to_address: Optional[str] = None
    value: Optional[Union[float, str]] = None
    gas_used: Optional[int] = None
    gas_price: Optional[int] = None
    memo: Optional[str] = None
    contract_address: Optional[str] = None
    method_id: Optional[str] = None
    # Typed token transfer list.  Use TokenTransfer, never raw Dict.
    token_transfers: List[TokenTransfer] = field(default_factory=list)
    # --- UTXO fields (Bitcoin and UTXO-based chains) ---
    inputs: List[UTXOInput] = field(default_factory=list)
    outputs: List[UTXOOutput] = field(default_factory=list)
    # --- Shared metadata ---
    block_number: Optional[int] = None
    block_hash: Optional[str] = None
    fee: Optional[float] = None
    status: str = "confirmed"
    confirmations: int = 0
    # CoinJoin flag: True if this transaction has been heuristically identified
    # as a CoinJoin candidate.  Taint analysis MUST halt at CoinJoin txs and
    # flag as AMBIGUOUS — never silently propagate through.
    is_coinjoin: bool = False
    # Bridge flags set by the collector when a bridge contract interaction is detected.
    is_bridge_ingress: bool = False
    is_bridge_egress: bool = False
    bridge_protocol: Optional[str] = None


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
        self.collection_interval = config.get("collection_interval", 60)  # seconds

        # Performance metrics
        self.metrics = {
            "transactions_collected": 0,
            "blocks_processed": 0,
            "errors": 0,
            "last_collection": None,
            "collection_rate": 0.0,
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
    async def get_address_transactions(
        self, address: str, limit: int = 100
    ) -> List[Transaction]:
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
                self.metrics["errors"] += 1
                await asyncio.sleep(10)  # Wait before retrying

    async def collect_new_blocks(self):
        """Collect and process new blocks"""
        try:
            latest_block = await self.get_latest_block_number()

            if latest_block <= self.last_block_processed:
                return

            # Process blocks in batches
            batch_size = self.config.get("batch_size", 10)
            start_block = self.last_block_processed + 1
            end_block = min(latest_block, start_block + batch_size - 1)
            processed_blocks = 0

            for block_num in range(start_block, end_block + 1):
                await self.process_block(block_num)

                if self.last_block_processed > block_num:
                    logger.info(
                        "Advanced %s checkpoint to %s while processing block %s",
                        self.blockchain,
                        self.last_block_processed,
                        block_num,
                    )
                    break

                self.last_block_processed = max(self.last_block_processed, block_num)
                processed_blocks += 1

            # Save progress
            await self.save_last_processed_block()

            # Update metrics
            self.metrics["blocks_processed"] += processed_blocks
            self.metrics["last_collection"] = datetime.now(timezone.utc)

            if processed_blocks:
                processed_end = start_block + processed_blocks - 1
                logger.info(
                    f"Processed blocks {start_block}-{processed_end} for {self.blockchain}"
                )

        except Exception as e:
            logger.error(f"Error collecting blocks for {self.blockchain}: {e}")
            self.metrics["errors"] += 1

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
            logger.error(
                f"Error processing block {block_number} for {self.blockchain}: {e}"
            )

    async def process_transaction(self, tx_hash: str):
        """Process a single transaction."""
        try:
            tx = await self.get_transaction(tx_hash)
            if not tx:
                return

            # Store transaction in Neo4j using the bipartite model.
            await self.store_transaction(tx)

            # Dual-write raw facts to the PostgreSQL event store (ADR-002).
            # Gated by DUAL_WRITE_RAW_EVENT_STORE so the feature is safely
            # off until migration 006 has been applied in production.
            if settings.DUAL_WRITE_RAW_EVENT_STORE:
                asyncio.create_task(self._insert_raw_transaction(tx))
                if tx.token_transfers:
                    asyncio.create_task(self._insert_raw_token_transfers(tx))
                if tx.inputs:
                    asyncio.create_task(self._insert_raw_utxo_inputs(tx))
                if tx.outputs:
                    asyncio.create_task(self._insert_raw_utxo_outputs(tx))
                # Solana instruction-level data lives in a dedicated table so
                # that the SolanaChainCompiler can resolve ATA ownership and
                # instruction semantics without scanning raw_transactions.
                if tx.blockchain == "solana":
                    asyncio.create_task(self._insert_raw_solana_instructions(tx))

            # Update address info for account-based chains.
            if tx.from_address:
                await self.update_address_info(tx.from_address, tx)
            if tx.to_address:
                await self.update_address_info(tx.to_address, tx)

            # For UTXO chains, update info for each input/output address.
            for utxo_input in tx.inputs:
                await self.update_address_info(utxo_input.address, tx)
            for utxo_output in tx.outputs:
                if utxo_output.address and not utxo_output.is_op_return:
                    await self.update_address_info(utxo_output.address, tx)

            # Check for stablecoin transfers.
            await self.process_stablecoin_transfers(tx)

            # Process token transfers if present.
            if tx.token_transfers:
                await self.process_token_transfers(tx)

            # Update metrics
            self.metrics["transactions_collected"] += 1

        except Exception as e:
            logger.error(
                f"Error processing transaction {tx_hash} for {self.blockchain}: {e}"
            )

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
            await session.run(
                query,
                hash=block.hash,
                blockchain=block.blockchain,
                number=block.number,
                timestamp=block.timestamp,
                transaction_count=block.transaction_count,
                parent_hash=block.parent_hash,
                miner=block.miner,
                difficulty=block.difficulty,
                size=block.size,
            )

    async def store_transaction(self, tx: Transaction):
        """Store transaction in Neo4j using the bipartite graph model.

        The canonical model is:
            ``(Address)-[:SENT]->(Transaction)-[:RECEIVED]->(Address)``

        Token movements are stored as additional ``:TRANSFER`` relationships
        from the Transaction node to each destination Address.  UTXO inputs
        are stored as ``:SENT`` edges from each input Address to the
        Transaction; UTXO outputs are stored as ``:RECEIVED`` edges from the
        Transaction to each output Address (with UTXO metadata on the edge).

        Direct ``(Address)-[:SENT]->(Address)`` edges are never created here.
        """

        # ------------------------------------------------------------------
        # Normalise EVM addresses to lowercase for consistent lookups.
        # Bitcoin/Solana addresses are case-sensitive — leave them as-is.
        # ------------------------------------------------------------------
        def _norm(addr: Optional[str]) -> Optional[str]:
            """Return lowercase EVM address; leave non-EVM addresses unchanged."""
            if addr and addr.startswith("0x"):
                return addr.lower()
            return addr

        # Upsert the Transaction node.
        tx_query = """
        MERGE (t:Transaction {hash: $hash, blockchain: $blockchain})
        SET t.value = $value,
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
            t.method_id = $method_id,
            t.is_coinjoin = $is_coinjoin,
            t.is_bridge_ingress = $is_bridge_ingress,
            t.is_bridge_egress = $is_bridge_egress,
            t.bridge_protocol = $bridge_protocol,
            t.processed_at = timestamp()
        """

        async with get_neo4j_session() as session:
            await session.run(
                tx_query,
                hash=tx.hash,
                blockchain=tx.blockchain,
                value=float(tx.value) if tx.value is not None else None,
                timestamp=tx.timestamp,
                block_number=tx.block_number,
                block_hash=tx.block_hash,
                gas_used=int(tx.gas_used) if tx.gas_used is not None else None,
                gas_price=int(tx.gas_price) if tx.gas_price is not None else None,
                fee=float(tx.fee) if tx.fee is not None else None,
                status=tx.status,
                confirmations=tx.confirmations,
                memo=tx.memo,
                contract_address=tx.contract_address,
                method_id=getattr(tx, "method_id", None),
                is_coinjoin=tx.is_coinjoin,
                is_bridge_ingress=tx.is_bridge_ingress,
                is_bridge_egress=tx.is_bridge_egress,
                bridge_protocol=tx.bridge_protocol,
            )

        # ------------------------------------------------------------------
        # Account-based chains: single from/to pair
        # Model: (from_addr)-[:SENT]->(tx)-[:RECEIVED]->(to_addr)
        # ------------------------------------------------------------------
        if tx.from_address:
            from_addr = _norm(tx.from_address)
            account_from_query = """
            MATCH (t:Transaction {hash: $hash, blockchain: $blockchain})
            MERGE (from_addr:Address {address: $from_address, blockchain: $blockchain})
            ON CREATE SET from_addr.first_seen = $timestamp
            ON MATCH SET from_addr.last_seen = $timestamp,
                         from_addr.transaction_count = coalesce(from_addr.transaction_count, 0) + 1
                         /* Note: Non-atomic increment acceptable for approximate counts.
                            Race conditions may cause occasional inaccuracies but don't affect
                            core functionality. Exact accuracy not required for this use case. */
            MERGE (from_addr)-[:SENT {blockchain: $blockchain}]->(t)
            """
            async with get_neo4j_session() as session:
                await session.run(
                    account_from_query,
                    hash=tx.hash,
                    blockchain=tx.blockchain,
                    from_address=from_addr,
                    timestamp=tx.timestamp,
                )

        if tx.to_address:
            to_addr = _norm(tx.to_address)
            account_to_query = """
            MATCH (t:Transaction {hash: $hash, blockchain: $blockchain})
            MERGE (to_addr:Address {address: $to_address, blockchain: $blockchain})
            ON CREATE SET to_addr.first_seen = $timestamp
            ON MATCH SET to_addr.last_seen = $timestamp,
                         to_addr.transaction_count = coalesce(to_addr.transaction_count, 0) + 1
            MERGE (t)-[:RECEIVED {blockchain: $blockchain, value: $value}]->(to_addr)
            """
            async with get_neo4j_session() as session:
                await session.run(
                    account_to_query,
                    hash=tx.hash,
                    blockchain=tx.blockchain,
                    to_address=to_addr,
                    timestamp=tx.timestamp,
                    value=float(tx.value) if tx.value is not None else None,
                )

        # ------------------------------------------------------------------
        # UTXO chains: N inputs → Transaction → M outputs
        # Each input: (input_addr)-[:SENT]->(tx)
        # Each output: (tx)-[:RECEIVED {output_index, value_satoshis}]->(out_addr)
        # ------------------------------------------------------------------
        
        # Batch UTXO inputs
        if tx.inputs:
            input_query = """
            UNWIND $inputs AS input
            MATCH (t:Transaction {hash: $hash, blockchain: $blockchain})
            MERGE (in_addr:Address {address: input.address, blockchain: $blockchain})
            ON CREATE SET in_addr.first_seen = input.timestamp
            ON MATCH SET in_addr.last_seen = input.timestamp,
                         in_addr.transaction_count = coalesce(in_addr.transaction_count, 0) + 1
            MERGE (in_addr)-[:SENT {
                blockchain: $blockchain,
                prev_tx_hash: input.prev_tx_hash,
                prev_output_index: input.prev_output_index,
                value_satoshis: input.value_satoshis
            }]->(t)
            """
            
            input_params = [
                {
                    "address": utxo_input.address,
                    "timestamp": tx.timestamp,
                    "prev_tx_hash": utxo_input.prev_tx_hash,
                    "prev_output_index": utxo_input.prev_output_index,
                    "value_satoshis": utxo_input.value_satoshis
                }
                for utxo_input in tx.inputs
            ]
            
            async with get_neo4j_session() as session:
                await session.run(
                    input_query,
                    hash=tx.hash,
                    blockchain=tx.blockchain,
                    inputs=input_params
                )

        # Batch UTXO outputs
        valid_outputs = [
            utxo_output for utxo_output in tx.outputs
            if not utxo_output.is_op_return and utxo_output.address
        ]
        
        if valid_outputs:
            output_query = """
            UNWIND $outputs AS output
            MATCH (t:Transaction {hash: $hash, blockchain: $blockchain})
            MERGE (out_addr:Address {address: output.address, blockchain: $blockchain})
            ON CREATE SET out_addr.first_seen = output.timestamp, out_addr.transaction_count = 1
            ON MATCH SET out_addr.last_seen = output.timestamp, out_addr.transaction_count = coalesce(out_addr.transaction_count, 0) + 1
            MERGE (t)-[:RECEIVED {
                blockchain: $blockchain,
                output_index: output.output_index,
                value_satoshis: output.value_satoshis,
                script_type: output.script_type,
                is_probable_change: output.is_probable_change
            }]->(out_addr)
            """
            
            output_params = [
                {
                    "address": utxo_output.address,
                    "timestamp": tx.timestamp,
                    "output_index": utxo_output.output_index,
                    "value_satoshis": utxo_output.value_satoshis,
                    "script_type": utxo_output.script_type,
                    "is_probable_change": utxo_output.is_probable_change
                }
                for utxo_output in valid_outputs
            ]
            
            async with get_neo4j_session() as session:
                await session.run(
                    output_query,
                    hash=tx.hash,
                    blockchain=tx.blockchain,
                    outputs=output_params
                )

        # Address monitoring hook (fire-and-forget)
        try:
            from src.services.address_monitor import get_address_monitor
            monitor = get_address_monitor()
            addresses = []
            if tx.from_address:
                addresses.append(tx.from_address)
            if tx.to_address:
                addresses.append(tx.to_address)
            for t in (tx.token_transfers or []):
                addresses.extend([t.from_address, t.to_address])
            if addresses:
                asyncio.create_task(
                    monitor.notify_if_watched(tx.hash, addresses, tx.blockchain)
                )
        except Exception:
            pass  # monitoring is non-critical; never block indexing

    async def process_token_transfers(self, tx: Transaction):
        """Process token transfers using bipartite graph model.

        Token movements are stored as ``:TRANSFER`` relationships from the
        Transaction node to each destination Address.  This keeps the core
        ``(Address)-[:SENT]->(Transaction)-[:RECEIVED]->(Address)`` model
        clean while still capturing token flow.

        Args:
            tx: Transaction with token_transfers list
        """
        if not tx.token_transfers:
            return

        for transfer in tx.token_transfers:
            transfer_query = """
            MATCH (t:Transaction {hash: $hash, blockchain: $blockchain})
            MERGE (to_addr:Address {address: $to_address, blockchain: $blockchain})
            ON CREATE SET to_addr.first_seen = $timestamp
            MERGE (t)-[:TRANSFER {
                transfer_index: $transfer_index,
                asset_type: $asset_type,
                asset_symbol: $asset_symbol,
                asset_contract: $asset_contract,
                from_address: $from_address,
                to_address: $to_address,
                amount_raw: $amount_raw,
                amount_normalized: $amount_normalized,
                fiat_value_at_transfer: $fiat_value_at_transfer,
                canonical_asset_id: $canonical_asset_id,
                blockchain: $blockchain
            }]->(to_addr)
            """
            async with get_neo4j_session() as session:
                await session.run(
                    transfer_query,
                    hash=tx.hash,
                    blockchain=tx.blockchain,
                    to_address=transfer.to_address,
                    timestamp=tx.timestamp,
                    transfer_index=transfer.transfer_index,
                    asset_type=transfer.asset_type,
                    asset_symbol=transfer.asset_symbol,
                    asset_contract=transfer.asset_contract,
                    from_address=transfer.from_address,
                    amount_raw=transfer.amount_raw,
                    amount_normalized=transfer.amount_normalized,
                    fiat_value_at_transfer=transfer.fiat_value_at_transfer,
                    canonical_asset_id=transfer.canonical_asset_id,
                )

    async def update_address_info(self, address: str, tx: Transaction):
        """Update address information"""
        # Cache address info in Redis for performance
        cache_key = f"address:{self.blockchain}:{address}"

        async with get_redis_connection() as redis:
            cached_info = await redis.get(cache_key)

            if cached_info:
                info = json.loads(cached_info)
                info["last_seen"] = tx.timestamp.isoformat()
                info["transaction_count"] += 1
            else:
                balance = await self.get_address_balance(address)
                info = {
                    "address": address,
                    "blockchain": self.blockchain,
                    "balance": str(balance),
                    "transaction_count": 1,
                    "first_seen": tx.timestamp.isoformat(),
                    "last_seen": tx.timestamp.isoformat(),
                    "type": "unknown",
                    "risk_score": 0.0,
                    "labels": [],
                }

            await redis.setex(cache_key, 3600, json.dumps(info))  # Cache for 1 hour

    async def process_stablecoin_transfers(self, tx: Transaction):
        """Link stablecoin TokenTransfer objects to their Stablecoin nodes.

        Each matching ``TokenTransfer`` gets a ``:STABLECOIN_TRANSFER``
        relationship from the ``Transaction`` node to the ``Stablecoin`` node.
        The ``:TRANSFER`` edge to the destination ``Address`` is created by
        ``store_transaction()``.
        """
        if not tx.token_transfers:
            return

        supported_stablecoins = get_supported_stablecoins()

        for transfer in tx.token_transfers:
            if transfer.asset_symbol not in supported_stablecoins:
                continue

            query = """
            MATCH (t:Transaction {hash: $tx_hash})
            MERGE (s:Stablecoin {symbol: $symbol, blockchain: $blockchain})
            SET s += $stablecoinProps
            MERGE (t)-[r:STABLECOIN_TRANSFER]->(s)
            SET r.amount_normalized = $amount_normalized,
                r.amount_raw = $amount_raw,
                r.from_address = $from_address,
                r.to_address = $to_address,
                r.transfer_index = $transfer_index,
                r.canonical_asset_id = $canonical_asset_id
            """

            async with get_neo4j_session() as session:
                result = await session.run(
                    query,
                    tx_hash=tx.hash,
                    symbol=transfer.asset_symbol,
                    blockchain=self.blockchain,
                    stablecoinProps={
                        "name": transfer.asset_symbol,
                        "type": "stablecoin",
                        "updated_at": datetime.now(timezone.utc)
                    },
                    amount_normalized=transfer.amount_normalized,
                    amount_raw=transfer.amount_raw,
                    from_address=transfer.from_address,
                    to_address=transfer.to_address,
                    transfer_index=transfer.transfer_index,
                    canonical_asset_id=transfer.canonical_asset_id,
                )
                
                # Check if relationship was created
                summary = result.consume()
                if summary.relationships_created == 0:
                    logger.debug(
                        f"Failed to create STABLECOIN_TRANSFER relationship for tx {tx.hash} "
                        f"and stablecoin {transfer.asset_symbol}"
                    )

    # ------------------------------------------------------------------
    # Raw event store writers (ADR-002)
    # All methods are fire-and-forget via asyncio.create_task.
    # Failures are logged at DEBUG level and never propagate to the caller.
    # ------------------------------------------------------------------

    async def _insert_raw_transaction(self, tx: Transaction) -> None:
        """Write a single raw transaction fact to the PostgreSQL event store.

        Uses INSERT … ON CONFLICT DO NOTHING so re-indexing a block is safe.

        Args:
            tx: Transaction dataclass produced by the collector.
        """
        query = """
            INSERT INTO raw_transactions (
                blockchain, tx_hash, block_number, timestamp,
                from_address, to_address,
                value_native,
                gas_used, gas_price, status,
                is_bridge_ingress, is_bridge_egress, bridge_protocol
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6,
                $7,
                $8, $9, $10,
                $11, $12, $13
            )
            ON CONFLICT (blockchain, tx_hash) DO NOTHING
        """
        try:
            async with get_postgres_connection() as conn:
                await conn.execute(
                    query,
                    tx.blockchain,
                    tx.hash,
                    tx.block_number,
                    tx.timestamp,
                    tx.from_address,
                    tx.to_address,
                    float(tx.value) if tx.value is not None else None,
                    tx.gas_used,
                    tx.gas_price,
                    tx.status,
                    tx.is_bridge_ingress,
                    tx.is_bridge_egress,
                    tx.bridge_protocol,
                )
        except Exception as exc:
            logger.warning(
                "dual-write _insert_raw_transaction failed for %s/%s: %s — "
                "event store parity loss; investigate before T1.15 cutover",
                tx.blockchain,
                tx.hash,
                exc,
            )

    async def _insert_raw_token_transfers(self, tx: Transaction) -> None:
        """Write token transfer facts to the PostgreSQL event store.

        Uses INSERT … ON CONFLICT DO NOTHING for idempotency.

        Args:
            tx: Transaction whose token_transfers list will be persisted.
        """
        query = """
            INSERT INTO raw_token_transfers (
                blockchain, tx_hash, transfer_index,
                asset_symbol, asset_contract, canonical_asset_id,
                from_address, to_address,
                amount_raw, amount_normalized,
                timestamp
            ) VALUES (
                $1, $2, $3,
                $4, $5, $6,
                $7, $8,
                $9, $10,
                $11
            )
            ON CONFLICT (blockchain, tx_hash, transfer_index) DO NOTHING
        """
        try:
            async with get_postgres_connection() as conn:
                await conn.executemany(
                    query,
                    [
                        (
                            t.blockchain,
                            t.tx_hash,
                            t.transfer_index,
                            t.asset_symbol,
                            t.asset_contract,
                            t.canonical_asset_id,
                            t.from_address,
                            t.to_address,
                            str(t.amount_raw) if t.amount_raw is not None else None,
                            t.amount_normalized,
                            tx.timestamp,
                        )
                        for t in tx.token_transfers
                    ],
                )
        except Exception as exc:
            logger.warning(
                "dual-write _insert_raw_token_transfers failed for %s/%s: %s — "
                "event store parity loss; investigate before T1.15 cutover",
                tx.blockchain,
                tx.hash,
                exc,
            )

    async def _insert_raw_utxo_inputs(self, tx: Transaction) -> None:
        """Write UTXO input facts to the PostgreSQL event store.

        Args:
            tx: Transaction with inputs list (Bitcoin-style).
        """
        query = """
            INSERT INTO raw_utxo_inputs (
                blockchain, tx_hash, input_index,
                prev_tx_hash, prev_output_index,
                address, value_satoshis, sequence, timestamp
            ) VALUES (
                $1, $2, $3,
                $4, $5,
                $6, $7, $8, $9
            )
            ON CONFLICT (blockchain, tx_hash, input_index) DO NOTHING
        """
        try:
            async with get_postgres_connection() as conn:
                await conn.executemany(
                    query,
                    [
                        (
                            tx.blockchain,
                            tx.hash,
                            idx,
                            inp.prev_tx_hash,
                            inp.prev_output_index,
                            inp.address,
                            inp.value_satoshis,
                            inp.sequence,
                            tx.timestamp,
                        )
                        for idx, inp in enumerate(tx.inputs)
                    ],
                )
        except Exception as exc:
            logger.warning(
                "dual-write _insert_raw_utxo_inputs failed for %s/%s: %s — "
                "event store parity loss; investigate before T1.15 cutover",
                tx.blockchain,
                tx.hash,
                exc,
            )

    async def _insert_raw_utxo_outputs(self, tx: Transaction) -> None:
        """Write UTXO output facts to the PostgreSQL event store.

        Args:
            tx: Transaction with outputs list (Bitcoin-style).
        """
        query = """
            INSERT INTO raw_utxo_outputs (
                blockchain, tx_hash, output_index,
                address, value_satoshis, script_type,
                is_probable_change, timestamp
            ) VALUES (
                $1, $2, $3,
                $4, $5, $6,
                $7, $8
            )
            ON CONFLICT (blockchain, tx_hash, output_index) DO NOTHING
        """
        try:
            async with get_postgres_connection() as conn:
                await conn.executemany(
                    query,
                    [
                        (
                            tx.blockchain,
                            tx.hash,
                            out.output_index,
                            out.address,
                            out.value_satoshis,
                            out.script_type,
                            out.is_probable_change,
                            tx.timestamp,
                        )
                        for out in tx.outputs
                    ],
                )
        except Exception as exc:
            logger.warning(
                "dual-write _insert_raw_utxo_outputs failed for %s/%s: %s — "
                "event store parity loss; investigate before T1.15 cutover",
                tx.blockchain,
                tx.hash,
                exc,
            )

    async def _insert_raw_solana_instructions(self, tx: Transaction) -> None:
        """Write Solana instruction facts to the PostgreSQL event store.

        Solana transactions are instruction bundles — a single ``tx_hash`` may
        contain SPL token transfers, native SOL moves, and program invocations.
        This method writes one row per instruction derived from the transaction's
        ``token_transfers`` list (which the Solana collector populates from
        ``parseTokenBalances`` / ``message.instructions``).

        The ``raw_solana_instructions`` table is the primary data source for
        ``SolanaChainCompiler`` ATA resolution and SPL transfer expansion.

        Args:
            tx: Transaction with ``blockchain == "solana"`` and a populated
                ``token_transfers`` list produced by the Solana collector.
        """
        if not tx.token_transfers:
            return

        query = """
            INSERT INTO raw_solana_instructions (
                blockchain, tx_hash, instruction_index,
                program_id,
                from_address, to_address,
                asset_symbol, asset_contract, canonical_asset_id,
                amount_raw, amount_normalized,
                timestamp
            ) VALUES (
                $1, $2, $3,
                $4,
                $5, $6,
                $7, $8, $9,
                $10, $11,
                $12
            )
            ON CONFLICT (blockchain, tx_hash, instruction_index) DO NOTHING
        """
        try:
            async with get_postgres_connection() as conn:
                await conn.executemany(
                    query,
                    [
                        (
                            tx.blockchain,
                            tx.hash,
                            t.transfer_index,
                            t.program_id if hasattr(t, "program_id") else t.asset_contract,  # Use program_id if available, otherwise mint
                            t.from_address,
                            t.to_address,
                            t.asset_symbol,
                            t.asset_contract,
                            t.canonical_asset_id,
                            str(t.amount_raw) if t.amount_raw is not None else None,
                            t.amount_normalized,
                            tx.timestamp,
                        )
                        for t in tx.token_transfers
                    ],
                )
        except Exception as exc:
            logger.warning(
                "dual-write _insert_raw_solana_instructions failed for solana/%s: %s — "
                "event store parity loss; investigate before T1.15 cutover",
                tx.hash,
                exc,
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
            "blockchain": self.blockchain,
            "is_running": self.is_running,
            "last_block_processed": self.last_block_processed,
            "collection_interval": self.collection_interval,
            **self.metrics,
        }

    @abstractmethod
    async def get_block_transactions(self, block_number: int) -> List[str]:
        """Get transaction hashes for a block"""
        pass


def get_supported_stablecoins() -> List[str]:
    """Get list of supported stablecoins"""
    return [
        "USDT",
        "USDC",
        "RLUSD",
        "USDe",
        "USDS",
        "USD1",
        "BUSD",
        "A7A5",
        "EURC",
        "EURT",
        "BRZ",
        "EURS",
    ]


def hash_address(address: str) -> str:
    """Hash address for GDPR compliance"""
    return hashlib.sha256(address.encode()).hexdigest()
