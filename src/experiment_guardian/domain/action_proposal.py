"""Agent 高影响操作提案契约。

提案只冻结一次待确认操作及其证据。确认者必须使用经过近期认证的 Web Session，并继续
满足目标业务原有的 Owner/Researcher 权限；Agent 自身没有执行工具。正式操作仍由现有
Policy、Plan 或 Submission 业务服务完成。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from experiment_guardian.domain.administration import (
    PlanCheckDecisionRequest,
    SubmissionDecisionRequest,
)
from experiment_guardian.domain.contracts import ContractModel
from experiment_guardian.domain.enums import (
    ActionProposalConfirmability,
    ActionProposalOperation,
    ActionProposalStatus,
    ApprovalDecision,
)
from experiment_guardian.domain.policy_draft import canonical_json
from experiment_guardian.domain.web_management import PolicyPublishRequest

ACTION_PROPOSAL_SCHEMA_VERSION = 1
ACTION_PROPOSAL_TTL_HOURS = 24


class ActionProposalPrepareInput(ContractModel):
    draft_id: UUID


class PlanDecisionProposalPrepareInput(ContractModel):
    plan_check_id: UUID
    decision: ApprovalDecision
    decision_reason: str = Field(min_length=1, max_length=2000)

    @field_validator("decision_reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Plan 决策提案必须提供明确理由")
        return normalized


class SubmissionDecisionProposalPrepareInput(ContractModel):
    submission_id: UUID
    decision: ApprovalDecision
    decision_reason: str = Field(min_length=1, max_length=2000)

    @field_validator("decision_reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Submission 审核提案必须提供明确理由")
        return normalized


class ActionProposalConfirmRequest(ContractModel):
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ActionProposalCancelRequest(ContractModel):
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=2000)


class ActionProposalView(ContractModel):
    proposal_id: UUID
    project_id: UUID
    created_by: UUID
    operation: ActionProposalOperation
    status: ActionProposalStatus
    confirmability: ActionProposalConfirmability
    confirmability_reasons: list[str] = Field(default_factory=list)
    allowed_actions: list[Literal["CONFIRM", "CANCEL"]] = Field(default_factory=list)
    source_thread_id: UUID
    source_run_id: UUID
    source_tool_call_id: UUID
    source_draft_id: UUID | None = None
    source_draft_revision_id: UUID | None = None
    source_draft_revision: int | None = None
    source_candidate_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    target_plan_check_id: UUID | None = None
    target_submission_id: UUID | None = None
    target_state_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    payload: PolicyPublishRequest | PlanCheckDecisionRequest | SubmissionDecisionRequest
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_context_id: UUID
    base_context_version: int
    base_intent_id: UUID
    base_intent_version: int
    base_policy_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    diff_snapshot: list[dict[str, Any]]
    impact_snapshot: dict[str, Any]
    pending_state_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    confirmed_by: UUID | None = None
    confirmed_at: datetime | None = None
    canceled_by: UUID | None = None
    canceled_at: datetime | None = None
    cancel_reason: str | None = None
    executed_context_id: UUID | None = None
    executed_context_version: int | None = None
    executed_approval_record_id: UUID | None = None
    executed_experiment_id: UUID | None = None
    execution_result: dict[str, Any] | None = None
    execution_error: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def parse_payload_for_operation(cls, value: Any) -> Any:
        if not isinstance(value, dict) or not isinstance(value.get("payload"), dict):
            return value
        parsed = dict(value)
        raw_operation = parsed.get("operation")
        if not isinstance(raw_operation, (str, ActionProposalOperation)):
            return value
        operation = ActionProposalOperation(raw_operation)
        if operation is ActionProposalOperation.POLICY_PUBLISH:
            parsed["payload"] = PolicyPublishRequest.model_validate(parsed["payload"])
        elif operation is ActionProposalOperation.PLAN_CHECK_DECISION:
            parsed["payload"] = PlanCheckDecisionRequest.model_validate(parsed["payload"])
        else:
            parsed["payload"] = SubmissionDecisionRequest.model_validate(parsed["payload"])
        return parsed

    @model_validator(mode="after")
    def validate_operation_fields(self) -> ActionProposalView:
        policy_fields = (
            self.source_draft_id,
            self.source_draft_revision_id,
            self.source_draft_revision,
            self.source_candidate_hash,
            self.base_policy_hash,
            self.pending_state_hash,
        )
        if self.operation is ActionProposalOperation.POLICY_PUBLISH:
            if any(value is None for value in policy_fields):
                raise ValueError("Policy 发布提案缺少草稿或正式策略来源")
            if (
                self.target_plan_check_id is not None
                or self.target_submission_id is not None
                or self.target_state_hash is not None
            ):
                raise ValueError("Policy 发布提案不能绑定决策目标")
            if not isinstance(self.payload, PolicyPublishRequest):
                raise ValueError("Policy 发布提案 payload 类型错误")
        elif self.operation is ActionProposalOperation.PLAN_CHECK_DECISION:
            if self.target_plan_check_id is None or self.target_state_hash is None:
                raise ValueError("Plan 决策提案缺少目标或状态哈希")
            if self.target_submission_id is not None:
                raise ValueError("Plan 决策提案不能绑定 Submission")
            if any(value is not None for value in policy_fields):
                raise ValueError("Plan 决策提案不能绑定 Policy Draft")
            if not isinstance(self.payload, PlanCheckDecisionRequest):
                raise ValueError("Plan 决策提案 payload 类型错误")
        elif self.operation is ActionProposalOperation.SUBMISSION_DECISION:
            if self.target_submission_id is None or self.target_state_hash is None:
                raise ValueError("Submission 审核提案缺少目标或状态哈希")
            if self.target_plan_check_id is not None:
                raise ValueError("Submission 审核提案不能绑定 Plan Check")
            if any(value is not None for value in policy_fields):
                raise ValueError("Submission 审核提案不能绑定 Policy Draft")
            if not isinstance(self.payload, SubmissionDecisionRequest):
                raise ValueError("Submission 审核提案 payload 类型错误")
        return self


class ActionProposalPage(ContractModel):
    items: list[ActionProposalView]
    next_cursor: str | None = None


def build_action_proposal_digest(
    *,
    proposal_id: UUID,
    operation: ActionProposalOperation,
    project_id: UUID,
    payload: PolicyPublishRequest,
    source_draft_id: UUID,
    source_draft_revision_id: UUID,
    source_draft_revision: int,
    source_candidate_hash: str,
    base_policy_hash: str,
    pending_state_hash: str,
    expires_at: datetime,
) -> str:
    """绑定所有影响执行含义的字段，避免确认请求被替换或跨提案复用。"""

    canonical = canonical_json(
        {
            "schema_version": ACTION_PROPOSAL_SCHEMA_VERSION,
            "proposal_id": str(proposal_id),
            "operation": operation.value,
            "project_id": str(project_id),
            "payload": payload.model_dump(mode="json"),
            "source_draft_id": str(source_draft_id),
            "source_draft_revision_id": str(source_draft_revision_id),
            "source_draft_revision": source_draft_revision,
            "source_candidate_hash": source_candidate_hash,
            "base_policy_hash": base_policy_hash,
            "pending_state_hash": pending_state_hash,
            "expires_at": expires_at.isoformat(),
        }
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_plan_decision_proposal_digest(
    *,
    proposal_id: UUID,
    project_id: UUID,
    plan_check_id: UUID,
    payload: PlanCheckDecisionRequest,
    target_state_hash: str,
    base_context_id: UUID,
    base_context_version: int,
    base_intent_id: UUID,
    base_intent_version: int,
    expires_at: datetime,
) -> str:
    """冻结 Plan 决定、正式依据版本和过期时间。"""

    canonical = canonical_json(
        {
            "schema_version": 1,
            "proposal_id": str(proposal_id),
            "operation": ActionProposalOperation.PLAN_CHECK_DECISION.value,
            "project_id": str(project_id),
            "plan_check_id": str(plan_check_id),
            "payload": payload.model_dump(mode="json"),
            "target_state_hash": target_state_hash,
            "base_context_id": str(base_context_id),
            "base_context_version": base_context_version,
            "base_intent_id": str(base_intent_id),
            "base_intent_version": base_intent_version,
            "expires_at": expires_at.isoformat(),
        }
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_submission_decision_proposal_digest(
    *,
    proposal_id: UUID,
    project_id: UUID,
    submission_id: UUID,
    payload: SubmissionDecisionRequest,
    target_state_hash: str,
    base_context_id: UUID,
    base_context_version: int,
    base_intent_id: UUID,
    base_intent_version: int,
    expires_at: datetime,
) -> str:
    """冻结 Submission 决定、审核依据版本和过期时间。"""

    canonical = canonical_json(
        {
            "schema_version": 1,
            "proposal_id": str(proposal_id),
            "operation": ActionProposalOperation.SUBMISSION_DECISION.value,
            "project_id": str(project_id),
            "submission_id": str(submission_id),
            "payload": payload.model_dump(mode="json"),
            "target_state_hash": target_state_hash,
            "base_context_id": str(base_context_id),
            "base_context_version": base_context_version,
            "base_intent_id": str(base_intent_id),
            "base_intent_version": base_intent_version,
            "expires_at": expires_at.isoformat(),
        }
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
