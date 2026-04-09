"""
Lightweight runtime dedicated to graph ingestion and raw event-store backfill.

This keeps the standalone graph stack's ingestion service separate from the
request-serving graph API while preserving a small health surface for Docker.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from datetime import timezone
from queue import Empty
from typing import Any
from typing import Callable

from fastapi import FastAPI
from fastapi import Response
from fastapi import status

from src.api.config import settings
from src.api.database import close_databases
from src.api.database import init_databases
from src.api.database import start_connection_monitoring
from src.api.database import stop_connection_monitoring

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

RuntimeStateCallback = Callable[[dict[str, Any]], None]


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_runtime_state(
    *,
    status_value: str,
    ready: bool,
    collector_count: int = 0,
    running_collectors: int = 0,
    last_error: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status_value,
        "ready": ready,
        "collector_count": collector_count,
        "running_collectors": running_collectors,
        "last_error": last_error,
        "last_update": _iso_now(),
    }


async def run_graph_ingest_runtime(
    stop_event: asyncio.Event,
    *,
    state_callback: RuntimeStateCallback | None = None,
):
    """Run collectors/backfill until ``stop_event`` is set."""
    from src.api.migrations.migration_manager import run_database_migrations
    from src.collectors.manager import CollectorManager
    from src.collectors.rpc.factory import close_all_clients

    collector_manager = None
    collector_task: asyncio.Task | None = None
    stop_wait_task: asyncio.Task | None = None
    failed = False

    def publish_state(**kwargs: Any) -> None:
        if state_callback is not None:
            state_callback(_build_runtime_state(**kwargs))

    logger.info("Starting Jackdaw Sentry Graph Ingest runtime...")
    publish_state(status_value="starting", ready=False)

    try:
        await init_databases()
        await start_connection_monitoring()

        migrations_ok = await run_database_migrations(profile="graph")
        if not migrations_ok:
            logger.warning("Graph migrations were not fully applied for ingest runtime")

        collector_manager = CollectorManager()
        await collector_manager.initialize()
        collector_task = asyncio.create_task(collector_manager.start_all())
        stop_wait_task = asyncio.create_task(stop_event.wait())

        publish_state(
            status_value="running",
            ready=True,
            collector_count=len(collector_manager.collectors),
            running_collectors=0,
        )
        logger.info("Graph ingest collectors started")

        while True:
            done, _ = await asyncio.wait(
                {collector_task, stop_wait_task},
                timeout=1,
                return_when=asyncio.FIRST_COMPLETED,
            )

            running_collectors = sum(
                1 for collector in collector_manager.collectors.values() if collector.is_running
            )
            publish_state(
                status_value="running",
                ready=True,
                collector_count=len(collector_manager.collectors),
                running_collectors=running_collectors,
            )

            if stop_wait_task in done:
                break

            if collector_task in done:
                exc = collector_task.exception()
                if exc is not None:
                    raise exc
                raise RuntimeError("Graph ingest collector task exited unexpectedly")

        publish_state(
            status_value="stopping",
            ready=False,
            collector_count=len(collector_manager.collectors),
            running_collectors=sum(
                1 for collector in collector_manager.collectors.values() if collector.is_running
            ),
        )
    except Exception as exc:
        failed = True
        logger.exception("Failed to initialize or run graph ingest runtime")
        publish_state(
            status_value="error",
            ready=False,
            collector_count=(len(collector_manager.collectors) if collector_manager else 0),
            running_collectors=(
                sum(1 for collector in collector_manager.collectors.values() if collector.is_running)
                if collector_manager
                else 0
            ),
            last_error=str(exc),
        )
        raise
    finally:
        if stop_wait_task is not None:
            stop_wait_task.cancel()
            await asyncio.gather(stop_wait_task, return_exceptions=True)

        if collector_manager is not None:
            try:
                await collector_manager.stop()
            except Exception:
                logger.exception("Collector manager stop failed during shutdown")

        if collector_task is not None:
            collector_task.cancel()
            await asyncio.gather(collector_task, return_exceptions=True)

        try:
            await stop_connection_monitoring()
        except Exception:
            logger.exception("Connection monitoring stop failed during shutdown")

        try:
            await close_all_clients()
        except Exception:
            logger.exception("Collector RPC client shutdown failed")

        await close_databases()

        if not failed:
            publish_state(status_value="stopped", ready=False)


def _run_graph_ingest_runtime_subprocess(
    stop_flag: mp.synchronize.Event,
    state_queue: mp.queues.Queue,
) -> None:
    """Run the ingest runtime in a subprocess and publish state snapshots."""

    async def _main() -> None:
        stop_event = asyncio.Event()

        async def watch_stop_flag() -> None:
            while not stop_flag.is_set():
                await asyncio.sleep(0.2)
            stop_event.set()

        watcher = asyncio.create_task(watch_stop_flag())
        try:
            await run_graph_ingest_runtime(stop_event, state_callback=state_queue.put)
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            state_queue.put(None)

    asyncio.run(_main())


class IngestRuntimeController:
    """Runs collectors in a subprocess so health stays responsive."""

    def __init__(self, startup_timeout_seconds: int = 60):
        self.startup_timeout_seconds = startup_timeout_seconds
        self._process: mp.Process | None = None
        self._stop_flag: mp.synchronize.Event | None = None
        self._state_queue: mp.queues.Queue | None = None
        self._listener_thread: threading.Thread | None = None
        self._listener_stop = threading.Event()
        self._state_lock = threading.Lock()
        self._ready_event = threading.Event()
        self._state = _build_runtime_state(status_value="idle", ready=False)

    def _publish_state(self, state: dict[str, Any]) -> None:
        with self._state_lock:
            self._state = state

        if state["status"] in {"running", "error"}:
            self._ready_event.set()

    def _listen_for_state(self) -> None:
        if self._state_queue is None:
            return

        while not self._listener_stop.is_set():
            try:
                state = self._state_queue.get(timeout=0.5)
            except Empty:
                continue
            except (EOFError, OSError):
                break

            if state is None:
                break

            self._publish_state(state)

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            state = dict(self._state)

        process = self._process
        if process is not None and not process.is_alive() and state["status"] not in {"stopped", "error"}:
            state.update(
                _build_runtime_state(
                    status_value="error",
                    ready=False,
                    collector_count=state.get("collector_count", 0),
                    running_collectors=0,
                    last_error=(
                        state.get("last_error")
                        or f"Graph ingest runtime exited with code {process.exitcode}"
                    ),
                )
            )
        return state

    async def start(self) -> None:
        if self._process is not None and self._process.is_alive():
            return

        ctx = mp.get_context("spawn")
        self._ready_event.clear()
        self._listener_stop.clear()
        self._publish_state(_build_runtime_state(status_value="starting", ready=False))
        self._stop_flag = ctx.Event()
        self._state_queue = ctx.Queue()
        self._listener_thread = threading.Thread(
            target=self._listen_for_state,
            name="graph-ingest-state-listener",
            daemon=True,
        )
        self._listener_thread.start()
        self._process = ctx.Process(
            target=_run_graph_ingest_runtime_subprocess,
            args=(self._stop_flag, self._state_queue),
            name="graph-ingest-runtime",
            daemon=True,
        )
        self._process.start()

        ready = await asyncio.to_thread(
            self._ready_event.wait,
            self.startup_timeout_seconds,
        )
        if not ready:
            raise TimeoutError(
                f"Graph ingest runtime did not become ready within {self.startup_timeout_seconds}s"
            )

        snapshot = self.snapshot()
        if snapshot["status"] == "error":
            raise RuntimeError(snapshot.get("last_error") or "Graph ingest runtime failed")

    async def stop(self) -> None:
        process = self._process
        stop_flag = self._stop_flag
        state_queue = self._state_queue
        listener_thread = self._listener_thread

        if stop_flag is not None:
            stop_flag.set()

        if process is not None:
            await asyncio.to_thread(process.join, 30)
            if process.is_alive():
                logger.warning("Graph ingest runtime process did not stop within 30 seconds; terminating")
                process.terminate()
                await asyncio.to_thread(process.join, 5)

        self._listener_stop.set()
        if state_queue is not None:
            try:
                state_queue.put_nowait(None)
            except Exception:
                pass
            state_queue.close()
            state_queue.join_thread()

        if listener_thread is not None:
            await asyncio.to_thread(listener_thread.join, 5)

        self._process = None
        self._stop_flag = None
        self._state_queue = None
        self._listener_thread = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Boot the collector runtime in a subprocess and stop it cleanly."""
    controller = IngestRuntimeController()
    app.state.runtime_controller = controller
    await controller.start()
    try:
        yield
    finally:
        logger.info("Shutting down Jackdaw Sentry Graph Ingest runtime...")
        await controller.stop()


app = FastAPI(
    title="Jackdaw Sentry Graph Ingest",
    description="Standalone ingestion and backfill runtime for the graph stack",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


def _runtime_snapshot() -> dict[str, Any]:
    controller = getattr(app.state, "runtime_controller", None)
    if controller is None:
        return _build_runtime_state(status_value="starting", ready=False)
    return controller.snapshot()


@app.get("/health", tags=["Health"])
async def health_check(response: Response):
    """Basic container health probe."""
    runtime = _runtime_snapshot()
    healthy = runtime["status"] == "running" and runtime["ready"]
    response.status_code = (
        status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return {
        **runtime,
        "status": "healthy" if healthy else runtime["status"],
        "service": "Jackdaw Sentry Graph Ingest",
        "version": "1.0.0",
    }


@app.get("/health/detailed", tags=["Health"])
async def detailed_health_check(response: Response):
    """Detailed health probe for the collector runtime."""
    runtime = _runtime_snapshot()
    healthy = runtime["status"] == "running" and runtime["ready"]
    response.status_code = (
        status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return {
        "status": "healthy" if healthy else runtime["status"],
        "service": "Jackdaw Sentry Graph Ingest",
        "version": "1.0.0",
        "runtime": runtime,
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
