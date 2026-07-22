"""将应用错误转换为稳定且不泄露内部信息的 HTTP 响应。"""

from fastapi import Request
from fastapi.responses import JSONResponse

from experiment_guardian.application.errors import (
    ApplicationError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    FeatureUnavailableError,
    InputValidationError,
    RecentAuthenticationRequiredError,
    ResourceNotFoundError,
    ServiceUnavailableError,
)

STATUS_BY_ERROR: dict[type[ApplicationError], int] = {
    AuthenticationError: 401,
    AuthorizationError: 403,
    RecentAuthenticationRequiredError: 428,
    ResourceNotFoundError: 404,
    ConflictError: 409,
    InputValidationError: 422,
    ServiceUnavailableError: 503,
    FeatureUnavailableError: 501,
}


async def application_error_handler(_: Request, exc: ApplicationError) -> JSONResponse:
    status = STATUS_BY_ERROR.get(type(exc), 500)
    return JSONResponse(
        status_code=status,
        content={"error": {"code": exc.code, "message": exc.message}},
        headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
    )
