"""正式项目策略的人类可读派生表示。

结构化 Context、Intent 和 Constraint 始终是唯一事实源。本模块只把经过确认的结构化
快照渲染为稳定 Markdown，并为该快照计算哈希；渲染结果不能反向参与约束判断。
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from experiment_guardian.domain.contracts import (
    ExperimentIntentPayload,
    ExperimentIntentReference,
    ParameterConstraint,
    ProjectContextPayload,
    ProjectContextReference,
)
from experiment_guardian.domain.enums import ProtectionLevel

POLICY_NARRATIVE_FORMAT = "MARKDOWN"
POLICY_NARRATIVE_GENERATOR = "DETERMINISTIC_TEMPLATE"
POLICY_NARRATIVE_VERSION = "policy-narrative-v1"
MAX_POLICY_NARRATIVE_CHARACTERS = 50_000
POLICY_NARRATIVE_NOTICE = (
    "该说明由正式结构化 Context、Intent 和 Constraints 派生，仅用于阅读和辅助理解；"
    "执行、约束检查、审批和 Manifest 固化始终以同一版本的结构化数据为准。"
)


def build_policy_narrative_source(
    *,
    context: ProjectContextReference,
    intent: ExperimentIntentReference,
    context_payload: ProjectContextPayload,
    intent_payload: ExperimentIntentPayload,
    constraints: list[ParameterConstraint],
) -> dict[str, Any]:
    """构造不受生命周期状态变化影响的规范来源。

    Context/Intent 被替换时，ACTIVE/CLOSED 等生命周期状态会变化，但该版本当时的正式含义
    不应因此变成另一份文本。来源保留稳定 ID、版本、确认信息和全部语义字段，排除会随
    生命周期改变的 status/verification_status。
    """

    normalized_constraints = []
    for item in sorted(
        constraints,
        key=lambda value: (
            value.parameter_path,
            value.version or 0,
            str(value.constraint_id or ""),
        ),
    ):
        raw = item.model_dump(
            mode="json",
            exclude={"verification_status"},
        )
        raw["confirmed_at"] = _utc_iso(item.confirmed_at)
        normalized_constraints.append(raw)
    context_reference = context.model_dump(mode="json")
    context_reference["confirmed_at"] = _utc_iso(context.confirmed_at)
    context_reference["effective_at"] = _utc_iso(context.effective_at)
    return {
        "schema_version": 1,
        "context_reference": context_reference,
        "intent_reference": {
            "intent_id": str(intent.intent_id),
            "version": intent.version,
            "context_id": str(intent.context_id),
            "context_version": intent.context_version,
            "mode": intent.mode.value,
        },
        "context_payload": context_payload.model_dump(mode="json"),
        "intent_payload": intent_payload.model_dump(mode="json"),
        "constraints": normalized_constraints,
    }


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.isoformat().replace("+00:00", "Z")


def policy_narrative_source_hash(source: dict[str, Any]) -> str:
    encoded = json.dumps(
        source,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def render_policy_narrative(source: dict[str, Any]) -> str:
    """使用确定性模板生成 Markdown，不调用模型，也不推断新事实。"""

    context_ref = _object(source, "context_reference")
    intent_ref = _object(source, "intent_reference")
    context = _object(source, "context_payload")
    intent = _object(source, "intent_payload")
    constraints = source.get("constraints")
    if not isinstance(constraints, list) or any(not isinstance(item, dict) for item in constraints):
        raise ValueError("策略说明来源中的 constraints 无效")

    grouped: dict[str, list[dict[str, Any]]] = {
        ProtectionLevel.LOCKED.value: [],
        ProtectionLevel.APPROVAL_REQUIRED.value: [],
        ProtectionLevel.EXPERIMENT_VARIABLE.value: [],
    }
    for item in constraints:
        level = item.get("protection_level")
        if level not in grouped:
            raise ValueError("策略说明来源包含未知保护级别")
        grouped[level].append(item)

    lines = [
        f"# {_single_line(context.get('project_name'))}：正式实验策略",
        "",
        f"> {POLICY_NARRATIVE_NOTICE}",
        "",
        "## 版本与来源",
        f"- Context：`{_single_line(context_ref.get('context_id'))}`，"
        f"版本 `v{_single_line(context_ref.get('version'))}`",
        f"- Experiment Intent：`{_single_line(intent_ref.get('intent_id'))}`，"
        f"版本 `v{_single_line(intent_ref.get('version'))}`，"
        f"模式 `{_single_line(intent_ref.get('mode'))}`",
        f"- Context 变更原因：{_single_line(context_ref.get('change_reason'))}",
        f"- Context 生效时间：`{_single_line(context_ref.get('effective_at'))}`",
        "",
        "## 项目目标",
        _paragraph(context.get("goal")),
        "",
        "## 数据集与实验协议",
        f"- 数据集：{_code_value(context.get('dataset'))}",
        f"- 实验协议：{_code_value(context.get('protocol'))}",
        f"- 主指标：{_code_value(context.get('primary_metric'))}",
        f"- 默认 seeds：{_code_value(context.get('default_seeds'))}",
        "",
        "## 主线模型与基线",
        f"- 主线模型：{_code_value(context.get('mainline_model'))}",
        f"- 基线：{_code_value(context.get('baseline'))}",
        f"- 正式分支：{_code_value(context.get('active_branch'))}",
        "",
        "## 实验非目标",
        *_bullet_values(context.get("non_goals")),
        "",
        "## 当前实验意图",
        f"- 名称：{_single_line(intent.get('name'))}",
        f"- 目标：{_single_line(intent.get('objective'))}",
        f"- 假设：{_single_line(intent.get('hypothesis'))}",
        f"- 受控变量：{_inline_values(intent.get('controlled_variables'))}",
        f"- 允许实验的变量：{_inline_values(intent.get('allowed_variables'))}",
        f"- 预期输出：{_inline_values(intent.get('expected_outputs'))}",
        f"- 接受标准：{_inline_values(intent.get('acceptance_criteria'))}",
        "",
        "## 锁定参数",
        *_constraint_lines(grouped[ProtectionLevel.LOCKED.value], ProtectionLevel.LOCKED),
        "",
        "## 需要 Owner 审批的参数",
        *_constraint_lines(
            grouped[ProtectionLevel.APPROVAL_REQUIRED.value],
            ProtectionLevel.APPROVAL_REQUIRED,
        ),
        "",
        "## 允许实验的参数",
        "以下参数仅表示可在当前 Intent 约束范围内修改，不表示系统推荐修改。",
        *_constraint_lines(
            grouped[ProtectionLevel.EXPERIMENT_VARIABLE.value],
            ProtectionLevel.EXPERIMENT_VARIABLE,
        ),
        "",
        "## 关键决策",
        *_bullet_values(context.get("key_decisions")),
        "",
        "## 已弃用事项",
        *_bullet_values(context.get("deprecated_items")),
    ]
    content = "\n".join(lines).strip()
    if len(content) > MAX_POLICY_NARRATIVE_CHARACTERS:
        raise ValueError("策略说明超过 50000 字符上限")
    return content


def _object(value: dict[str, Any], key: str) -> dict[str, Any]:
    item = value.get(key)
    if not isinstance(item, dict):
        raise ValueError(f"策略说明来源中的 {key} 无效")
    return item


def _single_line(value: Any) -> str:
    if value is None:
        return "未设置"
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text or "未设置"


def _paragraph(value: Any) -> str:
    return _single_line(value)


def _canonical_value(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _code_value(value: Any) -> str:
    escaped = _canonical_value(value).replace("`", "\\`")
    return f"`{escaped}`"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("策略说明来源中的列表字段无效")
    return value


def _bullet_values(value: Any) -> list[str]:
    items = _as_list(value)
    return ["- 无"] if not items else [f"- {_single_line(item)}" for item in items]


def _inline_values(value: Any) -> str:
    items = _as_list(value)
    return "无" if not items else "、".join(_code_value(item) for item in items)


def _constraint_lines(
    constraints: list[dict[str, Any]],
    expected_level: ProtectionLevel,
) -> list[str]:
    if not constraints:
        return ["- 无"]
    lines: list[str] = []
    for item in constraints:
        path = _single_line(item.get("parameter_path"))
        expected = _code_value(item.get("expected_value"))
        reason = _single_line(item.get("reason"))
        allowed = {
            "allowed_values": item.get("allowed_values"),
            "minimum": item.get("minimum"),
            "maximum": item.get("maximum"),
        }
        range_parts = {key: value for key, value in allowed.items() if value is not None}
        range_text = f"；允许范围：{_code_value(range_parts)}" if range_parts else ""
        source_text = _single_line(item.get("source_type"))
        version = item.get("version")
        version_text = f"；约束版本：`v{version}`" if version is not None else ""
        if expected_level is ProtectionLevel.LOCKED:
            policy = "不得在实验配置中修改"
        elif expected_level is ProtectionLevel.APPROVAL_REQUIRED:
            policy = "修改前必须由 Owner 审批"
        else:
            policy = "允许在当前 Intent 约束范围内实验"
        lines.append(
            f"- `{path}` = {expected}：{policy}；原因：{reason}{range_text}"
            f"；来源：`{source_text}`{version_text}"
        )
    return lines
