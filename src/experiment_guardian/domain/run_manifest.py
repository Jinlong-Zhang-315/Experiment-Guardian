"""Run Manifest 的确定性快照提取和哈希规则。"""

import hashlib
import json
from typing import Any

from experiment_guardian.application.errors import ConflictError
from experiment_guardian.domain.enums import EvidenceApplicability

MANIFEST_SCHEMA_VERSION = 1


def canonical_json_hash(value: Any) -> str:
    """对 JSON 值生成跨进程稳定的 SHA-256，拒绝 NaN/Infinity。"""

    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ConflictError("Plan Check 快照包含无法稳定序列化的值") from exc
    return hashlib.sha256(payload).hexdigest()


def _required_text(value: Any, field_name: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ConflictError(f"Plan Check 快照缺少合法的 {field_name}")
    return value


def _evidence_value(local_attestation: dict[str, Any], path: tuple[str, ...]) -> Any | None:
    current: Any = local_attestation
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    if not isinstance(current, dict):
        return None
    if current.get("applicability", EvidenceApplicability.APPLICABLE.value) != (
        EvidenceApplicability.APPLICABLE.value
    ):
        return None
    return current.get("value")


def _environment_value(local_attestation: dict[str, Any], name: str) -> Any | None:
    value = _evidence_value(local_attestation, ("environment", name))
    if isinstance(value, str) and value.strip().lower() in {
        "n/a",
        "na",
        "not available",
        "unavailable",
    }:
        return None
    return value


def build_manifest_content(plan: Any, approval_record_id: Any | None) -> dict[str, Any]:
    """仅从不可变 Plan Check 快照生成参与 Manifest 哈希的内容。"""

    if (
        plan.input_document_hash == "UNAVAILABLE"
        or not isinstance(plan.configuration_document, dict)
        or not plan.configuration_document
        or not isinstance(plan.parsed_config, dict)
        or not isinstance(plan.context_snapshot, dict)
        or not plan.context_snapshot
        or not isinstance(plan.intent_snapshot, dict)
        or not plan.intent_snapshot
        or not isinstance(plan.local_attestation, dict)
        or not plan.local_attestation
    ):
        raise ConflictError("Plan Check 缺少完整历史快照，不能创建 Manifest")

    context_reference = plan.context_snapshot.get("reference")
    context_payload = plan.context_snapshot.get("payload")
    intent_reference = plan.intent_snapshot.get("reference")
    if (
        not isinstance(context_reference, dict)
        or not isinstance(context_payload, dict)
        or not isinstance(intent_reference, dict)
    ):
        raise ConflictError("Plan Check 的 Context 或 Intent 快照结构不完整")
    if (
        context_reference.get("context_id") != str(plan.context_id)
        or context_reference.get("version") != plan.context_version
        or intent_reference.get("intent_id") != str(plan.intent_id)
        or intent_reference.get("version") != plan.intent_version
        or intent_reference.get("context_id") != str(plan.context_id)
        or intent_reference.get("context_version") != plan.context_version
    ):
        raise ConflictError("Plan Check 快照版本与追溯字段不一致")

    parsed_config = plan.parsed_config
    dataset_value = parsed_config.get("dataset")
    if isinstance(dataset_value, dict) and "name" in dataset_value:
        dataset = dataset_value["name"]
    elif isinstance(dataset_value, str):
        dataset = dataset_value
    else:
        dataset = context_payload.get("dataset")
    dataset = _required_text(dataset, "dataset", max_length=200)

    if isinstance(dataset_value, dict) and "protocol" in dataset_value:
        protocol = dataset_value["protocol"]
    elif "protocol" in parsed_config:
        protocol = parsed_config["protocol"]
    else:
        protocol = context_payload.get("protocol")
    protocol = _required_text(protocol, "protocol", max_length=200)

    if "seed" in parsed_config:
        seed = parsed_config["seed"]
        if type(seed) is not int:
            raise ConflictError("配置中的 seed 必须是整数且不能是布尔值")
    else:
        default_seeds = context_payload.get("default_seeds")
        if not isinstance(default_seeds, list) or len(default_seeds) != 1:
            raise ConflictError("配置未提供 seed，Context 必须且只能提供一个 default seed")
        seed = default_seeds[0]
        if type(seed) is not int:
            raise ConflictError("Context 的唯一 default seed 必须是整数且不能是布尔值")

    local_attestation = plan.local_attestation
    git_branch = _required_text(
        _evidence_value(local_attestation, ("git_branch",)),
        "git_branch 本地声明",
        max_length=500,
    )
    checkpoint_value = _evidence_value(local_attestation, ("checkpoint_path",))
    checkpoint = (
        _required_text(checkpoint_value, "checkpoint_path 本地声明", max_length=1500)
        if checkpoint_value is not None
        else None
    )
    git_diff_value = _evidence_value(local_attestation, ("git_diff_sha256",))
    git_diff_hash = (
        _required_text(git_diff_value, "git_diff_sha256 本地声明", max_length=64)
        if git_diff_value is not None
        else None
    )
    environment = {
        key: _environment_value(local_attestation, key) for key in ("python", "cuda", "pytorch")
    }
    config_snapshot = {"document": plan.configuration_document, "parsed": parsed_config}
    evidence_snapshot = {
        "local_attestation": local_attestation,
        "plan_check": {
            "check_result": plan.check_result.value,
            "approval_status": plan.approval_status.value,
            "risk_level": plan.risk_level.value,
            "risks": plan.report.get("risks", []),
        },
    }
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "project_id": str(plan.project_id),
        "plan_check_id": str(plan.id),
        "approval_record_id": str(approval_record_id) if approval_record_id else None,
        "context_id": str(plan.context_id),
        "context_version": plan.context_version,
        "experiment_intent_id": str(plan.intent_id),
        "intent_version": plan.intent_version,
        "experiment_mode": plan.experiment_mode.value,
        "config_snapshot": config_snapshot,
        "config_hash": plan.input_config_hash,
        "config_document_hash": plan.input_document_hash,
        "git_branch": git_branch,
        "git_commit": plan.git_commit,
        "git_diff_hash": git_diff_hash,
        "dataset": dataset,
        "protocol": protocol,
        "seed": seed,
        "checkpoint": checkpoint,
        "command": plan.command,
        "environment": environment,
        "evidence_snapshot": evidence_snapshot,
    }
