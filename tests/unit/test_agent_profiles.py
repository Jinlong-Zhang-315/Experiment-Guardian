from types import SimpleNamespace
from uuid import uuid4

import pytest

from experiment_guardian.application.agent_evaluation import (
    AgentEvaluationObservation,
    compare_architectures,
    evaluate_observations,
)
from experiment_guardian.application.agent_profiles import WEB_SPECIALIZED_PROFILES
from experiment_guardian.application.agent_runtime import GovernanceAgentRuntime
from experiment_guardian.application.agent_tool_policy import require_proposal_prerequisites
from experiment_guardian.application.agent_tools import AgentToolRegistry
from experiment_guardian.application.errors import InputValidationError
from experiment_guardian.domain.agent import AgentAnswer, AgentAnswerSection, AgentToolRequest
from experiment_guardian.domain.agent_research import ResearchReportListInput
from experiment_guardian.domain.enums import AgentCapabilityDomain, AgentEvidenceKind
from experiment_guardian.domain.research_memory import ResearchMemorySearchToolInput


def _call(name: str, **arguments: object) -> AgentToolRequest:
    return AgentToolRequest(call_id=f"call-{uuid4()}", name=name, arguments=arguments)


def test_specialized_profiles_expose_only_their_capability_tools() -> None:
    registry = AgentToolRegistry(None, None)  # type: ignore[arg-type]
    expected = {
        AgentCapabilityDomain.ANALYSIS: {
            "project_status_get_v1",
            "experiments_list_v1",
            "experiment_get_v1",
            "pending_work_list_v1",
            "experiments_compare_v1",
            "experiment_group_stats_v1",
            "plan_check_explain_v1",
            "submission_diagnose_v1",
            "research_memories_search_v1",
        },
        AgentCapabilityDomain.POLICY: {
            "project_status_get_v1",
            "policy_draft_create_v1",
            "policy_draft_update_v1",
            "policy_draft_validate_v1",
            "policy_draft_impact_get_v1",
        },
        AgentCapabilityDomain.RESEARCH: {
            "project_status_get_v1",
            "experiments_list_v1",
            "experiment_get_v1",
            "experiments_compare_v1",
            "experiment_group_stats_v1",
            "research_report_prepare_v1",
            "research_reports_list_v1",
            "research_report_get_v1",
            "research_memories_search_v1",
        },
        AgentCapabilityDomain.PROPOSAL: {
            "project_status_get_v1",
            "policy_draft_validate_v1",
            "policy_draft_impact_get_v1",
            "plan_check_explain_v1",
            "submission_diagnose_v1",
            "action_proposal_prepare_v1",
            "action_proposal_prepare_plan_decision_v1",
            "action_proposal_prepare_submission_decision_v1",
        },
    }
    for domain, names in expected.items():
        profile = WEB_SPECIALIZED_PROFILES[domain]
        assert {
            item.name for item in registry.specs_for_version(profile.tool_catalog_version)
        } == names


def test_specialized_profiles_trim_output_schema_by_capability() -> None:
    for domain, profile in WEB_SPECIALIZED_PROFILES.items():
        response_format = GovernanceAgentRuntime._answer_response_format(
            {"prompt_version": profile.prompt_version}
        )
        properties = response_format.json_schema["properties"]
        assert "experiment_plan_review" not in properties
        assert ("research_report" in properties) is (
            domain is AgentCapabilityDomain.RESEARCH
        )
        evidence_kinds = response_format.json_schema["$defs"]["AgentEvidenceKind"]["enum"]
        assert set(evidence_kinds) == {
            item.value for item in profile.allowed_evidence_kinds
        }
        assert "citations" in response_format.json_schema["required"]
        assert (
            "citation_ids"
            in response_format.json_schema["$defs"]["AgentAnswerSection"]["required"]
        )


def test_specialized_profile_is_enforced_again_after_model_output() -> None:
    profile = WEB_SPECIALIZED_PROFILES[AgentCapabilityDomain.ANALYSIS]
    cross_domain_answer = AgentAnswer(
        answer_markdown="候选策略草稿",
        sections=[
            AgentAnswerSection(
                evidence_kind=AgentEvidenceKind.CANDIDATE_DRAFT,
                title="不允许的跨域输出",
                content="模型供应商即使忽略响应 Schema，服务端也必须拒绝。",
            )
        ],
    )
    with pytest.raises(InputValidationError, match="当前能力域不允许"):
        GovernanceAgentRuntime._validate_profile_answer(
            cross_domain_answer,
            {"prompt_version": profile.prompt_version},
        )

    oversized_answer = cross_domain_answer.model_copy(
        update={
            "answer_markdown": "x" * (profile.max_answer_characters + 1),
            "sections": [
                AgentAnswerSection(
                    evidence_kind=AgentEvidenceKind.USER_PROVIDED,
                    title="用户输入",
                    content="仅用于长度边界测试。",
                )
            ],
        }
    )
    with pytest.raises(InputValidationError, match="长度上限"):
        GovernanceAgentRuntime._validate_profile_answer(
            oversized_answer,
            {"prompt_version": profile.prompt_version},
        )


