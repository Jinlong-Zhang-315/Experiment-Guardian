"""submission_prepare 外部输入边界测试。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from experiment_guardian.domain.contracts import SubmissionPrepareCommand


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
        artifact("config.json", "CONFIG", "application/json", size_bytes=20 * 1024 * 1024),
        artifact("result.json", "RESULT", "application/json", size_bytes=20 * 1024 * 1024),
        *[
            artifact(f"log-{index}.txt", "LOG", "text/plain", size_bytes=20 * 1024 * 1024)
            for index in range(4)
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
