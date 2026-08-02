"""R14 Web 管理端读取、版本发布和审核列表契约。"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from experiment_guardian.domain.administration import (
    InitialConstraintInput,
    InitialContextInput,
    InitialIntentInput,
)
from experiment_guardian.domain.contracts import (
    ContractModel,
    HumanReadablePolicy,
    PresignedDownload,
    ProjectContextBundle,
    SubmissionMaterialProvenance,
)
from experiment_guardian.domain.enums import (
    ApprovalStatus,
    CheckResult,
    ExperimentMode,
    ExperimentStatus,
    RiskSeverity,
    SubmissionStatus,
    WorkflowStatus,
    WorkflowStep,
)


class ProjectSummary(ContractModel):
    project_id: UUID
    name: str
    description: str
    repository_url: str | None = None
    active: bool


class ProjectList(ContractModel):
    items: list[ProjectSummary]


class ContextVersionSummary(ContractModel):
    context_id: UUID
    version: int
    status: str
    change_reason: str
    created_by: UUID
    confirmed_by: UUID | None = None
    confirmed_at: datetime | None = None
    effective_at: datetime | None = None
    created_at: datetime
    human_readable: HumanReadablePolicy | None = None


class ProjectSettingsView(ContractModel):
    project: ProjectSummary
    current: ProjectContextBundle
    context_history: list[ContextVersionSummary]


class PolicyPublishRequest(ContractModel):
    expected_context_version: int = Field(gt=0)
    context: InitialContextInput
    intent: InitialIntentInput
    constraints: list[InitialConstraintInput] = Field(min_length=1)

    @model_validator(mode="after")
    def require_consistent_constraint_paths(self) -> "PolicyPublishRequest":
        paths = [item.parameter_path for item in self.constraints]
        if len(paths) != len(set(paths)):
            raise ValueError("发布约束的 parameter_path 不能重复")
        variables = {
            item.parameter_path
            for item in self.constraints
            if item.protection_level.value == "EXPERIMENT_VARIABLE"
        }
        if variables != set(self.intent.allowed_variables):
            raise ValueError("intent.allowed_variables 必须与 EXPERIMENT_VARIABLE 约束完全一致")
        return self


class PolicyPublishResult(ContractModel):
    project_id: UUID
    previous_context_version: int
    context_bundle: ProjectContextBundle


class PlanCheckWebView(ContractModel):
    plan_check_id: UUID
    project_id: UUID
    requester_id: UUID
    context_id: UUID
    context_version: int
    intent_id: UUID
    intent_version: int
    experiment_mode: ExperimentMode
    check_result: CheckResult
    approval_status: ApprovalStatus
    risk_level: RiskSeverity
    planned_changes: list[Any]
    report: dict[str, Any]
    git_commit: str
    command: str
    experiment_plan_decision_id: UUID | None = None
    experiment_plan_trace: dict[str, Any] | None = None
    invariant_check: dict[str, Any] | None = None
    created_at: datetime
    allowed_actions: list[str]


class PlanCheckPage(ContractModel):
    items: list[PlanCheckWebView]
    next_cursor: str | None = None


class ArtifactWebView(ContractModel):
    artifact_id: UUID
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    artifact_type: str
    cloud_hash_verified: bool
    s3_version_id: str | None = None
    material_origin: str
    provenance: dict[str, Any]


class SubmissionWebView(ContractModel):
    submission_id: UUID
    project_id: UUID
    run_manifest_id: UUID
    submitted_by: UUID
    source_agent: str
    status: SubmissionStatus
    workflow_status: WorkflowStatus
    processing_step: WorkflowStep | None = None
    processing_error: dict[str, Any] | None = None
    generated_summary: dict[str, Any] | None = None
    review_receipt: dict[str, Any] | None = None
    invariant_check: dict[str, Any] | None = None
    material_provenance: SubmissionMaterialProvenance
    risks: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[ArtifactWebView] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str]


class SubmissionPage(ContractModel):
    items: list[SubmissionWebView]
    next_cursor: str | None = None


class ExperimentWebView(ContractModel):
    experiment_id: UUID
    project_id: UUID
    submission_id: UUID
    run_manifest_id: UUID
    name: str
    model_name: str
    dataset: str
    protocol: str
    seed: int
    experiment_mode: ExperimentMode
    status: ExperimentStatus
    context_id: UUID
    context_version: int
    intent_id: UUID
    intent_version: int
    config_hash: str
    git_commit: str
    summary: dict[str, Any]
    confirmed_at: datetime
    created_at: datetime
    detail_level: Literal["SUMMARY", "FULL"] = "SUMMARY"


class ExperimentMetricWebView(ContractModel):
    name: str
    value: float
    split: str
    aggregation_type: str
    epoch: int | None = None
    is_primary: bool


class ExperimentDetailWebView(ExperimentWebView):
    detail_level: Literal["FULL"] = "FULL"
    metrics: list[ExperimentMetricWebView] = Field(default_factory=list)
    artifacts: list[ArtifactWebView] = Field(default_factory=list)
    material_provenance: SubmissionMaterialProvenance
    final_run_evidence: dict[str, Any] | None = None


class ExperimentPage(ContractModel):
    items: list[ExperimentWebView]
    next_cursor: str | None = None


class ArtifactDownloadResult(PresignedDownload):
    artifact_id: UUID
