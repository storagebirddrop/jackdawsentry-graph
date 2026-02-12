"""
Jackdaw Sentry - Database Connection Management
Handles Neo4j, PostgreSQL, and Redis connections
"""

import asyncio
import logging
from typing import Any, List, Optional
import asyncpg
from neo4j import AsyncGraphDatabase
import redis.asyncio as redis_async
from contextlib import asynccontextmanager

from src.api.config import settings

logger = logging.getLogger(__name__)

# Global connection pools
_postgres_pool: Optional[asyncpg.Pool] = None
_neo4j_driver: Optional[AsyncGraphDatabase.driver] = None
_redis_pool: Optional[redis_async.ConnectionPool] = None

# Initialization lock to prevent race conditions
_init_lock = asyncio.Lock()
_init_event = asyncio.Event()
_initialized = False


async def init_databases():
    """Initialize all database connections with thread safety"""
    global _initialized
    
    # If already initialized, just wait for completion
    if _initialized:
        await _init_event.wait()
        return
    
    # Use async lock to prevent concurrent initialization
    async with _init_lock:
        if _initialized:
            await _init_event.wait()
            return
        
        try:
            logger.info("Initializing database connections...")
            
            # Initialize all databases
            await init_postgres()
            await init_neo4j()
            await init_redis()
            
            _initialized = True
            _init_event.set()
            
            logger.info("✅ All database connections initialized")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize databases: {e}")
            # Reset event to allow retry
            _init_event.clear()
            raise


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
        _redis_pool = redis_async.ConnectionPool.from_url(
            f"redis://:{settings.REDIS_PASSWORD}@{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}",
            max_connections=20,
            retry_on_timeout=True
        )
        
        # Test connection
        redis = redis_async.Redis(connection_pool=_redis_pool)
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


def get_redis_pool() -> redis_async.ConnectionPool:
    """Get Redis connection pool"""
    if _redis_pool is None:
        raise RuntimeError("Redis pool not initialized")
    return _redis_pool


@asynccontextmanager
async def get_postgres_connection():
    """Get PostgreSQL connection from pool with connection reuse optimization"""
    async with get_postgres_pool().acquire() as conn:
        # Set connection parameters for better performance
        await conn.execute("SET statement_timeout = '60s'")
        yield conn


@asynccontextmanager
async def get_neo4j_session():
    """Get Neo4j session from driver with session reuse optimization"""
    async with get_neo4j_driver().session() as session:
        # Configure session for better performance
        yield session


@asynccontextmanager
async def get_redis_connection():
    """Get Redis connection from pool with connection reuse optimization"""
    redis = redis_async.Redis(connection_pool=get_redis_pool())
    try:
        yield redis
    finally:
        # Connection is returned to pool automatically
        pass


async def close_databases():
    """Close all database connections and monitoring"""
    logger.info("Closing database connections...")
    
    # Stop connection monitoring first
    await stop_connection_monitoring()
    
    if _postgres_pool:
        await _postgres_pool.close()
        logger.info("✅ PostgreSQL pool closed")
    
    if _neo4j_driver:
        await _neo4j_driver.close()
        logger.info("✅ Neo4j driver closed")
    
    if _redis_pool:
        await _redis_pool.disconnect()
        logger.info("✅ Redis pool closed")
    
    # Reset initialization state
    global _initialized
    _initialized = False
    _init_event.clear()
    
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
    """Execute multiple PostgreSQL queries in a transaction with proper rollback"""
    async with get_postgres_connection() as conn:
        async with conn.transaction():
            results = []
            try:
                for query, args in queries:
                    if len(args) == 2 and args[1] in ["all", "one", "val"]:
                        result = await conn.fetch(query, *args[0]) if args[1] == "all" else \
                                await conn.fetchrow(query, *args[0]) if args[1] == "one" else \
                                await conn.fetchval(query, *args[0])
                    else:
                        result = await conn.execute(query, *args)
                    results.append(result)
                
                logger.info(f"PostgreSQL transaction completed: {len(queries)} queries executed")
                return results
                
            except Exception as e:
                logger.error(f"PostgreSQL transaction failed, rolling back: {e}")
                # Transaction will automatically rollback on exception
                raise


