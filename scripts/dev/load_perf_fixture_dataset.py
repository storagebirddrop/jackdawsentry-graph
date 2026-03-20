#!/usr/bin/env python3
"""Load a deterministic high-degree graph fixture into the local graph stack."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
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

USDC_CONTRACT = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDT_CONTRACT = "0xdac17f958d2ee523a2206206994597c13d831ec7"


@dataclass
class FixtureBundle:
    chain: str
    seed_address: str
    transactions: List[Dict[str, Any]]
    token_transfers: List[Dict[str, Any]]
    asset_prices: List[Dict[str, Any]]
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

        return {
            "transactions": inserted_transactions,
            "token_transfers": inserted_token_transfers,
            "asset_prices": inserted_asset_prices,
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
    bundle = build_high_degree_evm_fixture()

    postgres_result = asyncio.run(_insert_postgres(bundle, dotenv))
    neo4j_inserted = 0 if args.skip_neo4j else _insert_neo4j(bundle, dotenv)

    print("Loaded high-degree EVM performance fixture.")
    print(f"Recommended seed: {bundle.chain} {bundle.seed_address}")
    print(
        "Inserted rows:"
        f" raw_transactions={postgres_result['transactions']}"
        f" raw_token_transfers={postgres_result['token_transfers']}"
        f" asset_prices={postgres_result['asset_prices']}"
        f" neo4j_transactions={neo4j_inserted}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
