"""
Jackdaw Sentry - Lightning Network Monitor
Lightning Network channel state and payment routing analysis
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
import json
import grpc
import base64

from .base import hash_address
from src.api.database import get_neo4j_session, get_redis_connection
from src.api.config import settings

logger = logging.getLogger(__name__)


class LightningMonitor:
    """Lightning Network monitoring and analysis"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.rpc_url = config.get('rpc_url', settings.LND_RPC_URL)
        self.macaroon_path = config.get('macaroon_path', settings.LND_MACAROON_PATH)
        self.tls_cert_path = config.get('tls_cert_path', settings.LND_TLS_CERT_PATH)
        
        self.is_running = False
        self.lnd_stub = None
        self.router_stub = None
        
        # Metrics
        self.metrics = {
            'channels_monitored': 0,
            'payments_tracked': 0,
            'nodes_discovered': 0,
            'last_update': None,
            'network_capacity': 0
        }
    
    async def connect(self) -> bool:
        """Connect to LND gRPC"""
        try:
            import lnrpc
            import routerrpc
            
            # Read TLS cert
            with open(self.tls_cert_path, 'rb') as f:
                cert = f.read()
            
            # Read macaroon
            with open(self.macaroon_path, 'rb') as f:
                macaroon = f.read()
            
            # Create credentials
            credentials = grpc.ssl_channel_credentials(cert)
            call_credentials = grpc.metadata_call_credentials(
                lambda _, callback: callback([('macaroon', macaroon)], None)
            )
            
            # Create channel
            channel = grpc.secure_channel(self.rpc_url, credentials)
            
            # Create stubs
            self.lnd_stub = lnrpc.LightningStub(channel)
            self.router_stub = routerrpc.RouterStub(channel)
            
            # Test connection
            info = await self.get_info()
            if info:
                logger.info(f"Connected to Lightning Network (alias: {info.get('alias')})")
                return True
            
        except Exception as e:
            logger.error(f"Failed to connect to Lightning Network: {e}")
        
        return False
    
    async def disconnect(self):
        """Disconnect from LND"""
        if self.lnd_stub:
            self.lnd_stub = None
        if self.router_stub:
            self.router_stub = None
    
    async def start(self):
        """Start Lightning Network monitoring"""
        logger.info("Starting Lightning Network monitoring...")
        
        if not await self.connect():
            return
        
        self.is_running = True
        
        try:
            # Start monitoring tasks
            tasks = [
                asyncio.create_task(self.monitor_channels()),
                asyncio.create_task(self.monitor_payments()),
                asyncio.create_task(self.monitor_network_topology()),
                asyncio.create_task(self.track_routing_events()),
                asyncio.create_task(self.collect_metrics())
            ]
            
            await asyncio.gather(*tasks)
            
        except Exception as e:
            logger.error(f"Error in Lightning monitoring: {e}")
        finally:
            await self.stop()
    
    async def stop(self):
        """Stop Lightning Network monitoring"""
        logger.info("Stopping Lightning Network monitoring...")
        self.is_running = False
        await self.disconnect()
    
    async def get_info(self) -> Optional[Dict]:
        """Get LND node information"""
        try:
            import lnrpc
            
            request = lnrpc.GetInfoRequest()
            response = await asyncio.get_event_loop().run_in_executor(
                None, self.lnd_stub.GetInfo, request
            )
            
            return {
                'identity_pubkey': response.identity_pubkey,
                'alias': response.alias,
                'num_peers': response.num_peers,
                'num_pending_channels': response.num_pending_channels,
                'num_active_channels': response.num_active_channels,
                'num_inactive_channels': response.num_inactive_channels,
                'block_hash': response.block_hash,
                'block_height': response.block_height,
                'synced_to_chain': response.synced_to_chain,
                'testnet': response.testnet,
                'chains': list(response.chains)
            }
            
        except Exception as e:
            logger.error(f"Error getting Lightning info: {e}")
        
        return None
    
    async def monitor_channels(self):
        """Monitor Lightning Network channels"""
        logger.info("Starting Lightning channel monitoring...")
        
        while self.is_running:
            try:
                await self.update_channels()
                await asyncio.sleep(60)  # Update every minute
                
            except Exception as e:
                logger.error(f"Error in channel monitoring: {e}")
                await asyncio.sleep(30)
    
    async def update_channels(self):
        """Update channel information"""
        try:
            import lnrpc
            
            request = lnrpc.ListChannelsRequest()
            response = await asyncio.get_event_loop().run_in_executor(
                None, self.lnd_stub.ListChannels, request
            )
            
            channels = []
            total_capacity = 0
            
            for channel in response.channels:
                channel_info = {
                    'channel_id': channel.chan_id,
                    'remote_pubkey': channel.remote_pubkey,
                    'local_pubkey': channel.remote_pubkey,  # Would be from get_info
                    'capacity': channel.capacity,
                    'local_balance': channel.local_balance,
                    'remote_balance': channel.remote_balance,
                    'initiator': channel.initiator,
                    'private': channel.private,
                    'active': channel.active,
                    'last_update': datetime.fromtimestamp(channel.last_update / 1000000) if channel.last_update else None
                }
                
                channels.append(channel_info)
                total_capacity += channel.capacity
                
                # Store in Neo4j
                await self.store_channel(channel_info)
            
            # Update metrics
            self.metrics['channels_monitored'] = len(channels)
            self.metrics['network_capacity'] = total_capacity
            self.metrics['last_update'] = datetime.now(timezone.utc)
            
            # Cache in Redis
            await self.cache_channel_data(channels)
            
            logger.info(f"Updated {len(channels)} Lightning channels")
            
        except Exception as e:
            logger.error(f"Error updating channels: {e}")
    
    async def store_channel(self, channel_info: Dict):
        """Store channel information in Neo4j"""
        query = """
        MERGE (local_node:LightningNode {pubkey: $local_pubkey})
        MERGE (remote_node:LightningNode {pubkey: $remote_pubkey})
        MERGE (c:LightningChannel {channel_id: $channel_id})
        SET c.capacity = $capacity,
            c.local_balance = $local_balance,
            c.remote_balance = $remote_balance,
            c.initiator = $initiator,
            c.private = $private,
            c.active = $active,
            c.last_update = $last_update,
            c.updated_at = timestamp()
        
        MERGE (local_node)-[r:CHANNEL]->(remote_node)
        SET r.channel_id = $channel_id,
            r.capacity = $capacity,
            r.active = $active,
            r.created_at = timestamp()
        """
        
        async with get_neo4j_session() as session:
            await session.run(query,
                local_pubkey=channel_info.get('local_pubkey'),
                remote_pubkey=channel_info.get('remote_pubkey'),
                channel_id=channel_info.get('channel_id'),
                capacity=channel_info.get('capacity'),
                local_balance=channel_info.get('local_balance'),
                remote_balance=channel_info.get('remote_balance'),
                initiator=channel_info.get('initiator'),
                private=channel_info.get('private'),
                active=channel_info.get('active'),
                last_update=channel_info.get('last_update')
            )
    
    async def monitor_payments(self):
        """Monitor Lightning Network payments"""
        logger.info("Starting Lightning payment monitoring...")
        
        while self.is_running:
            try:
                await self.update_payments()
                await asyncio.sleep(30)  # Update every 30 seconds
                
            except Exception as e:
                logger.error(f"Error in payment monitoring: {e}")
                await asyncio.sleep(15)
    
    async def update_payments(self):
        """Update payment information"""
        try:
            import lnrpc
            
            # Get recent payments
            request = lnrpc.ListPaymentsRequest(
                max_payments=100,
                include_incomplete=True
            )
            response = await asyncio.get_event_loop().run_in_executor(
                None, self.lnd_stub.ListPayments, request
            )
            
            payments = []
            for payment in response.payments:
                payment_info = {
                    'payment_hash': payment.payment_hash,
                    'value': payment.value,
                    'fee': payment.fee,
                    'status': payment.status,
                    'creation_time': datetime.fromtimestamp(payment.creation_date / 1000000) if payment.creation_date else None,
                    'payment_preimage': payment.payment_preimage,
                    'path': list(payment.path) if payment.path else []
                }
                
                payments.append(payment_info)
                await self.store_payment(payment_info)
            
            self.metrics['payments_tracked'] = len(payments)
            
            # Check for suspicious payments
            await self.analyze_payments(payments)
            
        except Exception as e:
            logger.error(f"Error updating payments: {e}")
    
    async def store_payment(self, payment_info: Dict):
        """Store payment information in Neo4j"""
        if not payment_info.get('path'):
            return
        
        query = """
        MERGE (p:LightningPayment {payment_hash: $payment_hash})
        SET p.value = $value,
            p.fee = $fee,
            p.status = $status,
            p.creation_time = $creation_time,
            p.payment_preimage = $payment_preimage,
            p.created_at = timestamp()
        
        WITH p
        UNWIND $path AS node_pubkey
        MATCH (n:LightningNode {pubkey: node_pubkey})
        MERGE (p)-[r:USES_PATH]->(n)
        """
        
        async with get_neo4j_session() as session:
            await session.run(query,
                payment_hash=payment_info.get('payment_hash'),
                value=payment_info.get('value'),
                fee=payment_info.get('fee'),
                status=payment_info.get('status'),
                creation_time=payment_info.get('creation_time'),
                payment_preimage=payment_info.get('payment_preimage'),
                path=payment_info.get('path')
            )
    
    async def analyze_payments(self, payments: List[Dict]):
        """Analyze payments for suspicious patterns"""
        for payment in payments:
            # Check for high-value payments
            if payment.get('value', 0) > 100000000:  # > 1 BTC in satoshis
                await self.alert_high_value_payment(payment)
            
            # Check for rapid payments to same destination
            await self.check_rapid_payments(payment)
            
            # Check for mixer-like patterns
            await self.check_mixer_patterns(payment)
    
    async def alert_high_value_payment(self, payment: Dict):
        """Alert on high-value Lightning payments"""
        logger.warning(f"High-value Lightning payment detected: {payment.get('payment_hash')} - {payment.get('value')} satoshis")
        
        query = """
        MERGE (p:LightningPayment {payment_hash: $payment_hash})
        MERGE (a:Alert {type: 'high_value_lightning_payment'})
        MERGE (p)-[:TRIGGERED]->(a)
        SET a.value = $value,
            a.threshold = $threshold,
            a.created_at = timestamp()
        """
        
        async with get_neo4j_session() as session:
            await session.run(query,
                payment_hash=payment.get('payment_hash'),
                value=payment.get('value'),
                threshold=100000000
            )
    
    async def check_rapid_payments(self, payment: Dict):
        """Check for rapid payments to same destination"""
        # This would check for multiple payments to same node in short time
        pass
    
    async def check_mixer_patterns(self, payment: Dict):
        """Check for mixer-like payment patterns"""
        # This would analyze payment paths for mixer characteristics
        pass
    
    async def monitor_network_topology(self):
        """Monitor Lightning Network topology"""
        logger.info("Starting Lightning network topology monitoring...")
        
        while self.is_running:
            try:
                await self.update_network_graph()
                await asyncio.sleep(300)  # Update every 5 minutes
                
            except Exception as e:
                logger.error(f"Error in topology monitoring: {e}")
                await asyncio.sleep(60)
    
    async def update_network_graph(self):
        """Update Lightning Network graph"""
        try:
            import lnrpc
            
            # Get network graph
            request = lnrpc.ChannelGraphRequest()
            response = await asyncio.get_event_loop().run_in_executor(
                None, self.lnd_stub.DescribeGraph, request
            )
            
            nodes = []
            edges = []
            
            for node in response.nodes:
                node_info = {
                    'pubkey': node.pubkey,
                    'alias': node.alias,
                    'addresses': [(addr.addr, addr.port) for addr in node.addresses],
                    'last_update': datetime.fromtimestamp(node.last_update / 1000000) if node.last_update else None
                }
                nodes.append(node_info)
                await self.store_node(node_info)
            
            for edge in response.edges:
                edge_info = {
                    'channel_id': edge.chan_id,
                    'node1_pubkey': edge.node1_pub,
                    'node2_pubkey': edge.node2_pub,
                    'capacity': edge.capacity,
                    'last_update': datetime.fromtimestamp(edge.last_update / 1000000) if edge.last_update else None
                }
                edges.append(edge_info)
            
            self.metrics['nodes_discovered'] = len(nodes)
            
            logger.info(f"Updated Lightning network: {len(nodes)} nodes, {len(edges)} channels")
            
        except Exception as e:
            logger.error(f"Error updating network graph: {e}")
    
    async def store_node(self, node_info: Dict):
        """Store Lightning node information"""
        query = """
        MERGE (n:LightningNode {pubkey: $pubkey})
        SET n.alias = $alias,
            n.addresses = $addresses,
            n.last_update = $last_update,
            n.updated_at = timestamp()
        """
        
        async with get_neo4j_session() as session:
            await session.run(query,
                pubkey=node_info.get('pubkey'),
                alias=node_info.get('alias'),
                addresses=node_info.get('addresses'),
                last_update=node_info.get('last_update')
            )
    
    async def track_routing_events(self):
        """Track routing events for analysis"""
        logger.info("Starting Lightning routing event tracking...")
        
        try:
            import routerrpc
            
            request = routerrpc.TrackPaymentsRequest()
            
            # This would stream routing events
            # For now, implement periodic checking
            while self.is_running:
                await asyncio.sleep(60)
                
        except Exception as e:
            logger.error(f"Error in routing event tracking: {e}")
    
    async def find_payment_routes(self, source_pubkey: str, target_pubkey: str, 
                               amount: int, max_hops: int = 5) -> List[Dict]:
        """Find payment routes between nodes"""
        try:
            import routerrpc
            
            request = routerrpc.QueryRoutesRequest(
                pub_key=target_pubkey,
                amt=amount,
                max_routes=5,
                fee_limit=1000000  # 10 mBTC fee limit
            )
            
            response = await asyncio.get_event_loop().run_in_executor(
                None, self.router_stub.QueryRoutes, request
            )
            
            routes = []
            for route in response.routes:
                route_info = {
                    'total_time_lock': route.total_time_lock,
                    'total_fees': route.total_fees,
                    'total_amt': route.total_amt,
                    'hops': []
                }
                
                for hop in route.hops:
                    hop_info = {
                        'pub_key': hop.pub_key,
                        'chan_id': hop.chan_id,
                        'amt_to_forward': hop.amt_to_forward,
                        'fee': hop.fee,
                        'time_lock': hop.time_lock
                    }
                    route_info['hops'].append(hop_info)
                
                routes.append(route_info)
            
            return routes
            
        except Exception as e:
            logger.error(f"Error finding payment routes: {e}")
            return []
    
    async def analyze_channel_liquidity(self, channel_id: str) -> Dict:
        """Analyze channel liquidity patterns"""
        try:
            # This would analyze historical channel balance changes
            # to identify liquidity patterns and potential issues
            
            return {
                'channel_id': channel_id,
                'liquidity_score': 0.5,  # Placeholder
                'utilization': 0.3,       # Placeholder
                'rebalance_needed': False    # Placeholder
            }
            
        except Exception as e:
            logger.error(f"Error analyzing channel liquidity: {e}")
            return {}
    
    async def cache_channel_data(self, channels: List[Dict]):
        """Cache channel data in Redis"""
        try:
            async with get_redis_connection() as redis:
                await redis.setex(
                    'lightning_channels',
                    300,  # 5 minutes
                    json.dumps(channels)
                )
        except Exception as e:
            logger.error(f"Error caching channel data: {e}")
    
    async def collect_metrics(self):
        """Collect Lightning Network metrics"""
        while self.is_running:
            try:
                # Get network info
                info = await self.get_info()
                if info:
                    self.metrics.update({
                        'num_peers': info.get('num_peers', 0),
                        'num_active_channels': info.get('num_active_channels', 0),
                        'synced_to_chain': info.get('synced_to_chain', False)
                    })
                
                # Cache metrics
                async with get_redis_connection() as redis:
                    await redis.setex(
                        'lightning_metrics',
                        300,  # 5 minutes
                        json.dumps(self.metrics)
                    )
                
                await asyncio.sleep(60)  # Update every minute
                
            except Exception as e:
                logger.error(f"Error collecting Lightning metrics: {e}")
                await asyncio.sleep(30)
    
    async def get_network_stats(self) -> Dict[str, Any]:
        """Get Lightning Network statistics"""
        return {
            'blockchain': 'bitcoin_lightning',
            'channels_monitored': self.metrics.get('channels_monitored', 0),
            'payments_tracked': self.metrics.get('payments_tracked', 0),
            'nodes_discovered': self.metrics.get('nodes_discovered', 0),
            'network_capacity': self.metrics.get('network_capacity', 0),
            'last_update': self.metrics.get('last_update')
        }
