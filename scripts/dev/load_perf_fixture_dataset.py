#!/usr/bin/env python3
"""Load a deterministic high-degree graph fixture into the local graph stack."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List

import asyncpg
from neo4j import GraphDatabase


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

FIXTURE_CHAIN = "ethereum"
FIXTURE_SEED_ADDRESS = "0xfeed00000000000000000000000000000000cafe"
OUTBOUND_NATIVE_COUNT = 80
INBOUND_NATIVE_COUNT = 80
SECOND_HOP_NATIVE_COUNT = 40
OUTBOUND_TOKEN_COUNT = 30
INBOUND_TOKEN_COUNT = 30
ETH_PRICE_USD = 3_000.0
BTC_PRICE_USD = 96_000.0
SOL_PRICE_USD = 180.0

USDC_CONTRACT = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDT_CONTRACT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
THORCHAIN_ETH = "0xd37bbe5744d730a1d98d8dc97c42f0ca46ad7146"
CHAINFLIP_ETH = "0x6995ab7c4d7f4b03f467cf4c8e920427d9621dbd"
WORMHOLE_ETH = "0x3ee18b2214aff97000d974cf647e7c347e8fa585"
DEBRIDGE_ETH = "0xef4fb24ad0916217251f553c0596f8edc630eb66"
ACROSS_ETH = "0x5c7bcd6e7de5423a257d81b4f24d0a0b28f94a05"
CELER_ETH = "0x5427fefa711eff984124bfbb1ab6fbf5e3da1820"
WORMHOLE_SOLANA = "worm2ZoG2kUd4vFXhvjh93UUH596ayRfgQ2MgjNMTth"
ACROSS_BASE = "0x09aea4b2242abc8bb4bb78d537a67a245a7bec64"
CELER_BSC = "0xdd90e5e87a2081dcf0391920868ebc2ffb81a1af"
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


@dataclass
class FixtureBundle:
    chain: str
    seed_address: str
    transactions: List[Dict[str, Any]]
    token_transfers: List[Dict[str, Any]]
    asset_prices: List[Dict[str, Any]]
    bridge_correlations: List[Dict[str, Any]]
    neo4j_transactions: List[Dict[str, Any]]


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _env(dotenv: dict[str, str], key: str, default: str | None = None) -> str | None:
    return os.environ.get(key) or dotenv.get(key) or default


def _fixture_address(namespace: str, index: int) -> str:
    digest = hashlib.sha256(f"{namespace}:{index}".encode("utf-8")).hexdigest()
    return "0x" + digest[:40]


def _fixture_tx_hash(namespace: str, index: int) -> str:
    return "0x" + hashlib.sha256(f"{namespace}:{index}".encode("utf-8")).hexdigest()


def _fixture_base58(namespace: str, index: int, *, length: int = 44) -> str:
    digest = hashlib.sha256(f"{namespace}:{index}".encode("utf-8")).digest()
    chars: list[str] = []
    cursor = 0
    while len(chars) < length:
        if cursor >= len(digest):
            digest = hashlib.sha256(digest).digest()
            cursor = 0
        chars.append(BASE58_ALPHABET[digest[cursor] % len(BASE58_ALPHABET)])
        cursor += 1
    return "".join(chars)


def _fixture_btc_address(namespace: str, index: int) -> str:
    return "bc1q" + hashlib.sha256(f"{namespace}:{index}".encode("utf-8")).hexdigest()[:38]


def build_high_degree_evm_fixture() -> FixtureBundle:
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    block_number = 19_000_000

    transactions: List[Dict[str, Any]] = []
    token_transfers: List[Dict[str, Any]] = []
    neo4j_transactions: List[Dict[str, Any]] = []

    outbound_counterparties = [
        _fixture_address("fixture-outbound", index)
        for index in range(OUTBOUND_NATIVE_COUNT)
    ]
    inbound_counterparties = [
        _fixture_address("fixture-inbound", index)
        for index in range(INBOUND_NATIVE_COUNT)
    ]

    for index, counterparty in enumerate(outbound_counterparties):
        tx_hash = _fixture_tx_hash("fixture-native-outbound", index)
        timestamp = base_time + timedelta(minutes=index)
        row = {
            "blockchain": FIXTURE_CHAIN,
            "tx_hash": tx_hash,
            "block_number": block_number + index,
            "timestamp": timestamp,
            "from_address": FIXTURE_SEED_ADDRESS,
            "to_address": counterparty,
            "value_native": round(1.25 + (index * 0.01), 8),
            "status": "success",
        }
        transactions.append(row)
        neo4j_transactions.append(row)

    for index, counterparty in enumerate(inbound_counterparties):
        tx_hash = _fixture_tx_hash("fixture-native-inbound", index)
        timestamp = base_time + timedelta(hours=2, minutes=index)
        row = {
            "blockchain": FIXTURE_CHAIN,
            "tx_hash": tx_hash,
            "block_number": block_number + 1_000 + index,
            "timestamp": timestamp,
            "from_address": counterparty,
            "to_address": FIXTURE_SEED_ADDRESS,
            "value_native": round(0.9 + (index * 0.015), 8),
            "status": "success",
        }
        transactions.append(row)
        neo4j_transactions.append(row)

    for index in range(SECOND_HOP_NATIVE_COUNT):
        source = outbound_counterparties[index % len(outbound_counterparties)]
        sink = _fixture_address("fixture-second-hop", index)
        tx_hash = _fixture_tx_hash("fixture-second-hop", index)
        timestamp = base_time + timedelta(hours=4, minutes=index)
        row = {
            "blockchain": FIXTURE_CHAIN,
            "tx_hash": tx_hash,
            "block_number": block_number + 2_000 + index,
            "timestamp": timestamp,
            "from_address": source,
            "to_address": sink,
            "value_native": round(0.25 + (index * 0.005), 8),
            "status": "success",
        }
        transactions.append(row)
        neo4j_transactions.append(row)

    for index in range(OUTBOUND_TOKEN_COUNT):
        tx_hash = _fixture_tx_hash("fixture-token-outbound", index)
        timestamp = base_time + timedelta(hours=6, minutes=index)
        counterparty = outbound_counterparties[index % len(outbound_counterparties)]
        token_transfers.append(
            {
                "blockchain": FIXTURE_CHAIN,
                "tx_hash": tx_hash,
                "transfer_index": 0,
                "asset_symbol": "USDC",
                "asset_contract": USDC_CONTRACT,
                "canonical_asset_id": "usd-coin",
                "from_address": FIXTURE_SEED_ADDRESS,
                "to_address": counterparty,
                "amount_raw": str(5_000_000 + (index * 250_000)),
                "amount_normalized": round(5.0 + (index * 0.25), 6),
                "timestamp": timestamp,
            }
        )

    for index in range(INBOUND_TOKEN_COUNT):
        tx_hash = _fixture_tx_hash("fixture-token-inbound", index)
        timestamp = base_time + timedelta(hours=8, minutes=index)
        counterparty = inbound_counterparties[index % len(inbound_counterparties)]
        token_transfers.append(
            {
                "blockchain": FIXTURE_CHAIN,
                "tx_hash": tx_hash,
                "transfer_index": 0,
                "asset_symbol": "USDT",
                "asset_contract": USDT_CONTRACT,
                "canonical_asset_id": "tether",
                "from_address": counterparty,
                "to_address": FIXTURE_SEED_ADDRESS,
                "amount_raw": str(7_500_000 + (index * 100_000)),
                "amount_normalized": round(7.5 + (index * 0.1), 6),
                "timestamp": timestamp,
            }
        )

    timestamp_hour = base_time.replace(minute=0, second=0, microsecond=0)
    asset_prices = [
        {
            "canonical_asset_id": "usd-coin",
            "timestamp_hour": timestamp_hour,
            "price_usd": 1.0,
            "source": "fixture",
        },
        {
            "canonical_asset_id": "tether",
            "timestamp_hour": timestamp_hour,
            "price_usd": 1.0,
            "source": "fixture",
        },
    ]

    return FixtureBundle(
        chain=FIXTURE_CHAIN,
        seed_address=FIXTURE_SEED_ADDRESS,
        transactions=transactions,
        token_transfers=token_transfers,
        asset_prices=asset_prices,
        bridge_correlations=[],
        neo4j_transactions=neo4j_transactions,
    )


def build_bridge_cross_chain_fixture() -> FixtureBundle:
    base_time = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    source_block_number = 19_100_000

    transactions: List[Dict[str, Any]] = []
    bridge_correlations: List[Dict[str, Any]] = []
    neo4j_transactions: List[Dict[str, Any]] = []

    bridge_specs: List[Dict[str, Any]] = [
        {
            "protocol_id": "thorchain",
            "mechanism": "native_amm",
            "contract": THORCHAIN_ETH,
            "source_amount": 4.25,
            "source_fiat_value": 4.25 * ETH_PRICE_USD,
            "destination_chain": "bitcoin",
            "destination_asset": "BTC",
            "destination_amount": 0.081,
            "destination_fiat_value": 0.081 * BTC_PRICE_USD,
            "destination_address": _fixture_btc_address("thorchain-destination", 0),
            "destination_source_address": _fixture_btc_address("thorchain-vault", 0),
            "status": "completed",
            "correlation_confidence": 0.99,
            "time_delta_seconds": 540,
            "order_id": "fixture-thorchain-order-001",
            "resolution_method": "memo_match",
            "metadata": {
                "fixture_family": "bridge_crosschain",
                "route_summary": "ethereum -> bitcoin via THORChain",
            },
        },
        {
            "protocol_id": "chainflip",
            "mechanism": "native_amm",
            "contract": CHAINFLIP_ETH,
            "source_amount": 5.1,
            "source_fiat_value": 5.1 * ETH_PRICE_USD,
            "destination_chain": "bitcoin",
            "destination_asset": "BTC",
            "destination_amount": 0.095,
            "destination_fiat_value": 0.095 * BTC_PRICE_USD,
            "destination_address": _fixture_btc_address("chainflip-destination", 0),
            "destination_source_address": _fixture_btc_address("chainflip-vault", 0),
            "status": "completed",
            "correlation_confidence": 0.97,
            "time_delta_seconds": 315,
            "order_id": "fixture-chainflip-order-001",
            "resolution_method": "order_id_match",
            "metadata": {
                "fixture_family": "bridge_crosschain",
                "route_summary": "ethereum -> bitcoin via Chainflip",
            },
        },
        {
            "protocol_id": "wormhole",
            "mechanism": "lock_mint",
            "contract": WORMHOLE_ETH,
            "source_amount": 3.8,
            "source_fiat_value": 3.8 * ETH_PRICE_USD,
            "destination_chain": "solana",
            "destination_asset": "SOL",
            "destination_amount": 63.0,
            "destination_fiat_value": 63.0 * SOL_PRICE_USD,
            "destination_address": _fixture_base58("wormhole-destination", 0),
            "destination_source_address": WORMHOLE_SOLANA,
            "status": "completed",
            "correlation_confidence": 0.98,
            "time_delta_seconds": 180,
            "order_id": "fixture-wormhole-order-001",
            "resolution_method": "event_log_match",
            "metadata": {
                "fixture_family": "bridge_crosschain",
                "route_summary": "ethereum -> solana via Wormhole",
            },
        },
        {
            "protocol_id": "debridge",
            "mechanism": "solver",
            "contract": DEBRIDGE_ETH,
            "source_amount": 6.75,
            "source_fiat_value": 6.75 * ETH_PRICE_USD,
            "destination_chain": None,
            "destination_asset": "USDC",
            "destination_amount": None,
            "destination_fiat_value": None,
            "destination_address": None,
            "destination_source_address": None,
            "status": "pending",
            "correlation_confidence": 0.66,
            "time_delta_seconds": None,
            "order_id": "fixture-debridge-order-001",
            "resolution_method": None,
            "metadata": {
                "fixture_family": "bridge_crosschain",
                "route_summary": "ethereum -> solana via deBridge pending resolution",
            },
        },
        {
            "protocol_id": "across",
            "mechanism": "liquidity",
            "contract": ACROSS_ETH,
            "source_amount": 2.35,
            "source_fiat_value": 2.35 * ETH_PRICE_USD,
            "destination_chain": "base",
            "destination_asset": "ETH",
            "destination_amount": 2.31,
            "destination_fiat_value": 2.31 * ETH_PRICE_USD,
            "destination_address": _fixture_address("across-destination", 0),
            "destination_source_address": ACROSS_BASE,
            "status": "completed",
            "correlation_confidence": 0.95,
            "time_delta_seconds": 105,
            "order_id": "fixture-across-order-001",
            "resolution_method": "api_lookup",
            "metadata": {
                "fixture_family": "bridge_crosschain",
                "route_summary": "ethereum -> base via Across",
            },
        },
        {
            "protocol_id": "celer",
            "mechanism": "burn_release",
            "contract": CELER_ETH,
            "source_amount": 1.95,
            "source_fiat_value": 1.95 * ETH_PRICE_USD,
            "destination_chain": "bsc",
            "destination_asset": "BNB",
            "destination_amount": 8.4,
            "destination_fiat_value": 8.4 * 610.0,
            "destination_address": _fixture_address("celer-destination", 0),
            "destination_source_address": CELER_BSC,
            "status": "completed",
            "correlation_confidence": 0.94,
            "time_delta_seconds": 240,
            "order_id": "fixture-celer-order-001",
            "resolution_method": "api_lookup",
            "metadata": {
                "fixture_family": "bridge_crosschain",
                "route_summary": "ethereum -> bsc via Celer cBridge",
            },
        },
    ]

    destination_block_bases = {
        "bitcoin": 890_000,
        "solana": 321_000_000,
        "base": 19_500_000,
        "bsc": 37_200_000,
    }

    for index, spec in enumerate(bridge_specs):
        source_tx_hash = _fixture_tx_hash(f"fixture-bridge-source-{spec['protocol_id']}", index)
        source_timestamp = base_time + timedelta(minutes=index)
        source_row = {
            "blockchain": FIXTURE_CHAIN,
            "tx_hash": source_tx_hash,
            "block_number": source_block_number + index,
            "timestamp": source_timestamp,
            "from_address": FIXTURE_SEED_ADDRESS,
            "to_address": spec["contract"].lower(),
            "value_native": spec["source_amount"],
            "status": "success",
        }
        transactions.append(source_row)
        neo4j_transactions.append(source_row)

        destination_tx_hash = None
        resolved_at = None
        if spec["status"] == "completed" and spec["destination_chain"] and spec["destination_address"]:
            destination_tx_hash = _fixture_tx_hash(
                f"fixture-bridge-destination-{spec['protocol_id']}",
                index,
            )
            resolved_at = source_timestamp + timedelta(seconds=spec["time_delta_seconds"] or 0)
            dest_row = {
                "blockchain": spec["destination_chain"],
                "tx_hash": destination_tx_hash,
                "block_number": destination_block_bases[spec["destination_chain"]] + index,
                "timestamp": resolved_at,
                "from_address": spec["destination_source_address"],
                "to_address": spec["destination_address"],
                "value_native": spec["destination_amount"],
                "status": "success",
            }
            transactions.append(dest_row)
            neo4j_transactions.append(dest_row)

        bridge_correlations.append(
            {
                "protocol_id": spec["protocol_id"],
                "mechanism": spec["mechanism"],
                "source_chain": FIXTURE_CHAIN,
                "source_tx_hash": source_tx_hash,
                "source_address": FIXTURE_SEED_ADDRESS,
                "source_asset": "ETH",
                "source_amount": spec["source_amount"],
                "source_fiat_value": spec["source_fiat_value"],
                "destination_chain": spec["destination_chain"],
                "destination_tx_hash": destination_tx_hash,
                "destination_address": spec["destination_address"],
                "destination_asset": spec["destination_asset"],
                "destination_amount": spec["destination_amount"],
                "destination_fiat_value": spec["destination_fiat_value"],
                "time_delta_seconds": spec["time_delta_seconds"],
                "status": spec["status"],
                "correlation_confidence": spec["correlation_confidence"],
                "order_id": spec["order_id"],
                "resolution_method": spec["resolution_method"],
                "resolved_at": resolved_at,
                "updated_at": resolved_at or source_timestamp,
                "metadata": spec["metadata"],
            }
        )

    return FixtureBundle(
        chain=FIXTURE_CHAIN,
        seed_address=FIXTURE_SEED_ADDRESS,
        transactions=transactions,
        token_transfers=[],
        asset_prices=[],
        bridge_correlations=bridge_correlations,
        neo4j_transactions=neo4j_transactions,
    )


def merge_fixture_bundles(*bundles: FixtureBundle) -> FixtureBundle:
    transactions: List[Dict[str, Any]] = []
    token_transfers: List[Dict[str, Any]] = []
    asset_prices: List[Dict[str, Any]] = []
    bridge_correlations: List[Dict[str, Any]] = []
    neo4j_transactions: List[Dict[str, Any]] = []

    for bundle in bundles:
        transactions.extend(bundle.transactions)
        token_transfers.extend(bundle.token_transfers)
        asset_prices.extend(bundle.asset_prices)
        bridge_correlations.extend(bundle.bridge_correlations)
        neo4j_transactions.extend(bundle.neo4j_transactions)

    return FixtureBundle(
        chain=FIXTURE_CHAIN,
        seed_address=FIXTURE_SEED_ADDRESS,
        transactions=transactions,
        token_transfers=token_transfers,
        asset_prices=asset_prices,
        bridge_correlations=bridge_correlations,
        neo4j_transactions=neo4j_transactions,
    )


async def _insert_postgres(bundle: FixtureBundle, dotenv: dict[str, str]) -> Dict[str, int]:
    conn = await asyncpg.connect(
        host=_env(dotenv, "POSTGRES_HOST", "127.0.0.1"),
        port=int(_env(dotenv, "POSTGRES_PORT", "5433")),
        user=_env(dotenv, "POSTGRES_USER", "jackdawsentry_user"),
        password=_env(dotenv, "POSTGRES_PASSWORD"),
        database=_env(dotenv, "POSTGRES_DB", "jackdawsentry_graph"),
    )
    try:
        inserted_transactions = 0
        inserted_token_transfers = 0
        inserted_asset_prices = 0
        inserted_bridge_correlations = 0

        for row in bundle.transactions:
            result = await conn.execute(
                """
                INSERT INTO raw_transactions (
                    blockchain,
                    tx_hash,
                    block_number,
                    timestamp,
                    from_address,
                    to_address,
                    value_native,
                    status
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (blockchain, tx_hash) DO NOTHING
                """,
                row["blockchain"],
                row["tx_hash"],
                row["block_number"],
                row["timestamp"],
                row["from_address"],
                row["to_address"],
                row["value_native"],
                row["status"],
            )
            inserted_transactions += int(result.split()[-1])

        for row in bundle.token_transfers:
            result = await conn.execute(
                """
                INSERT INTO raw_token_transfers (
                    blockchain,
                    tx_hash,
                    transfer_index,
                    asset_symbol,
                    asset_contract,
                    canonical_asset_id,
                    from_address,
                    to_address,
                    amount_raw,
                    amount_normalized,
                    timestamp
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (blockchain, tx_hash, transfer_index) DO NOTHING
                """,
                row["blockchain"],
                row["tx_hash"],
                row["transfer_index"],
                row["asset_symbol"],
                row["asset_contract"],
                row["canonical_asset_id"],
                row["from_address"],
                row["to_address"],
                row["amount_raw"],
                row["amount_normalized"],
                row["timestamp"],
            )
            inserted_token_transfers += int(result.split()[-1])

        for row in bundle.asset_prices:
            result = await conn.execute(
                """
                INSERT INTO asset_prices (
                    canonical_asset_id,
                    timestamp_hour,
                    price_usd,
                    source
                )
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (canonical_asset_id, timestamp_hour) DO NOTHING
                """,
                row["canonical_asset_id"],
                row["timestamp_hour"],
                row["price_usd"],
                row["source"],
            )
            inserted_asset_prices += int(result.split()[-1])

        bridge_columns = {
            record["column_name"]
            for record in await conn.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'bridge_correlations'
                """
            )
        }
        protocol_column = "protocol_id" if "protocol_id" in bridge_columns else "protocol"
        retry_columns_supported = {
            "retry_count",
            "next_retry_at",
            "order_id",
            "resolution_method",
            "resolved_at",
        }.issubset(bridge_columns)

        for row in bundle.bridge_correlations:
            if retry_columns_supported:
                sql = f"""
                    INSERT INTO bridge_correlations (
                        {protocol_column},
                        mechanism,
                        source_chain,
                        source_tx_hash,
                        source_address,
                        source_asset,
                        source_amount,
                        source_fiat_value,
                        destination_chain,
                        destination_tx_hash,
                        destination_address,
                        destination_asset,
                        destination_amount,
                        destination_fiat_value,
                        time_delta_seconds,
                        status,
                        correlation_confidence,
                        order_id,
                        resolution_method,
                        resolved_at,
                        updated_at,
                        metadata
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                        $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22
                    )
                    ON CONFLICT (source_chain, source_tx_hash) DO NOTHING
                """
                params = (
                    row["protocol_id"],
                    row["mechanism"],
                    row["source_chain"],
                    row["source_tx_hash"],
                    row["source_address"],
                    row["source_asset"],
                    row["source_amount"],
                    row["source_fiat_value"],
                    row["destination_chain"],
                    row["destination_tx_hash"],
                    row["destination_address"],
                    row["destination_asset"],
                    row["destination_amount"],
                    row["destination_fiat_value"],
                    row["time_delta_seconds"],
                    row["status"],
                    row["correlation_confidence"],
                    row["order_id"],
                    row["resolution_method"],
                    row["resolved_at"],
                    row["updated_at"],
                    json.dumps(row["metadata"]),
                )
            else:
                sql = f"""
                    INSERT INTO bridge_correlations (
                        {protocol_column},
                        mechanism,
                        source_chain,
                        source_tx_hash,
                        source_address,
                        source_asset,
                        source_amount,
                        source_fiat_value,
                        destination_chain,
                        destination_tx_hash,
                        destination_address,
                        destination_asset,
                        destination_amount,
                        destination_fiat_value,
                        time_delta_seconds,
                        status,
                        correlation_confidence,
                        updated_at,
                        metadata
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                        $11, $12, $13, $14, $15, $16, $17, $18, $19
                    )
                    ON CONFLICT (source_chain, source_tx_hash) DO NOTHING
                """
                params = (
                    row["protocol_id"],
                    row["mechanism"],
                    row["source_chain"],
                    row["source_tx_hash"],
                    row["source_address"],
                    row["source_asset"],
                    row["source_amount"],
                    row["source_fiat_value"],
                    row["destination_chain"],
                    row["destination_tx_hash"],
                    row["destination_address"],
                    row["destination_asset"],
                    row["destination_amount"],
                    row["destination_fiat_value"],
                    row["time_delta_seconds"],
                    row["status"],
                    row["correlation_confidence"],
                    row["updated_at"],
                    json.dumps(row["metadata"]),
                )
            result = await conn.execute(sql, *params)
            inserted_bridge_correlations += int(result.split()[-1])

        return {
            "transactions": inserted_transactions,
            "token_transfers": inserted_token_transfers,
            "asset_prices": inserted_asset_prices,
            "bridge_correlations": inserted_bridge_correlations,
        }
    finally:
        await conn.close()


