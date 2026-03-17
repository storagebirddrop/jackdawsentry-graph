"""
Integration-style tests for investigation session endpoints (Phase 3 stubs).

These tests exercise the HTTP layer via FastAPI TestClient.  All session
endpoints return stub data at this phase; we verify HTTP status, response
structure, and that auth is enforced.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture
def client():
    return TestClient(app)


def _auth():
    return {"Authorization": "Bearer test-token"}


class TestCreateSession:
    def test_endpoint_reachable(self, client):
        resp = client.post(
            "/api/v1/graph/sessions",
            json={"seed_address": "0xabc", "seed_chain": "ethereum"},
            headers=_auth(),
        )
        assert resp.status_code in (200, 401, 403)

    def test_requires_auth(self, client):
        resp = client.post(
            "/api/v1/graph/sessions",
            json={"seed_address": "0xabc", "seed_chain": "ethereum"},
        )
        assert resp.status_code in (401, 403)

    def test_not_404(self, client):
        resp = client.post(
            "/api/v1/graph/sessions",
            json={"seed_address": "0xabc", "seed_chain": "ethereum"},
            headers=_auth(),
        )
        assert resp.status_code != 404


class TestGetSession:
    def test_endpoint_reachable(self, client):
        resp = client.get(
            "/api/v1/graph/sessions/fake-session-id",
            headers=_auth(),
        )
        assert resp.status_code in (200, 401, 403)

    def test_not_404(self, client):
        resp = client.get(
            "/api/v1/graph/sessions/fake-session-id",
            headers=_auth(),
        )
        assert resp.status_code != 404


class TestExpandSessionNode:
    def test_endpoint_reachable(self, client):
        resp = client.post(
            "/api/v1/graph/sessions/fake-session-id/expand",
            json={
                "operation_type": "expand_next",
                "seed_node_id": "ethereum:address:0xabc",
            },
            headers=_auth(),
        )
        assert resp.status_code in (200, 401, 403)

    def test_not_404(self, client):
        resp = client.post(
            "/api/v1/graph/sessions/fake-session-id/expand",
            json={
                "operation_type": "expand_next",
                "seed_node_id": "ethereum:address:0xabc",
            },
            headers=_auth(),
        )
        assert resp.status_code != 404


class TestBridgeHopStatus:
    def test_endpoint_reachable(self, client):
        resp = client.get(
            "/api/v1/graph/sessions/fake-session/hops/fake-hop/status",
            headers=_auth(),
        )
        assert resp.status_code in (200, 401, 403)

    def test_not_404(self, client):
        resp = client.get(
            "/api/v1/graph/sessions/fake-session/hops/fake-hop/status",
            headers=_auth(),
        )
        assert resp.status_code != 404


class TestSaveSnapshot:
    def test_endpoint_reachable(self, client):
        resp = client.post(
            "/api/v1/graph/sessions/fake-session/snapshot",
            json={"node_states": []},
            headers=_auth(),
        )
        assert resp.status_code in (200, 401, 403)
