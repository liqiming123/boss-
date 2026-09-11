import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from recruitment_collab.infrastructure import models  # noqa: F401
from recruitment_collab.infrastructure.database import Base

ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_CONFIG = ROOT / "apps/api/alembic.ini"


def _migration_env(database_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": f"sqlite:///{database_path}",
        # A developer's local Feishu target must never affect migration tests.
        "FEISHU_BITABLE_APP_TOKEN": "",
        "FEISHU_BITABLE_CANDIDATE_TABLE_ID": "",
    }


def _upgrade(database_path: Path, revision: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_CONFIG), "upgrade", revision],
        cwd=ROOT,
        env=_migration_env(database_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_empty_database_upgrades_to_head_with_current_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    _upgrade(database_path, "head")

    with sqlite3.connect(database_path) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        actual_tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            if row[0] not in {"alembic_version", "sqlite_sequence"}
        }
        actual_columns = {
            table_name: {row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")')}
            for table_name in actual_tables
        }

    assert revision == ("0029_requeue_daily_after_table_reset",)
    assert actual_tables == set(Base.metadata.tables)
    for table_name, table in Base.metadata.tables.items():
        assert actual_columns[table_name] == set(table.columns.keys())


def test_existing_0028_database_upgrades_to_head_and_requeues_daily_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "existing.db"
    _upgrade(database_path, "0028_company_daily_sync")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO boss_company_daily_rows
                (id, company_id, collector_account_id, metric_date, boss_name, metrics,
                 source_updated_at, version, status, feishu_record_id, synced_at,
                 retry_count, next_retry_at, last_error, created_at, updated_at)
            VALUES
                ('row-1', 'company-1', NULL, '2026-09-09', '测试账号', '{}',
                 CURRENT_TIMESTAMP, 3, 'SENT', 'record-1', CURRENT_TIMESTAMP,
                 2, CURRENT_TIMESTAMP, 'old error', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        )

    _upgrade(database_path, "head")
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT status, feishu_record_id, retry_count, last_error FROM boss_company_daily_rows WHERE id = 'row-1'"
        ).fetchone()
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()

    assert row == ("PENDING", None, 0, None)
    assert revision == ("0029_requeue_daily_after_table_reset",)
