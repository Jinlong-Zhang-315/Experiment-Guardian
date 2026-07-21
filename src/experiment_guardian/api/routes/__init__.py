"""FastAPI 路由集合。"""

from experiment_guardian.api.routes.health import router as health_router

__all__ = ["health_router"]
