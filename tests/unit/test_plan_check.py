"""训练前确定性规则和证据边界的核心验收测试。"""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from experiment_guardian.domain.contracts import (
    ConfigurationDocument,
    FieldEvidence,
    LocalAttestation,
    LocalEnvironment,
    ParameterConstraint,
    PlanEvaluationInput,
)
from experiment_guardian.domain.enums import (
    ApprovalStatus,
    CheckResult,
    ConfigFormat,
    ConstraintSource,
    EvidenceType,
    ProtectionLevel,
    VerificationStatus,
)
from experiment_guardian.domain.plan_check import (
    ConfigurationError,
    canonical_config_hash,
    evaluate_plan,
    parse_configuration,
)

BASELINE = {
    "dataset": {"protocol": "40/20"},
    "model": {"backbone": "shift-gcn", "fusion": 0.2},
}
CONFIRMER_ID = UUID("00000000-0000-0000-0000-000000000001")
COLLECTED_AT = datetime(2026, 7, 21, tzinfo=UTC)
GIT_COMMIT = "a1b2c3d4"
RUN_COMMAND = "python train.py --config config.yaml"
CHECKPOINT_PATH = "checkpoints/baseline.pt"


def evidence(value: object, source: str) -> FieldEvidence:
    return FieldEvidence(
        value=value,
        evidence_type=EvidenceType.LOCAL_ATTESTED,
        source=source,
        collected_at=COLLECTED_AT,
        collection_tool="experiment-guardian-local-preflight/0.1",
    )


def complete_attestation(
    *, output_directory_exists: bool = False, git_commit: str = GIT_COMMIT
) -> LocalAttestation:
    return LocalAttestation(
        working_tree_clean=evidence(True, "git status --porcelain"),
        git_branch=evidence("main", "git branch --show-current"),
        git_commit=evidence(git_commit, "git rev-parse HEAD"),
        run_command=evidence(RUN_COMMAND, "local agent run plan"),
        output_directory_exists=evidence(output_directory_exists, "local filesystem"),
        checkpoint_exists=evidence(True, "local filesystem"),
        checkpoint_path=evidence(CHECKPOINT_PATH, "local run plan"),
        config_sha256=evidence("a" * 64, "sha256sum config.yaml"),
        git_diff_sha256=evidence("b" * 64, "git diff"),
        environment=LocalEnvironment(
            python=evidence("3.12.13", "python --version"),
            cuda=evidence("12.4", "nvidia-smi"),
            pytorch=evidence("2.7.0", "python import torch"),
        ),
    )


def constraint(
    path: str,
    level: ProtectionLevel,
    *,
    source_type: ConstraintSource = ConstraintSource.EXPLICIT,
    verification_status: VerificationStatus = VerificationStatus.CONFIRMED,
    **kwargs: object,
) -> ParameterConstraint:
    values: dict[str, object] = dict(
        parameter_path=path,
        protection_level=level,
        expected_value=None,
        source_type=source_type,
        verification_status=verification_status,
        original_message=f"约束 {path}",
    )
    if source_type is ConstraintSource.INFERRED:
        values.update(inference_basis="根据上下文中的控制变量推断", confidence=0.82)
    if verification_status is VerificationStatus.CONFIRMED:
        values.update(confirmed_by=CONFIRMER_ID, confirmed_at=COLLECTED_AT)
    values.update(kwargs)
    return ParameterConstraint.model_validate(values)


def evaluate(
    content: str,
    constraints: list[ParameterConstraint],
    *,
    local_attestation: LocalAttestation | None = None,
):
    return evaluate_plan(
        PlanEvaluationInput(
            baseline_config=BASELINE,
            candidate=ConfigurationDocument(format=ConfigFormat.YAML, content=content),
            constraints=constraints,
            allowed_variable_paths={"model.fusion"},
            local_attestation=local_attestation or complete_attestation(),
            git_commit=GIT_COMMIT,
            run_command=RUN_COMMAND,
            checkpoint=CHECKPOINT_PATH,
        )
    )


def test_experiment_variable_change_passes() -> None:
    result = evaluate(
        """
dataset:
  protocol: 40/20
model:
  backbone: shift-gcn
  fusion: 0.3
""",
        [constraint("model.fusion", ProtectionLevel.EXPERIMENT_VARIABLE, minimum=0, maximum=1)],
    )

    assert result.check_result is CheckResult.PASS
    assert result.approval_status is ApprovalStatus.NOT_REQUIRED
    assert [item.parameter_path for item in result.changes] == ["model.fusion"]


