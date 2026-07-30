"""内部实验治理 Agent 的稳定领域契约。

这些类型只描述对话、模型工具调用和可追溯回答，不赋予模型任何业务权限。正式事实仍由
现有 Context、Intent、Constraint、Plan、Submission 和 Experiment 模型定义。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from experiment_guardian.domain.agent_research import (
    AgentResearchReportPayload,
    ResearchReportReference,
)
from experiment_guardian.domain.contracts import ContractModel, ProjectContextBundle
from experiment_guardian.domain.enums import (
    AgentCallStatus,
    AgentCapabilityDomain,
    AgentContextSummaryStatus,
    AgentEvidenceKind,
    AgentMessageRole,
    AgentModelCallPurpose,
    AgentRunKind,
    AgentRunStatus,
    AgentThreadOrigin,
    AgentThreadStatus,
)
from experiment_guardian.domain.experiment_plan import ExperimentPlanReviewPayload
from experiment_guardian.domain.research_memory import ResearchMemoryReference

MAX_AGENT_MESSAGE_BYTES = 8 * 1024


class AgentThreadCreateRequest(ContractModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    capability_domain: AgentCapabilityDomain = AgentCapabilityDomain.GENERAL


class AgentThreadUpdateRequest(ContractModel):
    archived: bool


class AgentMessageCreateRequest(ContractModel):
    content: str = Field(min_length=1, max_length=MAX_AGENT_MESSAGE_BYTES)

    @model_validator(mode="after")
    def validate_utf8_size(self) -> "AgentMessageCreateRequest":
        if not self.content.strip():
            raise ValueError("Agent 消息不能为空")
        if len(self.content.encode("utf-8")) > MAX_AGENT_MESSAGE_BYTES:
            raise ValueError("Agent 消息不能超过 8 KiB")
        self.content = self.content.strip()
        return self


class AgentThreadSummary(ContractModel):
    thread_id: UUID
    project_id: UUID
    title: str
    origin: AgentThreadOrigin = AgentThreadOrigin.WEB
    capability_domain: AgentCapabilityDomain = AgentCapabilityDomain.GENERAL
    status: AgentThreadStatus
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class AgentThreadPage(ContractModel):
    items: list[AgentThreadSummary]
    next_cursor: str | None = None


class AgentCitationView(ContractModel):
    evidence_id: str
    evidence_kind: AgentEvidenceKind
    entity_type: str
    entity_id: UUID | None = None
    entity_version: str | None = None
    label: str
    excerpt: str


class AgentMessageView(ContractModel):
    message_id: UUID
    sequence: int
    role: AgentMessageRole
    content: str
    run_id: UUID | None = None
    research_report_id: UUID | None = None
    sections: list["AgentAnswerSection"] = Field(default_factory=list)
    citations: list[AgentCitationView] = Field(default_factory=list)
    created_at: datetime


class AgentContextSummaryView(ContractModel):
    summary_id: UUID | None = None
    status: AgentContextSummaryStatus | None = None
    covered_sequence_from: int | None = None
    covered_sequence_to: int | None = None
    provider: str | None = None
    model_id: str | None = None
    generated_at: datetime | None = None
    degraded: bool = False
    warning: str | None = None
    authoritative: bool = False


class AgentThreadView(ContractModel):
    thread: AgentThreadSummary
    messages: list[AgentMessageView]
    context_summary: AgentContextSummaryView | None = None
    external_task_context: "ExternalAgentTaskContextView | None" = None


class AgentRunReceipt(ContractModel):
    run_id: UUID
    thread_id: UUID
    trigger_message_id: UUID
    status: AgentRunStatus
    events_url: str


class AgentRunView(AgentRunReceipt):
    attempt_count: int
    max_attempts: int
    provider: str
    model_id: str
    run_kind: AgentRunKind
    capability_domain: str = Field(min_length=1, max_length=50)
    prompt_version: str
    tool_catalog_version: str
    usage: dict[str, Any] = Field(default_factory=dict)
    model_calls: list["AgentModelCallView"] = Field(default_factory=list, max_length=50)
    error: dict[str, Any] | None = None
    final_message_id: UUID | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ExternalAgentTaskStartRequest(ContractModel):
    task_description: str = Field(min_length=1, max_length=MAX_AGENT_MESSAGE_BYTES)
    title: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_utf8_size(self) -> "ExternalAgentTaskStartRequest":
        self.task_description = self.task_description.strip()
        if not self.task_description:
            raise ValueError("外部 Agent 任务说明不能为空")
        if len(self.task_description.encode("utf-8")) > MAX_AGENT_MESSAGE_BYTES:
            raise ValueError("外部 Agent 任务说明不能超过 8 KiB")
        if self.title is not None:
            self.title = self.title.strip()
        return self


class ExternalAgentQuestionRequest(ContractModel):
    question: str = Field(min_length=1, max_length=MAX_AGENT_MESSAGE_BYTES)

    @model_validator(mode="after")
    def validate_utf8_size(self) -> "ExternalAgentQuestionRequest":
        self.question = self.question.strip()
        if not self.question:
            raise ValueError("外部 Agent 问题不能为空")
        if len(self.question.encode("utf-8")) > MAX_AGENT_MESSAGE_BYTES:
            raise ValueError("外部 Agent 问题不能超过 8 KiB")
        return self


class ExternalAgentTaskContextView(ContractModel):
    captured_at: datetime
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    authoritative_scope: Literal["FORMAL_POLICY_ONLY"] = "FORMAL_POLICY_ONLY"
    policy: ProjectContextBundle
    governance_notice: str
    context_freshness: Literal["CURRENT", "STALE"] | None = None
    current_context_id: UUID | None = None
    current_context_version: int | None = Field(default=None, gt=0)
    current_intent_id: UUID | None = None
    current_intent_version: int | None = Field(default=None, gt=0)
    warning: str | None = None


class ExternalAgentTaskStartResult(ContractModel):
    schema_version: Literal[1] = 1
    task_id: UUID
    thread_id: UUID
    task_status: AgentThreadStatus
    origin: Literal[AgentThreadOrigin.EXTERNAL_MCP] = AgentThreadOrigin.EXTERNAL_MCP
    initial_context: ExternalAgentTaskContextView
    context_freshness: Literal["CURRENT"] = "CURRENT"
    run: AgentRunReceipt
    poll_after_seconds: float = Field(gt=0, le=30)


class ExternalAgentTaskPollResult(ContractModel):
    schema_version: Literal[1] = 1
    task: AgentThreadSummary
    initial_context: ExternalAgentTaskContextView
    context_freshness: Literal["CURRENT", "STALE"]
    current_context_id: UUID
    current_context_version: int = Field(gt=0)
    current_intent_id: UUID | None = None
    current_intent_version: int | None = Field(default=None, gt=0)
    warning: str | None = None
    messages: list[AgentMessageView]
    latest_run: AgentRunView | None = None
    next_sequence: int = Field(ge=0)


class AgentModelCallView(ContractModel):
    call_id: UUID
    generation: int = Field(ge=0)
    ordinal: int = Field(ge=0)
    purpose: AgentModelCallPurpose
    status: AgentCallStatus
    provider: str
    model_id: str
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    cost_currency: str | None = None
    finish_reason: str | None = None
    error_code: str | None = None
    started_at: datetime
    completed_at: datetime | None = None


class AgentObservabilityTotals(ContractModel):
    run_count: int = Field(ge=0)
    model_call_count: int = Field(ge=0)
    succeeded_call_count: int = Field(ge=0)
    failed_call_count: int = Field(ge=0)
    abandoned_call_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    missing_usage_call_count: int = Field(ge=0)
    unpriced_call_count: int = Field(ge=0)
    average_latency_ms: int | None = Field(default=None, ge=0)
    maximum_latency_ms: int | None = Field(default=None, ge=0)


class AgentObservabilityGroup(AgentObservabilityTotals):
    provider: str
    model_id: str
    purpose: AgentModelCallPurpose


class AgentCostTotal(ContractModel):
    currency: str
    estimated_cost: Decimal = Field(ge=0)


class AgentModelObservabilityView(ContractModel):
    project_id: UUID
    window_from: datetime
    window_to: datetime
    current_provider: str
    current_model_id: str
    pricing_configured: bool
    totals: AgentObservabilityTotals
    groups: list[AgentObservabilityGroup]
    costs: list[AgentCostTotal]
    failure_categories: dict[str, int]


class AgentChatMessage(ContractModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class AgentToolSpec(ContractModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    version: str = Field(min_length=1, max_length=20)
    description: str = Field(min_length=1, max_length=1000)
    input_schema: dict[str, Any]


class AgentResponseFormat(ContractModel):
    """Provider 无关的严格 JSON 输出契约。"""

    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    description: str = Field(min_length=1, max_length=500)
    json_schema: dict[str, Any]


class AgentToolRequest(ContractModel):
    call_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any]


class AgentModelUsage(ContractModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class AgentModelEvent(ContractModel):
    event_type: Literal["text_delta", "tool_call", "usage", "completed"]
    text: str | None = None
    tool_call: AgentToolRequest | None = None
    usage: AgentModelUsage | None = None
    finish_reason: str | None = None
    provider_request_id: str | None = None


class AgentAnswerSection(ContractModel):
    evidence_kind: AgentEvidenceKind
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=6000)
    citation_ids: list[str] = Field(default_factory=list, max_length=50)


class AgentAnswer(ContractModel):
    answer_markdown: str = Field(min_length=1, max_length=12000)
    sections: list[AgentAnswerSection] = Field(min_length=1, max_length=20)
    citations: list[str] = Field(default_factory=list, max_length=100)
    follow_up_required: bool = False
    research_report: AgentResearchReportPayload | None = None
    experiment_plan_review: ExperimentPlanReviewPayload | None = None


class AgentDraftSummaryReference(ContractModel):
    draft_id: UUID
    revision: int = Field(ge=1)
    status: str = Field(min_length=1, max_length=32)
    unresolved_ambiguities: list[str] = Field(default_factory=list, max_length=20)


class AgentProposalSummaryReference(ContractModel):
    proposal_id: UUID
    operation: str = Field(min_length=1, max_length=32)
    status: str = Field(min_length=1, max_length=32)
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_draft_id: UUID | None = None
    source_draft_revision: int | None = Field(default=None, ge=1)
    target_plan_check_id: UUID | None = None
    target_submission_id: UUID | None = None
    decision: str | None = Field(default=None, max_length=16)
    review_eligibility: str | None = Field(default=None, max_length=32)
    expires_at: datetime

    @model_validator(mode="after")
    def validate_operation_reference(self) -> "AgentProposalSummaryReference":
        if self.operation == "POLICY_PUBLISH":
            if self.source_draft_id is None or self.source_draft_revision is None:
                raise ValueError("Policy 提案摘要缺少草稿引用")
        elif self.operation == "PLAN_CHECK_DECISION" and (
            self.target_plan_check_id is None
            or self.decision
            not in {
                "APPROVED",
                "REJECTED",
            }
        ):
            raise ValueError("Plan 提案摘要缺少目标或决定")
        elif self.operation == "SUBMISSION_DECISION" and (
            self.target_submission_id is None
            or self.decision not in {"APPROVED", "REJECTED"}
            or self.review_eligibility not in {"RESEARCHER_OR_OWNER", "OWNER_ONLY", "BLOCKED"}
        ):
            raise ValueError("Submission 提案摘要缺少目标、决定或审核资格")
        return self


class AgentContextSummaryPayload(ContractModel):
    schema_version: Literal[1, 2, 3, 4, 5, 6, 7] = 1
    covered_sequence_from: int = Field(ge=1)
    covered_sequence_to: int = Field(ge=1)
    user_requests_and_context: list[str] = Field(default_factory=list, max_length=30)
    prior_answers_and_analysis: list[str] = Field(default_factory=list, max_length=30)
    open_questions: list[str] = Field(default_factory=list, max_length=20)
    source_message_ids: list[UUID] = Field(min_length=1, max_length=200)
    formal_reference_labels: list[str] = Field(default_factory=list, max_length=50)
    draft_references: list[AgentDraftSummaryReference] = Field(
        default_factory=list,
        max_length=20,
    )
    proposal_references: list[AgentProposalSummaryReference] = Field(
        default_factory=list,
        max_length=20,
    )
    research_report_references: list[ResearchReportReference] = Field(
        default_factory=list,
        max_length=20,
    )
    research_memory_references: list[ResearchMemoryReference] = Field(
        default_factory=list,
        max_length=20,
    )

    @model_validator(mode="after")
    def validate_sequence_range(self) -> "AgentContextSummaryPayload":
        if self.covered_sequence_to < self.covered_sequence_from:
            raise ValueError("上下文摘要的消息序号范围无效")
        if len(set(self.source_message_ids)) != len(self.source_message_ids):
            raise ValueError("上下文摘要的 source_message_ids 不能重复")
        if self.schema_version == 1 and self.draft_references:
            raise ValueError("schema_version=1 的上下文摘要不能包含 draft_references")
        if self.schema_version in {1, 2} and self.proposal_references:
            raise ValueError("schema_version<3 的上下文摘要不能包含 proposal_references")
        if self.schema_version < 6 and self.research_report_references:
            raise ValueError("schema_version<6 的上下文摘要不能包含研究报告引用")
        if self.schema_version < 7 and self.research_memory_references:
            raise ValueError("schema_version<7 的上下文摘要不能包含研究记忆引用")
        return self


class AgentEvidence(ContractModel):
    evidence_id: str
    evidence_kind: AgentEvidenceKind
    entity_type: str
    entity_id: UUID | None = None
    entity_version: str | None = None
    label: str
    excerpt: str
    payload: dict[str, Any]


class AgentToolResult(ContractModel):
    content: dict[str, Any]
    evidence: list[AgentEvidence] = Field(default_factory=list)
