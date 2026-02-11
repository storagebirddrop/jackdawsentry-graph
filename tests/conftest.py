"""
Jackdaw Sentry - Test Configuration
Pytest fixtures and configuration for all tests
"""

import pytest
import asyncio
import os
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator
import asyncpg
from neo4j import AsyncGraphDatabase
import aioredis
from fastapi.testclient import TestClient
from httpx import AsyncClient

from src.api.main import app
from src.api.config import settings
from src.api.database import get_postgres_connection, get_neo4j_session, get_redis_connection


# =============================================================================
# Test Configuration
# =============================================================================
TEST_DATABASE_URL = "postgresql://test_user:test_pass@localhost:5433/test_jackdawsentry"
TEST_NEO4J_URI = "bolt://neo4j:test_pass@localhost:7688"
TEST_REDIS_URL = "redis://localhost:6380/1"

# Override settings for testing
settings.DATABASE_URL = TEST_DATABASE_URL
settings.NEO4J_URI = TEST_NEO4J_URI
settings.REDIS_URL = TEST_REDIS_URL
settings.LOG_LEVEL = "DEBUG"
settings.API_SECRET_KEY = "test-secret-key-for-testing-only"
settings.ENCRYPTION_KEY = "test-encryption-key-32-chars-long"
settings.JWT_SECRET_KEY = "test-jwt-secret-key-for-testing"


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def setup_test_databases() -> AsyncGenerator[None, None]:
    """Setup test databases and clean up after tests."""
    # Create temporary directories for test data
    test_dirs = []
    
    try:
        # Setup test databases
        await setup_postgres_test_db()
        await setup_neo4j_test_db()
        await setup_redis_test_db()
        
        yield
        
    finally:
        # Cleanup
        await cleanup_test_databases()
        
        # Remove temporary directories
        for temp_dir in test_dirs:
            if os.path.exists(temp_dir):
                os.rmdir(temp_dir)


async def setup_postgres_test_db():
    """Setup PostgreSQL test database."""
    try:
        # Connect to default database and create test database
        conn = await asyncpg.connect(
            host="localhost",
            port=5433,
            user="test_user",
            password="test_pass",
            database="postgres"
        )
        
        # Drop test database if exists
        await conn.execute("DROP DATABASE IF EXISTS test_jackdawsentry")
        
        # Create test database
        await conn.execute("CREATE DATABASE test_jackdawsentry")
        
        await conn.close()
        
        # Run migrations on test database
        await run_test_migrations()
        
    except Exception as e:
        print(f"Failed to setup PostgreSQL test database: {e}")


async def setup_neo4j_test_db():
    """Setup Neo4j test database."""
    try:
        # Connect to Neo4j and clear data
        driver = AsyncGraphDatabase.driver(
            "bolt://localhost:7688",
            auth=("neo4j", "test_pass")
        )
        
        async with driver.session() as session:
            # Clear all data
            await session.run("MATCH (n) DETACH DELETE n")
            
            # Create constraints
            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (a:Address) REQUIRE a.address IS UNIQUE")
            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (t:Transaction) REQUIRE t.hash IS UNIQUE")
        
        await driver.close()
        
    except Exception as e:
        print(f"Failed to setup Neo4j test database: {e}")


async def setup_redis_test_db():
    """Setup Redis test database."""
    try:
        # Connect to Redis and clear data
        redis = aioredis.from_url("redis://localhost:6380/1", decode_responses=True)
        
        # Clear all data
        await redis.flushdb()
        
        await redis.close()
        
    except Exception as e:
        print(f"Failed to setup Redis test database: {e}")


async def run_test_migrations():
    """Run database migrations on test database."""
    try:
        conn = await asyncpg.connect(TEST_DATABASE_URL)
        
        # Read and apply initial schema
        migration_path = Path(__file__).parent.parent / "src/api/migrations/001_initial_schema.sql"
        if migration_path.exists():
            with open(migration_path, 'r') as f:
                schema_sql = f.read()
            
            await conn.execute(schema_sql)
        
        await conn.close()
        
    except Exception as e:
        print(f"Failed to run test migrations: {e}")


async def cleanup_test_databases():
    """Clean up test databases."""
    try:
        # Cleanup PostgreSQL
        conn = await asyncpg.connect(
            host="localhost",
            port=5433,
            user="test_user",
            password="test_pass",
            database="postgres"
        )
        await conn.execute("DROP DATABASE IF EXISTS test_jackdawsentry")
        await conn.close()
        
        # Cleanup Neo4j
        driver = AsyncGraphDatabase.driver(
            "bolt://localhost:7688",
            auth=("neo4j", "test_pass")
        )
        async with driver.session() as session:
            await session.run("MATCH (n) DETACH DELETE n")
        await driver.close()
        
        # Cleanup Redis
        redis = aioredis.from_url("redis://localhost:6380/1", decode_responses=True)
        await redis.flushdb()
        await redis.close()
        
    except Exception as e:
        print(f"Failed to cleanup test databases: {e}")


# =============================================================================
# Database Fixtures
# =============================================================================
@pytest.fixture
async def postgres_connection() -> AsyncGenerator[asyncpg.Connection, None]:
    """Provide a PostgreSQL connection for tests."""
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    yield conn
    await conn.close()


