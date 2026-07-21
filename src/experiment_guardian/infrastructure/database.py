"""SQLAlchemy 数据库连接与事务入口。

模块导入时只创建 Engine，不会立即连接 CockroachDB。真正的网络连接在首次执行 SQL 时
建立，因此 API 健康检查和纯领域单元测试不依赖本地数据库是否已经启动。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.core.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        echo=settings.database_echo,
        pool_pre_ping=True,
        # 事务失败后的自动重试应放在应用服务层，因为只有应用层知道操作是否幂等。
        # 这里不做隐式 SQL 重试，避免在未知状态下重复创建 Manifest 或 experiment。
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """供后台任务和 MCP 工具使用的显式事务边界。"""

    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db_session() -> Iterator[Session]:
    """FastAPI 依赖：请求结束后关闭会话，提交动作由应用服务显式完成。"""

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
