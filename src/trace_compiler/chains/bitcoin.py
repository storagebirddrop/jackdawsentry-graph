"""
UTXOChainCompiler — trace compiler for Bitcoin and Bitcoin-derived chains.

UTXO chain tracing semantics (from tasks/memory.md Section 3 / ADR-001):
- Each UTXO output is a discrete spendable coin; taint analysis must track
  individual outputs, not address-level aggregates.
- CoinJoin detection: if ``is_coinjoin=True`` on a transaction, expansion
  MUST return a CoinJoin halt node rather than propagating taint.  The
  frontend displays this as an analysis boundary and must not silently
  continue.
- Probable change output detection: a single-recipient, low-value second
  output is flagged ``is_probable_change=True`` and rendered with a dashed
  border in the frontend.
- Multi-input aggregation: when N inputs fund one transaction, all N input
  addresses are "co-spenders" and are a co-spend clustering signal.

Data sources:
1. PostgreSQL event store (``raw_utxo_inputs`` / ``raw_utxo_outputs``).
2. Neo4j bipartite fallback (``Address-[:SENT]->Transaction-[:RECEIVED]->Address``).

Phase 4: expand_next / expand_prev implemented with CoinJoin halt semantics.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from src.trace_compiler.chains.base import BaseChainCompiler
from src.trace_compiler.lineage import edge_id as mk_edge_id
from src.trace_compiler.lineage import lineage_id as mk_lineage
from src.trace_compiler.lineage import node_id as mk_node_id
from src.trace_compiler.lineage import path_id as mk_path
from src.trace_compiler.models import ActivitySummary
from src.trace_compiler.models import AddressNodeData
from src.trace_compiler.models import BtcSidechainPegData
from src.trace_compiler.models import ExpandOptions
from src.trace_compiler.models import InvestigationEdge
from src.trace_compiler.models import InvestigationNode
from src.trace_compiler.models import LightningChannelOpenData
from src.api.config import settings

logger = logging.getLogger(__name__)

_BITCOIN_CHAINS = {"bitcoin", "litecoin", "bitcoin_cash", "dogecoin"}
_SQL_FETCH_LIMIT = 500
_BITCOIN_SIDECHAIN_ASSETS = {
    "liquid": "L-BTC",
    "rootstock": "RBTC",
    "stacks": "sBTC",
}


class UTXOChainCompiler(BaseChainCompiler):
    """Trace compiler for Bitcoin and UTXO-based chains.

    Handles CoinJoin halt semantics, change output flagging, and co-spend
    aggregation in accordance with UTXO tracing rules in tasks/memory.md.

    Args:
        postgres_pool: asyncpg pool for event store reads.
        neo4j_driver:  Neo4j driver for fallback queries.
        redis_client:  Redis client (reserved for future co-spend cache).
    """

    @property
    def supported_chains(self) -> List[str]:
        """Return the Bitcoin-family chain names handled by this compiler."""
        return list(_BITCOIN_CHAINS)

    def __init__(self, postgres_pool=None, neo4j_driver=None, redis_client=None):
        super().__init__(postgres_pool=postgres_pool, neo4j_driver=neo4j_driver, redis_client=redis_client)
        self._sidechain_peg_hints = _load_sidechain_peg_hints()

    async def expand_next(
        self,
        session_id: str,
        branch_id: str,
        path_sequence: int,
        depth: int,
        seed_address: str,
        chain: str,
        options: ExpandOptions,
    ) -> Tuple[List[InvestigationNode], List[InvestigationEdge]]:
        """Follow funds forward: find transactions funded by ``seed_address``.

        Looks up UTXO outputs of transactions where ``seed_address`` appears
        as an input.  CoinJoin transactions produce a single halt node rather
        than expanding counterparty addresses.

        Args:
            session_id:       Investigation session UUID.
            branch_id:        Branch ID for lineage assignment.
            path_sequence:    Integer index for path_id generation.
            depth:            Current hop depth from the session root.
            seed_address:     Bitcoin address (Base58, case-sensitive).
            chain:            Chain name (e.g. ``"bitcoin"``).
            options:          Expansion options.

        Returns:
            Tuple of (nodes, edges).
        """
        rows = await self._fetch_outbound_event_store(seed_address, chain, options)
        if not rows:
            rows = await self._fetch_outbound_neo4j(seed_address, chain, options)
        lightning_channel_opens = await self._fetch_lightning_channel_open_events(
            rows, chain=chain, direction="forward"
        )
        sidechain_peg_events = self._match_sidechain_peg_events(
            rows, chain=chain, direction="forward"
        )

        return self._build_graph(
            rows=rows,
            session_id=session_id,
            branch_id=branch_id,
            path_sequence=path_sequence,
            depth=depth,
            seed_address=seed_address,
            chain=chain,
            direction="forward",
            options=options,
            lightning_channel_opens=lightning_channel_opens,
            sidechain_peg_events=sidechain_peg_events,
        )

    async def expand_prev(
        self,
        session_id: str,
        branch_id: str,
        path_sequence: int,
        depth: int,
        seed_address: str,
        chain: str,
        options: ExpandOptions,
    ) -> Tuple[List[InvestigationNode], List[InvestigationEdge]]:
        """Follow funds backward: find transactions that sent to ``seed_address``.

        Looks up UTXO outputs where the output address is ``seed_address``.

        Args: same as expand_next.

        Returns:
            Tuple of (nodes, edges).
        """
        rows = await self._fetch_inbound_event_store(seed_address, chain, options)
        if not rows:
            rows = await self._fetch_inbound_neo4j(seed_address, chain, options)
        lightning_channel_opens = await self._fetch_lightning_channel_open_events(
            rows, chain=chain, direction="backward"
        )
        sidechain_peg_events = self._match_sidechain_peg_events(
            rows, chain=chain, direction="backward"
        )

        return self._build_graph(
            rows=rows,
            session_id=session_id,
            branch_id=branch_id,
            path_sequence=path_sequence,
            depth=depth,
            seed_address=seed_address,
            chain=chain,
            direction="backward",
            options=options,
            lightning_channel_opens=lightning_channel_opens,
            sidechain_peg_events=sidechain_peg_events,
        )

    # ------------------------------------------------------------------
    # Event store queries
    # ------------------------------------------------------------------

    async def _fetch_outbound_event_store(
        self, address: str, chain: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Find transactions funded by address, return their outputs.

        Query pattern:
        1. Find all tx_hashes in raw_utxo_inputs where input address = seed.
        2. For each tx_hash, fetch all raw_utxo_outputs (counterparties).
        3. Tag each row with is_coinjoin (join: raw_transactions.is_coinjoin).
        """
        if self._pg is None:
            return []
        try:
            limit = min(options.max_results, _SQL_FETCH_LIMIT)
            sql = """
                SELECT
                    o.tx_hash,
                    o.address           AS counterparty,
                    o.value_satoshis,
                    o.output_index,
                    o.script_type,
                    o.is_probable_change,
                    o.is_spent,
                    o.timestamp,
                    COALESCE(t.is_coinjoin, FALSE) AS is_coinjoin
                FROM raw_utxo_inputs  i
                JOIN raw_utxo_outputs o ON o.blockchain = i.blockchain
                                      AND o.tx_hash    = i.tx_hash
                LEFT JOIN raw_transactions t ON t.blockchain = i.blockchain
                                           AND t.tx_hash    = i.tx_hash
                WHERE i.blockchain = $1
                  AND i.address    = $2
                  AND o.address IS NOT NULL
                  AND o.address <> $2
                ORDER BY o.timestamp DESC
                LIMIT $3
            """
            async with self._pg.acquire() as conn:
                rows = await conn.fetch(sql, chain, address, limit)
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning(
                "UTXOChainCompiler._fetch_outbound_event_store failed %s/%s: %s",
                chain, address, exc, exc_info=True,
            )
            return []

    async def _fetch_inbound_event_store(
        self, address: str, chain: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Find UTXO outputs sent to address, return their input addresses."""
        if self._pg is None:
            return []
        try:
            limit = min(options.max_results, _SQL_FETCH_LIMIT)
            sql = """
                SELECT
                    i.tx_hash,
                    i.address           AS counterparty,
                    o.value_satoshis,
                    o.output_index,
                    o.script_type,
                    o.is_probable_change,
                    o.is_spent,
                    o.timestamp,
                    COALESCE(t.is_coinjoin, FALSE) AS is_coinjoin
                FROM raw_utxo_outputs o
                JOIN raw_utxo_inputs  i ON i.blockchain = o.blockchain
                                      AND i.tx_hash    = o.tx_hash
                LEFT JOIN raw_transactions t ON t.blockchain = o.blockchain
                                           AND t.tx_hash    = o.tx_hash
                WHERE o.blockchain = $1
                  AND o.address    = $2
                  AND i.address IS NOT NULL
                  AND i.address <> $2
                ORDER BY o.timestamp DESC
                LIMIT $3
            """
            async with self._pg.acquire() as conn:
                rows = await conn.fetch(sql, chain, address, limit)
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning(
                "UTXOChainCompiler._fetch_inbound_event_store failed %s/%s: %s",
                chain, address, exc, exc_info=True,
            )
            return []

    # ------------------------------------------------------------------
    # Neo4j fallback queries
    # ------------------------------------------------------------------

    async def _fetch_outbound_neo4j(
        self, address: str, chain: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Fallback: query Neo4j bipartite graph for outbound UTXO flows."""
        if self._neo4j is None:
            return []
        try:
            limit = min(options.max_results, _SQL_FETCH_LIMIT)
            cypher = """
                MATCH (a:Address {address: $addr, blockchain: $chain})
                      -[:SENT]->(t:Transaction)
                      -[r:RECEIVED]->(tgt:Address)
                WHERE tgt.address <> $addr
                RETURN tgt.address        AS counterparty,
                       t.hash             AS tx_hash,
                       r.value_satoshis   AS value_satoshis,
                       r.output_index     AS output_index,
                       r.script_type      AS script_type,
                       r.is_probable_change AS is_probable_change,
                       NULL              AS is_spent,  -- Neo4j fallback: spent state not tracked
                       t.timestamp        AS timestamp,
                       t.is_coinjoin      AS is_coinjoin
                LIMIT $limit
            """
            async with self._neo4j.session() as session:
                result = await session.run(
                    cypher, addr=address, chain=chain, limit=limit
                )
                return [dict(r) async for r in result]
        except Exception as exc:
            logger.debug("UTXOChainCompiler._fetch_outbound_neo4j failed: %s", exc)
            return []

    async def _fetch_inbound_neo4j(
        self, address: str, chain: str, options: ExpandOptions
    ) -> List[Dict[str, Any]]:
        """Fallback: query Neo4j for inbound UTXO flows."""
        if self._neo4j is None:
            return []
        try:
            limit = min(options.max_results, _SQL_FETCH_LIMIT)
            cypher = """
                MATCH (src:Address)-[:SENT]->(t:Transaction)
                      -[r:RECEIVED]->(a:Address {address: $addr, blockchain: $chain})
                WHERE src.address <> $addr
                RETURN src.address        AS counterparty,
                       t.hash             AS tx_hash,
                       r.value_satoshis   AS value_satoshis,
                       r.output_index     AS output_index,
                       r.script_type      AS script_type,
                       r.is_probable_change AS is_probable_change,
                       NULL              AS is_spent,  -- Neo4j fallback: spent state not tracked
                       t.timestamp        AS timestamp,
                       t.is_coinjoin      AS is_coinjoin
                LIMIT $limit
            """
            async with self._neo4j.session() as session:
                result = await session.run(
                    cypher, addr=address, chain=chain, limit=limit
                )
                return [dict(r) async for r in result]
        except Exception as exc:
            logger.debug("UTXOChainCompiler._fetch_inbound_neo4j failed: %s", exc)
            return []

    async def _fetch_lightning_channel_open_events(
        self,
        rows: List[Dict[str, Any]],
        chain: str,
        direction: str,
    ) -> Dict[str, Dict[str, Any]]:
        """Return Lightning channel-open metadata keyed by ``tx_hash:vout``.

        Lightning channel funding currently rides on Bitcoin funding outputs.
        We only surface it on forward Bitcoin expansions where the seed address
        is spending into a channel open.
        """
        if direction != "forward" or chain != "bitcoin" or self._neo4j is None:
            return {}

        funding_refs: List[str] = []
        for row in rows:
            tx_hash = row.get("tx_hash")
            output_index = row.get("output_index")
            if not tx_hash or output_index is None:
                continue
            try:
                funding_refs.append(f"{tx_hash}:{int(output_index)}")
            except (TypeError, ValueError):
                continue

        if not funding_refs:
            return {}

        cypher = """
            MATCH (c:LightningChannel)-[:FUNDED_BY]->(t:Transaction {blockchain: 'lightning'})
            WHERE t.hash IN $funding_refs
            OPTIONAL MATCH (local:LightningNode)-[rel:CHANNEL {channel_id: c.channel_id}]->(remote:LightningNode)
            RETURN
                t.hash AS funding_ref,
                c.channel_id AS channel_id,
                t.memo AS short_channel_id,
                c.capacity AS capacity_sats,
                c.private AS is_private,
                c.active AS is_active,
                local.pubkey AS local_pubkey,
                local.alias AS local_alias,
                remote.pubkey AS remote_pubkey,
                remote.alias AS remote_alias
        """

        def _safe_bool(value: Any, default: bool = False) -> bool:
            if value is None:
                return default
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in {"1", "true", "yes", "open", "active"}
            return bool(value)

        try:
            async with self._neo4j.session() as session:
                result = await session.run(cypher, funding_refs=funding_refs)
                records = [dict(record) async for record in result]
        except Exception as exc:
            logger.debug(
                "UTXOChainCompiler._fetch_lightning_channel_open_events failed: %s",
                exc,
            )
            return {}

        events: Dict[str, Dict[str, Any]] = {}
        for record in records:
            funding_ref = record.get("funding_ref")
            channel_id = record.get("channel_id")
            if not funding_ref or not channel_id:
                continue
            tx_hash, _, vout = funding_ref.partition(":")
            try:
                funding_vout = int(vout) if vout else None
            except ValueError:
                funding_vout = None

            capacity_sats = record.get("capacity_sats")
            try:
                capacity_btc = float(capacity_sats or 0) / 1e8
            except (TypeError, ValueError):
                capacity_btc = 0.0

            is_active = _safe_bool(record.get("is_active"), default=True)
            is_private = (
                None
                if record.get("is_private") is None
                else _safe_bool(record.get("is_private"))
            )
            local_alias = record.get("local_alias")
            remote_alias = record.get("remote_alias")
            local_pubkey = record.get("local_pubkey")
            remote_pubkey = record.get("remote_pubkey")
            peer_summary = " <-> ".join(
                part
                for part in [local_alias or local_pubkey, remote_alias or remote_pubkey]
                if part
            )

            events[funding_ref] = {
                "channel_id": str(channel_id),
                "funding_ref": funding_ref,
                "funding_tx_hash": tx_hash,
                "funding_vout": funding_vout,
                "short_channel_id": record.get("short_channel_id"),
                "capacity_btc": capacity_btc,
                "local_pubkey": local_pubkey,
                "remote_pubkey": remote_pubkey,
                "local_alias": local_alias,
                "remote_alias": remote_alias,
                "is_private": is_private,
                "status": "open" if is_active else "closed",
                "peer_summary": peer_summary or "Lightning channel",
            }

        return events

    def _match_sidechain_peg_events(
        self,
        rows: List[Dict[str, Any]],
        chain: str,
        direction: str,
    ) -> Dict[str, Dict[str, Any]]:
        """Return peg events keyed by ``tx_hash:vout`` based on configured hints."""
        if chain != "bitcoin":
            return {}

        events: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            counterparty = (row.get("counterparty") or "").strip()
            tx_hash = row.get("tx_hash")
            output_index = row.get("output_index")
            if not counterparty or not tx_hash or output_index is None:
                continue
            try:
                funding_ref = f"{tx_hash}:{int(output_index)}"
            except (TypeError, ValueError):
                continue

            counterparty_key = counterparty.lower()
            value_sats = row.get("value_satoshis")
            try:
                amount_btc = float(value_sats or 0) / 1e8
            except (TypeError, ValueError):
                amount_btc = None

            for sidechain, hint in self._sidechain_peg_hints.items():
                peg_in_addresses = hint.get("peg_in_addresses", set())
                peg_out_addresses = hint.get("peg_out_addresses", set())
                if direction == "forward" and counterparty_key in peg_in_addresses:
                    events[funding_ref] = {
                        "direction": "peg_in",
                        "sidechain": sidechain,
                        "bitcoin_tx_hash": tx_hash,
                        "funding_vout": int(output_index),
                        "peg_address_or_contract": counterparty,
                        "asset_in": "BTC",
                        "asset_out": hint.get("asset_out", _BITCOIN_SIDECHAIN_ASSETS.get(sidechain, sidechain.upper())),
                        "amount_btc": amount_btc,
                        "mechanism": hint.get("mechanism", "bridge"),
                        "confidence": hint.get("confidence", 0.75),
                        "status": "observed",
                    }
                    break
                if direction == "backward" and counterparty_key in peg_out_addresses:
                    events[funding_ref] = {
                        "direction": "peg_out",
                        "sidechain": sidechain,
                        "bitcoin_tx_hash": tx_hash,
                        "funding_vout": int(output_index),
                        "peg_address_or_contract": counterparty,
                        "asset_in": hint.get("asset_out", _BITCOIN_SIDECHAIN_ASSETS.get(sidechain, sidechain.upper())),
                        "asset_out": "BTC",
                        "amount_btc": amount_btc,
                        "mechanism": hint.get("mechanism", "bridge"),
                        "confidence": hint.get("confidence", 0.75),
                        "status": "observed",
                    }
                    break

        return events

    # ------------------------------------------------------------------
    # Node / edge construction with CoinJoin halt semantics
    # ------------------------------------------------------------------

    def _build_graph(
        self,
        rows: List[Dict[str, Any]],
        session_id: str,
        branch_id: str,
        path_sequence: int,
        depth: int,
        seed_address: str,
        chain: str,
        direction: str,
        options: ExpandOptions,
        lightning_channel_opens: Optional[Dict[str, Dict[str, Any]]] = None,
        sidechain_peg_events: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Tuple[List[InvestigationNode], List[InvestigationEdge]]:
        """Convert UTXO transfer rows into InvestigationNodes and edges.

        **CoinJoin halt rule** (tasks/memory.md Section 3): if any row has
        ``is_coinjoin=True``, the entire expansion returns a single opaque
        CoinJoin halt node marked ``is_coinjoin_halt=True``.  Taint analysis
        must not propagate through this node.  The frontend renders it as an
        analysis boundary with a warning indicator.

        Args:
            rows:           List of dicts from event store or Neo4j fallback.
            session_id:     Investigation session UUID.
            branch_id:      Branch ID for lineage assignment.
            path_sequence:  Integer index for path_id generation.
            depth:          Current hop depth from session root.
            seed_address:   Bitcoin address being expanded.
            chain:          Chain name.
            direction:      ``"forward"`` or ``"backward"``.
            options:        Expansion options.

        Returns:
            Tuple of (nodes, edges).
        """
        if not rows:
            return [], []

        _path = mk_path(branch_id, path_sequence)
        seed_node_id = mk_node_id(chain, "address", seed_address)

        # CoinJoin halt: any CoinJoin transaction terminates the expansion.
        coinjoin_txs = {r["tx_hash"] for r in rows if r.get("is_coinjoin")}
        if coinjoin_txs:
            _lineage = mk_lineage(session_id, branch_id, _path, depth + 1)
            halt_node_id = mk_node_id(chain, "address", f"coinjoin_halt:{seed_address}")
            halt_node = InvestigationNode(
                node_id=halt_node_id,
                lineage_id=_lineage,
                node_type="address",
                branch_id=branch_id,
                path_id=_path,
                depth=depth + 1,
                display_label="CoinJoin (taint boundary)",
                display_sublabel="Expansion halted — CoinJoin transaction detected",
                chain=chain,
                expandable_directions=[],  # not expandable
                is_highlighted=True,
                address_data=AddressNodeData(
                    address=seed_address,
                    address_type="utxo_p2pkh",
                    is_coinjoin_halt=True,
                ),
            )
            halt_edge = InvestigationEdge(
                edge_id=mk_edge_id(seed_node_id, halt_node_id, branch_id, None),
                source_node_id=seed_node_id if direction == "forward" else halt_node_id,
                target_node_id=halt_node_id if direction == "forward" else seed_node_id,
                branch_id=branch_id,
                path_id=_path,
                edge_type="transfer",
                direction=direction,
            )
            return [halt_node], [halt_edge]

        # Normal expansion: build one node per unique counterparty address.
        seen_nodes: Dict[str, InvestigationNode] = {}
        edges: List[InvestigationEdge] = []
        lightning_channel_opens = lightning_channel_opens or {}
        sidechain_peg_events = sidechain_peg_events or {}

        for row in rows:
            counterparty = row.get("counterparty") or ""
            if not counterparty or counterparty == seed_address:
                continue

            tx_hash = row.get("tx_hash") or ""
            output_index = row.get("output_index")
            funding_ref = None
            if tx_hash and output_index is not None:
                try:
                    funding_ref = f"{tx_hash}:{int(output_index)}"
                except (TypeError, ValueError):
                    funding_ref = None
            channel_open = (
                lightning_channel_opens.get(funding_ref)
                if direction == "forward" and funding_ref
                else None
            )
            sidechain_peg = sidechain_peg_events.get(funding_ref) if funding_ref else None
            value_sats: Optional[int] = row.get("value_satoshis")
            value_btc: Optional[float] = (
                value_sats / 1e8 if value_sats is not None else None
            )
            script_type: str = row.get("script_type") or "unknown"
            is_probable_change: bool = bool(row.get("is_probable_change"))
            node_key = (
                f"lightning_channel_open:{funding_ref}"
                if channel_open and funding_ref
                else (
                    f"btc_sidechain_peg:{sidechain_peg['direction']}:{funding_ref}"
                    if sidechain_peg and funding_ref
                    else counterparty
                )
            )

            if node_key not in seen_nodes:
                _lineage = mk_lineage(session_id, branch_id, _path, depth + 1)
                if channel_open and funding_ref:
                    peer_summary = channel_open.get("peer_summary") or "Lightning channel"
                    display_sublabel = (
                        f"{channel_open['capacity_btc']:.6f} BTC"
                        if channel_open.get("capacity_btc") is not None
                        else None
                    )
                    node = InvestigationNode(
                        node_id=mk_node_id(
                            "lightning", "lightning_channel_open", funding_ref
                        ),
                        lineage_id=_lineage,
                        node_type="lightning_channel_open",
                        branch_id=branch_id,
                        path_id=_path,
                        depth=depth + 1,
                        display_label="Channel Open",
                        display_sublabel=display_sublabel,
                        chain="lightning",
                        expandable_directions=[],
                        lightning_channel_open_data=LightningChannelOpenData(
                            channel_id=channel_open["channel_id"],
                            funding_tx_hash=channel_open["funding_tx_hash"],
                            funding_vout=channel_open.get("funding_vout"),
                            short_channel_id=channel_open.get("short_channel_id"),
                            capacity_btc=float(channel_open.get("capacity_btc") or 0.0),
                            local_pubkey=channel_open.get("local_pubkey"),
                            remote_pubkey=channel_open.get("remote_pubkey"),
                            local_alias=channel_open.get("local_alias"),
                            remote_alias=channel_open.get("remote_alias"),
                            is_private=channel_open.get("is_private"),
                            status=channel_open.get("status") or "open",
                        ),
                        activity_summary=ActivitySummary(
                            activity_type="lightning_channel_open",
                            title="Lightning channel open",
                            protocol_id="lightning",
                            protocol_type="channel_open",
                            tx_hash=funding_ref,
                            tx_chain="bitcoin",
                            status=channel_open.get("status") or "open",
                            source_chain="bitcoin",
                            destination_chain="lightning",
                            source_tx_hash=channel_open.get("funding_tx_hash"),
                            asset_symbol="BTC",
                            canonical_asset_id="btc",
                            value_native=float(channel_open.get("capacity_btc") or 0.0),
                            route_summary=peer_summary,
                        ),
                    )
                elif sidechain_peg and funding_ref:
                    direction_label = "btc_sidechain_peg_in" if sidechain_peg["direction"] == "peg_in" else "btc_sidechain_peg_out"
                    route_summary = f"{sidechain_peg['asset_in']} -> {sidechain_peg['asset_out']}"
                    sidechain = sidechain_peg["sidechain"]
                    node = InvestigationNode(
                        node_id=mk_node_id(
                            "bitcoin",
                            direction_label,
                            f"{sidechain}:{funding_ref}",
                        ),
                        lineage_id=_lineage,
                        node_type=direction_label,
                        branch_id=branch_id,
                        path_id=_path,
                        depth=depth + 1,
                        display_label="Peg In" if sidechain_peg["direction"] == "peg_in" else "Peg Out",
                        display_sublabel=route_summary,
                        chain="bitcoin",
                        expandable_directions=[],
                        btc_sidechain_peg_data=BtcSidechainPegData(
                            sidechain=sidechain,
                            bitcoin_tx_hash=sidechain_peg["bitcoin_tx_hash"],
                            peg_address_or_contract=sidechain_peg.get("peg_address_or_contract"),
                            asset_in=sidechain_peg["asset_in"],
                            asset_out=sidechain_peg["asset_out"],
                            amount_btc=sidechain_peg.get("amount_btc"),
                            mechanism=sidechain_peg.get("mechanism", "bridge"),
                            confidence=float(sidechain_peg.get("confidence") or 0.0),
                            status=sidechain_peg.get("status", "observed"),
                        ),
                        activity_summary=ActivitySummary(
                            activity_type=direction_label,
                            title=f"{sidechain.title()} peg {'in' if sidechain_peg['direction'] == 'peg_in' else 'out'}",
                            protocol_id=sidechain,
                            protocol_type="sidechain_peg",
                            tx_hash=funding_ref,
                            tx_chain="bitcoin",
                            status=sidechain_peg.get("status", "observed"),
                            source_chain="bitcoin" if sidechain_peg["direction"] == "peg_in" else sidechain,
                            destination_chain=sidechain if sidechain_peg["direction"] == "peg_in" else "bitcoin",
                            source_tx_hash=sidechain_peg["bitcoin_tx_hash"],
                            asset_symbol="BTC",
                            canonical_asset_id="btc",
                            value_native=sidechain_peg.get("amount_btc"),
                            source_asset=sidechain_peg["asset_in"],
                            destination_asset=sidechain_peg["asset_out"],
                            source_amount=sidechain_peg.get("amount_btc"),
                            route_summary=route_summary,
                        ),
                    )
                else:
                    _cp_node_id = mk_node_id(chain, "address", counterparty)
                    node = InvestigationNode(
                        node_id=_cp_node_id,
                        lineage_id=_lineage,
                        node_type="address",
                        branch_id=branch_id,
                        path_id=_path,
                        depth=depth + 1,
                        display_label=counterparty[:12] + "…" if len(counterparty) > 12 else counterparty,
                        chain=chain,
                        expandable_directions=["prev", "next", "neighbors"],
                        address_data=AddressNodeData(
                            address=counterparty,
                            address_type=_script_type_to_address_type(script_type),
                        ),
                    )
                seen_nodes[node_key] = node

            if direction == "forward":
                src_node_id = seed_node_id
                tgt_node_id = seen_nodes[node_key].node_id
            else:
                src_node_id = seen_nodes[node_key].node_id
                tgt_node_id = seed_node_id

            raw_ts = row.get("timestamp")
            _ts_str: Optional[str] = None
            if isinstance(raw_ts, datetime):
                _ts_str = raw_ts.isoformat()
            elif isinstance(raw_ts, str):
                _ts_str = raw_ts

            # Get chain-specific symbol and canonical ID
            chain_symbol, chain_canonical_id = _get_chain_symbol_and_canonical_id(chain)
            
            edge = InvestigationEdge(
                edge_id=mk_edge_id(src_node_id, tgt_node_id, branch_id, tx_hash),
                source_node_id=src_node_id,
                target_node_id=tgt_node_id,
                branch_id=branch_id,
                path_id=_path,
                edge_type="transfer",
                value_native=value_btc,
                asset_symbol=chain_symbol,
                canonical_asset_id=chain_canonical_id,
                tx_hash=tx_hash or None,
                tx_chain=chain,
                timestamp=_ts_str,
                is_suspected_change=is_probable_change,
                direction=direction,
            )
            edges.append(edge)

        nodes = list(seen_nodes.values())[: options.max_results]
        edges = edges[: options.max_results * 3]
        return nodes, edges


def _script_type_to_address_type(script_type: str) -> str:
    """Map a UTXO script type string to an InvestigationNode address_type."""
    _MAP = {
        "p2pkh": "utxo_p2pkh",
        "p2sh": "utxo_p2sh",
        "p2wpkh": "utxo_p2wpkh",
        "p2tr": "utxo_p2tr",
        "op_return": "utxo_op_return",
    }
    return _MAP.get(script_type.lower(), "utxo_p2pkh")


def _get_chain_symbol_and_canonical_id(chain: str) -> tuple[str, str]:
    """Get symbol and canonical asset ID for a Bitcoin-derived chain."""
    _CHAIN_MAP = {
        "bitcoin": ("BTC", "btc"),
        "bitcoin_cash": ("BCH", "bch"), 
        "litecoin": ("LTC", "ltc"),
        "dogecoin": ("DOGE", "doge"),
    }
    return _CHAIN_MAP.get(chain.lower(), ("BTC", "btc"))


def _load_sidechain_peg_hints() -> Dict[str, Dict[str, Any]]:
    """Load peg detection hints from settings.

    The JSON format is intentionally simple so deployments can add known peg
    addresses without changing code, for example:

        {
          "liquid": {
            "peg_in_addresses": ["..."],
            "peg_out_addresses": ["..."],
            "asset_out": "L-BTC",
            "mechanism": "federated",
            "confidence": 0.9
          }
        }
    """
    raw = settings.BITCOIN_SIDECHAIN_PEG_HINTS_JSON
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        logger.warning("Invalid BITCOIN_SIDECHAIN_PEG_HINTS_JSON: %s", exc)
        return {}

    hints: Dict[str, Dict[str, Any]] = {}
    if not isinstance(parsed, dict):
        return hints

    for sidechain, config in parsed.items():
        if not isinstance(config, dict):
            continue
        peg_in_addresses = {
            str(addr).strip().lower()
            for addr in (config.get("peg_in_addresses") or [])
            if str(addr).strip()
        }
        peg_out_addresses = {
            str(addr).strip().lower()
            for addr in (config.get("peg_out_addresses") or [])
            if str(addr).strip()
        }
        hints[str(sidechain).strip().lower()] = {
            "peg_in_addresses": peg_in_addresses,
            "peg_out_addresses": peg_out_addresses,
            "asset_out": config.get("asset_out") or _BITCOIN_SIDECHAIN_ASSETS.get(str(sidechain).strip().lower(), str(sidechain).upper()),
            "mechanism": config.get("mechanism") or "bridge",
            "confidence": float(config.get("confidence") or 0.75),
        }

    return hints
