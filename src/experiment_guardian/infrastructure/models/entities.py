"""P0 数据库实体。

模型优先表达追溯链和状态约束，不在 ORM 中堆叠复杂级联关系。关键写操作会由应用服务在
显式事务中按顺序执行，便于审计，也更符合 CockroachDB 事务重试的要求。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from experiment_guardian.domain.enums import (
    ActionProposalOperation,
    ActionProposalStatus,
    AgentCallStatus,
    AgentContextSummaryStatus,
    AgentMessageRole,
    AgentModelCallPurpose,
    AgentRunAuthMethod,
    AgentRunKind,
    AgentRunStatus,
    AgentThreadOrigin,
    AgentThreadStatus,
    ApprovalDecision,
    ApprovalStatus,
    ApprovalTargetType,
    ArtifactType,
    CheckResult,
    ConstraintSource,
    ContextStatus,
    EvidenceType,
    ExperimentMode,
    ExperimentPlanDecisionType,
    ExperimentPlanRevisionAuthor,
    ExperimentPlanStatus,
    ExperimentStatus,
    IdempotencyOperationStatus,
    IntentStatus,
    OutboxStatus,
    PolicyDraftReadiness,
    PolicyDraftSource,
    PolicyDraftStatus,
    ProtectionLevel,
    ResearchMemoryEmbeddingStatus,
    ResearchMemoryStatus,
    ResearchMemoryType,
    RiskSeverity,
    SubmissionStatus,
    SubmittedRunStatus,
    TeamRole,
    TokenAudience,
    VerificationStatus,
    WorkflowJobStatus,
    WorkflowJobType,
    WorkflowStatus,
    WorkflowStep,
)
from experiment_guardian.infrastructure.models.base import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    VectorType,
)


def enum_column(enum_type: type[Any], name: str, *, length: int | None = None) -> Enum:
    """使用字符串 CHECK/Enum 映射，避免依赖 PostgreSQL 原生枚举迁移。"""

    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        validate_strings=True,
        length=length,
    )


class User(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    # Cognito ``sub`` 是稳定的人类身份主键；邮箱只用于首次、显式且已验证的绑定。
    cognito_sub: Mapped[str | None] = mapped_column(String(128), unique=True)


class Team(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "teams"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class TeamMember(CreatedAtMixin, Base):
    __tablename__ = "team_members"

    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[TeamRole] = mapped_column(enum_column(TeamRole, "team_role"), nullable=False)


class AccessToken(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "access_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        Index("ix_access_token_principal", "user_id", "audience", "project_id"),
        CheckConstraint(
            "audience != 'MCP' OR project_id IS NOT NULL",
            name="mcp_token_requires_project",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("projects.id"))
    audience: Mapped[TokenAudience] = mapped_column(
        enum_column(TokenAudience, "token_audience"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class WebSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """服务端 Web 会话；数据库只保存 Cookie 的 SHA-256，不保存原始值。"""

    __tablename__ = "web_sessions"
    __table_args__ = (
        UniqueConstraint("session_hash"),
        Index("ix_web_session_user_active", "user_id", "revoked_at", "absolute_expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    session_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    authenticated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reauthenticated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(500))
    user_agent_hash: Mapped[str | None] = mapped_column(String(64))


class OidcTransaction(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """一次性 OIDC state/PKCE 事务；敏感内容以应用密钥加密后持久化。"""

    __tablename__ = "oidc_transactions"
    __table_args__ = (
        UniqueConstraint("state_hash"),
        Index("ix_oidc_transaction_expiry", "expires_at", "consumed_at"),
        CheckConstraint("purpose IN ('LOGIN', 'REAUTH')", name="oidc_purpose_valid"),
    )

    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    session_id: Mapped[UUID | None] = mapped_column(ForeignKey("web_sessions.id"))
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class McpOAuthClient(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """预注册 Cognito public client 与单一项目的服务端绑定。"""

    __tablename__ = "mcp_oauth_clients"
    __table_args__ = (UniqueConstraint("cognito_client_id"),)

    cognito_client_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    allowed_scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(500))


class McpOAuthGrant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """用户与预注册客户端的本地授权，可独立于 Cognito Token 立即撤销。"""

    __tablename__ = "mcp_oauth_grants"
    __table_args__ = (
        UniqueConstraint("mcp_oauth_client_id", "user_id", name="uq_mcp_oauth_grant_principal"),
        Index("ix_mcp_oauth_grant_active", "user_id", "revoked_at"),
    )

    mcp_oauth_client_id: Mapped[UUID] = mapped_column(
        ForeignKey("mcp_oauth_clients.id"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    granted_scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(500))


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("team_id", "name"),)

    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    repository_url: Mapped[str | None] = mapped_column(String(1000))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ProjectContext(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "project_contexts"
    __table_args__ = (
        UniqueConstraint("project_id", "version"),
        Index("ix_project_context_active", "project_id", "status"),
        CheckConstraint(
            "status != 'ACTIVE' OR "
            "(confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL AND effective_at IS NOT NULL)",
            name="active_context_requires_confirmation",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    non_goals: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    mainline_model: Mapped[str] = mapped_column(String(500), nullable=False)
    baseline: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    dataset: Mapped[str] = mapped_column(String(200), nullable=False)
    protocol: Mapped[str] = mapped_column(String(200), nullable=False)
    primary_metric: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    default_seeds: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    active_branch: Mapped[str] = mapped_column(String(500), nullable=False)
    active_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    deprecated_items: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    key_decisions: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    change_reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ContextStatus] = mapped_column(
        enum_column(ContextStatus, "context_status"), nullable=False
    )
    supersedes_context_id: Mapped[UUID | None] = mapped_column(ForeignKey("project_contexts.id"))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    confirmed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PolicyNarrative(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """绑定一个正式 Context/Intent 版本的可重建阅读表示。"""

    __tablename__ = "policy_narratives"
    __table_args__ = (
        UniqueConstraint("context_id", "intent_id", name="uq_policy_narratives_source_version"),
        Index("ix_policy_narrative_project_version", "project_id", "context_version"),
        CheckConstraint(
            "status IN ('READY', 'FAILED')",
            name="status_valid",
        ),
        CheckConstraint(
            "(status = 'READY' AND content IS NOT NULL AND source_hash IS NOT NULL "
            "AND generated_at IS NOT NULL AND error IS NULL) OR "
            "(status = 'FAILED' AND content IS NULL AND error IS NOT NULL)",
            name="state_consistent",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    context_id: Mapped[UUID] = mapped_column(ForeignKey("project_contexts.id"), nullable=False)
    context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    intent_id: Mapped[UUID] = mapped_column(ForeignKey("experiment_intents.id"), nullable=False)
    intent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_hash: Mapped[str | None] = mapped_column(String(64))
    format: Mapped[str] = mapped_column(String(20), nullable=False)
    generator: Mapped[str] = mapped_column(String(50), nullable=False)
    generator_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    generated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExperimentIntent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "experiment_intents"
    __table_args__ = (
        UniqueConstraint("project_id", "version"),
        Index("ix_experiment_intent_active", "project_id", "status"),
        CheckConstraint(
            "verification_status != 'CONFIRMED' OR "
            "(confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)",
            name="confirmed_intent_requires_actor",
        ),
        CheckConstraint(
            "status != 'ACTIVE' OR "
            "(verification_status = 'CONFIRMED' AND activated_by IS NOT NULL "
            "AND activated_at IS NOT NULL)",
            name="active_intent_requires_confirmed_version",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    context_id: Mapped[UUID] = mapped_column(ForeignKey("project_contexts.id"), nullable=False)
    context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_intent_id: Mapped[UUID | None] = mapped_column(ForeignKey("experiment_intents.id"))
    experiment_mode: Mapped[ExperimentMode] = mapped_column(
        enum_column(ExperimentMode, "intent_experiment_mode"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_variables: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    controlled_variables: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    expected_outputs: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    acceptance_criteria: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    source_type: Mapped[ConstraintSource] = mapped_column(
        enum_column(ConstraintSource, "intent_source_type"), nullable=False
    )
    verification_status: Mapped[VerificationStatus] = mapped_column(
        enum_column(VerificationStatus, "intent_verification_status"), nullable=False
    )
    original_message: Mapped[str] = mapped_column(Text, nullable=False)
    inference_basis: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    unresolved_ambiguities: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    intent_receipt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[IntentStatus] = mapped_column(
        enum_column(IntentStatus, "intent_status"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    confirmed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProtectedParameter(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "protected_parameters"
    __table_args__ = (
        UniqueConstraint("project_id", "context_version", "parameter_path", "version"),
        Index(
            "ix_protected_parameter_effective",
            "project_id",
            "context_version",
            "verification_status",
            "active",
        ),
        CheckConstraint(
            "verification_status != 'CONFIRMED' OR "
            "(confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)",
            name="confirmed_constraint_requires_actor",
        ),
        CheckConstraint(
            "verification_status NOT IN ('REJECTED', 'SUPERSEDED') OR NOT active",
            name="inactive_rejected_constraint",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    context_id: Mapped[UUID] = mapped_column(ForeignKey("project_contexts.id"), nullable=False)
    context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    intent_id: Mapped[UUID | None] = mapped_column(ForeignKey("experiment_intents.id"))
    intent_version: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_constraint_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("protected_parameters.id", name="fk_protected_parameters_supersedes")
    )
    parameter_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    protection_level: Mapped[ProtectionLevel] = mapped_column(
        enum_column(ProtectionLevel, "protection_level"), nullable=False
    )
    expected_value: Mapped[Any] = mapped_column(JSON, nullable=False)
    allowed_range: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_type: Mapped[ConstraintSource] = mapped_column(
        enum_column(ConstraintSource, "constraint_source_type"), nullable=False
    )
    verification_status: Mapped[VerificationStatus] = mapped_column(
        enum_column(VerificationStatus, "constraint_verification_status"), nullable=False
    )
    original_message: Mapped[str] = mapped_column(Text, nullable=False)
    inference_basis: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    confirmed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class PlanCheck(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "plan_checks"
    __table_args__ = (
        UniqueConstraint("requester_id", "idempotency_key"),
        Index("ix_plan_check_project_created", "project_id", "created_at"),
        CheckConstraint(
            "(check_result = 'PASS' AND approval_status = 'NOT_REQUIRED') OR "
            "(check_result = 'BLOCKED' AND approval_status = 'NOT_REQUIRED') OR "
            "(check_result = 'NEEDS_APPROVAL' AND "
            "approval_status IN ('PENDING', 'APPROVED', 'REJECTED'))",
            name="result_approval_consistent",
        ),
        CheckConstraint(
            "approval_status != 'APPROVED' OR "
            "(approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="approved_requires_actor",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    intent_id: Mapped[UUID] = mapped_column(ForeignKey("experiment_intents.id"), nullable=False)
    context_id: Mapped[UUID] = mapped_column(ForeignKey("project_contexts.id"), nullable=False)
    context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    intent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    experiment_mode: Mapped[ExperimentMode] = mapped_column(
        enum_column(ExperimentMode, "plan_experiment_mode"), nullable=False
    )
    requester_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    idempotency_key: Mapped[UUID] = mapped_column(nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_document_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration_document: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    parsed_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    context_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    intent_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    git_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    local_attestation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    constraint_snapshot: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    planned_changes: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    check_result: Mapped[CheckResult] = mapped_column(
        enum_column(CheckResult, "check_result"), nullable=False
    )
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        enum_column(ApprovalStatus, "approval_status"), nullable=False
    )
    risk_level: Mapped[RiskSeverity] = mapped_column(
        enum_column(RiskSeverity, "plan_risk_severity"), nullable=False
    )
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    approved_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApprovalRecord(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "approval_records"
    __table_args__ = (
        UniqueConstraint("target_type", "target_id", name="uq_approval_records_target"),
        CheckConstraint(
            "status IN ('APPROVED', 'REJECTED')",
            name="approval_record_final_status",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    target_type: Mapped[ApprovalTargetType] = mapped_column(
        enum_column(ApprovalTargetType, "approval_target_type"), nullable=False
    )
    # 多态目标不建立数据库外键；应用服务必须验证 target 与 project_id 一致。
    target_id: Mapped[UUID] = mapped_column(nullable=False)
    approval_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[ApprovalDecision] = mapped_column(
        enum_column(ApprovalDecision, "approval_decision"), nullable=False
    )
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    decided_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    request_reason: Mapped[str] = mapped_column(Text, nullable=False)
    decision_reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunManifest(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "run_manifests"
    __table_args__ = (
        UniqueConstraint("plan_check_id", name="uq_run_manifests_plan_check"),
        UniqueConstraint(
            "project_id", "idempotency_key", name="uq_run_manifests_project_idempotency"
        ),
        UniqueConstraint("project_id", "manifest_hash", name="uq_run_manifests_project_hash"),
        CheckConstraint("schema_version = 1", name="run_manifest_schema_version_one"),
    )

    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    intent_id: Mapped[UUID] = mapped_column(ForeignKey("experiment_intents.id"), nullable=False)
    plan_check_id: Mapped[UUID] = mapped_column(ForeignKey("plan_checks.id"), nullable=False)
    approval_record_id: Mapped[UUID | None] = mapped_column(ForeignKey("approval_records.id"))
    context_id: Mapped[UUID] = mapped_column(ForeignKey("project_contexts.id"), nullable=False)
    context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    intent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    experiment_mode: Mapped[ExperimentMode] = mapped_column(
        enum_column(ExperimentMode, "manifest_experiment_mode"), nullable=False
    )
    idempotency_key: Mapped[UUID] = mapped_column(nullable=False)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_document_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    git_branch: Mapped[str] = mapped_column(String(500), nullable=False)
    git_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    git_diff_hash: Mapped[str | None] = mapped_column(String(64))
    dataset: Mapped[str] = mapped_column(String(200), nullable=False)
    protocol: Mapped[str] = mapped_column(String(200), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    checkpoint: Mapped[str | None] = mapped_column(String(1500))
    command: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class ExperimentSubmission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "experiment_submissions"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "submitted_by",
            "idempotency_key",
            name="uq_experiment_submissions_actor_idempotency",
        ),
        CheckConstraint(
            "declared_experiment_status IN ('COMPLETED', 'FAILED')",
            name="submission_declared_status_final",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    run_manifest_id: Mapped[UUID] = mapped_column(ForeignKey("run_manifests.id"), nullable=False)
    submitted_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    source_agent: Mapped[str] = mapped_column(String(300), nullable=False)
    idempotency_key: Mapped[UUID] = mapped_column(nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    declared_experiment_status: Mapped[SubmittedRunStatus] = mapped_column(
        enum_column(SubmittedRunStatus, "submitted_run_status"), nullable=False
    )
    declared_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[SubmissionStatus] = mapped_column(
        enum_column(SubmissionStatus, "submission_status", length=32), nullable=False
    )
    upload_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    upload_verified_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    upload_verification_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    workflow_status: Mapped[WorkflowStatus] = mapped_column(
        enum_column(WorkflowStatus, "submission_workflow_status", length=32),
        default=WorkflowStatus.NOT_STARTED,
        nullable=False,
    )
    processing_step: Mapped[WorkflowStep | None] = mapped_column(
        enum_column(WorkflowStep, "submission_processing_step", length=32)
    )
    processing_error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    analysis_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    generated_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    review_receipt: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class SubmissionEmbedding(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """R12b 草稿检索向量；正式确认后由 R13 复制或关联到 Memory。"""

    __tablename__ = "submission_embeddings"
    __table_args__ = (
        UniqueConstraint("submission_id", name="uq_submission_embeddings_submission_id"),
        CheckConstraint("dimension = 1024", name="submission_embedding_dimension_1024"),
        CheckConstraint("normalized", name="submission_embedding_normalized"),
        CheckConstraint(
            "length(input_sha256) = 64", name="submission_embedding_input_sha256_length"
        ),
    )

    submission_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_submissions.id"), nullable=False
    )
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(VectorType(1024), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), default="bedrock", nullable=False)
    model_id: Mapped[str] = mapped_column(String(500), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    normalized: Mapped[bool] = mapped_column(Boolean, nullable=False)
    document_version: Mapped[str] = mapped_column(String(100), nullable=False)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    input_token_count: Mapped[int | None] = mapped_column(Integer)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkflowJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """可租约执行的异步工作项；每种类型在一个 Submission 下保持单例。"""

    __tablename__ = "workflow_jobs"
    __table_args__ = (
        UniqueConstraint("submission_id", "job_type", name="uq_workflow_jobs_submission_type"),
        CheckConstraint(
            "job_type IN ('SUBMISSION_SUMMARY', 'SUBMISSION_REVIEW_PREPARATION')",
            name="workflow_job_type_valid",
        ),
        CheckConstraint(
            "status IN ('PENDING_DISPATCH', 'QUEUED', 'RUNNING', 'RETRYABLE_FAILURE', "
            "'SUCCEEDED', 'DEAD_LETTER', 'FAILED')",
            name="workflow_job_status_valid",
        ),
        CheckConstraint("generation >= 1", name="workflow_job_generation_positive"),
        CheckConstraint("attempt_count >= 0", name="workflow_job_attempt_count_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="workflow_job_max_attempts_positive"),
        Index("ix_workflow_jobs_status_available", "status", "available_at"),
    )

    submission_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_submissions.id"), nullable=False
    )
    job_type: Mapped[WorkflowJobType] = mapped_column(
        enum_column(WorkflowJobType, "workflow_job_type", length=32), nullable=False
    )
    status: Mapped[WorkflowJobStatus] = mapped_column(
        enum_column(WorkflowJobStatus, "workflow_job_status", length=32), nullable=False
    )
    generation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(300))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    sqs_message_id: Mapped[str | None] = mapped_column(String(300))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """事务内写入、事务外投递的 SQS 消息。"""

    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("workflow_job_id", "generation", name="uq_outbox_events_job_generation"),
        CheckConstraint(
            "event_type IN ('SUBMISSION_SUMMARY_REQUESTED', "
            "'SUBMISSION_REVIEW_PREPARATION_REQUESTED')",
            name="outbox_event_type_valid",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'PUBLISHING', 'PUBLISHED', 'COMPLETED', 'DEAD_LETTER')",
            name="outbox_status_valid",
        ),
        CheckConstraint("generation >= 1", name="outbox_generation_positive"),
        CheckConstraint("attempt_count >= 0", name="outbox_attempt_count_nonnegative"),
        Index("ix_outbox_events_status_available", "status", "available_at"),
    )

    workflow_job_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_jobs.id"), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(
        enum_column(OutboxStatus, "outbox_status", length=16), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(300))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sqs_message_id: Mapped[str | None] = mapped_column(String(300))


class Artifact(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("submission_id", "filename", name="uq_artifacts_submission_filename"),
        UniqueConstraint("s3_key", name="uq_artifacts_s3_key"),
        CheckConstraint(
            "size_bytes > 0 AND size_bytes <= 20971520",
            name="artifact_size_limit",
        ),
        CheckConstraint("length(sha256) = 64", name="artifact_sha256_length"),
        Index("ix_artifact_submission_type", "submission_id", "artifact_type"),
    )

    submission_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_submissions.id"), nullable=False
    )
    experiment_id: Mapped[UUID | None] = mapped_column(ForeignKey("experiments.id"))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(200), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    s3_key: Mapped[str] = mapped_column(String(1500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_type: Mapped[ArtifactType] = mapped_column(
        enum_column(ArtifactType, "artifact_type"), nullable=False
    )
    cloud_hash_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    s3_version_id: Mapped[str | None] = mapped_column(String(1024))


class SubmissionRisk(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "submission_risks"
    __table_args__ = (
        UniqueConstraint(
            "submission_id",
            "risk_fingerprint",
            name="uq_submission_risks_submission_fingerprint",
        ),
        Index("ix_submission_risks_submission_severity", "submission_id", "severity"),
    )

    submission_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_submissions.id"), nullable=False
    )
    risk_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[RiskSeverity] = mapped_column(
        enum_column(RiskSeverity, "risk_severity"), nullable=False
    )
    field_path: Mapped[str | None] = mapped_column(String(1000))
    previous_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    current_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    expected_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    rule_id: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    impact: Mapped[str | None] = mapped_column(Text)
    evidence_type: Mapped[EvidenceType | None] = mapped_column(
        enum_column(EvidenceType, "risk_evidence_type")
    )
    evidence_source: Mapped[str | None] = mapped_column(String(500))
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collection_tool: Mapped[str | None] = mapped_column(String(500))
    constraint_source: Mapped[ConstraintSource | None] = mapped_column(
        enum_column(ConstraintSource, "risk_constraint_source")
    )
    constraint_status: Mapped[VerificationStatus | None] = mapped_column(
        enum_column(VerificationStatus, "risk_constraint_status")
    )
    inference_basis: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    constraint_candidates: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    recommendation: Mapped[str | None] = mapped_column(Text)
    blocking: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Experiment(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "experiments"
    __table_args__ = (
        UniqueConstraint("submission_id"),
        Index("ix_experiment_project_status", "project_id", "status"),
        CheckConstraint(
            "NOT (experiment_mode = 'EXPLORATORY' AND eligible_as_baseline)",
            name="exploratory_not_eligible_as_baseline",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    intent_id: Mapped[UUID] = mapped_column(ForeignKey("experiment_intents.id"), nullable=False)
    run_manifest_id: Mapped[UUID] = mapped_column(ForeignKey("run_manifests.id"), nullable=False)
    submission_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_submissions.id"), nullable=False
    )
    project_context_id: Mapped[UUID] = mapped_column(
        ForeignKey("project_contexts.id"), nullable=False
    )
    project_context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    intent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    approval_record_id: Mapped[UUID] = mapped_column(
        ForeignKey("approval_records.id"), nullable=False
    )
    experiment_mode: Mapped[ExperimentMode] = mapped_column(
        enum_column(ExperimentMode, "experiment_mode"), nullable=False
    )
    eligible_as_baseline: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    model_name: Mapped[str] = mapped_column(String(300), nullable=False)
    dataset: Mapped[str] = mapped_column(String(200), nullable=False)
    protocol: Mapped[str] = mapped_column(String(200), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ExperimentStatus] = mapped_column(
        enum_column(ExperimentStatus, "experiment_status"), nullable=False
    )
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    git_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    checkpoint: Mapped[str | None] = mapped_column(String(1500))
    command: Mapped[str] = mapped_column(Text, nullable=False)
    summary_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    review_receipt_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExperimentMetric(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "experiment_metrics"
    __table_args__ = (UniqueConstraint("experiment_id", "name", name="uq_experiment_metrics_name"),)

    experiment_id: Mapped[UUID] = mapped_column(ForeignKey("experiments.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    split: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregation_type: Mapped[str] = mapped_column(String(100), nullable=False)
    epoch: Mapped[int | None] = mapped_column(Integer)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Memory(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "memories"
    __table_args__ = (
        UniqueConstraint("experiment_id", "memory_type", name="uq_memories_experiment_type"),
        CheckConstraint("embedding_dimension = 1024", name="memory_embedding_dimension_1024"),
        CheckConstraint("embedding_normalized", name="memory_embedding_normalized"),
        CheckConstraint("length(content_sha256) = 64", name="memory_content_sha256_length"),
        Index(
            "ix_memory_structured_filter",
            "project_id",
            "verification_status",
            "experiment_status",
            "protocol",
            "current_valid",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    experiment_id: Mapped[UUID] = mapped_column(ForeignKey("experiments.id"), nullable=False)
    protocol: Mapped[str] = mapped_column(String(200), nullable=False)
    model_name: Mapped[str] = mapped_column(String(300), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    experiment_status: Mapped[ExperimentStatus] = mapped_column(
        enum_column(ExperimentStatus, "memory_experiment_status"), nullable=False
    )
    current_valid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    memory_type: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(VectorType(1024), nullable=False)
    embedding_provider: Mapped[str] = mapped_column(String(50), default="bedrock", nullable=False)
    embedding_model_id: Mapped[str] = mapped_column(String(500), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_normalized: Mapped[bool] = mapped_column(Boolean, nullable=False)
    document_version: Mapped[str] = mapped_column(String(100), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        enum_column(VerificationStatus, "memory_verification_status"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_id: Mapped[UUID] = mapped_column(nullable=False)


class AuditLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_project_created", "project_id", "created_at"),)

    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("projects.id"))
    actor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    target_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_id: Mapped[UUID] = mapped_column(nullable=False)
    before_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class IdempotencyRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("actor_id", "operation", "idempotency_key"),
        Index("ix_idempotency_expiry", "expires_at"),
    )

    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[UUID] = mapped_column(nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    operation_status: Mapped[IdempotencyOperationStatus] = mapped_column(
        enum_column(IdempotencyOperationStatus, "idempotency_operation_status"),
        nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentThread(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """一个用户在单个项目中的私有治理对话。"""

    __tablename__ = "agent_threads"
    __table_args__ = (
        Index(
            "ix_agent_threads_owner_status_updated",
            "project_id",
            "created_by",
            "status",
            "updated_at",
        ),
        UniqueConstraint(
            "project_id",
            "created_by",
            "origin",
            "start_idempotency_key",
            name="uq_agent_threads_external_start_idempotency",
        ),
        CheckConstraint(
            "(origin = 'WEB' AND start_idempotency_key IS NULL "
            "AND start_request_hash IS NULL AND task_context_snapshot IS NULL "
            "AND task_context_hash IS NULL) OR "
            "(origin = 'EXTERNAL_MCP' AND start_idempotency_key IS NOT NULL "
            "AND start_request_hash IS NOT NULL AND task_context_snapshot IS NOT NULL "
            "AND task_context_hash IS NOT NULL)",
            name="agent_thread_origin_payload_consistent",
        ),
        CheckConstraint(
            "start_request_hash IS NULL OR length(start_request_hash) = 64",
            name="agent_thread_start_request_hash_length",
        ),
        CheckConstraint(
            "task_context_hash IS NULL OR length(task_context_hash) = 64",
            name="agent_thread_task_context_hash_length",
        ),
    )

    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    origin: Mapped[AgentThreadOrigin] = mapped_column(
        enum_column(AgentThreadOrigin, "agent_thread_origin", length=16),
        default=AgentThreadOrigin.WEB,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[AgentThreadStatus] = mapped_column(
        enum_column(AgentThreadStatus, "agent_thread_status", length=16), nullable=False
    )
    last_sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 指向最近一次 READY 摘要。为避免与摘要表形成循环建表依赖，不添加数据库外键；
    # 应用层只会在同一事务中写入已成功持久化的摘要 ID。
    current_summary_id: Mapped[UUID | None] = mapped_column()
    start_idempotency_key: Mapped[UUID | None] = mapped_column()
    start_request_hash: Mapped[str | None] = mapped_column(String(64))
    task_context_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    task_context_hash: Mapped[str | None] = mapped_column(String(64))


class AgentMessage(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """追加式对话消息；失败 Run 不伪造 Assistant 消息。"""

    __tablename__ = "agent_messages"
    __table_args__ = (
        UniqueConstraint("thread_id", "sequence", name="uq_agent_messages_thread_sequence"),
        Index("ix_agent_messages_thread_created", "thread_id", "created_at"),
        CheckConstraint("length(content_sha256) = 64", name="content_sha256_length"),
    )

    thread_id: Mapped[UUID] = mapped_column(ForeignKey("agent_threads.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[AgentMessageRole] = mapped_column(
        enum_column(AgentMessageRole, "agent_message_role", length=16), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    # 不建立循环外键；AgentRun.trigger_message_id 是用户消息的权威关联。
    run_id: Mapped[UUID | None] = mapped_column()


class AgentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """持久化 Agent 执行，同时承担 CockroachDB 租约队列。"""

    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("thread_id", "idempotency_key", name="uq_agent_runs_thread_idempotency"),
        Index(
            "ix_agent_runs_claim",
            "status",
            "available_at",
            "lease_expires_at",
            "created_at",
        ),
        Index("ix_agent_runs_thread_created", "thread_id", "created_at"),
        Index(
            "ix_agent_runs_project_observability",
            "project_id",
            "created_at",
            "provider",
            "model_id",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint("generation >= 0", name="generation_nonnegative"),
        CheckConstraint(
            "(run_kind = 'CONVERSATION' AND target_experiment_plan_revision_id IS NULL) OR "
            "(run_kind = 'EXPERIMENT_PLAN_REVIEW' "
            "AND target_experiment_plan_revision_id IS NOT NULL)",
            name="agent_run_kind_target_consistent",
        ),
        CheckConstraint(
            "(auth_method = 'WEB_SESSION' AND auth_session_id IS NOT NULL "
            "AND auth_access_token_id IS NULL AND auth_oauth_grant_id IS NULL) OR "
            "(auth_method = 'MCP_TOKEN' AND auth_session_id IS NULL "
            "AND auth_access_token_id IS NOT NULL AND auth_oauth_grant_id IS NULL) OR "
            "(auth_method = 'MCP_OAUTH' AND auth_session_id IS NULL "
            "AND auth_access_token_id IS NULL AND auth_oauth_grant_id IS NOT NULL "
            "AND auth_expires_at IS NOT NULL)",
            name="agent_run_auth_binding_consistent",
        ),
    )

    thread_id: Mapped[UUID] = mapped_column(ForeignKey("agent_threads.id"), nullable=False)
    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    auth_method: Mapped[AgentRunAuthMethod] = mapped_column(
        enum_column(AgentRunAuthMethod, "agent_run_auth_method", length=16),
        default=AgentRunAuthMethod.WEB_SESSION,
        nullable=False,
    )
    auth_session_id: Mapped[UUID | None] = mapped_column(ForeignKey("web_sessions.id"))
    auth_access_token_id: Mapped[UUID | None] = mapped_column(ForeignKey("access_tokens.id"))
    auth_oauth_grant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("mcp_oauth_grants.id")
    )
    auth_scopes_snapshot: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    auth_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trigger_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_messages.id"), nullable=False
    )
    idempotency_key: Mapped[UUID] = mapped_column(nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[AgentRunStatus] = mapped_column(
        enum_column(AgentRunStatus, "agent_run_status", length=32), nullable=False
    )
    run_kind: Mapped[AgentRunKind] = mapped_column(
        enum_column(AgentRunKind, "agent_run_kind", length=32),
        default=AgentRunKind.CONVERSATION,
        nullable=False,
    )
    # 计划 revision 反向关联 Review/Run，避免与 revision.source_run_id 形成循环外键。
    target_experiment_plan_revision_id: Mapped[UUID | None] = mapped_column()
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[str] = mapped_column(String(500), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    tool_catalog_version: Mapped[str] = mapped_column(String(50), nullable=False)
    context_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    final_message_id: Mapped[UUID | None] = mapped_column()
    generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    lease_owner: Mapped[str | None] = mapped_column(String(300))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExperimentPlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """一个外部任务中的稳定实验计划容器。"""

    __tablename__ = "experiment_plans"
    __table_args__ = (
        UniqueConstraint("source_thread_id", name="uq_experiment_plans_source_thread"),
        Index("ix_experiment_plans_project_status_updated", "project_id", "status", "updated_at"),
        Index("ix_experiment_plans_creator_updated", "created_by", "updated_at"),
        CheckConstraint("current_revision >= 1", name="experiment_plan_revision_positive"),
    )

    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    source_thread_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_threads.id"), nullable=False
    )
    status: Mapped[ExperimentPlanStatus] = mapped_column(
        enum_column(ExperimentPlanStatus, "experiment_plan_status", length=32),
        nullable=False,
    )
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False)


class ExperimentPlanRevision(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """完整、追加式的自然语言计划版本。"""

    __tablename__ = "experiment_plan_revisions"
    __table_args__ = (
        UniqueConstraint("plan_id", "revision", name="uq_experiment_plan_revision"),
        Index("ix_experiment_plan_revisions_plan_created", "plan_id", "created_at"),
        CheckConstraint("revision >= 1", name="experiment_plan_revision_number_positive"),
        CheckConstraint(
            "automatic_revision_round >= 0 AND automatic_revision_round <= 2",
            name="experiment_plan_auto_round_range",
        ),
        CheckConstraint("length(policy_hash) = 64", name="experiment_plan_policy_hash_length"),
        CheckConstraint("length(content_hash) = 64", name="experiment_plan_content_hash_length"),
        CheckConstraint("length(evidence_hash) = 64", name="experiment_plan_evidence_hash_length"),
    )

    plan_id: Mapped[UUID] = mapped_column(ForeignKey("experiment_plans.id"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    author_type: Mapped[ExperimentPlanRevisionAuthor] = mapped_column(
        enum_column(
            ExperimentPlanRevisionAuthor,
            "experiment_plan_revision_author",
            length=32,
        ),
        nullable=False,
    )
    author_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    parent_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("experiment_plan_revisions.id")
    )
    source_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    automatic_revision_round: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    plan_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    context_id: Mapped[UUID] = mapped_column(ForeignKey("project_contexts.id"), nullable=False)
    context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    intent_id: Mapped[UUID | None] = mapped_column(ForeignKey("experiment_intents.id"))
    intent_version: Mapped[int | None] = mapped_column(Integer)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ExperimentPlanReview(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """某个 revision 的成功审核结果；失败尝试保留在 Agent Run 中。"""

    __tablename__ = "experiment_plan_reviews"
    __table_args__ = (
        UniqueConstraint("revision_id", name="uq_experiment_plan_reviews_revision"),
        UniqueConstraint("source_run_id", name="uq_experiment_plan_reviews_source_run"),
        CheckConstraint("length(review_hash) = 64", name="experiment_plan_review_hash_length"),
        CheckConstraint(
            "length(approval_digest) = 64",
            name="experiment_plan_approval_digest_length",
        ),
    )

    revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_plan_revisions.id"), nullable=False
    )
    source_run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), nullable=False)
    final_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_messages.id"), nullable=False
    )
    hard_check: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    semantic_review: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    candidate_invariants: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    approval_receipt: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    review_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[str] = mapped_column(String(500), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)


class ExperimentPlanDecision(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """人类对精确计划 revision 和候选不变量作出的不可变决定。"""

    __tablename__ = "experiment_plan_decisions"
    __table_args__ = (
        UniqueConstraint("revision_id", name="uq_experiment_plan_decisions_revision"),
        CheckConstraint("length(review_hash) = 64", name="experiment_plan_decision_review_hash"),
        CheckConstraint("length(decision_hash) = 64", name="experiment_plan_decision_hash"),
    )

    plan_id: Mapped[UUID] = mapped_column(ForeignKey("experiment_plans.id"), nullable=False)
    revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_plan_revisions.id"), nullable=False
    )
    decided_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    decided_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("web_sessions.id"), nullable=False
    )
    decision: Mapped[ExperimentPlanDecisionType] = mapped_column(
        enum_column(ExperimentPlanDecisionType, "experiment_plan_decision", length=32),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    conditions: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    confirmed_candidate_invariants: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    rejected_candidate_invariants: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    approved_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    review_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class AgentModelCall(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "agent_model_calls"
    __table_args__ = (
        UniqueConstraint("run_id", "generation", "ordinal", name="uq_agent_model_call_order"),
        Index("ix_agent_model_calls_run", "run_id", "generation", "ordinal"),
        Index(
            "ix_agent_model_calls_observability",
            "provider",
            "model_id",
            "created_at",
            "status",
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="latency_nonnegative",
        ),
        CheckConstraint(
            "estimated_cost IS NULL OR estimated_cost >= 0",
            name="estimated_cost_nonnegative",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[AgentModelCallPurpose] = mapped_column(
        enum_column(AgentModelCallPurpose, "agent_model_call_purpose", length=24),
        default=AgentModelCallPurpose.AGENT_TURN,
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[AgentCallStatus] = mapped_column(
        enum_column(AgentCallStatus, "agent_call_status", length=16), nullable=False
    )
    request_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    response_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    provider_request_id: Mapped[str | None] = mapped_column(String(500))
    finish_reason: Mapped[str | None] = mapped_column(String(100))
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost_currency: Mapped[str | None] = mapped_column(String(3))
    input_cost_per_million: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    output_cost_per_million: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentContextSummary(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """对较早对话消息的非权威滚动摘要；失败尝试也保留审计记录。"""

    __tablename__ = "agent_context_summaries"
    __table_args__ = (
        UniqueConstraint("run_id", "generation", name="uq_agent_context_summaries_run_generation"),
        Index(
            "ix_agent_context_summaries_thread_created",
            "thread_id",
            "created_at",
        ),
        CheckConstraint(
            "(status = 'READY' AND payload IS NOT NULL AND error IS NULL) OR "
            "(status = 'FAILED' AND payload IS NULL AND error IS NOT NULL)",
            name="state_consistent",
        ),
        CheckConstraint(
            "covered_sequence_to >= covered_sequence_from",
            name="sequence_range_valid",
        ),
        CheckConstraint("length(source_hash) = 64", name="source_hash_length"),
    )

    thread_id: Mapped[UUID] = mapped_column(ForeignKey("agent_threads.id"), nullable=False)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AgentContextSummaryStatus] = mapped_column(
        enum_column(
            AgentContextSummaryStatus,
            "agent_context_summary_status",
            length=16,
        ),
        nullable=False,
    )
    covered_sequence_from: Mapped[int] = mapped_column(Integer, nullable=False)
    covered_sequence_to: Mapped[int] = mapped_column(Integer, nullable=False)
    source_message_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[str] = mapped_column(String(500), nullable=False)
    model_call_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_model_calls.id"))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))


class AgentToolCall(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "agent_tool_calls"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "generation", "call_id", name="uq_agent_tool_calls_provider_call"
        ),
        UniqueConstraint("run_id", "generation", "sequence", name="uq_agent_tool_calls_order"),
        CheckConstraint("length(arguments_hash) = 64", name="arguments_hash_length"),
    )

    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    call_id: Mapped[str] = mapped_column(String(200), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[AgentCallStatus] = mapped_column(
        enum_column(AgentCallStatus, "agent_tool_call_status", length=16), nullable=False
    )
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    arguments_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output_hash: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentPolicyDraft(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """完整 Policy Bundle 候选的稳定容器；当前 revision 指向追加式历史。"""

    __tablename__ = "agent_policy_drafts"
    __table_args__ = (
        Index(
            "ix_agent_policy_drafts_project_status_updated",
            "project_id",
            "status",
            "updated_at",
        ),
        Index(
            "ix_agent_policy_drafts_creator_status_updated",
            "created_by",
            "status",
            "updated_at",
        ),
        CheckConstraint("current_revision >= 1", name="current_revision_positive"),
        CheckConstraint("length(base_policy_hash) = 64", name="base_policy_hash_length"),
    )

    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    originating_thread_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_threads.id"), nullable=False
    )
    status: Mapped[PolicyDraftStatus] = mapped_column(
        enum_column(PolicyDraftStatus, "agent_policy_draft_status", length=16),
        nullable=False,
    )
    base_context_id: Mapped[UUID] = mapped_column(
        ForeignKey("project_contexts.id"), nullable=False
    )
    base_context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    base_intent_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_intents.id"), nullable=False
    )
    base_intent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    base_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    base_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    abandoned_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    abandon_reason: Mapped[str | None] = mapped_column(Text)


class AgentPolicyDraftRevision(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """不可变草稿 revision；模型失败重试通过 source_run_id 返回原结果。"""

    __tablename__ = "agent_policy_draft_revisions"
    __table_args__ = (
        UniqueConstraint(
            "draft_id",
            "revision",
            name="uq_agent_policy_draft_revisions_draft_revision",
        ),
        UniqueConstraint(
            "source_run_id",
            name="uq_agent_policy_draft_revisions_source_run",
        ),
        Index(
            "ix_agent_policy_draft_revisions_draft_created",
            "draft_id",
            "created_at",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("length(candidate_hash) = 64", name="candidate_hash_length"),
        CheckConstraint("length(source_request_hash) = 64", name="source_request_hash_length"),
        CheckConstraint("length(pending_state_hash) = 64", name="pending_state_hash_length"),
    )

    draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_policy_drafts.id"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    author_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    source: Mapped[PolicyDraftSource] = mapped_column(
        enum_column(PolicyDraftSource, "agent_policy_draft_source", length=16),
        nullable=False,
    )
    source_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    source_tool_call_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_tool_calls.id")
    )
    candidate_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    candidate_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    change_summary: Mapped[str] = mapped_column(Text, nullable=False)
    unresolved_ambiguities: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    readiness: Mapped[PolicyDraftReadiness] = mapped_column(
        enum_column(
            PolicyDraftReadiness,
            "agent_policy_draft_readiness",
            length=32,
        ),
        nullable=False,
    )
    validation_report: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    diff_snapshot: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    narrative_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    impact_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    pending_state_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class AgentActionProposal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Agent 准备、由人类确认的不可变高影响操作快照。"""

    __tablename__ = "agent_action_proposals"
    __table_args__ = (
        UniqueConstraint(
            "source_run_id",
            name="uq_agent_action_proposals_source_run",
        ),
        Index(
            "ix_agent_action_proposals_project_status_created",
            "project_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_agent_action_proposals_creator_status_created",
            "created_by",
            "status",
            "created_at",
        ),
        Index(
            "ix_agent_action_proposals_plan_status",
            "target_plan_check_id",
            "status",
        ),
        Index(
            "ix_agent_action_proposals_submission_status",
            "target_submission_id",
            "status",
        ),
        CheckConstraint("source_draft_revision >= 1", name="source_revision_positive"),
        CheckConstraint("length(payload_hash) = 64", name="payload_hash_length"),
        CheckConstraint(
            "length(source_candidate_hash) = 64",
            name="source_candidate_hash_length",
        ),
        CheckConstraint("length(base_policy_hash) = 64", name="base_policy_hash_length"),
        CheckConstraint("length(pending_state_hash) = 64", name="pending_state_hash_length"),
        CheckConstraint("length(target_state_hash) = 64", name="target_state_hash_length"),
        CheckConstraint("length(proposal_digest) = 64", name="proposal_digest_length"),
        CheckConstraint(
            "operation != 'POLICY_PUBLISH' OR "
            "(source_draft_id IS NOT NULL AND source_draft_revision_id IS NOT NULL "
            "AND source_draft_revision IS NOT NULL AND source_candidate_hash IS NOT NULL "
            "AND base_policy_hash IS NOT NULL AND pending_state_hash IS NOT NULL "
            "AND target_plan_check_id IS NULL AND target_submission_id IS NULL "
            "AND target_state_hash IS NULL)",
            name="policy_publish_fields",
        ),
        CheckConstraint(
            "operation != 'PLAN_CHECK_DECISION' OR "
            "(source_draft_id IS NULL AND source_draft_revision_id IS NULL "
            "AND source_draft_revision IS NULL AND source_candidate_hash IS NULL "
            "AND base_policy_hash IS NULL AND pending_state_hash IS NULL "
            "AND target_plan_check_id IS NOT NULL AND target_submission_id IS NULL "
            "AND target_state_hash IS NOT NULL)",
            name="plan_decision_fields",
        ),
        CheckConstraint(
            "operation != 'SUBMISSION_DECISION' OR "
            "(source_draft_id IS NULL AND source_draft_revision_id IS NULL "
            "AND source_draft_revision IS NULL AND source_candidate_hash IS NULL "
            "AND base_policy_hash IS NULL AND pending_state_hash IS NULL "
            "AND target_plan_check_id IS NULL AND target_submission_id IS NOT NULL "
            "AND target_state_hash IS NOT NULL)",
            name="submission_decision_fields",
        ),
    )

    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    source_thread_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_threads.id"), nullable=False
    )
    source_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=False
    )
    source_tool_call_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_tool_calls.id"), nullable=False
    )
    source_draft_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_policy_drafts.id"), nullable=True
    )
    source_draft_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_policy_draft_revisions.id"), nullable=True
    )
    source_draft_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operation: Mapped[ActionProposalOperation] = mapped_column(
        enum_column(
            ActionProposalOperation,
            "agent_action_proposal_operation",
            length=32,
        ),
        nullable=False,
    )
    status: Mapped[ActionProposalStatus] = mapped_column(
        enum_column(ActionProposalStatus, "agent_action_proposal_status", length=16),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_candidate_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_plan_check_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("plan_checks.id"), nullable=True
    )
    target_submission_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("experiment_submissions.id"), nullable=True
    )
    target_state_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_context_id: Mapped[UUID] = mapped_column(
        ForeignKey("project_contexts.id"), nullable=False
    )
    base_context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    base_intent_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_intents.id"), nullable=False
    )
    base_intent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    base_policy_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    diff_snapshot: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    impact_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    pending_state_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proposal_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    confirmed_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("web_sessions.id")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canceled_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    executed_context_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("project_contexts.id")
    )
    executed_context_version: Mapped[int | None] = mapped_column(Integer)
    executed_approval_record_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("approval_records.id")
    )
    executed_experiment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("experiments.id")
    )
    execution_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    execution_error: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class AgentCitation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "agent_citations"
    __table_args__ = (
        UniqueConstraint("message_id", "evidence_id", name="uq_agent_citations_message_evidence"),
        Index("ix_agent_citations_run", "run_id"),
    )

    message_id: Mapped[UUID] = mapped_column(ForeignKey("agent_messages.id"), nullable=False)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), nullable=False)
    tool_call_id: Mapped[UUID] = mapped_column(ForeignKey("agent_tool_calls.id"), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column()
    entity_version: Mapped[str | None] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)


