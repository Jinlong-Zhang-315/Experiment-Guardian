"""Amazon S3 Artifact Storage 适配器。"""

import base64
import binascii
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from experiment_guardian.application.errors import InputValidationError, ServiceUnavailableError
from experiment_guardian.domain.contracts import (
    PresignedDownload,
    PresignedUpload,
    StoredObjectMetadata,
)


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

    def read_object_version(
        self, *, object_key: str, version_id: str, max_bytes: int
    ) -> bytes | None:
        """只读取上传验证时固定的 VersionId，并在内存分配前限制大小。"""

        try:
            response = self._get_client().get_object(
                Bucket=self._bucket,
                Key=object_key,
                VersionId=version_id,
            )
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code = str(error.get("Code", ""))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404 or code in {"404", "NoSuchKey", "NoSuchVersion", "NotFound"}:
                return None
            raise ServiceUnavailableError("S3 对象版本读取服务暂时不可用") from exc
        except BotoCoreError as exc:
            raise ServiceUnavailableError("S3 对象版本读取服务暂时不可用") from exc

        returned_version = response.get("VersionId")
        if returned_version != version_id:
            raise ServiceUnavailableError("S3 返回的对象版本与请求 VersionId 不一致")
        content_length = response.get("ContentLength")
        if type(content_length) is not int or content_length < 0:
            raise ServiceUnavailableError("S3 返回了无效的对象大小")
        if content_length > max_bytes:
            raise InputValidationError(f"Artifact 内容超过分析上限 {max_bytes} 字节")

        body = response.get("Body")
        if body is None or not hasattr(body, "read"):
            raise ServiceUnavailableError("S3 返回了无效的对象内容流")
        try:
            payload = body.read(max_bytes + 1)
        except (BotoCoreError, OSError) as exc:
            raise ServiceUnavailableError("S3 对象版本读取服务暂时不可用") from exc
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
        if not isinstance(payload, bytes):
            raise ServiceUnavailableError("S3 返回了非字节对象内容")
        if len(payload) > max_bytes:
            raise InputValidationError(f"Artifact 内容超过分析上限 {max_bytes} 字节")
        if len(payload) != content_length:
            raise ServiceUnavailableError("S3 对象读取长度与元数据不一致")
        return payload

    def create_download_url(
        self,
        *,
        object_key: str,
        version_id: str,
        filename: str,
        expires_in: int,
    ) -> PresignedDownload:
        """下载始终固定 VersionId，避免验证后对象被替换造成证据漂移。"""

        if not version_id:
            raise InputValidationError("Artifact 尚未绑定不可变 S3 VersionId")
        safe_filename = filename.replace('"', "").replace("\r", "").replace("\n", "")
        try:
            url = self._get_client().generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": object_key,
                    "VersionId": version_id,
                    "ResponseContentDisposition": f'attachment; filename="{safe_filename}"',
                },
                ExpiresIn=expires_in,
                HttpMethod="GET",
            )
        except (BotoCoreError, ClientError, ValueError) as exc:
            raise ServiceUnavailableError("S3 下载预签名服务暂时不可用") from exc
        return PresignedDownload(
            download_url=url,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
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

    def read_object_version(
        self, *, object_key: str, version_id: str, max_bytes: int
    ) -> bytes | None:
        del object_key, version_id, max_bytes
        raise ServiceUnavailableError("S3_BUCKET 尚未配置")

    def create_download_url(
        self,
        *,
        object_key: str,
        version_id: str,
        filename: str,
        expires_in: int,
    ) -> PresignedDownload:
        del object_key, version_id, filename, expires_in
        raise ServiceUnavailableError("S3_BUCKET 尚未配置")