@pytest.fixture
async def neo4j_session() -> AsyncGenerator[AsyncGraphDatabase.Session, None]:
    """Provide a Neo4j session for tests."""
    driver = AsyncGraphDatabase.driver(
        "bolt://localhost:7688",
        auth=("neo4j", "test_pass")
    )
    async with driver.session() as session:
        yield session
    await driver.close()


@pytest.fixture
async def redis_connection() -> AsyncGenerator[aioredis.Redis, None]:
    """Provide a Redis connection for tests."""
    redis = aioredis.from_url("redis://localhost:6380/1", decode_responses=True)
    yield redis
    await redis.close()


# =============================================================================
# API Client Fixtures
# =============================================================================
@pytest.fixture
def client() -> TestClient:
    """Provide a FastAPI test client."""
    return TestClient(app)


@pytest.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Provide an async FastAPI test client."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client


# =============================================================================
# Authentication Fixtures
# =============================================================================
@pytest.fixture
def test_user_token() -> str:
    """Provide a test user JWT token."""
    import jwt
    from datetime import datetime, timedelta
    
    payload = {
        "sub": "test_user",
        "exp": datetime.utcnow() + timedelta(hours=1),
        "role": "analyst",
        "permissions": ["read", "write"]
    }
    
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")


@pytest.fixture
def test_admin_token() -> str:
    """Provide a test admin JWT token."""
    import jwt
    from datetime import datetime, timedelta
    
    payload = {
        "sub": "test_admin",
        "exp": datetime.utcnow() + timedelta(hours=1),
        "role": "admin",
        "permissions": ["read", "write", "admin"]
    }
    
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")


@pytest.fixture
def auth_headers(test_user_token: str) -> dict:
    """Provide authentication headers for tests."""
    return {"Authorization": f"Bearer {test_user_token}"}


@pytest.fixture
def admin_headers(test_admin_token: str) -> dict:
    """Provide admin authentication headers for tests."""
    return {"Authorization": f"Bearer {test_admin_token}"}


# =============================================================================
# Test Data Fixtures
# =============================================================================
@pytest.fixture
def sample_transaction_data() -> dict:
    """Provide sample transaction data for tests."""
    return {
        "transaction_hash": "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        "blockchain": "ethereum",
        "block_number": 12345678,
        "from_address": "0x1234567890abcdef1234567890abcdef12345678",
        "to_address": "0x876543210fedcba9876543210fedcba987654321",
        "amount": "1.5",
        "amount_usd": "3000.00",
        "gas_used": 21000,
        "gas_price": "20.0",
        "timestamp": "2024-01-01T12:00:00Z"
    }


@pytest.fixture
def sample_address_data() -> dict:
    """Provide sample address data for tests."""
    return {
        "address": "0x1234567890abcdef1234567890abcdef12345678",
        "blockchain": "ethereum",
        "label": "Test Address",
        "risk_score": 75.5,
        "tags": ["suspicious", "exchange"]
    }


@pytest.fixture
def sample_investigation_data() -> dict:
    """Provide sample investigation data for tests."""
    return {
        "case_number": "INV-2024-001",
        "title": "Test Investigation",
        "description": "Test investigation description",
        "priority": "high",
        "blockchain": "ethereum",
        "tags": ["suspicious", "aml"]
    }


# =============================================================================
# Mock Fixtures
# =============================================================================
@pytest.fixture
def mock_blockchain_collector():
    """Mock blockchain collector for tests."""
    from unittest.mock import Mock, AsyncMock
    
    collector = Mock()
    collector.collect_transactions = AsyncMock(return_value=[])
    collector.collect_blocks = AsyncMock(return_value=[])
    collector.get_address_balance = AsyncMock(return_value="1.0")
    collector.get_transaction_details = AsyncMock(return_value={})
    
    return collector


@pytest.fixture
def mock_analysis_engine():
    """Mock analysis engine for tests."""
    from unittest.mock import Mock, AsyncMock
    
    engine = Mock()
    engine.analyze_transaction = AsyncMock(return_value={"risk_score": 50.0})
    engine.analyze_address = AsyncMock(return_value={"risk_score": 75.0})
    engine.detect_patterns = AsyncMock(return_value=[])
    
    return engine


# =============================================================================
# Utility Functions
# =============================================================================
@pytest.fixture
def temp_file() -> Generator[str, None, None]:
    """Provide a temporary file for tests."""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        yield f.name
    os.unlink(f.name)


@pytest.fixture
def temp_dir() -> Generator[str, None, None]:
    """Provide a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as d:
        yield d


# =============================================================================
# Performance Testing Fixtures
# =============================================================================
@pytest.fixture
def performance_timer() -> Generator[dict, None, None]:
    """Provide a simple performance timer for tests."""
    import time
    
    timer = {"start": None, "end": None, "duration": None}
    
    def start():
        timer["start"] = time.time()
    
    def stop():
        timer["end"] = time.time()
        timer["duration"] = timer["end"] - timer["start"]
    
    timer["start"] = start
    timer["stop"] = stop
    
    yield timer
