"""按开发阶段控制 Alembic 可见表，避免提前冻结未实现模块的 Schema。"""

FOUNDATION_TABLES = {
    "access_tokens",
    "audit_logs",
    "experiment_intents",
    "idempotency_records",
    "project_contexts",
    "projects",
    "protected_parameters",
    "team_members",
    "teams",
    "users",
}


def include_foundation_object(
    obj: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object | None,
) -> bool:
    """只让 Alembic 比较本阶段已承诺迁移的表及其附属对象。"""

    del name, reflected, compare_to
    if type_ == "table":
        return getattr(obj, "name", None) in FOUNDATION_TABLES
    table = getattr(obj, "table", None)
    return table is None or getattr(table, "name", None) in FOUNDATION_TABLES
