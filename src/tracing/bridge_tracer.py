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
        """Resolve native_amm hops. Only THORChain supports direct tx_hash lookup."""
        if protocol.protocol_id == "thorchain":
            return await self._thorchain_lookup(protocol, tx_hash, blockchain)
        # Chainflip requires a swap_id extracted from tx calldata; skip for now.
        logger.debug(
            "_resolve_native_amm: %s requires intermediate ID — returning None",
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

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url)

        if resp.status_code == 404:
            logger.debug("THORChain tx %s not found", tx_hash[:16])
            return None
        resp.raise_for_status()
        data = resp.json()

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

        elapsed = int(time.monotonic() - t0)

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
        """Resolve burn_release hops. Only Synapse supports direct tx_hash lookup."""
        if protocol.protocol_id == "synapse":
            return await self._synapse_lookup(protocol, tx_hash, blockchain)
        # Celer needs transfer_id from decoded event logs.
        logger.debug(
            "_resolve_burn_release: %s requires decoded event logs — returning None",
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
        """Dispatch solver-mechanism protocols to their specific resolvers."""
        resolvers = {
            "lifi": self._lifi_lookup,
            "squid": self._squid_lookup,
            "mayan": self._mayan_lookup,
            "debridge": self._debridge_lookup,
            "symbiosis": self._symbiosis_lookup,
        }
        resolver = resolvers.get(protocol.protocol_id)
        if resolver is None:
            logger.debug(
                "_resolve_solver: %s requires intermediate request ID — returning None",
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
    # liquidity — Stargate (no public status API), Across (needs depositId)
    # ------------------------------------------------------------------

    async def _resolve_liquidity(
        self,
        protocol: BridgeProtocol,
        tx_hash: str,
        blockchain: str,
    ) -> Optional[BridgeCorrelation]:
        """Liquidity-pool bridges require IDs extracted from event logs.

        Neither Stargate nor Across exposes a public tx_hash → status API
        that does not require the depositId / send-token ID from decoded logs.
        Return None until a log-decoding layer is in place.
        """
        logger.debug(
            "_resolve_liquidity: %s requires decoded event logs — returning None",
            protocol.protocol_id,
        )
        return None


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
