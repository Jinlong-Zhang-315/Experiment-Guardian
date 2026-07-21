"""基于 CockroachDB 的 Token 签发、验证和 stdio 身份适配器。"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.errors import (
    AuthenticationError,
    ConflictError,
    ServiceUnavailableError,
)
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.core.config import Settings
from experiment_guardian.domain.enums import TokenAudience
from experiment_guardian.infrastructure.models import AccessToken

TOKEN_PREFIX = "eg_"
TOKEN_DISPLAY_PREFIX_LENGTH = 12
MAX_TOKEN_LIFETIME_DAYS = 90


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token_id: UUID
    raw_token: str
    token_prefix: str
    expires_at: datetime


def token_digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SqlAlchemyTokenService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def authenticate(self, raw_token: str, *, audience: TokenAudience) -> RequestIdentity:
        if not raw_token.startswith(TOKEN_PREFIX):
            raise AuthenticationError("访问 Token 无效、已过期或已撤销")

        try:
            with self._session_factory() as session:
                token = session.scalar(
                    select(AccessToken).where(AccessToken.token_hash == token_digest(raw_token))
                )
                now = datetime.now(UTC)
                if (
                    token is None
                    or token.audience is not audience
                    or token.revoked_at is not None
                    or _as_utc(token.expires_at) <= now
                ):
                    raise AuthenticationError("访问 Token 无效、已过期或已撤销")
                return RequestIdentity(
                    user_id=token.user_id,
                    team_id=token.team_id,
                    token_id=token.id,
                    project_id=token.project_id,
                    scopes=frozenset(token.scopes),
                )
        except DBAPIError as exc:
            raise ServiceUnavailableError("Token 验证服务暂时不可用") from exc

    def issue(
        self,
        session: Session,
        *,
        user_id: UUID,
        team_id: UUID,
        project_id: UUID | None,
        audience: TokenAudience,
        name: str,
        scopes: set[str],
        lifetime_days: int,
        created_by: UUID,
    ) -> IssuedToken:
        if not 1 <= lifetime_days <= MAX_TOKEN_LIFETIME_DAYS:
            raise ValueError(f"Token 有效期必须为 1 到 {MAX_TOKEN_LIFETIME_DAYS} 天")
        if audience is TokenAudience.MCP and project_id is None:
            raise ValueError("MCP Token 必须绑定 project_id")
        if not scopes:
            raise ValueError("Token 至少需要一个 scope")

        now = datetime.now(UTC)
        existing_tokens = session.scalars(
            select(AccessToken).where(
                AccessToken.user_id == user_id,
                AccessToken.team_id == team_id,
                AccessToken.project_id == project_id,
                AccessToken.audience == audience,
                AccessToken.name == name,
                AccessToken.revoked_at.is_(None),
            )
        ).all()
        for existing in existing_tokens:
            existing.revoked_at = now

        raw_token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        expires_at = now + timedelta(days=lifetime_days)
        token = AccessToken(
            user_id=user_id,
            team_id=team_id,
            project_id=project_id,
            audience=audience,
            name=name,
            token_prefix=raw_token[:TOKEN_DISPLAY_PREFIX_LENGTH],
            token_hash=token_digest(raw_token),
            scopes=sorted(scopes),
            expires_at=expires_at,
            created_by=created_by,
        )
        session.add(token)
        session.flush()
        return IssuedToken(
            token_id=token.id,
            raw_token=raw_token,
            token_prefix=token.token_prefix,
            expires_at=expires_at,
        )

    @staticmethod
    def revoke(session: Session, token_id: UUID) -> None:
        token = session.get(AccessToken, token_id)
        if token is None:
            raise ConflictError("指定 Token 不存在")
        if token.revoked_at is None:
            token.revoked_at = datetime.now(UTC)


class EnvironmentIdentityProvider:
    """stdio MCP 进程从环境变量读取一次调用凭据，但每次调用都重新验证数据库状态。"""

    def __init__(self, settings: Settings, token_service: SqlAlchemyTokenService) -> None:
        self._settings = settings
        self._token_service = token_service

    def current_identity(self) -> RequestIdentity:
        configured = self._settings.mcp_access_token
        if configured is None or not configured.get_secret_value():
            raise AuthenticationError("MCP_ACCESS_TOKEN 未配置")
        return self._token_service.authenticate(
            configured.get_secret_value(), audience=TokenAudience.MCP
        )
