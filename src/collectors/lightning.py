"""
Jackdaw Sentry - Lightning Network Monitor
Lightning Network channel state and payment routing analysis
"""

import asyncio
import base64
import json
import logging
import os
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

try:
    import aiohttp
except ImportError:  # pragma: no cover
    aiohttp = None

from src.api.config import settings
from src.api.database import get_neo4j_session
from src.api.database import get_postgres_connection
from src.api.database import get_redis_connection

from .base import hash_address
from .base import Transaction

logger = logging.getLogger(__name__)


class LightningMonitor:
    """Lightning Network monitoring and analysis"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.rpc_url = config.get("rpc_url", settings.LND_RPC_URL)
        self.macaroon_path = config.get("macaroon_path", settings.LND_MACAROON_PATH)
        self.tls_cert_path = config.get("tls_cert_path", settings.LND_TLS_CERT_PATH)
        self.api_url = config.get("api_url", settings.LIGHTNING_API_URL).rstrip("/")
        self.public_top_nodes = int(
            config.get("public_top_nodes", settings.LIGHTNING_PUBLIC_TOP_NODES)
        )
        self.public_channels_per_node = int(
            config.get(
                "public_channels_per_node",
                settings.LIGHTNING_PUBLIC_CHANNELS_PER_NODE,
            )
        )

        self.is_running = False
        self.lnd_stub = None
        self.router_stub = None
        self.identity_pubkey: Optional[str] = None
        self.session = None
        self.public_mode = False

        # Metrics
        self.metrics = {
            "channels_monitored": 0,
            "payments_tracked": 0,
            "nodes_discovered": 0,
            "last_update": None,
            "network_capacity": 0,
        }

    async def connect(self) -> bool:
        """Connect to LND gRPC"""
        if not self._lnd_configured():
            return await self._connect_public_api()

        try:
            import grpc
            import lnrpc
            import routerrpc

            # Read TLS cert
            with open(self.tls_cert_path, "rb") as f:
                cert = f.read()

            # Read macaroon
            with open(self.macaroon_path, "rb") as f:
                macaroon = f.read()

            # Create credentials
            credentials = grpc.ssl_channel_credentials(cert)
            call_credentials = grpc.metadata_call_credentials(
                lambda _, callback: callback([("macaroon", macaroon)], None)
            )

            # Create channel
            channel = grpc.secure_channel(self.rpc_url, credentials)

            # Create stubs
            self.lnd_stub = lnrpc.LightningStub(channel)
            self.router_stub = routerrpc.RouterStub(channel)

            # Test connection
            info = await self.get_info()
            if info:
                self.identity_pubkey = info.get("identity_pubkey")
                logger.info(
                    f"Connected to Lightning Network (alias: {info.get('alias')})"
                )
                return True

        except Exception as e:
            logger.error(f"Failed to connect to Lightning Network: {e}")

        return False

    def _lnd_configured(self) -> bool:
        """Return True when usable LND credentials are available."""
        if not self.macaroon_path or not self.tls_cert_path:
            return False
        return os.path.exists(self.macaroon_path) and os.path.exists(self.tls_cert_path)

    async def _connect_public_api(self) -> bool:
        """Connect to the public mempool Lightning API."""
        if aiohttp is None:
            logger.error("aiohttp is required for public Lightning API mode")
            return False

        try:
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(timeout=timeout)
            stats = await self._public_get("/api/v1/lightning/statistics/latest")
            if stats:
                self.public_mode = True
                logger.info("Connected to Lightning public API at %s", self.api_url)
                latest = (stats or {}).get("latest") or {}
                self.metrics["channels_monitored"] = latest.get("channel_count", 0)
                self.metrics["nodes_discovered"] = latest.get("node_count", 0)
                self.metrics["network_capacity"] = latest.get("total_capacity", 0)
                return True
        except Exception as exc:
            logger.error("Failed to connect to Lightning public API: %s", exc)

        if self.session:
            await self.session.close()
            self.session = None
        return False

    async def _public_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Call the public Lightning REST API and return decoded JSON."""
        if self.session is None:
            raise RuntimeError("Lightning public session is not initialized")

        async with self.session.get(f"{self.api_url}{path}", params=params) as response:
            response.raise_for_status()
            return await response.json()

    async def disconnect(self):
        """Disconnect from LND"""
        if self.lnd_stub:
            self.lnd_stub = None
        if self.router_stub:
            self.router_stub = None
        if self.session:
            await self.session.close()
            self.session = None
        self.public_mode = False

    async def start(self):
        """Start Lightning Network monitoring"""
        logger.info("Starting Lightning Network monitoring...")

        if not await self.connect():
            return

        self.is_running = True

        try:
            if self.public_mode:
                tasks = [
                    asyncio.create_task(self.monitor_channels()),
                    asyncio.create_task(self.collect_metrics()),
                ]
            else:
                # Start monitoring tasks
                tasks = [
                    asyncio.create_task(self.monitor_channels()),
                    asyncio.create_task(self.monitor_payments()),
                    asyncio.create_task(self.monitor_network_topology()),
                    asyncio.create_task(self.track_routing_events()),
                    asyncio.create_task(self.collect_metrics()),
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
        if self.public_mode:
            try:
                stats = await self._public_get("/api/v1/lightning/statistics/latest")
                latest = (stats or {}).get("latest") or {}
                return {
                    "identity_pubkey": None,
                    "alias": "mempool.space",
                    "num_peers": 0,
                    "num_pending_channels": 0,
                    "num_active_channels": latest.get("channel_count", 0),
                    "num_inactive_channels": 0,
                    "block_hash": None,
                    "block_height": None,
                    "synced_to_chain": True,
                    "testnet": False,
                    "chains": ["lightning"],
                    "node_count": latest.get("node_count", 0),
                    "total_capacity": latest.get("total_capacity", 0),
                }
            except Exception as e:
                logger.error(f"Error getting Lightning public stats: {e}")
                return None

        try:
            import lnrpc

            request = lnrpc.GetInfoRequest()
            response = await asyncio.get_event_loop().run_in_executor(
                None, self.lnd_stub.GetInfo, request
            )

            return {
                "identity_pubkey": response.identity_pubkey,
                "alias": response.alias,
                "num_peers": response.num_peers,
                "num_pending_channels": response.num_pending_channels,
                "num_active_channels": response.num_active_channels,
                "num_inactive_channels": response.num_inactive_channels,
                "block_hash": response.block_hash,
                "block_height": response.block_height,
                "synced_to_chain": response.synced_to_chain,
                "testnet": response.testnet,
                "chains": list(response.chains),
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
        if self.public_mode:
            await self._update_public_channels()
            return

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
                    "channel_id": channel.chan_id,
                    "remote_pubkey": channel.remote_pubkey,
                    "local_pubkey": channel.remote_pubkey,  # Would be from get_info
                    "capacity": channel.capacity,
                    "local_balance": channel.local_balance,
                    "remote_balance": channel.remote_balance,
                    "initiator": channel.initiator,
                    "private": channel.private,
                    "active": channel.active,
                    "last_update": (
                        datetime.fromtimestamp(
                            channel.last_update / 1000000, tz=timezone.utc
                        )
                        if channel.last_update
                        else None
                    ),
                }

                channels.append(channel_info)
                total_capacity += channel.capacity

                # Store in Neo4j
                await self.store_channel(channel_info)

            closed_channels = await self.update_closed_channels()

            # Update metrics
            self.metrics["channels_monitored"] = len(channels) + len(closed_channels)
            self.metrics["network_capacity"] = total_capacity
            self.metrics["last_update"] = datetime.now(timezone.utc)

            # Cache in Redis
            await self.cache_channel_data(channels)

            logger.info(f"Updated {len(channels)} Lightning channels")

        except Exception as e:
            logger.error(f"Error updating channels: {e}")

    async def update_closed_channels(self) -> List[Dict[str, Any]]:
        """Update closed Lightning channels when full LND access is available."""
        if self.public_mode:
            return []

        try:
            import lnrpc

            request = lnrpc.ClosedChannelsRequest()
            response = await asyncio.get_event_loop().run_in_executor(
                None, self.lnd_stub.ClosedChannels, request
            )

            closed_channels: List[Dict[str, Any]] = []
            for summary in response.channels:
                channel_info = self._closed_channel_to_channel_info(summary)
                if not channel_info.get("channel_id") or not channel_info.get("close_tx_hash"):
                    continue

                closed_channels.append(channel_info)
                await self.store_channel(channel_info)
                await self._store_closed_channel_transaction(
                    channel_info,
                    self._closed_channel_to_transaction(channel_info),
                )

            return closed_channels
        except Exception as e:
            logger.error(f"Error updating closed channels: {e}")
            return []

    async def _update_public_channels(self):
        """Update channel information using the public Lightning API."""
        try:
            ranked_nodes = await self._public_get(
                "/api/v1/lightning/nodes/rankings/connectivity"
            )
            ranked_nodes = (ranked_nodes or [])[: self.public_top_nodes]

            channels: List[Dict[str, Any]] = []
            total_capacity = 0
            seen_channel_ids = set()

            for node in ranked_nodes:
                pubkey = node.get("publicKey")
                if not pubkey:
                    continue

                await self.store_node(
                    {
                        "pubkey": pubkey,
                        "alias": node.get("alias"),
                        "addresses": [],
                        "last_update": self._parse_dt(
                            node.get("updatedAt"), assume_epoch_seconds=True
                        ),
                    }
                )

                node_channels = await self._public_get(
                    "/api/v1/lightning/channels",
                    params={"public_key": pubkey, "status": "open"},
                )
                for channel_summary in (node_channels or [])[
                    : self.public_channels_per_node
                ]:
                    channel_id = str(channel_summary.get("id") or "")
                    if not channel_id or channel_id in seen_channel_ids:
                        continue
                    seen_channel_ids.add(channel_id)

                    detail = await self._public_get(
                        f"/api/v1/lightning/channels/{channel_id}"
                    )
                    if not detail:
                        continue

                    channel_info = self._channel_detail_to_channel_info(detail)
                    tx = self._channel_detail_to_transaction(detail)
                    channels.append(channel_info)
                    total_capacity += channel_info.get("capacity", 0)

                    await self.store_node(self._channel_node_to_node_info(detail.get("node_left")))
                    await self.store_node(self._channel_node_to_node_info(detail.get("node_right")))
                    await self.store_channel(channel_info)
                    await self._store_public_channel_transaction(channel_info, tx)

            self.metrics["channels_monitored"] = len(channels)
            self.metrics["payments_tracked"] = 0  # No payments tracked in public mode
            self.metrics["network_capacity"] = total_capacity
            self.metrics["last_update"] = datetime.now(timezone.utc)

            await self.cache_channel_data(channels)
            logger.info("Updated %s Lightning channels via public API", len(channels))
        except Exception as e:
            logger.error(f"Error updating public Lightning channels: {e}")

    def _channel_detail_to_channel_info(self, detail: Dict[str, Any]) -> Dict[str, Any]:
        """Map channel detail payload to the existing channel graph shape."""
        return {
            "channel_id": str(detail.get("id")),
            "remote_pubkey": ((detail.get("node_right") or {}).get("public_key")),
            "local_pubkey": ((detail.get("node_left") or {}).get("public_key")),
            "capacity": int(detail.get("capacity") or 0),
            "local_balance": int((detail.get("node_left") or {}).get("funding_balance") or 0),
            "remote_balance": int((detail.get("node_right") or {}).get("funding_balance") or 0),
            "initiator": None,
            "private": False,
            "active": int(detail.get("status") or 0) == 1,
            "last_update": self._parse_dt(detail.get("updated_at")),
        }

    def _channel_detail_to_transaction(self, detail: Dict[str, Any]) -> Transaction:
        """Project a public channel detail payload into a canonical tx row."""
        txid = detail.get("transaction_id")
        tx_vout = detail.get("transaction_vout")
        tx_hash = (
            f"{txid}:{tx_vout}"
            if txid is not None and tx_vout is not None
            else f"lightning:{detail.get('id')}"
        )
        short_id = str(detail.get("short_id") or "")
        block_number = None
        if "x" in short_id:
            try:
                block_number = int(short_id.split("x", 1)[0])
            except ValueError:
                block_number = None

        return Transaction(
            hash=tx_hash,
            blockchain="lightning",
            timestamp=self._parse_dt(detail.get("created"))
            or self._parse_dt(detail.get("updated_at"))
            or datetime.now(timezone.utc),
            from_address=((detail.get("node_left") or {}).get("public_key")),
            to_address=((detail.get("node_right") or {}).get("public_key")),
            value=float(detail.get("capacity") or 0) / 1e8,
            block_number=block_number,
            status="confirmed" if int(detail.get("status") or 0) == 1 else "closed",
            confirmations=0,
            memo=detail.get("short_id"),
        )

    def _channel_node_to_node_info(self, node: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Map public channel node detail to LightningNode storage shape."""
        node = node or {}
        return {
            "pubkey": node.get("public_key"),
            "alias": node.get("alias"),
            "addresses": [],
            "last_update": self._parse_dt(node.get("updated_at")),
        }

    def _parse_dt(
        self, value: Any, assume_epoch_seconds: bool = False
    ) -> Optional[datetime]:
        """Parse ISO timestamps or integer epochs into UTC datetimes."""
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)):
            scale = 1
            if not assume_epoch_seconds and value > 10_000_000_000:
                scale = 1000
            return datetime.fromtimestamp(value / scale, tz=timezone.utc)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                if value.isdigit():
                    return self._parse_dt(int(value), assume_epoch_seconds=assume_epoch_seconds)
        return None

    def _closed_channel_to_channel_info(self, summary: Any) -> Dict[str, Any]:
        """Project an LND ChannelCloseSummary into channel graph storage shape."""
        close_type = self._normalize_close_type(getattr(summary, "close_type", None))
        return {
            "channel_id": str(getattr(summary, "chan_id", "") or ""),
            "remote_pubkey": getattr(summary, "remote_pubkey", None),
            "local_pubkey": self.identity_pubkey,
            "capacity": int(getattr(summary, "capacity", 0) or 0),
            "local_balance": int(getattr(summary, "settled_balance", 0) or 0),
            "remote_balance": int(getattr(summary, "time_locked_balance", 0) or 0),
            "initiator": getattr(summary, "open_initiator", None),
            "private": None,
            "active": False,
            "last_update": datetime.now(timezone.utc),
            "close_tx_hash": getattr(summary, "closing_tx_hash", None),
            "close_type": close_type,
            "settled_balance_sats": int(getattr(summary, "settled_balance", 0) or 0),
            "close_height": int(getattr(summary, "close_height", 0) or 0),
            "status": "closed",
        }

    def _closed_channel_to_transaction(self, channel_info: Dict[str, Any]) -> Transaction:
        """Project a closed channel summary into the canonical transaction shape."""
        settled_sats = int(channel_info.get("settled_balance_sats") or 0)
        close_tx_hash = channel_info.get("close_tx_hash") or f"lightning-close:{channel_info.get('channel_id')}"
        return Transaction(
            hash=close_tx_hash,
            blockchain="lightning",
            timestamp=channel_info.get("last_update") or datetime.now(timezone.utc),
            from_address=channel_info.get("local_pubkey"),
            to_address=channel_info.get("remote_pubkey"),
            value=float(settled_sats) / 1e8,
            block_number=channel_info.get("close_height"),
            status="closed",
            confirmations=0,
            memo=channel_info.get("channel_id"),
        )

    def _normalize_close_type(self, value: Any) -> str:
        """Normalize LND close type enums/strings into compact investigator labels."""
        if value is None:
            return "unknown"
        raw = getattr(value, "name", value)
        text = str(raw).upper()
        if "COOPERATIVE" in text:
            return "cooperative"
        if "FORCE" in text:
            return "force"
        if "BREACH" in text:
            return "breach"
        return "unknown"

    async def _store_public_channel_transaction(
        self, channel_info: Dict[str, Any], tx: Transaction
    ) -> None:
        """Persist a public Lightning channel as both graph and raw event rows."""
        query = """
        MERGE (t:Transaction {hash: $tx_hash, blockchain: 'lightning'})
        SET t.value = $value,
            t.timestamp = $timestamp,
            t.block_number = $block_number,
            t.status = $status,
            t.memo = $memo,
            t.processed_at = timestamp()

        FOREACH (_ IN CASE WHEN $from_address IS NULL THEN [] ELSE [1] END |
            MERGE (from_addr:Address {address: $from_address, blockchain: 'lightning'})
            ON CREATE SET from_addr.first_seen = $timestamp
            ON MATCH SET from_addr.last_seen = $timestamp
            MERGE (from_addr)-[s:SENT {blockchain: 'lightning'}]->(t)
            ON CREATE SET from_addr.transaction_count = coalesce(from_addr.transaction_count, 0) + 1
        )

        FOREACH (_ IN CASE WHEN $to_address IS NULL THEN [] ELSE [1] END |
            MERGE (to_addr:Address {address: $to_address, blockchain: 'lightning'})
            ON CREATE SET to_addr.first_seen = $timestamp
            ON MATCH SET to_addr.last_seen = $timestamp
            MERGE (t)-[r:RECEIVED {blockchain: 'lightning', value: $value}]->(to_addr)
            ON CREATE SET to_addr.transaction_count = coalesce(to_addr.transaction_count, 0) + 1
        )

        MERGE (c:LightningChannel {channel_id: $channel_id})
        MERGE (c)-[:FUNDED_BY]->(t)
        """

        async with get_neo4j_session() as session:
            await session.run(
                query,
                tx_hash=tx.hash,
                value=tx.value,
                timestamp=tx.timestamp,
                block_number=tx.block_number,
                status=tx.status,
                memo=tx.memo,
                from_address=tx.from_address,
                to_address=tx.to_address,
                channel_id=channel_info.get("channel_id"),
            )

        await self._insert_raw_transaction(tx)

    async def _store_closed_channel_transaction(
        self, channel_info: Dict[str, Any], tx: Transaction
    ) -> None:
        """Persist a closed Lightning channel as graph context plus a raw event row."""
        query = """
        MERGE (t:Transaction {hash: $tx_hash, blockchain: 'lightning'})
        SET t.value = $value,
            t.timestamp = $timestamp,
            t.block_number = $block_number,
            t.status = $status,
            t.memo = $memo,
            t.close_type = $close_type,
            t.processed_at = timestamp()

        FOREACH (_ IN CASE WHEN $from_address IS NULL THEN [] ELSE [1] END |
            MERGE (from_addr:Address {address: $from_address, blockchain: 'lightning'})
            ON CREATE SET from_addr.first_seen = $timestamp
            ON MATCH SET from_addr.last_seen = $timestamp
            MERGE (from_addr)-[:SENT {blockchain: 'lightning'}]->(t)
        )

        FOREACH (_ IN CASE WHEN $to_address IS NULL THEN [] ELSE [1] END |
            MERGE (to_addr:Address {address: $to_address, blockchain: 'lightning'})
            ON CREATE SET to_addr.first_seen = $timestamp
            ON MATCH SET to_addr.last_seen = $timestamp
            MERGE (t)-[:RECEIVED {blockchain: 'lightning', value: $value}]->(to_addr)
        )

        MERGE (c:LightningChannel {channel_id: $channel_id})
        SET c.active = false,
            c.status = 'closed',
            c.close_tx_hash = $tx_hash,
            c.close_type = $close_type,
            c.settled_balance = $settled_balance_sats,
            c.close_height = $close_height,
            c.updated_at = timestamp()
        MERGE (c)-[:CLOSED_BY]->(t)
        """

        async with get_neo4j_session() as session:
            await session.run(
                query,
                tx_hash=tx.hash,
                value=tx.value,
                timestamp=tx.timestamp,
                block_number=tx.block_number,
                status=tx.status,
                memo=tx.memo,
                close_type=channel_info.get("close_type"),
                from_address=tx.from_address,
                to_address=tx.to_address,
                channel_id=channel_info.get("channel_id"),
                settled_balance_sats=channel_info.get("settled_balance_sats"),
                close_height=channel_info.get("close_height"),
            )

        await self._insert_raw_transaction(tx)

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
            await session.run(
                query,
                local_pubkey=channel_info.get("local_pubkey"),
                remote_pubkey=channel_info.get("remote_pubkey"),
                channel_id=channel_info.get("channel_id"),
                capacity=channel_info.get("capacity"),
                local_balance=channel_info.get("local_balance"),
                remote_balance=channel_info.get("remote_balance"),
                initiator=channel_info.get("initiator"),
                private=channel_info.get("private"),
                active=channel_info.get("active"),
                last_update=channel_info.get("last_update"),
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
                max_payments=100, include_incomplete=True
            )
            response = await asyncio.get_event_loop().run_in_executor(
                None, self.lnd_stub.ListPayments, request
            )

            payments = []
            for payment in response.payments:
                payment_info = {
                    "payment_hash": payment.payment_hash,
                    "value": payment.value,
                    "fee": payment.fee,
                    "status": payment.status,
                    "creation_time": (
                        datetime.fromtimestamp(
                            payment.creation_date / 1000000, tz=timezone.utc
                        )
                        if payment.creation_date
                        else None
                    ),
                    "payment_preimage": payment.payment_preimage,
                    "path": list(payment.path) if payment.path else [],
                }

                payments.append(payment_info)
                await self.store_payment(payment_info)

            self.metrics["payments_tracked"] = len(payments)

            # Check for suspicious payments
            await self.analyze_payments(payments)

        except Exception as e:
            logger.error(f"Error updating payments: {e}")

    async def store_payment(self, payment_info: Dict):
        """Store payment information in Neo4j"""
        if not payment_info.get("path"):
            return

        tx = self._payment_to_transaction(payment_info)

        query = """
        MERGE (p:LightningPayment {payment_hash: $payment_hash})
        SET p.value = $value,
            p.fee = $fee,
            p.status = $status,
            p.creation_time = $creation_time,
            p.payment_preimage = $payment_preimage,
            p.created_at = timestamp(),
            p.blockchain = 'lightning'

        MERGE (t:Transaction {hash: $tx_hash, blockchain: 'lightning'})
        SET t.value = $value,
            t.timestamp = $creation_time,
            t.fee = $fee,
            t.status = $tx_status,
            t.processed_at = timestamp()

        FOREACH (_ IN CASE WHEN $from_address IS NULL THEN [] ELSE [1] END |
            MERGE (from_addr:Address {address: $from_address, blockchain: 'lightning'})
            ON CREATE SET from_addr.first_seen = $creation_time
            ON MATCH SET from_addr.last_seen = $creation_time
            MERGE (from_addr)-[:SENT {blockchain: 'lightning'}]->(t)
            ON CREATE SET from_addr.transaction_count = coalesce(from_addr.transaction_count, 0) + 1
        )

        FOREACH (_ IN CASE WHEN $to_address IS NULL THEN [] ELSE [1] END |
            MERGE (to_addr:Address {address: $to_address, blockchain: 'lightning'})
            ON CREATE SET to_addr.first_seen = $creation_time
            ON MATCH SET to_addr.last_seen = $creation_time
            MERGE (t)-[:RECEIVED {blockchain: 'lightning', value: $value}]->(to_addr)
            ON CREATE SET to_addr.transaction_count = coalesce(to_addr.transaction_count, 0) + 1
        )

        MERGE (t)-[:LIGHTNING_PAYMENT]->(p)
        """
        async with get_neo4j_session() as session:
            await session.run(
                query,
                payment_hash=payment_info.get("payment_hash"),
                value=payment_info.get("value"),
                fee=payment_info.get("fee"),
                status=payment_info.get("status"),
                creation_time=payment_info.get("creation_time"),
                payment_preimage=payment_info.get("payment_preimage"),
                path=payment_info.get("path"),
                tx_hash=tx.hash,
                tx_status=tx.status,
                from_address=tx.from_address,
                to_address=tx.to_address,
            )

        await self._insert_raw_transaction(tx)

    def _payment_to_transaction(self, payment_info: Dict[str, Any]) -> Transaction:
        """Project a Lightning payment into the canonical raw transaction shape."""
        path = payment_info.get("path") or []
        from_address = self.identity_pubkey or (path[0] if path else None)
        to_address = path[-1] if path else None
        created_at = payment_info.get("creation_time") or datetime.now(timezone.utc)

        return Transaction(
            hash=payment_info["payment_hash"],
            blockchain="lightning",
            timestamp=created_at,
            from_address=from_address,
            to_address=to_address,
            value=float(payment_info.get("value", 0)) / 1e8,
            fee=float(payment_info.get("fee", 0)) / 1e8,
            status=self._normalize_payment_status(payment_info.get("status")),
            confirmations=0,
        )

    def _normalize_payment_status(self, status: Any) -> str:
        """Return a stable lowercase payment status string."""
        if status is None:
            return "unknown"
        if hasattr(status, "name"):
            return str(status.name).lower()
        if isinstance(status, str):
            return status.lower()

        status_name = {
            0: "unknown",
            1: "in_flight",
            2: "succeeded",
            3: "failed",
        }.get(int(status))
        return status_name or str(status).lower()

    async def _insert_raw_transaction(self, tx: Transaction) -> None:
        """Persist canonical Lightning payments to the raw event store."""
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
                "dual-write lightning _insert_raw_transaction failed for %s: %s",
                tx.hash,
                exc,
            )

    async def analyze_payments(self, payments: List[Dict]):
        """Analyze payments for suspicious patterns"""
        for payment in payments:
            # Check for high-value payments
            if payment.get("value", 0) > 100000000:  # > 1 BTC in satoshis
                await self.alert_high_value_payment(payment)

            # Check for rapid payments to same destination
            await self.check_rapid_payments(payment)

            # Check for mixer-like patterns
            await self.check_mixer_patterns(payment)

    async def alert_high_value_payment(self, payment: Dict):
        """Alert on high-value Lightning payments"""
        logger.warning(
            f"High-value Lightning payment detected: {payment.get('payment_hash')} - {payment.get('value')} satoshis"
        )

        query = """
        MERGE (p:LightningPayment {payment_hash: $payment_hash})
        MERGE (a:Alert {type: 'high_value_lightning_payment'})
        MERGE (p)-[:TRIGGERED]->(a)
        SET a.value = $value,
            a.threshold = $threshold,
            a.created_at = timestamp()
        """

        async with get_neo4j_session() as session:
            await session.run(
                query,
                payment_hash=payment.get("payment_hash"),
                value=payment.get("value"),
                threshold=100000000,
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
        if self.public_mode:
            await self._update_public_channels()
            return

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
                    "pubkey": node.pubkey,
                    "alias": node.alias,
                    "addresses": [(addr.addr, addr.port) for addr in node.addresses],
                    "last_update": (
                        datetime.fromtimestamp(
                            node.last_update / 1000000, tz=timezone.utc
                        )
                        if node.last_update
                        else None
                    ),
                }
                nodes.append(node_info)
                await self.store_node(node_info)

            for edge in response.edges:
                edge_info = {
                    "channel_id": edge.chan_id,
                    "node1_pubkey": edge.node1_pub,
                    "node2_pubkey": edge.node2_pub,
                    "capacity": edge.capacity,
                    "last_update": (
                        datetime.fromtimestamp(
                            edge.last_update / 1000000, tz=timezone.utc
                        )
                        if edge.last_update
                        else None
                    ),
                }
                edges.append(edge_info)

            self.metrics["nodes_discovered"] = len(nodes)

            logger.info(
                f"Updated Lightning network: {len(nodes)} nodes, {len(edges)} channels"
            )

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
            await session.run(
                query,
                pubkey=node_info.get("pubkey"),
                alias=node_info.get("alias"),
                addresses=node_info.get("addresses"),
                last_update=node_info.get("last_update"),
            )

    async def track_routing_events(self):
        """Track routing events for analysis"""
        if self.public_mode:
            while self.is_running:
                await asyncio.sleep(60)
            return

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

    async def find_payment_routes(
        self, source_pubkey: str, target_pubkey: str, amount: int, max_hops: int = 5
    ) -> List[Dict]:
        """Find payment routes between nodes"""
        try:
            import routerrpc

            request = routerrpc.QueryRoutesRequest(
                pub_key=target_pubkey,
                amt=amount,
                max_routes=5,
                fee_limit=1000000,  # 10 mBTC fee limit
            )

            response = await asyncio.get_event_loop().run_in_executor(
                None, self.router_stub.QueryRoutes, request
            )

            routes = []
            for route in response.routes:
                route_info = {
                    "total_time_lock": route.total_time_lock,
                    "total_fees": route.total_fees,
                    "total_amt": route.total_amt,
                    "hops": [],
                }

                for hop in route.hops:
                    hop_info = {
                        "pub_key": hop.pub_key,
                        "chan_id": hop.chan_id,
                        "amt_to_forward": hop.amt_to_forward,
                        "fee": hop.fee,
                        "time_lock": hop.time_lock,
                    }
                    route_info["hops"].append(hop_info)

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
                "channel_id": channel_id,
                "liquidity_score": 0.5,  # Placeholder
                "utilization": 0.3,  # Placeholder
                "rebalance_needed": False,  # Placeholder
            }

        except Exception as e:
            logger.error(f"Error analyzing channel liquidity: {e}")
            return {}

    async def cache_channel_data(self, channels: List[Dict]):
        """Cache channel data in Redis"""
        try:
            async with get_redis_connection() as redis:
                await redis.setex(
                    "lightning_channels", 300, json.dumps(channels)  # 5 minutes
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
                    self.metrics.update(
                        {
                            "num_peers": info.get("num_peers", 0),
                            "num_active_channels": info.get("num_active_channels", 0),
                            "synced_to_chain": info.get("synced_to_chain", False),
                        }
                    )

                # Cache metrics
                async with get_redis_connection() as redis:
                    serializable = {
                        k: v.isoformat() if hasattr(v, "isoformat") else v
                        for k, v in self.metrics.items()
                    }
                    await redis.setex(
                        "lightning_metrics", 300, json.dumps(serializable)  # 5 minutes
                    )

                await asyncio.sleep(60)  # Update every minute

            except Exception as e:
                logger.error(f"Error collecting Lightning metrics: {e}")
                await asyncio.sleep(30)

    async def get_network_stats(self) -> Dict[str, Any]:
        """Get Lightning Network statistics"""
        return {
            "blockchain": "lightning",
            "channels_monitored": self.metrics.get("channels_monitored", 0),
            "payments_tracked": self.metrics.get("payments_tracked", 0),
            "nodes_discovered": self.metrics.get("nodes_discovered", 0),
            "network_capacity": self.metrics.get("network_capacity", 0),
            "last_update": self.metrics.get("last_update"),
        }