def _insert_neo4j(bundle: FixtureBundle, dotenv: dict[str, str]) -> int:
    uri = _env(dotenv, "NEO4J_URI", "bolt://127.0.0.1:7688")
    if uri.startswith("bolt://neo4j:"):
        uri = uri.replace("bolt://neo4j:", "bolt://127.0.0.1:", 1)
    if uri == "bolt://localhost:7687":
        uri = "bolt://127.0.0.1:7688"

    driver = GraphDatabase.driver(
        uri,
        auth=(
            _env(dotenv, "NEO4J_USER", "neo4j"),
            _env(dotenv, "NEO4J_PASSWORD"),
        ),
    )
    try:
        with driver.session() as session:
            session.run(
                """
                UNWIND $rows AS row
                MERGE (src:Address {address: row.from_address, blockchain: row.blockchain})
                MERGE (dst:Address {address: row.to_address, blockchain: row.blockchain})
                MERGE (tx:Transaction {hash: row.tx_hash, blockchain: row.blockchain})
                SET
                    tx.timestamp = row.timestamp,
                    tx.block_number = row.block_number,
                    tx.value = row.value_native
                MERGE (src)-[:SENT]->(tx)
                MERGE (tx)-[recv:RECEIVED]->(dst)
                SET recv.value_native = row.value_native
                """,
                rows=[
                    {
                        "blockchain": row["blockchain"],
                        "tx_hash": row["tx_hash"],
                        "timestamp": row["timestamp"].isoformat(),
                        "block_number": row["block_number"],
                        "value_native": row["value_native"],
                        "from_address": row["from_address"],
                        "to_address": row["to_address"],
                    }
                    for row in bundle.neo4j_transactions
                ],
            ).consume()
        return len(bundle.neo4j_transactions)
    finally:
        driver.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument(
        "--skip-neo4j",
        action="store_true",
        help="Only seed PostgreSQL raw-event tables and asset prices.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dotenv = _load_dotenv(Path(args.env_file))
    bundle = merge_fixture_bundles(
        build_high_degree_evm_fixture(),
        build_bridge_cross_chain_fixture(),
    )

    postgres_result = asyncio.run(_insert_postgres(bundle, dotenv))
    neo4j_inserted = 0 if args.skip_neo4j else _insert_neo4j(bundle, dotenv)

    print("Loaded high-degree + bridge/cross-chain performance fixture.")
    print(f"Recommended seed: {bundle.chain} {bundle.seed_address}")
    print(
        "Inserted rows:"
        f" raw_transactions={postgres_result['transactions']}"
        f" raw_token_transfers={postgres_result['token_transfers']}"
        f" asset_prices={postgres_result['asset_prices']}"
        f" bridge_correlations={postgres_result['bridge_correlations']}"
        f" neo4j_transactions={neo4j_inserted}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
