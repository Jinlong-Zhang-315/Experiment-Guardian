"""Cognito Managed Login 与服务端 Web Session 路由。"""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from experiment_guardian.api.dependencies import ApiIdentity, CsrfIdentity
from experiment_guardian.application.container import get_web_auth_service
from experiment_guardian.application.errors import AuthenticationError
from experiment_guardian.core.config import get_settings
from experiment_guardian.domain.web_auth import AuthSessionView, LogoutResult

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login", response_class=RedirectResponse)
async def login(return_to: Annotated[str, Query(max_length=2000)] = "/") -> RedirectResponse:
    return RedirectResponse(
        get_web_auth_service().begin_login(return_to=return_to), status_code=302
    )


@router.get("/callback", response_class=RedirectResponse)
async def callback(
    request: Request,
    state: Annotated[str, Query(min_length=1)],
    code: Annotated[str, Query(min_length=1)],
) -> RedirectResponse:
    completion = get_web_auth_service().complete_login(
        state=state,
        code=code,
        user_agent=request.headers.get("User-Agent"),
    )
    response = RedirectResponse(completion.redirect_url, status_code=303)
    if completion.raw_session:
        settings = get_settings()
        response.set_cookie(
            key=settings.web_session_cookie_name,
            value=completion.raw_session,
            max_age=completion.max_age,
            httponly=True,
            secure=settings.app_env in {"staging", "production"},
            samesite="lax",
            path="/",
        )
    return response


@router.get("/me", response_model=AuthSessionView)
async def me(request: Request, identity: ApiIdentity) -> AuthSessionView:
    raw_session = getattr(request.state, "raw_web_session", None)
    if not isinstance(raw_session, str):
        raise AuthenticationError("/auth/me 仅支持 Web Session")
    return get_web_auth_service().session_view(identity=identity, raw_session=raw_session)


@router.get("/reauth", response_class=RedirectResponse)
async def reauthenticate(
    identity: ApiIdentity,
    return_to: Annotated[str, Query(max_length=2000)] = "/",
) -> RedirectResponse:
    if identity.authentication_method != "WEB_SESSION":
        raise AuthenticationError("近期认证仅适用于 Web Session")
    url = get_web_auth_service().begin_login(
        return_to=return_to,
        purpose="REAUTH",
        session_id=identity.token_id,
    )
    return RedirectResponse(url, status_code=302)


@router.post("/logout", response_model=LogoutResult)
async def logout(identity: CsrfIdentity) -> JSONResponse:
    service = get_web_auth_service()
    service.revoke(identity=identity)
    result = LogoutResult(logout_url=service.logout_url())
    response = JSONResponse(result.model_dump(mode="json"))
    response.delete_cookie(get_settings().web_session_cookie_name, path="/")
    return response
