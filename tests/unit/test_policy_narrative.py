"""结构化策略到人类可读表示的确定性与完整性测试。"""

from datetime import UTC, datetime
from uuid import uuid4

from experiment_guardian.domain.contracts import (
    ExperimentIntentPayload,
    ExperimentIntentReference,
    ParameterConstraint,
    ProjectContextPayload,
    ProjectContextReference,
)
from experiment_guardian.domain.enums import (
    ConstraintSource,
    ExperimentMode,
    IntentStatus,
    ProtectionLevel,
    VerificationStatus,
)
from experiment_guardian.domain.policy_narrative import (
    POLICY_NARRATIVE_NOTICE,
    build_policy_narrative_source,
    policy_narrative_source_hash,
    render_policy_narrative,
)


def _source() -> dict[str, object]:
    context_id, intent_id, user_id, project_id = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime(2026, 7, 23, tzinfo=UTC)
    context = ProjectContextReference(
        context_id=context_id,
        version=3,
        confirmed_by=user_id,
        confirmed_at=now,
        effective_at=now,
        change_reason="确认第二轮正式基线",
    )
    intent = ExperimentIntentReference(
        intent_id=intent_id,
        version=4,
        context_id=context_id,
        context_version=3,
        status=IntentStatus.ACTIVE,
        mode=ExperimentMode.FORMAL,
    )
    context_payload = ProjectContextPayload(
        project_id=project_id,
        project_name="NTU60 Guardian",
        description="骨架动作识别实验",
        goal="验证融合策略的正式收益",
        non_goals=["不自动训练", "不替换正式 baseline"],
        mainline_model="shift-gcn",
        baseline={"checkpoint": "baseline.pt", "fusion": 0.2},
        dataset="NTU60",
        protocol="40/20",
        primary_metric={"name": "top1", "higher_is_better": True},
        default_seeds=[1, 2],
        active_branch="main",
        active_config={
            "dataset": {"protocol": "40/20"},
            "model": {"backbone": "shift-gcn", "fusion": 0.2},
        },
        deprecated_items=["legacy split"],
        key_decisions=["protocol 固定为 40/20"],
    )
    intent_payload = ExperimentIntentPayload(
        name="fusion sweep",
        objective="验证 fusion=0.3",
        hypothesis="适度融合可以提高 top1",
        allowed_variables=["model.fusion"],
        controlled_variables=["dataset.protocol", "model.backbone"],
        expected_outputs=["top1"],
        acceptance_criteria=["结果可追溯"],
        original_message="只修改 fusion",
        intent_receipt="Owner 已确认",
    )
    constraints = [
        ParameterConstraint(
            constraint_id=uuid4(),
            version=2,
            parameter_path="dataset.protocol",
            context_id=context_id,
            context_version=3,
            protection_level=ProtectionLevel.LOCKED,
            expected_value="40/20",
            reason="正式协议",
            source_type=ConstraintSource.EXPLICIT,
            verification_status=VerificationStatus.CONFIRMED,
            original_message="固定 protocol",
            confirmed_by=user_id,
            confirmed_at=now,
        ),
        ParameterConstraint(
            constraint_id=uuid4(),
            version=3,
            parameter_path="model.backbone",
            context_id=context_id,
            context_version=3,
            protection_level=ProtectionLevel.APPROVAL_REQUIRED,
            expected_value="shift-gcn",
            reason="主线变化需要审查",
            source_type=ConstraintSource.EXPLICIT,
            verification_status=VerificationStatus.CONFIRMED,
            original_message="backbone 需审批",
            confirmed_by=user_id,
            confirmed_at=now,
        ),
        ParameterConstraint(
            constraint_id=uuid4(),
            version=4,
            parameter_path="model.fusion",
            context_id=context_id,
            context_version=3,
            intent_id=intent_id,
            intent_version=4,
            protection_level=ProtectionLevel.EXPERIMENT_VARIABLE,
            expected_value=0.2,
            minimum=0.0,
            maximum=1.0,
            reason="当前实验变量",
            source_type=ConstraintSource.EXPLICIT,
            verification_status=VerificationStatus.CONFIRMED,
            original_message="允许修改 fusion",
            confirmed_by=user_id,
            confirmed_at=now,
        ),
    ]
    return build_policy_narrative_source(
        context=context,
        intent=intent,
        context_payload=context_payload,
        intent_payload=intent_payload,
        constraints=constraints,
    )


def test_policy_narrative_is_deterministic_and_covers_formal_meaning() -> None:
    source = _source()
    content = render_policy_narrative(source)

    assert render_policy_narrative(source) == content
    assert POLICY_NARRATIVE_NOTICE in content
    for heading in [
        "项目目标",
        "数据集与实验协议",
        "主线模型与基线",
        "实验非目标",
        "当前实验意图",
        "锁定参数",
        "需要 Owner 审批的参数",
        "允许实验的参数",
    ]:
        assert heading in content
    for path in ["dataset.protocol", "model.backbone", "model.fusion"]:
        assert path in content
    assert "不得在实验配置中修改" in content
    assert "修改前必须由 Owner 审批" in content
    assert "不表示系统推荐修改" in content


def test_policy_narrative_hash_changes_with_structured_source() -> None:
    source = _source()
    original = policy_narrative_source_hash(source)
    changed = {**source, "context_payload": {**source["context_payload"], "goal": "新目标"}}  # type: ignore[dict-item]

    assert policy_narrative_source_hash(source) == original
    assert policy_narrative_source_hash(changed) != original


def test_policy_narrative_hash_does_not_depend_on_constraint_input_order() -> None:
    source = _source()
    intent_reference = dict(source["intent_reference"])  # type: ignore[arg-type]
    intent_reference["status"] = IntentStatus.ACTIVE
    constraints = [
        ParameterConstraint.model_validate(item)
        for item in source["constraints"]  # type: ignore[union-attr]
    ]
    rebuilt = build_policy_narrative_source(
        context=ProjectContextReference.model_validate(source["context_reference"]),
        intent=ExperimentIntentReference.model_validate(intent_reference),
        context_payload=ProjectContextPayload.model_validate(source["context_payload"]),
        intent_payload=ExperimentIntentPayload.model_validate(source["intent_payload"]),
        constraints=list(reversed(constraints)),
    )

    assert rebuilt == source
    assert policy_narrative_source_hash(rebuilt) == policy_narrative_source_hash(source)