async def neo4j_transaction(queries: list):
    """Execute multiple Neo4j queries in a transaction with proper rollback"""
    async with get_neo4j_session() as session:
        async with session.begin_transaction() as tx:
            results = []
            try:
                for query, kwargs in queries:
                    result = await tx.run(query, **kwargs)
                    results.append([record.data() for record in result])
                
                # Commit the transaction
                await tx.commit()
                logger.info(f"Neo4j transaction completed: {len(queries)} queries executed")
                return results
                
            except Exception as e:
                logger.error(f"Neo4j transaction failed, rolling back: {e}")
                # Rollback the transaction
                await tx.rollback()
                raise


async def postgres_transaction_with_rollback(queries: list, rollback_callback=None):
    """Execute PostgreSQL transaction with custom rollback handling"""
    async with get_postgres_connection() as conn:
        async with conn.transaction():
            results = []
            executed_queries = []
            
            try:
                for i, (query, args) in enumerate(queries):
                    if len(args) == 2 and args[1] in ["all", "one", "val"]:
                        result = await conn.fetch(query, *args[0]) if args[1] == "all" else \
                                await conn.fetchrow(query, *args[0]) if args[1] == "one" else \
                                await conn.fetchval(query, *args[0])
                    else:
                        result = await conn.execute(query, *args)
                    
                    results.append(result)
                    executed_queries.append((query, args))
                
                logger.info(f"PostgreSQL transaction completed: {len(queries)} queries executed")
                return results
                
            except Exception as e:
                logger.error(f"PostgreSQL transaction failed: {e}")
                
                # Execute rollback callback if provided
                if rollback_callback:
                    try:
                        await rollback_callback(executed_queries, e)
                    except Exception as callback_error:
                        logger.error(f"Rollback callback failed: {callback_error}")
                
                # Transaction will automatically rollback
                raise


async def neo4j_transaction_with_rollback(queries: list, rollback_callback=None):
    """Execute Neo4j transaction with custom rollback handling"""
    async with get_neo4j_session() as session:
        async with session.begin_transaction() as tx:
            results = []
            executed_queries = []
            
            try:
                for query, kwargs in queries:
                    result = await tx.run(query, **kwargs)
                    results.append([record.data() for record in result])
                    executed_queries.append((query, kwargs))
                
                # Commit the transaction
                await tx.commit()
                logger.info(f"Neo4j transaction completed: {len(queries)} queries executed")
                return results
                
            except Exception as e:
                logger.error(f"Neo4j transaction failed: {e}")
                
                # Execute rollback callback if provided
                if rollback_callback:
                    try:
                        await rollback_callback(executed_queries, e)
                    except Exception as callback_error:
                        logger.error(f"Rollback callback failed: {callback_error}")
                
                # Rollback the transaction
                await tx.rollback()
                raise


# Cache utilities with invalidation strategy
_cache_invalidation_callbacks = {}

_CACHE_DEPS_PREFIX = "cache:deps:"   # cache:deps:{cache_key} -> SET of dependencies
_CACHE_RDEPS_PREFIX = "cache:rdeps:"  # cache:rdeps:{dep}       -> SET of cache keys


async def cache_get(key: str) -> Optional[str]:
    """Get value from Redis cache"""
    try:
        async with get_redis_connection() as redis:
            return await redis.get(key)
    except Exception as e:
        logger.error(f"Cache get failed for key {key}: {e}")
        return None


async def cache_set(key: str, value: str, ttl: int = None, dependencies: List[str] = None):
    """Set value in Redis cache with optional dependencies"""
    try:
        async with get_redis_connection() as redis:
            if ttl:
                await redis.setex(key, ttl, value)
            else:
                await redis.set(key, value)

            # Track dependencies in Redis sets
            if dependencies:
                deps_key = f"{_CACHE_DEPS_PREFIX}{key}"
                await redis.sadd(deps_key, *dependencies)
                # Maintain reverse index: dep -> cache keys
                for dep in dependencies:
                    await redis.sadd(f"{_CACHE_RDEPS_PREFIX}{dep}", key)

    except Exception as e:
        logger.error(f"Cache set failed for key {key}: {e}")


