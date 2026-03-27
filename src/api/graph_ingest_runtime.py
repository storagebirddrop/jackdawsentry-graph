"""
Lightweight runtime dedicated to graph ingestion and raw event-store backfill.

This keeps the standalone graph stack's ingestion service separate from the
request-serving graph API while preserving a small health surface for Docker.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.config import settings
from src.api.database import close_databases
from src.api.database import init_databases

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Boot the collector manager in the background and stop it cleanly."""
    logger.info("Starting Jackdaw Sentry Graph Ingest runtime...")

    collector_manager = None
    collector_task: asyncio.Task | None = None

    try:
        await init_databases()

        from src.api.migrations.migration_manager import run_database_migrations

        migrations_ok = await run_database_migrations(profile="graph")
        if not migrations_ok:
            logger.warning("Graph migrations were not fully applied for ingest runtime")

        from src.collectors.manager import CollectorManager

        collector_manager = CollectorManager()
        await collector_manager.initialize()
        collector_task = asyncio.create_task(collector_manager.start_all())
        app.state.collector_manager = collector_manager
        app.state.collector_task = collector_task
        logger.info("Graph ingest collectors started")
    except Exception:
        logger.exception("Failed to initialize graph ingest runtime")
        if collector_task is not None:
            collector_task.cancel()
            await asyncio.gather(collector_task, return_exceptions=True)
        await close_databases()
        raise

    yield

    logger.info("Shutting down Jackdaw Sentry Graph Ingest runtime...")
    if collector_manager is not None:
        try:
            await collector_manager.stop()
        except Exception:
            logger.exception("Collector manager stop failed during shutdown")
    if collector_task is not None:
        collector_task.cancel()
        await asyncio.gather(collector_task, return_exceptions=True)
    await close_databases()


app = FastAPI(
    title="Jackdaw Sentry Graph Ingest",
    description="Standalone ingestion and backfill runtime for the graph stack",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.get("/health", tags=["Health"])
async def health_check():
    """Basic container health probe."""
    return {
        "status": "healthy",
        "service": "Jackdaw Sentry Graph Ingest",
        "version": "1.0.0",
    }


@app.get("/health/detailed", tags=["Health"])
async def detailed_health_check():
    """Detailed health probe including backing datastore status."""
    from src.api.database import check_database_health

    db_health = await check_database_health()
    manager = getattr(app.state, "collector_manager", None)
    collector_count = len(manager.collectors) if manager is not None else 0
    return {
        "status": "healthy" if all(db_health.values()) else "degraded",
        "service": "Jackdaw Sentry Graph Ingest",
        "version": "1.0.0",
        "databases": db_health,
        "collector_count": collector_count,
    }


@app.get("/", tags=["Root"])
async def root():
    """Runtime metadata."""
    return {
        "name": "Jackdaw Sentry Graph Ingest",
        "health": "/health",
        "details": "/health/detailed",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.api.graph_ingest_runtime:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
