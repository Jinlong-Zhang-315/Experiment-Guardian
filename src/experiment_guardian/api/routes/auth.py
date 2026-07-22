"""托管 OIDC/local_owner 登录与服务端 Web Session 路由。"""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from experiment_guardian.api.dependencies import ApiIdentity, CsrfIdentity
from experiment_guardian.application.container import get_web_auth_service
from experiment_guardian.application.errors import AuthenticationError
from experiment_guardian.application.web_auth import LoginCompletion
from experiment_guardian.core.config import get_settings
from experiment_guardian.domain.web_auth import AuthSessionView, LogoutResult

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login", response_class=RedirectResponse)
async def login(
    request: Request,
    return_to: Annotated[str, Query(max_length=2000)] = "/",
) -> RedirectResponse:
    service = get_web_auth_service()
    return _login_response(
        service.start_login(
            return_to=return_to,
            user_agent=request.headers.get("User-Agent"),
        ),
        status_code=302,
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
    return _login_response(completion, status_code=303)


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
    completion = get_web_auth_service().start_reauthentication(
        identity=identity,
        return_to=return_to,
    )
    return _login_response(completion, status_code=302)


@router.post("/logout", response_model=LogoutResult)
async def logout(identity: CsrfIdentity) -> JSONResponse:
    service = get_web_auth_service()
    service.revoke(identity=identity)
    result = LogoutResult(logout_url=service.logout_url())
    response = JSONResponse(result.model_dump(mode="json"))
    settings = get_settings()
    response.delete_cookie(settings.web_session_cookie_name, path="/")
    response.delete_cookie(settings.web_csrf_cookie_name, path="/")
    return response


def _login_response(completion: LoginCompletion, *, status_code: int) -> RedirectResponse:
    service = get_web_auth_service()
    response = RedirectResponse(completion.redirect_url, status_code=status_code)
    raw_session = completion.raw_session
    if raw_session:
        settings = get_settings()
        secure = settings.app_env in {"staging", "production"}
        response.set_cookie(
            key=settings.web_session_cookie_name,
            value=raw_session,
            max_age=completion.max_age,
            httponly=True,
            secure=secure,
            samesite="lax",
            path="/",
        )
        response.set_cookie(
            key=settings.web_csrf_cookie_name,
            value=service.csrf_token(raw_session),
            max_age=completion.max_age,
            httponly=False,
            secure=secure,
            samesite="lax",
            path="/",
        )
    return response
