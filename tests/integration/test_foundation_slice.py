"""认证、初始化事务和正式上下文读取的纵向验收测试。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian import admin_cli
from experiment_guardian.api.dependencies import require_api_identity
from experiment_guardian.api.routes import projects as projects_route
from experiment_guardian.application.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    InputValidationError,
)
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.services import (
    GuardianApplication,
    ProjectAdministrationService,
)
from experiment_guardian.domain.administration import ProjectInitializeRequest
from experiment_guardian.domain.enums import (
    ConstraintSource,
    ProtectionLevel,
    TeamRole,
    TokenAudience,
    VerificationStatus,
)
from experiment_guardian.infrastructure.models import (
    AccessToken,
    AuditLog,
    IdempotencyRecord,
    Project,
    ProtectedParameter,
    Team,
    TeamMember,
    User,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyPlanCheckRepository,
    SqlAlchemyProjectRepository,
)
from experiment_guardian.infrastructure.security import SqlAlchemyTokenService, token_digest
from experiment_guardian.main import create_app


def initial_request() -> ProjectInitializeRequest:
    return ProjectInitializeRequest.model_validate(
        {
            "project": {
                "name": "NTU60 Governance",
                "description": "P0 demo",
                "repository_url": "https://example.invalid/research.git",
            },
            "context": {
                "goal": "验证融合系数变化",
                "non_goals": ["不自动训练"],
                "mainline_model": "shift-gcn",
                "baseline": {"checkpoint": "baseline.pt"},
                "dataset": "NTU60",
                "protocol": "40/20",
                "primary_metric": {"name": "top1", "higher_is_better": True},
                "default_seeds": [1],
                "active_branch": "main",
                "active_config": {
                    "dataset": {"protocol": "40/20"},
                    "model": {"backbone": "shift-gcn", "fusion": 0.2},
                },
                "deprecated_items": [],
                "key_decisions": ["protocol 固定为 40/20"],
                "change_reason": "创建首个正式上下文",
            },
            "intent": {
                "name": "fusion sweep",
                "objective": "验证 fusion=0.3",
                "hypothesis": "适度提高融合系数可以提升准确率",
                "allowed_variables": ["model.fusion"],
                "controlled_variables": ["dataset.protocol", "model.backbone"],
                "expected_outputs": ["top1"],
                "acceptance_criteria": ["配置可追溯"],
                "original_message": "只修改 fusion coefficient",
            },
            "constraints": [
                {
                    "parameter_path": "dataset.protocol",
                    "protection_level": "LOCKED",
                    "expected_value": "40/20",
                    "reason": "正式协议",
                    "original_message": "protocol 固定为 40/20",
                },
                {
                    "parameter_path": "model.fusion",
                    "protection_level": "EXPERIMENT_VARIABLE",
                    "expected_value": 0.2,
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "reason": "本轮实验变量",
                    "original_message": "允许修改 fusion coefficient",
                },
            ],
        }
    )


def seed_owner(factory: sessionmaker[Session]) -> RequestIdentity:
    user_id, team_id, token_id = uuid4(), uuid4(), uuid4()
    with factory() as session, session.begin():
        session.add(User(id=user_id, name="Owner", email="owner@example.com"))
        session.flush()
        session.add(Team(id=team_id, name="Vision Lab", owner_id=user_id))
        session.flush()
        session.add(TeamMember(team_id=team_id, user_id=user_id, role=TeamRole.OWNER))
    return RequestIdentity(
        user_id=user_id,
        team_id=team_id,
        token_id=token_id,
        scopes=frozenset({"project:initialize"}),
    )


def services(
    factory: sessionmaker[Session],
) -> tuple[ProjectAdministrationService, GuardianApplication]:
    repository = SqlAlchemyProjectRepository()
    return (
        ProjectAdministrationService(factory, repository),
        GuardianApplication(factory, repository, SqlAlchemyPlanCheckRepository()),
    )


def test_token_hash_expiry_audience_and_revocation(
    foundation_session_factory: sessionmaker[Session],
) -> None:
    identity = seed_owner(foundation_session_factory)
    token_service = SqlAlchemyTokenService(foundation_session_factory)
    with foundation_session_factory() as session, session.begin():
        issued = token_service.issue(
            session,
            user_id=identity.user_id,
            team_id=identity.team_id,
            project_id=None,
            audience=TokenAudience.API,
            name="test-admin",
            scopes={"project:initialize"},
            lifetime_days=7,
            created_by=identity.user_id,
        )

    authenticated = token_service.authenticate(issued.raw_token, audience=TokenAudience.API)
    assert authenticated.user_id == identity.user_id
    with foundation_session_factory() as session:
        stored = session.get(AccessToken, issued.token_id)
        assert stored is not None
        assert stored.token_hash == token_digest(issued.raw_token)
        assert issued.raw_token not in stored.token_hash

    with pytest.raises(AuthenticationError):
        token_service.authenticate(issued.raw_token, audience=TokenAudience.MCP)

    with foundation_session_factory() as session, session.begin():
        stored = session.get(AccessToken, issued.token_id)
        assert stored is not None
        stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(AuthenticationError):
        token_service.authenticate(issued.raw_token, audience=TokenAudience.API)

    with foundation_session_factory() as session, session.begin():
        rotated = token_service.issue(
            session,
            user_id=identity.user_id,
            team_id=identity.team_id,
            project_id=None,
            audience=TokenAudience.API,
            name="test-admin",
            scopes={"project:initialize"},
            lifetime_days=7,
            created_by=identity.user_id,
        )
    with pytest.raises(AuthenticationError):
        token_service.authenticate(issued.raw_token, audience=TokenAudience.API)
    token_service.authenticate(rotated.raw_token, audience=TokenAudience.API)

    with foundation_session_factory() as session, session.begin():
        token_service.revoke(session, rotated.token_id)
    with pytest.raises(AuthenticationError):
        token_service.authenticate(rotated.raw_token, audience=TokenAudience.API)


def test_issue_mcp_token_cli_grants_plan_check_scope(
    foundation_session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = seed_owner(foundation_session_factory)
    administration, _ = services(foundation_session_factory)
    initialized = administration.initialize_project(
        identity=identity,
        idempotency_key=uuid4(),
        request=initial_request(),
    )
    token_service = SqlAlchemyTokenService(foundation_session_factory)
    monkeypatch.setattr(admin_cli, "get_session_factory", lambda: foundation_session_factory)
    monkeypatch.setattr(admin_cli, "get_token_service", lambda: token_service)

    result = admin_cli._issue_mcp_token(
        SimpleNamespace(
            owner_email="owner@example.com",
            project_id=str(initialized.project_id),
            token_name="plan-check-agent",
            ttl_days=30,
        )
    )

    authenticated = token_service.authenticate(
        str(result["access_token"]), audience=TokenAudience.MCP
    )
    assert authenticated.project_id == initialized.project_id
    assert authenticated.scopes == frozenset(
        {"project:read", "experiment:check", "manifest:create"}
    )


def test_project_initialization_is_atomic_idempotent_and_readable(
    foundation_session_factory: sessionmaker[Session],
) -> None:
    api_identity = seed_owner(foundation_session_factory)
    administration, guardian = services(foundation_session_factory)
    request = initial_request()
    key = uuid4()

    first = administration.initialize_project(
        identity=api_identity, idempotency_key=key, request=request
    )
    replay = administration.initialize_project(
        identity=api_identity, idempotency_key=key, request=request
    )
    assert replay.project_id == first.project_id

    mcp_identity = RequestIdentity(
        user_id=api_identity.user_id,
        team_id=api_identity.team_id,
        token_id=uuid4(),
        project_id=first.project_id,
        scopes=frozenset({"project:read"}),
    )
    bundle = guardian.project_get_context(project_id=first.project_id, identity=mcp_identity)
    assert bundle.context.version == 1
    assert bundle.active_intent is not None and bundle.active_intent.version == 1
    assert [item.parameter_path for item in bundle.constraints] == [
        "dataset.protocol",
        "model.fusion",
    ]
    assert bundle.context_payload.protocol == "40/20"

    with foundation_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Project)) == 1
        assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 1

    changed = request.model_copy(deep=True)
    changed.project.name = "different"
    with pytest.raises(ConflictError, match="不同请求"):
        administration.initialize_project(
            identity=api_identity, idempotency_key=key, request=changed
        )


def test_context_read_filters_pending_constraints_and_enforces_project_binding(
    foundation_session_factory: sessionmaker[Session],
) -> None:
    api_identity = seed_owner(foundation_session_factory)
    administration, guardian = services(foundation_session_factory)
    initialized = administration.initialize_project(
        identity=api_identity, idempotency_key=uuid4(), request=initial_request()
    )
    with foundation_session_factory() as session, session.begin():
        confirmed = session.scalar(
            select(ProtectedParameter).where(
                ProtectedParameter.project_id == initialized.project_id
            )
        )
        assert confirmed is not None
        session.add(
            ProtectedParameter(
                project_id=confirmed.project_id,
                context_id=confirmed.context_id,
                context_version=confirmed.context_version,
                version=2,
                parameter_path="model.pending",
                protection_level=ProtectionLevel.LOCKED,
                expected_value="candidate",
                reason="尚未确认",
                source_type=ConstraintSource.INFERRED,
                verification_status=VerificationStatus.PENDING,
                original_message="保持其他模块不变",
                inference_basis="模型推断",
                confidence=0.5,
                created_by=api_identity.user_id,
                active=True,
            )
        )
        session.add(
            ProtectedParameter(
                project_id=confirmed.project_id,
                context_id=confirmed.context_id,
                context_version=confirmed.context_version,
                version=2,
                parameter_path="model.confirmed_inference",
                protection_level=ProtectionLevel.APPROVAL_REQUIRED,
                expected_value="kept",
                reason="用户确认后的推断约束",
                source_type=ConstraintSource.INFERRED,
                verification_status=VerificationStatus.CONFIRMED,
                original_message="保持该模块不变",
                inference_basis="从自然语言意图推断",
                confidence=0.8,
                created_by=api_identity.user_id,
                confirmed_by=api_identity.user_id,
                confirmed_at=datetime.now(UTC),
                active=True,
            )
        )

    identity = RequestIdentity(
        user_id=api_identity.user_id,
        team_id=api_identity.team_id,
        token_id=uuid4(),
        project_id=initialized.project_id,
        scopes=frozenset({"project:read"}),
    )
    bundle = guardian.project_get_context(project_id=initialized.project_id, identity=identity)
    assert "model.pending" not in {item.parameter_path for item in bundle.constraints}
    inferred = next(
        item for item in bundle.constraints if item.parameter_path == "model.confirmed_inference"
    )
    assert inferred.source_type is ConstraintSource.INFERRED
    assert inferred.inference_basis == "从自然语言意图推断"

    with pytest.raises(AuthorizationError, match="未绑定"):
        guardian.project_get_context(project_id=uuid4(), identity=identity)


def test_researcher_cannot_initialize_and_invalid_formal_config_writes_nothing(
    foundation_session_factory: sessionmaker[Session],
) -> None:
    owner_identity = seed_owner(foundation_session_factory)
    administration, _ = services(foundation_session_factory)
    researcher_id = uuid4()
    with foundation_session_factory() as session, session.begin():
        session.add(User(id=researcher_id, name="Researcher", email="researcher@example.com"))
        session.flush()
        session.add(
            TeamMember(
                team_id=owner_identity.team_id,
                user_id=researcher_id,
                role=TeamRole.RESEARCHER,
            )
        )
    researcher = RequestIdentity(
        user_id=researcher_id,
        team_id=owner_identity.team_id,
        token_id=uuid4(),
        scopes=frozenset({"project:initialize"}),
    )
    with pytest.raises(AuthorizationError):
        administration.initialize_project(
            identity=researcher,
            idempotency_key=uuid4(),
            request=initial_request(),
        )

    invalid = initial_request().model_copy(deep=True)
    invalid.constraints[0].expected_value = "48/12"
    with pytest.raises(InputValidationError, match="expected_value"):
        administration.initialize_project(
            identity=owner_identity,
            idempotency_key=uuid4(),
            request=invalid,
        )
    with foundation_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Project)) == 0


@pytest.mark.asyncio
async def test_initialize_api_requires_auth_and_supports_idempotent_replay(
    foundation_session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = seed_owner(foundation_session_factory)
    administration, _ = services(foundation_session_factory)
    app = create_app()
    monkeypatch.setattr(
        projects_route, "get_project_administration_service", lambda: administration
    )
    transport = httpx.ASGITransport(app=app)
    request = initial_request().model_dump(mode="json")
    key = str(uuid4())

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unauthorized = await client.post(
            "/api/v1/projects/initialize",
            headers={"Idempotency-Key": key},
            json=request,
        )
        assert unauthorized.status_code == 401

        async def override_identity() -> RequestIdentity:
            return identity

        app.dependency_overrides[require_api_identity] = override_identity
        first = await client.post(
            "/api/v1/projects/initialize",
            headers={"Idempotency-Key": key},
            json=request,
        )
        replay = await client.post(
            "/api/v1/projects/initialize",
            headers={"Idempotency-Key": key},
            json=request,
        )

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["project_id"] == first.json()["project_id"]
