"""FastAPI 认证依赖。"""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from experiment_guardian.application.container import get_token_service
from experiment_guardian.application.errors import AuthenticationError
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.domain.enums import TokenAudience

bearer_scheme = HTTPBearer(auto_error=False)


async def require_api_identity(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> RequestIdentity:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("缺少有效的 Bearer Token")
    return get_token_service().authenticate(credentials.credentials, audience=TokenAudience.API)


ApiIdentity = Annotated[RequestIdentity, Depends(require_api_identity)]
