"""候选意图、正式上下文、查询和人工确认契约测试。"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from experiment_guardian.domain.contracts import (
    ExperimentIntentReference,
    ExperimentQueryCommand,
    ExperimentQueryResult,
    GeneratedSummary,
    IntentInterpretation,
    ParameterConstraint,
    ProjectContextBundle,
    ProjectContextReference,
    ReviewFact,
    ReviewTrace,
    RiskItem,
    SubmissionReceipt,
)
from experiment_guardian.domain.enums import (
    ConstraintSource,
    EvidenceType,
    ExperimentMode,
    ExperimentStatus,
    IntentStatus,
    ProtectionLevel,
    ReviewEligibility,
    RiskSeverity,
    VerificationStatus,
)

USER_ID = UUID("00000000-0000-0000-0000-000000000001")
PROJECT_ID = UUID("00000000-0000-0000-0000-000000000002")
CONTEXT_ID = UUID("00000000-0000-0000-0000-000000000003")
INTENT_ID = UUID("00000000-0000-0000-0000-000000000004")
NOW = datetime(2026, 7, 21, tzinfo=UTC)


def pending_constraint(source: ConstraintSource) -> ParameterConstraint:
    values: dict[str, object] = {
        "parameter_path": "model.backbone",
        "protection_level": ProtectionLevel.APPROVAL_REQUIRED,
        "expected_value": "shift-gcn",
        "source_type": source,
        "verification_status": VerificationStatus.PENDING,
        "original_message": "只修改局部模块，保持主干行为不变",
    }
    if source is ConstraintSource.INFERRED:
        values.update(inference_basis="主干行为映射为 backbone", confidence=0.61)
    return ParameterConstraint.model_validate(values)


def test_intent_interpretation_keeps_llm_output_pending() -> None:
    result = IntentInterpretation(
        original_message="只修改局部模块，保持主干行为不变",
        explicit_constraints=[pending_constraint(ConstraintSource.EXPLICIT)],
        inferred_constraints=[pending_constraint(ConstraintSource.INFERRED)],
        unresolved_ambiguities=["局部模块具体对应哪个配置路径？"],
        intent_receipt="允许局部修改；backbone 是否保持不变仍需确认。",
    )

    assert result.inferred_constraints[0].confidence == 0.61
    assert result.unresolved_ambiguities


def test_context_bundle_rejects_unconfirmed_constraint() -> None:
    with pytest.raises(ValidationError, match="CONFIRMED"):
        ProjectContextBundle(
            context=ProjectContextReference(
                context_id=CONTEXT_ID,
                version=2,
                confirmed_by=USER_ID,
                confirmed_at=NOW,
                effective_at=NOW,
                change_reason="修正 baseline checkpoint",
            ),
            active_intent=ExperimentIntentReference(
                intent_id=INTENT_ID,
                version=3,
                context_id=CONTEXT_ID,
                context_version=2,
                status=IntentStatus.ACTIVE,
                mode=ExperimentMode.FORMAL,
            ),
            constraints=[pending_constraint(ConstraintSource.INFERRED)],
            context_payload={
                "project_id": PROJECT_ID,
                "project_name": "demo",
                "description": "",
                "goal": "验证融合策略",
                "non_goals": [],
                "mainline_model": "shift-gcn",
                "baseline": {},
                "dataset": "NTU60",
                "protocol": "40/20",
                "primary_metric": {"name": "top1"},
                "default_seeds": [1],
                "active_branch": "main",
                "active_config": {"model": {"backbone": "shift-gcn"}},
                "deprecated_items": [],
                "key_decisions": [],
            },
            intent_payload={
                "name": "fusion",
                "objective": "验证融合系数",
                "hypothesis": "融合可以提升准确率",
                "allowed_variables": ["model.fusion"],
                "controlled_variables": ["model.backbone"],
                "expected_outputs": ["top1"],
                "acceptance_criteria": ["结果可追溯"],
                "original_message": "只修改融合系数",
                "intent_receipt": "已确认",
            },
        )


def test_critical_receipt_cannot_be_confirmed() -> None:
    with pytest.raises(ValidationError, match="CRITICAL"):
        SubmissionReceipt(
            submission_id=INTENT_ID,
            objective="测试融合系数",
            objective_evidence=ReviewFact(
                name="objective",
                value="测试融合系数",
                evidence_type=EvidenceType.USER_PROVIDED,
                source="intent",
                collected_at=NOW,
                collection_tool="test",
            ),
            trace=ReviewTrace(
                project_id=PROJECT_ID,
                context_id=CONTEXT_ID,
                context_version=1,
                intent_id=INTENT_ID,
                intent_version=1,
                plan_check_id=USER_ID,
                run_manifest_id=USER_ID,
                manifest_hash="a" * 64,
            ),
            run_conditions=[],
            allowed_changes=[],
            key_results=[],
            highest_risk=RiskSeverity.CRITICAL,
            highlighted_risks=[
                RiskItem(
                    code="BLOCKED",
                    severity=RiskSeverity.CRITICAL,
                    message="critical",
                    blocking=True,
                )
            ],
            collapsed_low_risk_count=0,
            collapsed_medium_risk_count=0,
            evidence_counts={item: 0 for item in EvidenceType},
            review_eligibility=ReviewEligibility.RESEARCHER_OR_OWNER,
            can_confirm=True,
            requires_owner=False,
            summary_available=True,
            source_hash="b" * 64,
            generated_at=NOW,
        )


def test_query_defaults_exclude_historical_results() -> None:
    command = ExperimentQueryCommand(
        project_id=PROJECT_ID,
        query="融合系数实验",
        protocol="40/20",
    )

    assert command.verification_status is VerificationStatus.CONFIRMED
    assert ExperimentStatus.DEPRECATED not in command.statuses
    assert ExperimentStatus.SUPERSEDED not in command.statuses
    assert command.include_historical is False


def test_historical_query_result_cannot_be_current() -> None:
    with pytest.raises(ValidationError, match="当前有效"):
        ExperimentQueryResult(
            experiment_id=INTENT_ID,
            submission_id=USER_ID,
            name="fusion",
            experiment_mode=ExperimentMode.FORMAL,
            status=ExperimentStatus.SUPERSEDED,
            dataset="NTU60",
            protocol="40/20",
            model_name="shift-gcn",
            seed=1,
            current_valid=True,
            verification_status=VerificationStatus.CONFIRMED,
            manifest_id=USER_ID,
            manifest_hash="a" * 64,
            plan_check_id=USER_ID,
            context_id=CONTEXT_ID,
            context_version=2,
            intent_id=INTENT_ID,
            intent_version=3,
            retrieval_role="CANDIDATE_EVIDENCE",
            detail_level="SUMMARY",
            vector_similarity=0.8,
            summary=GeneratedSummary(
                text="历史实验摘要",
                model_id="test-model",
                source_hash="b" * 64,
                generated_at=NOW,
                disclaimer="模型摘要仅用于解释，不代表实验已经验证正确。",
            ),
            metrics=[],
            config_hash="c" * 64,
            git_commit="abcdef1",
        )
