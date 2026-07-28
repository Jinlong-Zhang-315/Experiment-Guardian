"""R17b 实验计划契约与确定性硬检查。"""

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from experiment_guardian.application.agent_runtime import GovernanceAgentRuntime
from experiment_guardian.application.errors import InputValidationError
from experiment_guardian.domain.agent import AgentAnswer
from experiment_guardian.domain.contracts import ConfigurationDocument, ParameterConstraint
from experiment_guardian.domain.enums import (
    ConstraintSource,
    ProtectionLevel,
    VerificationStatus,
)
from experiment_guardian.domain.experiment_plan import (
    ExperimentPlanEvidence,
    ExperimentPlanReviewPayload,
    evaluate_plan_evidence,
)


def _constraint(path: str, expected_value: object) -> ParameterConstraint:
    return ParameterConstraint(
        parameter_path=path,
        protection_level=ProtectionLevel.LOCKED,
        expected_value=expected_value,
        source_type=ConstraintSource.EXPLICIT,
        verification_status=VerificationStatus.CONFIRMED,
        original_message=f"固定 {path}",
        confirmed_by=uuid4(),
        confirmed_at=datetime.now(UTC),
    )


def test_plan_evidence_rejects_mismatched_configuration_hash() -> None:
    content = "model:\n  fusion: 0.3\n"
    with pytest.raises(ValidationError, match="config_sha256 与配置原始字节不一致"):
        ExperimentPlanEvidence(
            configuration=ConfigurationDocument(format="yaml", content=content),
            config_sha256="0" * 64,
        )

    evidence = ExperimentPlanEvidence(
        configuration=ConfigurationDocument(format="yaml", content=content),
        config_sha256=hashlib.sha256(content.encode()).hexdigest(),
    )
    assert evidence.config_sha256 == hashlib.sha256(content.encode()).hexdigest()


def test_plan_hard_check_uses_type_strict_locked_comparison() -> None:
    bundle = SimpleNamespace(constraints=[_constraint("model.enabled", 1)])
    result = evaluate_plan_evidence(
        ExperimentPlanEvidence(
            configuration=ConfigurationDocument(
                format="json",
                content='{"model":{"enabled":true}}',
            )
        ),
        bundle,  # type: ignore[arg-type]
    )

    assert result.status == "BLOCKED"
    assert result.issues[0].code == "LOCKED_PARAMETER_CONFLICT"
    assert result.issues[0].blocking is True


def test_plan_yaml_core_schema_keeps_ambiguous_scalars_as_strings() -> None:
    bundle = SimpleNamespace(constraints=[])
    result = evaluate_plan_evidence(
        ExperimentPlanEvidence(
            configuration=ConfigurationDocument(
                format="yaml",
                content="flag: yes\nmode: on\nrelease_date: 2026-07-28\n",
            )
        ),
        bundle,  # type: ignore[arg-type]
    )

    assert result.status == "PASS"
    assert result.parsed_configuration == {
        "flag": "yes",
        "mode": "on",
        "release_date": "2026-07-28",
    }


def test_auto_revision_requires_an_explicit_auto_fixable_finding() -> None:
    with pytest.raises(ValidationError, match="至少一个可自动修正的问题"):
        ExperimentPlanReviewPayload(
            recommendation="REVISE",
            review_markdown="需要调整。",
            revised_plan_markdown="完整修订计划。",
            findings=[],
        )


def test_plan_review_cannot_be_ready_without_formal_policy_evidence() -> None:
    answer = AgentAnswer(
        answer_markdown="审核完成。",
        sections=[
            {
                "evidence_kind": "ANALYSIS",
                "title": "历史比较",
                "content": "计划看起来可行。",
                "citation_ids": ["ev_analysis"],
            }
        ],
        citations=["ev_analysis"],
        experiment_plan_review=ExperimentPlanReviewPayload(
            recommendation="READY",
            review_markdown="计划可进入人工审批。",
            findings=[
                {
                    "kind": "HISTORICAL_DUPLICATION",
                    "severity": "LOW",
                    "statement": "未发现重复。",
                    "rationale": "来自历史比较。",
                    "citation_ids": ["ev_analysis"],
                }
            ],
            citations=["ev_analysis"],
        ),
    )

    with pytest.raises(InputValidationError, match="必须读取并引用当前正式策略"):
        GovernanceAgentRuntime._validate_experiment_plan_review_answer(
            answer,
            {
                "ev_analysis": {
                    "evidence_kind": "ANALYSIS",
                    "entity_type": "EXPERIMENT_COMPARISON",
                }
            },
            {"experiment_plan_input": {"hard_check": {"status": "PASS"}}},
        )
