"""R15e-a 候选研究报告引用和来源边界。"""

from uuid import uuid4

import pytest

from experiment_guardian.domain.agent_research import (
    AgentResearchReportPayload,
    research_report_source_hash,
    validate_report_against_source,
)


def _source() -> tuple[dict[str, object], list[str]]:
    experiment_ids = [str(uuid4()), str(uuid4())]
    content = {
        "schema_version": 1,
        "objective": "比较两个实验",
        "metric_name": None,
        "include_historical": False,
        "experiment_ids": experiment_ids,
        "experiments": [
            {"experiment_id": experiment_ids[0], "status": "COMPLETED", "evidence_id": "ev_1_1"},
            {"experiment_id": experiment_ids[1], "status": "FAILED", "evidence_id": "ev_1_2"},
        ],
        "comparisons": [{"comparability": "NOT_COMPARABLE", "evidence_id": "ev_1_3"}],
        "repeated_group": {"analysis": {"accepted": False}, "evidence_id": "ev_1_4"},
        "notice": "候选分析",
    }
    content["source_hash"] = research_report_source_hash(content)
    source = {
        "content": content,
        "evidence": [
            {
                "evidence_id": "ev_1_1",
                "evidence_kind": "CONFIRMED_FACT",
                "entity_type": "EXPERIMENT",
                "entity_id": experiment_ids[0],
                "label": "实验一",
                "excerpt": "COMPLETED",
                "payload": {},
            },
            {
                "evidence_id": "ev_1_2",
                "evidence_kind": "CONFIRMED_FACT",
                "entity_type": "EXPERIMENT",
                "entity_id": experiment_ids[1],
                "label": "实验二",
                "excerpt": "FAILED",
                "payload": {},
            },
            {
                "evidence_id": "ev_1_3",
                "evidence_kind": "ANALYSIS",
                "entity_type": "EXPERIMENT_COMPARISON",
                "entity_id": None,
                "label": "确定性比较",
                "excerpt": "NOT_COMPARABLE",
                "payload": {},
            },
        ],
    }
    return source, experiment_ids


def _report(experiment_ids: list[str], source_hash: str) -> AgentResearchReportPayload:
    return AgentResearchReportPayload.model_validate(
        {
            "schema_version": 1,
            "source_hash": source_hash,
            "title": "阶段研究报告",
            "executive_summary": "一个实验完成，一个实验失败，不能直接排名。",
            "executive_summary_citation_ids": ["ev_1_1", "ev_1_2", "ev_1_3"],
            "findings": [
                {
                    "finding_id": "F001",
                    "kind": "CONFLICT",
                    "statement": "两次运行状态不同。",
                    "rationale": "正式状态和确定性比较均显示条件不足。",
                    "citation_ids": ["ev_1_1", "ev_1_2", "ev_1_3"],
                    "limitations": ["不构成因果结论"],
                }
            ],
            "limitations": [],
            "selected_experiment_ids": experiment_ids,
        }
    )


def test_report_requires_exact_source_hash_selection_and_complete_citations() -> None:
    source, experiment_ids = _source()
    source_hash = str(source["content"]["source_hash"])
    validate_report_against_source(_report(experiment_ids, source_hash), source)

    wrong_hash = _report(experiment_ids, source_hash).model_copy(
        update={"source_hash": "b" * 64}
    )
    with pytest.raises(ValueError, match="source_hash"):
        validate_report_against_source(wrong_hash, source)

    wrong_order = _report(list(reversed(experiment_ids)), source_hash)
    with pytest.raises(ValueError, match="集合或顺序"):
        validate_report_against_source(wrong_order, source)


def test_conclusion_requires_two_facts_and_analysis_and_covers_selection() -> None:
    source, experiment_ids = _source()
    report = _report(experiment_ids, str(source["content"]["source_hash"]))
    invalid_finding = report.findings[0].model_copy(
        update={"citation_ids": ["ev_1_1", "ev_1_3"]}
    )
    invalid = report.model_copy(
        update={
            "findings": [invalid_finding],
            "executive_summary_citation_ids": ["ev_1_1", "ev_1_3"],
        }
    )
    with pytest.raises(ValueError, match="至少两个实验"):
        validate_report_against_source(invalid, source)

    unknown = report.model_copy(
        update={"executive_summary_citation_ids": ["ev_unknown"]}
    )
    with pytest.raises(ValueError, match="来源之外"):
        validate_report_against_source(unknown, source)


def test_source_hash_ignores_tool_evidence_ids_but_detects_fact_changes() -> None:
    content = {
        "schema_version": 1,
        "objective": "比较实验",
        "metric_name": "top1",
        "include_historical": False,
        "experiment_ids": ["experiment-1", "experiment-2"],
        "experiments": [
            {"experiment_id": "experiment-1", "status": "COMPLETED", "evidence_id": "a"},
            {"experiment_id": "experiment-2", "status": "FAILED", "evidence_id": "b"},
        ],
        "comparisons": [{"comparability": "NOT_COMPARABLE", "evidence_id": "c"}],
        "repeated_group": {"analysis": {"accepted": False}, "evidence_id": "d"},
        "notice": "候选分析",
    }
    expected = research_report_source_hash(content)
    changed_ids = {
        **content,
        "experiments": [
            {**item, "evidence_id": f"new-{index}"}
            for index, item in enumerate(content["experiments"])
        ],
        "comparisons": [{"comparability": "NOT_COMPARABLE", "evidence_id": "new-c"}],
    }
    assert research_report_source_hash(changed_ids) == expected
    changed_fact = {
        **content,
        "experiments": [
            content["experiments"][0],
            {**content["experiments"][1], "status": "COMPLETED"},
        ],
    }
    assert research_report_source_hash(changed_fact) != expected
