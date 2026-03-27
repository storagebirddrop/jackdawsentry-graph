"""
Address exposure enrichment for trace-compiler address nodes.

This module assigns high-signal semantic context to plain address nodes when
their own on-chain activity directly interacts with known high-risk services.
The current implementation focuses on direct mixer exposure so investigators
can see addresses associated with Tornado Cash-style flows without needing a
separate private attribution database.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from typing import Dict
from typing import Optional

from src.trace_compiler.models import AddressNodeData
from src.trace_compiler.models import InvestigationNode
from src.trace_compiler.services.service_classifier import ServiceClassifier

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60 * 60
_MIXER_RISK_SCORE = 0.9

_DIRECT_SERVICE_EXPOSURE_SQL = """
WITH counterparties AS (
    SELECT lower(to_address) AS counterparty
    FROM raw_transactions
    WHERE blockchain = $1
      AND from_address = $2
      AND to_address IS NOT NULL

    UNION ALL

    SELECT lower(from_address) AS counterparty
    FROM raw_transactions
    WHERE blockchain = $1
      AND to_address = $2
      AND from_address IS NOT NULL

    UNION ALL

    SELECT lower(to_address) AS counterparty
    FROM raw_token_transfers
    WHERE blockchain = $1
      AND from_address = $2
      AND to_address IS NOT NULL

    UNION ALL

    SELECT lower(from_address) AS counterparty
    FROM raw_token_transfers
    WHERE blockchain = $1
      AND to_address = $2
      AND from_address IS NOT NULL
)
SELECT counterparty, COUNT(*)::int AS interaction_count
FROM counterparties
WHERE counterparty IS NOT NULL
GROUP BY counterparty
ORDER BY interaction_count DESC
LIMIT 128
"""


class AddressExposureEnricher:
    """Annotate address nodes using direct counterparty exposure heuristics."""

    def __init__(
        self,
        postgres_pool=None,
        redis_client=None,
        service_classifier: Optional[ServiceClassifier] = None,
    ) -> None:
        self._pg = postgres_pool
        self._redis = redis_client
        self._service = service_classifier or ServiceClassifier(postgres_pool=postgres_pool)

    @staticmethod
    def _cache_key(chain: str, address: str) -> str:
        return f"tc:address_exposure:{chain.lower()}:{address.lower()}"

    async def lookup_exposure(
        self,
        address: str,
        chain: str,
    ) -> Optional[Dict[str, Any]]:
        """Return best-effort exposure metadata for an address."""
        if self._pg is None or not address or not chain:
            return None

        addr = address.lower().strip()
        chain_lc = chain.lower().strip()
        if not addr:
            return None

        cached = await self._read_cache(chain_lc, addr)
        if cached is not None:
            return cached

        exposure: Optional[Dict[str, Any]] = None
        try:
            async with self._pg.acquire() as conn:
                rows = await conn.fetch(_DIRECT_SERVICE_EXPOSURE_SQL, chain_lc, addr)
        except Exception as exc:
            logger.debug(
                "AddressExposureEnricher lookup failed for %s on %s: %s",
                addr,
                chain_lc,
                exc,
            )
            return None

        for row in rows:
            counterparty = row["counterparty"]
            record = self._service.get_record(chain_lc, counterparty)
            if record is None or record.service_type != "mixer":
                continue

            interaction_count = int(row["interaction_count"] or 0)
            exposure = {
                "entity_name": f"{record.display_name}-associated address",
                "entity_type": "mixer",
                "entity_category": "mixer",
                "risk_score": _MIXER_RISK_SCORE,
                "is_mixer": True,
                "matched_contract": counterparty,
                "matched_protocol_id": record.protocol_id,
                "matched_protocol_name": record.display_name,
                "interaction_count": interaction_count,
                "risk_factors": [
                    (
                        f"Direct interaction with {record.display_name} mixer "
                        f"contracts ({interaction_count} observed event"
                        f"{'' if interaction_count == 1 else 's'})"
                    )
                ],
                "label": f"{record.display_name} exposure",
            }
            break

        await self._write_cache(chain_lc, addr, exposure)
        return exposure

    async def enrich_address_node(self, node: InvestigationNode) -> InvestigationNode:
        """Attach direct-exposure metadata to an address node when available."""
        if node.node_type != "address":
            return node

        address_data = node.address_data
        address = address_data.address if address_data is not None else None
        if not address:
            return node

        exposure = await self.lookup_exposure(address, node.chain)
        if exposure is None:
            return node

        existing_risk = (
            node.risk_score
            if node.risk_score is not None
            else address_data.risk_score if address_data is not None else None
        )
        merged_risk = max(existing_risk or 0.0, float(exposure["risk_score"]))

        merged_factors = list(dict.fromkeys([*node.risk_factors, *exposure["risk_factors"]]))

        updated_address_data = (
            address_data.model_copy(
                update={
                    "chain": address_data.chain or node.chain,
                    "entity_name": address_data.entity_name or exposure["entity_name"],
                    "entity_category": address_data.entity_category or exposure["entity_category"],
                    "risk_score": max(address_data.risk_score or 0.0, merged_risk),
                    "is_mixer": bool(address_data.is_mixer) or bool(exposure["is_mixer"]),
                    "label": address_data.label or exposure["label"],
                }
            )
            if address_data is not None
            else AddressNodeData(
                address=address,
                chain=node.chain,
                address_type="unknown",
                entity_name=exposure["entity_name"],
                entity_category=exposure["entity_category"],
                risk_score=merged_risk,
                is_mixer=True,
                label=exposure["label"],
            )
        )

        return node.model_copy(
            update={
                "entity_name": node.entity_name or exposure["entity_name"],
                "entity_type": node.entity_type or exposure["entity_type"],
                "entity_category": node.entity_category or exposure["entity_category"],
                "risk_score": merged_risk,
                "risk_factors": merged_factors,
                "address_data": updated_address_data,
            }
        )

    async def _read_cache(
        self,
        chain: str,
        address: str,
    ) -> Optional[Optional[Dict[str, Any]]]:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(self._cache_key(chain, address))
        except Exception as exc:
            logger.debug("AddressExposureEnricher cache read failed: %s", exc)
            return None

        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except Exception as exc:
            logger.debug("AddressExposureEnricher cache decode failed: %s", exc)
            return None
        return payload or None

    async def _write_cache(
        self,
        chain: str,
        address: str,
        payload: Optional[Dict[str, Any]],
    ) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.setex(
                self._cache_key(chain, address),
                _CACHE_TTL_SECONDS,
                json.dumps(payload or {}),
            )
        except Exception as exc:
            logger.debug("AddressExposureEnricher cache write failed: %s", exc)
