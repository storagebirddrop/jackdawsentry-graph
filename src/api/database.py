"""
Jackdaw Sentry - Database Connection Management
Handles Neo4j, PostgreSQL, and Redis connections
"""

import asyncio
import logging
from typing import Optional
import asyncpg
from neo4j import AsyncGraphDatabase
import aioredis
from contextlib import asynccontextmanager

from src.api.config import settings

logger = logging.getLogger(__name__)

# Global connection pools
_postgres_pool: Optional[asyncpg.Pool] = None
_neo4j_driver: Optional[AsyncGraphDatabase.driver] = None
_redis_pool: Optional[aioredis.ConnectionPool] = None


async def init_databases():
    """Initialize all database connections"""
    logger.info("Initializing database connections...")
    
    await init_postgres()
    await init_neo4j()
    await init_redis()
    
    logger.info("✅ All database connections initialized")


async def init_postgres():
    """Initialize PostgreSQL connection pool"""
    global _postgres_pool
    
    try:
        _postgres_pool = await asyncpg.create_pool(
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            database=settings.POSTGRES_DB,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            min_size=2,
            max_size=20,
            command_timeout=60
        )
        logger.info("✅ PostgreSQL connection pool initialized")
    except Exception as e:
        logger.error(f"❌ Failed to initialize PostgreSQL: {e}")
        raise


async def init_neo4j():
    """Initialize Neo4j driver"""
    global _neo4j_driver
    
    try:
        _neo4j_driver = AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
            max_connection_lifetime=3600,
            max_connection_pool_size=50
        )
        
        # Test connection
        async with _neo4j_driver.session() as session:
            await session.run("RETURN 1")
        
        logger.info("✅ Neo4j driver initialized")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Neo4j: {e}")
        raise


async def init_redis():
    """Initialize Redis connection pool"""
    global _redis_pool
    
    try:
        _redis_pool = aioredis.ConnectionPool.from_url(
            f"redis://:{settings.REDIS_PASSWORD}@{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}",
            max_connections=20,
            retry_on_timeout=True
        )
        
        # Test connection
        redis = aioredis.Redis(connection_pool=_redis_pool)
        await redis.ping()
        
        logger.info("✅ Redis connection pool initialized")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Redis: {e}")
        raise


def get_postgres_pool() -> asyncpg.Pool:
    """Get PostgreSQL connection pool"""
    if _postgres_pool is None:
        raise RuntimeError("PostgreSQL pool not initialized")
    return _postgres_pool


def get_neo4j_driver() -> AsyncGraphDatabase.driver:
    """Get Neo4j driver"""
    if _neo4j_driver is None:
        raise RuntimeError("Neo4j driver not initialized")
    return _neo4j_driver


def get_redis_pool() -> aioredis.ConnectionPool:
    """Get Redis connection pool"""
    if _redis_pool is None:
        raise RuntimeError("Redis pool not initialized")
    return _redis_pool


@asynccontextmanager
async def get_postgres_connection():
    """Get PostgreSQL connection from pool"""
    async with get_postgres_pool().acquire() as conn:
        yield conn


@asynccontextmanager
async def get_neo4j_session():
    """Get Neo4j session from driver"""
    async with get_neo4j_driver().session() as session:
        yield session


@asynccontextmanager
async def get_redis_connection():
    """Get Redis connection from pool"""
    redis = aioredis.Redis(connection_pool=get_redis_pool())
    yield redis


async def close_databases():
    """Close all database connections"""
    logger.info("Closing database connections...")
    
    if _postgres_pool:
        await _postgres_pool.close()
        logger.info("✅ PostgreSQL pool closed")
    
    if _neo4j_driver:
        await _neo4j_driver.close()
        logger.info("✅ Neo4j driver closed")
    
    if _redis_pool:
        await _redis_pool.disconnect()
        logger.info("✅ Redis pool closed")
    
    logger.info("✅ All database connections closed")


