"""显式开启后使用真实 Docker MinIO 验证不可变 Artifact 语义。"""

import base64
import hashlib
import os
from uuid import uuid4

import httpx
import pytest

from experiment_guardian.infrastructure.storage import S3CompatibleObjectStorage


@pytest.mark.skipif(
    os.getenv("RUN_MINIO_INTEGRATION") != "1",
    reason="设置 RUN_MINIO_INTEGRATION=1 后才访问真实 MinIO",
)
def test_minio_presign_versioning_fixed_read_and_download() -> None:
    endpoint = os.getenv("MINIO_TEST_ENDPOINT", "http://127.0.0.1:9000")
    bucket = os.getenv("MINIO_TEST_BUCKET", "experiment-guardian-test")
    access_key = os.getenv("MINIO_TEST_ACCESS_KEY", "experiment-guardian")
    secret_key = os.getenv("MINIO_TEST_SECRET_KEY", "change-this-local-secret")
    storage = S3CompatibleObjectStorage(
        bucket=bucket,
        region="us-east-1",
        endpoint_url=endpoint,
        presign_endpoint_url=endpoint,
        access_key=access_key,
        secret_key=secret_key,
    )
    storage.ensure_bucket()
    object_key = f"integration/{uuid4()}/result.json"
    first = b'{"version":1}'
    first_hash = hashlib.sha256(first).hexdigest()
    signed = storage.create_upload_url(
        object_key=object_key,
        content_type="application/json",
        content_length=len(first),
        sha256=first_hash,
        expires_in=300,
    )
    http_client = httpx.Client(trust_env=False, timeout=30)
    try:
        response = http_client.put(
            signed.upload_url,
            content=first,
            headers=signed.required_headers,
        )
        response.raise_for_status()
        overwrite = http_client.put(
            signed.upload_url,
            content=first,
            headers=signed.required_headers,
        )
        assert overwrite.status_code == 412

        first_metadata = storage.inspect_object(object_key=object_key)
        assert first_metadata is not None
        assert first_metadata.checksum_sha256 == first_hash
        assert first_metadata.version_id not in {None, "", "null"}
        first_version = str(first_metadata.version_id)

        second = b'{"version":2}'
        second_hash = hashlib.sha256(second).hexdigest()
        storage._get_client().put_object(
            Bucket=bucket,
            Key=object_key,
            Body=second,
            ContentType="application/json",
            ChecksumSHA256=base64.b64encode(bytes.fromhex(second_hash)).decode("ascii"),
            Metadata={"sha256": second_hash},
        )
        latest = storage.inspect_object(object_key=object_key)
        assert latest is not None and latest.version_id != first_version
        assert storage.read_object_version(
            object_key=object_key,
            version_id=first_version,
            max_bytes=1024,
        ) == first
        assert storage.read_object_version(
            object_key=object_key,
            version_id="does-not-exist",
            max_bytes=1024,
        ) is None

        download = storage.create_download_url(
            object_key=object_key,
            version_id=first_version,
            filename="result.json",
            expires_in=300,
        )
        downloaded = http_client.get(download.download_url)
        downloaded.raise_for_status()
        assert downloaded.content == first
    finally:
        http_client.close()
        versions = storage._get_client().list_object_versions(Bucket=bucket, Prefix=object_key)
        objects = [
            {"Key": item["Key"], "VersionId": item["VersionId"]}
            for group in ("Versions", "DeleteMarkers")
            for item in versions.get(group, [])
            if item["Key"] == object_key
        ]
        if objects:
            storage._get_client().delete_objects(
                Bucket=bucket,
                Delete={"Objects": objects, "Quiet": True},
            )
