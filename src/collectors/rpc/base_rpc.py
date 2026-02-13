"""
Jackdaw Sentry - Base RPC Client
Abstract async JSON-RPC client with rate limiting, retries, and timeout handling.
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

import aiohttp

from src.api.config import settings
from src.collectors.base import Transaction, Block, Address

logger = logging.getLogger(__name__)


class RPCError(Exception):
    """Raised when an RPC call fails"""

    def __init__(self, message: str, code: int = -1, blockchain: str = "unknown"):
        self.message = message
        self.code = code
        self.blockchain = blockchain
        super().__init__(f"[{blockchain}] RPC error {code}: {message}")


class BaseRPCClient(ABC):
    """Abstract base for lightweight async RPC clients.

    Uses only ``aiohttp`` — no Web3, Solana SDK, or other heavy dependencies.
    Each subclass implements chain-family-specific request formatting and
    response parsing, but shares the HTTP transport, rate-limiter, and retry
    logic defined here.
    """

    def __init__(
        self,
        rpc_url: str,
        blockchain: str,
        *,
        timeout: int = 0,
        rate_limit_rpm: int = 0,
    ):
        self.rpc_url = rpc_url.rstrip("/")
        self.blockchain = blockchain
        self.timeout = timeout or settings.RPC_REQUEST_TIMEOUT_SECONDS
        self.rate_limit_rpm = rate_limit_rpm or settings.RPC_RATE_LIMIT_PER_MINUTE

        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()
        self._request_id = 0

        # Simple sliding-window rate limiter
        self._request_timestamps: List[float] = []
        self._rate_limit_lock = asyncio.Lock()

        # Metrics
        self.metrics = {
            "requests_sent": 0,
            "requests_failed": 0,
            "last_request": None,
            "avg_latency_ms": 0.0,
        }
        self._latency_sum = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _ensure_session(self) -> aiohttp.ClientSession:
        async with self._session_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                    headers={"Content-Type": "application/json"},
                )
            return self._session

    async def close(self) -> None:
        async with self._session_lock:
            if self._session and not self._session.closed:
                await self._session.close()
                self._session = None

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    async def _wait_for_rate_limit(self) -> None:
        """Block until we are within the per-minute request budget."""
        if self.rate_limit_rpm <= 0:
            return
        sleep_for = 0.0
        async with self._rate_limit_lock:
            now = time.monotonic()
            window = 60.0
            self._request_timestamps = [
                ts for ts in self._request_timestamps if now - ts < window
            ]
            if len(self._request_timestamps) >= self.rate_limit_rpm:
                sleep_for = window - (now - self._request_timestamps[0]) + 0.05
            self._request_timestamps.append(time.monotonic())
        if sleep_for > 0:
            logger.debug(
                f"[{self.blockchain}] RPC rate limit hit, sleeping {sleep_for:.1f}s"
            )
            await asyncio.sleep(sleep_for)

    # ------------------------------------------------------------------
    # JSON-RPC transport
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _json_rpc(
        self,
        method: str,
        params: Any = None,
        *,
        retries: int = 2,
    ) -> Any:
        """Send a JSON-RPC 2.0 request and return the ``result`` field."""
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params if params is not None else [],
        }
        return await self._post(payload, retries=retries)

    async def _post(
        self,
        payload: Dict[str, Any],
        *,
        retries: int = 2,
    ) -> Any:
        """POST *payload* to the RPC endpoint with retries."""
        await self._wait_for_rate_limit()
        session = await self._ensure_session()
        last_exc: Optional[Exception] = None

        for attempt in range(1, retries + 2):
            start = time.monotonic()
            try:
                async with session.post(self.rpc_url, json=payload) as resp:
                    elapsed_ms = (time.monotonic() - start) * 1000
                    self._record_latency(elapsed_ms)
                    body = await resp.json(content_type=None)

                    if resp.status != 200:
                        raise RPCError(
                            f"HTTP {resp.status}: {body}",
                            code=resp.status,
                            blockchain=self.blockchain,
                        )

                    if "error" in body and body["error"]:
                        err = body["error"]
                        raise RPCError(
                            err.get("message", str(err)),
                            code=err.get("code", -1),
                            blockchain=self.blockchain,
                        )

                    self.metrics["requests_sent"] += 1
                    self.metrics["last_request"] = datetime.now(timezone.utc).isoformat()
                    return body.get("result")

            except RPCError as rpc_exc:
                # Retry transient 5xx server errors and rate-limit responses.
                # Count logical failed request only once on final failure.
                if rpc_exc.code and 500 <= rpc_exc.code <= 599 or rpc_exc.code == 429:
                    last_exc = rpc_exc
                    if attempt <= retries:
                        wait = 0.5 * (2 ** (attempt - 1))
                        logger.warning(
                            f"[{self.blockchain}] RPC transient error (HTTP {rpc_exc.code}) "
                            f"attempt {attempt}, retrying in {wait:.1f}s"
                        )
                        await asyncio.sleep(wait)
                        continue
                self.metrics["requests_failed"] += 1
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                # Count logical failed request only once on final failure,
                # not on every retry attempt.
                elapsed_ms = (time.monotonic() - start) * 1000
                self._record_latency(elapsed_ms)
                last_exc = exc
                if attempt <= retries:
                    wait = 0.5 * (2 ** (attempt - 1))
                    logger.warning(
                        f"[{self.blockchain}] RPC attempt {attempt} failed: {exc}, "
                        f"retrying in {wait:.1f}s"
                    )
                    await asyncio.sleep(wait)
                else:
                    self.metrics["requests_failed"] += 1

        raise RPCError(
            f"RPC request failed after {retries + 1} attempts: {last_exc}",
            blockchain=self.blockchain,
        )

    def _record_latency(self, ms: float) -> None:
        total = self.metrics["requests_sent"] + self.metrics["requests_failed"]
        self._latency_sum += ms
        self.metrics["avg_latency_ms"] = (
            self._latency_sum / (total + 1) if total >= 0 else ms
        )

    # ------------------------------------------------------------------
    # Abstract interface — each chain family implements these
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_transaction(self, tx_hash: str) -> Optional[Transaction]:
        """Fetch a transaction by hash from the live RPC."""

    @abstractmethod
    async def get_address_info(self, address: str) -> Optional[Address]:
        """Fetch address balance and metadata from the live RPC."""

    @abstractmethod
    async def get_address_transactions(
        self, address: str, *, limit: int = 25, offset: int = 0
    ) -> List[Transaction]:
        """Fetch recent transactions for an address.

        Not all chains support this natively via RPC; subclasses may
        return an empty list and rely on indexed data (Neo4j / explorer API).
        """

    @abstractmethod
    async def get_block(self, block_id: Union[int, str]) -> Optional[Block]:
        """Fetch a block by number or hash."""

    async def health_check(self) -> bool:
        """Quick liveness probe against the RPC endpoint."""
        try:
            await self.get_latest_block_number()
            return True
        except Exception:
            return False

    @abstractmethod
    async def get_latest_block_number(self) -> int:
        """Return the current chain tip block number."""
