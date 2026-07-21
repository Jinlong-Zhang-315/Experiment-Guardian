"""S3 预签名适配器测试。"""

import base64

import pytest

from experiment_guardian.application.errors import ServiceUnavailableError
from experiment_guardian.infrastructure.storage import (
    S3ArtifactStorage,
    UnconfiguredArtifactStorage,
)


def test_s3_presign_binds_content_type_and_sha256_checksum() -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def generate_presigned_url(self, operation: str, **kwargs: object) -> str:
            captured["operation"] = operation
            captured.update(kwargs)
            return "https://s3.example.invalid/signed"

    storage = S3ArtifactStorage(bucket="experiments", region="us-east-1")
    storage._client = FakeClient()
    result = storage.create_upload_url(
        object_key="projects/p/submissions/s/artifacts/a",
        content_type="application/json",
        content_length=123,
        sha256="ab" * 32,
        expires_in=900,
    )

    expected_checksum = base64.b64encode(bytes.fromhex("ab" * 32)).decode("ascii")
    assert captured["operation"] == "put_object"
    assert captured["Params"] == {
        "Bucket": "experiments",
        "Key": "projects/p/submissions/s/artifacts/a",
        "ContentType": "application/json",
        "ContentLength": 123,
        "ChecksumSHA256": expected_checksum,
        "IfNoneMatch": "*",
    }
    assert captured["ExpiresIn"] == 900
    assert captured["HttpMethod"] == "PUT"
    assert result.required_headers == {
        "Content-Type": "application/json",
        "Content-Length": "123",
        "If-None-Match": "*",
        "x-amz-checksum-sha256": expected_checksum,
    }


def test_unconfigured_storage_fails_without_placeholder_url() -> None:
    with pytest.raises(ServiceUnavailableError, match="S3_BUCKET"):
        UnconfiguredArtifactStorage().create_upload_url(
            object_key="key",
            content_type="application/json",
            content_length=10,
            sha256="a" * 64,
            expires_in=900,
        )
