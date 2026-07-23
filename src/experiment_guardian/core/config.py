"""应用配置。

配置只从环境变量和本地 ``.env`` 读取。代码中不提供真实凭据默认值，避免开发阶段
将数据库密码、AWS Key 或 MCP Token 意外提交到仓库。
"""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Experiment Guardian 进程级配置。

    API 与 MCP Server 共用该对象。后续拆分为多个部署单元时，可以继续使用相同环境
    变量，只覆盖各进程真正需要的部分。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Experiment Guardian"
    app_env: Literal["local", "development", "test", "staging", "production"] = "local"
    deployment_mode: Literal["cloud", "local"] = "cloud"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    api_prefix: str = "/api/v1"
    api_host: str = "127.0.0.1"

    # 数据库 URL 使用 SQLAlchemy 的 psycopg 方言。测试可以覆盖为独立数据库。
    database_url: str = (
        "cockroachdb+psycopg://root@127.0.0.1:26257/experiment_guardian?sslmode=disable"
    )
    database_echo: bool = False

    mcp_transport: Literal["stdio", "streamable-http"] = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8001, ge=1, le=65535)
    mcp_access_token: SecretStr | None = None
    # 远程 MCP 是 OAuth 受保护资源。客户端必须预先在 Cognito 和本表中注册；R14 不支持
    # 动态客户端注册或任意第三方客户端零配置接入。
    mcp_public_url: str = ""
    mcp_oauth_resource_identifier: str = ""
    mcp_oauth_scope_prefix: str = "experiment-guardian"

    web_auth_mode: Literal["cognito", "local_owner"] = "cognito"
    local_owner_email: str = ""
    # Cognito 和 local_owner 最终都创建相同的可撤销服务端 Session。浏览器不接触
    # Cognito access/id/refresh token。
    web_public_base_url: str = "http://127.0.0.1:8000"
    web_frontend_url: str = "http://127.0.0.1:5173"
    web_session_cookie_name: str = "eg_session"
    web_csrf_cookie_name: str = "eg_csrf"
    web_session_idle_seconds: int = Field(default=8 * 60 * 60, ge=300)
    web_session_absolute_seconds: int = Field(default=7 * 24 * 60 * 60, ge=3600)
    web_recent_auth_seconds: int = Field(default=10 * 60, ge=60, le=3600)
    oidc_transaction_ttl_seconds: int = Field(default=5 * 60, ge=60, le=900)
    cognito_issuer_url: str = ""
    cognito_domain: str = ""
    cognito_web_client_id: str = ""
    cognito_web_client_secret: SecretStr | None = None
    # 生产环境必须显式注入两个不同的密钥。开发默认值只用于启动和本地测试。
    web_oidc_state_key: SecretStr = SecretStr("local-only-change-oidc-state-key")
    web_csrf_secret: SecretStr = SecretStr("local-only-change-csrf-secret")

    manifest_hash_algorithm: Literal["sha256"] = "sha256"

    object_storage_backend: Literal["aws_s3", "s3_compatible"] = "aws_s3"
    aws_region: str = "us-east-1"
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_presign_endpoint_url: str = ""
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    s3_region: str = "us-east-1"
    s3_force_path_style: bool = False
    s3_presign_expires_seconds: int = Field(default=900, ge=60, le=3600)

    queue_backend: Literal["sqs", "database"] = "sqs"
    sqs_submission_queue_url: str = ""
    sqs_wait_time_seconds: int = Field(default=20, ge=0, le=20)
    sqs_visibility_timeout_seconds: int = Field(default=120, ge=30, le=43200)
    worker_lease_seconds: int = Field(default=120, ge=30, le=3600)
    worker_max_attempts: int = Field(default=5, ge=1, le=20)
    database_queue_poll_interval_seconds: float = Field(default=1.0, ge=0.1, le=60)
    database_queue_batch_size: int = Field(default=10, ge=1, le=100)

    llm_provider: Literal["bedrock", "bailian"] = "bedrock"
    bedrock_summary_model_id: str = ""
    bedrock_embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    embedding_dimension: int = Field(default=1024, ge=1)
    bedrock_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    bedrock_read_timeout_seconds: int = Field(default=60, ge=1, le=300)
    bailian_api_key: SecretStr | None = None
    bailian_base_url: str = ""
    bailian_summary_model: str = ""
    bailian_embedding_model: str = ""
    bailian_embedding_dimension: int = Field(default=1024, ge=1)
    bailian_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    bailian_read_timeout_seconds: int = Field(default=60, ge=1, le=300)

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        """允许环境变量使用 ``info`` 等小写形式，内部始终保留标准大写值。"""

        return value.upper() if isinstance(value, str) else value

    @field_validator("embedding_dimension")
    @classmethod
    def require_r12b_embedding_dimension(cls, value: int) -> int:
        if value != 1024:
            raise ValueError("R12b 的 EMBEDDING_DIMENSION 必须为 1024")
        return value

    @field_validator("bailian_embedding_dimension")
    @classmethod
    def require_bailian_embedding_dimension(cls, value: int) -> int:
        if value != 1024:
            raise ValueError("BAILIAN_EMBEDDING_DIMENSION 必须为 1024")
        return value

    @model_validator(mode="after")
    def validate_deployment_backends(self) -> "Settings":
        if self.deployment_mode == "local":
            if self.app_env not in {"development", "test"}:
                raise ValueError("DEPLOYMENT_MODE=local 只能用于 development 或 test 环境")
            expected = {
                "WEB_AUTH_MODE": (self.web_auth_mode, "local_owner"),
                "OBJECT_STORAGE_BACKEND": (self.object_storage_backend, "s3_compatible"),
                "QUEUE_BACKEND": (self.queue_backend, "database"),
                "LLM_PROVIDER": (self.llm_provider, "bailian"),
            }
            invalid = [name for name, values in expected.items() if values[0] != values[1]]
            if invalid:
                raise ValueError("本地部署后端配置不一致: " + ", ".join(invalid))
            if not self.database_url.startswith("cockroachdb+psycopg://"):
                raise ValueError("本地 DATABASE_URL 必须使用 cockroachdb+psycopg:// dialect")
            local_urls = {
                "WEB_PUBLIC_BASE_URL": self.web_public_base_url,
                "WEB_FRONTEND_URL": self.web_frontend_url,
            }
            invalid_urls = [
                name
                for name, value in local_urls.items()
                if urlparse(value).scheme not in {"http", "https"}
                or urlparse(value).hostname not in {"127.0.0.1", "localhost"}
            ]
            if invalid_urls:
                raise ValueError(
                    "本地 Web URL 必须使用 127.0.0.1 或 localhost: "
                    + ", ".join(invalid_urls)
                )
            required_local = {
                "LOCAL_OWNER_EMAIL": self.local_owner_email,
                "S3_ENDPOINT_URL": self.s3_endpoint_url,
                "S3_PRESIGN_ENDPOINT_URL": self.s3_presign_endpoint_url,
                "S3_BUCKET": self.s3_bucket,
                "S3_ACCESS_KEY": (
                    self.s3_access_key.get_secret_value() if self.s3_access_key else ""
                ),
                "S3_SECRET_KEY": (
                    self.s3_secret_key.get_secret_value() if self.s3_secret_key else ""
                ),
                "BAILIAN_API_KEY": (
                    self.bailian_api_key.get_secret_value() if self.bailian_api_key else ""
                ),
                "BAILIAN_BASE_URL": self.bailian_base_url,
                "BAILIAN_SUMMARY_MODEL": self.bailian_summary_model,
                "BAILIAN_EMBEDDING_MODEL": self.bailian_embedding_model,
            }
            missing = [name for name, value in required_local.items() if not value.strip()]
            if missing:
                raise ValueError("本地部署缺少配置: " + ", ".join(missing))
            return self

        expected_cloud = {
            "WEB_AUTH_MODE": (self.web_auth_mode, "cognito"),
            "OBJECT_STORAGE_BACKEND": (self.object_storage_backend, "aws_s3"),
            "QUEUE_BACKEND": (self.queue_backend, "sqs"),
            "LLM_PROVIDER": (self.llm_provider, "bedrock"),
        }
        invalid_cloud = [
            name for name, values in expected_cloud.items() if values[0] != values[1]
        ]
        if invalid_cloud:
            raise ValueError("云端部署后端配置不一致: " + ", ".join(invalid_cloud))
        if self.app_env != "production":
            return self
        if not self.database_url.startswith("cockroachdb+psycopg://"):
            raise ValueError("生产 DATABASE_URL 必须使用 cockroachdb+psycopg:// dialect")
        required_cloud = {
            "COGNITO_ISSUER_URL": self.cognito_issuer_url,
            "COGNITO_DOMAIN": self.cognito_domain,
            "COGNITO_WEB_CLIENT_ID": self.cognito_web_client_id,
            "COGNITO_WEB_CLIENT_SECRET": (
                self.cognito_web_client_secret.get_secret_value()
                if self.cognito_web_client_secret
                else ""
            ),
            "S3_BUCKET": self.s3_bucket,
            "SQS_SUBMISSION_QUEUE_URL": self.sqs_submission_queue_url,
            "BEDROCK_SUMMARY_MODEL_ID": self.bedrock_summary_model_id,
            "BEDROCK_EMBEDDING_MODEL_ID": self.bedrock_embedding_model_id,
        }
        missing = [name for name, value in required_cloud.items() if not value]
        if missing:
            raise ValueError("生产环境缺少托管身份配置: " + ", ".join(missing))
        state_key = self.web_oidc_state_key.get_secret_value()
        csrf_secret = self.web_csrf_secret.get_secret_value()
        if len(state_key) < 32 or len(csrf_secret) < 32 or state_key == csrf_secret:
            raise ValueError("生产环境 OIDC state 和 CSRF 必须使用两个不同的高熵密钥")
        if not self.web_public_base_url.startswith("https://"):
            raise ValueError("生产环境 WEB_PUBLIC_BASE_URL 必须使用 HTTPS")
        if not self.web_frontend_url.startswith("https://"):
            raise ValueError("生产环境 WEB_FRONTEND_URL 必须使用 HTTPS")
        if not self.cognito_issuer_url.startswith("https://") or not self.cognito_domain.startswith(
            "https://"
        ):
            raise ValueError("生产环境 Cognito issuer 和 managed-login domain 必须使用 HTTPS")
        if self.mcp_transport == "streamable-http":
            if not self.mcp_public_url.startswith("https://"):
                raise ValueError("生产环境远程 MCP_PUBLIC_URL 必须使用 HTTPS")
            if (
                self.mcp_oauth_resource_identifier.rstrip("/")
                != self.mcp_public_url.rstrip("/")
            ):
                raise ValueError("MCP OAuth resource identifier 必须与 MCP_PUBLIC_URL 一致")
        return self

    def local_web_allowed_hosts(self) -> list[str]:
        """返回 URL 中明确配置的回环 Host，供应用层拒绝 DNS rebinding。"""

        return sorted(
            {
                host
                for value in (self.web_public_base_url, self.web_frontend_url)
                if (host := urlparse(value).hostname) is not None
            }
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回缓存后的配置，避免每个请求重复读取 ``.env``。"""

    return Settings()
