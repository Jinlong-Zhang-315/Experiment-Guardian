"""Alembic 环境配置。"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.sql.sqltypes import NullType, Numeric

from experiment_guardian.core.config import get_settings
from experiment_guardian.infrastructure.models import Base
from experiment_guardian.infrastructure.models.base import VectorType
from migrations.scope import include_migrated_object

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def compare_column_type(
    migration_context: object,
    inspected_column: object,
    metadata_column: object,
    inspected_type: object,
    metadata_type: object,
) -> bool | None:
    """忽略数据库无法原样反射的 VECTOR 类型，其余类型仍交给 Alembic。"""

    del inspected_column, metadata_column
    if not isinstance(metadata_type, VectorType):
        return None
    dialect = getattr(migration_context, "dialect", None)
    dialect_name = getattr(dialect, "name", None)
    if dialect_name == "sqlite" and isinstance(inspected_type, Numeric):
        return inspected_type.precision != metadata_type.dimension
    if dialect_name in {"cockroachdb", "postgresql"} and isinstance(
        inspected_type, NullType
    ):
        return False
    return None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=compare_column_type,
        include_object=include_migrated_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=compare_column_type,
            include_object=include_migrated_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
