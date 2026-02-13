"""
Jackdaw Sentry - RPC Client Factory
Instantiates the correct RPC client for a given blockchain.
"""

import logging
import threading
from typing import Dict, Optional

from src.api.config import get_blockchain_config
from src.collectors.rpc.base_rpc import BaseRPCClient
from src.collectors.rpc.evm_rpc import EvmRpcClient
from src.collectors.rpc.bitcoin_rpc import BitcoinRpcClient

logger = logging.getLogger(__name__)

# Module-level cache: one client instance per blockchain
_clients: Dict[str, BaseRPCClient] = {}
_clients_lock = threading.Lock()


def get_rpc_client(blockchain: str) -> Optional[BaseRPCClient]:
    """Return a cached RPC client for the given blockchain.

    Returns ``None`` if the blockchain family is not yet supported by the
    lightweight RPC layer (e.g. Solana, Tron, XRPL — planned for later).
    """
    blockchain = blockchain.lower()

    if blockchain in _clients:
        return _clients[blockchain]

    with _clients_lock:
        # Double-check after acquiring lock
        if blockchain in _clients:
            return _clients[blockchain]

        config = get_blockchain_config(blockchain)
        if not config or not config.get("rpc_url"):
            logger.debug(f"No RPC config for {blockchain}")
            return None

        family = config.get("family", "")
        rpc_url = config["rpc_url"]

        client: Optional[BaseRPCClient] = None

        if family == "evm":
            client = EvmRpcClient(rpc_url, blockchain)
        elif family == "bitcoin":
            client = BitcoinRpcClient(
                rpc_url,
                blockchain,
                rpc_user=config.get("user"),
                rpc_password=config.get("password"),
            )
        else:
            logger.debug(
                f"RPC family '{family}' for {blockchain} not yet implemented"
            )
            return None

        _clients[blockchain] = client
        return client


async def close_all_clients() -> None:
    """Close all cached RPC client sessions (call on shutdown)."""
    for name, client in _clients.items():
        try:
            await client.close()
        except Exception as exc:
            logger.warning(f"Error closing RPC client for {name}: {exc}")
    _clients.clear()
