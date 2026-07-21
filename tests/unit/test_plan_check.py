"""训练前确定性规则和证据边界的核心验收测试。"""

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from experiment_guardian.domain.contracts import (
    ConfigurationDocument,
    ExperimentCheckPlanCommand,
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
    EvidenceApplicability,
    EvidenceType,
    ProtectionLevel,
    VerificationStatus,
)
from experiment_guardian.domain.plan_check import (
    ConfigurationError,
    _flatten,
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
EXPECTED_VALUES = {
    "dataset.protocol": "40/20",
    "model.backbone": "shift-gcn",
    "model.fusion": 0.2,
}


def evidence(value: object, source: str) -> FieldEvidence:
    return FieldEvidence(
        value=value,
        evidence_type=EvidenceType.LOCAL_ATTESTED,
        source=source,
        collected_at=COLLECTED_AT,
        collection_tool="experiment-guardian-local-preflight/0.1",
    )


def not_applicable(reason: str) -> FieldEvidence:
    return FieldEvidence(
        evidence_type=EvidenceType.LOCAL_ATTESTED,
        source="local run plan",
        collected_at=COLLECTED_AT,
        collection_tool="experiment-guardian-local-preflight/0.1",
        applicability=EvidenceApplicability.NOT_APPLICABLE,
        not_applicable_reason=reason,
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
        expected_value=EXPECTED_VALUES.get(path),
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
    checkpoint: str | None = CHECKPOINT_PATH,
):
    attestation = (local_attestation or complete_attestation()).model_copy(deep=True)
    attestation.config_sha256 = evidence(
        hashlib.sha256(content.encode("utf-8")).hexdigest(), "sha256sum config.yaml"
    )
    return evaluate_plan(
        PlanEvaluationInput(
            baseline_config=BASELINE,
            candidate=ConfigurationDocument(format=ConfigFormat.YAML, content=content),
            constraints=constraints,
            allowed_variable_paths={"model.fusion"},
            local_attestation=attestation,
            git_commit=GIT_COMMIT,
            run_command=RUN_COMMAND,
            checkpoint=checkpoint,
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
        local_attestation=complete_attestation(git_commit="deadbeef"),
    )

    assert result.check_result is CheckResult.NEEDS_APPROVAL
    risk = next(item for item in result.risks if item.code == "LOCAL_ATTESTATION_CONFLICT")
    assert risk.field_path == "git_commit"
    assert risk.evidence_type is EvidenceType.LOCAL_ATTESTED


def test_not_applicable_local_evidence_is_not_treated_as_missing() -> None:
    attestation = complete_attestation()
    attestation.checkpoint_exists = not_applicable("本次实验从头训练")
    attestation.checkpoint_path = not_applicable("本次实验不加载 checkpoint")
    attestation.environment.cuda = not_applicable("本次任务只使用 CPU")
    attestation.environment.pytorch = not_applicable("本次任务不依赖 PyTorch")

    result = evaluate(
        """
dataset:
  protocol: 40/20
model:
  backbone: shift-gcn
  fusion: 0.2
""",
        [],
        local_attestation=attestation,
        checkpoint=None,
    )

    assert result.check_result is CheckResult.PASS
    assert not result.risks


def test_not_applicable_evidence_requires_reason() -> None:
    with pytest.raises(ValueError, match="必须说明不适用原因"):
        FieldEvidence(
            evidence_type=EvidenceType.LOCAL_ATTESTED,
            source="local run plan",
            collected_at=COLLECTED_AT,
            collection_tool="experiment-guardian-local-preflight/0.1",
            applicability=EvidenceApplicability.NOT_APPLICABLE,
        )


def test_not_applicable_evidence_cannot_carry_value() -> None:
    with pytest.raises(ValueError, match="不能同时携带实际值"):
        FieldEvidence(
            value="12.4",
            evidence_type=EvidenceType.LOCAL_ATTESTED,
            source="local run plan",
            collected_at=COLLECTED_AT,
            collection_tool="experiment-guardian-local-preflight/0.1",
            applicability=EvidenceApplicability.NOT_APPLICABLE,
            not_applicable_reason="本次任务只使用 CPU",
        )


def test_applicable_evidence_requires_actual_value() -> None:
    with pytest.raises(ValueError, match="必须携带实际值"):
        FieldEvidence(
            evidence_type=EvidenceType.LOCAL_ATTESTED,
            source="local preflight",
            collected_at=COLLECTED_AT,
            collection_tool="experiment-guardian-local-preflight/0.1",
        )


def test_core_local_evidence_cannot_be_not_applicable() -> None:
    attestation = complete_attestation()

    with pytest.raises(ValueError, match="run_command"):
        LocalAttestation(
            working_tree_clean=attestation.working_tree_clean,
            git_branch=attestation.git_branch,
            git_commit=attestation.git_commit,
            run_command=not_applicable("尝试绕过命令采集"),
            output_directory_exists=attestation.output_directory_exists,
            checkpoint_exists=attestation.checkpoint_exists,
            checkpoint_path=attestation.checkpoint_path,
            config_sha256=attestation.config_sha256,
            git_diff_sha256=attestation.git_diff_sha256,
            environment=attestation.environment,
        )


def test_checkpoint_not_applicable_conflicts_with_planned_checkpoint() -> None:
    attestation = complete_attestation()
    attestation.checkpoint_exists = not_applicable("本次实验从头训练")
    attestation.checkpoint_path = not_applicable("本次实验不加载 checkpoint")

    result = evaluate(
        """
dataset:
  protocol: 40/20
model:
  backbone: shift-gcn
  fusion: 0.2
""",
        [],
        local_attestation=attestation,
    )

    assert result.check_result is CheckResult.NEEDS_APPROVAL
    assert any(risk.code == "CHECKPOINT_APPLICABILITY_CONFLICT" for risk in result.risks)


def test_rule_engine_defends_against_mutated_core_not_applicable_evidence() -> None:
    config_content = """
dataset:
  protocol: 40/20
model:
  backbone: shift-gcn
  fusion: 0.2
"""
    attestation = complete_attestation()
    attestation.config_sha256 = evidence(
        hashlib.sha256(config_content.encode("utf-8")).hexdigest(), "sha256sum config.yaml"
    )
    attestation.run_command = not_applicable("对象构建后被错误修改")
    # 模拟内部代码使用 model_construct 绕过 Pydantic 校验；规则引擎仍不得返回 PASS。
    evaluation_input = PlanEvaluationInput.model_construct(
        baseline_config=BASELINE,
        candidate=ConfigurationDocument(
            format=ConfigFormat.YAML,
            content=config_content,
        ),
        constraints=[],
        allowed_variable_paths={"model.fusion"},
        local_attestation=attestation,
        git_commit=GIT_COMMIT,
        run_command=RUN_COMMAND,
        checkpoint=CHECKPOINT_PATH,
    )
    result = evaluate_plan(evaluation_input)

    assert result.check_result is CheckResult.NEEDS_APPROVAL
    assert any(
        risk.code == "CORE_LOCAL_ATTESTATION_REQUIRED" and risk.field_path == "run_command"
        for risk in result.risks
    )


def test_formal_baseline_must_match_confirmed_expected_value() -> None:
    drifted_baseline = {
        "dataset": {"protocol": "48/12"},
        "model": {"backbone": "shift-gcn", "fusion": 0.2},
    }
    result = evaluate_plan(
        PlanEvaluationInput(
            baseline_config=drifted_baseline,
            candidate=ConfigurationDocument(
                format=ConfigFormat.YAML,
                content="""
dataset:
  protocol: 48/12
model:
  backbone: shift-gcn
  fusion: 0.2
""",
            ),
            constraints=[constraint("dataset.protocol", ProtectionLevel.LOCKED)],
            local_attestation=complete_attestation(),
            git_commit=GIT_COMMIT,
            run_command=RUN_COMMAND,
            checkpoint=CHECKPOINT_PATH,
        )
    )

    assert result.check_result is CheckResult.BLOCKED
    assert any(risk.code == "FORMAL_BASELINE_CONSTRAINT_MISMATCH" for risk in result.risks)


def test_same_path_pending_constraints_are_all_reported() -> None:
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
                ProtectionLevel.EXPERIMENT_VARIABLE,
                verification_status=VerificationStatus.PENDING,
                expected_value=0.25,
            ),
            constraint(
                "model.fusion",
                ProtectionLevel.EXPERIMENT_VARIABLE,
                source_type=ConstraintSource.INFERRED,
                verification_status=VerificationStatus.PENDING,
                expected_value=0.3,
            ),
        ],
    )

    assert result.check_result is CheckResult.NEEDS_APPROVAL
    risk = next(item for item in result.risks if item.code == "CONFLICTING_PENDING_CONSTRAINTS")
    assert [item["expected_value"] for item in risk.constraint_candidates] == [0.25, 0.3]


