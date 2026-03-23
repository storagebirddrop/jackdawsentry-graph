"""
Jackdaw Sentry — BridgeTracer

Resolves cross-chain bridge hops by querying bridge protocol APIs to populate
``destination_chain``, ``destination_address``, and ``destination_asset`` for
a given source-chain transaction.

Resolution is attempted immediately on ``detect_bridge_hop``; the result is
persisted to the ``bridge_correlations`` PostgreSQL table so that subsequent
graph expansions use the cached record rather than re-hitting the API.

Supported resolution paths (tx_hash → API → destination):
  native_amm   — THORChain (via THORNode API)
  lock_mint    — Wormhole (via Wormholescan API), Allbridge Core API
  burn_release — Synapse (via Synapse API)
  solver       — LI.FI, Squid, Mayan, deBridge, Symbiosis

Protocols that require an intermediate ID from decoded event logs (Chainflip,
Across, Celer, Rango, Relay, Stargate) return ``None`` from
``detect_bridge_hop``; the hop node stays ``status="pending"`` until a
future log-decoding pass resolves it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from dataclasses import field
from typing import Optional

import httpx

from src.tracing.bridge_registry import BridgeProtocol
from src.tracing.bridge_registry import detect_protocol_by_contract

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 10.0  # seconds per bridge API call

# ---------------------------------------------------------------------------
# Wormhole chain-ID → internal chain name
# ---------------------------------------------------------------------------

_WORMHOLE_CHAIN_MAP: dict[int, str] = {
    1: "solana",
    2: "ethereum",
    4: "bsc",
    5: "polygon",
    6: "avalanche",
    10: "fantom",
    14: "celo",
    16: "moonbeam",
    21: "sui",
    22: "aptos",
    23: "arbitrum",
    24: "optimism",
    30: "base",
}

# THORChain ticker prefix → internal chain name
_THORCHAIN_CHAIN_MAP: dict[str, str] = {
    "ETH": "ethereum",
    "BTC": "bitcoin",
    "BSC": "bsc",
    "AVAX": "avalanche",
    "GAIA": "cosmos",
    "LTC": "litecoin",
    "BCH": "bitcoin_cash",
    "DOGE": "dogecoin",
}


# ---------------------------------------------------------------------------
# BridgeCorrelation
# ---------------------------------------------------------------------------


@dataclass
class BridgeCorrelation:
    """Resolved or partially-resolved bridge hop correlation record.

    Attribute names intentionally match what ``graph.py`` expects when it
    accesses ``correlation.<field>`` after ``detect_bridge_hop`` returns.
    ``protocol`` (not ``protocol_id``) is used because the DB column was
    renamed in migration 010 but the application contract predates that.
    """

    protocol: str                            # e.g. "thorchain"
    mechanism: str                           # e.g. "native_amm"
    source_chain: str
    source_tx_hash: str
    source_address: str                      # sender address; "" when unavailable
    source_asset: str
    source_amount: Optional[float]
    source_fiat_value: Optional[float]
    destination_chain: Optional[str]
    destination_tx_hash: Optional[str]
    destination_address: Optional[str]
    destination_asset: Optional[str]
    destination_amount: Optional[float]
    destination_fiat_value: Optional[float]
    time_delta_seconds: Optional[int]
    status: str                              # "pending" | "completed" | "failed"
    correlation_confidence: float
    order_id: Optional[str] = field(default=None)
    resolution_method: Optional[str] = field(default=None)


# ---------------------------------------------------------------------------
# BridgeTracer
# ---------------------------------------------------------------------------


class BridgeTracer:
    """Resolve bridge hops and persist results to ``bridge_correlations``.

    Instantiated per-request in ``graph.py``; uses module-level
    ``get_postgres_pool()`` lazily so construction never blocks.
    """

    @staticmethod
    async def _make_http_request(url: str) -> Optional[dict]:
        """Helper method to make HTTP requests with shared client configuration."""
        import httpx
        
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.debug("HTTP request failed for %s: %s", url, exc)
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def lookup_correlation(
        self,
        source_chain: str,
        source_tx_hash: str,
    ) -> Optional[BridgeCorrelation]:
        """Return a stored correlation from ``bridge_correlations``, or None.

        Reads the ``protocol_id`` column (renamed from ``protocol`` in
        migration 010) and surfaces it as the ``protocol`` attribute.
        """
        from src.api.database import get_postgres_pool

        try:
            pg = get_postgres_pool()
            async with pg.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        protocol_id,
                        mechanism,
                        source_chain,
                        source_tx_hash,
                        source_address,
                        source_asset,
                        CAST(source_amount        AS FLOAT) AS source_amount,
                        CAST(source_fiat_value    AS FLOAT) AS source_fiat_value,
                        destination_chain,
                        destination_tx_hash,
                        destination_address,
                        destination_asset,
                        CAST(destination_amount   AS FLOAT) AS destination_amount,
                        CAST(destination_fiat_value AS FLOAT) AS destination_fiat_value,
                        time_delta_seconds,
                        status,
                        correlation_confidence,
                        order_id,
                        resolution_method
                    FROM bridge_correlations
                    WHERE source_chain    = $1
                      AND source_tx_hash = $2
                    LIMIT 1
                    """,
                    source_chain,
                    source_tx_hash,
                )
        except Exception as exc:
            logger.warning("lookup_correlation DB error for %s/%s: %s", source_chain, source_tx_hash, exc)
            return None

        if not row:
            return None

        return BridgeCorrelation(
            protocol=row["protocol_id"],
            mechanism=row["mechanism"],
            source_chain=row["source_chain"],
            source_tx_hash=row["source_tx_hash"],
            source_address=row["source_address"] or "",
            source_asset=row["source_asset"] or "",
            source_amount=row["source_amount"],
            source_fiat_value=row["source_fiat_value"],
            destination_chain=row["destination_chain"],
            destination_tx_hash=row["destination_tx_hash"],
            destination_address=row["destination_address"],
            destination_asset=row["destination_asset"],
            destination_amount=row["destination_amount"],
            destination_fiat_value=row["destination_fiat_value"],
            time_delta_seconds=row["time_delta_seconds"],
            status=row["status"],
            correlation_confidence=row["correlation_confidence"],
            order_id=row["order_id"],
            resolution_method=row["resolution_method"],
        )

    async def detect_bridge_hop(
        self,
        tx_hash: str,
        blockchain: str,
        to_address: Optional[str],
    ) -> Optional[BridgeCorrelation]:
        """Detect and resolve a bridge hop by querying the relevant protocol API.

        Returns a ``BridgeCorrelation`` when the hop can be identified, or
        ``None`` when the contract address is not a known bridge or the
        resolution path requires intermediate IDs that are not yet available.
        """
        if not to_address:
            return None

        protocol = detect_protocol_by_contract(blockchain, to_address)
        if protocol is None:
            logger.debug(
                "detect_bridge_hop: %s is not a known bridge contract on %s",
                to_address,
                blockchain,
            )
            return None

        logger.debug(
            "detect_bridge_hop: %s tx %s → protocol %s (mechanism %s)",
            blockchain,
            tx_hash[:16],
            protocol.protocol_id,
            protocol.mechanism,
        )

        try:
            return await self._resolve_by_mechanism(protocol, tx_hash, blockchain)
        except Exception as exc:
            logger.warning(
                "detect_bridge_hop failed for %s tx %s (protocol %s): %s",
                blockchain,
                tx_hash[:16],
                protocol.protocol_id,
                exc,
            )
            return None

    async def store_correlation(self, correlation: BridgeCorrelation) -> None:
        """Upsert a correlation record into ``bridge_correlations``.

        On conflict (same source_chain + source_tx_hash), updates all
        destination fields and status only when the stored record is still
        ``pending`` — completed records are never overwritten.
        """
        from src.api.database import get_postgres_pool

        try:
            pg = get_postgres_pool()
            async with pg.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO bridge_correlations (
                        protocol_id, mechanism,
                        source_chain, source_tx_hash, source_address,
                        source_asset, source_amount, source_fiat_value,
                        destination_chain, destination_tx_hash, destination_address,
                        destination_asset, destination_amount, destination_fiat_value,
                        time_delta_seconds, status, correlation_confidence,
                        order_id, resolution_method,
                        resolved_at, updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7, $8,
                        $9, $10, $11,
                        $12, $13, $14,
                        $15, $16, $17,
                        $18, $19,
                        CASE WHEN $16 = 'completed' THEN NOW() ELSE NULL END,
                        NOW()
                    )
                    ON CONFLICT (source_chain, source_tx_hash)
                    DO UPDATE SET
                        destination_chain        = EXCLUDED.destination_chain,
                        destination_tx_hash      = EXCLUDED.destination_tx_hash,
                        destination_address      = EXCLUDED.destination_address,
                        destination_asset        = EXCLUDED.destination_asset,
                        destination_amount       = EXCLUDED.destination_amount,
                        destination_fiat_value   = EXCLUDED.destination_fiat_value,
                        time_delta_seconds       = EXCLUDED.time_delta_seconds,
                        status                   = EXCLUDED.status,
                        correlation_confidence   = EXCLUDED.correlation_confidence,
                        order_id                 = EXCLUDED.order_id,
                        resolution_method        = EXCLUDED.resolution_method,
                        resolved_at = CASE
                            WHEN EXCLUDED.status = 'completed' THEN NOW()
                            ELSE bridge_correlations.resolved_at
                        END,
                        updated_at               = NOW()
                    WHERE bridge_correlations.status = 'pending'
                    """,
                    correlation.protocol,
                    correlation.mechanism,
                    correlation.source_chain,
                    correlation.source_tx_hash,
                    correlation.source_address,
                    correlation.source_asset,
                    correlation.source_amount,
                    correlation.source_fiat_value,
                    correlation.destination_chain,
                    correlation.destination_tx_hash,
                    correlation.destination_address,
                    correlation.destination_asset,
                    correlation.destination_amount,
                    correlation.destination_fiat_value,
                    correlation.time_delta_seconds,
                    correlation.status,
                    correlation.correlation_confidence,
                    correlation.order_id,
                    correlation.resolution_method,
                )
        except Exception as exc:
            logger.warning(
                "store_correlation failed for %s/%s: %s",
                correlation.source_chain,
                correlation.source_tx_hash,
                exc,
            )

    # ------------------------------------------------------------------
    # Mechanism dispatch
    # ------------------------------------------------------------------

    async def _resolve_by_mechanism(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Dispatch to the appropriate resolver based on bridge mechanism."""
        if protocol.mechanism == "native_amm":
            return await self._resolve_native_amm(protocol, tx_hash, blockchain)
        if protocol.mechanism == "lock_mint":
            return await self._resolve_lock_mint(protocol, tx_hash, blockchain)
        if protocol.mechanism == "burn_release":
            return await self._resolve_burn_release(protocol, tx_hash, blockchain)
        if protocol.mechanism == "solver":
            return await self._resolve_solver(protocol, tx_hash, blockchain)
        if protocol.mechanism == "liquidity":
            return await self._resolve_liquidity(protocol, tx_hash, blockchain)
        logger.debug("No resolver for mechanism %s (protocol %s)", protocol.mechanism, protocol.protocol_id)
        return None

    # ------------------------------------------------------------------
    # native_amm — THORChain (Chainflip requires swap_id from calldata)
    # ------------------------------------------------------------------

    async def _resolve_native_amm(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Resolve native_amm hops.

        THORChain: direct tx_hash API lookup.
        Chainflip: decode vault SwapNative/SwapToken event to get dstChain,
                   then attempt broker status lookup by swap_id when available.
        """
        if protocol.protocol_id == "thorchain":
            return await self._thorchain_lookup(protocol, tx_hash, blockchain)
        if protocol.protocol_id == "chainflip":
            return await self._chainflip_lookup(protocol, tx_hash, blockchain)
        logger.debug(
            "_resolve_native_amm: %s not handled — returning None",
            protocol.protocol_id,
        )
        return None

    async def _thorchain_lookup(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Query THORNode API to resolve a THORChain swap hop."""
        url = f"{protocol.api_base}/thorchain/tx/details/{tx_hash}"
        t0 = time.monotonic()

        data = await self._make_http_request(url)
        if data is None:
            return None

        observed = data.get("observed_tx", {})
        tx = observed.get("tx", {})
        source_address = tx.get("from_address", "")
        coins = tx.get("coins") or []
        source_asset = _thorchain_asset(coins[0].get("asset", "")) if coins else ""
        source_amount = _thorchain_amount(coins[0].get("amount")) if coins else None

        out_txs: list = data.get("out_txs") or []
        # Filter out REFUND outputs (THORChain emits a refund tx on failed swaps)
        completed_outs = [
            o for o in out_txs
            if not str(o.get("memo", "")).upper().startswith("REFUND")
        ]

        if not completed_outs:
            return BridgeCorrelation(
                protocol=protocol.protocol_id,
                mechanism=protocol.mechanism,
                source_chain=blockchain,
                source_tx_hash=tx_hash,
                source_address=source_address,
                source_asset=source_asset,
                source_amount=source_amount,
                source_fiat_value=None,
                destination_chain=None,
                destination_tx_hash=None,
                destination_address=None,
                destination_asset=None,
                destination_amount=None,
                destination_fiat_value=None,
                time_delta_seconds=None,
                status="pending",
                correlation_confidence=0.85,
                resolution_method="thorchain_api",
            )

        out = completed_outs[0]
        raw_chain = out.get("chain", "")
        dest_chain = _THORCHAIN_CHAIN_MAP.get(raw_chain.upper(), raw_chain.lower())
        dest_address = out.get("to_address", "")
        dest_coins = out.get("coins") or []
        dest_asset = _thorchain_asset(dest_coins[0].get("asset", "")) if dest_coins else ""
        dest_amount = _thorchain_amount(dest_coins[0].get("amount")) if dest_coins else None
        dest_tx = out.get("id", "") or None

        elapsed = time.monotonic() - t0

        return BridgeCorrelation(
            protocol=protocol.protocol_id,
            mechanism=protocol.mechanism,
            source_chain=blockchain,
            source_tx_hash=tx_hash,
            source_address=source_address,
            source_asset=source_asset,
            source_amount=source_amount,
            source_fiat_value=None,
            destination_chain=dest_chain or None,
            destination_tx_hash=dest_tx,
            destination_address=dest_address or None,
            destination_asset=dest_asset or None,
            destination_amount=dest_amount,
            destination_fiat_value=None,
            time_delta_seconds=elapsed if dest_tx else None,
            status="completed" if (dest_chain and dest_address) else "pending",
            correlation_confidence=0.95,
            resolution_method="thorchain_api",
        )

    # ------------------------------------------------------------------
    # lock_mint — Wormhole, Allbridge
    # ------------------------------------------------------------------

    async def _resolve_lock_mint(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Resolve lock_mint hops (Wormhole, Allbridge)."""
        if protocol.protocol_id == "wormhole":
            return await self._wormhole_lookup(protocol, tx_hash, blockchain)
        if protocol.protocol_id == "allbridge":
            return await self._allbridge_lookup(protocol, tx_hash, blockchain)
        return None

    async def _wormhole_lookup(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Query Wormholescan API to resolve a Wormhole lock-mint hop."""
        url = f"{protocol.api_base}/api/v1/operations?txHash={tx_hash}"

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url)

        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()

        operations = data.get("operations") or []
        if not operations:
            return None

        op = operations[0]
        target_chain_id: Optional[int] = None
        dest_address: Optional[str] = None
        dest_asset: Optional[str] = None
        dest_tx_hash: Optional[str] = None

        # Wormholescan nests destination info across several possible paths
        content = op.get("content") or {}
        payload = content.get("payload") or {}
        target_chain_id = payload.get("toChain") or target_chain_id
        dest_address = payload.get("toAddress") or dest_address

        target_chain_obj = op.get("targetChain") or {}
        if not target_chain_id:
            target_chain_id = target_chain_obj.get("chainId")
        dest_tx_hash = (target_chain_obj.get("transaction") or {}).get("txHash") or dest_tx_hash

        token_info = (op.get("data") or {})
        dest_asset = token_info.get("symbol") or dest_asset

        dest_chain = _WORMHOLE_CHAIN_MAP.get(target_chain_id) if target_chain_id else None
        status = "completed" if (dest_chain and dest_address) else "pending"

        return BridgeCorrelation(
            protocol=protocol.protocol_id,
            mechanism=protocol.mechanism,
            source_chain=blockchain,
            source_tx_hash=tx_hash,
            source_address="",
            source_asset="",
            source_amount=None,
            source_fiat_value=None,
            destination_chain=dest_chain,
            destination_tx_hash=dest_tx_hash,
            destination_address=dest_address,
            destination_asset=dest_asset,
            destination_amount=None,
            destination_fiat_value=None,
            time_delta_seconds=None,
            status=status,
            correlation_confidence=0.90 if status == "completed" else 0.60,
            resolution_method="wormholescan_api",
        )

    async def _allbridge_lookup(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Query Allbridge Core API to resolve a lock-mint hop."""
        # Allbridge uses chain short names like ETH, BSC, SOL
        allbridge_chain = _to_allbridge_chain(blockchain)
        url = f"{protocol.api_base}/v1/receive/{allbridge_chain}/{tx_hash}"

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url)

        if resp.status_code in (404, 422):
            return None
        resp.raise_for_status()
        data = resp.json()

        dest_chain_raw = data.get("destinationChainSymbol", "")
        dest_chain = _from_allbridge_chain(dest_chain_raw)
        dest_address = data.get("receiverAddress")
        dest_asset = data.get("destinationTokenSymbol")
        dest_tx_hash = data.get("receiveTransactionHash")
        status_raw = data.get("status", "")
        status = "completed" if status_raw.upper() == "COMPLETED" else "pending"

        return BridgeCorrelation(
            protocol=protocol.protocol_id,
            mechanism=protocol.mechanism,
            source_chain=blockchain,
            source_tx_hash=tx_hash,
            source_address="",
            source_asset=data.get("sourceTokenSymbol", ""),
            source_amount=_safe_float(data.get("sentAmount")),
            source_fiat_value=None,
            destination_chain=dest_chain or None,
            destination_tx_hash=dest_tx_hash,
            destination_address=dest_address,
            destination_asset=dest_asset,
            destination_amount=_safe_float(data.get("receiveAmount")),
            destination_fiat_value=None,
            time_delta_seconds=None,
            status=status,
            correlation_confidence=0.88 if status == "completed" else 0.55,
            resolution_method="allbridge_api",
        )

    # ------------------------------------------------------------------
    # burn_release — Synapse (Celer requires transfer_id from logs)
    # ------------------------------------------------------------------

    async def _resolve_burn_release(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Resolve burn_release hops.

        Synapse: direct tx_hash API lookup.
        Celer: decode Send event to extract transferId, then call POST status API.
        """
        if protocol.protocol_id == "synapse":
            return await self._synapse_lookup(protocol, tx_hash, blockchain)
        if protocol.protocol_id == "celer":
            return await self._celer_lookup(protocol, tx_hash, blockchain)
        logger.debug(
            "_resolve_burn_release: %s not handled — returning None",
            protocol.protocol_id,
        )
        return None

    async def _synapse_lookup(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Query Synapse API for bridge receipt status."""
        url = f"{protocol.api_base}/v1/bridge/receipts?originTxHash={tx_hash}"

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url)

        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()

        if data.get("pending"):
            return BridgeCorrelation(
                protocol=protocol.protocol_id,
                mechanism=protocol.mechanism,
                source_chain=blockchain,
                source_tx_hash=tx_hash,
                source_address="",
                source_asset="",
                source_amount=None,
                source_fiat_value=None,
                destination_chain=None,
                destination_tx_hash=None,
                destination_address=None,
                destination_asset=None,
                destination_amount=None,
                destination_fiat_value=None,
                time_delta_seconds=None,
                status="pending",
                correlation_confidence=0.60,
                resolution_method="synapse_api",
            )

        receipts = data.get("receipts") or []
        if not receipts:
            return None

        receipt = receipts[0]
        to_info = receipt.get("toInfo") or {}
        dest_chain_id = to_info.get("chainID")
        dest_address = to_info.get("address")
        dest_asset = to_info.get("token")
        dest_amount = _safe_float(to_info.get("value"))
        dest_tx_hash = receipt.get("toTxnHash")

        # Synapse uses numeric EVM chain IDs
        dest_chain = _evm_chain_id_to_name(dest_chain_id)

        return BridgeCorrelation(
            protocol=protocol.protocol_id,
            mechanism=protocol.mechanism,
            source_chain=blockchain,
            source_tx_hash=tx_hash,
            source_address="",
            source_asset=(receipt.get("fromInfo") or {}).get("token", ""),
            source_amount=_safe_float((receipt.get("fromInfo") or {}).get("value")),
            source_fiat_value=None,
            destination_chain=dest_chain,
            destination_tx_hash=dest_tx_hash,
            destination_address=dest_address,
            destination_asset=dest_asset,
            destination_amount=dest_amount,
            destination_fiat_value=None,
            time_delta_seconds=None,
            status="completed" if (dest_chain and dest_address) else "pending",
            correlation_confidence=0.90,
            resolution_method="synapse_api",
        )

    # ------------------------------------------------------------------
    # solver — LI.FI, Squid, Mayan, deBridge, Symbiosis
    #          (Rango / Relay require request_id from tx data)
    # ------------------------------------------------------------------

    async def _resolve_solver(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Dispatch solver-mechanism protocols to their specific resolvers.

        LI.FI, Squid, Mayan, deBridge, Symbiosis: direct tx_hash API lookup.
        Rango: status API supports txId parameter — no log decode required.
        Relay: status API supports originTxHash search — no log decode required.
        """
        resolvers = {
            "lifi": self._lifi_lookup,
            "squid": self._squid_lookup,
            "mayan": self._mayan_lookup,
            "debridge": self._debridge_lookup,
            "symbiosis": self._symbiosis_lookup,
            "rango": self._rango_lookup,
            "relay": self._relay_lookup,
        }
        resolver = resolvers.get(protocol.protocol_id)
        if resolver is None:
            logger.debug(
                "_resolve_solver: %s not handled — returning None",
                protocol.protocol_id,
            )
            return None
        return await resolver(protocol, tx_hash, blockchain)

    async def _lifi_lookup(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Query LI.FI status API."""
        url = f"{protocol.api_base}/status?txHash={tx_hash}"

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url)

        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()

        status_raw = data.get("status", "")
        # LI.FI statuses: PENDING, DONE, FAILED, NOT_FOUND
        if status_raw == "NOT_FOUND":
            return None

        receiving = data.get("receiving") or {}
        sending = data.get("sending") or {}

        dest_chain_id = receiving.get("chainId")
        dest_address = receiving.get("address")
        dest_asset = (receiving.get("token") or {}).get("symbol")
        dest_amount = _safe_float(receiving.get("amount"))
        dest_tx_hash = receiving.get("txHash")

        src_asset = (sending.get("token") or {}).get("symbol", "")
        src_amount = _safe_float(sending.get("amount"))

        dest_chain = _evm_chain_id_to_name(dest_chain_id)
        status = "completed" if status_raw == "DONE" else "pending"

        return BridgeCorrelation(
            protocol=protocol.protocol_id,
            mechanism=protocol.mechanism,
            source_chain=blockchain,
            source_tx_hash=tx_hash,
            source_address=sending.get("address", ""),
            source_asset=src_asset,
            source_amount=src_amount,
            source_fiat_value=None,
            destination_chain=dest_chain,
            destination_tx_hash=dest_tx_hash,
            destination_address=dest_address,
            destination_asset=dest_asset,
            destination_amount=dest_amount,
            destination_fiat_value=None,
            time_delta_seconds=None,
            status=status,
            correlation_confidence=0.92 if status == "completed" else 0.65,
            resolution_method="lifi_api",
        )

    async def _squid_lookup(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Query Squid Router status API."""
        url = f"{protocol.api_base}/v2/status?transactionId={tx_hash}"

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url)

        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()

        squid_status = data.get("squidTransactionStatus", "")
        dest_chain_id = data.get("toChainId")
        dest_address = data.get("toAddress")
        dest_asset = (data.get("toToken") or {}).get("symbol")
        dest_amount = _safe_float(data.get("toAmount"))

        to_chain_data = data.get("toChain") or {}
        dest_tx_hash = (to_chain_data.get("txData") or {}).get("txHash")

        dest_chain = _evm_chain_id_to_name(dest_chain_id)
        status = "completed" if squid_status == "success" else "pending"

        return BridgeCorrelation(
            protocol=protocol.protocol_id,
            mechanism=protocol.mechanism,
            source_chain=blockchain,
            source_tx_hash=tx_hash,
            source_address="",
            source_asset="",
            source_amount=None,
            source_fiat_value=None,
            destination_chain=dest_chain,
            destination_tx_hash=dest_tx_hash,
            destination_address=dest_address,
            destination_asset=dest_asset,
            destination_amount=dest_amount,
            destination_fiat_value=None,
            time_delta_seconds=None,
            status=status,
            correlation_confidence=0.90 if status == "completed" else 0.60,
            resolution_method="squid_api",
        )

    async def _mayan_lookup(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Query Mayan Finance status API (signature = tx_hash for EVM)."""
        url = f"{protocol.api_base}/v3/swap/trx/{tx_hash}"

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url)

        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()

        mayan_status = data.get("status", "")
        dest_chain = (data.get("destChain", "") or "").lower() or None
        dest_address = data.get("destAddress") or data.get("recipient")
        dest_asset = (data.get("toToken") or {}).get("symbol")
        dest_amount = _safe_float(data.get("destAmount") or data.get("toAmount"))
        dest_tx_hash = data.get("destTxHash")

        status = "completed" if mayan_status.upper() == "COMPLETED" else "pending"

        return BridgeCorrelation(
            protocol=protocol.protocol_id,
            mechanism=protocol.mechanism,
            source_chain=blockchain,
            source_tx_hash=tx_hash,
            source_address="",
            source_asset=(data.get("fromToken") or {}).get("symbol", ""),
            source_amount=_safe_float(data.get("fromAmount") or data.get("srcAmount")),
            source_fiat_value=None,
            destination_chain=dest_chain,
            destination_tx_hash=dest_tx_hash,
            destination_address=dest_address,
            destination_asset=dest_asset,
            destination_amount=dest_amount,
            destination_fiat_value=None,
            time_delta_seconds=None,
            status=status,
            correlation_confidence=0.90 if status == "completed" else 0.60,
            resolution_method="mayan_api",
        )

    async def _debridge_lookup(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Query deBridge API (two-step: tx → order_id → order details)."""
        # Step 1: resolve tx_hash → order_id
        orders_url = f"{protocol.api_base}/v1.0/dln/tx/{tx_hash}/orders"

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(orders_url)

        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        orders_data = resp.json()

        order_ids: list = orders_data.get("orderIds") or []
        if not order_ids:
            return None
        order_id = str(order_ids[0])

        # Step 2: fetch order details
        order_url = f"{protocol.api_base}/v1.0/dln/order/{order_id}"

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(order_url)

        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        order = resp.json()

        state = order.get("status", {})
        order_status = state.get("value", "")
        # deBridge statuses: Created, FulFilled, SentUnlock, ClaimedUnlock, etc.
        is_complete = order_status.lower() in ("fulfilled", "sentunlock", "claimedunlock")

        take = order.get("takeOffer") or {}
        dest_chain_id = take.get("chainId")
        dest_address = take.get("receiverDst")
        dest_asset_addr = take.get("tokenAddress")
        dest_amount = _safe_float(take.get("finalAmount") or take.get("amount"))

        # deBridge uses EVM chain IDs for take side
        dest_chain = _evm_chain_id_to_name(dest_chain_id)

        # Fulfil tx hash lives in order metadata
        fulfill_tx = (order.get("fulfilledDstEventMetadata") or {}).get("transactionHash")

        return BridgeCorrelation(
            protocol=protocol.protocol_id,
            mechanism=protocol.mechanism,
            source_chain=blockchain,
            source_tx_hash=tx_hash,
            source_address="",
            source_asset="",
            source_amount=None,
            source_fiat_value=None,
            destination_chain=dest_chain,
            destination_tx_hash=fulfill_tx,
            destination_address=dest_address,
            destination_asset=dest_asset_addr,  # token contract addr; symbol unavailable here
            destination_amount=dest_amount,
            destination_fiat_value=None,
            time_delta_seconds=None,
            status="completed" if is_complete else "pending",
            correlation_confidence=0.90 if is_complete else 0.70,
            order_id=order_id,
            resolution_method="debridge_api",
        )

    async def _symbiosis_lookup(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Query Symbiosis Finance status API."""
        url = f"{protocol.api_base}/v1/tx/{tx_hash}"

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url)

        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()

        sym_status = data.get("state", "")
        is_complete = sym_status.upper() in ("MINTING", "SUCCESS")

        dest_chain_id = data.get("toChainId")
        dest_address = data.get("to")
        dest_asset = (data.get("tokenOut") or {}).get("symbol")
        dest_amount = _safe_float(data.get("amountOut"))
        dest_tx_hash = data.get("transitTokenSentInfo", {}).get("txHash")

        dest_chain = _evm_chain_id_to_name(dest_chain_id)

        return BridgeCorrelation(
            protocol=protocol.protocol_id,
            mechanism=protocol.mechanism,
            source_chain=blockchain,
            source_tx_hash=tx_hash,
            source_address=data.get("from", ""),
            source_asset=(data.get("tokenIn") or {}).get("symbol", ""),
            source_amount=_safe_float(data.get("amountIn")),
            source_fiat_value=None,
            destination_chain=dest_chain,
            destination_tx_hash=dest_tx_hash,
            destination_address=dest_address,
            destination_asset=dest_asset,
            destination_amount=dest_amount,
            destination_fiat_value=None,
            time_delta_seconds=None,
            status="completed" if is_complete else "pending",
            correlation_confidence=0.88 if is_complete else 0.60,
            resolution_method="symbiosis_api",
        )

    # ------------------------------------------------------------------
    # liquidity — Across (depositId from logs → status API)
    #             Stargate (destChain from logs, no public status API)
    # ------------------------------------------------------------------

    async def _resolve_liquidity(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Resolve liquidity-pool bridge hops via on-chain event log decoding.

        Across:   Fetch receipt → decode V3FundsDeposited → depositId →
                  GET /deposit/status?depositId={id}&originChainId={chainId}.
        Stargate: Fetch receipt → decode Swap → LayerZero destChainId →
                  mark completed with destination chain (no public status API).
        """
        if protocol.protocol_id == "across":
            return await self._across_lookup(protocol, tx_hash, blockchain)
        if protocol.protocol_id == "stargate":
            return await self._stargate_lookup(protocol, tx_hash, blockchain)
        logger.debug(
            "_resolve_liquidity: %s not handled — returning None",
            protocol.protocol_id,
        )
        return None

    async def _across_lookup(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Resolve an Across V3 deposit by decoding the V3FundsDeposited event.

        Extracts ``depositId`` and ``destinationChainId`` from indexed topics,
        then queries ``/deposit/status?depositId={id}&originChainId={chainId}``.
        """
        from src.tracing.bridge_log_decoder import decode_bridge_deposit
        from src.tracing.bridge_log_decoder import CHAIN_TO_EVM_ID
        from src.tracing.bridge_log_decoder import _LZ_V1_CHAIN_MAP  # noqa: F401

        decoded = await decode_bridge_deposit("across", blockchain, tx_hash)
        if decoded is None:
            return None

        deposit_id: int = decoded["deposit_id"]
        dest_chain_evm: int = decoded["dest_chain_id_evm"]
        origin_chain_id = CHAIN_TO_EVM_ID.get(blockchain)

        # Build status URL; originChainId required by Across API
        params = f"depositId={deposit_id}"
        if origin_chain_id:
            params += f"&originChainId={origin_chain_id}"
        url = f"{protocol.api_base}/deposit/status?{params}"

        data = await self._make_http_request(url)
        if data is None:
            # Log decode succeeded but API not yet ready; store order_id as pending
            dest_chain = _evm_chain_id_to_name(dest_chain_evm)
            return BridgeCorrelation(
                protocol=protocol.protocol_id,
                mechanism=protocol.mechanism,
                source_chain=blockchain,
                source_tx_hash=tx_hash,
                source_address="",
                source_asset="",
                source_amount=None,
                source_fiat_value=None,
                destination_chain=dest_chain,
                destination_tx_hash=None,
                destination_address=None,
                destination_asset=None,
                destination_amount=None,
                destination_fiat_value=None,
                time_delta_seconds=None,
                status="pending",
                correlation_confidence=0.75,
                order_id=str(deposit_id),
                resolution_method="across_log_decode",
            )

        status_raw = (data.get("status") or "").upper()
        is_filled = status_raw in ("FILLED", "RELAYED")

        dest_chain = (data.get("destinationChainId") and _evm_chain_id_to_name(data["destinationChainId"])) \
            or _evm_chain_id_to_name(dest_chain_evm)
        dest_address = data.get("depositor") or data.get("recipient")
        dest_tx_hash = data.get("fillTxHash")
        src_asset = data.get("inputToken")
        dest_asset = data.get("outputToken")
        src_amount = _safe_float(data.get("inputAmount"))
        dest_amount = _safe_float(data.get("outputAmount"))

        return BridgeCorrelation(
            protocol=protocol.protocol_id,
            mechanism=protocol.mechanism,
            source_chain=blockchain,
            source_tx_hash=tx_hash,
            source_address="",
            source_asset=src_asset or "",
            source_amount=src_amount,
            source_fiat_value=None,
            destination_chain=dest_chain,
            destination_tx_hash=dest_tx_hash,
            destination_address=dest_address,
            destination_asset=dest_asset,
            destination_amount=dest_amount,
            destination_fiat_value=None,
            time_delta_seconds=None,
            status="completed" if is_filled else "pending",
            correlation_confidence=0.93 if is_filled else 0.75,
            order_id=str(deposit_id),
            resolution_method="across_log_decode",
        )

    async def _stargate_lookup(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Resolve a Stargate V1 hop by decoding the Router Swap event.

        Extracts the LayerZero destination chain ID from indexed topic1.
        Stargate has no public REST status API so the destination tx hash and
        address cannot be resolved here — the hop is marked ``completed`` with
        only destination chain populated (confidence 0.70).
        """
        from src.tracing.bridge_log_decoder import decode_bridge_deposit

        decoded = await decode_bridge_deposit("stargate", blockchain, tx_hash)
        if decoded is None:
            return None

        dest_chain: Optional[str] = decoded.get("dest_chain")

        return BridgeCorrelation(
            protocol=protocol.protocol_id,
            mechanism=protocol.mechanism,
            source_chain=blockchain,
            source_tx_hash=tx_hash,
            source_address="",
            source_asset="",
            source_amount=None,
            source_fiat_value=None,
            destination_chain=dest_chain,
            destination_tx_hash=None,
            destination_address=None,
            destination_asset=None,
            destination_amount=None,
            destination_fiat_value=None,
            time_delta_seconds=None,
            # Destination chain is confirmed from event; tx hash unknown without
            # LayerZero scan API — mark completed at reduced confidence.
            status="completed" if dest_chain else "pending",
            correlation_confidence=0.70 if dest_chain else 0.40,
            order_id=str(decoded.get("lz_chain_id", "")),
            resolution_method="stargate_log_decode",
        )

    # ------------------------------------------------------------------
    # Celer cBridge — Send event log decode + POST status API
    # ------------------------------------------------------------------

    async def _celer_lookup(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Resolve a Celer cBridge hop by decoding the Send event.

        Extracts ``transferId`` (indexed bytes32 in topic1) and ``dstChainId``
        (from ABI-encoded log data word 2), then calls the POST status endpoint.
        """
        from src.tracing.bridge_log_decoder import decode_bridge_deposit

        decoded = await decode_bridge_deposit("celer", blockchain, tx_hash)
        if decoded is None:
            return None

        transfer_id: str = decoded["transfer_id"]
        dst_chain_id: int = decoded["dst_chain_id_evm"]
        dest_chain = _evm_chain_id_to_name(dst_chain_id)

        # Celer status API is a POST endpoint
        url = f"{protocol.api_base}/v2/getTransferStatus"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.post(url, json={"transfer_id": transfer_id})
            if resp.status_code == 404:
                # Known transfer_id but not yet indexed — store as pending
                return BridgeCorrelation(
                    protocol=protocol.protocol_id,
                    mechanism=protocol.mechanism,
                    source_chain=blockchain,
                    source_tx_hash=tx_hash,
                    source_address="",
                    source_asset="",
                    source_amount=None,
                    source_fiat_value=None,
                    destination_chain=dest_chain,
                    destination_tx_hash=None,
                    destination_address=None,
                    destination_asset=None,
                    destination_amount=None,
                    destination_fiat_value=None,
                    time_delta_seconds=None,
                    status="pending",
                    correlation_confidence=0.72,
                    order_id=transfer_id,
                    resolution_method="celer_log_decode",
                )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.debug("_celer_lookup POST failed for %s: %s", tx_hash[:16], exc)
            # Return partial result with decoded transfer_id and dest chain
            return BridgeCorrelation(
                protocol=protocol.protocol_id,
                mechanism=protocol.mechanism,
                source_chain=blockchain,
                source_tx_hash=tx_hash,
                source_address="",
                source_asset="",
                source_amount=None,
                source_fiat_value=None,
                destination_chain=dest_chain,
                destination_tx_hash=None,
                destination_address=None,
                destination_asset=None,
                destination_amount=None,
                destination_fiat_value=None,
                time_delta_seconds=None,
                status="pending",
                correlation_confidence=0.72,
                order_id=transfer_id,
                resolution_method="celer_log_decode",
            )

        # Celer transfer statuses: 0=unknown, 1=submitting, 2=failed, 3=waiting_for_sgn_confirmation,
        # 4=waiting_for_fund_release, 5=completed, 6=to_be_refunded, 7=requesting_refund,
        # 8=refund_to_be_confirmed, 9=confirming_your_refund, 10=refunded
        status_code: int = (data.get("status") or {}).get("code") if isinstance(data.get("status"), dict) \
            else int(data.get("status") or 0)
        is_complete = status_code == 5
        dest_tx_hash = data.get("dst_block_tx_link") or data.get("dst_transfer_tx_hash")
        dest_address = data.get("dst_transfer") and (data["dst_transfer"].get("receiver"))

        return BridgeCorrelation(
            protocol=protocol.protocol_id,
            mechanism=protocol.mechanism,
            source_chain=blockchain,
            source_tx_hash=tx_hash,
            source_address="",
            source_asset="",
            source_amount=None,
            source_fiat_value=None,
            destination_chain=dest_chain,
            destination_tx_hash=dest_tx_hash,
            destination_address=dest_address,
            destination_asset=None,
            destination_amount=None,
            destination_fiat_value=None,
            time_delta_seconds=None,
            status="completed" if is_complete else "pending",
            correlation_confidence=0.90 if is_complete else 0.72,
            order_id=transfer_id,
            resolution_method="celer_log_decode",
        )

    # ------------------------------------------------------------------
    # Rango — tx-hash-based API query (no log decode required)
    # ------------------------------------------------------------------

    async def _rango_lookup(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Resolve a Rango Exchange hop using their txId-based status endpoint.

        Rango's ``/basic/status`` supports a ``txId`` query parameter that
        accepts the source transaction hash without requiring a pre-fetched
        ``requestId``.  An API key is recommended but the endpoint is reachable
        without one for basic status lookups.
        """
        url = f"{protocol.api_base}/basic/status?txId={tx_hash}"

        data = await self._make_http_request(url)
        if data is None:
            return None

        rango_status = (data.get("status") or "").upper()
        is_complete = rango_status in ("SUCCESS",)

        dest_chain = (data.get("output") or {}).get("blockchainType", "").lower() or None
        dest_address = (data.get("output") or {}).get("address")
        dest_asset = (data.get("output") or {}).get("token", {}).get("symbol") if data.get("output") else None
        request_id = data.get("requestId")

        return BridgeCorrelation(
            protocol=protocol.protocol_id,
            mechanism=protocol.mechanism,
            source_chain=blockchain,
            source_tx_hash=tx_hash,
            source_address="",
            source_asset=(data.get("input") or {}).get("token", {}).get("symbol", "") if data.get("input") else "",
            source_amount=_safe_float((data.get("input") or {}).get("amount")),
            source_fiat_value=None,
            destination_chain=dest_chain,
            destination_tx_hash=None,
            destination_address=dest_address,
            destination_asset=dest_asset,
            destination_amount=_safe_float((data.get("output") or {}).get("amount")),
            destination_fiat_value=None,
            time_delta_seconds=None,
            status="completed" if is_complete else "pending",
            correlation_confidence=0.85 if is_complete else 0.55,
            order_id=request_id,
            resolution_method="rango_api",
        )

    # ------------------------------------------------------------------
    # Relay Bridge — tx-hash-based API query (no log decode required)
    # ------------------------------------------------------------------

    async def _relay_lookup(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Resolve a Relay Bridge hop by searching requests by origin tx hash.

        Relay's API supports ``GET /requests?originChainId={id}&originTxHash={hash}``
        which returns matching requests without requiring the internal requestId.
        """
        from src.tracing.bridge_log_decoder import CHAIN_TO_EVM_ID

        origin_chain_id = CHAIN_TO_EVM_ID.get(blockchain)
        if not origin_chain_id:
            return None

        url = (
            f"{protocol.api_base}/requests"
            f"?originChainId={origin_chain_id}&originTxHash={tx_hash}"
        )
        data = await self._make_http_request(url)
        if data is None:
            return None

        # Relay returns a list or a dict with a requests array
        requests = data if isinstance(data, list) else (data.get("requests") or [])
        if not requests:
            return None

        req = requests[0]
        request_id = req.get("id")
        relay_status = (req.get("status") or "").lower()
        is_complete = relay_status in ("success", "fulfilled")

        dest_chain_id = req.get("destinationChainId")
        dest_chain = _evm_chain_id_to_name(dest_chain_id)
        dest_address = req.get("recipient")
        dest_tx_hash = req.get("destinationTxHash") or req.get("fillTxHash")
        dest_asset = (req.get("currency") or {}).get("symbol") if req.get("currency") else None
        src_amount = _safe_float(req.get("requestedAmount") or req.get("value"))
        dest_amount = _safe_float(req.get("fillAmount"))

        return BridgeCorrelation(
            protocol=protocol.protocol_id,
            mechanism=protocol.mechanism,
            source_chain=blockchain,
            source_tx_hash=tx_hash,
            source_address=req.get("from", ""),
            source_asset="",
            source_amount=src_amount,
            source_fiat_value=None,
            destination_chain=dest_chain,
            destination_tx_hash=dest_tx_hash,
            destination_address=dest_address,
            destination_asset=dest_asset,
            destination_amount=dest_amount,
            destination_fiat_value=None,
            time_delta_seconds=None,
            status="completed" if is_complete else "pending",
            correlation_confidence=0.88 if is_complete else 0.60,
            order_id=request_id,
            resolution_method="relay_api",
        )

    # ------------------------------------------------------------------
    # Chainflip — vault event log decode
    # ------------------------------------------------------------------

    async def _chainflip_lookup(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Resolve a Chainflip hop by decoding the Vault SwapNative/SwapToken event.

        Extracts ``dstChain`` (Chainflip internal chain ID) from indexed topic1.
        The broker status endpoint requires a ``swap_id`` (channel/deposit ID)
        which is not emitted as an EVM event topic and is not currently derivable
        from on-chain data alone, so the hop is marked pending with dest chain
        populated from the decoded vault event.
        """
        from src.tracing.bridge_log_decoder import decode_bridge_deposit

        decoded = await decode_bridge_deposit("chainflip", blockchain, tx_hash)
        if decoded is None:
            return None

        dest_chain: Optional[str] = decoded.get("dest_chain")

        return BridgeCorrelation(
            protocol=protocol.protocol_id,
            mechanism=protocol.mechanism,
            source_chain=blockchain,
            source_tx_hash=tx_hash,
            source_address="",
            source_asset="",
            source_amount=None,
            source_fiat_value=None,
            destination_chain=dest_chain,
            destination_tx_hash=None,
            destination_address=None,
            destination_asset=None,
            destination_amount=None,
            destination_fiat_value=None,
            time_delta_seconds=None,
            # Dest chain is confirmed from vault event; swap_id needed for
            # full resolution via broker API — leave as pending.
            status="pending",
            correlation_confidence=0.65 if dest_chain else 0.35,
            order_id=None,
            resolution_method="chainflip_log_decode",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _thorchain_asset(raw: str) -> str:
    """Extract ticker from THORChain asset notation e.g. 'ETH.ETH' → 'ETH'."""
    return raw.split(".")[-1].split("-")[0] if raw else ""


def _thorchain_amount(raw) -> Optional[float]:
    """Convert THORChain 1e8-scaled integer string to float, or None."""
    try:
        return int(raw) / 1e8
    except (TypeError, ValueError):
        return None


def _safe_float(value) -> Optional[float]:
    """Convert a value to float, returning None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_EVM_CHAIN_ID_MAP: dict[int, str] = {
    1: "ethereum",
    10: "optimism",
    56: "bsc",
    137: "polygon",
    250: "fantom",
    42161: "arbitrum",
    43114: "avalanche",
    8453: "base",
    59144: "linea",
    1088: "metis",
    5000: "mantle",
    534352: "scroll",
    324: "zksync",
    34443: "mode",
    81457: "blast",
    42220: "celo",
    1284: "moonbeam",
    1285: "moonriver",
    1313161554: "aurora",
}


def _evm_chain_id_to_name(chain_id) -> Optional[str]:
    """Convert a numeric EVM chain ID to our internal chain name."""
    if chain_id is None:
        return None
    try:
        return _EVM_CHAIN_ID_MAP.get(int(chain_id))
    except (TypeError, ValueError):
        return None


_ALLBRIDGE_CHAIN_MAP: dict[str, str] = {
    "ethereum": "ETH",
    "bsc": "BSC",
    "solana": "SOL",
    "polygon": "POL",
    "avalanche": "AVA",
    "arbitrum": "ARB",
    "optimism": "OPT",
    "base": "BAS",
    "celo": "CEL",
    "tron": "TRX",
}

_ALLBRIDGE_REVERSE: dict[str, str] = {v: k for k, v in _ALLBRIDGE_CHAIN_MAP.items()}


def _to_allbridge_chain(chain: str) -> str:
    return _ALLBRIDGE_CHAIN_MAP.get(chain.lower(), chain.upper())


def _from_allbridge_chain(raw: str) -> Optional[str]:
    return _ALLBRIDGE_REVERSE.get(raw.upper())
