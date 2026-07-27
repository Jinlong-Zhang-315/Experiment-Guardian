"""R15c 治理草稿的权限、revision、影响模拟和正式版本隔离验收。"""

import json
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.agent import AgentRunIdentityResolver
from experiment_guardian.application.agent_runtime import (
    AgentRunProcessor,
    GovernanceAgentRuntime,
)
from experiment_guardian.application.agent_tools import AgentToolRegistry
from experiment_guardian.application.errors import ConflictError, ResourceNotFoundError
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.policy_drafts import PolicyDraftService
from experiment_guardian.application.ports import AgentChatModel
from experiment_guardian.application.services import GuardianApplication
from experiment_guardian.application.web_management import WebManagementService
from experiment_guardian.domain.agent import (
    AgentChatMessage,
    AgentMessageCreateRequest,
    AgentModelEvent,
    AgentResponseFormat,
    AgentThreadCreateRequest,
    AgentToolRequest,
    AgentToolSpec,
)
from experiment_guardian.domain.enums import (
    AgentCallStatus,
    ApprovalStatus,
    CheckResult,
    PolicyDraftFreshness,
    PolicyDraftReadiness,
    ProtectionLevel,
    TeamRole,
)
from experiment_guardian.domain.policy_draft import (
    PolicyDraftCandidate,
    PolicyDraftCreateInput,
    PolicyDraftRevisionInput,
)
from experiment_guardian.infrastructure.models import (
    AgentPolicyDraft,
    AgentPolicyDraftRevision,
    AgentToolCall,
    ExperimentIntent,
    PlanCheck,
    ProjectContext,
    ProtectedParameter,
    TeamMember,
    User,
    WebSession,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyAgentRepository,
    SqlAlchemyPlanCheckRepository,
    SqlAlchemyProjectRepository,
)
from experiment_guardian.infrastructure.storage import UnconfiguredArtifactStorage
from tests.integration.test_agent_slice import _setup
from tests.integration.test_foundation_slice import initial_request
from tests.integration.test_plan_check_slice import command
from tests.integration.test_web_management_slice import _publish_request


def _candidate() -> PolicyDraftCandidate:
    request = initial_request()
    return PolicyDraftCandidate(
        context=request.context,
        intent=request.intent,
        constraints=request.constraints,
    )


def _agent_source(
    factory: sessionmaker[Session],
    conversation: object,
    identity: RequestIdentity,
    project_id: UUID,
) -> tuple[UUID, UUID]:
    thread = conversation.create_thread(  # type: ignore[attr-defined]
        project_id=project_id,
        identity=identity,
        request=AgentThreadCreateRequest(),
    )
    receipt = conversation.create_message(  # type: ignore[attr-defined]
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
        idempotency_key=uuid4(),
        request=AgentMessageCreateRequest(content="创建一个治理候选草稿"),
    )
    with factory() as session, session.begin():
        call = AgentToolCall(
            run_id=receipt.run_id,
            generation=0,
            call_id=f"draft-{uuid4()}",
            sequence=1,
            tool_name="policy_draft_create_v1",
            tool_version="1",
            status=AgentCallStatus.RUNNING,
            arguments={},
            arguments_hash="a" * 64,
            started_at=datetime.now(UTC),
        )
        session.add(call)
        session.flush()
        return receipt.run_id, call.id


def _create_request(initialized: object, candidate: PolicyDraftCandidate) -> PolicyDraftCreateInput:
    bundle = initialized.context_bundle  # type: ignore[attr-defined]
    assert bundle.active_intent is not None
    return PolicyDraftCreateInput(
        base_context_id=bundle.context.context_id,
        base_context_version=bundle.context.version,
        base_intent_id=bundle.active_intent.intent_id,
        base_intent_version=bundle.active_intent.version,
        candidate=candidate,
        change_summary="调整候选项目目标",
    )


