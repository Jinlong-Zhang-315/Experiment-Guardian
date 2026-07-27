"""R15d-a Policy 发布提案的权限、失效与原子确认验收。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.action_proposals import ActionProposalService
from experiment_guardian.application.agent_tools import AgentToolRegistry
from experiment_guardian.application.errors import AuthorizationError, ConflictError
from experiment_guardian.application.experiments import ExperimentReviewService
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.policy_drafts import PolicyDraftService
from experiment_guardian.application.services import GuardianApplication, PlanApprovalService
from experiment_guardian.application.web_auth import RESEARCHER_WEB_SCOPES
from experiment_guardian.application.web_management import WebManagementService
from experiment_guardian.domain.action_proposal import (
    ActionProposalConfirmRequest,
    ActionProposalPrepareInput,
    PlanDecisionProposalPrepareInput,
)
from experiment_guardian.domain.administration import PlanCheckDecisionRequest
from experiment_guardian.domain.agent import (
    AgentMessageCreateRequest,
    AgentThreadCreateRequest,
)
from experiment_guardian.domain.enums import (
    ActionProposalConfirmability,
    ActionProposalOperation,
    ActionProposalStatus,
    AgentCallStatus,
    ApprovalDecision,
    ApprovalStatus,
    PolicyDraftFreshness,
    TeamRole,
)
from experiment_guardian.domain.policy_draft import PolicyDraftRevisionInput
from experiment_guardian.infrastructure.models import (
    AgentActionProposal,
    AgentToolCall,
    ApprovalRecord,
    AuditLog,
    IdempotencyRecord,
    PlanCheck,
    ProjectContext,
    TeamMember,
    User,
    WebSession,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyGovernanceRepository,
    SqlAlchemyPlanCheckRepository,
    SqlAlchemyProjectRepository,
    SqlAlchemySubmissionRepository,
)
from experiment_guardian.infrastructure.storage import UnconfiguredArtifactStorage
from tests.integration.test_agent_slice import _setup
from tests.integration.test_plan_check_slice import command, config_yaml
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
    approvals = PlanApprovalService(
        factory,
        projects,
        SqlAlchemyGovernanceRepository(),
    )
    reviews = ExperimentReviewService(
        factory,
        projects,
        SqlAlchemyGovernanceRepository(),
        SqlAlchemySubmissionRepository(),
    )
    return drafts, ActionProposalService(
        factory,
        projects,
        drafts,
        web,
        approvals,
        reviews,
    )


def _proposal_source(
    factory: sessionmaker[Session],
    conversation: object,
    identity: RequestIdentity,
    project_id: UUID,
    *,
    tool_name: str = "action_proposal_prepare_v1",
    message: str = "准备当前治理草稿的发布提案",
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
        request=AgentMessageCreateRequest(content=message),
    )
    with factory() as session, session.begin():
        call = AgentToolCall(
            run_id=receipt.run_id,
            generation=0,
            call_id=f"proposal-{uuid4()}",
            sequence=1,
            tool_name=tool_name,
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


def _pending_plan(
    factory: sessionmaker[Session],
    identity: RequestIdentity,
    initialized: object,
) -> PlanCheck:
    project_id = initialized.project_id  # type: ignore[attr-defined]
    intent = initialized.context_bundle.active_intent  # type: ignore[attr-defined]
    assert intent is not None
    mcp_identity = RequestIdentity(
        user_id=identity.user_id,
        team_id=identity.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"experiment:check", "project:read"}),
    )
    guardian = GuardianApplication(
        factory,
        SqlAlchemyProjectRepository(),
        SqlAlchemyPlanCheckRepository(),
        SqlAlchemyGovernanceRepository(),
    )
    result = guardian.experiment_check_plan(
        command(
            project_id=project_id,
            intent_id=intent.intent_id,
            content=config_yaml(backbone="transformer"),
        ),
        mcp_identity,
    )
    with factory() as session:
        plan = session.get(PlanCheck, result.plan_check_id)
        assert plan is not None
        return plan


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
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(IdempotencyRecord.operation == "agent.action_proposal.confirm")
            )
            == 1
        )
        actions = set(
            session.scalars(
                select(AuditLog.action).where(AuditLog.target_id == prepared.proposal_id)
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
        request=ActionProposalConfirmRequest(proposal_digest=prepared.proposal_digest),
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
        request=ActionProposalConfirmRequest(proposal_digest=expiring.proposal_digest),
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
        proposals.prepare_from_agent(
            project_id=project_id,
            identity=owner,
            run_id=run_id,
            tool_call_id=call_id,
            request=ActionProposalPrepareInput(draft_id=created.summary.draft_id),
        )
    with pytest.raises(AuthorizationError):
        proposals.confirm(
            project_id=project_id,
            proposal_id=prepared.proposal_id,
            identity=researcher,
            idempotency_key=uuid4(),
            request=ActionProposalConfirmRequest(proposal_digest=prepared.proposal_digest),
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
        request=ActionProposalConfirmRequest(proposal_digest=prepared.proposal_digest),
    )
    assert executed.status is ActionProposalStatus.EXECUTED


@pytest.mark.parametrize("decision", [ApprovalDecision.APPROVED, ApprovalDecision.REJECTED])
def test_owner_confirms_plan_decision_proposal_atomically(
    plan_check_session_factory: sessionmaker[Session],
    decision: ApprovalDecision,
) -> None:
    conversation, identity, initialized, _ = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    plan = _pending_plan(plan_check_session_factory, identity, initialized)
    _, proposals = _services(plan_check_session_factory)
    run_id, call_id = _proposal_source(
        plan_check_session_factory,
        conversation,
        identity,
        project_id,
        tool_name="action_proposal_prepare_plan_decision_v1",
        message=f"请准备 {decision.value} 这个 Plan Check 的提案",
    )
    prepared = proposals.prepare_plan_decision_from_agent(
        project_id=project_id,
        identity=identity,
        run_id=run_id,
        tool_call_id=call_id,
        request=PlanDecisionProposalPrepareInput(
            plan_check_id=plan.id,
            decision=decision,
            decision_reason="已核对正式参数变化和风险",
        ),
    )
    assert prepared.operation is ActionProposalOperation.PLAN_CHECK_DECISION
    assert prepared.target_plan_check_id == plan.id
    assert prepared.target_state_hash
    assert prepared.executed_approval_record_id is None
    with plan_check_session_factory() as session:
        stored = session.get(PlanCheck, plan.id)
        assert stored is not None
        assert stored.approval_status is ApprovalStatus.PENDING
        assert session.scalar(select(func.count()).select_from(ApprovalRecord)) == 0

    key = uuid4()
    confirmation = ActionProposalConfirmRequest(proposal_digest=prepared.proposal_digest)
    executed = proposals.confirm(
        project_id=project_id,
        proposal_id=prepared.proposal_id,
        identity=identity,
        idempotency_key=key,
        request=confirmation,
    )
    replay = proposals.confirm(
        project_id=project_id,
        proposal_id=prepared.proposal_id,
        identity=identity,
        idempotency_key=key,
        request=confirmation,
    )
    assert replay.proposal_id == executed.proposal_id
    assert replay.execution_result == executed.execution_result
    assert executed.status is ActionProposalStatus.EXECUTED
    assert executed.executed_approval_record_id is not None
    with plan_check_session_factory() as session:
        stored = session.get(PlanCheck, plan.id)
        approval = session.get(
            ApprovalRecord,
            executed.executed_approval_record_id,
        )
        assert stored is not None and approval is not None
        assert stored.approval_status.value == decision.value
        assert approval.status is decision
        assert session.scalar(select(func.count()).select_from(ApprovalRecord)) == 1
        operations = set(
            session.scalars(
                select(IdempotencyRecord.operation).where(
                    IdempotencyRecord.operation.in_(
                        {"agent.action_proposal.confirm", "plan_check.decision"}
                    )
                )
            ).all()
        )
        assert operations == {
            "agent.action_proposal.confirm",
            "plan_check.decision",
        }


def test_direct_plan_decision_makes_proposal_stale(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    conversation, identity, initialized, _ = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    plan = _pending_plan(plan_check_session_factory, identity, initialized)
    _, proposals = _services(plan_check_session_factory)
    run_id, call_id = _proposal_source(
        plan_check_session_factory,
        conversation,
        identity,
        project_id,
        tool_name="action_proposal_prepare_plan_decision_v1",
        message="准备批准 Plan 的提案",
    )
    prepared = proposals.prepare_plan_decision_from_agent(
        project_id=project_id,
        identity=identity,
        run_id=run_id,
        tool_call_id=call_id,
        request=PlanDecisionProposalPrepareInput(
            plan_check_id=plan.id,
            decision=ApprovalDecision.APPROVED,
            decision_reason="准备批准",
        ),
    )
    PlanApprovalService(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
        SqlAlchemyGovernanceRepository(),
    ).decide(
        identity=identity,
        project_id=project_id,
        plan_check_id=plan.id,
        idempotency_key=uuid4(),
        request=PlanCheckDecisionRequest.model_validate(prepared.payload),
    )
    stale = proposals.confirm(
        project_id=project_id,
        proposal_id=prepared.proposal_id,
        identity=identity,
        idempotency_key=uuid4(),
        request=ActionProposalConfirmRequest(proposal_digest=prepared.proposal_digest),
    )
    assert stale.status is ActionProposalStatus.STALE
    assert any("审批状态" in reason for reason in stale.confirmability_reasons)
    with plan_check_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ApprovalRecord)) == 1


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


def test_r15d_b1_agent_tool_prepares_plan_proposal_without_deciding(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    conversation, identity, initialized, _ = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    plan = _pending_plan(plan_check_session_factory, identity, initialized)
    drafts, proposals = _services(plan_check_session_factory)
    run_id, call_id = _proposal_source(
        plan_check_session_factory,
        conversation,
        identity,
        project_id,
        tool_name="action_proposal_prepare_plan_decision_v1",
        message="请明确准备批准这个 Plan Check 的提案",
    )
    result = AgentToolRegistry(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
        drafts,
        proposals,
    ).execute(
        tool_name="action_proposal_prepare_plan_decision_v1",
        arguments={
            "plan_check_id": str(plan.id),
            "decision": "APPROVED",
            "decision_reason": "符合当前 Intent，变化需要 Owner 明确批准",
        },
        project_id=project_id,
        identity=identity,
        evidence_prefix="ev_plan",
        catalog_version="r15d-b1-v1",
        run_id=run_id,
        tool_call_id=call_id,
    )
    assert result.evidence[0].evidence_kind.value == "ACTION_PROPOSAL"
    assert result.evidence[0].entity_version.startswith(f"plan:{plan.id}")
    with plan_check_session_factory() as session:
        stored = session.get(PlanCheck, plan.id)
        assert stored is not None
        assert stored.approval_status is ApprovalStatus.PENDING
        assert session.scalar(select(func.count()).select_from(ApprovalRecord)) == 0
