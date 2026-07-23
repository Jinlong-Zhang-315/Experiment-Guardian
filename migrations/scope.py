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
SUBMISSION_ANALYSIS_TABLES = frozenset(
    {"submission_risks", "workflow_jobs", "outbox_events", "submission_embeddings"}
)
FORMAL_EXPERIMENT_TABLES = frozenset({"experiments", "experiment_metrics", "memories"})
WEB_AUTH_TABLES = frozenset({"web_sessions", "oidc_transactions"})
MCP_OAUTH_TABLES = frozenset({"mcp_oauth_clients", "mcp_oauth_grants"})
POLICY_NARRATIVE_TABLES = frozenset({"policy_narratives"})
AGENT_TABLES = frozenset(
    {
        "agent_threads",
        "agent_messages",
        "agent_runs",
        "agent_model_calls",
        "agent_tool_calls",
        "agent_citations",
        "agent_run_events",
    }
)
MIGRATED_TABLES = (
    FOUNDATION_TABLES
    | PLAN_CHECK_TABLES
    | GOVERNANCE_TABLES
    | SUBMISSION_PREPARE_TABLES
    | SUBMISSION_ANALYSIS_TABLES
    | FORMAL_EXPERIMENT_TABLES
    | WEB_AUTH_TABLES
    | MCP_OAUTH_TABLES
    | POLICY_NARRATIVE_TABLES
    | AGENT_TABLES
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
