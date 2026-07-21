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
)
from experiment_guardian.infrastructure.security import (
    EnvironmentIdentityProvider,
    SqlAlchemyTokenService,
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
def get_token_service() -> SqlAlchemyTokenService:
    return SqlAlchemyTokenService(get_session_factory())


@lru_cache(maxsize=1)
def get_guardian_use_cases() -> GuardianUseCases:
    return GuardianApplication(
        get_session_factory(),
        get_project_repository(),
        get_plan_check_repository(),
        get_governance_repository(),
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
