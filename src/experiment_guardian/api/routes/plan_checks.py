"""Plan Check 的 Owner 审批接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, status

from experiment_guardian.api.dependencies import ApiIdentity
from experiment_guardian.application.container import get_plan_approval_service
from experiment_guardian.domain.administration import (
    PlanCheckDecisionRequest,
    PlanCheckDecisionResult,
)

router = APIRouter(prefix="/projects/{project_id}/plan-checks", tags=["plan-checks"])


@router.post(
    "/{plan_check_id}/decision",
    response_model=PlanCheckDecisionResult,
    status_code=status.HTTP_201_CREATED,
)
async def decide_plan_check(
    project_id: UUID,
    plan_check_id: UUID,
    request: PlanCheckDecisionRequest,
    identity: ApiIdentity,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> PlanCheckDecisionResult:
    """由项目 Owner 批准或拒绝一个待审批的 Plan Check。"""

    return get_plan_approval_service().decide(
        identity=identity,
        project_id=project_id,
        plan_check_id=plan_check_id,
        idempotency_key=idempotency_key,
        request=request,
    )
