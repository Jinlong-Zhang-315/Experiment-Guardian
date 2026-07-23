"""FastAPI 进程入口。"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from experiment_guardian import __version__
from experiment_guardian.api.errors import application_error_handler
from experiment_guardian.api.router import api_router
from experiment_guardian.application.errors import ApplicationError
from experiment_guardian.core.config import get_settings
from experiment_guardian.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="提高实验一致性、可追溯性和风险可见性的治理服务",
    )
    app.add_exception_handler(ApplicationError, application_error_handler)  # type: ignore[arg-type]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_frontend_url.rstrip("/")],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-CSRF-Token"],
    )
    if settings.web_auth_mode == "local_owner":
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=settings.local_web_allowed_hosts(),
        )
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()


def run() -> None:
    """供 ``experiment-guardian-api`` 命令调用的开发启动函数。"""

    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "experiment_guardian.main:app",
        host=settings.api_host,
        port=8000,
        reload=False,
    )
