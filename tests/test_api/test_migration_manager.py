from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import AsyncMock

import pytest

from src.api.migrations.migration_manager import MigrationManager


@pytest.mark.asyncio
async def test_apply_migration_marks_001_applied_when_base_schema_exists(tmp_path: Path):
    migration = tmp_path / "001_initial_schema.sql"
    migration.write_text("THIS IS NOT VALID SQL;", encoding="utf-8")

    manager = MigrationManager()
    manager.migrations_dir = tmp_path
    manager.mark_migration_applied = AsyncMock(return_value=None)
    manager._base_schema_exists = AsyncMock(return_value=True)

    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=Exception("syntax error"))
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction.return_value = tx

    success = await manager.apply_migration(conn, "001_initial_schema.sql")

    assert success is True
    manager.mark_migration_applied.assert_awaited_once_with(conn, "001_initial_schema.sql")


@pytest.mark.asyncio
async def test_apply_migration_marks_008_applied_when_legacy_table_missing(tmp_path: Path):
    migration = tmp_path / "008_cluster_attribution.sql"
    migration.write_text("ALTER TABLE address_attributions ADD COLUMN cluster_id UUID;", encoding="utf-8")

    manager = MigrationManager()
    manager.migrations_dir = tmp_path
    manager.mark_migration_applied = AsyncMock(return_value=None)
    manager._table_exists = AsyncMock(return_value=False)

    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=Exception("relation does not exist"))
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction.return_value = tx

    success = await manager.apply_migration(conn, "008_cluster_attribution.sql")

    assert success is True
    manager.mark_migration_applied.assert_awaited_once_with(conn, "008_cluster_attribution.sql")
