"""
Jackdaw Sentry - Solana Collector
Solana blockchain data collection
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any, Union
from datetime import datetime
import json

# Try to import Solana dependencies, but don't fail if not available
try:
    import base58
    from solders.pubkey import Pubkey
    from solana.rpc.async_api import AsyncClient
    from solana.rpc.types import RPCResponse
    SOLANA_AVAILABLE = True
except ImportError:
    SOLANA_AVAILABLE = False
    
    # Fallback for base58
    try:
        import base58
    except ImportError:
        base58 = None

from .base import BaseCollector, Transaction, Block, Address
from src.api.config import settings

logger = logging.getLogger(__name__)


class SolanaCollector(BaseCollector):
    """Solana blockchain collector"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__("solana", config)
        self.rpc_url = config.get('rpc_url', settings.SOLANA_RPC_URL)
        self.network = config.get('network', settings.SOLANA_NETWORK)
        
        # Solana-specific settings
        self.token_tracking = config.get('token_tracking', True)
        self.program_tracking = config.get('program_tracking', True)
        
        # Solana stablecoin contracts
        self.stablecoin_mints = {
            'USDT': 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB',
            'USDC': 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
        }
        
        self.client = None
    
    async def connect(self) -> bool:
        """Connect to Solana RPC"""
        if not SOLANA_AVAILABLE:
            logger.warning("Solana dependencies not available, skipping connection")
            return False
            
        try:
            self.client = AsyncClient(self.rpc_url)
            
            # Test connection
            health = await self.client.get_health()
            if health.value == 'ok':
                logger.info(f"Connected to Solana {self.network}")
                return True
            else:
                logger.error(f"Solana health check failed: {health.value}")
                
        except Exception as e:
            logger.error(f"Failed to connect to Solana: {e}")
        
        return False
    
    async def disconnect(self):
        """Disconnect from Solana RPC"""
        if self.client:
            await self.client.close()
    
    async def get_latest_block_number(self) -> int:
        """Get latest slot number"""
        try:
            if not self.client:
                return 0
            
            slot = await self.client.get_slot()
            return slot.value if slot else 0
            
        except Exception as e:
            logger.error(f"Error getting latest Solana slot: {e}")
            return 0
    
    async def get_block(self, slot_number: int) -> Optional[Block]:
        """Get block by slot number"""
        try:
            if not self.client:
                return None
            
            block_data = await self.client.get_block(
                slot_number,
                encoding='json',
                max_supported_transaction_version=0
            )
            
            if not block_data.value:
                return None
            
            block = block_data.value
            return Block(
                hash=str(slot_number),  # Solana uses slot numbers
                blockchain=self.blockchain,
                number=slot_number,
                timestamp=datetime.fromtimestamp(block['block_time'] / 1000) if block.get('block_time') else datetime.utcnow(),
                transaction_count=len(block.get('transactions', [])),
                parent_hash=str(block.get('parent_slot', 0)),
                miner=block.get('blockhash')[:32] if block.get('blockhash') else None,
                difficulty=None,  # Solana doesn't use traditional difficulty
                size=len(str(block))
            )
            
        except Exception as e:
            logger.error(f"Error getting Solana block {slot_number}: {e}")
        
        return None
    
    async def get_transaction(self, tx_signature: str) -> Optional[Transaction]:
        """Get transaction by signature"""
        try:
            if not self.client:
                return None
            
            tx_data = await self.client.get_transaction(
                tx_signature,
                encoding='json',
                max_supported_transaction_version=0
            )
            
            if not tx_data.value:
                return None
            
            transaction = tx_data.value
            meta = transaction.get('meta', {})
            tx_message = transaction.get('transaction', {}).get('message', {})
            
            # Parse transaction
            from_address = None
            to_address = None
            value = 0
            
            # Get account keys
            account_keys = tx_message.get('accountKeys', [])
            if account_keys:
                from_address = account_keys[0]  # First account is typically the fee payer
                
                # Try to find the primary recipient
                if len(account_keys) > 1:
                    to_address = account_keys[1]
            
            # Get value from post token balances
            pre_balances = meta.get('preTokenBalances', [])
            post_balances = meta.get('postTokenBalances', [])
            
            token_transfers = []
            if self.token_tracking and pre_balances and post_balances:
                token_transfers = await self.parse_token_balances(
                    pre_balances, post_balances, account_keys
                )
            
            # Calculate fee
            fee = meta.get('fee', 0) / 1e9  # Convert lamports to SOL
            
            # Get slot info
            slot = transaction.get('slot')
            block_time = transaction.get('blockTime')
            timestamp = datetime.fromtimestamp(block_time) if block_time else datetime.utcnow()
            
            return Transaction(
                hash=tx_signature,
                blockchain=self.blockchain,
                from_address=from_address or "unknown",
                to_address=to_address,
                value=value,  # SOL value (would need more complex parsing for actual value)
                timestamp=timestamp,
                block_number=slot,
                block_hash=str(slot),
                fee=fee,
                status="confirmed" if meta.get('err') is None else "failed",
                confirmations=meta.get('confirmationStatus', 'confirmed'),
                token_transfers=token_transfers
            )
            
        except Exception as e:
            logger.error(f"Error getting Solana transaction {tx_signature}: {e}")
        
        return None
    
    async def get_address_balance(self, address: str) -> float:
        """Get address balance in SOL"""
        try:
            if not self.client:
                return 0.0
            
            balance = await self.client.get_balance(address)
            if balance.value:
                return balance.value / 1e9  # Convert lamports to SOL
            
        except Exception as e:
            logger.error(f"Error getting Solana address balance for {address}: {e}")
        
        return 0.0
    
    async def get_address_transactions(self, address: str, limit: int = 100) -> List[Transaction]:
        """Get address transaction history"""
        try:
            if not self.client:
                return []
            
            # Get signatures for this address
            signatures = await self.client.get_signatures_for_address(
                address,
                limit=limit
            )
            
            if not signatures.value:
                return []
            
            transactions = []
            for sig_info in signatures.value:
                tx = await self.get_transaction(sig_info.signature)
                if tx:
                    transactions.append(tx)
            
            return transactions
            
        except Exception as e:
            logger.error(f"Error getting Solana address transactions for {address}: {e}")
            return []
    
    async def get_block_transactions(self, slot_number: int) -> List[str]:
        """Get transaction signatures for a block"""
        try:
            if not self.client:
                return []
            
            block_data = await self.client.get_block(
                slot_number,
                encoding='json',
                max_supported_transaction_version=0
            )
            
            if not block_data.value:
                return []
            
            transactions = block_data.value.get('transactions', [])
            return [tx.get('transaction', {}).get('signatures', [''])[0] for tx in transactions]
            
        except Exception as e:
            logger.error(f"Error getting Solana block transactions for {slot_number}: {e}")
            return []
    
    async def parse_token_balances(self, pre_balances: List[Dict], 
                                post_balances: List[Dict],
                                account_keys: List[str]) -> List[Dict]:
        """Parse token transfers from balance changes"""
        transfers = []
        
        try:
            # Create maps of pre and post balances
            pre_map = {bal['accountIndex']: bal for bal in pre_balances}
            post_map = {bal['accountIndex']: bal for bal in post_balances}
            
            # Find balance changes
            for account_index, post_bal in post_map.items():
                pre_bal = pre_map.get(account_index, {})
                
                if post_bal.get('mint') in self.stablecoin_mints.values():
                    pre_amount = pre_bal.get('uiTokenAmount', {}).get('uiAmount', 0)
                    post_amount = post_bal.get('uiTokenAmount', {}).get('uiAmount', 0)
                    
                    amount_change = post_amount - pre_amount
                    if abs(amount_change) > 0:
                        # Find stablecoin symbol
                        symbol = None
                        for sym, mint in self.stablecoin_mints.items():
                            if post_bal['mint'] == mint:
                                symbol = sym
                                break
                        
                        if symbol:
                            owner = post_bal.get('owner')
                            if owner and len(account_keys) > account_index:
                                from_address = account_keys[account_index]
                                to_address = owner
                                
                                transfers.append({
                                    'symbol': symbol,
                                    'contract_address': post_bal['mint'],
                                    'from_address': from_address,
                                    'to_address': to_address,
                                    'amount': abs(amount_change),
                                    'decimals': post_bal.get('uiTokenAmount', {}).get('decimals', 0)
                                })
        
        except Exception as e:
            logger.error(f"Error parsing token balances: {e}")
        
        return transfers
    
    async def get_token_accounts(self, address: str) -> List[Dict]:
        """Get token accounts for an address"""
        try:
            if not self.client:
                return []
            
            token_accounts = await self.client.get_token_accounts_by_owner(address)
            if not token_accounts.value:
                return []
            
            accounts = []
            for account_info in token_accounts.value:
                parsed = account_info.get('account', {}).get('data', {}).get('parsed', {})
                info = parsed.get('info', {})
                
                accounts.append({
                    'mint': info.get('mint'),
                    'amount': info.get('tokenAmount', {}).get('amount', 0),
                    'decimals': info.get('tokenAmount', {}).get('decimals', 0),
                    'ui_amount': info.get('tokenAmount', {}).get('uiAmount', 0)
                })
            
            return accounts
            
        except Exception as e:
            logger.error(f"Error getting token accounts for {address}: {e}")
            return []
    
    async def monitor_program_activity(self):
        """Monitor program activity for tracking"""
        if not self.program_tracking:
            return
        
        # This would monitor specific programs like DEXs, bridges, etc.
        # Implementation depends on specific programs to track
        pass
    
    async def get_network_stats(self) -> Dict[str, Any]:
        """Get Solana network statistics"""
        try:
            if not self.client:
                return {}
            
            # Get recent performance samples
            performance = await self.client.get_recent_performance_samples()
            slot_samples = performance.value[:10] if performance.value else []
            
            # Calculate average TPS
            avg_tps = 0
            if slot_samples:
                avg_tps = sum(sample.get('numTransactions', 0) for sample in slot_samples) / len(slot_samples)
            
            # Get latest slot
            slot = await self.client.get_slot()
            
            return {
                'blockchain': self.blockchain,
                'current_slot': slot.value if slot else 0,
                'average_tps': avg_tps,
                'block_time': '~400ms',  # Solana average block time
                'cluster_nodes': len(slot_samples) if slot_samples else 0
            }
            
        except Exception as e:
            logger.error(f"Error getting Solana network stats: {e}")
            return {}
