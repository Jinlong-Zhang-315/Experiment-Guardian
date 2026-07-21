"""Amazon S3 Artifact Storage 适配器。"""

import base64
import binascii
from datetime import UTC, datetime
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from experiment_guardian.application.errors import ServiceUnavailableError
from experiment_guardian.domain.contracts import PresignedUpload, StoredObjectMetadata


class S3ArtifactStorage:
    """生成受约束的 PUT URL，并提供不下载内容的对象元数据观测。"""

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

    def inspect_object(self, *, object_key: str) -> StoredObjectMetadata | None:
        try:
            response = self._get_client().head_object(
                Bucket=self._bucket,
                Key=object_key,
                ChecksumMode="ENABLED",
            )
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code = str(error.get("Code", ""))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise ServiceUnavailableError("S3 对象元数据服务暂时不可用") from exc
        except BotoCoreError as exc:
            raise ServiceUnavailableError("S3 对象元数据服务暂时不可用") from exc

        content_length = response.get("ContentLength")
        if type(content_length) is not int or content_length < 0:
            raise ServiceUnavailableError("S3 返回了无效的对象大小")
        checksum = self._decode_sha256(response.get("ChecksumSHA256"))
        etag = response.get("ETag")
        return StoredObjectMetadata(
            content_length=content_length,
            content_type=response.get("ContentType"),
            checksum_sha256=checksum,
            checksum_type=response.get("ChecksumType"),
            etag=etag.strip('"') if isinstance(etag, str) else None,
            version_id=response.get("VersionId"),
            last_modified=response.get("LastModified"),
            observed_at=datetime.now(UTC),
            evidence_source=f"s3://{self._bucket}/{object_key}",
        )

    @staticmethod
    def _decode_sha256(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            return None
        return decoded.hex() if len(decoded) == 32 else None


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

    def inspect_object(self, *, object_key: str) -> StoredObjectMetadata | None:
        del object_key
        raise ServiceUnavailableError("S3_BUCKET 尚未配置")
