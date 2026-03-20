"""
Dedicated ingestion runtime for the standalone graph product.

This process keeps live collectors and raw-event-store backfill outside the
request-serving graph API so the lightweight investigation UI/API stack stays
lean by default.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress

from src.api.config import settings
from src.api.database import close_databases
from src.api.database import init_databases
from src.api.database import start_connection_monitoring
from src.api.database import stop_connection_monitoring
from src.collectors.manager import CollectorManager

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    """Install SIGINT/SIGTERM handlers that trigger graceful shutdown."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)


async def _shutdown_runtime(manager: CollectorManager | None) -> None:
    """Close collector/runtime resources gracefully."""
    errors: list[Exception] = []

    if manager is not None:
        try:
            await manager.stop_all()
        except Exception as exc:  # pragma: no cover - shutdown best effort
            logger.error("Failed to stop collector manager cleanly: %s", exc, exc_info=True)
            errors.append(exc)

    try:
        await stop_connection_monitoring()
    except Exception as exc:  # pragma: no cover - shutdown best effort
        logger.error("Failed to stop connection monitoring: %s", exc, exc_info=True)
        errors.append(exc)

    try:
        from src.collectors.rpc.factory import close_all_clients

        await close_all_clients()
    except Exception as exc:  # pragma: no cover - shutdown best effort
        logger.error("Failed to close RPC clients: %s", exc, exc_info=True)
        errors.append(exc)

    try:
        await close_databases()
    except Exception as exc:  # pragma: no cover - shutdown best effort
        logger.error("Failed to close databases: %s", exc, exc_info=True)
        errors.append(exc)

    if errors:
        logger.warning("Graph ingest runtime shutdown completed with %s error(s)", len(errors))
    else:
        logger.info("Graph ingest runtime shutdown complete")


async def run_graph_ingest_runtime(
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run the dedicated collector/backfill runtime until a stop signal arrives."""
    local_stop_event = stop_event or asyncio.Event()
    if stop_event is None:
        _install_signal_handlers(local_stop_event)

    manager: CollectorManager | None = None
    manager_task: asyncio.Task[None] | None = None

    logger.info("Starting Jackdaw Sentry Graph ingest runtime...")
    if not settings.DUAL_WRITE_RAW_EVENT_STORE:
        logger.warning(
            "DUAL_WRITE_RAW_EVENT_STORE is disabled; collectors will not populate raw event tables",
        )
    if not settings.AUTO_BACKFILL_RAW_EVENT_STORE:
        logger.warning(
            "AUTO_BACKFILL_RAW_EVENT_STORE is disabled; only live collector ingestion will run",
        )

    try:
        await init_databases()

        from src.api.migrations.migration_manager import run_database_migrations

        migrations_ok = await run_database_migrations(profile="graph")
        if not migrations_ok:
            logger.warning("Graph migrations were not fully applied for the ingest runtime")

        await start_connection_monitoring()

        manager = CollectorManager()
        await manager.initialize()

        manager_task = asyncio.create_task(manager.start_all())
        await local_stop_event.wait()
    except asyncio.CancelledError:
        raise
    finally:
        if manager is not None:
            await _shutdown_runtime(manager)
        if manager_task is not None:
            manager_task.cancel()
            with suppress(asyncio.CancelledError):
                await manager_task


def main() -> None:
    """Entry point for ``python -m src.api.graph_ingest_runtime``."""
    try:
        asyncio.run(run_graph_ingest_runtime())
    except KeyboardInterrupt:  # pragma: no cover - normal CLI shutdown path
        logger.info("Graph ingest runtime interrupted")


if __name__ == "__main__":
    main()