def test_locked_change_cannot_be_approved() -> None:
    result = evaluate(
        """
dataset:
  protocol: 48/12
model:
  backbone: resnet
  fusion: 0.3
""",
        [
            constraint("dataset.protocol", ProtectionLevel.LOCKED),
            constraint("model.backbone", ProtectionLevel.APPROVAL_REQUIRED),
            constraint("model.fusion", ProtectionLevel.EXPERIMENT_VARIABLE),
        ],
    )

    assert result.check_result is CheckResult.BLOCKED
    assert result.approval_status is ApprovalStatus.NOT_REQUIRED
    assert any(risk.code == "LOCKED_PARAMETER_CHANGED" for risk in result.risks)


def test_approval_required_change_is_pending() -> None:
    result = evaluate(
        """
dataset:
  protocol: 40/20
model:
  backbone: resnet
  fusion: 0.2
""",
        [constraint("model.backbone", ProtectionLevel.APPROVAL_REQUIRED)],
    )

    assert result.check_result is CheckResult.NEEDS_APPROVAL
    assert result.approval_status is ApprovalStatus.PENDING


def test_unconfirmed_inferred_locked_constraint_cannot_block() -> None:
    result = evaluate(
        """
dataset:
  protocol: 48/12
model:
  backbone: shift-gcn
  fusion: 0.2
""",
        [
            constraint(
                "dataset.protocol",
                ProtectionLevel.LOCKED,
                source_type=ConstraintSource.INFERRED,
                verification_status=VerificationStatus.PENDING,
            )
        ],
    )

    assert result.check_result is CheckResult.NEEDS_APPROVAL
    risk = next(item for item in result.risks if item.code == "UNCONFIRMED_CONSTRAINT_CANDIDATE")
    assert risk.blocking is False
    assert "模型推断" in risk.message


def test_rejected_constraint_does_not_affect_allowed_variable() -> None:
    result = evaluate(
        """
dataset:
  protocol: 40/20
model:
  backbone: shift-gcn
  fusion: 0.3
""",
        [
            constraint(
                "model.fusion",
                ProtectionLevel.LOCKED,
                verification_status=VerificationStatus.REJECTED,
            )
        ],
    )

    assert result.check_result is CheckResult.PASS
    assert not result.risks


def test_local_output_conflict_preserves_attested_source() -> None:
    result = evaluate(
        """
dataset:
  protocol: 40/20
model:
  backbone: shift-gcn
  fusion: 0.2
""",
        [],
        local_attestation=complete_attestation(output_directory_exists=True),
    )

    assert result.check_result is CheckResult.NEEDS_APPROVAL
    risk = next(item for item in result.risks if item.code == "OUTPUT_DIRECTORY_CONFLICT_ATTESTED")
    assert risk.evidence_type is EvidenceType.LOCAL_ATTESTED
    assert risk.evidence_source == "local filesystem"
    assert "本地 Agent" in risk.message


def test_conflicting_git_commit_attestation_requires_approval() -> None:
    result = evaluate(
        """
dataset:
  protocol: 40/20
model:
  backbone: shift-gcn
  fusion: 0.2
""",
        [],
        local_attestation=complete_attestation(git_commit="different-commit"),
    )

    assert result.check_result is CheckResult.NEEDS_APPROVAL
    risk = next(item for item in result.risks if item.code == "LOCAL_ATTESTATION_CONFLICT")
    assert risk.field_path == "git_commit"
    assert risk.evidence_type is EvidenceType.LOCAL_ATTESTED


def test_yaml_and_json_have_same_canonical_hash() -> None:
    yaml_config = parse_configuration(
        ConfigurationDocument(format=ConfigFormat.YAML, content="a: 1\nb: 2\n")
    )
    json_config = parse_configuration(
        ConfigurationDocument(format=ConfigFormat.JSON, content='{"b": 2, "a": 1}')
    )

    assert canonical_config_hash(yaml_config) == canonical_config_hash(json_config)


def test_non_object_config_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="根节点"):
        parse_configuration(ConfigurationDocument(format=ConfigFormat.YAML, content="- a\n- b\n"))
