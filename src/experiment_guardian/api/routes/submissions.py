"""Submission 人工审核接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, status

from experiment_guardian.api.dependencies import ApiIdentity
from experiment_guardian.application.container import get_experiment_review_service
from experiment_guardian.domain.administration import (
    SubmissionDecisionRequest,
    SubmissionDecisionResult,
)

router = APIRouter(prefix="/projects/{project_id}/submissions", tags=["submissions"])


@router.post(
    "/{submission_id}/decision",
    response_model=SubmissionDecisionResult,
    status_code=status.HTTP_201_CREATED,
)
async def decide_submission(
    project_id: UUID,
    submission_id: UUID,
    request: SubmissionDecisionRequest,
    identity: ApiIdentity,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> SubmissionDecisionResult:
    """批准或拒绝一个已完成云端分析的 Submission。"""

    return get_experiment_review_service().decide(
        identity=identity,
        project_id=project_id,
        submission_id=submission_id,
        idempotency_key=idempotency_key,
        request=request,
    )
