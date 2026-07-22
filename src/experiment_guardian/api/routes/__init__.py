"""FastAPI 路由集合。"""

from experiment_guardian.api.routes.auth import router as auth_router
from experiment_guardian.api.routes.health import router as health_router
from experiment_guardian.api.routes.plan_checks import router as plan_checks_router
from experiment_guardian.api.routes.projects import router as projects_router
from experiment_guardian.api.routes.submissions import router as submissions_router
from experiment_guardian.api.routes.web import router as web_router

__all__ = [
    "auth_router",
    "health_router",
    "plan_checks_router",
    "projects_router",
    "submissions_router",
    "web_router",
]
