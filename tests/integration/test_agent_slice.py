"""R15a 治理 Agent 的持久化、租约和证据闭环测试。"""

import json
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.agent import (
    AgentConversationService,
    AgentRunIdentityResolver,
)
from experiment_guardian.application.agent_runtime import (
    AgentRunProcessor,
    GovernanceAgentRuntime,
)
from experiment_guardian.application.agent_tools import AgentToolRegistry
from experiment_guardian.application.errors import (
    AuthorizationError,
    InputValidationError,
)
from experiment_guardian.application.ports import AgentChatModel
from experiment_guardian.application.services import ProjectAdministrationService
from experiment_guardian.application.web_auth import OWNER_WEB_SCOPES
from experiment_guardian.core.config import Settings
from experiment_guardian.domain.agent import (
    AgentChatMessage,
    AgentMessageCreateRequest,
    AgentModelEvent,
    AgentModelUsage,
    AgentThreadCreateRequest,
    AgentToolRequest,
    AgentToolSpec,
)
from experiment_guardian.domain.enums import AgentRunStatus
from experiment_guardian.infrastructure.models import (
    AgentCitation,
    AgentMessage,
    AgentRun,
    AgentRunEvent,
    AuditLog,
    WebSession,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyAgentRepository,
    SqlAlchemyProjectRepository,
)
from tests.integration.test_foundation_slice import initial_request, seed_owner


class ScriptedAgentModel(AgentChatModel):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider(self) -> str:
        return "scripted"

    @property
    def model_id(self) -> str:
        return "scripted-r15a"

    def stream_turn(
        self,
        *,
        messages: Sequence[AgentChatMessage],
        tools: Sequence[AgentToolSpec],
        tool_choice: str,
        max_output_tokens: int,
        response_json: bool = False,
    ) -> Iterator[AgentModelEvent]:
        del tools, max_output_tokens, response_json
        self.calls += 1
        if self.calls == 1:
            assert tool_choice == "auto"
            yield AgentModelEvent(
                event_type="tool_call",
                tool_call=AgentToolRequest(
                    call_id="call-status",
                    name="project_status_get_v1",
                    arguments={},
                ),
            )
            yield AgentModelEvent(event_type="completed", finish_reason="tool_calls")
            return
        assert any(item.role == "tool" for item in messages)
        answer = {
            "answer_markdown": "当前项目正式目标已读取。",
            "sections": [
                {
                    "evidence_kind": "CONFIRMED_FACT",
                    "title": "项目目标",
                    "content": "以当前正式 Context 为准。",
                    "citation_ids": ["ev_1_1"],
                }
            ],
            "citations": ["ev_1_1"],
            "follow_up_required": False,
        }
        yield AgentModelEvent(event_type="text_delta", text=json.dumps(answer))
        yield AgentModelEvent(
            event_type="usage",
            usage=AgentModelUsage(input_tokens=20, output_tokens=10),
        )
        yield AgentModelEvent(
            event_type="completed",
            finish_reason="stop",
            provider_request_id="scripted-2",
        )


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        deployment_mode="cloud",
        web_auth_mode="cognito",
        object_storage_backend="aws_s3",
        queue_backend="sqs",
        llm_provider="bedrock",
        agent_enabled=True,
        agent_provider="bailian",
        bailian_api_key=SecretStr("test"),
        bailian_base_url="https://bailian.example/v1",
        bailian_agent_model="qwen-agent",
    )


def _setup(
    factory: sessionmaker[Session],
) -> tuple[AgentConversationService, object, object, Settings]:
    owner = seed_owner(factory)
    projects = SqlAlchemyProjectRepository()
    initialized = ProjectAdministrationService(factory, projects).initialize_project(
        identity=owner,
        idempotency_key=uuid4(),
        request=initial_request(),
    )
    now = datetime.now(UTC)
    session_id = uuid4()
    with factory() as session, session.begin():
        session.add(
            WebSession(
                id=session_id,
                user_id=owner.user_id,
                team_id=owner.team_id,
                session_hash="a" * 64,
                authenticated_at=now,
                reauthenticated_at=now,
                last_seen_at=now,
                absolute_expires_at=now + timedelta(hours=8),
            )
        )
    identity = owner.__class__(
        user_id=owner.user_id,
        team_id=owner.team_id,
        token_id=session_id,
        scopes=OWNER_WEB_SCOPES,
        authentication_method="WEB_SESSION",
        recent_authentication=True,
    )
    settings = _settings()
    return (
        AgentConversationService(
            factory, projects, SqlAlchemyAgentRepository(), settings
        ),
        identity,
        initialized,
        settings,
    )


