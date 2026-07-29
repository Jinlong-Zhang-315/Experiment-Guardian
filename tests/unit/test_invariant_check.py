from datetime import UTC, datetime
from uuid import uuid4

from experiment_guardian.domain.contracts import FieldEvidence
from experiment_guardian.domain.enums import EvidenceType
from experiment_guardian.domain.invariant_check import (
    ApprovedInvariantSnapshot,
    ApprovedPlanTrace,
    FinalRunEvidence,
    InvariantAttestation,
    InvariantCheckReport,
    KeyInvariant,
    evaluate_pre_run_invariants,
    evaluate_submission_invariants,
)

NOW = datetime(2026, 7, 28, tzinfo=UTC)


def trace() -> ApprovedPlanTrace:
    return ApprovedPlanTrace(
        plan_id=uuid4(),
        revision_id=uuid4(),
        revision=2,
        decision_id=uuid4(),
        decision_hash="a" * 64,
        review_hash="b" * 64,
        policy_hash="c" * 64,
    )


def snapshot(*invariants: KeyInvariant) -> ApprovedInvariantSnapshot:
    return ApprovedInvariantSnapshot(
        trace=trace(),
        invariants=list(invariants),
        plan_evidence={},
    )


def local(value: object) -> FieldEvidence:
    return FieldEvidence(
        value=value,
        evidence_type=EvidenceType.LOCAL_ATTESTED,
        source="local agent",
        collected_at=NOW,
        collection_tool="test-agent/1",
    )


def natural_attestation(invariant_id: str, status: str) -> InvariantAttestation:
    return InvariantAttestation.model_validate(
        {
            "invariant_id": invariant_id,
            "status": status,
            "explanation": "已检查相关实现和运行材料。",
            "source": "local review",
            "collected_at": NOW,
            "collection_tool": "test-agent/1",
        }
    )


def test_confirmed_structured_invariant_uses_strict_json_types() -> None:
    approved = snapshot(
        KeyInvariant(
            invariant_id="ci_protocol",
            source_type="CONFIRMED_CANDIDATE",
            statement="协议编号保持为整数 1。",
            representation="STRUCTURED_PARAMETER",
            parameter_path="protocol",
            expected_value=1,
            verification_method="严格配置比较",
            evidence_type=EvidenceType.USER_PROVIDED,
            source_reference="experiment_plan_candidate:ci_protocol",
        )
    )

    report = evaluate_pre_run_invariants(
        snapshot=approved,
        parsed_config={"protocol": True},
        git_commit="abc1234",
        run_command="python train.py",
        checkpoint=None,
        attestations=[],
        deviation_explanation=None,
    )

    assert report.overall_status == "CRITICAL_DEVIATION"
    assert report.checks[0].outcome == "VIOLATED"
    assert report.checks[0].blocking is True


def test_natural_language_invariant_uses_local_attestation_boundary() -> None:
    invariant = KeyInvariant(
        invariant_id="ci_mainline",
        source_type="CONFIRMED_CANDIDATE",
        statement="保持主干行为不变。",
        representation="NATURAL_LANGUAGE",
        verification_method="检查 diff 和冒烟测试",
        evidence_type=EvidenceType.USER_PROVIDED,
        source_reference="experiment_plan_candidate:ci_mainline",
    )
    approved = snapshot(invariant)

    missing = evaluate_pre_run_invariants(
        snapshot=approved,
        parsed_config={},
        git_commit="abc1234",
        run_command="python train.py",
        checkpoint=None,
        attestations=[],
        deviation_explanation=None,
    )
    satisfied = evaluate_pre_run_invariants(
        snapshot=approved,
        parsed_config={},
        git_commit="abc1234",
        run_command="python train.py",
        checkpoint=None,
        attestations=[natural_attestation("ci_mainline", "SATISFIED")],
        deviation_explanation=None,
    )

    assert missing.overall_status == "NEEDS_EXPLANATION"
    assert satisfied.overall_status == "CONSISTENT"
    assert satisfied.checks[0].evidence_type is EvidenceType.LOCAL_ATTESTED
    assert "云端未独立验证" in satisfied.checks[0].message


def test_submission_missing_final_evidence_is_blocking() -> None:
    invariant = KeyInvariant(
        invariant_id="condition:smoke",
        source_type="APPROVAL_CONDITION",
        statement="正式训练前完成冒烟测试。",
        representation="NATURAL_LANGUAGE",
        verification_method="本地声明",
        evidence_type=EvidenceType.USER_PROVIDED,
        source_reference="experiment_plan_decision:test",
    )
    approved = snapshot(invariant)
    pre_run = InvariantCheckReport(
        stage="PRE_RUN",
        overall_status="CONSISTENT",
        trace=approved.trace,
        checks=[],
    )

    report = evaluate_submission_invariants(
        snapshot=approved,
        manifest_report=pre_run,
        parsed_config={},
        config_document_sha256="d" * 64,
        manifest_git_commit="abc1234",
        manifest_run_command="python train.py",
        manifest_checkpoint=None,
        final_evidence=None,
    )

    assert report.overall_status == "CRITICAL_DEVIATION"
    assert any(item.invariant_id == "condition:smoke" and item.blocking for item in report.checks)
    assert any(item.invariant_id == "final.config_sha256" for item in report.checks)


def test_submission_matching_final_evidence_passes() -> None:
    approved = snapshot()
    pre_run = InvariantCheckReport(
        stage="PRE_RUN",
        overall_status="CONSISTENT",
        trace=approved.trace,
        checks=[],
    )
    final = FinalRunEvidence(
        git_commit=local("abc1234"),
        run_command=local("python train.py"),
        config_sha256=local("d" * 64),
    )

    report = evaluate_submission_invariants(
        snapshot=approved,
        manifest_report=pre_run,
        parsed_config={},
        config_document_sha256="d" * 64,
        manifest_git_commit="abc1234",
        manifest_run_command="python train.py",
        manifest_checkpoint=None,
        final_evidence=final,
    )

    assert report.overall_status == "CONSISTENT"
    assert all(item.outcome == "MATCH" for item in report.checks)