class _ScriptedPolicyDraftModel(AgentChatModel):
    def __init__(self, request: PolicyDraftCreateInput) -> None:
        self._request = request
        self.calls = 0

    @property
    def provider(self) -> str:
        return "bailian"

    @property
    def model_id(self) -> str:
        return "qwen-agent"

    def stream_turn(
        self,
        *,
        messages: Sequence[AgentChatMessage],
        tools: Sequence[AgentToolSpec],
        tool_choice: str,
        max_output_tokens: int,
        response_format: AgentResponseFormat | None = None,
    ) -> Iterator[AgentModelEvent]:
        del messages, tool_choice, max_output_tokens, response_format
        assert "policy_draft_create_v1" in {item.name for item in tools}
        self.calls += 1
        if self.calls == 1:
            yield AgentModelEvent(
                event_type="tool_call",
                tool_call=AgentToolRequest(
                    call_id="read-policy",
                    name="project_status_get_v1",
                    arguments={},
                ),
            )
            yield AgentModelEvent(event_type="completed", finish_reason="tool_calls")
            return
        if self.calls == 2:
            yield AgentModelEvent(
                event_type="tool_call",
                tool_call=AgentToolRequest(
                    call_id="create-draft",
                    name="policy_draft_create_v1",
                    arguments=self._request.model_dump(mode="json"),
                ),
            )
            yield AgentModelEvent(event_type="completed", finish_reason="tool_calls")
            return
        answer = {
            "answer_markdown": "已创建未生效的治理候选草稿。",
            "sections": [
                {
                    "evidence_kind": "CANDIDATE_DRAFT",
                    "title": "治理草稿",
                    "content": "候选内容尚未发布，正式策略未改变。",
                    "citation_ids": ["ev_2_1"],
                }
            ],
            "citations": ["ev_2_1"],
            "follow_up_required": False,
        }
        yield AgentModelEvent(event_type="text_delta", text=json.dumps(answer))
        yield AgentModelEvent(event_type="completed", finish_reason="stop")


def test_agent_policy_draft_is_append_only_idempotent_and_role_scoped(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    conversation, identity, initialized, _ = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    run_id, tool_call_id = _agent_source(
        plan_check_session_factory, conversation, identity, project_id
    )
    projects = SqlAlchemyProjectRepository()
    service = PolicyDraftService(plan_check_session_factory, projects)
    candidate = _candidate()
    candidate.context.goal = "验证 R15c 候选目标"
    request = _create_request(initialized, candidate)

    created = service.create_from_agent(
        project_id=project_id,
        identity=identity,
        run_id=run_id,
        tool_call_id=tool_call_id,
        request=request,
    )
    replay = service.create_from_agent(
        project_id=project_id,
        identity=identity,
        run_id=run_id,
        tool_call_id=tool_call_id,
        request=request,
    )
    assert replay.summary.draft_id == created.summary.draft_id
    assert created.summary.readiness is PolicyDraftReadiness.READY
    assert created.summary.freshness is PolicyDraftFreshness.CURRENT
    assert created.current.narrative.authoritative is False

    researcher_id = uuid4()
    researcher_session_id = uuid4()
    now = datetime.now(UTC)
    with plan_check_session_factory() as session, session.begin():
        session.add(
            User(
                id=researcher_id,
                name="Researcher",
                email="draft-researcher@example.com",
            )
        )
        session.flush()
        session.add(
            TeamMember(
                team_id=identity.team_id,
                user_id=researcher_id,
                role=TeamRole.RESEARCHER,
            )
        )
        session.add(
            WebSession(
                id=researcher_session_id,
                user_id=researcher_id,
                team_id=identity.team_id,
                session_hash="b" * 64,
                authenticated_at=now,
                reauthenticated_at=now,
                last_seen_at=now,
                absolute_expires_at=now + timedelta(hours=8),
            )
        )
    researcher = RequestIdentity(
        user_id=researcher_id,
        team_id=identity.team_id,
        token_id=researcher_session_id,
        scopes=frozenset({"project:read"}),
        authentication_method="WEB_SESSION",
    )
    with pytest.raises(ResourceNotFoundError):
        service.get_draft(
            project_id=project_id,
            draft_id=created.summary.draft_id,
            identity=researcher,
        )
    assert (
        service.list_drafts(
            project_id=project_id,
            identity=researcher,
            status=created.summary.status,
            cursor=None,
            limit=20,
        ).items
        == []
    )

    researcher_run_id, researcher_tool_call_id = _agent_source(
        plan_check_session_factory,
        conversation,
        researcher,
        project_id,
    )
    researcher_candidate = _candidate()
    researcher_candidate.context.goal = "Researcher 自有候选"
    researcher_draft = service.create_from_agent(
        project_id=project_id,
        identity=researcher,
        run_id=researcher_run_id,
        tool_call_id=researcher_tool_call_id,
        request=_create_request(initialized, researcher_candidate),
    )
    owner_revision = service.revise_from_web(
        project_id=project_id,
        draft_id=researcher_draft.summary.draft_id,
        identity=identity,
        idempotency_key=uuid4(),
        request=PolicyDraftRevisionInput(
            expected_revision=1,
            candidate=researcher_candidate,
            change_summary="Owner 代表项目修订 Researcher 草稿",
            unresolved_ambiguities=[
                {
                    "field_path": "context.goal",
                    "question": "是否保留该目标？",
                    "source_text": "Owner 复核",
                }
            ],
        ),
    )
    assert owner_revision.author_id == identity.user_id
    assert owner_revision.revision == 2

    update = candidate.model_copy(deep=True)
    update.constraints.append(update.constraints[0].model_copy(deep=True))
    revised = service.revise_from_web(
        project_id=project_id,
        draft_id=created.summary.draft_id,
        identity=identity,
        idempotency_key=uuid4(),
        request=PolicyDraftRevisionInput(
            expected_revision=1,
            candidate=update,
            change_summary="保留冲突以便 Owner 修正",
        ),
    )
    assert revised.revision == 2
    assert revised.validation.readiness is PolicyDraftReadiness.INVALID
    with pytest.raises(ConflictError, match="revision 已变化"):
        service.revise_from_web(
            project_id=project_id,
            draft_id=created.summary.draft_id,
            identity=identity,
            idempotency_key=uuid4(),
            request=PolicyDraftRevisionInput(
                expected_revision=1,
                candidate=candidate,
                change_summary="过期编辑",
            ),
        )

    with plan_check_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AgentPolicyDraft)) == 2
        assert session.scalar(select(func.count()).select_from(AgentPolicyDraftRevision)) == 4
        assert session.scalar(select(func.count()).select_from(ProjectContext)) == 1
        assert session.scalar(select(func.count()).select_from(ExperimentIntent)) == 1
        assert session.scalar(select(func.count()).select_from(ProtectedParameter)) == 2


