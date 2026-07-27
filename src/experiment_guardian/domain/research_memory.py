"""独立候选 Research Memory 的稳定契约与确定性文档构造。"""

import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from experiment_guardian.domain.contracts import ContractModel
from experiment_guardian.domain.enums import (
    ResearchMemoryEmbeddingStatus,
    ResearchMemoryStatus,
    ResearchMemoryType,
)

RESEARCH_MEMORY_DOCUMENT_VERSION = "agent-research-memory-v1"
MAX_RESEARCH_MEMORY_CANDIDATES = 200


class ResearchMemorySearchRequest(ContractModel):
    query: str = Field(min_length=1, max_length=1000)
    memory_types: list[ResearchMemoryType] = Field(default_factory=list, max_length=4)
    protocol: str | None = Field(default=None, min_length=1, max_length=200)
    experiment_ids: list[UUID] = Field(default_factory=list, max_length=8)
    include_stale: bool = False
    top_k: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def normalize(self) -> "ResearchMemorySearchRequest":
        self.query = self.query.strip()
        if not self.query:
            raise ValueError("研究记忆查询不能为空")
        if self.protocol is not None:
            self.protocol = self.protocol.strip()
        if len(set(self.memory_types)) != len(self.memory_types):
            raise ValueError("memory_types 不能重复")
        if len(set(self.experiment_ids)) != len(self.experiment_ids):
            raise ValueError("experiment_ids 不能重复")
        return self


class ResearchMemorySearchToolInput(ResearchMemorySearchRequest):
    pass


class ResearchMemoryIndexView(ContractModel):
    memory_id: UUID
    finding_id: str
    memory_type: ResearchMemoryType
    status: ResearchMemoryStatus
    source_freshness: Literal["CURRENT", "SOURCE_CHANGED", "SOURCE_MISSING"]
    embedding_status: ResearchMemoryEmbeddingStatus | Literal["NOT_SCHEDULED"]
    provider: str | None = None
    model_id: str | None = None
    document_version: str
    last_error: dict[str, object] | None = None


class ResearchMemorySearchResult(ContractModel):
    memory_id: UUID
    report_id: UUID
    finding_id: str
    memory_type: ResearchMemoryType
    statement: str
    rationale: str
    limitations: list[str]
    citation_ids: list[str]
    experiment_ids: list[UUID]
    protocols: list[str]
    source_references: list[dict[str, object]]
    source_freshness: Literal["CURRENT", "SOURCE_CHANGED", "SOURCE_MISSING"]
    source_warnings: list[str]
    similarity: float
    provider: str
    model_id: str
    document_version: str
    content_hash: str
    authoritative: Literal[False] = False
    evidence_classification: Literal["ANALYSIS"] = "ANALYSIS"
    retrieval_role: Literal["CANDIDATE_EVIDENCE"] = "CANDIDATE_EVIDENCE"


class ResearchMemorySearchResponse(ContractModel):
    items: list[ResearchMemorySearchResult]
    candidate_count: int
    candidate_truncated: bool
    authoritative: Literal[False] = False
    retrieval_role: Literal["CANDIDATE_EVIDENCE"] = "CANDIDATE_EVIDENCE"


class ResearchMemoryRetryResult(ContractModel):
    memory_id: UUID
    embedding_status: ResearchMemoryEmbeddingStatus
    provider: str
    model_id: str


class ResearchMemoryReference(ContractModel):
    memory_id: UUID
    report_id: UUID
    finding_id: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def research_memory_type(kind: str) -> ResearchMemoryType:
    mapping = {
        "SUPPORTED_CONCLUSION": ResearchMemoryType.RESEARCH_SYNTHESIS,
        "CONFLICT": ResearchMemoryType.CONFLICT,
        "OPEN_QUESTION": ResearchMemoryType.OPEN_QUESTION,
        "RECOMMENDATION": ResearchMemoryType.RECOMMENDATION,
    }
    try:
        return mapping[kind]
    except KeyError as exc:
        raise ValueError(f"不支持的研究 finding 类型: {kind}") from exc


def build_research_memory_document(
    *,
    title: str,
    objective: str,
    memory_type: ResearchMemoryType,
    statement: str,
    rationale: str,
    limitations: list[str],
    experiment_ids: list[str],
) -> str:
    """只重排已验证字段，不推断、补充或弱化报告内容。"""

    limitation_text = "；".join(limitations) if limitations else "无额外限制"
    return "\n".join(
        [
            "候选研究记忆（非正式事实）",
            f"类型：{memory_type.value}",
            f"来源报告：{title}",
            f"研究目标：{objective}",
            f"陈述：{statement}",
            f"依据：{rationale}",
            f"限制：{limitation_text}",
            "来源实验：" + "、".join(experiment_ids),
        ]
    )


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
