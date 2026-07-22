"""Owner 项目初始化接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, status

from experiment_guardian.api.dependencies import CsrfIdentity
from experiment_guardian.application.container import get_project_administration_service
from experiment_guardian.domain.administration import (
    ProjectInitializeRequest,
    ProjectInitializeResponse,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post(
    "/initialize",
    response_model=ProjectInitializeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def initialize_project(
    request: ProjectInitializeRequest,
    identity: CsrfIdentity,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> ProjectInitializeResponse:
    """原子创建项目首个正式 Context、Active Intent 和确认约束。"""

    return get_project_administration_service().initialize_project(
        identity=identity,
        idempotency_key=idempotency_key,
        request=request,
    )
