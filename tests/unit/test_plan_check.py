"""训练前确定性规则的核心验收测试。"""

import pytest

from experiment_guardian.domain.contracts import (
    ConfigurationDocument,
    LocalAttestation,
    ParameterConstraint,
    PlanEvaluationInput,
)
from experiment_guardian.domain.enums import (
    ApprovalStatus,
    CheckResult,
    ConfigFormat,
    ProtectionLevel,
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


def constraint(path: str, level: ProtectionLevel, **kwargs: object) -> ParameterConstraint:
    return ParameterConstraint(
        parameter_path=path,
        protection_level=level,
        expected_value=None,
        **kwargs,
    )


def evaluate(content: str, constraints: list[ParameterConstraint]):
    return evaluate_plan(
        PlanEvaluationInput(
            baseline_config=BASELINE,
            candidate=ConfigurationDocument(format=ConfigFormat.YAML, content=content),
            constraints=constraints,
            allowed_variable_paths={"model.fusion"},
            local_attestation=LocalAttestation(working_tree_clean=True),
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
