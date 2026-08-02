"""Submission 材料来源的确定性汇总。

该模块只整理已持久化声明，不调用模型，也不把来源声明提升为云端验证事实。
"""

from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from experiment_guardian.domain.contracts import (
    MaterialProvenance,
    MaterialProvenanceFact,
    SubmissionMaterialProvenance,
)
from experiment_guardian.domain.enums import MaterialOrigin
from experiment_guardian.infrastructure.models import Artifact

NON_CURRENT_ORIGINS = {
    MaterialOrigin.HISTORICAL_SOURCE,
    MaterialOrigin.TEST_FIXTURE,
    MaterialOrigin.DERIVED_FROM_LOG,
}


class MaterialProvenanceError(RuntimeError):
    """已持久化来源声明损坏或与索引列不一致。"""


def build_submission_material_provenance(
    artifacts: Sequence[Artifact], evidence_snapshot: dict[str, Any]
) -> SubmissionMaterialProvenance:
    facts: list[MaterialProvenanceFact] = []
    try:
        for artifact in artifacts:
            provenance = MaterialProvenance.model_validate(artifact.provenance or {})
            if provenance.classification is not artifact.material_origin:
                raise MaterialProvenanceError(
                    f"Artifact {artifact.id} 的来源分类与详情不一致"
                )
            facts.append(
                MaterialProvenanceFact(
                    subject=f"artifact:{artifact.filename}",
                    artifact_type=artifact.artifact_type,
                    filename=artifact.filename,
                    sha256=artifact.sha256,
                    cloud_hash_verified=artifact.cloud_hash_verified,
                    provenance=provenance,
                )
            )

        final_evidence = evidence_snapshot.get("final_run_evidence")
        if isinstance(final_evidence, dict):
            for field_name in (
                "git_commit",
                "run_command",
                "config_sha256",
                "checkpoint",
                "baseline_reference",
                "environment",
            ):
                raw = final_evidence.get(field_name)
                if not isinstance(raw, dict):
                    continue
                provenance = MaterialProvenance.model_validate(raw.get("provenance") or {})
                facts.append(
                    MaterialProvenanceFact(
                        subject=f"final_run_evidence:{field_name}",
                        provenance=provenance,
                    )
                )
    except ValidationError as exc:
        raise MaterialProvenanceError("Submission 材料来源快照无效") from exc

    classifications = {item.provenance.classification for item in facts}
    has_non_current = bool(classifications & NON_CURRENT_ORIGINS)
    has_unspecified = MaterialOrigin.UNSPECIFIED in classifications
    if has_non_current:
        disclaimer = (
            "该 Submission 包含历史材料、测试夹具或从日志派生的结果；这些标签是来源声明，"
            "不代表原始运行在执行前已经过 Experiment Guardian 验证。"
        )
    elif has_unspecified:
        disclaimer = (
            "部分材料未声明结构化来源，系统只能验证已上传对象及其固定版本，不能推断其"
            "是否来自当前运行。"
        )
    else:
        disclaimer = (
            "材料声明为当前治理运行产物；系统仍只保证已记录证据的一致性、可追溯性和风险可见性。"
        )
    return SubmissionMaterialProvenance(
        facts=facts,
        contains_non_current_material=has_non_current,
        contains_unspecified_material=has_unspecified,
        historical_material_was_prevalidated=False if has_non_current else None,
        disclaimer=disclaimer,
    )
