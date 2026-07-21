"""训练前配置检查的确定性核心。

本模块故意不调用 LLM。任何 LOCKED 冲突、配置解析失败或变量越界都必须由程序规则
稳定地产生 BLOCKED，后续语义模型只能增加风险，不能覆盖这里的结论。
"""

import hashlib
import json
from collections.abc import Mapping
from typing import Any

import yaml

from experiment_guardian.domain.contracts import (
    ConfigurationDocument,
    ParameterChange,
    ParameterConstraint,
    PlanEvaluationInput,
    PlanEvaluationResult,
    RiskItem,
)
from experiment_guardian.domain.enums import (
    ApprovalStatus,
    CheckResult,
    ConfigFormat,
    EvidenceType,
    ProtectionLevel,
    RiskSeverity,
)

_MISSING = object()


class ConfigurationError(ValueError):
    """配置不是合法 YAML/JSON 对象时抛出的领域异常。"""


def parse_configuration(document: ConfigurationDocument) -> dict[str, Any]:
    """解析配置，并拒绝列表、标量等非对象根节点。"""

    try:
        if document.format is ConfigFormat.JSON:
            parsed = json.loads(document.content)
        else:
            parsed = yaml.safe_load(document.content)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"配置无法解析: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ConfigurationError("配置根节点必须是 YAML/JSON 对象")
    return parsed


def canonical_config_hash(config: Mapping[str, Any]) -> str:
    """计算与空白、键顺序无关的配置哈希。

    原始文件 SHA-256 仍作为 artifact 哈希保存；该哈希用于判断两个结构化配置在语义上
    是否相同。二者用途不同，不应互相替代。
    """

    try:
        payload = json.dumps(
            config,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"配置包含无法规范化的值: {exc}") from exc
    return hashlib.sha256(payload).hexdigest()


def _flatten(value: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """将嵌套对象展开为点分路径；P0 将数组作为一个不可再拆分的整体值。"""

    flattened: dict[str, Any] = {}
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(child, Mapping):
            flattened.update(_flatten(child, path))
        else:
            flattened[path] = child
    return flattened


def _is_allowed(value: Any, constraint: ParameterConstraint) -> bool:
    if constraint.allowed_values is not None and value not in constraint.allowed_values:
        return False
    if constraint.minimum is None and constraint.maximum is None:
        return True
    if not isinstance(value, int | float) or isinstance(value, bool):
        return False
    if constraint.minimum is not None and value < constraint.minimum:
        return False
    return constraint.maximum is None or value <= constraint.maximum


def evaluate_plan(data: PlanEvaluationInput) -> PlanEvaluationResult:
    """比较候选配置与正式 baseline，并返回唯一、确定性的检查结论。"""

    candidate = parse_configuration(data.candidate)
    config_hash = canonical_config_hash(candidate)
    baseline_flat = _flatten(data.baseline_config)
    candidate_flat = _flatten(candidate)
    constraints = {item.parameter_path: item for item in data.constraints}

    paths = sorted(set(baseline_flat) | set(candidate_flat))
    changes: list[ParameterChange] = []
    risks: list[RiskItem] = []
    blocked = False
    needs_approval = False

    for path in paths:
        previous = baseline_flat.get(path, _MISSING)
        current = candidate_flat.get(path, _MISSING)
        if previous == current:
            continue

        constraint = constraints.get(path)
        changes.append(
            ParameterChange(
                parameter_path=path,
                previous_value=None if previous is _MISSING else previous,
                current_value=None if current is _MISSING else current,
                protection_level=constraint.protection_level if constraint else None,
            )
        )

        if current is _MISSING:
            blocked = True
            risks.append(
                RiskItem(
                    code="MISSING_CONFIG_FIELD",
                    severity=RiskSeverity.CRITICAL,
                    field_path=path,
                    blocking=True,
                    message=f"候选配置删除了正式配置字段 {path}",
                    recommendation="恢复该字段后重新提交检查。",
                )
            )
            continue

        if constraint is None:
            if path not in data.allowed_variable_paths:
                needs_approval = True
                risks.append(
                    RiskItem(
                        code="OUT_OF_INTENT_CHANGE",
                        severity=RiskSeverity.HIGH,
                        field_path=path,
                        message=f"参数 {path} 不在当前实验意图允许变量中",
                        recommendation="由 Owner 审批，或更新实验意图后重新检查。",
                    )
                )
            continue

        if constraint.protection_level is ProtectionLevel.LOCKED:
            blocked = True
            risks.append(
                RiskItem(
                    code="LOCKED_PARAMETER_CHANGED",
                    severity=RiskSeverity.CRITICAL,
                    field_path=path,
                    blocking=True,
                    message=f"参数 {path} 属于 LOCKED，不能通过审批绕过",
                    recommendation="恢复正式值，或先由 Owner 发布新的上下文/约束版本。",
                )
            )
        elif constraint.protection_level is ProtectionLevel.APPROVAL_REQUIRED:
            needs_approval = True
            risks.append(
                RiskItem(
                    code="OWNER_APPROVAL_REQUIRED",
                    severity=RiskSeverity.HIGH,
                    field_path=path,
                    message=f"参数 {path} 的修改需要 Owner 审批",
                    recommendation="在计划审批页完成审批后创建 Run Manifest。",
                )
            )
        elif not _is_allowed(current, constraint):
            blocked = True
            risks.append(
                RiskItem(
                    code="EXPERIMENT_VARIABLE_OUT_OF_RANGE",
                    severity=RiskSeverity.HIGH,
                    field_path=path,
                    blocking=True,
                    message=f"实验变量 {path} 超出正式约束允许范围",
                    recommendation="将参数调整到允许范围后重新检查。",
                )
            )

    # 本地文件哈希属于声明信息；云端只能将它与自身解析出的规范化配置哈希比较。
    # 由于本地可能计算的是原始文件哈希，两类哈希语义不同，因此此处只记录声明缺失，
    # 不把二者直接判为冲突。artifact 上传阶段会验证原始字节 SHA-256。
    if data.local_attestation and not data.local_attestation.working_tree_clean:
        needs_approval = True
        risks.append(
            RiskItem(
                code="DIRTY_WORKING_TREE_ATTESTED",
                severity=RiskSeverity.HIGH,
                evidence_type=EvidenceType.LOCAL_ATTESTED,
                message="本地 Agent 声明 Git 工作区存在未提交修改",
                recommendation="提交代码后重新检查，或由 Owner 明确审批该运行计划。",
            )
        )

    if blocked:
        result = CheckResult.BLOCKED
        approval = ApprovalStatus.NOT_REQUIRED
    elif needs_approval:
        result = CheckResult.NEEDS_APPROVAL
        approval = ApprovalStatus.PENDING
    else:
        result = CheckResult.PASS
        approval = ApprovalStatus.NOT_REQUIRED

    return PlanEvaluationResult(
        check_result=result,
        approval_status=approval,
        config_hash=config_hash,
        parsed_config=candidate,
        changes=changes,
        risks=risks,
    )
