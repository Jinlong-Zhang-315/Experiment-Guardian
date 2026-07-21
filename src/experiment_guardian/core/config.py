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
    bedrock_model_id: str = ""
    embedding_dimension: int = Field(default=1536, gt=0)

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        """允许环境变量使用 ``info`` 等小写形式，内部始终保留标准大写值。"""

        return value.upper() if isinstance(value, str) else value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回缓存后的配置，避免每个请求重复读取 ``.env``。"""

    return Settings()
