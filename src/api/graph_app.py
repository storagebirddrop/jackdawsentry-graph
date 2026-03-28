"""
Standalone graph-focused API application.

This entrypoint keeps the runtime intentionally narrow around authentication,
graph sessions, and trace compilation for the standalone investigation graph
product.
"""

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from datetime import timezone
from typing import Any
from uuid import UUID

from fastapi import Depends
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from src.api.auth import PERMISSIONS
from src.api.auth import ROLES
from src.api.auth import User
from src.api.auth import get_current_user
from src.api.auth import is_graph_auth_bypass_active
from src.api.config import settings
from src.api.database import close_databases
from src.api.database import init_databases
from src.api.exceptions import BlockchainException
from src.api.exceptions import ComplianceException
from src.api.exceptions import JackdawException
from src.api.middleware import GraphLatencyMiddleware
from src.api.middleware import RateLimitMiddleware
from src.api.middleware import SecurityMiddleware
from src.api.routers import auth as auth_router
from src.api.routers import graph

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
PUBLIC_GRAPH_USER_ID = UUID("00000000-0000-0000-0000-000000000042")
PUBLIC_GRAPH_PERMISSIONS = sorted(
    {
        *ROLES["analyst"],
        PERMISSIONS["write_blockchain"],
    }
)


class DateTimeEncoder(json.JSONEncoder):
    """Serialize datetime objects to ISO strings."""

    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class CustomJSONResponse(JSONResponse):
    """JSON response class that understands datetimes."""

    def render(self, content) -> bytes:
        return json.dumps(
            content,
            cls=DateTimeEncoder,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")


async def get_ingest_runtime_status() -> dict[str, Any]:
    """Summarize whether a dedicated ingest runtime appears to be active."""
    status: dict[str, Any] = {
        "detected": False,
        "source": "redis:collector_metrics",
    }

    try:
        from src.api.database import get_redis_connection

        async with get_redis_connection() as redis:
            payload = await redis.get("collector_metrics")
    except Exception as exc:  # pragma: no cover - defensive status path
        logger.warning("Unable to read ingest runtime status from Redis: %s", exc)
        status["error"] = "collector metrics unavailable"
        return status

    if not payload:
        status["message"] = "No collector metrics found. The request-serving graph API is running without a live ingest sidecar."
        return status

    try:
        decoded = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        status["error"] = "collector metrics payload invalid"
        return status

    status.update(
        {
            "detected": True,
            "running_collectors": decoded.get("running_collectors"),
            "total_collectors": decoded.get("total_collectors"),
            "total_transactions": decoded.get("total_transactions"),
            "total_blocks": decoded.get("total_blocks"),
            "last_update": decoded.get("last_update"),
        }
    )
    return status


async def get_graph_runtime_user() -> User:
    """Return the synthetic analyst user used by the public graph runtime."""
    return User(
        id=PUBLIC_GRAPH_USER_ID,
        username="graph_public",
        email="graph-public@jackdawsentry.local",
        role="analyst",
        permissions=PUBLIC_GRAPH_PERMISSIONS,
        is_active=True,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        last_login=None,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize only the services required by the graph runtime."""
    logger.info("Starting Jackdaw Sentry Graph API...")

    try:
        await init_databases()

        from src.api.migrations.migration_manager import run_database_migrations

        migrations_ok = await run_database_migrations(profile="graph")
        if not migrations_ok:
            logger.warning("Graph migrations were not fully applied")
    except Exception:
        logger.exception("Failed to initialize graph runtime")
        raise

    yield

    logger.info("Shutting down Jackdaw Sentry Graph API...")
    await close_databases()

def configure_middleware(target_app: FastAPI) -> None:
    """Attach the graph runtime middleware stack to an app."""
    target_app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    allowed_hosts = ["localhost", "127.0.0.1", "*.jackdawsentry.local"]
    if settings.DEBUG or settings.TESTING:
        allowed_hosts.append("testclient")
    target_app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    if not settings.TESTING:
        target_app.add_middleware(SecurityMiddleware)
        if settings.RATE_LIMIT_ENABLED:
            target_app.add_middleware(RateLimitMiddleware)

    target_app.add_middleware(GraphLatencyMiddleware)


def configure_auth_mode(target_app: FastAPI) -> None:
    """Toggle auth requirements for the standalone graph runtime only."""
    target_app.dependency_overrides.pop(get_current_user, None)
    if is_graph_auth_bypass_active():
        target_app.dependency_overrides[get_current_user] = get_graph_runtime_user
    elif settings.GRAPH_AUTH_DISABLED:
        logger.warning(
            "GRAPH_AUTH_DISABLED was requested but bypass confirmation is inactive; "
            "standard authentication remains enabled."
        )


def create_graph_app() -> FastAPI:
    """Build the standalone graph FastAPI application."""
    app = FastAPI(
        title="Jackdaw Sentry Graph API",
        description="Standalone investigation graph API",
        version="1.0.0",
        docs_url="/docs" if settings.EXPOSE_API_DOCS else None,
        redoc_url="/redoc" if settings.EXPOSE_API_DOCS else None,
        openapi_url="/openapi.json" if settings.EXPOSE_API_DOCS else None,
        lifespan=lifespan,
        default_response_class=CustomJSONResponse,
    )

    configure_middleware(app)
    configure_auth_mode(app)

    @app.exception_handler(JackdawException)
    async def jackdaw_exception_handler(request, exc: JackdawException):
        """Handle Jackdaw-specific exceptions."""
        logger.error("JackdawException: %s", exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": "JackdawError",
                "message": exc.message,
                "code": exc.error_code,
                "timestamp": exc.timestamp,
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request, exc: HTTPException):
        """Return HTTP errors without the default exception stack."""
        kwargs = {
            "status_code": exc.status_code,
            "content": {"detail": exc.detail},
        }
        if exc.headers:
            kwargs["headers"] = exc.headers
        return JSONResponse(**kwargs)

    @app.exception_handler(ComplianceException)
    async def compliance_exception_handler(request, exc: ComplianceException):
        """Handle shared compliance-shaped exceptions without private routes."""
        logger.error("ComplianceException: %s", exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": "ComplianceError",
                "message": exc.message,
                "regulation": exc.regulation,
                "timestamp": exc.timestamp,
            },
        )

    @app.exception_handler(BlockchainException)
    async def blockchain_exception_handler(request, exc: BlockchainException):
        """Handle blockchain exceptions."""
        logger.error("BlockchainException: %s", exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": "BlockchainError",
                "message": exc.message,
                "blockchain": exc.blockchain,
                "timestamp": exc.timestamp,
            },
        )

    @app.get("/health", tags=["Health"])
    async def health_check():
        """Basic graph-runtime health check."""
        response: dict[str, Any] = {
            "status": "healthy",
            "service": "Jackdaw Sentry Graph API",
            "version": "1.0.0",
            "auth_disabled": is_graph_auth_bypass_active(),
        }
        return response

    @app.get("/health/detailed", tags=["Health"])
    async def detailed_health_check():
        """Detailed graph-runtime health check."""
        from src.api.database import check_database_health

        db_health = await check_database_health()
        return {
            "status": "healthy" if all(db_health.values()) else "degraded",
            "service": "Jackdaw Sentry Graph API",
            "version": "1.0.0",
            "auth_disabled": is_graph_auth_bypass_active(),
            "databases": db_health,
        }

    @app.get("/api/v1/status", tags=["Status"])
    async def api_status(current_user: User = Depends(get_current_user)):
        """Status endpoint for authenticated graph users."""
        ingest_status = await get_ingest_runtime_status()
        return {
            "status": "operational",
            "product": "graph",
            "user": current_user.username,
            "features": {
                "graph_sessions": True,
                "graph_expansion": True,
                "bridge_status_polling": True,
            },
            "runtime": {
                "mode": "request-serving",
                "ingest": ingest_status,
            },
        }

    if not is_graph_auth_bypass_active():
        app.include_router(auth_router.router, prefix="/api/v1/auth", tags=["Authentication"])
    app.include_router(
        graph.router,
        prefix="/api/v1/graph",
        tags=["Graph"],
        dependencies=[Depends(get_current_user)],
    )

    @app.get("/", tags=["Root"])
    async def root():
        """Root endpoint with graph-runtime information."""
        payload = {
            "name": "Jackdaw Sentry Graph API",
            "description": "Standalone investigation graph API",
            "health": "/health",
            "status": "/api/v1/status",
        }
        if settings.EXPOSE_API_DOCS:
            payload["docs"] = "/docs"
        return payload

    return app


app = create_graph_app()
