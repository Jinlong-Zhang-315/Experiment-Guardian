"""API v1 总路由。"""

from fastapi import APIRouter

from experiment_guardian.api.routes import health_router, plan_checks_router, projects_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(projects_router)
api_router.include_router(plan_checks_router)
