"""R15b Agent 确定性比较与描述统计测试。"""

from dataclasses import replace
from uuid import uuid4

import pytest

from experiment_guardian.domain.agent_analysis import (
    ExperimentAnalysisRecord,
    MetricRecord,
    compare_experiments,
    configuration_diff,
    repeated_experiment_statistics,
)


def record(
    *,
    value: float = 0.8,
    seed: int = 1,
    config: dict[str, object] | None = None,
    **changes: object,
) -> ExperimentAnalysisRecord:
    base = ExperimentAnalysisRecord(
        experiment_id=uuid4(),
        name=f"experiment-seed-{seed}",
        status="COMPLETED",
        dataset="NTU60",
        protocol="40/20",
        model_name="shift-gcn",
        seed=seed,
        experiment_mode="FORMAL",
        context_id=uuid4(),
        context_version=1,
        intent_id=uuid4(),
        intent_version=1,
        git_commit="a" * 40,
        checkpoint="baseline.pt",
        command="python train.py",
        config=config or {"seed": seed, "fusion": {"coefficient": 0.2}},
        metrics=(
            MetricRecord(
                name="top1",
                value=value,
                split="REPORTED",
                aggregation_type="SINGLE_RUN",
                epoch=None,
                is_primary=True,
            ),
        ),
        primary_metric_name="top1",
        higher_is_better=True,
        trace_complete=True,
    )
    return replace(base, **changes)


def aligned_pair() -> tuple[ExperimentAnalysisRecord, ExperimentAnalysisRecord]:
    left = record(value=0.8, seed=1)
    right = record(
        value=0.84,
        seed=2,
        context_id=left.context_id,
        intent_id=left.intent_id,
    )
    return left, right


def test_compare_uses_layered_gate_and_zero_relative_delta() -> None:
    left, right = aligned_pair()
    result = compare_experiments(left, right)
    assert result["comparability"] == "COMPARABLE_WITH_CAVEATS"
    assert result["hard_blockers"] == []
    assert result["metric"]["delta_right_minus_left"] == pytest.approx(0.04)
    assert result["metric"]["outcome"] == "RIGHT_BETTER"
    assert "seed 不一致" in result["caveats"]

    zero = replace(left, metrics=(replace(left.metrics[0], value=0.0),))
    zero_result = compare_experiments(zero, right)
    assert zero_result["metric"]["relative_delta"] is None


def test_compare_hard_blocks_dataset_protocol_metric_and_direction_conflicts() -> None:
    left, right = aligned_pair()
    incompatible = replace(
        right,
        dataset="Other",
        protocol="48/12",
        higher_is_better=False,
        metrics=(replace(right.metrics[0], split="VALIDATION"),),
    )
    result = compare_experiments(left, incompatible)
    assert result["comparability"] == "NOT_COMPARABLE"
    assert result["metric"] is None
    assert {
        "dataset 不一致",
        "protocol 不一致",
        "指标名称、split、聚合方式或 epoch 语义不一致",
        "两个 Context 对主指标方向的定义冲突",
    } <= set(result["hard_blockers"])


def test_configuration_diff_is_type_strict_and_path_safe() -> None:
    changes, truncated = configuration_diff(
        {"flag": True, "count": 1, "a.b": 1},
        {"flag": 1, "count": 1.0, "a": {"b": 1}},
    )
    assert not truncated
    assert {item["path"] for item in changes} == {
        "flag",
        "count",
        r"a\.b",
        "a.b",
    }


def test_repeated_statistics_require_explicit_strict_cohort() -> None:
    left, right = aligned_pair()
    third = record(
        value=0.82,
        seed=3,
        context_id=left.context_id,
        intent_id=left.intent_id,
    )
    result = repeated_experiment_statistics([left, right, third])
    assert result["accepted"] is True
    assert result["statistics"]["mean"] == pytest.approx(0.82)
    assert result["statistics"]["sample_standard_deviation"] == pytest.approx(0.02)
    assert result["statistics"]["median"] == pytest.approx(0.82)
    assert result["best_experiment_id"] == str(right.experiment_id)

    changed = replace(
        third,
        config={"seed": 3, "fusion": {"coefficient": 0.3}},
    )
    rejected = repeated_experiment_statistics([left, right, changed])
    assert rejected["accepted"] is False
    assert any("非 seed 配置不一致" in item for item in rejected["incompatibilities"])
    assert "statistics" not in rejected


def test_repeated_statistics_warns_on_duplicate_seed_and_unknown_direction() -> None:
    left, right = aligned_pair()
    right = replace(
        right,
        seed=left.seed,
        config=left.config,
        higher_is_better=None,
    )
    left = replace(left, higher_is_better=None)
    result = repeated_experiment_statistics([left, right])
    assert result["accepted"] is True
    assert result["duplicate_seed_warning"] is True
    assert result["best_experiment_id"] is None


def test_repeated_statistics_rejects_duplicate_ids() -> None:
    item = record()
    with pytest.raises(ValueError, match="不能重复"):
        repeated_experiment_statistics([item, item])
