"""应用依赖装配入口。"""

from functools import lru_cache

from experiment_guardian.application.identity import IdentityProvider
from experiment_guardian.application.ports import GuardianUseCases
from experiment_guardian.application.services import (
    GuardianApplication,
    PlanApprovalService,
    ProjectAdministrationService,
)
from experiment_guardian.core.config import get_settings
from experiment_guardian.infrastructure.database import get_session_factory
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyGovernanceRepository,
    SqlAlchemyPlanCheckRepository,
    SqlAlchemyProjectRepository,
    SqlAlchemySubmissionRepository,
    SqlAlchemyWorkflowRepository,
)
from experiment_guardian.infrastructure.security import (
    EnvironmentIdentityProvider,
    SqlAlchemyTokenService,
)
from experiment_guardian.infrastructure.storage import (
    S3ArtifactStorage,
    UnconfiguredArtifactStorage,
)


@lru_cache(maxsize=1)
def get_project_repository() -> SqlAlchemyProjectRepository:
    return SqlAlchemyProjectRepository()


@lru_cache(maxsize=1)
def get_plan_check_repository() -> SqlAlchemyPlanCheckRepository:
    return SqlAlchemyPlanCheckRepository()


@lru_cache(maxsize=1)
def get_governance_repository() -> SqlAlchemyGovernanceRepository:
    return SqlAlchemyGovernanceRepository()


@lru_cache(maxsize=1)
def get_submission_repository() -> SqlAlchemySubmissionRepository:
    return SqlAlchemySubmissionRepository()


@lru_cache(maxsize=1)
def get_workflow_repository() -> SqlAlchemyWorkflowRepository:
    return SqlAlchemyWorkflowRepository()


@lru_cache(maxsize=1)
def get_artifact_storage() -> S3ArtifactStorage | UnconfiguredArtifactStorage:
    settings = get_settings()
    if not settings.s3_bucket:
        return UnconfiguredArtifactStorage()
    return S3ArtifactStorage(bucket=settings.s3_bucket, region=settings.aws_region)


@lru_cache(maxsize=1)
def get_token_service() -> SqlAlchemyTokenService:
    return SqlAlchemyTokenService(get_session_factory())


@lru_cache(maxsize=1)
def get_guardian_use_cases() -> GuardianUseCases:
    settings = get_settings()
    return GuardianApplication(
        get_session_factory(),
        get_project_repository(),
        get_plan_check_repository(),
        get_governance_repository(),
        get_submission_repository(),
        get_artifact_storage(),
        settings.s3_presign_expires_seconds,
        get_workflow_repository(),
        settings.worker_max_attempts,
    )


@lru_cache(maxsize=1)
def get_identity_provider() -> IdentityProvider:
    return EnvironmentIdentityProvider(get_settings(), get_token_service())


@lru_cache(maxsize=1)
def get_project_administration_service() -> ProjectAdministrationService:
    return ProjectAdministrationService(get_session_factory(), get_project_repository())


@lru_cache(maxsize=1)
def get_plan_approval_service() -> PlanApprovalService:
    return PlanApprovalService(
        get_session_factory(),
        get_project_repository(),
        get_governance_repository(),
    )
