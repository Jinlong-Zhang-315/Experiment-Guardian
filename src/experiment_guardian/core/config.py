"""应用配置。

配置只从环境变量和本地 ``.env`` 读取。代码中不提供真实凭据默认值，避免开发阶段
将数据库密码、AWS Key 或 MCP Token 意外提交到仓库。
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
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
    app_env: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    api_prefix: str = "/api/v1"

    # 数据库 URL 使用 SQLAlchemy 的 psycopg 方言。测试可以覆盖为独立数据库。
    database_url: str = (
        "postgresql+psycopg://root@127.0.0.1:26257/experiment_guardian?sslmode=disable"
    )
    database_echo: bool = False

    mcp_transport: Literal["stdio", "streamable-http"] = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8001, ge=1, le=65535)
    mcp_access_token: SecretStr | None = None

    manifest_hash_algorithm: Literal["sha256"] = "sha256"

    aws_region: str = "us-east-1"
    s3_bucket: str = ""
    s3_presign_expires_seconds: int = Field(default=900, ge=60, le=3600)
    sqs_submission_queue_url: str = ""
    sqs_wait_time_seconds: int = Field(default=20, ge=0, le=20)
    sqs_visibility_timeout_seconds: int = Field(default=120, ge=30, le=43200)
    worker_lease_seconds: int = Field(default=120, ge=30, le=3600)
    worker_max_attempts: int = Field(default=5, ge=1, le=20)
    bedrock_summary_model_id: str = ""
    bedrock_embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    embedding_dimension: int = Field(default=1024, ge=1)
    bedrock_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    bedrock_read_timeout_seconds: int = Field(default=60, ge=1, le=300)

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回缓存后的配置，避免每个请求重复读取 ``.env``。"""

    return Settings()
