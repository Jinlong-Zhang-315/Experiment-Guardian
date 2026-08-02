"""submission_prepare 外部输入边界测试。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from experiment_guardian.domain.contracts import (
    ArtifactVerificationIssue,
    ArtifactVerificationReceipt,
    SubmissionFinalizeResult,
    SubmissionPrepareCommand,
)


def artifact(
    filename: str,
    artifact_type: str,
    mime_type: str,
    *,
    size_bytes: int = 100,
) -> dict[str, object]:
    return {
        "filename": filename,
        "artifact_type": artifact_type,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "sha256": "A" * 64,
    }


def payload() -> dict[str, object]:
    return {
        "project_id": uuid4(),
        "run_manifest_id": uuid4(),
        "idempotency_key": uuid4(),
        "source_agent": "experiment-guardian-local/0.1",
        "collected_at": datetime(2026, 7, 21, tzinfo=UTC),
        "experiment_status": "COMPLETED",
        "metrics_summary": {"top1": 0.83},
        "files": [
            artifact("config.yaml", "CONFIG", "application/yaml"),
            artifact("result.json", "RESULT", "application/json"),
        ],
    }


def test_valid_declaration_normalizes_hash_and_metrics() -> None:
    command = SubmissionPrepareCommand.model_validate(payload())

    assert command.files[0].sha256 == "a" * 64
    assert command.metrics_summary == {"top1": 0.83}


def test_historical_and_derived_material_provenance_is_explicit_and_linked() -> None:
    value = payload()
    log = artifact("val_log.txt", "LOG", "text/plain")
    log["sha256"] = "B" * 64
    log["provenance"] = {
        "classification": "HISTORICAL_SOURCE",
        "source_reference": "results/run-315/val_log.txt",
        "note": "原始训练结束后收集",
    }
    value["files"].append(log)  # type: ignore[union-attr]
    value["files"][0]["provenance"] = {  # type: ignore[index]
        "classification": "TEST_FIXTURE",
        "source_reference": "fixture/config.yaml",
        "note": "用于治理链路验证，不代表原始运行配置",
    }
    value["files"][1]["provenance"] = {  # type: ignore[index]
        "classification": "DERIVED_FROM_LOG",
        "source_reference": "val_log.txt",
        "source_sha256": "B" * 64,
        "derivation_method": "确定性提取 Best Test Acc",
        "note": "派生 JSON，不是原始输出",
    }

    command = SubmissionPrepareCommand.model_validate(value)

    assert command.files[0].provenance.classification.value == "TEST_FIXTURE"
    assert command.files[1].provenance.source_sha256 == "b" * 64


def test_non_current_provenance_requires_basis_and_derived_log_must_match() -> None:
    missing_basis = payload()
    missing_basis["files"][0]["provenance"] = {  # type: ignore[index]
        "classification": "TEST_FIXTURE"
    }
    with pytest.raises(ValidationError, match="source_reference"):
        SubmissionPrepareCommand.model_validate(missing_basis)

    wrong_source = payload()
    wrong_source["files"].append(  # type: ignore[union-attr]
        artifact("val_log.txt", "LOG", "text/plain")
    )
    wrong_source["files"][1]["provenance"] = {  # type: ignore[index]
        "classification": "DERIVED_FROM_LOG",
        "source_reference": "other_log.txt",
        "source_sha256": "A" * 64,
        "derivation_method": "parse metric",
        "note": "derived result",
    }
    with pytest.raises(ValidationError, match="同一 Submission"):
        SubmissionPrepareCommand.model_validate(wrong_source)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda value: value.update(collected_at="2026-07-21T12:00:00"), "时区"),
        (lambda value: value.update(metrics_summary={}), "至少一个指标"),
        (lambda value: value.update(metrics_summary={"bad": True}), "不能是布尔值"),
        (
            lambda value: value.update(files=[value["files"][0]]),
            "at least 2 items",
        ),
        (
            lambda value: value["files"][0].update(filename="../config.yaml"),
            "不能包含路径",
        ),
        (
            lambda value: value["files"][0].update(mime_type="text/plain"),
            "mime_type",
        ),
        (
            lambda value: value["files"][1].update(filename="result.txt"),
            "不允许使用扩展名",
        ),
        (
            lambda value: value["files"].append(
                artifact("CONFIG.YAML", "CONFIG", "application/yaml")
            ),
            "filename 不能重复",
        ),
    ],
)
def test_invalid_declarations_are_rejected(mutator: object, message: str) -> None:
    value = payload()
    mutator(value)  # type: ignore[operator]
    with pytest.raises(ValidationError, match=message):
        SubmissionPrepareCommand.model_validate(value)


def test_failed_run_may_omit_metrics_and_optional_files() -> None:
    value = payload()
    value["experiment_status"] = "FAILED"
    value["metrics_summary"] = {}
    command = SubmissionPrepareCommand.model_validate(value)

    assert command.metrics_summary == {}


def test_total_file_size_and_optional_file_counts_are_limited() -> None:
    value = payload()
    value["files"] = [
        artifact("config.json", "CONFIG", "application/json", size_bytes=1024 * 1024),
        artifact("result.json", "RESULT", "application/json", size_bytes=1024 * 1024),
        *[
            artifact(f"log-{index}.txt", "LOG", "text/plain", size_bytes=20 * 1024 * 1024)
            for index in range(5)
        ],
    ]
    with pytest.raises(ValidationError, match="总大小"):
        SubmissionPrepareCommand.model_validate(value)

    duplicate_note = payload()
    duplicate_note["files"].extend(  # type: ignore[union-attr]
        [
            artifact("first.md", "NOTE", "text/markdown"),
            artifact("second.md", "NOTE", "text/markdown"),
        ]
    )
    with pytest.raises(ValidationError, match="NOTE"):
        SubmissionPrepareCommand.model_validate(duplicate_note)

    oversized_config = payload()
    oversized_config["files"][0]["size_bytes"] = 1024 * 1024 + 1
    with pytest.raises(ValidationError, match="1 MiB"):
        SubmissionPrepareCommand.model_validate(oversized_config)


def test_finalize_result_enforces_pass_and_retryable_failure_states() -> None:
    submission_id, project_id, artifact_id = uuid4(), uuid4(), uuid4()
    receipt = ArtifactVerificationReceipt(
        artifact_id=artifact_id,
        filename="config.yaml",
        artifact_type="CONFIG",
        content_length=100,
        content_type="application/yaml",
        checksum_sha256="a" * 64,
        version_id="version-1",
        verified_at=datetime(2026, 7, 22, tzinfo=UTC),
        evidence_source="s3://bucket/key",
    )
    passed = SubmissionFinalizeResult(
        submission_id=submission_id,
        project_id=project_id,
        verification_result="PASS",
        status="UPLOAD_VERIFIED",
        retryable=False,
        artifact_verifications=[receipt],
        verified_at=datetime(2026, 7, 22, tzinfo=UTC),
    )
    issue = ArtifactVerificationIssue(
        artifact_id=artifact_id,
        filename="config.yaml",
        code="OBJECT_MISSING",
        field="object",
        expected="PRESENT",
        actual="MISSING",
        message="missing",
        evidence_source="object_key:key",
        observed_at=datetime(2026, 7, 22, tzinfo=UTC),
    )
    failed = SubmissionFinalizeResult(
        submission_id=submission_id,
        project_id=project_id,
        verification_result="FAILED",
        status="RECEIVED",
        retryable=True,
        issues=[issue],
        reupload_artifact_ids=[artifact_id],
    )

    assert passed.verified_at is not None
    assert failed.retryable
    with pytest.raises(ValidationError, match="PASS"):
        SubmissionFinalizeResult(
            submission_id=submission_id,
            project_id=project_id,
            verification_result="PASS",
            status="RECEIVED",
            retryable=False,
            artifact_verifications=[receipt],
            verified_at=datetime(2026, 7, 22, tzinfo=UTC),
        )
