"""已批准实验计划的确定性关键不变量核对。

本模块不访问数据库、不调用模型。自然语言条件只能由本地 Agent 提供带来源声明，
服务端会保留证据边界；模型输出本身不能把一个条件标记为已经满足。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from experiment_guardian.domain.contracts import (
    GIT_COMMIT_PATTERN,
    MAX_RUN_COMMAND_LENGTH,
    SHA256_PATTERN,
    ContractModel,
    FieldEvidence,
)
from experiment_guardian.domain.enums import EvidenceType
from experiment_guardian.domain.plan_check import flatten_configuration

InvariantStage = Literal["PLAN_APPROVAL", "PRE_RUN", "RESULT_SUBMISSION"]
InvariantOverallStatus = Literal[
    "CONSISTENT",
    "NON_CRITICAL_CHANGE",
    "NEEDS_EXPLANATION",
    "CRITICAL_DEVIATION",
]
InvariantOutcome = Literal[
    "MATCH",
    "NON_CRITICAL_CHANGE",
    "UNVERIFIED",
    "VIOLATED",
]
InvariantAttestationStatus = Literal["SATISFIED", "VIOLATED", "UNKNOWN"]

_STATUS_RANK: dict[str, int] = {
    "CONSISTENT": 0,
    "NON_CRITICAL_CHANGE": 1,
    "NEEDS_EXPLANATION": 2,
    "CRITICAL_DEVIATION": 3,
}
_MISSING = object()


class InvariantAttestation(ContractModel):
    """外部 Agent 对无法由云端独立验证的不变量作出的声明。"""

    invariant_id: str = Field(min_length=1, max_length=100)
    status: InvariantAttestationStatus
    explanation: str = Field(min_length=1, max_length=2000)
    evidence_references: list[str] = Field(default_factory=list, max_length=10)
    evidence_type: Literal[EvidenceType.LOCAL_ATTESTED] = EvidenceType.LOCAL_ATTESTED
    source: str = Field(min_length=1, max_length=500)
    collected_at: datetime
    collection_tool: str = Field(min_length=1, max_length=200)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "invariant_id": "constraint:protocol",
                    "status": "SATISFIED",
                    "explanation": "本地配置仍使用正式 protocol",
                    "evidence_references": ["config.yaml#dataset.protocol"],
                    "evidence_type": "LOCAL_ATTESTED",
                    "source": "local configuration",
                    "collected_at": "2026-07-30T12:00:00Z",
                    "collection_tool": "local-preflight/1.0",
                }
            ]
        }
    )

    @model_validator(mode="after")
    def validate_attestation(self) -> InvariantAttestation:
        if self.collected_at.tzinfo is None or self.collected_at.utcoffset() is None:
            raise ValueError("不变量声明的 collected_at 必须包含时区")
        if len(set(self.evidence_references)) != len(self.evidence_references):
            raise ValueError("不变量声明的 evidence_references 不能重复")
        return self


class FinalRunEvidence(ContractModel):
    """结果提交时对最终实际运行条件的本地声明。

    字段保持可空，以便错误提交仍能形成可审计的 Submission；对于绑定 v2 Manifest 的
    Submission，缺失核心字段会在云端分析阶段形成 blocking 风险。
    """

    git_commit: FieldEvidence | None = None
    run_command: FieldEvidence | None = None
    config_sha256: FieldEvidence | None = None
    checkpoint: FieldEvidence | None = None
    baseline_reference: FieldEvidence | None = None
    environment: FieldEvidence | None = None
    invariant_attestations: list[InvariantAttestation] = Field(default_factory=list, max_length=50)
    deviation_explanation: str | None = Field(default=None, max_length=4000)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "git_commit": {
                        "value": "a1b2c3d4",
                        "evidence_type": "LOCAL_ATTESTED",
                        "source": "git rev-parse HEAD",
                        "collected_at": "2026-07-30T12:00:00Z",
                        "collection_tool": "local-preflight/1.0",
                    },
                    "run_command": {
                        "value": "python train.py --config config.yaml",
                        "evidence_type": "LOCAL_ATTESTED",
                        "source": "executed command",
                        "collected_at": "2026-07-30T12:00:00Z",
                        "collection_tool": "local-preflight/1.0",
                    },
                    "config_sha256": {
                        "value": "a" * 64,
                        "evidence_type": "LOCAL_ATTESTED",
                        "source": "sha256sum config.yaml",
                        "collected_at": "2026-07-30T12:00:00Z",
                        "collection_tool": "local-preflight/1.0",
                    },
                    "invariant_attestations": [],
                    "deviation_explanation": None,
                }
            ]
        }
    )

    @model_validator(mode="after")
    def validate_final_evidence(self) -> FinalRunEvidence:
        fields = {
            "git_commit": self.git_commit,
            "run_command": self.run_command,
            "config_sha256": self.config_sha256,
            "checkpoint": self.checkpoint,
            "baseline_reference": self.baseline_reference,
            "environment": self.environment,
        }
        if any(
            item is not None and item.evidence_type is not EvidenceType.LOCAL_ATTESTED
            for item in fields.values()
        ):
            raise ValueError("最终运行证据必须标记为 LOCAL_ATTESTED")
        ids = [item.invariant_id for item in self.invariant_attestations]
        if len(ids) != len(set(ids)):
            raise ValueError("同一不变量不能提交多个最终声明")
        self._validate_text_field("git_commit", self.git_commit, 64, GIT_COMMIT_PATTERN)
        self._validate_text_field("run_command", self.run_command, MAX_RUN_COMMAND_LENGTH)
        self._validate_text_field("config_sha256", self.config_sha256, 64, SHA256_PATTERN)
        self._validate_text_field("checkpoint", self.checkpoint, 1500)
        self._validate_text_field("baseline_reference", self.baseline_reference, 500)
        return self

    @staticmethod
    def _validate_text_field(
        name: str,
        evidence: FieldEvidence | None,
        maximum: int,
        pattern: str | None = None,
    ) -> None:
        if evidence is None or evidence.applicability.value != "APPLICABLE":
            return
        value = evidence.value
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"{name} 必须是长度合法的非空字符串")
        if pattern is not None:
            import re

            if re.fullmatch(pattern, value) is None:
                raise ValueError(f"{name} 格式不合法")


class KeyInvariant(ContractModel):
    invariant_id: str = Field(min_length=1, max_length=100)
    source_type: Literal["FORMAL_CONSTRAINT", "CONFIRMED_CANDIDATE", "APPROVAL_CONDITION"]
    statement: str = Field(min_length=1, max_length=3000)
    representation: Literal["STRUCTURED_PARAMETER", "NATURAL_LANGUAGE"]
    parameter_path: str | None = Field(default=None, max_length=500)
    expected_value: Any = None
    verification_method: str = Field(min_length=1, max_length=2000)
    protection_level: str | None = None
    evidence_type: EvidenceType
    source_reference: str = Field(min_length=1, max_length=500)


class ApprovedPlanTrace(ContractModel):
    plan_id: UUID
    revision_id: UUID
    revision: int = Field(ge=1)
    decision_id: UUID
    decision_hash: str = Field(pattern=SHA256_PATTERN)
    review_hash: str = Field(pattern=SHA256_PATTERN)
    policy_hash: str = Field(pattern=SHA256_PATTERN)


class ApprovedInvariantSnapshot(ContractModel):
    schema_version: Literal[1] = 1
    trace: ApprovedPlanTrace
    invariants: list[KeyInvariant]
    plan_evidence: dict[str, Any]


class InvariantCheckItem(ContractModel):
    invariant_id: str
    source_type: str
    statement: str
    outcome: InvariantOutcome
    parameter_path: str | None = None
    expected_value: Any = None
    actual_value: Any = None
    message: str
    blocking: bool = False
    evidence_type: EvidenceType
    evidence_source: str
    collected_at: datetime | None = None
    collection_tool: str | None = None


class InvariantCheckReport(ContractModel):
    schema_version: Literal[1] = 1
    stage: InvariantStage
    overall_status: InvariantOverallStatus
    trace: ApprovedPlanTrace
    checks: list[InvariantCheckItem]
    deviation_explanation: str | None = None


def build_approved_invariant_snapshot(
    *,
    plan_id: UUID,
    revision_id: UUID,
    revision: int,
    decision_id: UUID,
    decision_hash: str,
    review_hash: str,
    policy_hash: str,
    approved_snapshot: dict[str, Any],
) -> ApprovedInvariantSnapshot:
    """把 R17b 决定快照规范化为可由三个阶段共同读取的不变量集合。"""

    invariants: list[KeyInvariant] = []
    formal = approved_snapshot.get("existing_formal_invariants", [])
    if not isinstance(formal, list):
        raise ValueError("计划决定中的正式约束快照结构无效")
    for raw in formal:
        if not isinstance(raw, dict):
            raise ValueError("计划决定中的正式约束条目无效")
        protection = raw.get("protection_level")
        if protection not in {"LOCKED", "APPROVAL_REQUIRED"}:
            continue
        path = raw.get("parameter_path")
        if not isinstance(path, str) or not path:
            raise ValueError("计划决定中的正式约束缺少 parameter_path")
        source_id = raw.get("constraint_id")
        invariant_id = (
            f"constraint:{source_id}"
            if source_id
            else "constraint:" + _stable_id({"path": path, "payload": raw})
        )
        reason = raw.get("reason")
        invariants.append(
            KeyInvariant(
                invariant_id=invariant_id,
                source_type="FORMAL_CONSTRAINT",
                statement=(
                    str(reason)
                    if isinstance(reason, str) and reason.strip()
                    else f"参数 {path} 必须遵循正式 {protection} 约束。"
                ),
                representation="STRUCTURED_PARAMETER",
                parameter_path=path,
                expected_value=raw.get("expected_value"),
                verification_method="由正式 Plan Check 对解析后的配置执行严格类型比较。",
                protection_level=str(protection),
                evidence_type=EvidenceType.CLOUD_VERIFIED,
                source_reference=f"protected_parameters:{source_id or path}",
            )
        )

    candidates = approved_snapshot.get("confirmed_candidate_invariants", [])
    if not isinstance(candidates, list):
        raise ValueError("计划决定中的确认候选不变量结构无效")
    for raw in candidates:
        if not isinstance(raw, dict) or not isinstance(raw.get("candidate_id"), str):
            raise ValueError("确认候选不变量缺少稳定 candidate_id")
        invariants.append(
            KeyInvariant(
                invariant_id=str(raw["candidate_id"]),
                source_type="CONFIRMED_CANDIDATE",
                statement=str(raw.get("statement") or "已确认的计划关键不变量"),
                representation=str(raw.get("representation")),
                parameter_path=raw.get("parameter_path"),
                expected_value=raw.get("expected_value"),
                verification_method=str(
                    raw.get("verification_method") or "按用户确认的计划条件核对。"
                ),
                evidence_type=EvidenceType.USER_PROVIDED,
                source_reference=f"experiment_plan_candidate:{raw['candidate_id']}",
            )
        )

    conditions = approved_snapshot.get("conditions", [])
    if not isinstance(conditions, list):
        raise ValueError("计划决定中的批准条件结构无效")
    for condition in conditions:
        if not isinstance(condition, str) or not condition.strip():
            raise ValueError("计划决定中的批准条件必须是非空文本")
        invariants.append(
            KeyInvariant(
                invariant_id="condition:" + _stable_id(condition),
                source_type="APPROVAL_CONDITION",
                statement=condition,
                representation="NATURAL_LANGUAGE",
                verification_method="运行前和结果提交时由本地 Agent 声明，云端保留证据边界。",
                evidence_type=EvidenceType.USER_PROVIDED,
                source_reference=f"experiment_plan_decision:{decision_id}",
            )
        )

    ids = [item.invariant_id for item in invariants]
    if len(ids) != len(set(ids)):
        raise ValueError("计划决定包含重复关键不变量 ID")
    plan = approved_snapshot.get("plan")
    if not isinstance(plan, dict):
        raise ValueError("计划决定缺少批准的 plan revision 快照")
    evidence = plan.get("evidence")
    hard_check = approved_snapshot.get("hard_check")
    if not isinstance(evidence, dict) or not isinstance(hard_check, dict):
        raise ValueError("计划决定缺少计划证据或硬检查快照")
    plan_evidence = {
        "git_commit": evidence.get("git_commit"),
        "run_command": evidence.get("run_command"),
        "baseline_reference": evidence.get("baseline_reference"),
        "configuration_hash": hard_check.get("configuration_hash"),
        "parsed_configuration": hard_check.get("parsed_configuration"),
    }
    return ApprovedInvariantSnapshot(
        trace=ApprovedPlanTrace(
            plan_id=plan_id,
            revision_id=revision_id,
            revision=revision,
            decision_id=decision_id,
            decision_hash=decision_hash,
            review_hash=review_hash,
            policy_hash=policy_hash,
        ),
        invariants=invariants,
        plan_evidence=plan_evidence,
    )


def _stable_id(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()[:24]


def strict_json_equal(left: Any, right: Any) -> bool:
    """按 JSON 类型和值比较，避免 Python 的 bool/int/float 等价规则。"""

    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            strict_json_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            strict_json_equal(l_item, r_item) for l_item, r_item in zip(left, right, strict=True)
        )
    return bool(left == right)


def evaluate_pre_run_invariants(
    *,
    snapshot: ApprovedInvariantSnapshot,
    parsed_config: dict[str, Any],
    git_commit: str,
    run_command: str,
    checkpoint: str | None,
    attestations: list[InvariantAttestation],
    deviation_explanation: str | None,
) -> InvariantCheckReport:
    """将实际运行前证据与批准快照比较，不替代正式 Plan Check。"""

    flattened = flatten_configuration(parsed_config)
    supplied = {item.invariant_id: item for item in attestations}
    known_ids = {item.invariant_id for item in snapshot.invariants}
    unknown = sorted(set(supplied) - known_ids)
    if unknown:
        raise ValueError("不变量声明引用了未知 ID: " + ", ".join(unknown))

    checks = [
        _evaluate_invariant(item, flattened=flattened, attestations=supplied)
        for item in snapshot.invariants
    ]
    evidence = snapshot.plan_evidence
    checks.extend(
        _compare_plan_evidence(
            evidence=evidence,
            parsed_config=parsed_config,
            git_commit=git_commit,
            run_command=run_command,
            checkpoint=checkpoint,
        )
    )
    return InvariantCheckReport(
        stage="PRE_RUN",
        overall_status=_overall_status(checks),
        trace=snapshot.trace,
        checks=checks,
        deviation_explanation=deviation_explanation,
    )


def evaluate_submission_invariants(
    *,
    snapshot: ApprovedInvariantSnapshot,
    manifest_report: InvariantCheckReport,
    parsed_config: dict[str, Any],
    config_document_sha256: str,
    manifest_git_commit: str,
    manifest_run_command: str,
    manifest_checkpoint: str | None,
    final_evidence: FinalRunEvidence | None,
) -> InvariantCheckReport:
    """对最终上传配置和本地运行声明执行第三阶段核对。"""

    attestations = final_evidence.invariant_attestations if final_evidence else []
    flattened = flatten_configuration(parsed_config)
    supplied = {item.invariant_id: item for item in attestations}
    checks = [
        _evaluate_invariant(item, flattened=flattened, attestations=supplied)
        for item in snapshot.invariants
    ]
    # 结果已经进入不可变 Submission。确认过的自然语言条件仍无法判断时不能由 Owner
    # 形式化点击绕过，必须创建包含完整证据的新 Submission。
    checks = [
        item.model_copy(
            update={
                "outcome": "VIOLATED",
                "blocking": True,
                "message": "结果提交无法证明该关键条件满足，必须补齐证据并新建 Submission。",
            }
        )
        if item.outcome == "UNVERIFIED"
        else item
        for item in checks
    ]
    checks.extend(
        [
            _final_evidence_check(
                "final.config_sha256",
                "最终配置文件 SHA-256 必须与上传的固定版本一致。",
                config_document_sha256,
                final_evidence.config_sha256 if final_evidence else None,
            ),
            _final_evidence_check(
                "final.git_commit",
                "最终 Git commit 必须与 Run Manifest 一致。",
                manifest_git_commit,
                final_evidence.git_commit if final_evidence else None,
            ),
            _final_evidence_check(
                "final.run_command",
                "最终运行命令必须与 Run Manifest 一致。",
                manifest_run_command,
                final_evidence.run_command if final_evidence else None,
            ),
            _final_evidence_check(
                "final.checkpoint",
                "最终 checkpoint 必须与 Run Manifest 一致。",
                manifest_checkpoint,
                final_evidence.checkpoint if final_evidence else None,
                required=manifest_checkpoint is not None,
            ),
        ]
    )
    # 运行前已经未通过的不变量不能在结果阶段被静默描述为一致。
    if manifest_report.overall_status == "CRITICAL_DEVIATION":
        checks.append(
            InvariantCheckItem(
                invariant_id="pre_run.checkpoint",
                source_type="PRE_RUN_CHECK",
                statement="运行前关键不变量核对必须通过。",
                outcome="VIOLATED",
                message="Run Manifest 保存的运行前核对包含关键偏离。",
                blocking=True,
                evidence_type=EvidenceType.CLOUD_VERIFIED,
                evidence_source="run_manifests.evidence_snapshot",
            )
        )
    return InvariantCheckReport(
        stage="RESULT_SUBMISSION",
        overall_status=_overall_status(checks),
        trace=snapshot.trace,
        checks=checks,
        deviation_explanation=(final_evidence.deviation_explanation if final_evidence else None),
    )


def _evaluate_invariant(
    invariant: KeyInvariant,
    *,
    flattened: dict[str, Any],
    attestations: dict[str, InvariantAttestation],
) -> InvariantCheckItem:
    if invariant.representation == "STRUCTURED_PARAMETER":
        actual = flattened.get(invariant.parameter_path or "", _MISSING)
        matched = actual is not _MISSING and strict_json_equal(actual, invariant.expected_value)
        if matched:
            outcome: InvariantOutcome = "MATCH"
            message = "实际配置与关键不变量一致。"
            blocking = False
        else:
            protection = invariant.protection_level
            blocking = protection == "LOCKED" or invariant.source_type == "CONFIRMED_CANDIDATE"
            outcome = "VIOLATED" if blocking else "UNVERIFIED"
            message = (
                "实际配置违反不可绕过的关键不变量。"
                if blocking
                else "实际配置改变了需要解释或审批的正式参数。"
            )
        return InvariantCheckItem(
            invariant_id=invariant.invariant_id,
            source_type=invariant.source_type,
            statement=invariant.statement,
            outcome=outcome,
            parameter_path=invariant.parameter_path,
            expected_value=invariant.expected_value,
            actual_value=None if actual is _MISSING else actual,
            message=message,
            blocking=blocking,
            evidence_type=EvidenceType.CLOUD_VERIFIED,
            evidence_source="parsed configuration submitted to Experiment Guardian",
            collection_tool="experiment-guardian-invariant-check-v1",
        )

    attestation = attestations.get(invariant.invariant_id)
    if attestation is None or attestation.status == "UNKNOWN":
        return InvariantCheckItem(
            invariant_id=invariant.invariant_id,
            source_type=invariant.source_type,
            statement=invariant.statement,
            outcome="UNVERIFIED",
            message="该自然语言关键条件缺少可判断的本地声明。",
            blocking=False,
            evidence_type=EvidenceType.LOCAL_ATTESTED,
            evidence_source=attestation.source if attestation else "not provided",
            collected_at=attestation.collected_at if attestation else None,
            collection_tool=attestation.collection_tool if attestation else None,
        )
    return InvariantCheckItem(
        invariant_id=invariant.invariant_id,
        source_type=invariant.source_type,
        statement=invariant.statement,
        outcome="MATCH" if attestation.status == "SATISFIED" else "VIOLATED",
        actual_value=attestation.status,
        message=(
            "本地 Agent 声明该条件已经满足；云端未独立验证。"
            if attestation.status == "SATISFIED"
            else "本地 Agent 明确声明该关键条件未满足。"
        ),
        blocking=attestation.status == "VIOLATED",
        evidence_type=EvidenceType.LOCAL_ATTESTED,
        evidence_source=attestation.source,
        collected_at=attestation.collected_at,
        collection_tool=attestation.collection_tool,
    )


def _compare_plan_evidence(
    *,
    evidence: dict[str, Any],
    parsed_config: dict[str, Any],
    git_commit: str,
    run_command: str,
    checkpoint: str | None,
) -> list[InvariantCheckItem]:
    checks: list[InvariantCheckItem] = []
    planned_config = evidence.get("parsed_configuration")
    if isinstance(planned_config, dict) and not strict_json_equal(planned_config, parsed_config):
        planned_flat = flatten_configuration(planned_config)
        actual_flat = flatten_configuration(parsed_config)
        changed_paths = sorted(
            path
            for path in set(planned_flat) | set(actual_flat)
            if path not in planned_flat
            or path not in actual_flat
            or not strict_json_equal(planned_flat[path], actual_flat[path])
        )
        checks.append(
            InvariantCheckItem(
                invariant_id="plan.configuration",
                source_type="APPROVED_PLAN_EVIDENCE",
                statement="实际配置与计划阶段配置证据的普通差异。",
                outcome="NON_CRITICAL_CHANGE",
                expected_value={"approved_configuration_hash": evidence.get("configuration_hash")},
                actual_value={
                    "changed_path_count": len(changed_paths),
                    "changed_paths": changed_paths[:100],
                    "truncated": len(changed_paths) > 100,
                },
                message="配置与计划证据不同；关键字段仍由正式规则和确认不变量单独判断。",
                evidence_type=EvidenceType.CLOUD_VERIFIED,
                evidence_source="approved experiment plan and parsed pre-run configuration",
                collection_tool="experiment-guardian-invariant-check-v1",
            )
        )
    for key, actual, statement in (
        ("git_commit", git_commit, "实际 Git commit 与批准计划证据不同。"),
        ("run_command", run_command, "实际运行命令与批准计划证据不同。"),
        ("baseline_reference", checkpoint, "实际 checkpoint 与批准计划 baseline 说明不同。"),
    ):
        expected = evidence.get(key)
        if expected is None or strict_json_equal(expected, actual):
            continue
        checks.append(
            InvariantCheckItem(
                invariant_id=f"plan.{key}",
                source_type="APPROVED_PLAN_EVIDENCE",
                statement=statement,
                outcome="UNVERIFIED",
                expected_value=expected,
                actual_value=actual,
                message="该差异无法仅由配置规则判断，需要在正式 Plan Check 中解释或审批。",
                evidence_type=EvidenceType.LOCAL_ATTESTED,
                evidence_source="experiment_check_plan request and approved plan evidence",
                collection_tool="experiment-guardian-invariant-check-v1",
            )
        )
    return checks


def _final_evidence_check(
    invariant_id: str,
    statement: str,
    expected: Any,
    evidence: FieldEvidence | None,
    *,
    required: bool = True,
) -> InvariantCheckItem:
    if evidence is None or evidence.applicability.value != "APPLICABLE":
        matched = not required and expected is None
        return InvariantCheckItem(
            invariant_id=invariant_id,
            source_type="MANIFEST",
            statement=statement,
            outcome="MATCH" if matched else "VIOLATED",
            expected_value=expected,
            message=(
                "该字段在当前 Manifest 中不适用。"
                if matched
                else "结果提交缺少核对该字段所需的最终本地证据。"
            ),
            blocking=not matched,
            evidence_type=EvidenceType.LOCAL_ATTESTED,
            evidence_source=evidence.source if evidence else "not provided",
            collected_at=evidence.collected_at if evidence else None,
            collection_tool=evidence.collection_tool if evidence else None,
        )
    matched = strict_json_equal(evidence.value, expected)
    return InvariantCheckItem(
        invariant_id=invariant_id,
        source_type="MANIFEST",
        statement=statement,
        outcome="MATCH" if matched else "VIOLATED",
        expected_value=expected,
        actual_value=evidence.value,
        message=(
            "最终本地声明与 Run Manifest 一致。"
            if matched
            else "最终本地声明与 Run Manifest 不一致。"
        ),
        blocking=not matched,
        evidence_type=EvidenceType.LOCAL_ATTESTED,
        evidence_source=evidence.source,
        collected_at=evidence.collected_at,
        collection_tool=evidence.collection_tool,
    )


def _overall_status(checks: list[InvariantCheckItem]) -> InvariantOverallStatus:
    statuses: list[InvariantOverallStatus] = ["CONSISTENT"]
    for item in checks:
        if item.outcome == "VIOLATED":
            statuses.append("CRITICAL_DEVIATION" if item.blocking else "NEEDS_EXPLANATION")
        elif item.outcome == "UNVERIFIED":
            statuses.append("NEEDS_EXPLANATION")
        elif item.outcome == "NON_CRITICAL_CHANGE":
            statuses.append("NON_CRITICAL_CHANGE")
    return max(statuses, key=_STATUS_RANK.__getitem__)
