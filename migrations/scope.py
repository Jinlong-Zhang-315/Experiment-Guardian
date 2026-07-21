"""按开发阶段控制 Alembic 可见表，避免提前冻结未实现模块的 Schema。"""

FOUNDATION_TABLES = frozenset(
    {
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
)
PLAN_CHECK_TABLES = frozenset({"plan_checks"})
GOVERNANCE_TABLES = frozenset({"approval_records", "run_manifests"})
SUBMISSION_PREPARE_TABLES = frozenset({"experiment_submissions", "artifacts"})
SUBMISSION_ANALYSIS_TABLES = frozenset({"submission_risks"})
MIGRATED_TABLES = (
    FOUNDATION_TABLES
    | PLAN_CHECK_TABLES
    | GOVERNANCE_TABLES
    | SUBMISSION_PREPARE_TABLES
    | SUBMISSION_ANALYSIS_TABLES
)


def include_migrated_object(
    obj: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object | None,
) -> bool:
    """只让 Alembic 比较已经有正式 revision 的表及其附属对象。"""

    del name, reflected, compare_to
    if type_ == "table":
        return getattr(obj, "name", None) in MIGRATED_TABLES
    table = getattr(obj, "table", None)
    return table is None or getattr(table, "name", None) in MIGRATED_TABLES
