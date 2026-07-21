"""首个 Alembic revision 只能创建本阶段基础表。"""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

from migrations.scope import FOUNDATION_TABLES


def test_foundation_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    database_url = f"sqlite+pysqlite:///{database}"
    environment = {**os.environ, "DATABASE_URL": database_url}

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )
    engine = create_engine(database_url)
    assert set(inspect(engine).get_table_names()) == FOUNDATION_TABLES | {"alembic_version"}
    engine.dispose()

    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )
    engine = create_engine(database_url)
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    engine.dispose()
