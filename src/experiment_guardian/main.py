"""FastAPI 进程入口。"""

from fastapi import FastAPI

from experiment_guardian import __version__
from experiment_guardian.api.router import api_router
from experiment_guardian.core.config import get_settings
from experiment_guardian.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="深度学习实验记忆与意图防护服务",
    )
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()


def run() -> None:
    """供 ``experiment-guardian-api`` 命令调用的开发启动函数。"""

    import uvicorn

    uvicorn.run("experiment_guardian.main:app", host="0.0.0.0", port=8000, reload=False)
