from __future__ import annotations

import pytest

from scripts.dev.create_user import _resolve_port


def test_resolve_port_prefers_cli_value():
    assert _resolve_port(6543, {"POSTGRES_PORT": "5433"}) == 6543


def test_resolve_port_reads_env_value():
    assert _resolve_port(None, {"POSTGRES_PORT": "6543"}) == 6543


def test_resolve_port_raises_clean_error_on_invalid_env_value():
    with pytest.raises(SystemExit, match="Invalid PostgreSQL port 'not-a-port'"):
        _resolve_port(None, {"POSTGRES_PORT": "not-a-port"})
