#!/usr/bin/env python3
"""Exercise the live graph stack for drift, abuse controls, and secure defaults."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass
from typing import Any
from typing import Dict
from typing import Iterable
from urllib import error
from urllib import parse
from urllib import request


@dataclass
class HttpResult:
    status: int
    headers: Dict[str, str]
    body: str
    elapsed_ms: float


def _header_map(raw_headers) -> Dict[str, str]:
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
            body = resp.read().decode("utf-8")
            return HttpResult(
                status=resp.getcode(),
                headers=_header_map(resp.headers),
                body=body,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
            )
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return HttpResult(
            status=exc.code,
            headers=_header_map(exc.headers),
            body=body,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )


def _require(condition: bool, message: str, *, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _latency_summary(values: Iterable[float]) -> str:
    data = list(values)
    if not data:
        return "n/a"
    median = statistics.median(data)
    p95_index = max(0, min(len(data) - 1, int(round((len(data) - 1) * 0.95))))
    sorted_values = sorted(data)
    p95 = sorted_values[p95_index]
    return f"median={median:.1f}ms p95={p95:.1f}ms max={max(data):.1f}ms"


def _json_body(response: HttpResult) -> Dict[str, Any]:
    return json.loads(response.body or "{}")


def _base(base_url: str, path: str) -> str:
    return parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def run_probe(args: argparse.Namespace) -> int:
    errors: list[str] = []
    print(f"Probing live graph stack at {args.base_url}")

    health = _http_request("GET", _base(args.base_url, "/health"), timeout=args.timeout)
    print(f"[health] status={health.status} elapsed={health.elapsed_ms:.1f}ms")
    _require(health.status == 200, f"/health returned {health.status}, expected 200", errors=errors)

    docs = _http_request("GET", _base(args.base_url, "/docs"), timeout=args.timeout)
    print(f"[docs] status={docs.status}")
    _require(
        docs.status == 404,
        f"/docs returned {docs.status}; expected 404 when EXPOSE_API_DOCS=false",
        errors=errors,
    )

    login_shell = _http_request("GET", _base(args.base_url, "/login"), timeout=args.timeout)
    print(f"[login-shell] status={login_shell.status}")
    csp = login_shell.headers.get("content-security-policy", "")
    _require(login_shell.status == 200, f"/login returned {login_shell.status}, expected 200", errors=errors)
    _require("'unsafe-inline'" not in csp, "login CSP still allows 'unsafe-inline'", errors=errors)
    _require("cdn.tailwindcss.com" not in csp, "login CSP still allows tailwind CDN", errors=errors)
    _require("cdn.jsdelivr.net" not in csp, "login CSP still allows jsdelivr CDN", errors=errors)

    login = _http_request(
        "POST",
        _base(args.base_url, "/api/v1/auth/login"),
        json_body={"username": args.username, "password": args.password},
        headers={"User-Agent": "live-abuse-probe/1.0"},
        timeout=args.timeout,
    )
    print(f"[login] status={login.status} elapsed={login.elapsed_ms:.1f}ms")
    _require(login.status == 200, f"login returned {login.status}, expected 200", errors=errors)
    _require(
        "no-store" in login.headers.get("cache-control", "").lower(),
        "login response is missing Cache-Control: no-store",
        errors=errors,
    )
    if login.status != 200:
        for problem in errors:
            print(f"ERROR: {problem}")
        return 1

    token = _json_body(login)["access_token"]
    auth_headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "live-abuse-probe/1.0",
    }

    session = _http_request(
        "POST",
        _base(args.base_url, "/api/v1/graph/sessions"),
        json_body={"seed_address": args.seed_address, "seed_chain": args.seed_chain},
        headers=auth_headers,
        timeout=args.timeout,
    )
    print(f"[session-create] status={session.status} elapsed={session.elapsed_ms:.1f}ms")
    _require(session.status == 200, f"session create returned {session.status}, expected 200", errors=errors)
    if session.status != 200:
        for problem in errors:
            print(f"ERROR: {problem}")
        return 1

    session_id = _json_body(session)["session_id"]
    expand_url = _base(args.base_url, f"/api/v1/graph/sessions/{session_id}/expand")
    hop_url = _base(args.base_url, f"/api/v1/graph/sessions/{session_id}/hops/probe-hop/status")

    warmup_expand = _http_request(
        "POST",
        expand_url,
        json_body={
            "operation_type": "expand_next",
            "seed_node_id": f"{args.seed_chain}:address:{args.seed_address}",
            "options": {"chain_filter": ["bitcoin"]},
        },
        headers=auth_headers,
        timeout=args.timeout,
    )
    print(f"[expand-warmup] status={warmup_expand.status} elapsed={warmup_expand.elapsed_ms:.1f}ms")
    _require(
        warmup_expand.status == 400,
        f"unsupported chain_filter returned {warmup_expand.status}, expected 400",
        errors=errors,
    )

    expand_statuses: list[int] = []
    expand_latencies: list[float] = []
    for _ in range(args.expand_attempts):
        result = _http_request(
            "POST",
            expand_url,
            json_body={
                "operation_type": "expand_next",
                "seed_node_id": f"{args.seed_chain}:address:{args.seed_address}",
                "options": {"chain_filter": ["bitcoin"]},
            },
            headers=auth_headers,
            timeout=args.timeout,
        )
        expand_statuses.append(result.status)
        expand_latencies.append(result.elapsed_ms)
        if result.status == 429:
            break
    print(f"[expand-abuse] statuses={expand_statuses[:5]}... final={expand_statuses[-1]} latency={_latency_summary(expand_latencies)}")
    _require(429 in expand_statuses, "expand abuse probe never hit a 429 rate limit", errors=errors)

    hop_statuses: list[int] = []
    hop_latencies: list[float] = []
    for _ in range(args.hop_attempts):
        result = _http_request(
            "GET",
            hop_url,
            headers=auth_headers,
            timeout=args.timeout,
        )
        hop_statuses.append(result.status)
        hop_latencies.append(result.elapsed_ms)
        if result.status == 429:
            break
    print(f"[hop-abuse] statuses={hop_statuses[:5]}... final={hop_statuses[-1]} latency={_latency_summary(hop_latencies)}")
    _require(
        any(status == 404 for status in hop_statuses),
        "hop-status probe never returned the expected 404 allowlist miss",
        errors=errors,
    )
    _require(429 in hop_statuses, "hop-status abuse probe never hit a 429 rate limit", errors=errors)

    if errors:
        print("\nLive abuse probe failed:")
        for problem in errors:
            print(f"- {problem}")
        return 1

    print("\nLive abuse probe passed.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8081")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--seed-address", default="0xabc123")
    parser.add_argument("--seed-chain", default="ethereum")
    parser.add_argument("--expand-attempts", type=int, default=35)
    parser.add_argument("--hop-attempts", type=int, default=65)
    parser.add_argument("--timeout", type=float, default=10.0)
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(run_probe(parse_args()))
