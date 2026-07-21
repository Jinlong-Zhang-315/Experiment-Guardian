"""Amazon S3 Artifact Storage 适配器。"""

import base64
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from experiment_guardian.application.errors import ServiceUnavailableError
from experiment_guardian.domain.contracts import PresignedUpload


class S3ArtifactStorage:
    """只负责生成受约束的 PUT URL；对象复核属于 submission_finalize。"""

    def __init__(self, *, bucket: str, region: str) -> None:
        self._bucket = bucket
        self._region = region
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = boto3.client("s3", region_name=self._region)
        return self._client

    def create_upload_url(
        self,
        *,
        object_key: str,
        content_type: str,
        content_length: int,
        sha256: str,
        expires_in: int,
    ) -> PresignedUpload:
        try:
            checksum = base64.b64encode(bytes.fromhex(sha256)).decode("ascii")
            url = self._get_client().generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": object_key,
                    "ContentType": content_type,
                    "ContentLength": content_length,
                    "ChecksumSHA256": checksum,
                    "IfNoneMatch": "*",
                },
                ExpiresIn=expires_in,
                HttpMethod="PUT",
            )
        except (BotoCoreError, ClientError, ValueError) as exc:
            raise ServiceUnavailableError("S3 预签名服务暂时不可用") from exc
        return PresignedUpload(
            upload_url=url,
            required_headers={
                "Content-Type": content_type,
                "Content-Length": str(content_length),
                "If-None-Match": "*",
                "x-amz-checksum-sha256": checksum,
            },
        )


class UnconfiguredArtifactStorage:
    """未配置 Bucket 时提供稳定错误，避免生成无法使用的占位 URL。"""

    def create_upload_url(
        self,
        *,
        object_key: str,
        content_type: str,
        content_length: int,
        sha256: str,
        expires_in: int,
    ) -> PresignedUpload:
        del object_key, content_type, content_length, sha256, expires_in
        raise ServiceUnavailableError("S3_BUCKET 尚未配置")
