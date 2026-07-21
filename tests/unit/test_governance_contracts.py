"""候选意图、正式上下文、查询和人工确认契约测试。"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from experiment_guardian.domain.contracts import (
    ExperimentIntentReference,
    ExperimentQueryCommand,
    ExperimentQueryResult,
    IntentInterpretation,
    ParameterConstraint,
    ProjectContextBundle,
    ProjectContextReference,
    SubmissionReceipt,
)
from experiment_guardian.domain.enums import (
    ConstraintSource,
    ExperimentMode,
    ExperimentStatus,
    IntentStatus,
    ProtectionLevel,
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
            context_payload={"protocol": "40/20"},
        )


def test_critical_receipt_cannot_be_confirmed() -> None:
    with pytest.raises(ValidationError, match="CRITICAL"):
        SubmissionReceipt(
            submission_id=INTENT_ID,
            objective="测试融合系数",
            allowed_changes=[],
            key_results={"top1": 46.7},
            highest_risk=RiskSeverity.CRITICAL,
            highlighted_risks=[],
            collapsed_low_risk_count=0,
            can_confirm=True,
            requires_owner=True,
        )


def test_query_defaults_exclude_historical_results() -> None:
    command = ExperimentQueryCommand(
        project_id=PROJECT_ID,
        actor_id=USER_ID,
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
            status=ExperimentStatus.SUPERSEDED,
            protocol="40/20",
            model_name="shift-gcn",
            seed=1,
            current_valid=True,
            verification_status=VerificationStatus.CONFIRMED,
            manifest_id=USER_ID,
            context_id=CONTEXT_ID,
            context_version=2,
            intent_id=INTENT_ID,
            intent_version=3,
            payload={"top1": 46.7},
        )
