"""可选的真实 AWS S3 预签名上传兼容性验收。"""

import base64
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
    expected_checksum = base64.b64encode(bytes.fromhex(digest)).decode("ascii")
    object_key = f"integration-tests/submission-prepare/{uuid4()}.json"
    storage = S3ArtifactStorage(bucket=settings.s3_bucket, region=settings.aws_region)
    client = boto3.client("s3", region_name=settings.aws_region)

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
        metadata = client.head_object(
            Bucket=settings.s3_bucket,
            Key=object_key,
            ChecksumMode="ENABLED",
        )
        assert metadata["ContentLength"] == len(payload)
        assert metadata["ContentType"] == "application/json"
        assert metadata["ChecksumSHA256"] == expected_checksum
    finally:
        client.delete_object(Bucket=settings.s3_bucket, Key=object_key)