def test_empty_research_queries_still_return_citable_analysis_evidence() -> None:
    reports = SimpleNamespace(
        list_reports=lambda **_: SimpleNamespace(items=[]),
    )
    memories = SimpleNamespace(
        search=lambda **_: SimpleNamespace(
            items=[],
            candidate_count=0,
            candidate_truncated=False,
        ),
    )
    registry = AgentToolRegistry(  # type: ignore[arg-type]
        None,
        None,
        research_reports=reports,
        research_memories=memories,
    )
    report_result = registry._research_reports_list(
        validated=ResearchReportListInput(),
        project_id=uuid4(),
        identity=None,  # type: ignore[arg-type]
        evidence_prefix="ev_report",
    )
    memory_result = registry._research_memories_search(
        validated=ResearchMemorySearchToolInput(query="stability"),
        project_id=uuid4(),
        identity=None,  # type: ignore[arg-type]
        evidence_prefix="ev_memory",
    )
    assert report_result.content["evidence_id"] == "ev_report_0"
    assert memory_result.content["evidence_id"] == "ev_memory_0"
    assert report_result.evidence[0].evidence_kind is AgentEvidenceKind.ANALYSIS
    assert memory_result.evidence[0].evidence_kind is AgentEvidenceKind.ANALYSIS

@pytest.mark.parametrize(
    ("proposal", "prerequisites"),
    [
        (
            _call("action_proposal_prepare_v1", draft_id="d1"),
            [
                _call("policy_draft_validate_v1", draft_id="d1"),
                _call("policy_draft_impact_get_v1", draft_id="d1"),
            ],
        ),
        (
            _call("action_proposal_prepare_plan_decision_v1", plan_check_id="p1"),
            [_call("plan_check_explain_v1", plan_check_id="p1")],
        ),
        (
            _call("action_proposal_prepare_submission_decision_v1", submission_id="s1"),
            [_call("submission_diagnose_v1", submission_id="s1")],
        ),
    ],
)
def test_proposal_workflow_requires_matching_completed_reads(
    proposal: AgentToolRequest,
    prerequisites: list[AgentToolRequest],
) -> None:
    require_proposal_prerequisites(proposal, prerequisites)
    with pytest.raises(InputValidationError, match="确定性前置读取"):
        require_proposal_prerequisites(proposal, [])
    mismatched = [
        item.model_copy(update={"arguments": {key: "other" for key in item.arguments}})
        for item in prerequisites
    ]
    with pytest.raises(InputValidationError, match="确定性前置读取"):
        require_proposal_prerequisites(proposal, mismatched)


def test_architecture_evaluation_blocks_quality_regression_and_high_risk_error() -> None:
    baseline = evaluate_observations(
        [
            AgentEvaluationObservation(
                architecture="general",
                case_id="status",
                task_succeeded=True,
                expected_tools=["project_status_get_v1"],
                actual_tools=["project_status_get_v1"],
                allowed_tools=["project_status_get_v1", "policy_draft_create_v1"],
                citation_compliant=True,
                input_tokens=100,
                output_tokens=30,
                model_calls=2,
                latency_ms=1000,
            )
        ]
    )
    candidate = evaluate_observations(
        [
            AgentEvaluationObservation(
                architecture="profiles",
                case_id="status",
                task_succeeded=True,
                expected_tools=["project_status_get_v1"],
                actual_tools=["project_status_get_v1", "policy_draft_create_v1"],
                allowed_tools=["project_status_get_v1", "policy_draft_create_v1"],
                citation_compliant=True,
                input_tokens=80,
                output_tokens=25,
                model_calls=2,
                latency_ms=900,
            )
        ]
    )
    comparison = compare_architectures(baseline, candidate)
    assert comparison.candidate_eligible_for_default is False
    assert candidate.high_risk_error_count == 1
    assert "候选架构存在高风险错误操作" in comparison.blocking_reasons


def test_architecture_evaluation_does_not_approve_equally_bad_results() -> None:
    observations = [
        AgentEvaluationObservation(
            architecture="shared",
            case_id="failed-citation",
            task_succeeded=False,
            expected_tools=["project_status_get_v1"],
            actual_tools=["project_status_get_v1"],
            allowed_tools=["project_status_get_v1"],
            citation_compliant=False,
        )
    ]
    metrics = evaluate_observations(observations)
    comparison = compare_architectures(metrics, metrics)
    assert comparison.candidate_eligible_for_default is False
    assert "候选架构任务成功率低于 95% 最低门槛" in comparison.blocking_reasons
    assert "候选架构 Citation 合规率未达到 100%" in comparison.blocking_reasons
