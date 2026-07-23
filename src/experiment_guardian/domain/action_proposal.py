"""Agent 高影响操作提案契约。

提案只冻结一次待确认操作及其证据。只有 Web Owner 在近期认证后才能确认，Agent 自身
没有执行工具。正式发布仍由现有 Policy 发布服务完成。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from experiment_guardian.domain.contracts import ContractModel
from experiment_guardian.domain.enums import (
    ActionProposalConfirmability,
    ActionProposalOperation,
    ActionProposalStatus,
)
from experiment_guardian.domain.policy_draft import canonical_json
from experiment_guardian.domain.web_management import PolicyPublishRequest

ACTION_PROPOSAL_SCHEMA_VERSION = 1
ACTION_PROPOSAL_TTL_HOURS = 24


class ActionProposalPrepareInput(ContractModel):
    draft_id: UUID


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
    source_draft_id: UUID
    source_draft_revision_id: UUID
    source_draft_revision: int
    source_candidate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: PolicyPublishRequest
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_context_id: UUID
    base_context_version: int
    base_intent_id: UUID
    base_intent_version: int
    base_policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    diff_snapshot: list[dict[str, Any]]
    impact_snapshot: dict[str, Any]
    pending_state_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
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
    execution_result: dict[str, Any] | None = None
    execution_error: dict[str, Any] | None = None


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
