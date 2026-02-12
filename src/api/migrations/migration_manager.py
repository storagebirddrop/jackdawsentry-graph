"""
Jackdaw Sentry - Migration Manager
Database migration management system
"""

import asyncio
import logging
from typing import List, Optional
from pathlib import Path
import asyncpg
from datetime import datetime

from src.api.config import settings

logger = logging.getLogger(__name__)


class MigrationManager:
    """Manages database migrations"""
    
    def __init__(self):
        self.migrations_dir = Path(__file__).parent
        self.applied_migrations = set()
    
    async def get_applied_migrations(self, conn) -> set:
        """Get set of already applied migrations"""
        try:
            # Ensure migrations table exists
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    id SERIAL PRIMARY KEY,
                    filename VARCHAR(255) UNIQUE NOT NULL,
                    applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """)
            
            # Query applied migrations separately
            rows = await conn.fetch(
                "SELECT filename FROM schema_migrations ORDER BY applied_at"
            )
            return {row["filename"] for row in rows}
        except Exception as e:
            logger.error(f"Failed to get applied migrations: {e}")
            return set()
    
    async def mark_migration_applied(self, conn, filename: str):
        """Mark a migration as applied"""
        try:
            await conn.execute("""
                INSERT INTO schema_migrations (filename, applied_at) 
                VALUES ($1, CURRENT_TIMESTAMP)
                ON CONFLICT (filename) DO NOTHING;
            """, filename)
            logger.info(f"Marked migration {filename} as applied")
        except Exception as e:
            logger.error(f"Failed to mark migration {filename} as applied: {e}")
            raise
    
    async def apply_migration(self, conn, filename: str) -> bool:
        """Apply a single migration file"""
        migration_path = self.migrations_dir / filename
        
        if not migration_path.exists():
            logger.error(f"Migration file not found: {migration_path}")
            return False
        
        try:
            # Read migration SQL (small files, run in executor to avoid blocking)
            loop = asyncio.get_event_loop()
            migration_sql = await loop.run_in_executor(
                None, migration_path.read_text, "utf-8"
            )
            
            # Execute migration in a transaction
            async with conn.transaction():
                await conn.execute(migration_sql)
            
            # Mark as applied
            await self.mark_migration_applied(conn, filename)
            
            logger.info(f"Successfully applied migration: {filename}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to apply migration {filename}: {e}")
            return False
    
    async def get_pending_migrations(self) -> List[str]:
        """Get list of pending migrations"""
        migration_files = []
        
        # Get all migration files
        for file_path in self.migrations_dir.glob("*.sql"):
            if file_path.name != "__init__.py":
                migration_files.append(file_path.name)
        
        # Sort by filename to ensure proper order
        migration_files.sort()
        
        return migration_files
    
    async def run_migrations(self, conn):
        """Run all pending migrations"""
        logger.info("Starting database migrations...")
        
        # Get applied migrations
        applied = await self.get_applied_migrations(conn)
        
        # Get pending migrations
        pending = await self.get_pending_migrations()
        
        # Apply pending migrations
        applied_count = 0
        for migration_file in pending:
            if migration_file not in applied:
                success = await self.apply_migration(conn, migration_file)
                if success:
                    applied_count += 1
                    applied.add(migration_file)
                else:
                    logger.error(f"Migration failed: {migration_file}")
                    break  # Stop on first failure
        
        logger.info(f"Applied {applied_count} new migrations")
        return applied_count > 0
    
    async def rollback_migration(self, conn, filename: str) -> bool:
        """Rollback a migration (basic implementation)"""
        logger.warning(f"Rollback requested for {filename} - not implemented")
        return False
    
    async def get_migration_status(self, conn) -> dict:
        """Get current migration status"""
        applied = await self.get_applied_migrations(conn)
        pending = await self.get_pending_migrations()
        
        return {
            "applied_count": len(applied),
            "pending_count": len(pending),
            "applied_migrations": sorted(list(applied)),
            "pending_migrations": pending,
            "needs_migration": len(pending) > 0
        }


# Migration runner function
async def run_database_migrations():
    """Run database migrations"""
    from src.api.database import get_postgres_connection
    
    logger.info("Initializing database migrations...")
    
    try:
        async with get_postgres_connection() as conn:
            # Initialize migration manager
            manager = MigrationManager()
            
            # Run migrations
            success = await manager.run_migrations(conn)
            
            if success:
                logger.info("✅ Database migrations completed successfully")
            else:
                logger.error("❌ Database migrations failed")
                
    except Exception as e:
        logger.error(f"Migration system error: {e}")
        raise


# Create initial migration function
async def create_initial_migration():
    """Create the initial migration if needed"""
    from src.api.database import get_postgres_connection
    
    try:
        async with get_postgres_connection() as conn:
            # Check if migrations table exists
            result = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'schema_migrations'
                );
            """)
            
            if not result:
                logger.info("Creating initial database schema...")
                
                # Read and apply initial schema
                migration_path = Path(__file__).parent / "001_initial_schema.sql"
                if migration_path.exists():
                    with open(migration_path, 'r') as f:
                        schema_sql = f.read()
                    
                    async with conn.transaction():
                        await conn.execute(schema_sql)
                    
                    # Create migrations table
                    await conn.execute("""
                        CREATE TABLE schema_migrations (
                            id SERIAL PRIMARY KEY,
                            filename VARCHAR(255) UNIQUE NOT NULL,
                            applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                        );
                    """)
                    
                    # Mark initial migration as applied
                    await conn.execute("""
                        INSERT INTO schema_migrations (filename, applied_at) 
                        VALUES ('001_initial_schema.sql', CURRENT_TIMESTAMP);
                    """)
                    
                    logger.info("✅ Initial database schema created")
                else:
                    logger.error("Initial schema file not found")
                    
    except Exception as e:
        logger.error(f"Failed to create initial migration: {e}")
        raise
