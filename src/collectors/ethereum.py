"""
Jackdaw Sentry - Ethereum Collector
Ethereum and EVM-compatible blockchain data collection
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any, Union
from datetime import datetime
import json
import re
from web3 import Web3
from web3.middleware import geth_poa_middleware
from eth_utils import to_checksum_address, from_wei

from .base import BaseCollector, Transaction, Block, Address
from src.api.config import settings

logger = logging.getLogger(__name__)


class EthereumCollector(BaseCollector):
    """Ethereum and EVM-compatible blockchain collector"""
    
    def __init__(self, blockchain: str, config: Dict[str, Any]):
        super().__init__(blockchain, config)
        self.rpc_url = config.get('rpc_url')
        self.network = config.get('network')
        
        # EVM-specific settings
        self.erc20_tracking = config.get('erc20_tracking', True)
        self.contract_tracking = config.get('contract_tracking', True)
        self.event_tracking = config.get('event_tracking', True)
        
        # Stablecoin contracts for this blockchain
        self.stablecoin_contracts = self.get_stablecoin_contracts()
        
        self.w3 = None
        self.latest_block_cache = None
        self.cache_timeout = 30  # seconds
    
    def get_stablecoin_contracts(self) -> Dict[str, str]:
        """Get stablecoin contracts for this blockchain"""
        contracts = {}
        
        if self.blockchain == "ethereum":
            contracts.update({
                'USDT': '0xdAC17F958D2ee523a2206206994597C13D831ec7',
                'USDC': '0xA0b86a33E6441b6e8F9c2c2c4c4c4c4c4c4c4c4c',
                'EURC': '0x2A325e6831B0AD69618ebC6adD6f3B8c3C5d6B5f',
                'EURT': '0x0C10bF8FbC34C309b9F6D3394b5D1F5D6E7F8A9B'
            })
        elif self.blockchain == "bsc":
            contracts.update({
                'USDT': '0x55d398326f99059fF775485246999027B3197955',
                'USDC': '0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d',
                'BUSD': '0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56'
            })
        elif self.blockchain == "polygon":
            contracts.update({
                'USDT': '0xc2132D05D31c914a87C6611C10748AEb04B58e8F',
                'USDC': '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'
            })
        elif self.blockchain == "arbitrum":
            contracts.update({
                'USDT': '0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9',
                'USDC': '0xA0b86a33E6441b6e8F9c2c2c4c4c4c4c4c4c4c4c'
            })
        elif self.blockchain == "base":
            contracts.update({
                'USDC': '0xd9aAEc86B65D86f6A7B5B1b0c42FFA531770b969'
            })
        elif self.blockchain == "avalanche":
            contracts.update({
                'USDT': '0x9702230A8Ea53632f8Ee31f33D8d9B7644d6b7b',
                'USDC': '0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E'
            })
        
        return contracts
    
    async def connect(self) -> bool:
        """Connect to Ethereum RPC"""
        try:
            # Configure Web3
            self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
            
            # Add POA middleware for networks like BSC, Polygon
            if self.blockchain in ['bsc', 'polygon', 'arbitrum', 'base', 'avalanche']:
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
                hash=block_data['hash'].hex(),
                blockchain=self.blockchain,
                number=block_data['number'],
                timestamp=datetime.fromtimestamp(block_data['timestamp']),
                transaction_count=len(block_data['transactions']),
                parent_hash=block_data['parentHash'].hex(),
                miner=block_data['miner'],
                difficulty=str(block_data['difficulty']),
                size=block_data['size']
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
            block_number = tx_data['blockNumber']
            block_timestamp = None
            if block_number:
                block_data = self.w3.eth.get_block(block_number)
                if block_data:
                    block_timestamp = datetime.fromtimestamp(block_data['timestamp'])
            
            # Determine addresses
            from_address = tx_data['from']
            to_address = tx_data['to']
            
            # Handle contract creation
            if to_address is None:
                to_address = from_address  # Self-loop for contract creation
            
            # Convert value from wei
            value = from_wei(tx_data['value'], 'ether')
            
            # Calculate fee
            gas_used = receipt['gasUsed'] if receipt else 0
            gas_price = tx_data['gasPrice']
            fee = from_wei(gas_used * gas_price, 'ether') if gas_used and gas_price else 0
            
            # Get token transfers
            token_transfers = []
            if self.erc20_tracking and receipt:
                token_transfers = await self.get_token_transfers(tx_hash, receipt)
            
            return Transaction(
                hash=tx_hash,
                blockchain=self.blockchain,
                from_address=from_address,
                to_address=to_address,
                value=value,
                timestamp=block_timestamp or datetime.utcnow(),
                block_number=block_number,
                block_hash=tx_data['blockHash'].hex() if tx_data['blockHash'] else None,
                gas_used=gas_used,
                gas_price=gas_price,
                fee=fee,
                status="confirmed" if receipt and receipt['status'] == 1 else "failed",
                confirmations=receipt['confirmations'] if receipt else 0,
                contract_address=receipt['contractAddress'].hex() if receipt and receipt['contractAddress'] else None,
                token_transfers=token_transfers
            )
            
        except Exception as e:
            logger.error(f"Error getting {self.blockchain} transaction {tx_hash}: {e}")
        
        return None
    
    async def get_address_balance(self, address: str) -> float:
        """Get address balance in ETH"""
        try:
            if not self.w3:
                return 0.0
            
            checksum_address = to_checksum_address(address)
            balance_wei = self.w3.eth.get_balance(checksum_address)
            return from_wei(balance_wei, 'ether')
            
        except Exception as e:
            logger.error(f"Error getting {self.blockchain} address balance for {address}: {e}")
            return 0.0
    
    async def get_address_transactions(self, address: str, limit: int = 100) -> List[Transaction]:
        """Get address transaction history"""
        try:
            if not self.w3:
                return []
            
            checksum_address = to_checksum_address(address)
            
            # Get transactions using logs (more efficient than scanning all blocks)
            # This is a simplified approach - in production, you'd use a proper indexing service
            transactions = []
            
            # Get latest block and scan backwards
            latest_block = self.w3.eth.block_number
            start_block = max(0, latest_block - 10000)  # Limit scan range
            
            for block_num in range(latest_block, start_block, -1):
                if len(transactions) >= limit:
                    break
                
                block = self.w3.eth.get_block(block_num, full_transactions=True)
                if not block:
                    continue
                
                for tx in block['transactions']:
                    if (tx['from'] == checksum_address or 
                        (tx['to'] and tx['to'] == checksum_address)):
                        
                        tx_obj = await self.get_transaction(tx['hash'].hex())
                        if tx_obj:
                            transactions.append(tx_obj)
            
            return transactions[:limit]
            
        except Exception as e:
            logger.error(f"Error getting {self.blockchain} address transactions for {address}: {e}")
            return []
    
    async def get_block_transactions(self, block_number: int) -> List[str]:
        """Get transaction hashes for a block"""
        try:
            if not self.w3:
                return []
            
            block = self.w3.eth.get_block(block_number)
            if not block:
                return []
            
            return [tx['hash'].hex() for tx in block['transactions']]
            
        except Exception as e:
            logger.error(f"Error getting {self.blockchain} block transactions for {block_number}: {e}")
            return []
    
    async def get_token_transfers(self, tx_hash: str, receipt: Dict) -> List[Dict]:
        """Get ERC20 token transfers from transaction receipt"""
        transfers = []
        
        try:
            for log in receipt.get('logs', []):
                # Check if it's a Transfer event (topic0 = keccak256("Transfer(address,address,uint256)"))
                if (len(log['topics']) == 3 and 
                    log['topics'][0].hex() == '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'):
                    
                    # Parse Transfer event
                    from_address = '0x' + log['topics'][1].hex()[-40:]
                    to_address = '0x' + log['topics'][2].hex()[-40:]
                    
                    # Decode amount (first 32 bytes of data)
                    amount = int(log['data'].hex(), 16)
                    
                    # Check if this is a stablecoin
                    contract_address = log['address'].hex()
                    stablecoin_symbol = self.get_stablecoin_symbol(contract_address)
                    
                    if stablecoin_symbol:
                        # Get decimals for the token
                        decimals = await self.get_token_decimals(contract_address)
                        amount_adjusted = amount / (10 ** decimals)
                        
                        transfers.append({
                            'symbol': stablecoin_symbol,
                            'contract_address': contract_address,
                            'from_address': from_address,
                            'to_address': to_address,
                            'amount': amount_adjusted,
                            'decimals': decimals
                        })
        
        except Exception as e:
            logger.error(f"Error parsing token transfers for {tx_hash}: {e}")
        
        return transfers
    
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
            decimals_function = self.w3.sha3(text='decimals()').hex()[:10]
            
            result = self.w3.eth.call({
                'to': contract_address,
                'data': decimals_function
            })
            
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
            
            pending_filter = self.w3.eth.filter('pending')
            
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
                    if transfer['amount'] > 100000:  # > 100k USD equivalent
                        await self.alert_large_stablecoin_transfer(tx, transfer)
                        
        except Exception as e:
            logger.error(f"Error processing pending transaction {tx_hash}: {e}")
    
    async def alert_large_stablecoin_transfer(self, tx: Transaction, transfer: Dict):
        """Alert on large stablecoin transfers"""
        logger.warning(f"Large stablecoin transfer detected: {transfer['symbol']} {transfer['amount']} - {tx.hash}")
        
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
            await session.run(query,
                hash=tx.hash,
                blockchain=self.blockchain,
                symbol=transfer['symbol'],
                amount=transfer['amount'],
                threshold=100000
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
                'address': contract_address,
                'has_code': True,
                'bytecode_size': len(code)
            }
            
            # Try to get ERC20 token info
            try:
                # name() function
                name_function = self.w3.sha3(text='name()').hex()[:10]
                name_result = self.w3.eth.call({'to': checksum_address, 'data': name_function})
                if name_result:
                    info['name'] = self.w3.eth.contract(address=checksum_address).functions.name().call()
                
                # symbol() function
                symbol_function = self.w3.sha3(text='symbol()').hex()[:10]
                symbol_result = self.w3.eth.call({'to': checksum_address, 'data': symbol_function})
                if symbol_result:
                    info['symbol'] = self.w3.eth.contract(address=checksum_address).functions.symbol().call()
                
                # totalSupply() function
                supply_function = self.w3.sha3(text='totalSupply()').hex()[:10]
                supply_result = self.w3.eth.call({'to': checksum_address, 'data': supply_function})
                if supply_result:
                    info['total_supply'] = self.w3.eth.contract(address=checksum_address).functions.totalSupply().call()
                
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
            
            latest_block = self.w3.eth.get_block('latest')
            gas_price = self.w3.eth.gas_price
            
            return {
                'blockchain': self.blockchain,
                'block_number': latest_block['number'],
                'gas_price': from_wei(gas_price, 'gwei') if gas_price else 0,
                'difficulty': str(latest_block['difficulty']),
                'total_difficulty': str(latest_block['totalDifficulty']),
                'block_time': '12s',  # Ethereum block time
                'chain_id': self.w3.eth.chain_id
            }
            
        except Exception as e:
            logger.error(f"Error getting {self.blockchain} network stats: {e}")
            return {}
