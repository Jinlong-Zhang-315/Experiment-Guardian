"""四个 R14 Web 页面使用的管理 API。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, status

from experiment_guardian.api.dependencies import ApiIdentity, CsrfIdentity
from experiment_guardian.application.container import (
    get_experiment_query_service,
    get_web_management_service,
)
from experiment_guardian.application.errors import InputValidationError
from experiment_guardian.domain.contracts import (
    ExperimentQueryCommand,
    ExperimentQueryResult,
    HumanReadablePolicy,
)
from experiment_guardian.domain.web_management import (
    ArtifactDownloadResult,
    ExperimentDetailWebView,
    ExperimentPage,
    PlanCheckPage,
    PlanCheckWebView,
    PolicyPublishRequest,
    PolicyPublishResult,
    ProjectList,
    ProjectSettingsView,
    SubmissionPage,
    SubmissionWebView,
)

router = APIRouter(prefix="/projects", tags=["web-management"])


@router.get("", response_model=ProjectList)
async def list_projects(identity: ApiIdentity) -> ProjectList:
    return get_web_management_service().list_projects(identity)


@router.get("/{project_id}/settings", response_model=ProjectSettingsView)
async def get_project_settings(project_id: UUID, identity: ApiIdentity) -> ProjectSettingsView:
    return get_web_management_service().get_settings(project_id=project_id, identity=identity)


@router.post(
    "/{project_id}/policy-versions",
    response_model=PolicyPublishResult,
    status_code=status.HTTP_201_CREATED,
)
async def publish_policy_version(
    project_id: UUID,
    request: PolicyPublishRequest,
    identity: CsrfIdentity,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> PolicyPublishResult:
    return get_web_management_service().publish_policy(
        project_id=project_id,
        identity=identity,
        idempotency_key=idempotency_key,
        request=request,
    )


@router.post(
    "/{project_id}/contexts/{context_id}/human-readable/regenerate",
    response_model=HumanReadablePolicy,
)
async def regenerate_policy_narrative(
    project_id: UUID,
    context_id: UUID,
    identity: CsrfIdentity,
) -> HumanReadablePolicy:
    """重建结构化策略的派生说明；不会修改正式 Context、Intent 或 Constraints。"""

    return get_web_management_service().regenerate_policy_narrative(
        project_id=project_id,
        context_id=context_id,
        identity=identity,
    )


@router.get("/{project_id}/plan-checks", response_model=PlanCheckPage)
async def list_plan_checks(
    project_id: UUID,
    identity: ApiIdentity,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> PlanCheckPage:
    return get_web_management_service().list_plan_checks(
        project_id=project_id, identity=identity, cursor=cursor, limit=limit
    )


@router.get("/{project_id}/plan-checks/{plan_check_id}", response_model=PlanCheckWebView)
async def get_plan_check(
    project_id: UUID, plan_check_id: UUID, identity: ApiIdentity
) -> PlanCheckWebView:
    return get_web_management_service().get_plan_check(
        project_id=project_id, plan_check_id=plan_check_id, identity=identity
    )


@router.get("/{project_id}/submissions", response_model=SubmissionPage)
async def list_submissions(
    project_id: UUID,
    identity: ApiIdentity,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> SubmissionPage:
    return get_web_management_service().list_submissions(
        project_id=project_id, identity=identity, cursor=cursor, limit=limit
    )


@router.get("/{project_id}/submissions/{submission_id}", response_model=SubmissionWebView)
async def get_submission(
    project_id: UUID, submission_id: UUID, identity: ApiIdentity
) -> SubmissionWebView:
    return get_web_management_service().get_submission(
        project_id=project_id, submission_id=submission_id, identity=identity
    )


@router.get("/{project_id}/experiments", response_model=ExperimentPage)
async def list_experiments(
    project_id: UUID,
    identity: ApiIdentity,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ExperimentPage:
    return get_web_management_service().list_experiments(
        project_id=project_id, identity=identity, cursor=cursor, limit=limit
    )


@router.post("/{project_id}/experiments/query", response_model=list[ExperimentQueryResult])
async def query_experiments(
    project_id: UUID, request: ExperimentQueryCommand, identity: ApiIdentity
) -> list[ExperimentQueryResult]:
    if request.project_id != project_id:
        raise InputValidationError("URL project_id 与查询请求不一致")
    return list(get_experiment_query_service().query(request, identity))


@router.get(
    "/{project_id}/experiments/{experiment_id}",
    response_model=ExperimentDetailWebView,
)
async def get_experiment(
    project_id: UUID, experiment_id: UUID, identity: ApiIdentity
) -> ExperimentDetailWebView:
    return get_web_management_service().get_experiment(
        project_id=project_id, experiment_id=experiment_id, identity=identity
    )


@router.post(
    "/{project_id}/artifacts/{artifact_id}/download-url",
    response_model=ArtifactDownloadResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_artifact_download_url(
    project_id: UUID, artifact_id: UUID, identity: CsrfIdentity
) -> ArtifactDownloadResult:
    return get_web_management_service().create_artifact_download(
        project_id=project_id, artifact_id=artifact_id, identity=identity
    )
