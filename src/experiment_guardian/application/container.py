"""应用依赖装配入口。"""

from functools import lru_cache

from experiment_guardian.application.action_proposals import ActionProposalService
from experiment_guardian.application.agent import AgentConversationService
from experiment_guardian.application.agent_observability import AgentObservabilityService
from experiment_guardian.application.agent_tools import AgentToolRegistry
from experiment_guardian.application.experiment_plans import ExperimentPlanService
from experiment_guardian.application.experiments import (
    ExperimentQueryService,
    ExperimentReviewService,
)
from experiment_guardian.application.identity import IdentityProvider
from experiment_guardian.application.policy_drafts import PolicyDraftService
from experiment_guardian.application.ports import (
    AgentChatModel,
    ArtifactStorage,
    EmbeddingGenerator,
    EmbeddingModelOutput,
    GuardianUseCases,
)
from experiment_guardian.application.research_memories import ResearchMemoryService
from experiment_guardian.application.research_reports import ResearchReportService
from experiment_guardian.application.services import (
    GuardianApplication,
    PlanApprovalService,
    ProjectAdministrationService,
)
from experiment_guardian.application.web_auth import LocalOwnerWebAuthService, WebAuthService
from experiment_guardian.application.web_management import WebManagementService
from experiment_guardian.core.config import Settings, get_settings
from experiment_guardian.infrastructure.bailian import (
    BailianAgentChatModel,
    BailianEmbeddingGenerator,
)
from experiment_guardian.infrastructure.bedrock import (
    BedrockAgentChatModel,
    BedrockTitanV2EmbeddingGenerator,
)
from experiment_guardian.infrastructure.cognito import CognitoOidcProvider
from experiment_guardian.infrastructure.database import get_session_factory
from experiment_guardian.infrastructure.mcp_oauth import (
    CognitoMcpTokenVerifier,
    OAuthMcpIdentityProvider,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyAgentRepository,
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
    AwsS3ObjectStorage,
    S3CompatibleObjectStorage,
    UnconfiguredArtifactStorage,
)


class _LazyQueryEmbeddingGenerator(EmbeddingGenerator):
    """MCP 读取/提交工具不应因为尚未使用查询而初始化模型客户端。"""

    def __init__(self, provider: str, model_id: str, dimension: int) -> None:
        self._provider = provider
        self._model_id = model_id
        self._dimension = dimension

    @property
    def provider(self) -> str:
        return self._provider

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
def get_agent_repository() -> SqlAlchemyAgentRepository:
    return SqlAlchemyAgentRepository()


@lru_cache(maxsize=1)
def get_artifact_storage() -> ArtifactStorage:
    settings = get_settings()
    if not settings.s3_bucket:
        return UnconfiguredArtifactStorage()
    if settings.object_storage_backend == "s3_compatible":
        access_key = settings.s3_access_key
        secret_key = settings.s3_secret_key
        return S3CompatibleObjectStorage(
            bucket=settings.s3_bucket,
            region=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url,
            presign_endpoint_url=settings.s3_presign_endpoint_url,
            access_key=access_key.get_secret_value() if access_key else "",
            secret_key=secret_key.get_secret_value() if secret_key else "",
            force_path_style=settings.s3_force_path_style,
        )
    return AwsS3ObjectStorage(bucket=settings.s3_bucket, region=settings.aws_region)


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
    settings = get_settings()
    if settings.web_auth_mode == "local_owner":
        return LocalOwnerWebAuthService(get_session_factory(), settings)
    return WebAuthService(get_session_factory(), get_oidc_provider(), settings)


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
def get_agent_tool_registry() -> AgentToolRegistry:
    return AgentToolRegistry(
        get_session_factory(),
        get_project_repository(),
        get_policy_draft_service(),
        get_action_proposal_service(),
        get_research_report_service(),
        get_research_memory_service(),
    )


