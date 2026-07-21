"""可选的真实 AWS S3 预签名上传兼容性验收。"""

import hashlib
import os
from uuid import uuid4

import boto3  # type: ignore[import-untyped]
import httpx
import pytest

from experiment_guardian.core.config import get_settings
from experiment_guardian.infrastructure.storage import S3ArtifactStorage

RUN_S3_INTEGRATION = os.getenv("RUN_S3_INTEGRATION") == "1"


@pytest.mark.skipif(
    not RUN_S3_INTEGRATION,
    reason="set RUN_S3_INTEGRATION=1 and configure AWS credentials/S3_BUCKET",
)
def test_presigned_put_persists_declared_checksum_and_content_type() -> None:
    settings = get_settings()
    assert settings.s3_bucket, "RUN_S3_INTEGRATION=1 时必须配置 S3_BUCKET"

    payload = b'{"integration":true}'
    digest = hashlib.sha256(payload).hexdigest()
    object_key = f"integration-tests/submission-prepare/{uuid4()}.json"
    storage = S3ArtifactStorage(bucket=settings.s3_bucket, region=settings.aws_region)
    client = boto3.client("s3", region_name=settings.aws_region)
    assert client.get_bucket_versioning(Bucket=settings.s3_bucket).get("Status") == ("Enabled"), (
        "R10 要求测试 Bucket 开启 Versioning"
    )

    signed = storage.create_upload_url(
        object_key=object_key,
        content_type="application/json",
        content_length=len(payload),
        sha256=digest,
        expires_in=300,
    )
    try:
        response = httpx.put(
            signed.upload_url,
            content=payload,
            headers=signed.required_headers,
            timeout=30,
        )
        response.raise_for_status()
        overwrite = httpx.put(
            signed.upload_url,
            content=payload,
            headers=signed.required_headers,
            timeout=30,
        )
        assert overwrite.status_code == 412
        metadata = storage.inspect_object(object_key=object_key)
        assert metadata is not None
        assert metadata.content_length == len(payload)
        assert metadata.content_type == "application/json"
        assert metadata.checksum_sha256 == digest
        assert metadata.version_id not in {None, "", "null"}
        assert metadata.evidence_source == f"s3://{settings.s3_bucket}/{object_key}"
        assert metadata.observed_at is not None
        assert (
            storage.read_object_version(
                object_key=object_key,
                version_id=str(metadata.version_id),
                max_bytes=1024,
            )
            == payload
        )
    finally:
        versions = client.list_object_versions(
            Bucket=settings.s3_bucket,
            Prefix=object_key,
        )
        objects = [
            {"Key": item["Key"], "VersionId": item["VersionId"]}
            for group in ("Versions", "DeleteMarkers")
            for item in versions.get(group, [])
            if item["Key"] == object_key
        ]
        if objects:
            client.delete_objects(
                Bucket=settings.s3_bucket,
                Delete={"Objects": objects, "Quiet": True},
            )
