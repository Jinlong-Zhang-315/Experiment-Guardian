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
    FieldEvidence,
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
    ConstraintSource,
    EvidenceType,
    ProtectionLevel,
    RiskSeverity,
    VerificationStatus,
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


def _risk_from_local_evidence(
    *,
    evidence: FieldEvidence,
    code: str,
    severity: RiskSeverity,
    message: str,
    field_path: str,
    impact: str,
    recommendation: str,
) -> RiskItem:
    """保留本地证据边界，禁止风险文本把 Agent 声明升级为云端事实。"""

    return RiskItem(
        code=code,
        severity=severity,
        message=message,
        field_path=field_path,
        current_value=evidence.value,
        impact=impact,
        evidence_type=evidence.evidence_type,
        evidence_source=evidence.source,
        collected_at=evidence.collected_at,
        collection_tool=evidence.collection_tool,
        recommendation=recommendation,
    )


def evaluate_plan(data: PlanEvaluationInput) -> PlanEvaluationResult:
    """比较候选配置与正式 baseline，并返回唯一、确定性的检查结论。"""

    candidate = parse_configuration(data.candidate)
    config_hash = canonical_config_hash(candidate)
    baseline_flat = _flatten(data.baseline_config)
    candidate_flat = _flatten(candidate)
    confirmed_constraints: dict[str, ParameterConstraint] = {}
    pending_constraints: dict[str, ParameterConstraint] = {}
    duplicate_confirmed_paths: set[str] = set()
    for item in data.constraints:
        if item.verification_status is VerificationStatus.CONFIRMED:
            if item.parameter_path in confirmed_constraints:
                duplicate_confirmed_paths.add(item.parameter_path)
            confirmed_constraints[item.parameter_path] = item
        elif item.verification_status is VerificationStatus.PENDING:
            pending_constraints[item.parameter_path] = item
        # REJECTED 与 SUPERSEDED 只为审计保留，不能影响新的 Plan Check。

    paths = sorted(
        set(baseline_flat)
        | set(candidate_flat)
        | set(confirmed_constraints)
        | set(pending_constraints)
    )
    changes: list[ParameterChange] = []
    risks: list[RiskItem] = []
    blocked = bool(duplicate_confirmed_paths)
    needs_approval = False

    for path in sorted(duplicate_confirmed_paths):
        risks.append(
            RiskItem(
                code="CONFLICTING_CONFIRMED_CONSTRAINTS",
                severity=RiskSeverity.CRITICAL,
                message=f"参数 {path} 同时存在多个已确认约束",
                field_path=path,
                impact="云端无法唯一确定应执行的正式规则。",
                blocking=True,
                recommendation="由 Owner 修正约束版本后重新检查。",
            )
        )

    for path in paths:
        previous = baseline_flat.get(path, _MISSING)
        current = candidate_flat.get(path, _MISSING)
        if previous == current:
            continue

        constraint = confirmed_constraints.get(path)
        pending_constraint = pending_constraints.get(path)
        displayed_constraint = constraint or pending_constraint
        changes.append(
            ParameterChange(
                parameter_path=path,
                previous_value=None if previous is _MISSING else previous,
                current_value=None if current is _MISSING else current,
                protection_level=(
                    displayed_constraint.protection_level if displayed_constraint else None
                ),
                constraint_source=(
                    displayed_constraint.source_type if displayed_constraint else None
                ),
                constraint_status=(
                    displayed_constraint.verification_status if displayed_constraint else None
                ),
                inference_basis=(
                    displayed_constraint.inference_basis if displayed_constraint else None
                ),
                confidence=displayed_constraint.confidence if displayed_constraint else None,
            )
        )

        if current is _MISSING:
            blocked = True
            risks.append(
                RiskItem(
                    code="MISSING_CONFIG_FIELD",
                    severity=RiskSeverity.CRITICAL,
                    field_path=path,
                    previous_value=None if previous is _MISSING else previous,
                    current_value=None,
                    impact="运行配置缺少正式基线字段，无法建立完整配置追溯。",
                    blocking=True,
                    message=f"候选配置删除了正式配置字段 {path}",
                    recommendation="恢复该字段后重新提交检查。",
                )
            )
            continue

        if constraint is None and pending_constraint is not None:
            needs_approval = True
            origin = (
                "用户明确表达的候选约束"
                if pending_constraint.source_type is ConstraintSource.EXPLICIT
                else "模型推断的候选约束"
            )
            risks.append(
                RiskItem(
                    code="UNCONFIRMED_CONSTRAINT_CANDIDATE",
                    severity=RiskSeverity.MEDIUM,
                    field_path=path,
                    previous_value=None if previous is _MISSING else previous,
                    current_value=current,
                    expected_value=pending_constraint.expected_value,
                    message=f"参数 {path} 命中{origin}，但该约束尚未得到用户确认",
                    impact="该候选规则不能阻断运行，只能请求用户确认或审批。",
                    blocking=False,
                    evidence_type=EvidenceType.USER_PROVIDED,
                    evidence_source="intent_interpretation",
                    constraint_source=pending_constraint.source_type,
                    constraint_status=pending_constraint.verification_status,
                    inference_basis=pending_constraint.inference_basis,
                    confidence=pending_constraint.confidence,
                    recommendation="确认、拒绝或修正候选约束后重新执行检查。",
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
                        previous_value=None if previous is _MISSING else previous,
                        current_value=current,
                        impact="该变化可能超出本轮实验的结构化控制范围。",
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
                    previous_value=None if previous is _MISSING else previous,
                    current_value=current,
                    expected_value=constraint.expected_value,
                    impact="该变化违反用户已经确认的正式约束。",
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
                    previous_value=None if previous is _MISSING else previous,
                    current_value=current,
                    expected_value=constraint.expected_value,
                    impact="该变化需要 Owner 明确决策后才能生成 Manifest。",
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
                    previous_value=None if previous is _MISSING else previous,
                    current_value=current,
                    impact="变量值超出用户已经确认的实验范围。",
                    blocking=True,
                    message=f"实验变量 {path} 超出正式约束允许范围",
                    recommendation="将参数调整到允许范围后重新检查。",
                )
            )

    # 本地文件哈希属于声明信息。原始文件 SHA-256 与云端规范化配置哈希语义不同，不能
    # 直接比较；artifact 上传后，云端再对收到的原始字节执行 CLOUD_VERIFIED 校验。
    if data.local_attestation is None:
        needs_approval = True
        risks.append(
            RiskItem(
                code="LOCAL_ATTESTATION_MISSING",
                severity=RiskSeverity.HIGH,
                evidence_type=None,
                message="本次计划没有提供本地环境与 Git 状态声明",
                impact="云端无法观察真实工作区、输出目录、checkpoint 和运行环境。",
                recommendation="补充逐字段本地声明后重新检查。",
            )
        )
    else:
        local = data.local_attestation
        required_evidence = {
            "working_tree_clean": local.working_tree_clean,
            "git_branch": local.git_branch,
            "git_commit": local.git_commit,
            "run_command": local.run_command,
            "output_directory_exists": local.output_directory_exists,
            "checkpoint_exists": local.checkpoint_exists,
            "checkpoint_path": local.checkpoint_path,
            "config_sha256": local.config_sha256,
            "environment.python": local.environment.python,
            "environment.cuda": local.environment.cuda,
            "environment.pytorch": local.environment.pytorch,
        }
        for field_path, evidence in required_evidence.items():
            if evidence is None:
                needs_approval = True
                risks.append(
                    RiskItem(
                        code="LOCAL_ATTESTATION_FIELD_MISSING",
                        severity=RiskSeverity.MEDIUM,
                        evidence_type=None,
                        field_path=field_path,
                        message=f"缺少本地声明字段 {field_path}",
                        impact="云端无法判断该本地条件是否偏离 Manifest。",
                        recommendation="由本地 Agent 补充采集，或由用户明确确认缺失信息。",
                    )
                )

        consistency_checks = (
            ("git_commit", data.git_commit, local.git_commit),
            ("run_command", data.run_command, local.run_command),
            ("checkpoint_path", data.checkpoint, local.checkpoint_path),
        )
        for field_path, expected, evidence in consistency_checks:
            if expected is not None and evidence is not None and evidence.value != expected:
                needs_approval = True
                risks.append(
                    _risk_from_local_evidence(
                        evidence=evidence,
                        code="LOCAL_ATTESTATION_CONFLICT",
                        severity=RiskSeverity.HIGH,
                        message=f"本地 Agent 对 {field_path} 的声明与检查请求不一致",
                        field_path=field_path,
                        impact="Manifest 可能记录与真实计划不同的运行条件。",
                        recommendation="统一请求值和本地声明后重新检查。",
                    )
                )

        working_tree = local.working_tree_clean
        if working_tree is not None and working_tree.value is not True:
            needs_approval = True
            risks.append(
                _risk_from_local_evidence(
                    evidence=working_tree,
                    code="DIRTY_OR_UNKNOWN_WORKING_TREE_ATTESTED",
                    severity=RiskSeverity.HIGH,
                    message="本地 Agent 未声明 Git 工作区处于干净状态",
                    field_path="working_tree_clean",
                    impact="实际训练代码可能无法仅由 Git commit 复现。",
                    recommendation="提交代码后重新检查，或由 Owner 明确审批该运行计划。",
                )
            )
            if local.git_diff_sha256 is None:
                risks.append(
                    RiskItem(
                        code="GIT_DIFF_ATTESTATION_MISSING",
                        severity=RiskSeverity.HIGH,
                        evidence_type=None,
                        field_path="git_diff_sha256",
                        message="工作区并非明确干净，但缺少 Git diff 哈希声明",
                        impact="无法将未提交变化与本次运行建立稳定关联。",
                        recommendation="提供 Git diff 哈希或改用干净 commit。",
                    )
                )

        output_directory = local.output_directory_exists
        if output_directory is not None and output_directory.value is not False:
            needs_approval = True
            risks.append(
                _risk_from_local_evidence(
                    evidence=output_directory,
                    code="OUTPUT_DIRECTORY_CONFLICT_ATTESTED",
                    severity=RiskSeverity.HIGH,
                    message="本地 Agent 未声明输出目录为全新目录",
                    field_path="output_directory_exists",
                    impact="训练可能覆盖或混合已有实验产物。",
                    recommendation="选择新的输出目录，或由用户确认覆盖风险。",
                )
            )

        checkpoint = local.checkpoint_exists
        if checkpoint is not None and checkpoint.value is not True:
            needs_approval = True
            risks.append(
                _risk_from_local_evidence(
                    evidence=checkpoint,
                    code="CHECKPOINT_NOT_FOUND_ATTESTED",
                    severity=RiskSeverity.HIGH,
                    message="本地 Agent 未声明计划使用的 checkpoint 存在",
                    field_path="checkpoint_exists",
                    impact="运行可能失败或加载与 Manifest 不同的初始化权重。",
                    recommendation="修正 checkpoint 后重新检查。",
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