def test_agent_run_is_idempotent_and_persists_verified_answer(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    service, identity, initialized, settings = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    thread = service.create_thread(
        project_id=project_id,
        identity=identity,
        request=AgentThreadCreateRequest(),
    )
    key = uuid4()
    request = AgentMessageCreateRequest(content="当前项目目标是什么？")
    receipt = service.create_message(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
        idempotency_key=key,
        request=request,
    )
    replay = service.create_message(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
        idempotency_key=key,
        request=request,
    )
    assert replay.run_id == receipt.run_id

    repository = SqlAlchemyAgentRepository()
    runtime = GovernanceAgentRuntime(
        plan_check_session_factory,
        repository,
        AgentToolRegistry(
            plan_check_session_factory, SqlAlchemyProjectRepository()
        ),
        ScriptedAgentModel(),
        settings,
    )
    processor = AgentRunProcessor(
        plan_check_session_factory,
        repository,
        AgentRunIdentityResolver(settings),
        runtime,
        settings,
        worker_id="test-agent-worker",
    )
    assert processor.process_once()

    run = service.get_run(
        project_id=project_id, run_id=receipt.run_id, identity=identity
    )
    view = service.get_thread(
        project_id=project_id, thread_id=thread.thread_id, identity=identity
    )
    assert run.status is AgentRunStatus.SUCCEEDED
    assert len(view.messages) == 2
    assert view.messages[-1].content == "当前项目正式目标已读取。"
    assert view.messages[-1].citations[0].evidence_id == "ev_1_1"
    with plan_check_session_factory() as session:
        assert session.scalar(select(AgentCitation)) is not None
        events = list(
            session.scalars(
                select(AgentRunEvent)
                .where(AgentRunEvent.run_id == receipt.run_id)
                .order_by(AgentRunEvent.sequence)
            ).all()
        )
        assert [item.event_type for item in events] == [
            "run.queued",
            "run.started",
            "tool.started",
            "tool.completed",
            "answer.delta",
            "run.completed",
        ]


def test_expired_agent_lease_increments_generation_and_blocks_stale_owner(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    service, identity, initialized, settings = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    thread = service.create_thread(
        project_id=project_id,
        identity=identity,
        request=AgentThreadCreateRequest(),
    )
    receipt = service.create_message(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
        idempotency_key=uuid4(),
        request=AgentMessageCreateRequest(content="状态"),
    )
    repository = SqlAlchemyAgentRepository()
    with plan_check_session_factory() as session, session.begin():
        first = repository.claim_next(
            session, worker_id="worker-1", lease_seconds=settings.agent_run_lease_seconds
        )
    assert first is not None
    with plan_check_session_factory() as session, session.begin():
        run = session.get(AgentRun, receipt.run_id)
        assert run is not None
        run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with plan_check_session_factory() as session, session.begin():
        second = repository.claim_next(
            session, worker_id="worker-2", lease_seconds=settings.agent_run_lease_seconds
        )
    assert second is not None and second.generation == first.generation + 1
    with plan_check_session_factory() as session, session.begin():
        assert not repository.renew_lease(
            session, claim=first, lease_seconds=settings.agent_run_lease_seconds
        )
        assert repository.renew_lease(
            session, claim=second, lease_seconds=settings.agent_run_lease_seconds
        )
    with plan_check_session_factory() as session:
        assert session.scalar(
            select(AgentMessage).where(AgentMessage.role == "ASSISTANT")
        ) is None


def test_agent_conversations_require_web_session_and_validate_cursor(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    service, identity, initialized, _ = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    bearer_identity = identity.__class__(
        user_id=identity.user_id,
        team_id=identity.team_id,
        token_id=uuid4(),
        scopes=identity.scopes,
        authentication_method="ACCESS_TOKEN",
        recent_authentication=False,
    )
    with pytest.raises(AuthorizationError, match="Web Session"):
        service.list_threads(
            project_id=project_id,
            identity=bearer_identity,
            archived=False,
            cursor=None,
            limit=20,
        )
    with pytest.raises(InputValidationError, match="cursor"):
        service.list_threads(
            project_id=project_id,
            identity=identity,
            archived=False,
            cursor="a",
            limit=20,
        )


def test_agent_retry_writes_user_audit_record(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    service, identity, initialized, _ = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    thread = service.create_thread(
        project_id=project_id,
        identity=identity,
        request=AgentThreadCreateRequest(),
    )
    original = service.create_message(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
        idempotency_key=uuid4(),
        request=AgentMessageCreateRequest(content="读取项目状态"),
    )
    with plan_check_session_factory() as session, session.begin():
        run = session.get(AgentRun, original.run_id)
        assert run is not None
        run.status = AgentRunStatus.FAILED
        run.completed_at = datetime.now(UTC)

    retried = service.retry_run(
        project_id=project_id,
        run_id=original.run_id,
        identity=identity,
        idempotency_key=uuid4(),
    )
    view = service.get_thread(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
    )
    assert view.messages[-1].run_id == retried.run_id
    with plan_check_session_factory() as session:
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "agent.run.retried",
                AuditLog.target_id == retried.run_id,
            )
        )
        assert audit is not None
        assert audit.actor_id == identity.user_id
