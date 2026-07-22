"""FastAPI 认证依赖。"""

from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from experiment_guardian.application.container import get_token_service, get_web_auth_service
from experiment_guardian.application.errors import AuthenticationError
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.core.config import get_settings
from experiment_guardian.domain.enums import TokenAudience

bearer_scheme = HTTPBearer(auto_error=False)


async def require_api_identity(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> RequestIdentity:
    if credentials is not None and credentials.scheme.lower() == "bearer":
        return get_token_service().authenticate(
            credentials.credentials, audience=TokenAudience.API
        )
    raw_session = request.cookies.get(get_settings().web_session_cookie_name)
    if not raw_session:
        raise AuthenticationError("缺少有效的 Bearer Token 或 Web Session")
    identity = get_web_auth_service().authenticate(raw_session)
    request.state.raw_web_session = raw_session
    return identity


async def require_csrf_identity(
    request: Request,
    identity: Annotated[RequestIdentity, Depends(require_api_identity)],
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> RequestIdentity:
    """Bearer Token 不受浏览器 CSRF 影响；Cookie Session 的写请求必须校验。"""

    if identity.authentication_method == "WEB_SESSION":
        raw_session = getattr(request.state, "raw_web_session", None)
        if not isinstance(raw_session, str):
            raise AuthenticationError("Web Session 上下文缺失")
        get_web_auth_service().validate_csrf(raw_session, csrf_token)
    return identity


ApiIdentity = Annotated[RequestIdentity, Depends(require_api_identity)]
CsrfIdentity = Annotated[RequestIdentity, Depends(require_csrf_identity)]
