"""P0 数据库实体。

模型优先表达追溯链和状态约束，不在 ORM 中堆叠复杂级联关系。关键写操作会由应用服务在
显式事务中按顺序执行，便于审计，也更符合 CockroachDB 事务重试的要求。
"""

from datetime import datetime
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
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from experiment_guardian.domain.enums import (
    ApprovalDecision,
    ApprovalStatus,
    ApprovalTargetType,
    ArtifactType,
    CheckResult,
    ConstraintSource,
    ContextStatus,
    EvidenceType,
    ExperimentMode,
    ExperimentStatus,
    IdempotencyOperationStatus,
    IntentStatus,
    ProtectionLevel,
    RiskSeverity,
    SubmissionStatus,
    SubmittedRunStatus,
    TeamRole,
    TokenAudience,
    VerificationStatus,
)
from experiment_guardian.infrastructure.models.base import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    VectorType,
)


def enum_column(enum_type: type[Any], name: str) -> Enum:
    """使用字符串 CHECK/Enum 映射，避免依赖 PostgreSQL 原生枚举迁移。"""

    return Enum(enum_type, name=name, native_enum=False, validate_strings=True)


class User(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)


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
        enum_column(SubmissionStatus, "submission_status"), nullable=False
    )


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
    # experiments 表尚未迁移；R9 始终写 NULL，正式实验切片再补数据库外键。
    experiment_id: Mapped[UUID | None] = mapped_column()
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(200), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    s3_key: Mapped[str] = mapped_column(String(1500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_type: Mapped[ArtifactType] = mapped_column(
        enum_column(ArtifactType, "artifact_type"), nullable=False
    )
    cloud_hash_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SubmissionRisk(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "submission_risks"

    submission_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiment_submissions.id"), nullable=False, index=True
    )
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
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExperimentMetric(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "experiment_metrics"
    __table_args__ = (
        UniqueConstraint("experiment_id", "name", "split", "aggregation_type", "epoch"),
    )

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
    embedding: Mapped[list[float]] = mapped_column(VectorType(1536), nullable=False)
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
