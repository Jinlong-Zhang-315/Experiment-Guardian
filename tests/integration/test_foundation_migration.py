"""Alembic revision 按开发切片逐步增加正式表。"""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

from migrations.scope import FOUNDATION_TABLES, MIGRATED_TABLES


def test_foundation_and_plan_check_migrations_are_independently_reversible(
    tmp_path: Path,
) -> None:
    database = tmp_path / "migration.db"
    database_url = f"sqlite+pysqlite:///{database}"
    environment = {**os.environ, "DATABASE_URL": database_url}

    def run_alembic(*arguments: str) -> None:
        subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            check=True,
            env=environment,
            capture_output=True,
            text=True,
        )

    run_alembic("upgrade", "20260721_01")
    engine = create_engine(database_url)
    assert set(inspect(engine).get_table_names()) == FOUNDATION_TABLES | {"alembic_version"}
    engine.dispose()

    run_alembic("upgrade", "head")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == MIGRATED_TABLES | {"alembic_version"}
    check_names = {item["name"] for item in inspector.get_check_constraints("plan_checks")}
    assert "ck_plan_checks_result_approval_consistent" in check_names
    assert "ck_plan_checks_approved_requires_actor" in check_names
    engine.dispose()

    run_alembic("downgrade", "20260721_01")
    engine = create_engine(database_url)
    assert set(inspect(engine).get_table_names()) == FOUNDATION_TABLES | {"alembic_version"}
    engine.dispose()

    run_alembic("downgrade", "base")
    engine = create_engine(database_url)
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    engine.dispose()