def test_policy_draft_impact_simulates_pending_plan_without_writing_it(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    conversation, identity, initialized, _ = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    intent = initialized.context_bundle.active_intent  # type: ignore[attr-defined]
    assert intent is not None
    guardian = GuardianApplication(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
        SqlAlchemyPlanCheckRepository(),
    )
    mcp_identity = RequestIdentity(
        user_id=identity.user_id,
        team_id=identity.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"project:read", "experiment:check"}),
    )
    plan = guardian.experiment_check_plan(
        command(project_id=project_id, intent_id=intent.intent_id),
        mcp_identity,
    )
    with plan_check_session_factory() as session, session.begin():
        row = session.get(PlanCheck, plan.plan_check_id)
        assert row is not None
        row.check_result = CheckResult.NEEDS_APPROVAL
        row.approval_status = ApprovalStatus.PENDING

    run_id, tool_call_id = _agent_source(
        plan_check_session_factory, conversation, identity, project_id
    )
    candidate = _candidate()
    candidate.intent.allowed_variables = []
    candidate.constraints[1].protection_level = ProtectionLevel.LOCKED
    service = PolicyDraftService(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
    )
    created = service.create_from_agent(
        project_id=project_id,
        identity=identity,
        run_id=run_id,
        tool_call_id=tool_call_id,
        request=_create_request(initialized, candidate),
    )
    _, impact, _ = service.impact_for_agent(
        project_id=project_id,
        draft_id=created.summary.draft_id,
        revision=None,
        identity=identity,
    )
    assert len(impact.plan_simulations) == 1
    simulation = impact.plan_simulations[0]
    assert simulation.original_check_result is CheckResult.NEEDS_APPROVAL
    assert simulation.simulated_check_result is CheckResult.BLOCKED
    assert simulation.changed is True
    with plan_check_session_factory() as session:
        unchanged = session.get(PlanCheck, plan.plan_check_id)
        assert unchanged is not None
        assert unchanged.check_result is CheckResult.NEEDS_APPROVAL
        assert unchanged.approval_status is ApprovalStatus.PENDING


