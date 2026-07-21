"""R11 上传内容确定性解析测试。"""

import json

import pytest

from experiment_guardian.domain.submission_analysis import (
    SubmissionDocumentError,
    parse_submitted_configuration,
    parse_submitted_result,
)


def result_bytes(**updates: object) -> bytes:
    value: dict[str, object] = {
        "schema_version": 1,
        "status": "COMPLETED",
        "metrics": {"top1": 0.83},
        "failure_reason": None,
    }
    value.update(updates)
    return json.dumps(value, separators=(",", ":"), allow_nan=True).encode()


def test_result_document_accepts_fixed_completed_and_failed_shapes() -> None:
    completed = parse_submitted_result(result_bytes())
    failed = parse_submitted_result(
        result_bytes(status="FAILED", metrics={}, failure_reason="out of memory")
    )

    assert completed.metrics == {"top1": 0.83}
    assert failed.metrics == {}


@pytest.mark.parametrize(
    "payload",
    [
        result_bytes(schema_version="1"),
        result_bytes(metrics={}),
        result_bytes(metrics={"top1": True}),
        result_bytes(metrics={f"metric-{index}": index for index in range(51)}),
        result_bytes(extra="forbidden"),
        result_bytes(started_at="2026-07-22T12:00:00", completed_at=None),
        result_bytes(
            started_at="2026-07-22T13:00:00+08:00",
            completed_at="2026-07-22T12:00:00+08:00",
        ),
        result_bytes(metrics={"top1": float("nan")}),
        b'{"schema_version":1,"status":"COMPLETED","status":"FAILED","metrics":{}}',
    ],
)
def test_result_document_rejects_ambiguous_or_unstable_values(payload: bytes) -> None:
    with pytest.raises(SubmissionDocumentError):
        parse_submitted_result(payload)


def test_uploaded_config_reuses_strict_yaml_rules() -> None:
    parsed = parse_submitted_configuration(
        filename="config.yaml",
        payload=b"flag: yes\nmode: on\ndate: 2026-07-22\n",
    )
    assert parsed == {"flag": "yes", "mode": "on", "date": "2026-07-22"}

    with pytest.raises(SubmissionDocumentError, match="重复字段"):
        parse_submitted_configuration(filename="config.yaml", payload=b"seed: 1\nseed: 2\n")
