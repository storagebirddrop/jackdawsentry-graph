from __future__ import annotations

from datetime import datetime
from datetime import timezone
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from src.api.auth import LoginRequest
from src.api.auth import User
from src.api.auth import get_current_user
from src.api.graph_app import app


@pytest.mark.asyncio
async def test_get_current_user_fails_closed_when_database_lookup_fails(make_token):
    token = make_token(
        sub="analyst",
        user_id="00000000-0000-0000-0000-000000000001",
        permissions=["blockchain:read"],
    )
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    failing_cm = MagicMock()
    failing_cm.__aenter__ = AsyncMock(side_effect=Exception("db unavailable"))
    failing_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "src.api.database.get_postgres_connection",
            return_value=failing_cm,
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await get_current_user(credentials)

    assert exc.value.status_code == 401
    assert exc.value.detail == "Authentication backend unavailable"


def test_login_response_is_not_cacheable():
    user = User(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        username="analyst",
        email="analyst@example.com",
        role="analyst",
        permissions=["blockchain:read"],
        is_active=True,
        created_at=datetime.now(timezone.utc),
        last_login=datetime.now(timezone.utc),
    )

    with (
        patch("src.api.routers.auth.authenticate_user", new_callable=AsyncMock, return_value=user),
        patch("src.api.routers.auth.create_user_token", return_value="jwt-token"),
        patch("src.api.graph_app.init_databases", new_callable=AsyncMock),
        patch("src.api.graph_app.close_databases", new_callable=AsyncMock),
        patch(
            "src.api.migrations.migration_manager.run_database_migrations",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        with TestClient(app, raise_server_exceptions=False, base_url="http://localhost") as client:
            response = client.post(
                "/api/v1/auth/login",
                json=LoginRequest(username="analyst", password="secret").model_dump(),
            )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
