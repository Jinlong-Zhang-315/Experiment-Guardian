"""R15d-a Policy 发布提案的权限、失效与原子确认验收。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.action_proposals import ActionProposalService
from experiment_guardian.application.agent_tools import AgentToolRegistry
from experiment_guardian.application.errors import AuthorizationError, ConflictError
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.policy_drafts import PolicyDraftService
from experiment_guardian.application.web_auth import RESEARCHER_WEB_SCOPES
from experiment_guardian.application.web_management import WebManagementService
from experiment_guardian.domain.action_proposal import (
    ActionProposalConfirmRequest,
    ActionProposalPrepareInput,
)
from experiment_guardian.domain.agent import (
    AgentMessageCreateRequest,
    AgentThreadCreateRequest,
)
from experiment_guardian.domain.enums import (
    ActionProposalConfirmability,
    ActionProposalStatus,
    AgentCallStatus,
    PolicyDraftFreshness,
    TeamRole,
)
from experiment_guardian.domain.policy_draft import PolicyDraftRevisionInput
from experiment_guardian.infrastructure.models import (
    AgentActionProposal,
    AgentToolCall,
    AuditLog,
    IdempotencyRecord,
    ProjectContext,
    TeamMember,
    User,
    WebSession,
)
from experiment_guardian.infrastructure.repositories import SqlAlchemyProjectRepository
from experiment_guardian.infrastructure.storage import UnconfiguredArtifactStorage
from tests.integration.test_agent_slice import _setup
from tests.integration.test_policy_draft_slice import (
    _agent_source,
    _candidate,
    _create_request,
)


def _services(
    factory: sessionmaker[Session],
) -> tuple[PolicyDraftService, ActionProposalService]:
    projects = SqlAlchemyProjectRepository()
    drafts = PolicyDraftService(factory, projects)
    web = WebManagementService(
        factory,
        projects,
        UnconfiguredArtifactStorage(),
        900,
    )
    return drafts, ActionProposalService(factory, projects, drafts, web)


def _proposal_source(
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
        request=AgentMessageCreateRequest(content="准备当前治理草稿的发布提案"),
    )
    with factory() as session, session.begin():
        call = AgentToolCall(
            run_id=receipt.run_id,
            generation=0,
            call_id=f"proposal-{uuid4()}",
            sequence=1,
            tool_name="action_proposal_prepare_v1",
            tool_version="1",
            status=AgentCallStatus.RUNNING,
            arguments={},
            arguments_hash="d" * 64,
            started_at=datetime.now(UTC),
        )
        session.add(call)
        session.flush()
        return receipt.run_id, call.id


def _ready_draft(
    factory: sessionmaker[Session],
    conversation: object,
    identity: RequestIdentity,
    initialized: object,
    goal: str,
) -> tuple[PolicyDraftService, object]:
    project_id = initialized.project_id  # type: ignore[attr-defined]
    drafts, _ = _services(factory)
    run_id, call_id = _agent_source(
        factory,
        conversation,
        identity,
        project_id,
    )
    candidate = _candidate()
    candidate.context.goal = goal
    created = drafts.create_from_agent(
        project_id=project_id,
        identity=identity,
        run_id=run_id,
        tool_call_id=call_id,
        request=_create_request(initialized, candidate),
    )
    return drafts, created


def test_owner_confirms_frozen_policy_proposal_atomically_and_idempotently(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    conversation, identity, initialized, _ = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    drafts, created = _ready_draft(
        plan_check_session_factory,
        conversation,
        identity,
        initialized,
        "R15d 正式候选目标",
    )
    _, proposals = _services(plan_check_session_factory)
    run_id, call_id = _proposal_source(
        plan_check_session_factory,
        conversation,
        identity,
        project_id,
    )
    prepared = proposals.prepare_from_agent(
        project_id=project_id,
        identity=identity,
        run_id=run_id,
        tool_call_id=call_id,
        request=ActionProposalPrepareInput(draft_id=created.summary.draft_id),
    )
    assert prepared.status is ActionProposalStatus.PROPOSED
    assert prepared.confirmability is ActionProposalConfirmability.READY
    assert prepared.allowed_actions == ["CONFIRM", "CANCEL"]
    assert prepared.diff_snapshot
    assert prepared.payload.context.goal == "R15d 正式候选目标"

    second_run, second_call = _proposal_source(
        plan_check_session_factory,
        conversation,
        identity,
        project_id,
    )
    replay_prepare = proposals.prepare_from_agent(
        project_id=project_id,
        identity=identity,
        run_id=second_run,
        tool_call_id=second_call,
        request=ActionProposalPrepareInput(draft_id=created.summary.draft_id),
    )
    assert replay_prepare.proposal_id == prepared.proposal_id

    key = uuid4()
    request = ActionProposalConfirmRequest(proposal_digest=prepared.proposal_digest)
    executed = proposals.confirm(
        project_id=project_id,
        proposal_id=prepared.proposal_id,
        identity=identity,
        idempotency_key=key,
        request=request,
    )
    replay = proposals.confirm(
        project_id=project_id,
        proposal_id=prepared.proposal_id,
        identity=identity,
        idempotency_key=key,
        request=request,
    )
    assert replay.proposal_id == executed.proposal_id
    assert executed.status is ActionProposalStatus.EXECUTED
    assert executed.executed_context_version == 2
    assert executed.confirmed_by == identity.user_id
    assert (
        drafts.get_draft(
            project_id=project_id,
            draft_id=created.summary.draft_id,
            identity=identity,
        ).summary.freshness
        is PolicyDraftFreshness.STALE
    )

    with plan_check_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ProjectContext)) == 2
        assert session.scalar(select(func.count()).select_from(AgentActionProposal)) == 1
        assert (
            session.scalar(
                select(func.count()).select_from(IdempotencyRecord).where(
                    IdempotencyRecord.operation == "agent.action_proposal.confirm"
                )
            )
            == 1
        )
        actions = set(
            session.scalars(
                select(AuditLog.action).where(
                    AuditLog.target_id == prepared.proposal_id
                )
            ).all()
        )
        assert {
            "agent.action_proposal.prepared",
            "agent.action_proposal.executed",
        } <= actions

    with pytest.raises(ConflictError, match="已终止"):
        proposals.confirm(
            project_id=project_id,
            proposal_id=prepared.proposal_id,
            identity=identity,
            idempotency_key=uuid4(),
            request=request,
        )


def test_changed_draft_and_expiry_are_persisted_without_policy_publish(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    conversation, identity, initialized, _ = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    drafts, created = _ready_draft(
        plan_check_session_factory,
        conversation,
        identity,
        initialized,
        "等待失效的候选目标",
    )
    _, proposals = _services(plan_check_session_factory)
    run_id, call_id = _proposal_source(
        plan_check_session_factory,
        conversation,
        identity,
        project_id,
    )
    prepared = proposals.prepare_from_agent(
        project_id=project_id,
        identity=identity,
        run_id=run_id,
        tool_call_id=call_id,
        request=ActionProposalPrepareInput(draft_id=created.summary.draft_id),
    )

    changed = created.current.candidate.model_copy(deep=True)
    changed.context.goal = "草稿 revision 已更新"
    drafts.revise_from_web(
        project_id=project_id,
        draft_id=created.summary.draft_id,
        identity=identity,
        idempotency_key=uuid4(),
        request=PolicyDraftRevisionInput(
            expected_revision=1,
            candidate=changed,
            change_summary="提案准备后继续修改",
        ),
    )
    stale = proposals.confirm(
        project_id=project_id,
        proposal_id=prepared.proposal_id,
        identity=identity,
        idempotency_key=uuid4(),
        request=ActionProposalConfirmRequest(
            proposal_digest=prepared.proposal_digest
        ),
    )
    assert stale.status is ActionProposalStatus.STALE
    assert any("revision" in reason for reason in stale.confirmability_reasons)

    _, second = _ready_draft(
        plan_check_session_factory,
        conversation,
        identity,
        initialized,
        "等待过期的候选目标",
    )
    expiry_run, expiry_call = _proposal_source(
        plan_check_session_factory,
        conversation,
        identity,
        project_id,
    )
    expiring = proposals.prepare_from_agent(
        project_id=project_id,
        identity=identity,
        run_id=expiry_run,
        tool_call_id=expiry_call,
        request=ActionProposalPrepareInput(draft_id=second.summary.draft_id),
    )
    with plan_check_session_factory() as session, session.begin():
        row = session.get(AgentActionProposal, expiring.proposal_id)
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    expired = proposals.confirm(
        project_id=project_id,
        proposal_id=expiring.proposal_id,
        identity=identity,
        idempotency_key=uuid4(),
        request=ActionProposalConfirmRequest(
            proposal_digest=expiring.proposal_digest
        ),
    )
    assert expired.status is ActionProposalStatus.EXPIRED
    with plan_check_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ProjectContext)) == 1


def test_researcher_may_prepare_own_proposal_but_only_owner_can_confirm(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    conversation, owner, initialized, _ = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    researcher_id = uuid4()
    researcher_session = uuid4()
    now = datetime.now(UTC)
    with plan_check_session_factory() as session, session.begin():
        session.add(
            User(
                id=researcher_id,
                name="Proposal Researcher",
                email="proposal-researcher@example.com",
            )
        )
        session.flush()
        session.add(
            TeamMember(
                team_id=owner.team_id,
                user_id=researcher_id,
                role=TeamRole.RESEARCHER,
            )
        )
        session.add(
            WebSession(
                id=researcher_session,
                user_id=researcher_id,
                team_id=owner.team_id,
                session_hash="e" * 64,
                authenticated_at=now,
                reauthenticated_at=now,
                last_seen_at=now,
                absolute_expires_at=now + timedelta(hours=8),
            )
        )
    researcher = RequestIdentity(
        user_id=researcher_id,
        team_id=owner.team_id,
        token_id=researcher_session,
        scopes=RESEARCHER_WEB_SCOPES,
        authentication_method="WEB_SESSION",
        recent_authentication=True,
    )
    _, created = _ready_draft(
        plan_check_session_factory,
        conversation,
        researcher,
        initialized,
        "Researcher 准备的候选目标",
    )
    _, proposals = _services(plan_check_session_factory)
    run_id, call_id = _proposal_source(
        plan_check_session_factory,
        conversation,
        researcher,
        project_id,
    )
    prepared = proposals.prepare_from_agent(
        project_id=project_id,
        identity=researcher,
        run_id=run_id,
        tool_call_id=call_id,
        request=ActionProposalPrepareInput(draft_id=created.summary.draft_id),
    )
    assert prepared.allowed_actions == ["CANCEL"]
    with pytest.raises(AuthorizationError):
        proposals.confirm(
            project_id=project_id,
            proposal_id=prepared.proposal_id,
            identity=researcher,
            idempotency_key=uuid4(),
            request=ActionProposalConfirmRequest(
                proposal_digest=prepared.proposal_digest
            ),
        )

    owner_view = proposals.get_proposal(
        project_id=project_id,
        proposal_id=prepared.proposal_id,
        identity=owner,
    )
    assert "CONFIRM" in owner_view.allowed_actions
    executed = proposals.confirm(
        project_id=project_id,
        proposal_id=prepared.proposal_id,
        identity=owner,
        idempotency_key=uuid4(),
        request=ActionProposalConfirmRequest(
            proposal_digest=prepared.proposal_digest
        ),
    )
    assert executed.status is ActionProposalStatus.EXECUTED


def test_r15d_agent_tool_only_prepares_action_proposal_evidence(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    conversation, identity, initialized, _ = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    drafts, created = _ready_draft(
        plan_check_session_factory,
        conversation,
        identity,
        initialized,
        "Agent 工具准备的候选目标",
    )
    projects = SqlAlchemyProjectRepository()
    proposals = ActionProposalService(
        plan_check_session_factory,
        projects,
        drafts,
        WebManagementService(
            plan_check_session_factory,
            projects,
            UnconfiguredArtifactStorage(),
            900,
        ),
    )
    run_id, call_id = _proposal_source(
        plan_check_session_factory,
        conversation,
        identity,
        project_id,
    )
    result = AgentToolRegistry(
        plan_check_session_factory,
        projects,
        drafts,
        proposals,
    ).execute(
        tool_name="action_proposal_prepare_v1",
        arguments={"draft_id": str(created.summary.draft_id)},
        project_id=project_id,
        identity=identity,
        evidence_prefix="ev_1",
        catalog_version="r15d-v1",
        run_id=run_id,
        tool_call_id=call_id,
    )
    assert result.evidence[0].evidence_kind.value == "ACTION_PROPOSAL"
    assert result.evidence[0].entity_type == "ACTION_PROPOSAL"
    assert result.content["governance_notice"].startswith("提案尚未执行")
    with plan_check_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ProjectContext)) == 1
