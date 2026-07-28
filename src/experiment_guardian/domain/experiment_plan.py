"""自然语言实验计划的不可变契约和确定性证据检查。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from experiment_guardian.domain.contracts import (
    GIT_COMMIT_PATTERN,
    SHA256_PATTERN,
    ConfigurationDocument,
    ContractModel,
    ProjectContextBundle,
)
from experiment_guardian.domain.enums import (
    ExperimentPlanDecisionType,
    ExperimentPlanReviewRecommendation,
    ExperimentPlanRevisionAuthor,
    ExperimentPlanStatus,
    ProtectionLevel,
)
from experiment_guardian.domain.plan_check import flatten_configuration, parse_configuration

MAX_EXPERIMENT_PLAN_BYTES = 32 * 1024


class ExperimentPlanEvidence(ContractModel):
    """计划阶段的证据；它不等同于运行前 LocalAttestation。"""

    configuration: ConfigurationDocument | None = None
    config_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    config_summary: dict[str, Any] = Field(default_factory=dict)
    run_command: str | None = Field(default=None, min_length=1, max_length=8192)
    git_commit: str | None = Field(default=None, pattern=GIT_COMMIT_PATTERN)
    baseline_reference: str | None = Field(default=None, min_length=1, max_length=500)
    related_experiment_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_configuration_hash(self) -> ExperimentPlanEvidence:
        if len(set(self.related_experiment_ids)) != len(self.related_experiment_ids):
            raise ValueError("related_experiment_ids 不能重复")
        if self.configuration is not None:
            actual = hashlib.sha256(self.configuration.content.encode("utf-8")).hexdigest()
            if self.config_sha256 is not None and actual != self.config_sha256.lower():
                raise ValueError("config_sha256 与配置原始字节不一致")
            self.config_sha256 = actual
        return self


class ExperimentPlanSubmitRequest(ContractModel):
    title: str = Field(min_length=1, max_length=160)
    plan_markdown: str = Field(min_length=1, max_length=MAX_EXPERIMENT_PLAN_BYTES)
    evidence: ExperimentPlanEvidence = Field(default_factory=ExperimentPlanEvidence)

    @model_validator(mode="after")
    def validate_text(self) -> ExperimentPlanSubmitRequest:
        self.title = self.title.strip()
        self.plan_markdown = self.plan_markdown.strip()
        if not self.title or not self.plan_markdown:
            raise ValueError("计划标题和正文不能为空")
        if len(self.plan_markdown.encode("utf-8")) > MAX_EXPERIMENT_PLAN_BYTES:
            raise ValueError("计划正文不能超过 32 KiB")
        return self


class ExperimentPlanRevisionRequest(ExperimentPlanSubmitRequest):
    expected_revision: int = Field(ge=1)


class ExperimentPlanFinding(ContractModel):
    kind: Literal[
        "MAINLINE_ALIGNMENT",
        "HISTORICAL_DUPLICATION",
        "KNOWN_FAILURE",
        "BASELINE_CONTROL",
        "FAIRNESS",
        "RISK",
        "LOW_COST_VALIDATION",
        "AMBIGUITY",
    ]
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    statement: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=4000)
    auto_fixable: bool = False
    citation_ids: list[str] = Field(default_factory=list, max_length=30)


class ExperimentPlanCandidateInvariant(ContractModel):
    statement: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=3000)
    verification_method: str = Field(min_length=1, max_length=2000)
    representation: Literal["STRUCTURED_PARAMETER", "NATURAL_LANGUAGE"]
    parameter_path: str | None = Field(default=None, min_length=1, max_length=500)
    expected_value: Any = None
    citation_ids: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_representation(self) -> ExperimentPlanCandidateInvariant:
        if self.representation == "STRUCTURED_PARAMETER" and self.parameter_path is None:
            raise ValueError("结构化候选不变量必须提供 parameter_path")
        if self.representation == "NATURAL_LANGUAGE" and self.parameter_path is not None:
            raise ValueError("自然语言候选不变量不能伪装成结构化参数")
        return self


class ExperimentPlanReviewPayload(ContractModel):
    schema_version: Literal[1] = 1
    recommendation: ExperimentPlanReviewRecommendation
    review_markdown: str = Field(min_length=1, max_length=12000)
    findings: list[ExperimentPlanFinding] = Field(default_factory=list, max_length=30)
    candidate_invariants: list[ExperimentPlanCandidateInvariant] = Field(
        default_factory=list, max_length=20
    )
    free_exploration: list[str] = Field(default_factory=list, max_length=30)
    user_decisions: list[str] = Field(default_factory=list, max_length=20)
    revised_plan_markdown: str | None = Field(default=None, max_length=MAX_EXPERIMENT_PLAN_BYTES)
    citations: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_revision(self) -> ExperimentPlanReviewPayload:
        if self.recommendation is ExperimentPlanReviewRecommendation.REVISE:
            if not self.revised_plan_markdown or not self.revised_plan_markdown.strip():
                raise ValueError("REVISE 审核必须返回完整 revised_plan_markdown")
            if not self.findings or not any(item.auto_fixable for item in self.findings):
                raise ValueError("REVISE 审核必须说明至少一个可自动修正的问题")
            if len(self.revised_plan_markdown.encode("utf-8")) > MAX_EXPERIMENT_PLAN_BYTES:
                raise ValueError("自动修订正文不能超过 32 KiB")
            self.revised_plan_markdown = self.revised_plan_markdown.strip()
        elif self.revised_plan_markdown is not None:
            raise ValueError("非 REVISE 审核不能返回 revised_plan_markdown")
        return self


class PlanHardCheckIssue(ContractModel):
    code: str
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    message: str
    parameter_path: str | None = None
    blocking: bool = False
    current_value: Any = None
    expected_value: Any = None


class ExperimentPlanHardCheck(ContractModel):
    status: Literal["PASS", "NEEDS_ATTENTION", "BLOCKED"]
    issues: list[PlanHardCheckIssue]
    parsed_configuration: dict[str, Any] | None = None
    configuration_hash: str | None = None


class ExperimentPlanRevisionView(ContractModel):
    revision_id: UUID
    plan_id: UUID
    revision: int
    author_type: ExperimentPlanRevisionAuthor
    author_id: UUID | None = None
    parent_revision_id: UUID | None = None
    source_run_id: UUID | None = None
    automatic_revision_round: int = Field(ge=0, le=2)
    title: str
    plan_markdown: str
    evidence: ExperimentPlanEvidence
    context_id: UUID
    context_version: int = Field(ge=1)
    intent_id: UUID | None = None
    intent_version: int | None = Field(default=None, ge=1)
    policy_snapshot: dict[str, Any]
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


class ExperimentPlanReviewView(ContractModel):
    review_id: UUID
    revision_id: UUID
    source_run_id: UUID
    hard_check: ExperimentPlanHardCheck
    semantic_review: ExperimentPlanReviewPayload
    candidate_invariants: list[dict[str, Any]]
    approval_receipt: dict[str, Any]
    review_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str
    model_id: str
    prompt_version: str
    created_at: datetime


class ExperimentPlanSummary(ContractModel):
    plan_id: UUID
    project_id: UUID
    task_id: UUID
    created_by: UUID
    title: str
    status: ExperimentPlanStatus
    current_revision: int = Field(ge=1)
    freshness: Literal["CURRENT", "STALE"]
    created_at: datetime
    updated_at: datetime


class ExperimentPlanView(ContractModel):
    summary: ExperimentPlanSummary
    current: ExperimentPlanRevisionView
    review: ExperimentPlanReviewView | None = None
    decision: dict[str, Any] | None = None
    revisions: list[ExperimentPlanRevisionView]
    allowed_actions: list[str]


class ExperimentPlanPage(ContractModel):
    items: list[ExperimentPlanSummary]
    next_cursor: str | None = None


class ExperimentPlanReceipt(ContractModel):
    plan_id: UUID
    task_id: UUID
    revision_id: UUID
    revision: int = Field(ge=1)
    status: ExperimentPlanStatus
    run_id: UUID
    poll_after_seconds: float = Field(gt=0, le=30)


class ExperimentPlanDecisionRequest(ContractModel):
    expected_revision: int = Field(ge=1)
    review_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: ExperimentPlanDecisionType
    reason: str = Field(min_length=1, max_length=4000)
    conditions: list[str] = Field(default_factory=list, max_length=30)
    confirmed_candidate_ids: list[str] = Field(default_factory=list, max_length=20)
    rejected_candidate_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_decision(self) -> ExperimentPlanDecisionRequest:
        self.reason = self.reason.strip()
        self.conditions = [item.strip() for item in self.conditions if item.strip()]
        if (
            self.decision is ExperimentPlanDecisionType.CONDITIONALLY_APPROVED
            and not self.conditions
        ):
            raise ValueError("有条件批准必须提供至少一个条件")
        if self.decision is ExperimentPlanDecisionType.APPROVED and self.conditions:
            raise ValueError("无条件批准不能携带 conditions")
        confirmed = set(self.confirmed_candidate_ids)
        rejected = set(self.rejected_candidate_ids)
        if len(confirmed) != len(self.confirmed_candidate_ids) or len(rejected) != len(
            self.rejected_candidate_ids
        ):
            raise ValueError("候选不变量 ID 不能重复")
        if confirmed & rejected:
            raise ValueError("同一候选不变量不能同时确认和拒绝")
        return self


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def formal_policy_snapshot(bundle: ProjectContextBundle) -> tuple[dict[str, Any], str]:
    snapshot = bundle.model_dump(mode="json", exclude={"human_readable"})
    return snapshot, canonical_hash(snapshot)


def _strict_equal(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def evaluate_plan_evidence(
    evidence: ExperimentPlanEvidence,
    bundle: ProjectContextBundle,
) -> ExperimentPlanHardCheck:
    """只检查计划已提供的结构化证据，不把缺失的未来运行证据伪装成错误。"""

    if evidence.configuration is None:
        return ExperimentPlanHardCheck(
            status="NEEDS_ATTENTION",
            issues=[
                PlanHardCheckIssue(
                    code="CONFIG_EVIDENCE_NOT_PROVIDED",
                    severity="MEDIUM",
                    message="计划尚未提供可由云端解析的配置证据，运行前仍需正式核对。",
                )
            ],
        )
    parsed = parse_configuration(evidence.configuration)
    flattened = flatten_configuration(parsed)
    issues: list[PlanHardCheckIssue] = []
    blocked = False
    for constraint in bundle.constraints:
        current = flattened.get(constraint.parameter_path, _MISSING)
        if current is _MISSING:
            continue
        if constraint.protection_level is ProtectionLevel.LOCKED and not _strict_equal(
            current, constraint.expected_value
        ):
            blocked = True
            issues.append(
                PlanHardCheckIssue(
                    code="LOCKED_PARAMETER_CONFLICT",
                    severity="CRITICAL",
                    message=f"配置证据中的 {constraint.parameter_path} 违反正式 LOCKED 约束。",
                    parameter_path=constraint.parameter_path,
                    blocking=True,
                    current_value=current,
                    expected_value=constraint.expected_value,
                )
            )
        elif constraint.protection_level is ProtectionLevel.APPROVAL_REQUIRED and not _strict_equal(
            current, constraint.expected_value
        ):
            issues.append(
                PlanHardCheckIssue(
                    code="FORMAL_PARAMETER_APPROVAL_REQUIRED",
                    severity="HIGH",
                    message=(
                        f"配置证据中的 {constraint.parameter_path} 仍须通过正式 "
                        "Plan Check 的 Owner 审批。"
                    ),
                    parameter_path=constraint.parameter_path,
                    current_value=current,
                    expected_value=constraint.expected_value,
                )
            )
    config_hash = hashlib.sha256(evidence.configuration.content.encode("utf-8")).hexdigest()
    return ExperimentPlanHardCheck(
        status="BLOCKED" if blocked else "NEEDS_ATTENTION" if issues else "PASS",
        issues=issues,
        parsed_configuration=parsed,
        configuration_hash=config_hash,
    )


_MISSING = object()
