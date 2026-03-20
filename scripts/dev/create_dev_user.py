#!/usr/bin/env python3
"""Create or update a local graph-runtime user."""

from __future__ import annotations

import argparse
import asyncio

from src.api.auth import hash_password
from src.api.database import close_databases
from src.api.database import get_postgres_connection
from src.api.database import init_postgres


async def create_user(args: argparse.Namespace) -> None:
    await init_postgres()
    try:
        async with get_postgres_connection() as conn:
            await conn.execute(
                """
                INSERT INTO users (
                    username,
                    email,
                    password_hash,
                    full_name,
                    role,
                    is_active,
                    gdpr_consent_given
                )
                VALUES ($1, $2, $3, $4, $5, TRUE, FALSE)
                ON CONFLICT (username) DO UPDATE
                SET
                    email = EXCLUDED.email,
                    password_hash = EXCLUDED.password_hash,
                    full_name = EXCLUDED.full_name,
                    role = EXCLUDED.role,
                    is_active = TRUE
                """,
                args.username,
                args.email,
                hash_password(args.password),
                args.full_name,
                args.role,
            )
    finally:
        await close_databases()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--email", default="analyst@jackdawsentry.local")
    parser.add_argument("--full-name", default="Graph Analyst", dest="full_name")
    parser.add_argument("--role", default="analyst")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(create_user(parse_args()))
