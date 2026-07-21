"""CockroachDB 可重试事务的应用层边界。"""

from collections.abc import Callable

from sqlalchemy.exc import DBAPIError

from experiment_guardian.application.errors import ServiceUnavailableError

MAX_SERIALIZATION_ATTEMPTS = 3


def run_with_serialization_retry[T](operation: Callable[[], T]) -> T:
    """只重试 CockroachDB 40001；调用者必须保证操作具备幂等键。"""

    for attempt in range(MAX_SERIALIZATION_ATTEMPTS):
        try:
            return operation()
        except DBAPIError as exc:
            sqlstate = getattr(exc.orig, "sqlstate", None)
            if sqlstate == "40001" and attempt + 1 < MAX_SERIALIZATION_ATTEMPTS:
                continue
            if sqlstate == "40001":
                raise ServiceUnavailableError(
                    "CockroachDB 事务连续发生并发冲突，请使用相同 Idempotency-Key 重试"
                ) from exc
            raise ServiceUnavailableError("数据库暂时不可用") from exc
    raise RuntimeError("事务重试循环未返回结果")
