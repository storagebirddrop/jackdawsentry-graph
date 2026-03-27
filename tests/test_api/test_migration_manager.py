from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import AsyncMock

import pytest

from src.api.migrations.migration_manager import MigrationManager


@pytest.mark.asyncio
async def test_core_profile_excludes_optional_legacy_migrations(tmp_path: Path):
    for name in [
        "001_initial_schema.sql",
        "003_competitive_schema.sql",
        "003_sanctioned_addresses.sql",
        "008_cluster_attribution.sql",
        "009_event_store_backfill.sql",
        "016_token_metadata_cache.sql",
    ]:
        (tmp_path / name).write_text("-- sql", encoding="utf-8")

    manager = MigrationManager()
    manager.migrations_dir = tmp_path

    pending = await manager.get_pending_migrations(profile="core")

    assert "001_initial_schema.sql" in pending
    assert "003_sanctioned_addresses.sql" in pending
    assert "009_event_store_backfill.sql" in pending
    assert "016_token_metadata_cache.sql" in pending
    assert "003_competitive_schema.sql" not in pending
    assert "008_cluster_attribution.sql" not in pending


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
