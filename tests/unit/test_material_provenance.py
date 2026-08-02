"""Submission 材料来源的确定性汇总测试。"""

from experiment_guardian.application.material_provenance import (
    build_submission_material_provenance,
)
from experiment_guardian.domain.enums import ArtifactType, MaterialOrigin
from experiment_guardian.infrastructure.models import Artifact


def test_material_provenance_keeps_historical_fixture_and_derived_boundaries() -> None:
    artifacts = [
        Artifact(
            filename="config.yaml",
            artifact_type=ArtifactType.CONFIG,
            mime_type="application/yaml",
            size_bytes=10,
            s3_key="config",
            sha256="a" * 64,
            material_origin=MaterialOrigin.TEST_FIXTURE,
            provenance={
                "classification": "TEST_FIXTURE",
                "source_reference": "fixture/config.yaml",
                "note": "测试配置",
            },
            cloud_hash_verified=True,
        ),
        Artifact(
            filename="result.json",
            artifact_type=ArtifactType.RESULT,
            mime_type="application/json",
            size_bytes=10,
            s3_key="result",
            sha256="b" * 64,
            material_origin=MaterialOrigin.DERIVED_FROM_LOG,
            provenance={
                "classification": "DERIVED_FROM_LOG",
                "source_reference": "val_log.txt",
                "source_sha256": "c" * 64,
                "derivation_method": "parse best metric",
                "note": "日志派生结果",
            },
            cloud_hash_verified=True,
        ),
    ]
    evidence_snapshot = {
        "final_run_evidence": {
            "git_commit": {
                "value": "abc1234",
                "evidence_type": "LOCAL_ATTESTED",
                "source": "fixture repo",
                "collected_at": "2026-08-01T00:00:00Z",
                "collection_tool": "codex",
                "provenance": {
                    "classification": "TEST_FIXTURE",
                    "source_reference": "fixture commit",
                    "note": "不是原始训练 commit",
                },
            }
        }
    }

    result = build_submission_material_provenance(artifacts, evidence_snapshot)

    assert result.contains_non_current_material is True
    assert result.contains_unspecified_material is False
    assert result.historical_material_was_prevalidated is False
    assert {item.provenance.classification.value for item in result.facts} == {
        "TEST_FIXTURE",
        "DERIVED_FROM_LOG",
    }
    assert "不代表原始运行" in result.disclaimer


def test_legacy_material_is_not_silently_classified_as_current_run() -> None:
    artifact = Artifact(
        filename="config.yaml",
        artifact_type=ArtifactType.CONFIG,
        mime_type="application/yaml",
        size_bytes=10,
        s3_key="config",
        sha256="a" * 64,
        material_origin=MaterialOrigin.UNSPECIFIED,
        provenance={},
        cloud_hash_verified=True,
    )

    result = build_submission_material_provenance([artifact], {})

    assert result.contains_unspecified_material is True
    assert result.contains_non_current_material is False
    assert "未声明结构化来源" in result.disclaimer