@lru_cache(maxsize=1)
def get_policy_draft_service() -> PolicyDraftService:
    return PolicyDraftService(get_session_factory(), get_project_repository())


@lru_cache(maxsize=1)
def get_action_proposal_service() -> ActionProposalService:
    return ActionProposalService(
        get_session_factory(),
        get_project_repository(),
        get_policy_draft_service(),
        get_web_management_service(),
        get_plan_approval_service(),
        get_experiment_review_service(),
    )


@lru_cache(maxsize=1)
def get_agent_conversation_service() -> AgentConversationService:
    return AgentConversationService(
        get_session_factory(),
        get_project_repository(),
        get_agent_repository(),
        get_settings(),
    )


@lru_cache(maxsize=1)
def get_experiment_plan_service() -> ExperimentPlanService:
    return ExperimentPlanService(
        get_session_factory(),
        get_project_repository(),
        get_agent_repository(),
        get_settings(),
    )


@lru_cache(maxsize=1)
def get_research_report_service() -> ResearchReportService:
    return ResearchReportService(
        get_session_factory(),
        get_project_repository(),
        get_research_memory_service(),
    )


@lru_cache(maxsize=1)
def get_research_memory_service() -> ResearchMemoryService:
    settings = get_settings()
    return ResearchMemoryService(
        get_session_factory(),
        get_project_repository(),
        _LazyQueryEmbeddingGenerator(
            settings.llm_provider,
            (
                settings.bailian_embedding_model
                if settings.llm_provider == "bailian"
                else settings.bedrock_embedding_model_id
            ),
            settings.embedding_dimension,
        ),
        settings,
    )


@lru_cache(maxsize=1)
def get_agent_chat_model() -> AgentChatModel:
    settings = get_settings()
    return build_agent_chat_model(settings)


@lru_cache(maxsize=1)
def get_agent_observability_service() -> AgentObservabilityService:
    return AgentObservabilityService(
        get_session_factory(),
        get_project_repository(),
        get_settings(),
    )


def build_agent_chat_model(settings: Settings) -> AgentChatModel:
    """只在装配边界选择 provider，业务 Runtime 始终依赖统一端口。"""

    if not settings.agent_enabled:
        raise ValueError("AGENT_ENABLED=false，不能初始化治理 Agent 模型")
    if settings.agent_provider == "bailian":
        api_key = settings.bailian_api_key
        return BailianAgentChatModel(
            api_key=api_key.get_secret_value() if api_key else "",
            base_url=settings.bailian_base_url,
            model_id=settings.bailian_agent_model,
            connect_timeout_seconds=settings.bailian_connect_timeout_seconds,
            read_timeout_seconds=max(
                settings.bailian_read_timeout_seconds,
                settings.agent_max_wall_seconds,
            ),
        )
    return BedrockAgentChatModel(
        model_id=settings.bedrock_agent_model_id,
        region=settings.aws_region,
        connect_timeout_seconds=settings.bedrock_connect_timeout_seconds,
        read_timeout_seconds=max(
            settings.bedrock_read_timeout_seconds,
            settings.agent_max_wall_seconds,
        ),
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
            settings.llm_provider,
            (
                settings.bailian_embedding_model
                if settings.llm_provider == "bailian"
                else settings.bedrock_embedding_model_id
            ),
            settings.embedding_dimension,
        ),
    )


@lru_cache(maxsize=1)
def get_query_embedding_generator() -> EmbeddingGenerator:
    settings = get_settings()
    if settings.llm_provider == "bailian":
        api_key = settings.bailian_api_key
        return BailianEmbeddingGenerator(
            api_key=api_key.get_secret_value() if api_key else "",
            base_url=settings.bailian_base_url,
            model_id=settings.bailian_embedding_model,
            dimension=settings.bailian_embedding_dimension,
            connect_timeout_seconds=settings.bailian_connect_timeout_seconds,
            read_timeout_seconds=settings.bailian_read_timeout_seconds,
        )
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
