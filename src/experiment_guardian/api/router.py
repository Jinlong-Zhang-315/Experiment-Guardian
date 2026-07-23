"""API v1 总路由。"""

from fastapi import APIRouter

from experiment_guardian.api.routes import (
    agent_router,
    auth_router,
    health_router,
    plan_checks_router,
    projects_router,
    submissions_router,
    web_router,
)

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(health_router)
api_router.include_router(projects_router)
api_router.include_router(plan_checks_router)
api_router.include_router(submissions_router)
api_router.include_router(web_router)
api_router.include_router(agent_router)
