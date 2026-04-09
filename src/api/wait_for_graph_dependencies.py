"""Wait for graph runtime datastores before starting long-lived services."""

from __future__ import annotations

import asyncio
import logging
import os

import asyncpg
import redis.asyncio as redis_async
from neo4j import AsyncGraphDatabase

from src.api.config import settings

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

MAX_ATTEMPTS = int(os.getenv("GRAPH_STARTUP_WAIT_ATTEMPTS", "90"))
RETRY_DELAY_SECONDS = float(os.getenv("GRAPH_STARTUP_WAIT_DELAY_SECONDS", "2"))


async def _probe_postgres() -> str | None:
    try:
        conn = await asyncpg.connect(
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            database=settings.POSTGRES_DB,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            timeout=5,
        )
        try:
            await conn.fetchval("SELECT 1")
        finally:
            await conn.close()
        return None
    except Exception as exc:
        return str(exc)


async def _probe_neo4j() -> str | None:
    driver = AsyncGraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        max_connection_lifetime=60,
        max_connection_pool_size=5,
    )
    try:
        async with driver.session(database=settings.NEO4J_DATABASE) as session:
            result = await session.run("RETURN 1")
            await result.consume()
        return None
    except Exception as exc:
        return str(exc)
    finally:
        await driver.close()


async def _probe_redis() -> str | None:
    client = redis_async.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD,
        db=settings.REDIS_DB,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    try:
        await client.ping()
        return None
    except Exception as exc:
        return str(exc)
    finally:
        await client.aclose()


async def main() -> int:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        postgres_error, neo4j_error, redis_error = await asyncio.gather(
            _probe_postgres(),
            _probe_neo4j(),
            _probe_redis(),
        )
        errors: dict[str, str] = {}
        if postgres_error:
            errors["postgres"] = postgres_error
        if neo4j_error:
            errors["neo4j"] = neo4j_error
        if redis_error:
            errors["redis"] = redis_error

        if not errors:
            logger.info("Graph runtime dependencies are ready")
            return 0

        details = "; ".join(f"{name}: {message}" for name, message in errors.items())
        logger.warning(
            "Dependency check %s/%s failed; retrying in %.1fs (%s)",
            attempt,
            MAX_ATTEMPTS,
            RETRY_DELAY_SECONDS,
            details,
        )

        if attempt == MAX_ATTEMPTS:
            logger.error("Graph runtime dependencies never became ready")
            return 1

        await asyncio.sleep(RETRY_DELAY_SECONDS)

    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
