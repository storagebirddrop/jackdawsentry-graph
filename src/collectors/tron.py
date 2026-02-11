"""
Jackdaw Sentry - Tron Collector
Tron blockchain data collection
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any, Union
from datetime import datetime
import json
import base64

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

from .base import BaseCollector, Transaction, Block, Address
from src.api.config import settings

logger = logging.getLogger(__name__)


class TronCollector(BaseCollector):
    """Tron blockchain collector"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__("tron", config)
        self.rpc_url = config.get('rpc_url', settings.TRON_RPC_URL)
        self.network = config.get('network', settings.TRON_NETWORK)
        
        # Tron-specific settings
        self.trc20_tracking = config.get('trc20_tracking', True)
        self.contract_tracking = config.get('contract_tracking', True)
        
        # Tron stablecoin contracts
        self.stablecoin_contracts = {
            'USDT': 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'
        }
        
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
            
            # Test connection
            info = await self.rpc_call("wallet/getnodeinfo")
            if info:
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
        """Make Tron RPC call"""
        if not self.session:
            return None
        
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "parameters": params or {}
            }
            
            async with self.session.post(
                self.rpc_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get('result')
                else:
                    logger.error(f"Tron RPC error: {response.status}")
                    
        except Exception as e:
            logger.error(f"Tron RPC call failed: {e}")
        
        return None
    
    async def get_latest_block_number(self) -> int:
        """Get latest block number"""
        try:
            info = await self.rpc_call("wallet/getnowblock")
            return info.get('block_header', {}).get('raw_data', {}).get('number', 0) if info else 0
            
        except Exception as e:
            logger.error(f"Error getting latest Tron block: {e}")
            return 0
    
    async def get_block(self, block_number: int) -> Optional[Block]:
        """Get block by number"""
        try:
            block_data = await self.rpc_call("wallet/getblockbynum", {
                "num": block_number
            })
            
            if not block_data:
                return None
            
            block_header = block_data.get('block_header', {}).get('raw_data', {})
            transactions = block_data.get('transactions', [])
            
            return Block(
                hash=block_header.get('txTrieRoot', ''),
                blockchain=self.blockchain,
                number=block_number,
                timestamp=datetime.fromtimestamp(block_header.get('timestamp', 0) / 1000),
                transaction_count=len(transactions),
                parent_hash=block_header.get('parentHash'),
                miner=block_header.get('witness_address', ''),
                difficulty=None,
                size=len(str(block_data))
            )
            
        except Exception as e:
            logger.error(f"Error getting Tron block {block_number}: {e}")
        
        return None
    
    async def get_transaction(self, tx_hash: str) -> Optional[Transaction]:
        """Get transaction by hash"""
        try:
            tx_data = await self.rpc_call("wallet/gettransactionbyid", {
                "value": tx_hash
            })
            
            if not tx_data:
                return None
            
            raw_data = tx_data.get('raw_data', {})
            contract_data = raw_data.get('contract', [])
            
            # Parse transaction based on contract type
            from_address = raw_data.get('owner_address', '')
            to_address = None
            value = 0
            contract_address = None
            
            if contract_data:
                contract_type = contract_data[0].get('type', '')
                
                if contract_type == 'TransferContract':
                    # TRX transfer
                    transfer = contract_data[0].get('value', {}).get('amount', 0)
                    to_address = contract_data[0].get('value', {}).get('to_address', '')
                    value = transfer / 1_000_000  # Convert from sun to TRX
                
                elif contract_type == 'TransferAssetContract':
                    # TRC10 token transfer
                    asset_transfer = contract_data[0].get('value', {})
                    to_address = asset_transfer.get('to_address', '')
                    value = asset_transfer.get('amount', 0)
                
                elif contract_type == 'TriggerSmartContract':
                    # TRC20 token transfer or contract interaction
                    trigger = contract_data[0].get('value', {})
                    contract_address = trigger.get('contract_address', '')
                    parameter = trigger.get('data', '')
                    
                    # Parse TRC20 transfer function call
                    if parameter.startswith('a9059cbb'):  # transfer function signature
                        # This is a simplified parsing - would need more complex logic
                        pass
            
            # Get block info
            ref_block = raw_data.get('ref_block_hash', '')
            block_number = None
            block_timestamp = None
            
            if ref_block:
                # Would need to query block by hash to get full info
                block_timestamp = datetime.fromtimestamp(raw_data.get('timestamp', 0) / 1000)
            
            # Get token transfers
            token_transfers = []
            if self.trc20_tracking and contract_data:
                token_transfers = await self.parse_trc20_transfers(tx_data)
            
            return Transaction(
                hash=tx_hash,
                blockchain=self.blockchain,
                from_address=self.base58_to_hex(from_address) if from_address else "unknown",
                to_address=self.base58_to_hex(to_address) if to_address else None,
                value=value,
                timestamp=block_timestamp or datetime.utcnow(),
                block_number=block_number,
                block_hash=ref_block,
                contract_address=self.base58_to_hex(contract_address) if contract_address else None,
                token_transfers=token_transfers
            )
            
        except Exception as e:
            logger.error(f"Error getting Tron transaction {tx_hash}: {e}")
        
        return None
    
    async def get_address_balance(self, address: str) -> float:
        """Get address balance in TRX"""
        try:
            account_data = await self.rpc_call("wallet/getaccount", {
                "address": address
            })
            
            if account_data:
                balance = account_data.get('balance', 0)
                return balance / 1_000_000  # Convert from sun to TRX
            
        except Exception as e:
            logger.error(f"Error getting Tron address balance for {address}: {e}")
        
        return 0.0
    
    async def get_address_transactions(self, address: str, limit: int = 100) -> List[Transaction]:
        """Get address transaction history"""
        try:
            # Tron API doesn't have a direct method for address transactions
            # This would typically use a third-party indexer
            # For now, return empty list
            return []
            
        except Exception as e:
            logger.error(f"Error getting Tron address transactions for {address}: {e}")
            return []
    
    async def get_block_transactions(self, block_number: int) -> List[str]:
        """Get transaction hashes for a block"""
        try:
            block_data = await self.rpc_call("wallet/getblockbynum", {
                "num": block_number
            })
            
            if not block_data:
                return []
            
            transactions = block_data.get('transactions', [])
            return [tx.get('txID', '') for tx in transactions]
            
        except Exception as e:
            logger.error(f"Error getting Tron block transactions for {block_number}: {e}")
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
        """Convert hex address to Base58"""
        try:
            if BASE58_AVAILABLE:
                return base58.b58encode(bytes.fromhex(hex_address)).decode()
            else:
                return hex_address
        except Exception:
            return hex_address
    
    async def parse_trc20_transfers(self, tx_data: Dict) -> List[Dict]:
        """Parse TRC20 token transfers from transaction"""
        transfers = []
        
        try:
            contract_data = tx_data.get('raw_data', {}).get('contract', [])
            
            for contract in contract_data:
                if contract.get('type') == 'TriggerSmartContract':
                    trigger = contract.get('value', {})
                    contract_address = trigger.get('contract_address', '')
                    parameter = trigger.get('data', '')
                    
                    # Check if this is a stablecoin contract
                    stablecoin_symbol = None
                    for symbol, address in self.stablecoin_contracts.items():
                        if contract_address == address:
                            stablecoin_symbol = symbol
                            break
                    
                    if stablecoin_symbol and parameter.startswith('a9059cbb'):
                        # Parse transfer parameters (simplified)
                        # Would need proper ABI decoding here
                        transfers.append({
                            'symbol': stablecoin_symbol,
                            'contract_address': contract_address,
                            'from_address': '',  # Would parse from parameter
                            'to_address': '',   # Would parse from parameter
                            'amount': 0,       # Would parse from parameter
                            'decimals': 6       # USDT on Tron uses 6 decimals
                        })
        
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
                'blockchain': self.blockchain,
                'block_number': latest_block.get('block_header', {}).get('raw_data', {}).get('number', 0) if latest_block else 0,
                'block_time': '3s',  # Tron block time
                'active_nodes': node_info.get('activeNodeCount', 0) if node_info else 0,
                'total_nodes': node_info.get('totalNodeCount', 0) if node_info else 0
            }
            
        except Exception as e:
            logger.error(f"Error getting Tron network stats: {e}")
            return {}
