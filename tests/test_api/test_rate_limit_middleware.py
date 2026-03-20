from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware import RateLimitMiddleware
from src.api.middleware import SecurityMiddleware
from src.api.middleware import get_client_ip


@asynccontextmanager
async def _failing_redis_connection():
    raise RuntimeError("redis unavailable")
    yield None


def test_rate_limit_blocks_repeated_login_attempts_when_redis_is_unavailable(monkeypatch):
    from src.api import middleware as middleware_module

    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.post("/api/v1/auth/login")
    async def login() -> dict[str, str]:
        return {"status": "ok"}

    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setattr(middleware_module.settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(
        middleware_module,
        "get_redis_connection",
        _failing_redis_connection,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        for _ in range(5):
            response = client.post("/api/v1/auth/login")
            assert response.status_code == 200
            assert response.headers["X-RateLimit-Endpoint"] == "auth_login"

        blocked = client.post("/api/v1/auth/login")

    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "Rate limit exceeded"
    assert blocked.headers["Retry-After"] == "60"


def test_security_middleware_returns_400_for_suspicious_request(monkeypatch):
    app = FastAPI()
    app.add_middleware(SecurityMiddleware)

    @app.get("/unsafe")
    async def unsafe() -> dict[str, str]:
        return {"status": "ok"}

    monkeypatch.delenv("TESTING", raising=False)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/unsafe",
            headers={"User-Agent": "bad"},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid request"


def test_client_ip_ignores_proxy_headers_when_trust_is_disabled():
    request = SimpleNamespace(
        headers={
            "X-Forwarded-For": "203.0.113.20",
            "X-Real-IP": "198.51.100.15",
        },
        client=SimpleNamespace(host="127.0.0.1"),
    )

    assert get_client_ip(request, trust_proxy_headers=False) == "127.0.0.1"


def test_client_ip_uses_forwarded_for_when_trust_is_enabled():
    request = SimpleNamespace(
        headers={
            "X-Forwarded-For": "203.0.113.20, 198.51.100.15",
        },
        client=SimpleNamespace(host="127.0.0.1"),
    )

    assert get_client_ip(request, trust_proxy_headers=True) == "203.0.113.20"


def test_rate_limit_blocks_repeated_graph_expand_requests_per_user(monkeypatch):
    from src.api import middleware as middleware_module

    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.post("/api/v1/graph/sessions/demo/expand")
    async def expand() -> dict[str, str]:
        return {"status": "ok"}

    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setattr(middleware_module.settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(
        middleware_module,
        "get_redis_connection",
        _failing_redis_connection,
    )
    monkeypatch.setattr(
        "src.api.auth.verify_token",
        lambda _token: SimpleNamespace(user_id="user-123", username="analyst"),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        for _ in range(30):
            response = client.post(
                "/api/v1/graph/sessions/demo/expand",
                headers={"Authorization": "Bearer token"},
            )
            assert response.status_code == 200
            assert response.headers["X-RateLimit-Endpoint"] == "graph_expand"

        blocked = client.post(
            "/api/v1/graph/sessions/demo/expand",
            headers={"Authorization": "Bearer token"},
        )

    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "Rate limit exceeded"


def test_rate_limit_blocks_repeated_hop_status_requests_per_user(monkeypatch):
    from src.api import middleware as middleware_module

    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.get("/api/v1/graph/sessions/demo/hops/hop-1/status")
    async def hop_status() -> dict[str, str]:
        return {"status": "ok"}

    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.setattr(middleware_module.settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(
        middleware_module,
        "get_redis_connection",
        _failing_redis_connection,
    )
    monkeypatch.setattr(
        "src.api.auth.verify_token",
        lambda _token: SimpleNamespace(user_id="user-123", username="analyst"),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        for _ in range(60):
            response = client.get(
                "/api/v1/graph/sessions/demo/hops/hop-1/status",
                headers={"Authorization": "Bearer token"},
            )
            assert response.status_code == 200
            assert response.headers["X-RateLimit-Endpoint"] == "graph_hop_status"

        blocked = client.get(
            "/api/v1/graph/sessions/demo/hops/hop-1/status",
            headers={"Authorization": "Bearer token"},
        )

    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "Rate limit exceeded"
