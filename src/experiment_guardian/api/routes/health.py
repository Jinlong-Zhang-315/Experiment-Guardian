"""不依赖数据库的进程存活与能力发现接口。"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from experiment_guardian import __version__
from experiment_guardian.core.config import get_settings

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str
    environment: str


class CapabilitiesResponse(BaseModel):
    mcp_tools: list[str]
    config_formats: list[str]
    artifact_formats: list[str]
    plan_results: list[str]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """负载均衡器存活检查；不访问外部服务，避免数据库抖动触发进程重启。"""

    settings = get_settings()
    return HealthResponse(
        service=settings.app_name,
        version=__version__,
        environment=settings.app_env,
    )


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities() -> CapabilitiesResponse:
    """返回已冻结的 P0 接口边界，方便本地 Agent 启动时进行兼容性检查。"""

    return CapabilitiesResponse(
        mcp_tools=[
            "project_get_context",
            "experiment_check_plan",
            "run_manifest_create",
            "submission_prepare",
            "submission_finalize",
            "experiments_query",
        ],
        config_formats=["yaml", "json"],
        artifact_formats=["yaml", "json", "txt", "md"],
        plan_results=["PASS", "NEEDS_APPROVAL", "BLOCKED"],
    )