async def cache_delete(key: str):
    """Delete value from Redis cache and clean up dependency sets"""
    try:
        async with get_redis_connection() as redis:
            await redis.delete(key)

            # Clean up dependency tracking
            deps_key = f"{_CACHE_DEPS_PREFIX}{key}"
            deps = await redis.smembers(deps_key)
            for dep in deps:
                dep_str = dep.decode() if isinstance(dep, bytes) else dep
                await redis.srem(f"{_CACHE_RDEPS_PREFIX}{dep_str}", key)
            await redis.delete(deps_key)

    except Exception as e:
        logger.error(f"Cache delete failed for key {key}: {e}")


async def cache_invalidate_pattern(pattern: str):
    """Invalidate cache keys matching pattern using async SCAN"""
    try:
        async with get_redis_connection() as redis:
            all_deleted: List[str] = []
            batch: List[str] = []
            async for raw_key in redis.scan_iter(match=pattern, count=100):
                key_str = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
                batch.append(key_str)
                if len(batch) >= 100:
                    await redis.delete(*batch)
                    all_deleted.extend(batch)
                    batch = []
            if batch:
                await redis.delete(*batch)
                all_deleted.extend(batch)

            if all_deleted:
                logger.info(f"Invalidated {len(all_deleted)} cache keys matching pattern: {pattern}")

            # Clean up dependency tracking for all deleted keys
            for key_str in all_deleted:
                deps_key = f"{_CACHE_DEPS_PREFIX}{key_str}"
                deps = await redis.smembers(deps_key)
                for dep in deps:
                    dep_s = dep.decode() if isinstance(dep, bytes) else dep
                    await redis.srem(f"{_CACHE_RDEPS_PREFIX}{dep_s}", key_str)
                await redis.delete(deps_key)

    except Exception as e:
        logger.error(f"Cache pattern invalidation failed for pattern {pattern}: {e}")


async def cache_invalidate_dependencies(dependency: str):
    """Invalidate all cache keys that depend on a specific dependency"""
    try:
        async with get_redis_connection() as redis:
            rdeps_key = f"{_CACHE_RDEPS_PREFIX}{dependency}"
            raw_members = await redis.smembers(rdeps_key)
            if not raw_members:
                return

            keys_to_invalidate = [
                m.decode() if isinstance(m, bytes) else m for m in raw_members
            ]
            logger.info(f"Invalidating {len(keys_to_invalidate)} cache keys for dependency: {dependency}")

            # Delete the cached values
            await redis.delete(*keys_to_invalidate)

            # Clean up forward dependency sets and reverse entries
            for cache_key in keys_to_invalidate:
                deps_key = f"{_CACHE_DEPS_PREFIX}{cache_key}"
                deps = await redis.smembers(deps_key)
                for dep in deps:
                    dep_s = dep.decode() if isinstance(dep, bytes) else dep
                    await redis.srem(f"{_CACHE_RDEPS_PREFIX}{dep_s}", cache_key)
                await redis.delete(deps_key)

            # Remove the reverse index entry itself
            await redis.delete(rdeps_key)

    except Exception as e:
        logger.error(f"Cache dependency invalidation failed for {dependency}: {e}")


async def cache_invalidate_address(address: str):
    """Invalidate all cache entries related to a specific address"""
    patterns = [
        f"address:{address}:*",
        f"analysis:{address}:*",
        f"compliance:{address}:*",
        f"transaction:*:{address}:*",
        f"transaction:*:*:{address}"  # from/to address
    ]
    
    for pattern in patterns:
        await cache_invalidate_pattern(pattern)
    
    # Also invalidate by dependency
    await cache_invalidate_dependencies(f"address:{address}")


async def cache_invalidate_transaction(tx_hash: str):
    """Invalidate all cache entries related to a specific transaction"""
    patterns = [
        f"transaction:{tx_hash}:*",
        f"block:*:transaction:{tx_hash}",
        f"address:*:transaction:{tx_hash}"
    ]
    
    for pattern in patterns:
        await cache_invalidate_pattern(pattern)
    
    # Also invalidate by dependency
    await cache_invalidate_dependencies(f"transaction:{tx_hash}")