def test_config_comparison_distinguishes_boolean_integer_and_float_types() -> None:
    content = "flag: 1\nratio: 1.0\n"
    attestation = complete_attestation()
    attestation.config_sha256 = evidence(
        hashlib.sha256(content.encode("utf-8")).hexdigest(), "sha256sum config.yaml"
    )
    result = evaluate_plan(
        PlanEvaluationInput(
            baseline_config={"flag": True, "ratio": 1},
            candidate=ConfigurationDocument(format=ConfigFormat.YAML, content=content),
            constraints=[],
            allowed_variable_paths={"flag", "ratio"},
            local_attestation=attestation,
            git_commit=GIT_COMMIT,
            run_command=RUN_COMMAND,
            checkpoint=CHECKPOINT_PATH,
        )
    )

    assert result.check_result is CheckResult.PASS
    assert [item.parameter_path for item in result.changes] == ["flag", "ratio"]


def test_expected_value_and_allowed_values_use_strict_types() -> None:
    content = "flag: true\n"
    attestation = complete_attestation()
    attestation.config_sha256 = evidence(
        hashlib.sha256(content.encode("utf-8")).hexdigest(), "sha256sum config.yaml"
    )
    strict_constraint = constraint(
        "flag",
        ProtectionLevel.EXPERIMENT_VARIABLE,
        expected_value=False,
        allowed_values=[1],
    )
    result = evaluate_plan(
        PlanEvaluationInput(
            baseline_config={"flag": False},
            candidate=ConfigurationDocument(format=ConfigFormat.YAML, content=content),
            constraints=[strict_constraint],
            allowed_variable_paths={"flag"},
            local_attestation=attestation,
            git_commit=GIT_COMMIT,
            run_command=RUN_COMMAND,
            checkpoint=CHECKPOINT_PATH,
        )
    )

    assert result.check_result is CheckResult.BLOCKED
    assert any(risk.code == "EXPERIMENT_VARIABLE_OUT_OF_RANGE" for risk in result.risks)