class AgentResearchReport(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """由显式正式实验集合生成的不可变候选分析。"""

    __tablename__ = "agent_research_reports"
    __table_args__ = (
        UniqueConstraint("source_run_id", name="uq_agent_research_reports_source_run"),
        UniqueConstraint(
            "source_tool_call_id", name="uq_agent_research_reports_source_tool_call"
        ),
        UniqueConstraint(
            "final_message_id", name="uq_agent_research_reports_final_message"
        ),
        Index(
            "ix_agent_research_reports_project_created",
            "project_id",
            "created_at",
            "id",
        ),
        CheckConstraint("length(source_hash) = 64", name="source_hash_length"),
        CheckConstraint("length(payload_hash) = 64", name="payload_hash_length"),
        CheckConstraint("schema_version = 1", name="schema_version"),
    )

    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    source_thread_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_threads.id"), nullable=False
    )
    source_run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), nullable=False)
    source_tool_call_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_tool_calls.id"), nullable=False
    )
    final_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_messages.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    experiment_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    metric_name: Mapped[str | None] = mapped_column(String(200))
    include_historical: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[str] = mapped_column(String(500), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)


class AgentResearchMemory(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """研究报告 finding 的不可变候选记忆；与正式 Experiment Memory 完全隔离。"""

    __tablename__ = "agent_research_memories"
    __table_args__ = (
        UniqueConstraint("report_id", "finding_id", name="uq_agent_research_memory_finding"),
        Index(
            "ix_agent_research_memories_filter",
            "project_id",
            "status",
            "memory_type",
            "created_at",
        ),
        CheckConstraint("length(report_source_hash) = 64", name="report_source_hash_length"),
        CheckConstraint("length(report_payload_hash) = 64", name="report_payload_hash_length"),
        CheckConstraint("length(content_hash) = 64", name="content_hash_length"),
    )

    team_id: Mapped[UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    report_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_research_reports.id"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    finding_id: Mapped[str] = mapped_column(String(16), nullable=False)
    memory_type: Mapped[ResearchMemoryType] = mapped_column(
        enum_column(ResearchMemoryType, "research_memory_type", length=32), nullable=False
    )
    status: Mapped[ResearchMemoryStatus] = mapped_column(
        enum_column(ResearchMemoryStatus, "research_memory_status", length=16), nullable=False
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    limitations: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    citation_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    experiment_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    protocols: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    source_references: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    report_source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_document: Mapped[str] = mapped_column(Text, nullable=False)
    document_version: Mapped[str] = mapped_column(String(100), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class AgentResearchMemoryEmbedding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """候选研究记忆的可恢复 embedding 任务及版本化输出。"""

    __tablename__ = "agent_research_memory_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "memory_id",
            "provider",
            "model_id",
            "document_version",
            name="uq_agent_research_memory_embedding_version",
        ),
        Index(
            "ix_agent_research_memory_embeddings_claim",
            "status",
            "available_at",
            "lease_expires_at",
            "created_at",
        ),
        CheckConstraint("dimension = 1024", name="dimension_1024"),
        CheckConstraint("length(input_sha256) = 64", name="input_sha256_length"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint("generation >= 0", name="generation_nonnegative"),
        CheckConstraint(
            "status != 'READY' OR (embedding IS NOT NULL AND normalized)",
            name="ready_output_complete",
        ),
    )

    memory_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_research_memories.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[str] = mapped_column(String(500), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    document_version: Mapped[str] = mapped_column(String(100), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(VectorType(1024))
    normalized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[ResearchMemoryEmbeddingStatus] = mapped_column(
        enum_column(
            ResearchMemoryEmbeddingStatus,
            "research_memory_embedding_status",
            length=32,
        ),
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    lease_owner: Mapped[str | None] = mapped_column(String(300))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRunEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "agent_run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_agent_run_events_sequence"),
        Index("ix_agent_run_events_replay", "run_id", "sequence"),
    )

    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
