"""远程 MCP 的 Cognito OAuth 资源服务器验证器。"""

from datetime import UTC, datetime
from uuid import UUID

import jwt
from jwt import PyJWKClient
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.errors import AuthenticationError
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.core.config import Settings
from experiment_guardian.infrastructure.models import (
    AuditLog,
    McpOAuthClient,
    McpOAuthGrant,
    TeamMember,
    User,
)

MCP_APPLICATION_SCOPES = frozenset(
    {
        "project:read",
        "experiment:check",
        "manifest:create",
        "submission:create",
        "submission:finalize",
        "submission:read",
        "experiment:query",
    }
)


def oauth_scope_map(prefix: str) -> dict[str, str]:
    normalized = prefix.strip().strip("/")
    return {
        f"{normalized}/{scope.replace(':', '.')}": scope
        for scope in sorted(MCP_APPLICATION_SCOPES)
    }


class CognitoMcpTokenVerifier:
    """校验 JWT 后再查询本地客户端、Grant 和团队成员关系，实现即时撤销。"""

    def __init__(self, session_factory: sessionmaker[Session], settings: Settings) -> None:
        self._session_factory = session_factory
        self._issuer = settings.cognito_issuer_url.rstrip("/")
        self._resource = (
            settings.mcp_oauth_resource_identifier or settings.mcp_public_url
        ).rstrip("/")
        self._scope_map = oauth_scope_map(settings.mcp_oauth_scope_prefix)
        self._jwks = PyJWKClient(f"{self._issuer}/.well-known/jwks.json")

    async def verify_token(self, token: str) -> AccessToken | None:
        if not self._issuer or not self._resource:
            return None
        try:
            claims = self._decode_token(token)
            if claims.get("token_use") != "access":
                return None
            subject = claims.get("sub")
            client_id = claims.get("client_id")
            scope_claim = claims.get("scope", "")
            expires_at = claims.get("exp")
            if (
                not isinstance(subject, str)
                or not isinstance(client_id, str)
                or not isinstance(scope_claim, str)
                or not isinstance(expires_at, int)
            ):
                return None
            oauth_scopes = set(scope_claim.split())
            application_scopes = {
                self._scope_map[item] for item in oauth_scopes if item in self._scope_map
            }
            if not application_scopes:
                return None
            grant = self._authorize_locally(
                subject=subject,
                client_id=client_id,
                oauth_scopes=oauth_scopes,
                application_scopes=application_scopes,
            )
            if grant is None:
                return None
            return AccessToken(
                token=token,
                client_id=client_id,
                scopes=sorted(oauth_scopes),
                expires_at=expires_at,
                resource=self._resource,
                subject=subject,
                claims=grant,
            )
        except (jwt.PyJWTError, DBAPIError, IntegrityError, ValueError):
            return None

    def _decode_token(self, token: str) -> dict[str, object]:
        signing_key = self._jwks.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self._resource,
            issuer=self._issuer,
            options={"require": ["exp", "iat", "sub", "client_id", "token_use"]},
        )

    def _authorize_locally(
        self,
        *,
        subject: str,
        client_id: str,
        oauth_scopes: set[str],
        application_scopes: set[str],
    ) -> dict[str, object] | None:
        now = datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            client = session.scalar(
                select(McpOAuthClient).where(
                    McpOAuthClient.cognito_client_id == client_id,
                    McpOAuthClient.revoked_at.is_(None),
                )
            )
            user = session.scalar(select(User).where(User.cognito_sub == subject))
            if client is None or user is None:
                return None
            membership = session.get(TeamMember, (client.team_id, user.id))
            if membership is None:
                return None
            allowed = set(client.allowed_scopes)
            if not application_scopes <= allowed:
                return None
            grant = session.scalar(
                select(McpOAuthGrant).where(
                    McpOAuthGrant.mcp_oauth_client_id == client.id,
                    McpOAuthGrant.user_id == user.id,
                )
            )
            if grant is not None and grant.revoked_at is not None:
                return None
            if grant is None:
                grant = McpOAuthGrant(
                    mcp_oauth_client_id=client.id,
                    user_id=user.id,
                    granted_scopes=sorted(application_scopes),
                    last_used_at=now,
                )
                session.add(grant)
                session.flush()
                session.add(
                    AuditLog(
                        team_id=client.team_id,
                        project_id=client.project_id,
                        actor_type="USER",
                        actor_id=user.id,
                        action="mcp.oauth.grant.created",
                        target_type="MCP_OAUTH_GRANT",
                        target_id=grant.id,
                        before_value=None,
                        after_value={
                            "cognito_client_id": client_id,
                            "oauth_scopes": sorted(oauth_scopes),
                            "application_scopes": sorted(application_scopes),
                        },
                    )
                )
            else:
                grant.last_used_at = now
                grant.granted_scopes = sorted(set(grant.granted_scopes) | application_scopes)
            return {
                "user_id": str(user.id),
                "team_id": str(client.team_id),
                "project_id": str(client.project_id),
                "grant_id": str(grant.id),
                "application_scopes": sorted(application_scopes),
                "cognito_client_id": client_id,
            }


class OAuthMcpIdentityProvider:
    """从 MCP SDK 已认证的请求上下文构造不可由工具参数伪造的身份。"""

    def current_identity(self) -> RequestIdentity:
        access_token = get_access_token()
        claims = access_token.claims if access_token else None
        if access_token is None or not isinstance(claims, dict):
            raise AuthenticationError("远程 MCP 请求缺少已验证的 OAuth 身份")
        if access_token.expires_at is None:
            raise AuthenticationError("远程 MCP OAuth 身份缺少过期时间")
        try:
            return RequestIdentity(
                user_id=UUID(str(claims["user_id"])),
                team_id=UUID(str(claims["team_id"])),
                token_id=UUID(str(claims["grant_id"])),
                project_id=UUID(str(claims["project_id"])),
                scopes=frozenset(str(item) for item in claims["application_scopes"]),
                authentication_method="MCP_OAUTH",
                subject=access_token.subject,
                client_id=access_token.client_id,
                credential_expires_at=datetime.fromtimestamp(access_token.expires_at, tz=UTC),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthenticationError("远程 MCP OAuth 身份声明不完整") from exc
