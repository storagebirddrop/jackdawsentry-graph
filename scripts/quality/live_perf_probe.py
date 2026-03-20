#!/usr/bin/env python3
"""Profile the live graph stack and report whether the dataset is realistic."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Iterable
from typing import Optional
from urllib import error
from urllib import parse
from urllib import request

import asyncpg
from neo4j import GraphDatabase


@dataclass
class HttpResult:
    status: int
    headers: Dict[str, str]
    body: str
    elapsed_ms: float


def _load_dotenv(env_path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _get_env(env_values: Dict[str, str], key: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(key) or env_values.get(key) or default


def _headers_map(raw_headers) -> Dict[str, str]:
    return {key.lower(): value for key, value in raw_headers.items()}


def _http_request(
    method: str,
    url: str,
    *,
    json_body: Dict[str, Any] | None = None,
    headers: Dict[str, str] | None = None,
    timeout: float = 10.0,
) -> HttpResult:
    encoded_body = None
    request_headers = dict(headers or {})
    if json_body is not None:
        encoded_body = json.dumps(json_body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    req = request.Request(url, data=encoded_body, headers=request_headers, method=method)
    started = time.perf_counter()
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return HttpResult(
                status=resp.getcode(),
                headers=_headers_map(resp.headers),
                body=resp.read().decode("utf-8"),
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
            )
    except error.HTTPError as exc:
        return HttpResult(
            status=exc.code,
            headers=_headers_map(exc.headers),
            body=exc.read().decode("utf-8"),
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )


def _latency_summary(values: Iterable[float]) -> str:
    data = list(values)
    if not data:
        return "n/a"
    ordered = sorted(data)
    median = statistics.median(ordered)
    p95 = ordered[min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * 0.95))))]
    return f"median={median:.1f}ms p95={p95:.1f}ms max={max(ordered):.1f}ms"


def _json_body(response: HttpResult) -> Dict[str, Any]:
    return json.loads(response.body or "{}")


def _base(base_url: str, path: str) -> str:
    return parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


async def _postgres_profile(env_values: Dict[str, str]) -> Dict[str, int]:
    conn = await asyncpg.connect(
        host=_get_env(env_values, "POSTGRES_HOST", "127.0.0.1"),
        port=int(_get_env(env_values, "POSTGRES_PORT", "5433")),
        user=_get_env(env_values, "POSTGRES_USER", "jackdawsentry_user"),
        password=_get_env(env_values, "POSTGRES_PASSWORD"),
        database=_get_env(env_values, "POSTGRES_DB", "jackdawsentry_graph"),
    )
    try:
        counts = await conn.fetchrow(
            """
            SELECT
                COALESCE((
                    SELECT SUM(n_live_tup)::bigint
                    FROM pg_stat_user_tables
                    WHERE schemaname = 'public'
                      AND relname LIKE 'raw_transactions%%'
                ), 0) AS raw_transactions_estimate,
                COALESCE((
                    SELECT SUM(n_live_tup)::bigint
                    FROM pg_stat_user_tables
                    WHERE schemaname = 'public'
                      AND relname LIKE 'raw_token_transfers%%'
                ), 0) AS raw_token_transfers_estimate,
                (SELECT COUNT(*)::bigint FROM graph_sessions) AS graph_sessions,
                (SELECT COUNT(*)::bigint FROM bridge_correlations) AS bridge_correlations
            """
        )
        top_hubs = await conn.fetch(
            """
            WITH address_activity AS (
                SELECT blockchain AS chain, lower(from_address) AS address, count(*)::bigint AS degree
                FROM raw_transactions
                WHERE from_address IS NOT NULL
                GROUP BY blockchain, lower(from_address)
                UNION ALL
                SELECT blockchain AS chain, lower(to_address) AS address, count(*)::bigint AS degree
                FROM raw_transactions
                WHERE to_address IS NOT NULL
                GROUP BY blockchain, lower(to_address)
                UNION ALL
                SELECT blockchain AS chain, lower(from_address) AS address, count(*)::bigint AS degree
                FROM raw_token_transfers
                WHERE from_address IS NOT NULL
                GROUP BY blockchain, lower(from_address)
                UNION ALL
                SELECT blockchain AS chain, lower(to_address) AS address, count(*)::bigint AS degree
                FROM raw_token_transfers
                WHERE to_address IS NOT NULL
                GROUP BY blockchain, lower(to_address)
            )
            SELECT chain, address, SUM(degree)::bigint AS degree
            FROM address_activity
            WHERE address IS NOT NULL
            GROUP BY chain, address
            ORDER BY degree DESC, address ASC
            LIMIT 5
            """
        )
        return {
            "raw_transactions_estimate": counts["raw_transactions_estimate"],
            "raw_token_transfers_estimate": counts["raw_token_transfers_estimate"],
            "graph_sessions": counts["graph_sessions"],
            "bridge_correlations": counts["bridge_correlations"],
            "top_hubs": [
                {
                    "chain": row["chain"],
                    "address": row["address"],
                    "degree": row["degree"],
                }
                for row in top_hubs
            ],
        }
    finally:
        await conn.close()


def _neo4j_profile(env_values: Dict[str, str]) -> Dict[str, Any]:
    uri = _get_env(env_values, "NEO4J_URI", "bolt://127.0.0.1:7688")
    if uri.startswith("bolt://neo4j:"):
        uri = uri.replace("bolt://neo4j:", "bolt://127.0.0.1:", 1)
    if uri == "bolt://neo4j:7687":
        uri = "bolt://127.0.0.1:7688"

    driver = GraphDatabase.driver(
        uri,
        auth=(
            _get_env(env_values, "NEO4J_USER", "neo4j"),
            _get_env(env_values, "NEO4J_PASSWORD"),
        ),
    )
    try:
        with driver.session() as session:
            counts = session.run(
                "MATCH (n) RETURN count(n) AS nodes"
            ).single()
            rels = session.run(
                "MATCH ()-[r]->() RETURN count(r) AS rels"
            ).single()
            hubs = list(
                session.run(
                    """
                    MATCH (a:Address)--()
                    RETURN
                        a.address AS address,
                        coalesce(a.blockchain, 'unknown') AS chain,
                        count(*) AS degree
                    ORDER BY degree DESC
                    LIMIT 5
                    """
                )
            )
    finally:
        driver.close()

    return {
        "nodes": counts["nodes"],
        "relationships": rels["rels"],
        "top_hubs": [
            {
                "address": record["address"],
                "chain": record["chain"],
                "degree": record["degree"],
            }
            for record in hubs
            if record["address"]
        ],
    }


def _run_operation(
    *,
    base_url: str,
    session_id: str,
    seed_node_id: str,
    token: str,
    operation_type: str,
    iterations: int,
    timeout: float,
) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "live-perf-probe/1.0",
    }
    url = _base(base_url, f"/api/v1/graph/sessions/{session_id}/expand")
    latencies: list[float] = []
    sizes: list[int] = []
    statuses: list[int] = []
    node_counts: list[int] = []
    edge_counts: list[int] = []
    sample_node_type_counts: dict[str, int] = {}
    bridge_hop_ids: set[str] = set()
    bridge_protocols: set[str] = set()

    for _ in range(iterations):
        response = _http_request(
            "POST",
            url,
            json_body={
                "operation_type": operation_type,
                "seed_node_id": seed_node_id,
                "options": {"max_results": 25, "page_size": 25},
            },
            headers=headers,
            timeout=timeout,
        )
        statuses.append(response.status)
        latencies.append(response.elapsed_ms)
        sizes.append(len(response.body.encode("utf-8")))
        if response.status == 200:
            payload = _json_body(response)
            added_nodes = payload.get("added_nodes", [])
            node_counts.append(len(added_nodes))
            edge_counts.append(len(payload.get("added_edges", [])))
            if not sample_node_type_counts:
                sample_counter: Counter[str] = Counter()
                for node in added_nodes:
                    node_type = node.get("node_type") or node.get("type") or "unknown"
                    sample_counter[node_type] += 1
                sample_node_type_counts = dict(sorted(sample_counter.items()))
            for node in added_nodes:
                bridge_data = node.get("bridge_hop_data") or {}
                hop_id = bridge_data.get("hop_id")
                protocol_id = bridge_data.get("protocol_id")
                if hop_id:
                    bridge_hop_ids.add(hop_id)
                if protocol_id:
                    bridge_protocols.add(protocol_id)

    return {
        "operation": operation_type,
        "statuses": statuses,
        "latency": _latency_summary(latencies),
        "body_size_bytes": {
            "min": min(sizes) if sizes else 0,
            "max": max(sizes) if sizes else 0,
        },
        "node_counts": node_counts,
        "edge_counts": edge_counts,
        "node_type_counts": sample_node_type_counts,
        "bridge_hop_ids": sorted(bridge_hop_ids),
        "bridge_protocols": sorted(bridge_protocols),
    }


def _run_hop_status(
    *,
    base_url: str,
    session_id: str,
    hop_id: str,
    token: str,
    iterations: int,
    timeout: float,
) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "live-perf-probe/1.0",
    }
    url = _base(base_url, f"/api/v1/graph/sessions/{session_id}/hops/{hop_id}/status")
    latencies: list[float] = []
    statuses: list[int] = []
    hop_states: list[str] = []
    destination_chains: set[str] = set()
    confidences: list[float] = []

    for _ in range(iterations):
        response = _http_request("GET", url, headers=headers, timeout=timeout)
        statuses.append(response.status)
        latencies.append(response.elapsed_ms)
        if response.status == 200:
            payload = _json_body(response)
            hop_states.append(payload.get("status", "unknown"))
            destination_chain = payload.get("destination_chain")
            if destination_chain:
                destination_chains.add(destination_chain)
            confidence = payload.get("correlation_confidence")
            if confidence is not None:
                confidences.append(float(confidence))

    return {
        "statuses": statuses,
        "latency": _latency_summary(latencies),
        "hop_states": hop_states,
        "destination_chains": sorted(destination_chains),
        "confidence_min": min(confidences) if confidences else None,
        "confidence_max": max(confidences) if confidences else None,
    }


def run_probe(args: argparse.Namespace) -> int:
    env_values = _load_dotenv(Path(args.env_file))

    print(f"Profiling live graph stack at {args.base_url}")
    postgres_profile = asyncio.run(_postgres_profile(env_values))
    neo4j_profile = _neo4j_profile(env_values)

    print(
        "[dataset] postgres"
        f" raw_transactions~={postgres_profile['raw_transactions_estimate']}"
        f" raw_token_transfers~={postgres_profile['raw_token_transfers_estimate']}"
        f" graph_sessions={postgres_profile['graph_sessions']}"
        f" bridge_correlations={postgres_profile['bridge_correlations']}"
    )
    for hub in postgres_profile["top_hubs"]:
        print(
            f"[dataset] pg-top-hub address={hub['address']} chain={hub['chain']} degree={hub['degree']}"
        )
    print(
        "[dataset] neo4j"
        f" nodes={neo4j_profile['nodes']}"
        f" relationships={neo4j_profile['relationships']}"
    )

    for hub in neo4j_profile["top_hubs"]:
        print(
            f"[dataset] top-hub address={hub['address']} chain={hub['chain']} degree={hub['degree']}"
        )

    health = _http_request("GET", _base(args.base_url, "/health"), timeout=args.timeout)
    print(f"[health] status={health.status} elapsed={health.elapsed_ms:.1f}ms")

    login = _http_request(
        "POST",
        _base(args.base_url, "/api/v1/auth/login"),
        json_body={"username": args.username, "password": args.password},
        headers={"User-Agent": "live-perf-probe/1.0"},
        timeout=args.timeout,
    )
    print(f"[login] status={login.status} elapsed={login.elapsed_ms:.1f}ms")
    if login.status != 200:
        print("Live perf probe could not authenticate; aborting.")
        return 1

    token = _json_body(login)["access_token"]
    candidate_seed = args.seed_address
    candidate_chain = args.seed_chain
    if not candidate_seed and postgres_profile["top_hubs"]:
        candidate = postgres_profile["top_hubs"][0]
        candidate_seed = candidate["address"]
        candidate_chain = candidate["chain"]
    if not candidate_seed and neo4j_profile["top_hubs"]:
        candidate = neo4j_profile["top_hubs"][0]
        candidate_seed = candidate["address"]
        candidate_chain = candidate["chain"]

    if not candidate_seed:
        print("No seed address provided and no top-hub candidates were found.")
        print("Live perf probe completed control-plane checks only.")
        return 0

    session = _http_request(
        "POST",
        _base(args.base_url, "/api/v1/graph/sessions"),
        json_body={"seed_address": candidate_seed, "seed_chain": candidate_chain},
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "live-perf-probe/1.0",
        },
        timeout=args.timeout,
    )
    print(f"[session-create] status={session.status} elapsed={session.elapsed_ms:.1f}ms")
    if session.status != 200:
        print("Live perf probe could not create a graph session; aborting graph profiling.")
        return 1

    session_id = _json_body(session)["session_id"]
    seed_node_id = f"{candidate_chain}:address:{candidate_seed}"
    print(f"[seed] chain={candidate_chain} address={candidate_seed}")

    if neo4j_profile["nodes"] == 0 and postgres_profile["raw_transactions_estimate"] == 0:
        print("Representative graph profiling skipped: the local dataset is empty.")
        print("Control-plane latency is healthy, but graph-heavy performance numbers would be meaningless.")
        return 0

    for operation in ("expand_next", "expand_neighbors"):
        result = _run_operation(
            base_url=args.base_url,
            session_id=session_id,
            seed_node_id=seed_node_id,
            token=token,
            operation_type=operation,
            iterations=args.iterations,
            timeout=args.timeout,
        )
        print(
            f"[{operation}] statuses={result['statuses']} latency={result['latency']} "
            f"size={result['body_size_bytes']} nodes={result['node_counts']} edges={result['edge_counts']} "
            f"types={result['node_type_counts']} bridge_protocols={result['bridge_protocols']}"
        )
        if operation == "expand_next" and result["bridge_hop_ids"]:
            hop_id = result["bridge_hop_ids"][0]
            hop_status = _run_hop_status(
                base_url=args.base_url,
                session_id=session_id,
                hop_id=hop_id,
                token=token,
                iterations=args.iterations,
                timeout=args.timeout,
            )
            print(
                f"[hop-status] hop_id={hop_id} statuses={hop_status['statuses']} "
                f"latency={hop_status['latency']} states={hop_status['hop_states']} "
                f"destination_chains={hop_status['destination_chains']} "
                f"confidence=({hop_status['confidence_min']},{hop_status['confidence_max']})"
            )

    print("Live perf probe completed.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8081")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--seed-address")
    parser.add_argument("--seed-chain", default="ethereum")
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--env-file", default=".env")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(run_probe(parse_args()))
