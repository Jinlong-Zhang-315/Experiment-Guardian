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
    ServiceUnavailableError,
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
from experiment_guardian.domain.enums import (
    AgentContextSummaryStatus,
    AgentModelCallPurpose,
    AgentRunStatus,
)
from experiment_guardian.infrastructure.models import (
    AgentCitation,
    AgentContextSummary,
    AgentMessage,
    AgentModelCall,
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


class SummaryAwareAgentModel(AgentChatModel):
    def __init__(self, *, fail_summary: bool = False) -> None:
        self.fail_summary = fail_summary

    @property
    def provider(self) -> str:
        return "scripted"

    @property
    def model_id(self) -> str:
        return "scripted-r15b"

    def stream_turn(
        self,
        *,
        messages: Sequence[AgentChatMessage],
        tools: Sequence[AgentToolSpec],
        tool_choice: str,
        max_output_tokens: int,
        response_json: bool = False,
    ) -> Iterator[AgentModelEvent]:
        del tools, tool_choice, max_output_tokens, response_json
        if messages[0].content.startswith("你负责压缩"):
            if self.fail_summary:
                raise ServiceUnavailableError("summary provider unavailable")
            source = json.loads(messages[1].content.split("\n", 1)[1])
            schema_version = (
                5
                if "schema_version=5" in messages[1].content
                else 4
                if "schema_version=4" in messages[1].content
                else 3
                if "schema_version=3" in messages[1].content
                else 2
                if "schema_version=2" in messages[1].content
                else 1
            )
            payload = {
                "schema_version": schema_version,
                "covered_sequence_from": source["covered_sequence_from"],
                "covered_sequence_to": source["covered_sequence_to"],
                "user_requests_and_context": ["用户持续查询项目状态"],
                "prior_answers_and_analysis": ["此前回答为非权威对话历史"],
                "open_questions": [],
                "source_message_ids": source["source_message_ids"],
                "formal_reference_labels": [],
            }
            if schema_version >= 2:
                payload["draft_references"] = []
            if schema_version >= 3:
                payload["proposal_references"] = []
            yield AgentModelEvent(event_type="text_delta", text=json.dumps(payload))
            yield AgentModelEvent(event_type="completed", finish_reason="stop")
            return
        answer = {
            "answer_markdown": "已收到本轮问题。",
            "sections": [
                {
                    "evidence_kind": "USER_PROVIDED",
                    "title": "本轮输入",
                    "content": "已收到本轮问题。",
                    "citation_ids": [],
                }
            ],
            "citations": [],
            "follow_up_required": False,
        }
        yield AgentModelEvent(event_type="text_delta", text=json.dumps(answer))
        yield AgentModelEvent(event_type="completed", finish_reason="stop")


class CatalogInspectingModel(SummaryAwareAgentModel):
    def stream_turn(
        self,
        *,
        messages: Sequence[AgentChatMessage],
        tools: Sequence[AgentToolSpec],
        tool_choice: str,
        max_output_tokens: int,
        response_json: bool = False,
    ) -> Iterator[AgentModelEvent]:
        assert {item.name for item in tools} == {
            "project_status_get_v1",
            "experiments_list_v1",
            "experiment_get_v1",
            "pending_work_list_v1",
        }
        yield from super().stream_turn(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            max_output_tokens=max_output_tokens,
            response_json=response_json,
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
        AgentConversationService(factory, projects, SqlAlchemyAgentRepository(), settings),
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
        AgentToolRegistry(plan_check_session_factory, SqlAlchemyProjectRepository()),
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

    run = service.get_run(project_id=project_id, run_id=receipt.run_id, identity=identity)
    view = service.get_thread(project_id=project_id, thread_id=thread.thread_id, identity=identity)
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
        assert session.scalar(select(AgentMessage).where(AgentMessage.role == "ASSISTANT")) is None


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


@pytest.mark.parametrize("fail_summary", [False, True])
def test_agent_rolling_summary_is_audited_and_failure_degrades_safely(
    plan_check_session_factory: sessionmaker[Session],
    fail_summary: bool,
) -> None:
    service, identity, initialized, base_settings = _setup(plan_check_session_factory)
    settings = base_settings.model_copy(
        update={
            "agent_recent_message_limit": 2,
            "agent_summary_min_new_messages": 2,
        }
    )
    # Service 只读取 Feature 和 Run 配置；替换后保证新 Run 使用相同阈值。
    service = AgentConversationService(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
        SqlAlchemyAgentRepository(),
        settings,
    )
    project_id = initialized.project_id  # type: ignore[attr-defined]
    thread = service.create_thread(
        project_id=project_id,
        identity=identity,
        request=AgentThreadCreateRequest(),
    )
    repository = SqlAlchemyAgentRepository()
    model = SummaryAwareAgentModel(fail_summary=fail_summary)
    runtime = GovernanceAgentRuntime(
        plan_check_session_factory,
        repository,
        AgentToolRegistry(plan_check_session_factory, SqlAlchemyProjectRepository()),
        model,
        settings,
    )
    processor = AgentRunProcessor(
        plan_check_session_factory,
        repository,
        AgentRunIdentityResolver(settings),
        runtime,
        settings,
        worker_id="summary-agent-worker",
    )
    receipts = []
    for index in range(3):
        receipts.append(
            service.create_message(
                project_id=project_id,
                thread_id=thread.thread_id,
                identity=identity,
                idempotency_key=uuid4(),
                request=AgentMessageCreateRequest(content=f"第 {index + 1} 轮问题"),
            )
        )
        assert processor.process_once()

    final_run = service.get_run(
        project_id=project_id,
        run_id=receipts[-1].run_id,
        identity=identity,
    )
    view = service.get_thread(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
    )
    assert final_run.status is AgentRunStatus.SUCCEEDED
    assert view.messages[-1].sections[0].evidence_kind.value == "USER_PROVIDED"
    with plan_check_session_factory() as session:
        summary = session.scalar(
            select(AgentContextSummary).order_by(AgentContextSummary.created_at.desc())
        )
        summary_call = session.scalar(
            select(AgentModelCall).where(
                AgentModelCall.purpose == AgentModelCallPurpose.CONTEXT_SUMMARY
            )
        )
        assert summary is not None and summary_call is not None
        assert summary.covered_sequence_from == 1
        assert summary.covered_sequence_to == 3
        events = list(
            session.scalars(
                select(AgentRunEvent)
                .where(AgentRunEvent.run_id == receipts[-1].run_id)
                .order_by(AgentRunEvent.sequence)
            ).all()
        )
        event_types = [item.event_type for item in events]
        stored_run = session.get(AgentRun, receipts[-1].run_id)
        assert stored_run is not None
        context_mode = stored_run.context_snapshot["context_mode"]
        second_run = session.get(AgentRun, receipts[1].run_id)
        assert second_run is not None
        assert second_run.context_snapshot["context_mode"] == "DEGRADED_TRIM"
    if fail_summary:
        assert summary.status is AgentContextSummaryStatus.FAILED
        assert view.context_summary is not None
        assert view.context_summary.degraded
        assert "summary.failed" in event_types
        assert context_mode == "DEGRADED_TRIM"
    else:
        assert summary.status is AgentContextSummaryStatus.READY
        assert view.context_summary is not None
        assert not view.context_summary.authoritative
        assert not view.context_summary.degraded
        assert "summary.completed" in event_types
        assert context_mode == "ROLLING_SUMMARY"


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


def test_pending_r15a_run_keeps_original_prompt_and_tool_catalog(
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
        request=AgentMessageCreateRequest(content="旧 Run 继续执行"),
    )
    with plan_check_session_factory() as session, session.begin():
        run = session.get(AgentRun, receipt.run_id)
        assert run is not None
        run.prompt_version = "r15a-v1"
        run.tool_catalog_version = "r15a-v1"
    repository = SqlAlchemyAgentRepository()
    runtime = GovernanceAgentRuntime(
        plan_check_session_factory,
        repository,
        AgentToolRegistry(plan_check_session_factory, SqlAlchemyProjectRepository()),
        CatalogInspectingModel(),
        settings,
    )
    processor = AgentRunProcessor(
        plan_check_session_factory,
        repository,
        AgentRunIdentityResolver(settings),
        runtime,
        settings,
        worker_id="catalog-agent-worker",
    )
    assert processor.process_once()
    assert (
        service.get_run(
            project_id=project_id,
            run_id=receipt.run_id,
            identity=identity,
        ).status
        is AgentRunStatus.SUCCEEDED
    )
