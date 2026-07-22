"""应用依赖装配入口。"""

from functools import lru_cache

from experiment_guardian.application.experiments import (
    ExperimentQueryService,
    ExperimentReviewService,
)
from experiment_guardian.application.identity import IdentityProvider
from experiment_guardian.application.ports import (
    EmbeddingGenerator,
    EmbeddingModelOutput,
    GuardianUseCases,
)
from experiment_guardian.application.services import (
    GuardianApplication,
    PlanApprovalService,
    ProjectAdministrationService,
)
from experiment_guardian.application.web_auth import WebAuthService
from experiment_guardian.application.web_management import WebManagementService
from experiment_guardian.core.config import get_settings
from experiment_guardian.infrastructure.bedrock import BedrockTitanV2EmbeddingGenerator
from experiment_guardian.infrastructure.cognito import CognitoOidcProvider
from experiment_guardian.infrastructure.database import get_session_factory
from experiment_guardian.infrastructure.mcp_oauth import (
    CognitoMcpTokenVerifier,
    OAuthMcpIdentityProvider,
)
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


class _LazyQueryEmbeddingGenerator(EmbeddingGenerator):
    """MCP 读取/提交工具不应因为尚未使用查询而初始化 AWS 客户端。"""

    def __init__(self, model_id: str, dimension: int) -> None:
        self._model_id = model_id
        self._dimension = dimension

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, input_text: str) -> EmbeddingModelOutput:
        return get_query_embedding_generator().embed(input_text)


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
def get_oidc_provider() -> CognitoOidcProvider:
    settings = get_settings()
    secret = settings.cognito_web_client_secret
    return CognitoOidcProvider(
        issuer_url=settings.cognito_issuer_url,
        managed_login_domain=settings.cognito_domain,
        client_id=settings.cognito_web_client_id,
        client_secret=secret.get_secret_value() if secret else None,
    )


@lru_cache(maxsize=1)
def get_web_auth_service() -> WebAuthService:
    return WebAuthService(get_session_factory(), get_oidc_provider(), get_settings())


@lru_cache(maxsize=1)
def get_web_management_service() -> WebManagementService:
    settings = get_settings()
    return WebManagementService(
        get_session_factory(),
        get_project_repository(),
        get_artifact_storage(),
        settings.s3_presign_expires_seconds,
    )


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
        get_experiment_query_service(),
    )


@lru_cache(maxsize=1)
def get_experiment_query_service() -> ExperimentQueryService:
    settings = get_settings()
    return ExperimentQueryService(
        get_session_factory(),
        get_project_repository(),
        _LazyQueryEmbeddingGenerator(
            settings.bedrock_embedding_model_id, settings.embedding_dimension
        ),
    )


@lru_cache(maxsize=1)
def get_query_embedding_generator() -> BedrockTitanV2EmbeddingGenerator:
    settings = get_settings()
    return BedrockTitanV2EmbeddingGenerator(
        model_id=settings.bedrock_embedding_model_id,
        region=settings.aws_region,
        dimension=settings.embedding_dimension,
    )


@lru_cache(maxsize=1)
def get_experiment_review_service() -> ExperimentReviewService:
    return ExperimentReviewService(
        get_session_factory(),
        get_project_repository(),
        get_governance_repository(),
        get_submission_repository(),
    )


@lru_cache(maxsize=1)
def get_identity_provider() -> IdentityProvider:
    settings = get_settings()
    if settings.mcp_transport == "streamable-http":
        return OAuthMcpIdentityProvider()
    return EnvironmentIdentityProvider(settings, get_token_service())


@lru_cache(maxsize=1)
def get_mcp_token_verifier() -> CognitoMcpTokenVerifier:
    return CognitoMcpTokenVerifier(get_session_factory(), get_settings())


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
