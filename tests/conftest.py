"""不依赖外部 CockroachDB 的基础表测试夹具。"""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from experiment_guardian.infrastructure.models import Base
from migrations.scope import FOUNDATION_TABLES


@pytest.fixture
def foundation_session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    tables = [Base.metadata.tables[name] for name in FOUNDATION_TABLES]
    Base.metadata.create_all(engine, tables=tables)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield factory
    Base.metadata.drop_all(engine, tables=tables)
    engine.dispose()