def test_local_config_hash_must_match_received_document_bytes() -> None:
    content = "dataset:\n  protocol: 40/20\n"
    result = evaluate_plan(
        PlanEvaluationInput(
            baseline_config={"dataset": {"protocol": "40/20"}},
            candidate=ConfigurationDocument(format=ConfigFormat.YAML, content=content),
            constraints=[],
            local_attestation=complete_attestation(),
            git_commit=GIT_COMMIT,
            run_command=RUN_COMMAND,
            checkpoint=CHECKPOINT_PATH,
        )
    )

    assert result.check_result is CheckResult.BLOCKED
    risk = next(item for item in result.risks if item.code == "CONFIG_DOCUMENT_SHA256_MISMATCH")
    assert risk.blocking is True
    assert risk.evidence_type is EvidenceType.CLOUD_VERIFIED
    assert result.document_sha256 == hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_plan_input_size_git_command_and_hash_formats_are_bounded() -> None:
    with pytest.raises(ValueError, match="1 MiB"):
        ConfigurationDocument(format=ConfigFormat.YAML, content="测" * 600_000)

    attestation = complete_attestation()
    invalid_attestation = attestation.model_dump()
    invalid_attestation["config_sha256"]["value"] = "not-a-sha256"
    with pytest.raises(ValueError, match="64 位十六进制"):
        LocalAttestation.model_validate(invalid_attestation)

    command_values = {
        "project_id": uuid4(),
        "experiment_intent_id": uuid4(),
        "idempotency_key": uuid4(),
        "configuration": {"format": "yaml", "content": "value: 1\n"},
        "command": RUN_COMMAND,
        "git_commit": "not-a-git-sha",
        "local_attestation": attestation,
    }
    with pytest.raises(ValueError, match="git_commit"):
        ExperimentCheckPlanCommand.model_validate(command_values)

    command_values["git_commit"] = GIT_COMMIT
    command_values["command"] = "x" * 8193
    with pytest.raises(ValueError, match="command"):
        ExperimentCheckPlanCommand.model_validate(command_values)


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


@pytest.mark.parametrize(
    ("config_format", "content"),
    [
        (ConfigFormat.YAML, "dataset:\n  split: 40/20\n  split: 48/12\n"),
        (ConfigFormat.JSON, '{"dataset": {"split": "40/20", "split": "48/12"}}'),
    ],
)
def test_duplicate_config_fields_are_rejected(config_format: ConfigFormat, content: str) -> None:
    with pytest.raises(ConfigurationError, match="重复字段"):
        parse_configuration(ConfigurationDocument(format=config_format, content=content))


def test_yaml_non_string_key_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="不是字符串"):
        parse_configuration(ConfigurationDocument(format=ConfigFormat.YAML, content="1: value\n"))


def test_yaml_ambiguous_scalars_remain_strings() -> None:
    parsed = parse_configuration(
        ConfigurationDocument(
            format=ConfigFormat.YAML,
            content="""
flag: yes
mode: on
date: 2026-07-21
leading_zero: 012
enabled: true
disabled: false
count: 12
ratio: 0.25
empty:
""",
        )
    )

    assert parsed == {
        "flag": "yes",
        "mode": "on",
        "date": "2026-07-21",
        "leading_zero": "012",
        "enabled": True,
        "disabled": False,
        "count": 12,
        "ratio": 0.25,
        "empty": None,
    }
    canonical_config_hash(parsed)


def test_flatten_escapes_literal_dot_and_backslash_keys() -> None:
    flattened = _flatten({"a.b": 1, "a": {"b": 2}, r"a\b": 3, "": 4})

    assert flattened == {r"a\.b": 1, "a.b": 2, r"a\\b": 3, r"\0": 4}