def test_formal_policy_change_makes_draft_stale_without_mutating_history(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    conversation, identity, initialized, _ = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    run_id, tool_call_id = _agent_source(
        plan_check_session_factory, conversation, identity, project_id
    )
    service = PolicyDraftService(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
    )
    candidate = _candidate()
    candidate.context.goal = "候选目标"
    created = service.create_from_agent(
        project_id=project_id,
        identity=identity,
        run_id=run_id,
        tool_call_id=tool_call_id,
        request=_create_request(initialized, candidate),
    )
    WebManagementService(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
        UnconfiguredArtifactStorage(),
        900,
    ).publish_policy(
        project_id=project_id,
        identity=identity,
        idempotency_key=uuid4(),
        request=_publish_request(),
    )
    stale = service.get_draft(
        project_id=project_id,
        draft_id=created.summary.draft_id,
        identity=identity,
    )
    assert stale.summary.freshness is PolicyDraftFreshness.STALE
    with pytest.raises(ConflictError, match="基准正式版本已变化"):
        service.revise_from_web(
            project_id=project_id,
            draft_id=created.summary.draft_id,
            identity=identity,
            idempotency_key=uuid4(),
            request=PolicyDraftRevisionInput(
                expected_revision=1,
                candidate=candidate,
                change_summary="不允许静默 rebase",
            ),
        )


def test_r15c_agent_tool_catalog_creates_candidate_evidence(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    conversation, identity, initialized, _ = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    run_id, tool_call_id = _agent_source(
        plan_check_session_factory, conversation, identity, project_id
    )
    candidate = _candidate()
    candidate.context.goal = "由 Agent 工具生成候选"
    registry = AgentToolRegistry(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
        PolicyDraftService(
            plan_check_session_factory,
            SqlAlchemyProjectRepository(),
        ),
    )
    result = registry.execute(
        tool_name="policy_draft_create_v1",
        arguments=_create_request(initialized, candidate).model_dump(mode="json"),
        project_id=project_id,
        identity=identity,
        evidence_prefix="ev_1",
        catalog_version="r15c-v1",
        run_id=run_id,
        tool_call_id=tool_call_id,
    )
    assert result.evidence[0].evidence_kind.value == "CANDIDATE_DRAFT"
    assert result.evidence[0].entity_type == "POLICY_DRAFT"


def test_r15c_runtime_reads_formal_bundle_before_creating_candidate(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    conversation, identity, initialized, settings = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    candidate = _candidate()
    candidate.context.goal = "通过完整 Agent Runtime 生成候选"
    model = _ScriptedPolicyDraftModel(_create_request(initialized, candidate))
    thread = conversation.create_thread(
        project_id=project_id,
        identity=identity,
        request=AgentThreadCreateRequest(),
    )
    receipt = conversation.create_message(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
        idempotency_key=uuid4(),
        request=AgentMessageCreateRequest(content="请把项目目标改成新的候选目标"),
    )
    repository = SqlAlchemyAgentRepository()
    draft_service = PolicyDraftService(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
    )
    runtime = GovernanceAgentRuntime(
        plan_check_session_factory,
        repository,
        AgentToolRegistry(
            plan_check_session_factory,
            SqlAlchemyProjectRepository(),
            draft_service,
        ),
        model,
        settings,
    )
    processor = AgentRunProcessor(
        plan_check_session_factory,
        repository,
        AgentRunIdentityResolver(settings),
        runtime,
        settings,
        worker_id="policy-draft-agent-worker",
    )

    assert processor.process_once()
    run = conversation.get_run(
        project_id=project_id,
        run_id=receipt.run_id,
        identity=identity,
    )
    thread_view = conversation.get_thread(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
    )
    assert run.status.value == "SUCCEEDED"
    assert thread_view.messages[-1].sections[0].evidence_kind.value == "CANDIDATE_DRAFT"
    assert thread_view.messages[-1].citations[0].entity_type == "POLICY_DRAFT"
    with plan_check_session_factory() as session:
        calls = list(
            session.scalars(
                select(AgentToolCall)
                .where(AgentToolCall.run_id == receipt.run_id)
                .order_by(AgentToolCall.sequence)
            ).all()
        )
        assert [item.tool_name for item in calls] == [
            "project_status_get_v1",
            "policy_draft_create_v1",
        ]
        assert session.scalar(select(func.count()).select_from(AgentPolicyDraft)) == 1
        assert session.scalar(select(func.count()).select_from(ProjectContext)) == 1
        assert session.scalar(select(func.count()).select_from(ExperimentIntent)) == 1
        assert session.scalar(select(func.count()).select_from(ProtectedParameter)) == 2