async def check_database_health() -> dict:
    """Check health of all database connections"""
    health_status = {
        "postgres": False,
        "neo4j": False,
        "redis": False
    }
    
    # Check PostgreSQL
    try:
        async with get_postgres_connection() as conn:
            await conn.fetchval("SELECT 1")
        health_status["postgres"] = True
    except Exception as e:
        logger.error(f"PostgreSQL health check failed: {e}")
    
    # Check Neo4j
    try:
        async with get_neo4j_session() as session:
            await session.run("RETURN 1")
        health_status["neo4j"] = True
    except Exception as e:
        logger.error(f"Neo4j health check failed: {e}")
    
    # Check Redis
    try:
        async with get_redis_connection() as redis:
            await redis.ping()
        health_status["redis"] = True
    except Exception as e:
        logger.error(f"Redis health check failed: {e}")
    
    return health_status


# Database utility functions
async def execute_postgres_query(query: str, *args, fetch: str = "all"):
    """Execute PostgreSQL query with error handling"""
    try:
        async with get_postgres_connection() as conn:
            if fetch == "all":
                return await conn.fetch(query, *args)
            elif fetch == "one":
                return await conn.fetchrow(query, *args)
            elif fetch == "val":
                return await conn.fetchval(query, *args)
            else:
                return await conn.execute(query, *args)
    except Exception as e:
        logger.error(f"PostgreSQL query failed: {query}, Error: {e}")
        raise


async def execute_neo4j_query(query: str, **kwargs):
    """Execute Neo4j query with error handling"""
    try:
        async with get_neo4j_session() as session:
            result = await session.run(query, **kwargs)
            return [record.data() for record in result]
    except Exception as e:
        logger.error(f"Neo4j query failed: {query}, Error: {e}")
        raise


async def execute_redis_command(command: str, *args):
    """Execute Redis command with error handling"""
    try:
        async with get_redis_connection() as redis:
            return await redis.execute_command(command, *args)
    except Exception as e:
        logger.error(f"Redis command failed: {command}, Error: {e}")
        raise


# Transaction management
async def postgres_transaction(queries: list):
    """Execute multiple PostgreSQL queries in a transaction"""
    async with get_postgres_connection() as conn:
        async with conn.transaction():
            results = []
            for query, args in queries:
                if len(args) == 2 and args[1] in ["all", "one", "val"]:
                    result = await conn.fetch(query, *args[0]) if args[1] == "all" else \
                            await conn.fetchrow(query, *args[0]) if args[1] == "one" else \
                            await conn.fetchval(query, *args[0])
                else:
                    result = await conn.execute(query, *args)
                results.append(result)
            return results


async def neo4j_transaction(queries: list):
    """Execute multiple Neo4j queries in a transaction"""
    async with get_neo4j_session() as session:
        async with session.begin_transaction() as tx:
            results = []
            for query, kwargs in queries:
                result = await tx.run(query, **kwargs)
                results.append([record.data() for record in result])
            return results


# Cache utilities
async def cache_get(key: str) -> Optional[str]:
    """Get value from Redis cache"""
    try:
        async with get_redis_connection() as redis:
            return await redis.get(key)
    except Exception as e:
        logger.error(f"Cache get failed for key {key}: {e}")
        return None


async def cache_set(key: str, value: str, ttl: int = None):
    """Set value in Redis cache"""
    try:
        async with get_redis_connection() as redis:
            if ttl:
                await redis.setex(key, ttl, value)
            else:
                await redis.set(key, value)
    except Exception as e:
        logger.error(f"Cache set failed for key {key}: {e}")


async def cache_delete(key: str):
    """Delete value from Redis cache"""
    try:
        async with get_redis_connection() as redis:
            await redis.delete(key)
    except Exception as e:
        logger.error(f"Cache delete failed for key {key}: {e}")


# Connection monitoring
async def monitor_connections():
    """Monitor database connection health"""
    while True:
        try:
            health = await check_database_health()
            
            if not all(health.values()):
                logger.warning(f"Database health issues detected: {health}")
                
                # Attempt to reconnect failed connections
                if not health["postgres"]:
                    await init_postgres()
                if not health["neo4j"]:
                    await init_neo4j()
                if not health["redis"]:
                    await init_redis()
            
            await asyncio.sleep(60)  # Check every minute
            
        except Exception as e:
            logger.error(f"Connection monitoring error: {e}")
            await asyncio.sleep(30)  # Retry in 30 seconds


# Background task starter
async def start_connection_monitoring():
    """Start background connection monitoring"""
    asyncio.create_task(monitor_connections())
    logger.info("Started database connection monitoring")
