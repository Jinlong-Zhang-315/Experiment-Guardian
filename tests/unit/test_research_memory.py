from uuid import uuid4

import pytest

from experiment_guardian.domain.enums import ResearchMemoryType
from experiment_guardian.domain.research_memory import (
    RESEARCH_MEMORY_DOCUMENT_VERSION,
    ResearchMemorySearchRequest,
    build_research_memory_document,
    research_memory_type,
    text_hash,
)


def test_finding_types_and_document_are_deterministic() -> None:
    assert research_memory_type("SUPPORTED_CONCLUSION") is ResearchMemoryType.RESEARCH_SYNTHESIS
    assert research_memory_type("CONFLICT") is ResearchMemoryType.CONFLICT
    assert RESEARCH_MEMORY_DOCUMENT_VERSION == "agent-research-memory-v1"
    values = {
        "title": "阶段报告",
        "objective": "比较实验",
        "memory_type": ResearchMemoryType.OPEN_QUESTION,
        "statement": "结果仍不确定",
        "rationale": "重复次数不足",
        "limitations": ["仅两个 seed"],
        "experiment_ids": [str(uuid4()), str(uuid4())],
    }
    first = build_research_memory_document(**values)
    assert build_research_memory_document(**values) == first
    assert "非正式事实" in first
    assert len(text_hash(first)) == 64


def test_search_request_rejects_duplicate_filters() -> None:
    experiment_id = uuid4()
    with pytest.raises(ValueError, match="experiment_ids 不能重复"):
        ResearchMemorySearchRequest(
            query="查询",
            experiment_ids=[experiment_id, experiment_id],
        )

