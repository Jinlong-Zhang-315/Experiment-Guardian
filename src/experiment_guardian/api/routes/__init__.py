"""FastAPI 路由集合。"""

from experiment_guardian.api.routes.health import router as health_router
from experiment_guardian.api.routes.plan_checks import router as plan_checks_router
from experiment_guardian.api.routes.projects import router as projects_router

__all__ = ["health_router", "plan_checks_router", "projects_router"]
