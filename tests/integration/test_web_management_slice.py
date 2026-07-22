"""R14a Web 管理 API 服务的版本和授权测试。"""

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.errors import (
    ConflictError,
    RecentAuthenticationRequiredError,
)
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.services import ProjectAdministrationService
from experiment_guardian.application.web_management import WebManagementService
from experiment_guardian.domain.enums import ContextStatus, IntentStatus, VerificationStatus
from experiment_guardian.domain.web_management import PolicyPublishRequest
from experiment_guardian.infrastructure.models import (
    ExperimentIntent,
    ProjectContext,
    ProtectedParameter,
)
from experiment_guardian.infrastructure.repositories import SqlAlchemyProjectRepository
from experiment_guardian.infrastructure.storage import UnconfiguredArtifactStorage
from tests.integration.test_foundation_slice import initial_request, seed_owner


def _setup(
    factory: sessionmaker[Session], *, recent: bool = True
) -> tuple[WebManagementService, RequestIdentity, object]:
    owner = seed_owner(factory)
    repository = SqlAlchemyProjectRepository()
    initialized = ProjectAdministrationService(factory, repository).initialize_project(
        identity=owner,
        idempotency_key=uuid4(),
        request=initial_request(),
    )
    web_identity = RequestIdentity(
        user_id=owner.user_id,
        team_id=owner.team_id,
        token_id=uuid4(),
        scopes=frozenset(
            {
                "project:read",
                "project:write",
                "plan:read",
                "submission:read",
                "experiment:read",
                "artifact:read",
            }
        ),
        authentication_method="WEB_SESSION",
        recent_authentication=recent,
    )
    return (
        WebManagementService(factory, repository, UnconfiguredArtifactStorage(), 900),
        web_identity,
        initialized,
    )


def _publish_request() -> PolicyPublishRequest:
    initial = initial_request()
    context = initial.context.model_copy(deep=True)
    context.goal = "验证融合系数与新主线的一致性"
    context.change_reason = "R14 Web 发布第二版正式策略"
    intent = initial.intent.model_copy(deep=True)
    intent.objective = "在第二版上下文中验证 fusion=0.3"
    return PolicyPublishRequest(
        expected_context_version=1,
        context=context,
        intent=intent,
        constraints=initial.constraints,
    )


def test_policy_publish_versions_and_supersedes_without_overwriting_history(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    service, identity, initialized = _setup(plan_check_session_factory)
    key = uuid4()
    result = service.publish_policy(
        project_id=initialized.project_id,  # type: ignore[attr-defined]
        identity=identity,
        idempotency_key=key,
        request=_publish_request(),
    )
    replay = service.publish_policy(
        project_id=initialized.project_id,  # type: ignore[attr-defined]
        identity=identity,
        idempotency_key=key,
        request=_publish_request(),
    )
    assert replay == result
    assert result.previous_context_version == 1
    assert result.context_bundle.context.version == 2
    assert result.context_bundle.active_intent is not None
    assert result.context_bundle.active_intent.version == 2

    with plan_check_session_factory() as session:
        contexts = session.scalars(
            select(ProjectContext).order_by(ProjectContext.version)
        ).all()
        intents = session.scalars(
            select(ExperimentIntent).order_by(ExperimentIntent.version)
        ).all()
        constraints = session.scalars(
            select(ProtectedParameter).order_by(
                ProtectedParameter.context_version, ProtectedParameter.parameter_path
            )
        ).all()
        assert [item.status for item in contexts] == [
            ContextStatus.SUPERSEDED,
            ContextStatus.ACTIVE,
        ]
        assert [item.status for item in intents] == [IntentStatus.CLOSED, IntentStatus.ACTIVE]
        assert all(
            item.verification_status is VerificationStatus.SUPERSEDED
            for item in constraints
            if item.context_version == 1
        )
        assert all(item.active for item in constraints if item.context_version == 2)
        assert session.scalar(select(func.count()).select_from(ProjectContext)) == 2


def test_policy_publish_requires_fresh_context_and_recent_cognito_authentication(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    service, identity, initialized = _setup(plan_check_session_factory, recent=False)
    with pytest.raises(RecentAuthenticationRequiredError):
        service.publish_policy(
            project_id=initialized.project_id,  # type: ignore[attr-defined]
            identity=identity,
            idempotency_key=uuid4(),
            request=_publish_request(),
        )

    fresh_identity = RequestIdentity(
        user_id=identity.user_id,
        team_id=identity.team_id,
        token_id=identity.token_id,
        scopes=identity.scopes,
        authentication_method="WEB_SESSION",
        recent_authentication=True,
    )
    request = _publish_request()
    request.expected_context_version = 2
    with pytest.raises(ConflictError, match="当前为 1"):
        service.publish_policy(
            project_id=initialized.project_id,  # type: ignore[attr-defined]
            identity=fresh_identity,
            idempotency_key=uuid4(),
            request=request,
        )


def test_web_project_settings_expose_current_and_history(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    service, identity, initialized = _setup(plan_check_session_factory)
    projects = service.list_projects(identity)
    settings = service.get_settings(
        project_id=initialized.project_id, identity=identity  # type: ignore[attr-defined]
    )
    assert len(projects.items) == 1
    assert settings.current.context.version == 1
    assert settings.context_history[0].confirmed_by == identity.user_id