async def cache_invalidate_blockchain(blockchain: str):
    """Invalidate all cache entries for a specific blockchain"""
    patterns = [
        f"{blockchain}:*",
        f"blockchain:{blockchain}:*",
        f"node:{blockchain}:*",
        f"stats:{blockchain}:*"
    ]
    
    for pattern in patterns:
        await cache_invalidate_pattern(pattern)
    
    # Also invalidate by dependency
    await cache_invalidate_dependencies(f"blockchain:{blockchain}")


def register_cache_invalidation_callback(event_type: str, callback):
    """Register callback for cache invalidation events"""
    _cache_invalidation_callbacks[event_type] = callback


async def trigger_cache_invalidation_event(event_type: str, data: Any):
    """Trigger cache invalidation event"""
    if event_type in _cache_invalidation_callbacks:
        try:
            await _cache_invalidation_callbacks[event_type](data)
        except Exception as e:
            logger.error(f"Cache invalidation callback failed for {event_type}: {e}")


# Default invalidation callbacks
async def _on_address_updated(address_data: dict):
    """Handle address update invalidation"""
    address = address_data.get('address')
    if address:
        await cache_invalidate_address(address)


async def _on_transaction_updated(tx_data: dict):
    """Handle transaction update invalidation"""
    tx_hash = tx_data.get('transaction_hash')
    if tx_hash:
        await cache_invalidate_transaction(tx_hash)
        
        # Also invalidate related addresses
        from_address = tx_data.get('from_address')
        to_address = tx_data.get('to_address')
        
        if from_address:
            await cache_invalidate_address(from_address)
        if to_address:
            await cache_invalidate_address(to_address)


async def _on_blockchain_updated(blockchain_data: dict):
    """Handle blockchain update invalidation"""
    blockchain = blockchain_data.get('blockchain')
    if blockchain:
        await cache_invalidate_blockchain(blockchain)


# Register default callbacks
register_cache_invalidation_callback('address_updated', _on_address_updated)
register_cache_invalidation_callback('transaction_updated', _on_transaction_updated)
register_cache_invalidation_callback('blockchain_updated', _on_blockchain_updated)


# Connection monitoring
_monitoring_task: Optional[asyncio.Task] = None
_monitoring_active = False


async def monitor_connections():
    """Monitor database connection health with proper cleanup"""
    global _monitoring_active
    
    while _monitoring_active:
        try:
            health = await check_database_health()
            
            if not all(health.values()):
                logger.warning(f"Database health issues detected: {health}")
                
                # Attempt to reconnect failed connections
                if not health["postgres"]:
                    logger.info("Attempting to reconnect PostgreSQL...")
                    await init_postgres()
                if not health["neo4j"]:
                    logger.info("Attempting to reconnect Neo4j...")
                    await init_neo4j()
                if not health["redis"]:
                    logger.info("Attempting to reconnect Redis...")
                    await init_redis()
            
            # Check if monitoring should continue
            if not _monitoring_active:
                break
                
            await asyncio.sleep(60)  # Check every minute
            
        except Exception as e:
            logger.error(f"Connection monitoring error: {e}")
            if _monitoring_active:
                await asyncio.sleep(30)  # Retry in 30 seconds
            else:
                break
    
    logger.info("Connection monitoring stopped")


async def start_connection_monitoring():
    """Start background connection monitoring with proper task management"""
    global _monitoring_task, _monitoring_active
    
    if _monitoring_task and not _monitoring_task.done():
        logger.warning("Connection monitoring is already running")
        return
    
    _monitoring_active = True
    _monitoring_task = asyncio.create_task(monitor_connections())
    logger.info("Started database connection monitoring")


async def stop_connection_monitoring():
    """Stop connection monitoring and clean up resources"""
    global _monitoring_active, _monitoring_task
    
    _monitoring_active = False
    
    if _monitoring_task:
        _monitoring_task.cancel()
        try:
            await _monitoring_task
        except asyncio.CancelledError:
            pass
        _monitoring_task = None
    
    logger.info("Stopped database connection monitoring")
