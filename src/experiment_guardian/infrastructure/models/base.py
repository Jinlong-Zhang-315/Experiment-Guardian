"""SQLAlchemy 声明基类和可复用字段。"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, MetaData, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

# 稳定的约束命名让 Alembic 可以可靠生成升级和回滚脚本。
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class VectorType(UserDefinedType[Any]):
    """CockroachDB ``VECTOR(n)`` 类型的轻量 SQLAlchemy 映射。

    P0 不引入额外 ORM 插件。该类型只负责生成正确 DDL；相似度查询将在仓储层使用参数化
    SQL 封装，禁止把用户输入直接拼接到向量查询语句中。
    """

    cache_ok = True

    def __init__(self, dimension: int = 1536) -> None:
        self.dimension = dimension

    def get_col_spec(self, **_: object) -> str:
        return f"VECTOR({self.dimension})"
