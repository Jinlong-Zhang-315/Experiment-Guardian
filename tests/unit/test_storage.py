"""S3 预签名适配器测试。"""

import base64
import io
from datetime import UTC, datetime

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from experiment_guardian.application.errors import InputValidationError, ServiceUnavailableError
from experiment_guardian.infrastructure.storage import (
    S3ArtifactStorage,
    S3CompatibleObjectStorage,
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
    with pytest.raises(ServiceUnavailableError, match="S3_BUCKET"):
        UnconfiguredArtifactStorage().inspect_object(object_key="key")


def test_s3_head_normalizes_cloud_metadata_and_checksum() -> None:
    class FakeClient:
        def head_object(self, **_: object) -> dict[str, object]:
            return {
                "ContentLength": 123,
                "ContentType": "application/json",
                "ChecksumSHA256": base64.b64encode(bytes.fromhex("ab" * 32)).decode(),
                "ChecksumType": "FULL_OBJECT",
                "ETag": '"etag-value"',
                "VersionId": "version-1",
                "LastModified": datetime(2026, 7, 22, tzinfo=UTC),
            }

    storage = S3ArtifactStorage(bucket="experiments", region="us-east-1")
    storage._client = FakeClient()
    result = storage.inspect_object(object_key="objects/a")

    assert result is not None
    assert result.content_length == 123
    assert result.checksum_sha256 == "ab" * 32
    assert result.etag == "etag-value"
    assert result.version_id == "version-1"
    assert result.evidence_source == "s3://experiments/objects/a"


def test_s3_head_distinguishes_missing_objects_and_service_errors() -> None:
    class MissingClient:
        def head_object(self, **_: object) -> dict[str, object]:
            raise ClientError(
                {
                    "Error": {"Code": "404", "Message": "Not Found"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadObject",
            )

    storage = S3ArtifactStorage(bucket="experiments", region="us-east-1")
    storage._client = MissingClient()
    assert storage.inspect_object(object_key="missing") is None

    class ForbiddenClient:
        def head_object(self, **_: object) -> dict[str, object]:
            raise ClientError(
                {
                    "Error": {"Code": "AccessDenied", "Message": "Denied"},
                    "ResponseMetadata": {"HTTPStatusCode": 403},
                },
                "HeadObject",
            )

    storage._client = ForbiddenClient()
    with pytest.raises(ServiceUnavailableError, match="元数据服务"):
        storage.inspect_object(object_key="forbidden")


def test_s3_head_keeps_missing_or_malformed_checksum_unverified() -> None:
    class FakeClient:
        def head_object(self, **_: object) -> dict[str, object]:
            return {
                "ContentLength": 10,
                "ContentType": "text/plain",
                "ChecksumSHA256": "not-base64!",
            }

    storage = S3ArtifactStorage(bucket="experiments", region="us-east-1")
    storage._client = FakeClient()
    result = storage.inspect_object(object_key="objects/log")

    assert result is not None
    assert result.checksum_sha256 is None


def test_s3_compatible_uses_metadata_checksum_fallback_and_real_version() -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def generate_presigned_url(self, operation: str, **kwargs: object) -> str:
            captured["operation"] = operation
            captured.update(kwargs)
            return "http://127.0.0.1:9000/signed"

        def head_object(self, **_: object) -> dict[str, object]:
            return {
                "ContentLength": 7,
                "ContentType": "application/json",
                "Metadata": {"sha256": "ab" * 32},
                "VersionId": "minio-version-1",
            }

    client = FakeClient()
    storage = S3CompatibleObjectStorage(
        bucket="experiments",
        region="us-east-1",
        endpoint_url="http://minio:9000",
        presign_endpoint_url="http://127.0.0.1:9000",
        access_key="key",
        secret_key="secret",
        client=client,
    )
    upload = storage.create_upload_url(
        object_key="objects/result.json",
        content_type="application/json",
        content_length=7,
        sha256="ab" * 32,
        expires_in=300,
    )
    params = captured["Params"]
    assert isinstance(params, dict)
    assert params["Metadata"] == {"sha256": "ab" * 32}
    assert upload.required_headers["x-amz-meta-sha256"] == "ab" * 32
    metadata = storage.inspect_object(object_key="objects/result.json")
    assert metadata is not None
    assert metadata.checksum_sha256 == "ab" * 32
    assert metadata.version_id == "minio-version-1"


def test_s3_reads_only_the_exact_bounded_version_and_closes_stream() -> None:
    captured: dict[str, object] = {}

    class Body(io.BytesIO):
        closed_by_storage = False

        def close(self) -> None:
            self.closed_by_storage = True
            super().close()

    body = Body(b"payload")

    class FakeClient:
        def get_object(self, **kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {
                "VersionId": "version-7",
                "ContentLength": 7,
                "Body": body,
            }

    storage = S3ArtifactStorage(bucket="experiments", region="us-east-1")
    storage._client = FakeClient()

    assert (
        storage.read_object_version(object_key="objects/a", version_id="version-7", max_bytes=10)
        == b"payload"
    )
    assert captured == {
        "Bucket": "experiments",
        "Key": "objects/a",
        "VersionId": "version-7",
    }
    assert body.closed_by_storage


def test_s3_version_read_distinguishes_missing_oversized_and_service_errors() -> None:
    class MissingClient:
        def get_object(self, **_: object) -> dict[str, object]:
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchVersion", "Message": "missing"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "GetObject",
            )

    storage = S3ArtifactStorage(bucket="experiments", region="us-east-1")
    storage._client = MissingClient()
    assert (
        storage.read_object_version(object_key="objects/a", version_id="gone", max_bytes=10) is None
    )

    class OversizedClient:
        def get_object(self, **_: object) -> dict[str, object]:
            return {
                "VersionId": "version-1",
                "ContentLength": 11,
                "Body": io.BytesIO(b"x" * 11),
            }

    storage._client = OversizedClient()
    with pytest.raises(InputValidationError, match="分析上限"):
        storage.read_object_version(object_key="objects/a", version_id="version-1", max_bytes=10)

    class ForbiddenClient:
        def get_object(self, **_: object) -> dict[str, object]:
            raise ClientError(
                {
                    "Error": {"Code": "AccessDenied", "Message": "denied"},
                    "ResponseMetadata": {"HTTPStatusCode": 403},
                },
                "GetObject",
            )

    storage._client = ForbiddenClient()
    with pytest.raises(ServiceUnavailableError, match="版本读取"):
        storage.read_object_version(object_key="objects/a", version_id="version-1", max_bytes=10)


def test_s3_compatible_maps_minio_invalid_version_to_missing() -> None:
    class MinioClient:
        def get_object(self, **_: object) -> dict[str, object]:
            raise ClientError(
                {
                    "Error": {
                        "Code": "InvalidArgument",
                        "Message": "Invalid version id specified",
                    },
                    "ResponseMetadata": {"HTTPStatusCode": 400},
                },
                "GetObject",
            )

    storage = S3CompatibleObjectStorage(
        bucket="experiments",
        region="us-east-1",
        endpoint_url="http://minio:9000",
        access_key="local-key",
        secret_key="local-secret",
        client=MinioClient(),
    )
    assert (
        storage.read_object_version(object_key="objects/a", version_id="invalid", max_bytes=10)
        is None
    )
