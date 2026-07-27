"""R15d-b2 Submission 审核提案的权限、失效和原子确认验收。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.action_proposals import (
    ACTION_PROPOSAL_CONFIRM_OPERATION,
)
from experiment_guardian.application.agent import AgentConversationService
from experiment_guardian.application.agent_tools import AgentToolRegistry
from experiment_guardian.application.errors import (
    AuthorizationError,
    ConflictError,
    RecentAuthenticationRequiredError,
)
from experiment_guardian.application.experiments import SUBMISSION_DECISION_OPERATION
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.web_auth import OWNER_WEB_SCOPES, RESEARCHER_WEB_SCOPES
from experiment_guardian.domain.action_proposal import (
    ActionProposalConfirmRequest,
    SubmissionDecisionProposalPrepareInput,
)
from experiment_guardian.domain.enums import (
    ActionProposalConfirmability,
    ActionProposalStatus,
    ApprovalDecision,
    EvidenceType,
    ReviewEligibility,
    RiskSeverity,
    SubmissionStatus,
    TeamRole,
)
from experiment_guardian.infrastructure.models import (
    AgentActionProposal,
    ApprovalRecord,
    Artifact,
    Experiment,
    ExperimentMetric,
    ExperimentSubmission,
    IdempotencyRecord,
    Memory,
    SubmissionRisk,
    TeamMember,
    User,
    WebSession,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyAgentRepository,
    SqlAlchemyProjectRepository,
)
from tests.integration.test_action_proposal_slice import _proposal_source, _services
from tests.integration.test_agent_slice import _settings
from tests.integration.test_async_review_slice import (
    FakeEmbeddingGenerator,
    build_review_processor,
    prepare_summary,
)
from tests.integration.test_experiment_confirmation_slice import prepare_needs_review


def _web_identity(
    factory: sessionmaker[Session],
    principal: SimpleNamespace | RequestIdentity,
    project_id: UUID,
    *,
    role: TeamRole = TeamRole.OWNER,
    recent: bool = True,
) -> RequestIdentity:
    session_id = uuid4()
    now = datetime.now(UTC)
    with factory() as session, session.begin():
        session.add(
            WebSession(
                id=session_id,
                user_id=principal.user_id,
                team_id=principal.team_id,
                session_hash=uuid4().hex * 2,
                authenticated_at=now,
                reauthenticated_at=now if recent else None,
                last_seen_at=now,
                absolute_expires_at=now + timedelta(hours=8),
            )
        )
    return RequestIdentity(
        user_id=principal.user_id,
        team_id=principal.team_id,
        token_id=session_id,
        project_id=project_id,
        scopes=OWNER_WEB_SCOPES if role is TeamRole.OWNER else RESEARCHER_WEB_SCOPES,
        authentication_method="WEB_SESSION",
        recent_authentication=recent,
    )


def _conversation(factory: sessionmaker[Session]) -> AgentConversationService:
    return AgentConversationService(
        factory,
        SqlAlchemyProjectRepository(),
        SqlAlchemyAgentRepository(),
        _settings(),
    )


def _prepare(
    factory: sessionmaker[Session],
    *,
    identity: RequestIdentity,
    project_id: UUID,
    submission_id: UUID,
    decision: ApprovalDecision,
    reason: str,
) -> tuple[object, object]:
    run_id, call_id = _proposal_source(
        factory,
        _conversation(factory),
        identity,
        project_id,
        tool_name="action_proposal_prepare_submission_decision_v1",
        message=f"准备 {decision.value} Submission 审核提案",
    )
    _, service = _services(factory)
    proposal = service.prepare_submission_decision_from_agent(
        project_id=project_id,
        identity=identity,
        run_id=run_id,
        tool_call_id=call_id,
        request=SubmissionDecisionProposalPrepareInput(
            submission_id=submission_id,
            decision=decision,
            decision_reason=reason,
        ),
    )
    return service, proposal


def _add_researcher(
    factory: sessionmaker[Session],
    *,
    team_id: UUID,
    submission_id: UUID,
) -> RequestIdentity:
    researcher_id = uuid4()
    with factory() as session, session.begin():
        session.add(
            User(
                id=researcher_id,
                name="Proposal Researcher",
                email=f"proposal-{researcher_id}@example.com",
            )
        )
        session.flush()
        session.add(
            TeamMember(
                team_id=team_id,
                user_id=researcher_id,
                role=TeamRole.RESEARCHER,
            )
        )
        submission = session.get(ExperimentSubmission, submission_id)
        assert submission is not None
        submission.submitted_by = researcher_id
    return RequestIdentity(
        user_id=researcher_id,
        team_id=team_id,
        token_id=uuid4(),
        scopes=RESEARCHER_WEB_SCOPES,
    )


def test_submission_proposal_prepares_without_formal_mutation_and_confirms_once(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, command, _ = prepare_needs_review(plan_check_session_factory)
    reviewer = _web_identity(plan_check_session_factory, owner, project_id)
    service, proposal = _prepare(
        plan_check_session_factory,
        identity=reviewer,
        project_id=project_id,
        submission_id=command.submission_id,
        decision=ApprovalDecision.APPROVED,
        reason="已核对回执、风险和不可变材料",
    )
    assert proposal.status is ActionProposalStatus.PROPOSED
    assert proposal.confirmability is ActionProposalConfirmability.READY
    assert proposal.target_submission_id == command.submission_id
    assert proposal.impact_snapshot["approval_material_complete"] is True
    with plan_check_session_factory() as session:
        submission = session.get(ExperimentSubmission, command.submission_id)
        assert submission is not None and submission.status is SubmissionStatus.NEEDS_REVIEW
        assert session.scalar(select(func.count()).select_from(ApprovalRecord)) == 0
        assert session.scalar(select(func.count()).select_from(Experiment)) == 0

    key = uuid4()
    request = ActionProposalConfirmRequest(proposal_digest=proposal.proposal_digest)
    result = service.confirm(
        project_id=project_id,
        proposal_id=proposal.proposal_id,
        identity=reviewer,
        idempotency_key=key,
        request=request,
    )
    replay = service.confirm(
        project_id=project_id,
        proposal_id=proposal.proposal_id,
        identity=reviewer,
        idempotency_key=key,
        request=request,
    )
    assert result == replay
    assert result.status is ActionProposalStatus.EXECUTED
    assert result.executed_approval_record_id is not None
    assert result.executed_experiment_id is not None
    with plan_check_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ApprovalRecord)) == 1
        assert session.scalar(select(func.count()).select_from(Experiment)) == 1
        assert session.scalar(select(func.count()).select_from(Memory)) == 1
        assert session.scalar(select(func.count()).select_from(ExperimentMetric)) >= 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(
                    IdempotencyRecord.operation.in_(
                        {
                            ACTION_PROPOSAL_CONFIRM_OPERATION,
                            SUBMISSION_DECISION_OPERATION,
                        }
                    )
                )
            )
            == 2
        )


def test_agent_tool_returns_submission_proposal_evidence_without_reviewing(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, command, _ = prepare_needs_review(plan_check_session_factory)
    reviewer = _web_identity(plan_check_session_factory, owner, project_id)
    run_id, call_id = _proposal_source(
        plan_check_session_factory,
        _conversation(plan_check_session_factory),
        reviewer,
        project_id,
        tool_name="action_proposal_prepare_submission_decision_v1",
        message="准备批准 Submission 提案",
    )
    drafts, proposals = _services(plan_check_session_factory)
    result = AgentToolRegistry(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
        drafts,
        proposals,
    ).execute(
        tool_name="action_proposal_prepare_submission_decision_v1",
        arguments={
            "submission_id": str(command.submission_id),
            "decision": "APPROVED",
            "decision_reason": "已核对确定性诊断和审核材料",
        },
        project_id=project_id,
        identity=reviewer,
        evidence_prefix="ev_submission",
        catalog_version="r15d-b2-v1",
        run_id=run_id,
        tool_call_id=call_id,
    )
    assert result.evidence[0].evidence_kind.value == "ACTION_PROPOSAL"
    assert result.evidence[0].entity_type == "ACTION_PROPOSAL"
    assert "尚未改变" in str(result.content["governance_notice"])
    with plan_check_session_factory() as session:
        submission = session.get(ExperimentSubmission, command.submission_id)
        assert submission is not None and submission.status is SubmissionStatus.NEEDS_REVIEW
        assert session.scalar(select(func.count()).select_from(ApprovalRecord)) == 0
        assert session.scalar(select(func.count()).select_from(Experiment)) == 0


def test_researcher_can_confirm_own_low_risk_but_recent_auth_is_required(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, command, _ = prepare_needs_review(plan_check_session_factory)
    principal = _add_researcher(
        plan_check_session_factory,
        team_id=owner.team_id,
        submission_id=command.submission_id,
    )
    researcher = _web_identity(
        plan_check_session_factory,
        principal,
        project_id,
        role=TeamRole.RESEARCHER,
    )
    service, proposal = _prepare(
        plan_check_session_factory,
        identity=researcher,
        project_id=project_id,
        submission_id=command.submission_id,
        decision=ApprovalDecision.APPROVED,
        reason="自有低风险提交材料完整",
    )
    assert "CONFIRM" in proposal.allowed_actions
    stale_identity = replace(researcher, recent_authentication=False)
    with pytest.raises(RecentAuthenticationRequiredError):
        service.confirm(
            project_id=project_id,
            proposal_id=proposal.proposal_id,
            identity=stale_identity,
            idempotency_key=uuid4(),
            request=ActionProposalConfirmRequest(proposal_digest=proposal.proposal_digest),
        )
    result = service.confirm(
        project_id=project_id,
        proposal_id=proposal.proposal_id,
        identity=researcher,
        idempotency_key=uuid4(),
        request=ActionProposalConfirmRequest(proposal_digest=proposal.proposal_digest),
    )
    assert result.executed_experiment_id is not None


def test_high_risk_approval_requires_owner_but_researcher_may_prepare(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, _, command, queue = prepare_summary(plan_check_session_factory)
    principal = _add_researcher(
        plan_check_session_factory,
        team_id=owner.team_id,
        submission_id=command.submission_id,
    )
    with plan_check_session_factory() as session, session.begin():
        session.add(
            SubmissionRisk(
                submission_id=command.submission_id,
                risk_fingerprint="h" * 64,
                risk_type="HIGH_PROPOSAL_TEST",
                severity=RiskSeverity.HIGH,
                field_path="model.backbone",
                previous_value="shift-gcn",
                current_value="transformer",
                expected_value="shift-gcn",
                rule_id="R15D_B2.HIGH",
                message="高风险模型变化",
                impact="需要 Owner 决策",
                evidence_type=EvidenceType.CLOUD_VERIFIED,
                evidence_source="pytest",
                collected_at=datetime.now(UTC),
                collection_tool="pytest",
                constraint_candidates=[],
                blocking=False,
                resolved=False,
            )
        )
    assert build_review_processor(
        plan_check_session_factory, queue, FakeEmbeddingGenerator()
    ).process_delivery(queue.delivery(receipt="r15d-b2-high"))
    researcher = _web_identity(
        plan_check_session_factory,
        principal,
        project_id,
        role=TeamRole.RESEARCHER,
    )
    service, proposal = _prepare(
        plan_check_session_factory,
        identity=researcher,
        project_id=project_id,
        submission_id=command.submission_id,
        decision=ApprovalDecision.APPROVED,
        reason="准备交由 Owner 核对 HIGH 风险",
    )
    assert proposal.impact_snapshot["review_eligibility"] == ReviewEligibility.OWNER_ONLY.value
    assert "CONFIRM" not in proposal.allowed_actions
    with pytest.raises(AuthorizationError, match="HIGH"):
        service.confirm(
            project_id=project_id,
            proposal_id=proposal.proposal_id,
            identity=researcher,
            idempotency_key=uuid4(),
            request=ActionProposalConfirmRequest(proposal_digest=proposal.proposal_digest),
        )
    owner_identity = _web_identity(plan_check_session_factory, owner, project_id)
    owner_view = service.get_proposal(
        project_id=project_id,
        proposal_id=proposal.proposal_id,
        identity=owner_identity,
    )
    assert "CONFIRM" in owner_view.allowed_actions
    result = service.confirm(
        project_id=project_id,
        proposal_id=proposal.proposal_id,
        identity=owner_identity,
        idempotency_key=uuid4(),
        request=ActionProposalConfirmRequest(proposal_digest=proposal.proposal_digest),
    )
    assert result.executed_experiment_id is not None


def test_critical_submission_cannot_prepare_approval_but_can_be_rejected(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, _, command, queue = prepare_summary(plan_check_session_factory)
    with plan_check_session_factory() as session, session.begin():
        session.add(
            SubmissionRisk(
                submission_id=command.submission_id,
                risk_fingerprint="c" * 64,
                risk_type="CRITICAL_PROPOSAL_TEST",
                severity=RiskSeverity.CRITICAL,
                field_path="result.metrics.top1",
                previous_value=None,
                current_value=2.0,
                expected_value="0..1",
                rule_id="R15D_B2.CRITICAL",
                message="关键指标超出合法范围",
                impact="不能形成正式实验",
                evidence_type=EvidenceType.CLOUD_VERIFIED,
                evidence_source="pytest",
                collected_at=datetime.now(UTC),
                collection_tool="pytest",
                constraint_candidates=[],
                blocking=True,
                resolved=False,
            )
        )
    assert build_review_processor(
        plan_check_session_factory, queue, FakeEmbeddingGenerator()
    ).process_delivery(queue.delivery(receipt="r15d-b2-critical"))
    reviewer = _web_identity(plan_check_session_factory, owner, project_id)
    with pytest.raises(ConflictError, match="CRITICAL"):
        _prepare(
            plan_check_session_factory,
            identity=reviewer,
            project_id=project_id,
            submission_id=command.submission_id,
            decision=ApprovalDecision.APPROVED,
            reason="不应准备的批准提案",
        )
    service, proposal = _prepare(
        plan_check_session_factory,
        identity=reviewer,
        project_id=project_id,
        submission_id=command.submission_id,
        decision=ApprovalDecision.REJECTED,
        reason="CRITICAL 风险不能进入正式实验",
    )
    result = service.confirm(
        project_id=project_id,
        proposal_id=proposal.proposal_id,
        identity=reviewer,
        idempotency_key=uuid4(),
        request=ActionProposalConfirmRequest(proposal_digest=proposal.proposal_digest),
    )
    assert result.executed_experiment_id is None
    with plan_check_session_factory() as session:
        submission = session.get(ExperimentSubmission, command.submission_id)
        assert submission is not None and submission.status is SubmissionStatus.REJECTED
        assert session.scalar(select(func.count()).select_from(Experiment)) == 0


def test_changed_artifact_evidence_marks_proposal_stale_without_formal_rows(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, command, _ = prepare_needs_review(plan_check_session_factory)
    reviewer = _web_identity(plan_check_session_factory, owner, project_id)
    service, proposal = _prepare(
        plan_check_session_factory,
        identity=reviewer,
        project_id=project_id,
        submission_id=command.submission_id,
        decision=ApprovalDecision.APPROVED,
        reason="材料当前完整",
    )
    with plan_check_session_factory() as session, session.begin():
        artifact = session.scalar(
            select(Artifact).where(Artifact.submission_id == command.submission_id)
        )
        assert artifact is not None
        artifact.s3_version_id = "changed-version"
    result = service.confirm(
        project_id=project_id,
        proposal_id=proposal.proposal_id,
        identity=reviewer,
        idempotency_key=uuid4(),
        request=ActionProposalConfirmRequest(proposal_digest=proposal.proposal_digest),
    )
    assert result.status is ActionProposalStatus.STALE
    assert any("依据已变化" in item for item in result.confirmability_reasons)
    with plan_check_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ApprovalRecord)) == 0
        assert session.scalar(select(func.count()).select_from(Experiment)) == 0


def test_submission_proposal_execution_rolls_back_with_formal_review_failure(
    plan_check_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, project_id, command, _ = prepare_needs_review(plan_check_session_factory)
    reviewer = _web_identity(plan_check_session_factory, owner, project_id)
    service, proposal = _prepare(
        plan_check_session_factory,
        identity=reviewer,
        project_id=project_id,
        submission_id=command.submission_id,
        decision=ApprovalDecision.APPROVED,
        reason="测试正式审核失败时回滚",
    )

    def fail_approve(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise ConflictError("注入的正式确认失败")

    assert service._experiment_reviews is not None
    monkeypatch.setattr(service._experiment_reviews, "_approve", fail_approve)
    with pytest.raises(ConflictError, match="注入"):
        service.confirm(
            project_id=project_id,
            proposal_id=proposal.proposal_id,
            identity=reviewer,
            idempotency_key=uuid4(),
            request=ActionProposalConfirmRequest(proposal_digest=proposal.proposal_digest),
        )
    with plan_check_session_factory() as session:
        stored = session.get(AgentActionProposal, proposal.proposal_id)
        submission = session.get(ExperimentSubmission, command.submission_id)
        assert stored is not None and stored.status is ActionProposalStatus.PROPOSED
        assert submission is not None and submission.status is SubmissionStatus.NEEDS_REVIEW
        assert session.scalar(select(func.count()).select_from(ApprovalRecord)) == 0
        assert session.scalar(select(func.count()).select_from(Experiment)) == 0
        assert session.scalar(select(func.count()).select_from(Memory)) == 0
