#!/usr/bin/env python3
"""Create or update a local graph-runtime user for auth-enabled probes."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
from typing import Any

import asyncpg

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.api.auth import hash_password
from src.api.config import settings


def _load_dotenv(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _env_value(env_values: dict[str, str], key: str, default: str) -> str:
    return env_values.get(key) or default


async def _upsert_user(args: argparse.Namespace) -> dict[str, Any]:
    env_values = _load_dotenv(Path(args.env_file))
    conn = await asyncpg.connect(
        host=args.postgres_host or _env_value(env_values, "POSTGRES_HOST", "127.0.0.1"),
        port=int(args.postgres_port or _env_value(env_values, "POSTGRES_PORT", "5433")),
        user=args.postgres_user or _env_value(env_values, "POSTGRES_USER", settings.POSTGRES_USER),
        password=args.postgres_password or _env_value(env_values, "POSTGRES_PASSWORD", settings.POSTGRES_PASSWORD),
        database=args.postgres_db or _env_value(env_values, "POSTGRES_DB", settings.POSTGRES_DB),
    )
    try:
        existing = await conn.fetchrow(
            "SELECT id FROM users WHERE username = $1",
            args.username,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO users (
                username,
                email,
                password_hash,
                full_name,
                role,
                is_active
            )
            VALUES ($1, $2, $3, $4, $5, TRUE)
            ON CONFLICT (username) DO UPDATE
            SET
                email = EXCLUDED.email,
                password_hash = EXCLUDED.password_hash,
                full_name = EXCLUDED.full_name,
                role = EXCLUDED.role,
                is_active = TRUE,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id, username, email, role, is_active
            """,
            args.username,
            args.email,
            hash_password(args.password),
            args.full_name,
            args.role,
        )
    finally:
        await conn.close()

    return {
        "action": "updated" if existing else "created",
        "id": str(row["id"]),
        "username": row["username"],
        "email": row["email"],
        "role": row["role"],
        "is_active": row["is_active"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--email")
    parser.add_argument("--full-name")
    parser.add_argument(
        "--role",
        default="analyst",
        choices=["viewer", "analyst", "compliance_officer", "admin"],
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--postgres-host")
    parser.add_argument("--postgres-port", type=int)
    parser.add_argument("--postgres-user")
    parser.add_argument("--postgres-password")
    parser.add_argument("--postgres-db")
    args = parser.parse_args()
    if not args.email:
        args.email = f"{args.username}@local.invalid"
    if not args.full_name:
        args.full_name = args.username
    return args


def main() -> int:
    args = parse_args()
    result = asyncio.run(_upsert_user(args))
    print(
        f"{result['action']}: username={result['username']} "
        f"email={result['email']} role={result['role']} active={result['is_active']} id={result['id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
