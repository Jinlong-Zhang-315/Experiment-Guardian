"""治理 Agent 使用的确定性实验分析。

本模块不访问数据库、不调用模型，也不修改正式记录。应用层先把有权限读取的正式实验
投影为 ``ExperimentAnalysisRecord``，再调用这里的纯函数完成可比性判断和基础统计。
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from experiment_guardian.domain.plan_check import flatten_configuration

Comparability = Literal[
    "COMPARABLE",
    "COMPARABLE_WITH_CAVEATS",
    "NOT_COMPARABLE",
]


@dataclass(frozen=True, slots=True)
class MetricRecord:
    name: str
    value: float
    split: str
    aggregation_type: str
    epoch: int | None
    is_primary: bool

    @property
    def semantics(self) -> tuple[str, str, str, int | None]:
        return (self.name, self.split, self.aggregation_type, self.epoch)


@dataclass(frozen=True, slots=True)
class ExperimentAnalysisRecord:
    experiment_id: UUID
    name: str
    status: str
    dataset: str
    protocol: str
    model_name: str
    seed: int
    experiment_mode: str
    context_id: UUID
    context_version: int
    intent_id: UUID
    intent_version: int
    git_commit: str
    checkpoint: str | None
    command: str
    config: dict[str, Any]
    metrics: tuple[MetricRecord, ...]
    primary_metric_name: str | None
    higher_is_better: bool | None
    trace_complete: bool


def strict_json_equal(left: Any, right: Any) -> bool:
    """按 JSON 类型和值比较，避免 ``True == 1 == 1.0`` 的 Python 语义。"""

    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            strict_json_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            strict_json_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return bool(left == right)


def configuration_diff(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], bool]:
    """返回稳定、无路径碰撞的配置差异，并显式标记是否被截断。"""

    left_flat = flatten_configuration(left)
    right_flat = flatten_configuration(right)
    changes: list[dict[str, Any]] = []
    for path in sorted(set(left_flat) | set(right_flat)):
        left_present = path in left_flat
        right_present = path in right_flat
        if left_present and right_present and strict_json_equal(left_flat[path], right_flat[path]):
            continue
        changes.append(
            {
                "path": path,
                "change_type": (
                    "MODIFIED"
                    if left_present and right_present
                    else "REMOVED"
                    if left_present
                    else "ADDED"
                ),
                "before": left_flat.get(path) if left_present else None,
                "after": right_flat.get(path) if right_present else None,
                "before_present": left_present,
                "after_present": right_present,
            }
        )
    return changes[:limit], len(changes) > limit


def compare_experiments(
    left: ExperimentAnalysisRecord,
    right: ExperimentAnalysisRecord,
    *,
    metric_name: str | None = None,
) -> dict[str, Any]:
    """分层判断两个正式实验是否可比，并在可比时计算确定性指标差。"""

    blockers: list[str] = []
    caveats: list[str] = []
    if left.status != "COMPLETED" or right.status != "COMPLETED":
        blockers.append("只有当前状态为 COMPLETED 的正式实验可以进行结果比较")
    if left.dataset != right.dataset:
        blockers.append("dataset 不一致")
    if left.protocol != right.protocol:
        blockers.append("protocol 不一致")
    if not left.trace_complete or not right.trace_complete:
        blockers.append("至少一个实验的正式追溯链不完整")

    left_metric = _select_metric(left, metric_name)
    right_metric = _select_metric(right, metric_name)
    if left_metric is None or right_metric is None:
        blockers.append(f"至少一个实验缺少指标 {metric_name or '主指标'}")
    elif left_metric.semantics != right_metric.semantics:
        blockers.append("指标名称、split、聚合方式或 epoch 语义不一致")

    direction = _resolve_direction(left, right, left_metric, right_metric)
    if direction == "CONFLICT":
        blockers.append("两个 Context 对主指标方向的定义冲突")

    _append_difference(caveats, left.model_name, right.model_name, "model")
    _append_difference(
        caveats,
        (left.context_id, left.context_version),
        (right.context_id, right.context_version),
        "Context 版本",
    )
    _append_difference(
        caveats,
        (left.intent_id, left.intent_version),
        (right.intent_id, right.intent_version),
        "Intent 版本",
    )
    _append_difference(caveats, left.experiment_mode, right.experiment_mode, "实验模式")
    _append_difference(caveats, left.seed, right.seed, "seed")
    _append_difference(caveats, left.git_commit, right.git_commit, "Git commit")
    _append_difference(caveats, left.checkpoint, right.checkpoint, "checkpoint")
    _append_difference(caveats, left.command, right.command, "运行命令")

    config_changes, config_diff_truncated = configuration_diff(left.config, right.config, limit=50)
    non_seed_changes = [item for item in config_changes if item["path"] != "seed"]
    if non_seed_changes:
        caveats.append("除顶层 seed 外还存在配置差异")

    result: Comparability
    if blockers:
        result = "NOT_COMPARABLE"
    elif caveats:
        result = "COMPARABLE_WITH_CAVEATS"
    else:
        result = "COMPARABLE"

    metric_result: dict[str, Any] | None = None
    if not blockers and left_metric is not None and right_metric is not None:
        delta = right_metric.value - left_metric.value
        metric_result = {
            "name": left_metric.name,
            "left_value": left_metric.value,
            "right_value": right_metric.value,
            "delta_right_minus_left": delta,
            "relative_delta": (None if left_metric.value == 0 else delta / abs(left_metric.value)),
            "higher_is_better": (direction if isinstance(direction, bool) else None),
            "outcome": _metric_outcome(delta, direction),
            "semantics": {
                "split": left_metric.split,
                "aggregation_type": left_metric.aggregation_type,
                "epoch": left_metric.epoch,
            },
        }
    return {
        "comparability": result,
        "hard_blockers": blockers,
        "caveats": caveats,
        "metric": metric_result,
        "configuration_changes": config_changes,
        "configuration_diff_truncated": config_diff_truncated,
        "notice": ("这是基于正式结构化记录的确定性比较，不构成因果结论或统计显著性证明。"),
    }


def repeated_experiment_statistics(
    records: list[ExperimentAnalysisRecord],
    *,
    metric_name: str | None = None,
) -> dict[str, Any]:
    """验证显式 Experiment 集合是严格重复组，并计算基础描述统计。"""

    if not 2 <= len(records) <= 20:
        raise ValueError("重复实验统计需要显式提供 2 至 20 个 Experiment")
    if len({item.experiment_id for item in records}) != len(records):
        raise ValueError("Experiment ID 不能重复")

    reference = records[0]
    incompatibilities: list[str] = []
    for item in records:
        if item.status != "COMPLETED":
            incompatibilities.append(f"{item.experiment_id}: 状态不是 COMPLETED")
        if not item.trace_complete:
            incompatibilities.append(f"{item.experiment_id}: 正式追溯链不完整")
        for label, left, right in (
            ("dataset", reference.dataset, item.dataset),
            ("protocol", reference.protocol, item.protocol),
            ("model", reference.model_name, item.model_name),
            (
                "Context 版本",
                (reference.context_id, reference.context_version),
                (item.context_id, item.context_version),
            ),
            (
                "Intent 版本",
                (reference.intent_id, reference.intent_version),
                (item.intent_id, item.intent_version),
            ),
            ("实验模式", reference.experiment_mode, item.experiment_mode),
            ("Git commit", reference.git_commit, item.git_commit),
            ("checkpoint", reference.checkpoint, item.checkpoint),
            ("运行命令", reference.command, item.command),
        ):
            if left != right:
                incompatibilities.append(f"{item.experiment_id}: {label} 不一致")
        if not strict_json_equal(
            _without_top_level_seed(reference.config),
            _without_top_level_seed(item.config),
        ):
            incompatibilities.append(f"{item.experiment_id}: 非 seed 配置不一致")

    metrics = [_select_metric(item, metric_name) for item in records]
    if any(item is None for item in metrics):
        incompatibilities.append(f"至少一个实验缺少指标 {metric_name or '主指标'}")
    concrete_metrics = [item for item in metrics if item is not None]
    if concrete_metrics and any(
        item.semantics != concrete_metrics[0].semantics for item in concrete_metrics[1:]
    ):
        incompatibilities.append("指标名称、split、聚合方式或 epoch 语义不一致")

    directions = {
        item.higher_is_better
        for item in records
        if item.primary_metric_name == (concrete_metrics[0].name if concrete_metrics else None)
        and item.higher_is_better is not None
    }
    if len(directions) > 1:
        incompatibilities.append("Context 对主指标方向的定义冲突")
    if incompatibilities:
        return {
            "accepted": False,
            "incompatibilities": sorted(set(incompatibilities)),
            "notice": "该集合不是严格重复实验组，未计算聚合统计。",
        }

    values = [item.value for item in concrete_metrics]
    direction = next(iter(directions)) if len(directions) == 1 else None
    best_index: int | None = None
    if direction is not None:
        target = max(values) if direction else min(values)
        best_index = values.index(target)
    seeds = [item.seed for item in records]
    return {
        "accepted": True,
        "metric": {
            "name": concrete_metrics[0].name,
            "split": concrete_metrics[0].split,
            "aggregation_type": concrete_metrics[0].aggregation_type,
            "epoch": concrete_metrics[0].epoch,
            "higher_is_better": direction,
        },
        "count": len(records),
        "seeds": seeds,
        "duplicate_seed_warning": len(set(seeds)) != len(seeds),
        "values": [
            {"experiment_id": str(item.experiment_id), "seed": item.seed, "value": value}
            for item, value in zip(records, values, strict=True)
        ],
        "statistics": {
            "mean": statistics.fmean(values),
            "sample_standard_deviation": (statistics.stdev(values) if len(values) >= 2 else None),
            "median": statistics.median(values),
            "minimum": min(values),
            "maximum": max(values),
            "range": max(values) - min(values),
        },
        "best_experiment_id": (
            str(records[best_index].experiment_id) if best_index is not None else None
        ),
        "notice": ("仅对用户明确选择的严格重复实验计算描述统计；不代表因果关系或统计显著性。"),
    }


def _select_metric(
    record: ExperimentAnalysisRecord, metric_name: str | None
) -> MetricRecord | None:
    candidates = (
        [item for item in record.metrics if item.name == metric_name]
        if metric_name is not None
        else [item for item in record.metrics if item.is_primary]
    )
    return candidates[0] if len(candidates) == 1 else None


def _resolve_direction(
    left: ExperimentAnalysisRecord,
    right: ExperimentAnalysisRecord,
    left_metric: MetricRecord | None,
    right_metric: MetricRecord | None,
) -> bool | Literal["CONFLICT"] | None:
    if left_metric is None or right_metric is None:
        return None
    directions: list[bool] = []
    if left.primary_metric_name == left_metric.name and left.higher_is_better is not None:
        directions.append(left.higher_is_better)
    if right.primary_metric_name == right_metric.name and right.higher_is_better is not None:
        directions.append(right.higher_is_better)
    if len(set(directions)) > 1:
        return "CONFLICT"
    return directions[0] if directions else None


def _metric_outcome(delta: float, direction: bool | Literal["CONFLICT"] | None) -> str:
    if delta == 0:
        return "EQUAL"
    if not isinstance(direction, bool):
        return "DIRECTION_UNKNOWN"
    improved = delta > 0 if direction else delta < 0
    return "RIGHT_BETTER" if improved else "LEFT_BETTER"


def _append_difference(target: list[str], left: Any, right: Any, label: str) -> None:
    if left != right:
        target.append(f"{label} 不一致")


def _without_top_level_seed(config: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in config.items() if key != "seed"}


def require_finite_metrics(record: ExperimentAnalysisRecord) -> None:
    """供应用层在使用数据库历史值前执行防御性校验。"""

    if any(not math.isfinite(item.value) for item in record.metrics):
        raise ValueError(f"Experiment {record.experiment_id} 包含非法非有限指标")
