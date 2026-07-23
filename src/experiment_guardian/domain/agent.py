"""内部实验治理 Agent 的稳定领域契约。

这些类型只描述对话、模型工具调用和可追溯回答，不赋予模型任何业务权限。正式事实仍由
现有 Context、Intent、Constraint、Plan、Submission 和 Experiment 模型定义。
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from experiment_guardian.domain.contracts import ContractModel
from experiment_guardian.domain.enums import (
    AgentEvidenceKind,
    AgentMessageRole,
    AgentRunStatus,
    AgentThreadStatus,
)

MAX_AGENT_MESSAGE_BYTES = 8 * 1024


class AgentThreadCreateRequest(ContractModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)


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
    citations: list[AgentCitationView] = Field(default_factory=list)
    created_at: datetime


class AgentThreadView(ContractModel):
    thread: AgentThreadSummary
    messages: list[AgentMessageView]


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
    error: dict[str, Any] | None = None
    final_message_id: UUID | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


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
