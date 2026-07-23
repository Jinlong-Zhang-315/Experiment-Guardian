"""治理 Agent 使用的完整 Policy Bundle 候选与确定性分析。

草稿只保存候选含义。正式 Context、Intent 和 Constraints 仍只能通过现有发布服务写入；
本模块不会读取数据库，也不会把模型推断升级为正式规则。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from experiment_guardian.domain.administration import (
    InitialConstraintInput,
    InitialContextInput,
    InitialIntentInput,
)
from experiment_guardian.domain.contracts import ContractModel
from experiment_guardian.domain.enums import (
    ApprovalStatus,
    CheckResult,
    PolicyDraftFreshness,
    PolicyDraftReadiness,
    PolicyDraftSource,
    PolicyDraftStatus,
    ProtectionLevel,
    SubmissionStatus,
)
from experiment_guardian.domain.plan_check import ConfigurationError, flatten_configuration

MAX_POLICY_DRAFT_BYTES = 256 * 1024
MAX_DRAFT_AMBIGUITIES = 20
POLICY_DRAFT_NARRATIVE_VERSION = "policy-draft-template-v1"
POLICY_DRAFT_NOTICE = (
    "该内容是尚未生效的治理候选草稿。Plan Check、审批、Manifest 和实验确认仍以当前正式"
    "结构化 Policy Bundle 为准。"
)


def canonical_json(value: Any) -> str:
    """生成保留 JSON 类型差异的稳定表示。"""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def strict_json_equal(left: Any, right: Any) -> bool:
    """避免 Python 把 ``True``、``1`` 和 ``1.0`` 当成相同配置值。"""

    try:
        return canonical_json(left) == canonical_json(right)
    except (TypeError, ValueError):
        return False


class PolicyDraftCandidate(ContractModel):
    """一个完整候选 Bundle；语义冲突由确定性校验报告而非模型自行隐藏。"""

    context: InitialContextInput
    intent: InitialIntentInput
    constraints: list[InitialConstraintInput] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_json_serializable_size(self) -> PolicyDraftCandidate:
        try:
            encoded = canonical_json(self.model_dump(mode="json")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("Policy Bundle 草稿包含非 JSON 或非有限数值") from exc
        if len(encoded) > MAX_POLICY_DRAFT_BYTES:
            raise ValueError("Policy Bundle 草稿不能超过 256 KiB")
        return self


class PolicyDraftAmbiguity(ContractModel):
    field_path: str = Field(min_length=1, max_length=1000)
    question: str = Field(min_length=1, max_length=2000)
    source_text: str = Field(min_length=1, max_length=4000)


class PolicyDraftValidationIssue(ContractModel):
    code: str = Field(min_length=1, max_length=100)
    field_path: str = Field(min_length=1, max_length=1000)
    message: str = Field(min_length=1, max_length=2000)


class PolicyDraftDiffItem(ContractModel):
    field_path: str = Field(min_length=1, max_length=1200)
    change_type: Literal["ADDED", "MODIFIED", "REMOVED"]
    previous_value: Any = None
    candidate_value: Any = None
    attention_level: Literal["LOW", "MEDIUM", "HIGH"]
    impact: str = Field(min_length=1, max_length=2000)


class PolicyDraftValidation(ContractModel):
    readiness: PolicyDraftReadiness
    issues: list[PolicyDraftValidationIssue] = Field(default_factory=list)
    unresolved_ambiguities: list[PolicyDraftAmbiguity] = Field(default_factory=list)


class PolicyDraftNarrative(ContractModel):
    status: Literal["READY", "FAILED"]
    generator_version: str = POLICY_DRAFT_NARRATIVE_VERSION
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: str | None = None
    error: str | None = None
    authoritative: Literal[False] = False
    governance_notice: str = POLICY_DRAFT_NOTICE

    @model_validator(mode="after")
    def require_consistent_state(self) -> PolicyDraftNarrative:
        if self.status == "READY" and (not self.content or self.error is not None):
            raise ValueError("READY 草稿说明缺少内容")
        if self.status == "FAILED" and (self.content is not None or not self.error):
            raise ValueError("FAILED 草稿说明必须隐藏内容并说明原因")
        return self


class PolicyDraftPlanSimulation(ContractModel):
    plan_check_id: UUID
    context_version: int
    intent_version: int
    original_check_result: CheckResult
    original_approval_status: ApprovalStatus
    simulated_check_result: CheckResult | None = None
    simulated_approval_status: ApprovalStatus | None = None
    simulated_risk_codes: list[str] = Field(default_factory=list)
    changed: bool = False
    status: Literal["SIMULATED", "FAILED"] = "SIMULATED"
    error: str | None = None
    governance_notice: str = "该结果仅模拟候选策略下的规则结论，不撤销或修改原 Plan Check。"


class PolicyDraftSubmissionImpact(ContractModel):
    submission_id: UUID
    status: SubmissionStatus
    context_version: int
    intent_version: int
    classification: Literal["IMMUTABLE_VERSION_REFERENCE"] = "IMMUTABLE_VERSION_REFERENCE"
    message: str = "该 Submission 继续绑定原 Manifest 和策略版本；候选草稿不会追溯改变其状态。"


class PolicyDraftImpact(ContractModel):
    status: Literal["COMPLETE", "PARTIAL", "NOT_EVALUATED"]
    generated_at: datetime
    pending_state_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    attention_level: Literal["LOW", "MEDIUM", "HIGH"]
    future_policy_effects: list[str] = Field(default_factory=list)
    plan_simulations: list[PolicyDraftPlanSimulation] = Field(default_factory=list)
    plan_simulations_truncated: bool = False
    submission_impacts: list[PolicyDraftSubmissionImpact] = Field(default_factory=list)
    submission_impacts_truncated: bool = False
    warnings: list[str] = Field(default_factory=list)


class PolicyDraftRevisionInput(ContractModel):
    expected_revision: int = Field(ge=1)
    candidate: PolicyDraftCandidate
    change_summary: str = Field(min_length=1, max_length=4000)
    unresolved_ambiguities: list[PolicyDraftAmbiguity] = Field(
        default_factory=list,
        max_length=MAX_DRAFT_AMBIGUITIES,
    )


class PolicyDraftCreateInput(ContractModel):
    base_context_id: UUID
    base_context_version: int = Field(gt=0)
    base_intent_id: UUID
    base_intent_version: int = Field(gt=0)
    candidate: PolicyDraftCandidate
    change_summary: str = Field(min_length=1, max_length=4000)
    unresolved_ambiguities: list[PolicyDraftAmbiguity] = Field(
        default_factory=list,
        max_length=MAX_DRAFT_AMBIGUITIES,
    )


class PolicyDraftAbandonRequest(ContractModel):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class PolicyDraftRevisionSummary(ContractModel):
    revision_id: UUID
    revision: int
    author_id: UUID
    source: PolicyDraftSource
    readiness: PolicyDraftReadiness
    candidate_hash: str
    change_summary: str
    ambiguity_count: int
    created_at: datetime


class PolicyDraftSummary(ContractModel):
    draft_id: UUID
    project_id: UUID
    created_by: UUID
    status: PolicyDraftStatus
    freshness: PolicyDraftFreshness
    base_context_id: UUID
    base_context_version: int
    base_intent_id: UUID
    base_intent_version: int
    current_revision: int
    readiness: PolicyDraftReadiness
    ambiguity_count: int
    change_summary: str
    created_at: datetime
    updated_at: datetime
    abandoned_at: datetime | None = None


class PolicyDraftPage(ContractModel):
    items: list[PolicyDraftSummary]
    next_cursor: str | None = None


class PolicyDraftRevisionView(ContractModel):
    revision_id: UUID
    draft_id: UUID
    revision: int
    author_id: UUID
    source: PolicyDraftSource
    source_run_id: UUID | None = None
    candidate: PolicyDraftCandidate
    candidate_hash: str
    change_summary: str
    unresolved_ambiguities: list[PolicyDraftAmbiguity]
    validation: PolicyDraftValidation
    diff: list[PolicyDraftDiffItem]
    narrative: PolicyDraftNarrative
    stored_impact: PolicyDraftImpact
    current_impact: PolicyDraftImpact
    impact_changed_since_revision: bool
    created_at: datetime


class PolicyDraftView(ContractModel):
    summary: PolicyDraftSummary
    current: PolicyDraftRevisionView
    revisions: list[PolicyDraftRevisionSummary]


def validate_policy_candidate(
    candidate: PolicyDraftCandidate,
    ambiguities: list[PolicyDraftAmbiguity],
) -> PolicyDraftValidation:
    issues: list[PolicyDraftValidationIssue] = []
    constraints_by_path: dict[str, list[InitialConstraintInput]] = {}
    for item in candidate.constraints:
        constraints_by_path.setdefault(item.parameter_path, []).append(item)
    for path, items in sorted(constraints_by_path.items()):
        if len(items) > 1:
            issues.append(
                PolicyDraftValidationIssue(
                    code="DUPLICATE_CONSTRAINT_PATH",
                    field_path=f"constraints.{path}",
                    message=f"参数路径 {path} 存在 {len(items)} 条候选约束",
                )
            )

    variable_paths = {
        item.parameter_path
        for item in candidate.constraints
        if item.protection_level is ProtectionLevel.EXPERIMENT_VARIABLE
    }
    allowed_paths = set(candidate.intent.allowed_variables)
    if variable_paths != allowed_paths:
        issues.append(
            PolicyDraftValidationIssue(
                code="ALLOWED_VARIABLES_MISMATCH",
                field_path="intent.allowed_variables",
                message=(
                    "intent.allowed_variables 必须与 EXPERIMENT_VARIABLE 约束路径完全一致；"
                    f"仅 Intent 包含 {sorted(allowed_paths - variable_paths)}，"
                    f"仅约束包含 {sorted(variable_paths - allowed_paths)}"
                ),
            )
        )

    try:
        flattened = flatten_configuration(candidate.context.active_config)
    except ConfigurationError as exc:
        issues.append(
            PolicyDraftValidationIssue(
                code="ACTIVE_CONFIG_INVALID",
                field_path="context.active_config",
                message=str(exc),
            )
        )
        flattened = {}
    for path, items in sorted(constraints_by_path.items()):
        if len(items) != 1:
            continue
        item = items[0]
        if path not in flattened:
            issues.append(
                PolicyDraftValidationIssue(
                    code="CONSTRAINT_PATH_MISSING",
                    field_path=f"constraints.{path}",
                    message=f"约束路径 {path} 不存在于 context.active_config",
                )
            )
        elif not strict_json_equal(flattened[path], item.expected_value):
            issues.append(
                PolicyDraftValidationIssue(
                    code="EXPECTED_VALUE_MISMATCH",
                    field_path=f"constraints.{path}.expected_value",
                    message=f"约束 {path} 的 expected_value 与 active_config 严格类型值不一致",
                )
            )

    readiness = (
        PolicyDraftReadiness.INVALID
        if issues
        else (
            PolicyDraftReadiness.NEEDS_CLARIFICATION if ambiguities else PolicyDraftReadiness.READY
        )
    )
    return PolicyDraftValidation(
        readiness=readiness,
        issues=issues,
        unresolved_ambiguities=ambiguities,
    )


def diff_policy_candidates(
    base: PolicyDraftCandidate,
    candidate: PolicyDraftCandidate,
) -> list[PolicyDraftDiffItem]:
    """生成稳定、可读且不依赖列表位置的 Bundle 差异。"""

    changes: list[PolicyDraftDiffItem] = []
    base_dump = base.model_dump(mode="json")
    candidate_dump = candidate.model_dump(mode="json")
    for section in ("context", "intent"):
        before = base_dump[section]
        after = candidate_dump[section]
        for field in sorted(set(before) | set(after)):
            if strict_json_equal(before.get(field), after.get(field)):
                continue
            changes.append(
                _diff_item(
                    field_path=f"{section}.{field}",
                    previous=before.get(field),
                    current=after.get(field),
                )
            )

    before_constraints = {item["parameter_path"]: item for item in base_dump["constraints"]}
    after_constraints = {item["parameter_path"]: item for item in candidate_dump["constraints"]}
    for path in sorted(set(before_constraints) | set(after_constraints)):
        before = before_constraints.get(path)
        after = after_constraints.get(path)
        if strict_json_equal(before, after):
            continue
        changes.append(
            _diff_item(
                field_path=f"constraints.{path}",
                previous=before,
                current=after,
            )
        )
    return changes


def _diff_item(field_path: str, previous: Any, current: Any) -> PolicyDraftDiffItem:
    if previous is None:
        change_type = "ADDED"
    elif current is None:
        change_type = "REMOVED"
    else:
        change_type = "MODIFIED"
    attention, impact = _attention(field_path, previous, current)
    return PolicyDraftDiffItem(
        field_path=field_path,
        change_type=change_type,
        previous_value=previous,
        candidate_value=current,
        attention_level=attention,
        impact=impact,
    )


def _attention(field_path: str, previous: Any, current: Any) -> tuple[str, str]:
    high_context = {
        "context.goal",
        "context.mainline_model",
        "context.baseline",
        "context.dataset",
        "context.protocol",
        "context.primary_metric",
    }
    if field_path in high_context:
        return "HIGH", "可能改变项目主线、实验可比条件或结果评价方式。"
    if field_path.startswith("constraints."):
        levels = {
            item.get("protection_level") for item in (previous, current) if isinstance(item, dict)
        }
        if ProtectionLevel.LOCKED.value in levels:
            return "HIGH", "涉及 Locked 规则，后续 Plan 的阻断条件可能变化。"
        if ProtectionLevel.APPROVAL_REQUIRED.value in levels:
            return "MEDIUM", "后续 Plan 的 Owner 审批要求可能变化。"
        return "MEDIUM", "允许实验变量或其取值边界可能变化。"
    if field_path in {
        "context.active_config",
        "context.default_seeds",
        "context.active_branch",
        "intent.allowed_variables",
        "intent.controlled_variables",
    }:
        return "MEDIUM", "后续实验配置或受控变量范围可能变化。"
    return "LOW", "主要影响说明、范围或预期输出，不直接生成正式规则。"


def render_policy_draft_narrative(
    candidate: PolicyDraftCandidate,
    diff: list[PolicyDraftDiffItem],
    validation: PolicyDraftValidation,
) -> PolicyDraftNarrative:
    source_hash = canonical_hash(candidate.model_dump(mode="json"))
    try:
        by_level: dict[ProtectionLevel, list[InitialConstraintInput]] = {
            level: [] for level in ProtectionLevel
        }
        for item in candidate.constraints:
            by_level[item.protection_level].append(item)
        lines = [
            "# Policy Bundle 治理候选草稿",
            "",
            f"> {POLICY_DRAFT_NOTICE}",
            "",
            "## 项目目标",
            candidate.context.goal,
            "",
            "## 数据集、协议与主线",
            f"- 数据集：`{candidate.context.dataset}`",
            f"- 协议：`{candidate.context.protocol}`",
            f"- 主线模型：`{candidate.context.mainline_model}`",
            f"- 主指标：`{canonical_json(candidate.context.primary_metric)}`",
            "",
            "## 实验意图",
            f"- 名称：{candidate.intent.name}",
            f"- 目标：{candidate.intent.objective}",
            f"- 假设：{candidate.intent.hypothesis}",
            f"- 允许变量：{canonical_json(candidate.intent.allowed_variables)}",
            "",
            "## 约束",
        ]
        for level in ProtectionLevel:
            lines.append(f"### {level.value}")
            values = sorted(by_level[level], key=lambda item: item.parameter_path)
            lines.extend(
                (
                    f"- `{item.parameter_path}` = `{canonical_json(item.expected_value)}`："
                    f"{item.reason}"
                )
                for item in values
            )
            if not values:
                lines.append("- 无")
        lines.extend(
            [
                "",
                "## 变更与校验",
                f"- 结构化变更：{len(diff)} 项",
                f"- 当前状态：`{validation.readiness.value}`",
                f"- 未解决歧义：{len(validation.unresolved_ambiguities)} 项",
                f"- 校验冲突：{len(validation.issues)} 项",
            ]
        )
        return PolicyDraftNarrative(
            status="READY",
            source_hash=source_hash,
            content="\n".join(lines),
        )
    except Exception as exc:
        return PolicyDraftNarrative(
            status="FAILED",
            source_hash=source_hash,
            error=f"候选说明生成失败：{str(exc)[:1800]}",
        )


def max_attention(diff: list[PolicyDraftDiffItem]) -> Literal["LOW", "MEDIUM", "HIGH"]:
    rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    return max(
        (item.attention_level for item in diff),
        key=rank.__getitem__,
        default="LOW",
    )
