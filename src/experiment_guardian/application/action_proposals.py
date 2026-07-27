"""Agent 高影响操作提案服务。

Agent 只能准备冻结提案。正式策略发布必须由 Owner 通过 Web Session、CSRF 和近期认证
确认；确认时再次校验草稿 revision、正式策略版本及待处理对象状态。
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.errors import (
    AuthorizationError,
    ConflictError,
    InputValidationError,
    RecentAuthenticationRequiredError,
    ResourceNotFoundError,
)
from experiment_guardian.application.experiments import (
    ExperimentReviewService,
    SubmissionReviewBasis,
)
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.policy_drafts import (
    PolicyDraftProposalSource,
    PolicyDraftService,
)
from experiment_guardian.application.services import PlanApprovalService
from experiment_guardian.application.transactions import run_with_serialization_retry
from experiment_guardian.application.web_management import WebManagementService
from experiment_guardian.domain.action_proposal import (
    ACTION_PROPOSAL_TTL_HOURS,
    ActionProposalCancelRequest,
    ActionProposalConfirmRequest,
    ActionProposalPage,
    ActionProposalPrepareInput,
    ActionProposalView,
    PlanDecisionProposalPrepareInput,
    SubmissionDecisionProposalPrepareInput,
    build_action_proposal_digest,
    build_plan_decision_proposal_digest,
    build_submission_decision_proposal_digest,
)
from experiment_guardian.domain.administration import (
    PlanCheckDecisionRequest,
    SubmissionDecisionRequest,
)
from experiment_guardian.domain.enums import (
    ActionProposalConfirmability,
    ActionProposalOperation,
    ActionProposalStatus,
    ApprovalDecision,
    ApprovalStatus,
    ApprovalTargetType,
    CheckResult,
    IdempotencyOperationStatus,
    PolicyDraftFreshness,
    PolicyDraftReadiness,
    PolicyDraftStatus,
    ReviewEligibility,
    TeamRole,
)
from experiment_guardian.domain.policy_draft import canonical_hash
from experiment_guardian.domain.web_management import PolicyPublishRequest
from experiment_guardian.infrastructure.models import (
    AgentActionProposal,
    AgentPolicyDraftRevision,
    ApprovalRecord,
    AuditLog,
    IdempotencyRecord,
    PlanCheck,
    Project,
    RunManifest,
)
from experiment_guardian.infrastructure.repositories import SqlAlchemyProjectRepository

ACTION_PROPOSAL_CONFIRM_OPERATION = "agent.action_proposal.confirm"
ACTION_PROPOSAL_CANCEL_OPERATION = "agent.action_proposal.cancel"
TERMINAL_PROPOSAL_STATUSES = {
    ActionProposalStatus.EXECUTED,
    ActionProposalStatus.CANCELED,
    ActionProposalStatus.STALE,
    ActionProposalStatus.EXPIRED,
    ActionProposalStatus.FAILED,
}


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        value = int(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise InputValidationError("操作提案分页 cursor 无效") from exc
    if value < 0:
        raise InputValidationError("操作提案分页 cursor 无效")
    return value


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ActionProposalService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        projects: SqlAlchemyProjectRepository,
        policy_drafts: PolicyDraftService,
        web_management: WebManagementService,
        plan_approvals: PlanApprovalService | None = None,
        experiment_reviews: ExperimentReviewService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._projects = projects
        self._policy_drafts = policy_drafts
        self._web_management = web_management
        self._plan_approvals = plan_approvals
        self._experiment_reviews = experiment_reviews

    def prepare_from_agent(
        self,
        *,
        project_id: UUID,
        identity: RequestIdentity,
        run_id: UUID,
        tool_call_id: UUID,
        request: ActionProposalPrepareInput,
    ) -> ActionProposalView:
        self._require_web_identity(identity)

        def operation() -> ActionProposalView:
            with self._session_factory() as session, session.begin():
                run, thread = self._policy_drafts.require_agent_source(
                    session,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    project_id=project_id,
                    identity=identity,
                )
                existing_run = session.scalar(
                    select(AgentActionProposal).where(AgentActionProposal.source_run_id == run_id)
                )
                if existing_run is not None:
                    if existing_run.source_draft_id != request.draft_id:
                        raise ConflictError("同一 Agent Run 已准备不同的高影响操作")
                    return self._view(session, existing_run, identity=identity)

                if session.scalar(
                    select(AgentPolicyDraftRevision.id).where(
                        AgentPolicyDraftRevision.source_run_id == run_id
                    )
                ):
                    raise ConflictError("同一 Agent Run 只能执行一次草稿或提案写操作")

                source = self._policy_drafts.load_proposal_source(
                    session,
                    project_id=project_id,
                    draft_id=request.draft_id,
                    identity=identity,
                    for_update=True,
                )
                self._require_preparable(source)
                now = datetime.now(UTC)
                existing = session.scalar(
                    select(AgentActionProposal).where(
                        AgentActionProposal.source_draft_revision_id == source.revision.id,
                        AgentActionProposal.status == ActionProposalStatus.PROPOSED,
                        AgentActionProposal.expires_at > now,
                    )
                )
                if existing is not None:
                    return self._view(session, existing, identity=identity)

                payload = PolicyPublishRequest(
                    expected_context_version=source.draft.base_context_version,
                    context=source.candidate.context,
                    intent=source.candidate.intent,
                    constraints=source.candidate.constraints,
                )
                project, _ = self._require_project(session, project_id, identity)
                proposal_id = uuid4()
                expires_at = now + timedelta(hours=ACTION_PROPOSAL_TTL_HOURS)
                digest = build_action_proposal_digest(
                    proposal_id=proposal_id,
                    operation=ActionProposalOperation.POLICY_PUBLISH,
                    project_id=project_id,
                    payload=payload,
                    source_draft_id=source.draft.id,
                    source_draft_revision_id=source.revision.id,
                    source_draft_revision=source.revision.revision,
                    source_candidate_hash=source.revision.candidate_hash,
                    base_policy_hash=source.draft.base_policy_hash,
                    pending_state_hash=source.impact.pending_state_hash,
                    expires_at=expires_at,
                )
                proposal = AgentActionProposal(
                    id=proposal_id,
                    team_id=project.team_id,
                    project_id=project.id,
                    created_by=identity.user_id,
                    source_thread_id=thread.id,
                    source_run_id=run.id,
                    source_tool_call_id=tool_call_id,
                    source_draft_id=source.draft.id,
                    source_draft_revision_id=source.revision.id,
                    source_draft_revision=source.revision.revision,
                    operation=ActionProposalOperation.POLICY_PUBLISH,
                    status=ActionProposalStatus.PROPOSED,
                    payload=payload.model_dump(mode="json"),
                    payload_hash=canonical_hash(payload.model_dump(mode="json")),
                    source_candidate_hash=source.revision.candidate_hash,
                    base_context_id=source.draft.base_context_id,
                    base_context_version=source.draft.base_context_version,
                    base_intent_id=source.draft.base_intent_id,
                    base_intent_version=source.draft.base_intent_version,
                    base_policy_hash=source.draft.base_policy_hash,
                    diff_snapshot=source.revision.diff_snapshot,
                    impact_snapshot=source.impact.model_dump(mode="json"),
                    pending_state_hash=source.impact.pending_state_hash,
                    proposal_digest=digest,
                    expires_at=expires_at,
                )
                session.add(proposal)
                session.flush()
                session.add(
                    AuditLog(
                        team_id=project.team_id,
                        project_id=project.id,
                        actor_type="USER",
                        actor_id=identity.user_id,
                        action="agent.action_proposal.prepared",
                        target_type="AGENT_ACTION_PROPOSAL",
                        target_id=proposal.id,
                        before_value=None,
                        after_value={
                            "operation": proposal.operation.value,
                            "draft_id": str(proposal.source_draft_id),
                            "draft_revision": proposal.source_draft_revision,
                            "proposal_digest": proposal.proposal_digest,
                            "expires_at": proposal.expires_at.isoformat(),
                            "run_id": str(run.id),
                            "tool_call_id": str(tool_call_id),
                            "session_id": str(identity.token_id),
                        },
                    )
                )
                return self._view(session, proposal, identity=identity)

        return run_with_serialization_retry(operation)

    def prepare_plan_decision_from_agent(
        self,
        *,
        project_id: UUID,
        identity: RequestIdentity,
        run_id: UUID,
        tool_call_id: UUID,
        request: PlanDecisionProposalPrepareInput,
    ) -> ActionProposalView:
        """冻结一个 Plan 最终决定；本方法不改变 Plan 或 ApprovalRecord。"""

        self._require_web_identity(identity)
        payload = PlanCheckDecisionRequest(
            decision=request.decision,
            decision_reason=request.decision_reason.strip(),
        )

        def operation() -> ActionProposalView:
            with self._session_factory() as session, session.begin():
                run, thread = self._policy_drafts.require_agent_source(
                    session,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    project_id=project_id,
                    identity=identity,
                )
                existing_run = session.scalar(
                    select(AgentActionProposal).where(AgentActionProposal.source_run_id == run_id)
                )
                if existing_run is not None:
                    if (
                        existing_run.operation is not ActionProposalOperation.PLAN_CHECK_DECISION
                        or existing_run.target_plan_check_id != request.plan_check_id
                        or existing_run.payload_hash
                        != canonical_hash(payload.model_dump(mode="json"))
                    ):
                        raise ConflictError("同一 Agent Run 已准备不同的高影响操作")
                    return self._view(session, existing_run, identity=identity)

                if session.scalar(
                    select(AgentPolicyDraftRevision.id).where(
                        AgentPolicyDraftRevision.source_run_id == run_id
                    )
                ):
                    raise ConflictError("同一 Agent Run 只能执行一次草稿或提案写操作")

                project, role = self._require_project(session, project_id, identity)
                plan = session.scalar(
                    select(PlanCheck)
                    .where(
                        PlanCheck.id == request.plan_check_id,
                        PlanCheck.project_id == project_id,
                    )
                    .with_for_update()
                )
                if plan is None or (
                    role is TeamRole.RESEARCHER and plan.requester_id != identity.user_id
                ):
                    raise ResourceNotFoundError("项目中不存在当前用户可访问的 Plan Check")
                approval = self._plan_approval(session, plan.id)
                manifest = self._plan_manifest(session, plan.id)
                self._require_plan_preparable(plan, approval=approval, manifest=manifest)

                now = datetime.now(UTC)
                payload_hash = canonical_hash(payload.model_dump(mode="json"))
                existing = session.scalar(
                    select(AgentActionProposal).where(
                        AgentActionProposal.target_plan_check_id == plan.id,
                        AgentActionProposal.status == ActionProposalStatus.PROPOSED,
                        AgentActionProposal.expires_at > now,
                    )
                )
                if existing is not None:
                    if (
                        existing.created_by == identity.user_id
                        and existing.payload_hash == payload_hash
                    ):
                        return self._view(session, existing, identity=identity, role=role)
                    raise ConflictError("该 Plan Check 已有待处理的有效决策提案")

                state_snapshot = self._plan_state_snapshot(
                    plan,
                    approval=approval,
                    manifest=manifest,
                )
                state_hash = canonical_hash(state_snapshot)
                proposal_id = uuid4()
                expires_at = now + timedelta(hours=ACTION_PROPOSAL_TTL_HOURS)
                digest = build_plan_decision_proposal_digest(
                    proposal_id=proposal_id,
                    project_id=project_id,
                    plan_check_id=plan.id,
                    payload=payload,
                    target_state_hash=state_hash,
                    base_context_id=plan.context_id,
                    base_context_version=plan.context_version,
                    base_intent_id=plan.intent_id,
                    base_intent_version=plan.intent_version,
                    expires_at=expires_at,
                )
                proposal = AgentActionProposal(
                    id=proposal_id,
                    team_id=project.team_id,
                    project_id=project.id,
                    created_by=identity.user_id,
                    source_thread_id=thread.id,
                    source_run_id=run.id,
                    source_tool_call_id=tool_call_id,
                    source_draft_id=None,
                    source_draft_revision_id=None,
                    source_draft_revision=None,
                    operation=ActionProposalOperation.PLAN_CHECK_DECISION,
                    status=ActionProposalStatus.PROPOSED,
                    payload=payload.model_dump(mode="json"),
                    payload_hash=payload_hash,
                    source_candidate_hash=None,
                    target_plan_check_id=plan.id,
                    target_state_hash=state_hash,
                    base_context_id=plan.context_id,
                    base_context_version=plan.context_version,
                    base_intent_id=plan.intent_id,
                    base_intent_version=plan.intent_version,
                    base_policy_hash=None,
                    diff_snapshot=plan.planned_changes,
                    impact_snapshot=self._plan_impact_snapshot(
                        plan,
                        payload=payload,
                        state_snapshot=state_snapshot,
                    ),
                    pending_state_hash=None,
                    proposal_digest=digest,
                    expires_at=expires_at,
                )
                session.add(proposal)
                session.flush()
                session.add(
                    AuditLog(
                        team_id=project.team_id,
                        project_id=project.id,
                        actor_type="USER",
                        actor_id=identity.user_id,
                        action="agent.action_proposal.prepared",
                        target_type="AGENT_ACTION_PROPOSAL",
                        target_id=proposal.id,
                        before_value=None,
                        after_value={
                            "operation": proposal.operation.value,
                            "plan_check_id": str(plan.id),
                            "decision": payload.decision.value,
                            "target_state_hash": state_hash,
                            "proposal_digest": proposal.proposal_digest,
                            "expires_at": proposal.expires_at.isoformat(),
                            "run_id": str(run.id),
                            "tool_call_id": str(tool_call_id),
                            "session_id": str(identity.token_id),
                        },
                    )
                )
                return self._view(session, proposal, identity=identity, role=role)

        return run_with_serialization_retry(operation)

    def prepare_submission_decision_from_agent(
        self,
        *,
        project_id: UUID,
        identity: RequestIdentity,
        run_id: UUID,
        tool_call_id: UUID,
        request: SubmissionDecisionProposalPrepareInput,
    ) -> ActionProposalView:
        """冻结 Submission 审核决定；本方法不改变 Submission 或正式实验。"""

        self._require_web_identity(identity)
        if self._experiment_reviews is None:
            raise ConflictError("Submission 审核提案服务未装配")
        experiment_reviews = self._experiment_reviews
        payload = SubmissionDecisionRequest(
            decision=request.decision,
            decision_reason=request.decision_reason,
        )
        payload_hash = canonical_hash(payload.model_dump(mode="json"))

        def operation() -> ActionProposalView:
            with self._session_factory() as session, session.begin():
                run, thread = self._policy_drafts.require_agent_source(
                    session,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    project_id=project_id,
                    identity=identity,
                )
                existing_run = session.scalar(
                    select(AgentActionProposal).where(AgentActionProposal.source_run_id == run_id)
                )
                if existing_run is not None:
                    if (
                        existing_run.operation is not ActionProposalOperation.SUBMISSION_DECISION
                        or existing_run.target_submission_id != request.submission_id
                        or existing_run.payload_hash != payload_hash
                    ):
                        raise ConflictError("同一 Agent Run 已准备不同的高影响操作")
                    return self._view(session, existing_run, identity=identity)
                if session.scalar(
                    select(AgentPolicyDraftRevision.id).where(
                        AgentPolicyDraftRevision.source_run_id == run_id
                    )
                ):
                    raise ConflictError("同一 Agent Run 只能执行一次草稿或提案写操作")

                basis = experiment_reviews.load_proposal_basis_in_session(
                    session,
                    identity=identity,
                    project_id=project_id,
                    submission_id=request.submission_id,
                    for_update=True,
                )
                if (
                    payload.decision.value == "APPROVED"
                    and basis.eligibility is ReviewEligibility.BLOCKED
                ):
                    raise ConflictError("CRITICAL 或 blocking 风险的 Submission 不能准备批准提案")
                if payload.decision.value == "APPROVED" and basis.approval_material_issues:
                    raise ConflictError(
                        "Submission 批准材料不完整：" + "；".join(basis.approval_material_issues)
                    )

                now = datetime.now(UTC)
                existing = session.scalar(
                    select(AgentActionProposal).where(
                        AgentActionProposal.target_submission_id == request.submission_id,
                        AgentActionProposal.status == ActionProposalStatus.PROPOSED,
                        AgentActionProposal.expires_at > now,
                    )
                )
                if existing is not None:
                    if (
                        existing.created_by == identity.user_id
                        and existing.payload_hash == payload_hash
                    ):
                        return self._view(
                            session,
                            existing,
                            identity=identity,
                            role=basis.role,
                        )
                    raise ConflictError("该 Submission 已有待处理的有效审核提案")

                state_snapshot = self._submission_state_snapshot(basis)
                state_hash = canonical_hash(state_snapshot)
                proposal_id = uuid4()
                expires_at = now + timedelta(hours=ACTION_PROPOSAL_TTL_HOURS)
                trace = basis.receipt.trace
                digest = build_submission_decision_proposal_digest(
                    proposal_id=proposal_id,
                    project_id=project_id,
                    submission_id=request.submission_id,
                    payload=payload,
                    target_state_hash=state_hash,
                    base_context_id=trace.context_id,
                    base_context_version=trace.context_version,
                    base_intent_id=trace.intent_id,
                    base_intent_version=trace.intent_version,
                    expires_at=expires_at,
                )
                proposal = AgentActionProposal(
                    id=proposal_id,
                    team_id=basis.team_id,
                    project_id=project_id,
                    created_by=identity.user_id,
                    source_thread_id=thread.id,
                    source_run_id=run.id,
                    source_tool_call_id=tool_call_id,
                    source_draft_id=None,
                    source_draft_revision_id=None,
                    source_draft_revision=None,
                    operation=ActionProposalOperation.SUBMISSION_DECISION,
                    status=ActionProposalStatus.PROPOSED,
                    payload=payload.model_dump(mode="json"),
                    payload_hash=payload_hash,
                    source_candidate_hash=None,
                    target_plan_check_id=None,
                    target_submission_id=request.submission_id,
                    target_state_hash=state_hash,
                    base_context_id=trace.context_id,
                    base_context_version=trace.context_version,
                    base_intent_id=trace.intent_id,
                    base_intent_version=trace.intent_version,
                    base_policy_hash=None,
                    diff_snapshot=[
                        item.model_dump(mode="json") for item in basis.receipt.allowed_changes
                    ],
                    impact_snapshot=self._submission_impact_snapshot(
                        basis,
                        payload=payload,
                        state_snapshot=state_snapshot,
                    ),
                    pending_state_hash=None,
                    proposal_digest=digest,
                    expires_at=expires_at,
                )
                session.add(proposal)
                session.flush()
                session.add(
                    AuditLog(
                        team_id=basis.team_id,
                        project_id=project_id,
                        actor_type="USER",
                        actor_id=identity.user_id,
                        action="agent.action_proposal.prepared",
                        target_type="AGENT_ACTION_PROPOSAL",
                        target_id=proposal.id,
                        before_value=None,
                        after_value={
                            "operation": proposal.operation.value,
                            "submission_id": str(request.submission_id),
                            "decision": payload.decision.value,
                            "review_eligibility": basis.eligibility.value,
                            "target_state_hash": state_hash,
                            "proposal_digest": proposal.proposal_digest,
                            "expires_at": proposal.expires_at.isoformat(),
                            "run_id": str(run.id),
                            "tool_call_id": str(tool_call_id),
                            "session_id": str(identity.token_id),
                        },
                    )
                )
                return self._view(
                    session,
                    proposal,
                    identity=identity,
                    role=basis.role,
                )

        return run_with_serialization_retry(operation)

    def list_proposals(
        self,
        *,
        project_id: UUID,
        identity: RequestIdentity,
        cursor: str | None,
        limit: int,
    ) -> ActionProposalPage:
        self._require_web_identity(identity)
        offset = _decode_cursor(cursor)
        with self._session_factory() as session:
            _, role = self._require_project(session, project_id, identity)
            statement = select(AgentActionProposal).where(
                AgentActionProposal.project_id == project_id
            )
            if role is TeamRole.RESEARCHER:
                statement = statement.where(AgentActionProposal.created_by == identity.user_id)
            rows = list(
                session.scalars(
                    statement.order_by(
                        AgentActionProposal.created_at.desc(),
                        AgentActionProposal.id.desc(),
                    )
                    .offset(offset)
                    .limit(limit + 1)
                ).all()
            )
            return ActionProposalPage(
                items=[
                    self._view(session, row, identity=identity, role=role) for row in rows[:limit]
                ],
                next_cursor=_encode_cursor(offset + limit) if len(rows) > limit else None,
            )

    def get_proposal(
        self,
        *,
        project_id: UUID,
        proposal_id: UUID,
        identity: RequestIdentity,
    ) -> ActionProposalView:
        self._require_web_identity(identity)
        with self._session_factory() as session:
            proposal, role = self._require_proposal(
                session,
                project_id=project_id,
                proposal_id=proposal_id,
                identity=identity,
            )
            return self._view(session, proposal, identity=identity, role=role)

    def confirm(
        self,
        *,
        project_id: UUID,
        proposal_id: UUID,
        identity: RequestIdentity,
        idempotency_key: UUID,
        request: ActionProposalConfirmRequest,
    ) -> ActionProposalView:
        self._require_web_identity(identity)
        if not identity.recent_authentication:
            raise RecentAuthenticationRequiredError("确认高影响操作提案前需要完成近期身份认证")
        request_hash = canonical_hash(
            {
                "project_id": str(project_id),
                "proposal_id": str(proposal_id),
                **request.model_dump(mode="json"),
            }
        )

        def operation() -> ActionProposalView:
            with self._session_factory() as session, session.begin():
                proposal, role = self._require_proposal(
                    session,
                    project_id=project_id,
                    proposal_id=proposal_id,
                    identity=identity,
                    for_update=True,
                )
                replay = self._idempotency(
                    session,
                    identity=identity,
                    operation=ACTION_PROPOSAL_CONFIRM_OPERATION,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
                if replay is not None:
                    return ActionProposalView.model_validate(replay.response_snapshot)
                if proposal.proposal_digest != request.proposal_digest:
                    raise ConflictError("提案摘要不匹配，请刷新完整内容后重新确认")

                self._require_confirmation_permission(
                    session,
                    proposal=proposal,
                    identity=identity,
                    role=role,
                    for_update=True,
                )

                confirmability, reasons = self._confirmability(
                    session, proposal, identity=identity, for_update=True
                )
                if confirmability is ActionProposalConfirmability.TERMINAL:
                    raise ConflictError("该操作提案已终止，不能再次确认")
                if confirmability in {
                    ActionProposalConfirmability.EXPIRED,
                    ActionProposalConfirmability.STALE,
                }:
                    proposal.status = (
                        ActionProposalStatus.EXPIRED
                        if confirmability is ActionProposalConfirmability.EXPIRED
                        else ActionProposalStatus.STALE
                    )
                    proposal.execution_error = {
                        "code": proposal.status.value,
                        "reasons": reasons,
                    }
                    result = self._view(session, proposal, identity=identity, role=role)
                    self._save_idempotency(
                        session,
                        identity=identity,
                        operation=ACTION_PROPOSAL_CONFIRM_OPERATION,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                        response=result.model_dump(mode="json"),
                    )
                    self._audit_terminal(
                        session,
                        proposal=proposal,
                        identity=identity,
                        action=f"agent.action_proposal.{proposal.status.value.lower()}",
                        after_value={"reasons": reasons},
                    )
                    return result

                execution_result: dict[str, object]
                audit_result: dict[str, object]
                if proposal.operation is ActionProposalOperation.POLICY_PUBLISH:
                    publish_request = PolicyPublishRequest.model_validate(proposal.payload)
                    policy_key = uuid5(
                        NAMESPACE_URL,
                        f"agent-policy-publish:{proposal.id}:{idempotency_key}",
                    )
                    publish_result = self._web_management.publish_policy_in_session(
                        session,
                        project_id=project_id,
                        identity=identity,
                        idempotency_key=policy_key,
                        request=publish_request,
                        audit_context={
                            "proposal_id": str(proposal.id),
                            "proposal_digest": proposal.proposal_digest,
                            "source_draft_id": str(proposal.source_draft_id),
                            "source_draft_revision": proposal.source_draft_revision,
                        },
                    )
                    proposal.executed_context_id = publish_result.context_bundle.context.context_id
                    proposal.executed_context_version = (
                        publish_result.context_bundle.context.version
                    )
                    execution_result = publish_result.model_dump(mode="json")
                    audit_result = {
                        "context_id": str(proposal.executed_context_id),
                        "context_version": proposal.executed_context_version,
                        "policy_idempotency_key": str(policy_key),
                    }
                elif proposal.operation is ActionProposalOperation.PLAN_CHECK_DECISION:
                    if proposal.target_plan_check_id is None:
                        raise ConflictError("Plan 决策提案缺少目标 Plan Check")
                    if self._plan_approvals is None:
                        raise ConflictError("Plan 决策提案服务未装配")
                    decision_request = PlanCheckDecisionRequest.model_validate(proposal.payload)
                    decision_key = uuid5(
                        NAMESPACE_URL,
                        f"agent-plan-decision:{proposal.id}:{idempotency_key}",
                    )
                    decision_result = self._plan_approvals.decide_in_session(
                        session,
                        identity=identity,
                        project_id=project_id,
                        plan_check_id=proposal.target_plan_check_id,
                        idempotency_key=decision_key,
                        request=decision_request,
                        audit_context={
                            "proposal_id": str(proposal.id),
                            "proposal_digest": proposal.proposal_digest,
                        },
                    )
                    proposal.executed_approval_record_id = decision_result.approval_record_id
                    execution_result = decision_result.model_dump(mode="json")
                    audit_result = {
                        "plan_check_id": str(proposal.target_plan_check_id),
                        "approval_record_id": str(decision_result.approval_record_id),
                        "decision": decision_result.decision.value,
                        "plan_decision_idempotency_key": str(decision_key),
                    }
                else:
                    if proposal.target_submission_id is None:
                        raise ConflictError("Submission 审核提案缺少目标 Submission")
                    if self._experiment_reviews is None:
                        raise ConflictError("Submission 审核提案服务未装配")
                    submission_decision_request = SubmissionDecisionRequest.model_validate(
                        proposal.payload
                    )
                    decision_key = uuid5(
                        NAMESPACE_URL,
                        f"agent-submission-decision:{proposal.id}:{idempotency_key}",
                    )
                    submission_decision_result = self._experiment_reviews.decide_in_session(
                        session,
                        identity=identity,
                        project_id=project_id,
                        submission_id=proposal.target_submission_id,
                        idempotency_key=decision_key,
                        request=submission_decision_request,
                        audit_context={
                            "proposal_id": str(proposal.id),
                            "proposal_digest": proposal.proposal_digest,
                        },
                    )
                    proposal.executed_approval_record_id = (
                        submission_decision_result.approval_record_id
                    )
                    proposal.executed_experiment_id = submission_decision_result.experiment_id
                    execution_result = submission_decision_result.model_dump(mode="json")
                    audit_result = {
                        "submission_id": str(proposal.target_submission_id),
                        "approval_record_id": str(submission_decision_result.approval_record_id),
                        "experiment_id": (
                            str(submission_decision_result.experiment_id)
                            if submission_decision_result.experiment_id
                            else None
                        ),
                        "decision": submission_decision_result.decision.value,
                        "submission_decision_idempotency_key": str(decision_key),
                    }
                now = datetime.now(UTC)
                proposal.status = ActionProposalStatus.EXECUTED
                proposal.confirmed_by = identity.user_id
                proposal.confirmed_session_id = identity.token_id
                proposal.confirmed_at = now
                proposal.execution_result = execution_result
                proposal.execution_error = None
                result = self._view(session, proposal, identity=identity, role=role)
                self._save_idempotency(
                    session,
                    identity=identity,
                    operation=ACTION_PROPOSAL_CONFIRM_OPERATION,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    response=result.model_dump(mode="json"),
                )
                self._audit_terminal(
                    session,
                    proposal=proposal,
                    identity=identity,
                    action="agent.action_proposal.executed",
                    after_value=audit_result,
                )
                return result

        return run_with_serialization_retry(operation)

    def cancel(
        self,
        *,
        project_id: UUID,
        proposal_id: UUID,
        identity: RequestIdentity,
        idempotency_key: UUID,
        request: ActionProposalCancelRequest,
    ) -> ActionProposalView:
        self._require_web_identity(identity)
        request_hash = canonical_hash(
            {
                "project_id": str(project_id),
                "proposal_id": str(proposal_id),
                **request.model_dump(mode="json"),
            }
        )

        def operation() -> ActionProposalView:
            with self._session_factory() as session, session.begin():
                proposal, role = self._require_proposal(
                    session,
                    project_id=project_id,
                    proposal_id=proposal_id,
                    identity=identity,
                    for_update=True,
                )
                replay = self._idempotency(
                    session,
                    identity=identity,
                    operation=ACTION_PROPOSAL_CANCEL_OPERATION,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
                if replay is not None:
                    return self._view(session, proposal, identity=identity, role=role)
                if proposal.proposal_digest != request.proposal_digest:
                    raise ConflictError("提案摘要不匹配，请刷新完整内容后重试")
                if proposal.status in TERMINAL_PROPOSAL_STATUSES:
                    raise ConflictError("该操作提案已终止，不能取消")
                if role is TeamRole.RESEARCHER and proposal.created_by != identity.user_id:
                    raise ResourceNotFoundError("项目中不存在当前用户可访问的操作提案")

                now = datetime.now(UTC)
                proposal.status = ActionProposalStatus.CANCELED
                proposal.canceled_by = identity.user_id
                proposal.canceled_at = now
                proposal.cancel_reason = request.reason
                result = self._view(session, proposal, identity=identity, role=role)
                self._save_idempotency(
                    session,
                    identity=identity,
                    operation=ACTION_PROPOSAL_CANCEL_OPERATION,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    response=result.model_dump(mode="json"),
                )
                self._audit_terminal(
                    session,
                    proposal=proposal,
                    identity=identity,
                    action="agent.action_proposal.canceled",
                    after_value={"reason": request.reason},
                )
                return result

        return run_with_serialization_retry(operation)

    @staticmethod
    def _require_preparable(source: PolicyDraftProposalSource) -> None:
        if source.draft.status is not PolicyDraftStatus.ACTIVE:
            raise ConflictError("已取消的治理草稿不能准备发布提案")
        if source.freshness is not PolicyDraftFreshness.CURRENT:
            raise ConflictError("治理草稿基准版本已过期，请基于当前正式策略重新创建")
        if source.validation.readiness is not PolicyDraftReadiness.READY:
            raise ConflictError("治理草稿尚未通过确定性校验或仍有未解决歧义")
        if source.revision.revision != source.draft.current_revision:
            raise ConflictError("只能从治理草稿当前 revision 准备发布提案")
        if not source.revision.diff_snapshot:
            raise ConflictError("没有正式含义变化的治理草稿不能准备发布提案")

    @staticmethod
    def _require_plan_preparable(
        plan: PlanCheck,
        *,
        approval: ApprovalRecord | None,
        manifest: RunManifest | None,
    ) -> None:
        if (
            plan.check_result is not CheckResult.NEEDS_APPROVAL
            or plan.approval_status is not ApprovalStatus.PENDING
        ):
            raise ConflictError("只有 NEEDS_APPROVAL/PENDING 的 Plan Check 可以准备决策提案")
        if approval is not None:
            raise ConflictError("该 Plan Check 已存在最终审批记录")
        if manifest is not None:
            raise ConflictError("已创建 Run Manifest 的 Plan Check 不能准备决策提案")

    @staticmethod
    def _plan_approval(session: Session, plan_check_id: UUID) -> ApprovalRecord | None:
        return session.scalar(
            select(ApprovalRecord).where(
                ApprovalRecord.target_type == ApprovalTargetType.PLAN_CHECK,
                ApprovalRecord.target_id == plan_check_id,
            )
        )

    @staticmethod
    def _plan_manifest(session: Session, plan_check_id: UUID) -> RunManifest | None:
        return session.scalar(select(RunManifest).where(RunManifest.plan_check_id == plan_check_id))

    @staticmethod
    def _plan_state_snapshot(
        plan: PlanCheck,
        *,
        approval: ApprovalRecord | None,
        manifest: RunManifest | None,
    ) -> dict[str, object]:
        report = (
            {
                key: value
                for key, value in plan.report.items()
                if key not in {"approval_status", "can_create_manifest"}
            }
            if isinstance(plan.report, dict)
            else {}
        )
        return {
            "plan_check_id": str(plan.id),
            "project_id": str(plan.project_id),
            "requester_id": str(plan.requester_id),
            "context_id": str(plan.context_id),
            "context_version": plan.context_version,
            "intent_id": str(plan.intent_id),
            "intent_version": plan.intent_version,
            "experiment_mode": plan.experiment_mode.value,
            "request_hash": plan.request_hash,
            "input_config_hash": plan.input_config_hash,
            "input_document_hash": plan.input_document_hash,
            "configuration_document": plan.configuration_document,
            "parsed_config": plan.parsed_config,
            "context_snapshot": plan.context_snapshot,
            "intent_snapshot": plan.intent_snapshot,
            "constraint_snapshot": plan.constraint_snapshot,
            "planned_changes": plan.planned_changes,
            "git_commit": plan.git_commit,
            "command": plan.command,
            "local_attestation": plan.local_attestation,
            "check_result": plan.check_result.value,
            "approval_status": plan.approval_status.value,
            "risk_level": plan.risk_level.value,
            "report": report,
            "approval_record_id": str(approval.id) if approval else None,
            "manifest_id": str(manifest.id) if manifest else None,
        }

    @staticmethod
    def _plan_impact_snapshot(
        plan: PlanCheck,
        *,
        payload: PlanCheckDecisionRequest,
        state_snapshot: dict[str, object],
    ) -> dict[str, object]:
        report = state_snapshot.get("report")
        risks = report.get("risks", []) if isinstance(report, dict) else []
        return {
            "plan_check_id": str(plan.id),
            "requester_id": str(plan.requester_id),
            "check_result": plan.check_result.value,
            "approval_status": plan.approval_status.value,
            "risk_level": plan.risk_level.value,
            "context_version": plan.context_version,
            "intent_version": plan.intent_version,
            "decision": payload.decision.value,
            "decision_reason": payload.decision_reason,
            "decision_effect": (
                "确认后 Plan 可用于创建一次不可变 Run Manifest"
                if payload.decision.value == "APPROVED"
                else "确认后 Plan 将被最终拒绝，不能创建 Run Manifest"
            ),
            "risks": risks,
            "planned_change_count": len(plan.planned_changes),
            "source_report": report,
        }

    @staticmethod
    def _submission_state_snapshot(
        basis: SubmissionReviewBasis,
    ) -> dict[str, object]:
        submission = basis.submission
        manifest = basis.manifest
        plan = basis.plan
        embedding = basis.embedding
        risks = [
            {
                "id": str(item.id),
                "risk_fingerprint": item.risk_fingerprint,
                "risk_type": item.risk_type,
                "severity": item.severity.value,
                "field_path": item.field_path,
                "previous_value": item.previous_value,
                "current_value": item.current_value,
                "expected_value": item.expected_value,
                "rule_id": item.rule_id,
                "message": item.message,
                "impact": item.impact,
                "evidence_type": item.evidence_type.value if item.evidence_type else None,
                "evidence_source": item.evidence_source,
                "collected_at": item.collected_at.isoformat() if item.collected_at else None,
                "collection_tool": item.collection_tool,
                "constraint_source": (
                    item.constraint_source.value if item.constraint_source else None
                ),
                "constraint_status": (
                    item.constraint_status.value if item.constraint_status else None
                ),
                "inference_basis": item.inference_basis,
                "confidence": item.confidence,
                "constraint_candidates": item.constraint_candidates,
                "recommendation": item.recommendation,
                "blocking": item.blocking,
                "resolved": item.resolved,
            }
            for item in basis.risks
        ]
        artifacts = [
            {
                "id": str(item.id),
                "filename": item.filename,
                "mime_type": item.mime_type,
                "size_bytes": item.size_bytes,
                "s3_key": item.s3_key,
                "sha256": item.sha256,
                "artifact_type": item.artifact_type.value,
                "cloud_hash_verified": item.cloud_hash_verified,
                "verified_at": item.verified_at.isoformat() if item.verified_at else None,
                "verification_evidence": item.verification_evidence,
                "s3_version_id": item.s3_version_id,
                "experiment_id": str(item.experiment_id) if item.experiment_id else None,
            }
            for item in basis.artifacts
        ]
        return {
            "schema_version": 1,
            "submission": {
                "id": str(submission.id),
                "project_id": str(submission.project_id),
                "run_manifest_id": str(submission.run_manifest_id),
                "submitted_by": str(submission.submitted_by),
                "source_agent": submission.source_agent,
                "request_hash": submission.request_hash,
                "manifest_hash": submission.manifest_hash,
                "declared_experiment_status": submission.declared_experiment_status.value,
                "declared_metrics": submission.declared_metrics,
                "evidence_snapshot": submission.evidence_snapshot,
                "status": submission.status.value,
                "workflow_status": submission.workflow_status.value,
                "processing_step": (
                    submission.processing_step.value if submission.processing_step else None
                ),
                "analysis_snapshot_hash": canonical_hash(submission.analysis_snapshot),
                "generated_summary_hash": canonical_hash(submission.generated_summary),
                "review_receipt": basis.receipt.model_dump(mode="json"),
            },
            "trace": {
                "manifest_id": str(manifest.id) if manifest else None,
                "manifest_hash": manifest.manifest_hash if manifest else None,
                "plan_check_id": str(manifest.plan_check_id) if manifest else None,
                "plan_check_result": plan.check_result.value if plan else None,
                "plan_approval_status": plan.approval_status.value if plan else None,
                "context_id": str(manifest.context_id) if manifest else None,
                "context_version": manifest.context_version if manifest else None,
                "context_found": basis.context is not None,
                "intent_id": str(manifest.intent_id) if manifest else None,
                "intent_version": manifest.intent_version if manifest else None,
                "intent_found": basis.intent is not None,
                "config_hash": manifest.config_hash if manifest else None,
                "git_commit": manifest.git_commit if manifest else None,
                "command": manifest.command if manifest else None,
            },
            "review_eligibility": basis.eligibility.value,
            "risks": risks,
            "artifacts": artifacts,
            "embedding": (
                {
                    "id": str(embedding.id),
                    "project_id": str(embedding.project_id),
                    "provider": embedding.provider,
                    "model_id": embedding.model_id,
                    "dimension": embedding.dimension,
                    "normalized": embedding.normalized,
                    "document_version": embedding.document_version,
                    "input_sha256": embedding.input_sha256,
                    "input_text_hash": canonical_hash(embedding.input_text),
                }
                if embedding
                else None
            ),
            "approval_material_issues": list(basis.approval_material_issues),
            "approval_record_id": None,
            "experiment_id": None,
        }

    @staticmethod
    def _submission_impact_snapshot(
        basis: SubmissionReviewBasis,
        *,
        payload: SubmissionDecisionRequest,
        state_snapshot: dict[str, object],
    ) -> dict[str, object]:
        return {
            "submission_id": str(basis.submission.id),
            "submitted_by": str(basis.submission.submitted_by),
            "decision": payload.decision.value,
            "decision_reason": payload.decision_reason,
            "decision_effect": (
                "确认后将在同一事务中创建正式 Experiment、Metric、Memory 并关联 Artifact"
                if payload.decision.value == "APPROVED"
                else "确认后 Submission 将被最终拒绝，不会创建正式 Experiment"
            ),
            "review_eligibility": basis.eligibility.value,
            "highest_risk": (
                basis.receipt.highest_risk.value if basis.receipt.highest_risk else None
            ),
            "objective": basis.receipt.objective,
            "source_hash": basis.receipt.source_hash,
            "review_receipt": basis.receipt.model_dump(mode="json"),
            "trace": state_snapshot["trace"],
            "risks": state_snapshot["risks"],
            "artifacts": state_snapshot["artifacts"],
            "embedding": state_snapshot["embedding"],
            "approval_material_complete": not basis.approval_material_issues,
            "approval_material_issues": list(basis.approval_material_issues),
        }

    def _confirmability(
        self,
        session: Session,
        proposal: AgentActionProposal,
        *,
        identity: RequestIdentity,
        for_update: bool = False,
    ) -> tuple[ActionProposalConfirmability, list[str]]:
        if proposal.status in TERMINAL_PROPOSAL_STATUSES:
            stored_reasons = (
                proposal.execution_error.get("reasons")
                if isinstance(proposal.execution_error, dict)
                else None
            )
            return ActionProposalConfirmability.TERMINAL, [
                *(
                    [str(item) for item in stored_reasons]
                    if isinstance(stored_reasons, list)
                    else [f"提案持久化状态为 {proposal.status.value}"]
                )
            ]
        if _utc(proposal.expires_at) <= datetime.now(UTC):
            return ActionProposalConfirmability.EXPIRED, ["提案已超过 24 小时有效期"]
        if proposal.operation is ActionProposalOperation.PLAN_CHECK_DECISION:
            return self._plan_confirmability(
                session,
                proposal,
                for_update=for_update,
            )
        if proposal.operation is ActionProposalOperation.SUBMISSION_DECISION:
            return self._submission_confirmability(
                session,
                proposal,
                identity=identity,
                for_update=for_update,
            )

        reasons: list[str] = []
        if any(
            value is None
            for value in (
                proposal.source_draft_id,
                proposal.source_draft_revision_id,
                proposal.source_draft_revision,
                proposal.source_candidate_hash,
                proposal.base_policy_hash,
                proposal.pending_state_hash,
            )
        ):
            return ActionProposalConfirmability.STALE, ["Policy 提案来源字段不完整"]
        assert proposal.source_draft_id is not None
        assert proposal.source_draft_revision_id is not None
        assert proposal.source_draft_revision is not None
        assert proposal.source_candidate_hash is not None
        assert proposal.base_policy_hash is not None
        assert proposal.pending_state_hash is not None
        try:
            source = self._policy_drafts.load_proposal_source(
                session,
                project_id=proposal.project_id,
                draft_id=proposal.source_draft_id,
                identity=identity,
                for_update=for_update,
            )
        except (ConflictError, InputValidationError, ResourceNotFoundError) as exc:
            return ActionProposalConfirmability.STALE, [str(exc)]

        if source.draft.status is not PolicyDraftStatus.ACTIVE:
            reasons.append("来源草稿已取消")
        if source.draft.current_revision != proposal.source_draft_revision:
            reasons.append("来源草稿已有新 revision")
        if source.revision.id != proposal.source_draft_revision_id:
            reasons.append("来源草稿 revision 标识不匹配")
        if source.revision.candidate_hash != proposal.source_candidate_hash:
            reasons.append("候选 Policy Bundle 哈希已变化")
        if source.freshness is not PolicyDraftFreshness.CURRENT:
            reasons.append("正式 Context 或 Intent 版本已变化")
        if source.validation.readiness is not PolicyDraftReadiness.READY:
            reasons.append("候选 Bundle 不再满足发布校验")
        if source.revision.unresolved_ambiguities:
            reasons.append("来源草稿仍有未解决歧义")
        if source.impact.pending_state_hash != proposal.pending_state_hash:
            reasons.append("待审批 Plan 或待审核 Submission 状态已变化")

        payload = PolicyPublishRequest(
            expected_context_version=source.draft.base_context_version,
            context=source.candidate.context,
            intent=source.candidate.intent,
            constraints=source.candidate.constraints,
        )
        if canonical_hash(payload.model_dump(mode="json")) != proposal.payload_hash:
            reasons.append("冻结发布请求与当前草稿不一致")
        digest = build_action_proposal_digest(
            proposal_id=proposal.id,
            operation=proposal.operation,
            project_id=proposal.project_id,
            payload=PolicyPublishRequest.model_validate(proposal.payload),
            source_draft_id=proposal.source_draft_id,
            source_draft_revision_id=proposal.source_draft_revision_id,
            source_draft_revision=proposal.source_draft_revision,
            source_candidate_hash=proposal.source_candidate_hash,
            base_policy_hash=proposal.base_policy_hash,
            pending_state_hash=proposal.pending_state_hash,
            expires_at=_utc(proposal.expires_at),
        )
        if digest != proposal.proposal_digest:
            reasons.append("提案摘要校验失败")
        return (
            (ActionProposalConfirmability.STALE, reasons)
            if reasons
            else (ActionProposalConfirmability.READY, [])
        )

    def _plan_confirmability(
        self,
        session: Session,
        proposal: AgentActionProposal,
        *,
        for_update: bool,
    ) -> tuple[ActionProposalConfirmability, list[str]]:
        plan_id = proposal.target_plan_check_id
        state_hash = proposal.target_state_hash
        if plan_id is None or state_hash is None:
            return ActionProposalConfirmability.STALE, ["Plan 提案目标字段不完整"]
        statement = select(PlanCheck).where(
            PlanCheck.id == plan_id,
            PlanCheck.project_id == proposal.project_id,
        )
        if for_update:
            statement = statement.with_for_update()
        plan = session.scalar(statement)
        if plan is None:
            return ActionProposalConfirmability.STALE, ["目标 Plan Check 已不存在"]

        approval = self._plan_approval(session, plan.id)
        manifest = self._plan_manifest(session, plan.id)
        reasons: list[str] = []
        if plan.check_result is not CheckResult.NEEDS_APPROVAL:
            reasons.append("Plan Check 已不再需要审批")
        if plan.approval_status is not ApprovalStatus.PENDING:
            reasons.append(f"Plan Check 审批状态已变为 {plan.approval_status.value}")
        if approval is not None:
            reasons.append("Plan Check 已存在最终审批记录")
        if manifest is not None:
            reasons.append("Plan Check 已创建 Run Manifest")
        if (
            plan.context_id != proposal.base_context_id
            or plan.context_version != proposal.base_context_version
            or plan.intent_id != proposal.base_intent_id
            or plan.intent_version != proposal.base_intent_version
        ):
            reasons.append("Plan Check 的 Context 或 Intent 追溯版本不匹配")
        current_snapshot = self._plan_state_snapshot(
            plan,
            approval=approval,
            manifest=manifest,
        )
        if canonical_hash(current_snapshot) != state_hash:
            reasons.append("Plan Check 正式依据已变化")
        try:
            payload = PlanCheckDecisionRequest.model_validate(proposal.payload)
        except ValueError:
            return ActionProposalConfirmability.STALE, ["Plan 决策 payload 已损坏"]
        if canonical_hash(payload.model_dump(mode="json")) != proposal.payload_hash:
            reasons.append("冻结 Plan 决策与 payload 哈希不一致")
        digest = build_plan_decision_proposal_digest(
            proposal_id=proposal.id,
            project_id=proposal.project_id,
            plan_check_id=plan_id,
            payload=payload,
            target_state_hash=state_hash,
            base_context_id=proposal.base_context_id,
            base_context_version=proposal.base_context_version,
            base_intent_id=proposal.base_intent_id,
            base_intent_version=proposal.base_intent_version,
            expires_at=_utc(proposal.expires_at),
        )
        if digest != proposal.proposal_digest:
            reasons.append("提案摘要校验失败")
        return (
            (ActionProposalConfirmability.STALE, reasons)
            if reasons
            else (ActionProposalConfirmability.READY, [])
        )

    def _submission_confirmability(
        self,
        session: Session,
        proposal: AgentActionProposal,
        *,
        identity: RequestIdentity,
        for_update: bool,
    ) -> tuple[ActionProposalConfirmability, list[str]]:
        submission_id = proposal.target_submission_id
        state_hash = proposal.target_state_hash
        if submission_id is None or state_hash is None:
            return ActionProposalConfirmability.STALE, ["Submission 提案目标字段不完整"]
        if self._experiment_reviews is None:
            return ActionProposalConfirmability.STALE, ["Submission 审核提案服务未装配"]
        try:
            basis = self._experiment_reviews.load_proposal_basis_in_session(
                session,
                identity=identity,
                project_id=proposal.project_id,
                submission_id=submission_id,
                for_update=for_update,
            )
            payload = SubmissionDecisionRequest.model_validate(proposal.payload)
        except (ConflictError, ResourceNotFoundError, ValueError) as exc:
            return ActionProposalConfirmability.STALE, [str(exc)]

        reasons: list[str] = []
        trace = basis.receipt.trace
        if (
            trace.context_id != proposal.base_context_id
            or trace.context_version != proposal.base_context_version
            or trace.intent_id != proposal.base_intent_id
            or trace.intent_version != proposal.base_intent_version
        ):
            reasons.append("Submission 的 Context 或 Intent 追溯版本不匹配")
        current_snapshot = self._submission_state_snapshot(basis)
        if canonical_hash(current_snapshot) != state_hash:
            reasons.append("Submission 正式审核依据已变化")
        if canonical_hash(payload.model_dump(mode="json")) != proposal.payload_hash:
            reasons.append("冻结 Submission 决定与 payload 哈希不一致")
        if payload.decision is ApprovalDecision.APPROVED:
            if basis.eligibility is ReviewEligibility.BLOCKED:
                reasons.append("CRITICAL 或 blocking 风险的 Submission 不能批准")
            if basis.approval_material_issues:
                reasons.extend(basis.approval_material_issues)
        digest = build_submission_decision_proposal_digest(
            proposal_id=proposal.id,
            project_id=proposal.project_id,
            submission_id=submission_id,
            payload=payload,
            target_state_hash=state_hash,
            base_context_id=proposal.base_context_id,
            base_context_version=proposal.base_context_version,
            base_intent_id=proposal.base_intent_id,
            base_intent_version=proposal.base_intent_version,
            expires_at=_utc(proposal.expires_at),
        )
        if digest != proposal.proposal_digest:
            reasons.append("提案摘要校验失败")
        return (
            (ActionProposalConfirmability.STALE, reasons)
            if reasons
            else (ActionProposalConfirmability.READY, [])
        )

    def _view(
        self,
        session: Session,
        proposal: AgentActionProposal,
        *,
        identity: RequestIdentity,
        role: TeamRole | None = None,
    ) -> ActionProposalView:
        if role is None:
            _, role = self._require_project(session, proposal.project_id, identity)
        confirmability, reasons = self._confirmability(session, proposal, identity=identity)
        actions: list[str] = []
        if proposal.status is ActionProposalStatus.PROPOSED:
            if confirmability is ActionProposalConfirmability.READY and self._can_confirm(
                session,
                proposal=proposal,
                identity=identity,
                role=role,
            ):
                actions.append("CONFIRM")
            if role is TeamRole.OWNER or proposal.created_by == identity.user_id:
                actions.append("CANCEL")
        payload: PolicyPublishRequest | PlanCheckDecisionRequest | SubmissionDecisionRequest
        if proposal.operation is ActionProposalOperation.POLICY_PUBLISH:
            payload = PolicyPublishRequest.model_validate(proposal.payload)
        elif proposal.operation is ActionProposalOperation.PLAN_CHECK_DECISION:
            payload = PlanCheckDecisionRequest.model_validate(proposal.payload)
        else:
            payload = SubmissionDecisionRequest.model_validate(proposal.payload)
        return ActionProposalView(
            proposal_id=proposal.id,
            project_id=proposal.project_id,
            created_by=proposal.created_by,
            operation=proposal.operation,
            status=proposal.status,
            confirmability=confirmability,
            confirmability_reasons=reasons,
            allowed_actions=actions,
            source_thread_id=proposal.source_thread_id,
            source_run_id=proposal.source_run_id,
            source_tool_call_id=proposal.source_tool_call_id,
            source_draft_id=proposal.source_draft_id,
            source_draft_revision_id=proposal.source_draft_revision_id,
            source_draft_revision=proposal.source_draft_revision,
            source_candidate_hash=proposal.source_candidate_hash,
            target_plan_check_id=proposal.target_plan_check_id,
            target_submission_id=proposal.target_submission_id,
            target_state_hash=proposal.target_state_hash,
            payload=payload,
            payload_hash=proposal.payload_hash,
            base_context_id=proposal.base_context_id,
            base_context_version=proposal.base_context_version,
            base_intent_id=proposal.base_intent_id,
            base_intent_version=proposal.base_intent_version,
            base_policy_hash=proposal.base_policy_hash,
            diff_snapshot=proposal.diff_snapshot,
            impact_snapshot=proposal.impact_snapshot,
            pending_state_hash=proposal.pending_state_hash,
            proposal_digest=proposal.proposal_digest,
            expires_at=proposal.expires_at,
            created_at=proposal.created_at,
            updated_at=proposal.updated_at,
            confirmed_by=proposal.confirmed_by,
            confirmed_at=proposal.confirmed_at,
            canceled_by=proposal.canceled_by,
            canceled_at=proposal.canceled_at,
            cancel_reason=proposal.cancel_reason,
            executed_context_id=proposal.executed_context_id,
            executed_context_version=proposal.executed_context_version,
            executed_approval_record_id=proposal.executed_approval_record_id,
            executed_experiment_id=proposal.executed_experiment_id,
            execution_result=proposal.execution_result,
            execution_error=proposal.execution_error,
        )

    def _require_proposal(
        self,
        session: Session,
        *,
        project_id: UUID,
        proposal_id: UUID,
        identity: RequestIdentity,
        for_update: bool = False,
        owner: bool = False,
    ) -> tuple[AgentActionProposal, TeamRole]:
        _, role = self._require_project(session, project_id, identity)
        if owner and role is not TeamRole.OWNER:
            raise AuthorizationError("只有 Owner 可以确认高影响操作提案")
        statement = select(AgentActionProposal).where(
            AgentActionProposal.id == proposal_id,
            AgentActionProposal.project_id == project_id,
        )
        if for_update:
            statement = statement.with_for_update()
        proposal = session.scalar(statement)
        if proposal is None or (
            role is TeamRole.RESEARCHER and proposal.created_by != identity.user_id
        ):
            raise ResourceNotFoundError("项目中不存在当前用户可访问的操作提案")
        return proposal, role

    def _can_confirm(
        self,
        session: Session,
        *,
        proposal: AgentActionProposal,
        identity: RequestIdentity,
        role: TeamRole,
    ) -> bool:
        if proposal.operation is not ActionProposalOperation.SUBMISSION_DECISION:
            return role is TeamRole.OWNER
        if self._experiment_reviews is None or proposal.target_submission_id is None:
            return False
        try:
            basis = self._experiment_reviews.load_proposal_basis_in_session(
                session,
                identity=identity,
                project_id=proposal.project_id,
                submission_id=proposal.target_submission_id,
            )
            payload = SubmissionDecisionRequest.model_validate(proposal.payload)
        except (ConflictError, ResourceNotFoundError, ValueError):
            return False
        if role is TeamRole.OWNER:
            return payload.decision is ApprovalDecision.REJECTED or (
                basis.eligibility is not ReviewEligibility.BLOCKED
            )
        if basis.submission.submitted_by != identity.user_id:
            return False
        return payload.decision is ApprovalDecision.REJECTED or (
            basis.eligibility is ReviewEligibility.RESEARCHER_OR_OWNER
        )

    def _require_confirmation_permission(
        self,
        session: Session,
        *,
        proposal: AgentActionProposal,
        identity: RequestIdentity,
        role: TeamRole,
        for_update: bool,
    ) -> None:
        if proposal.operation is not ActionProposalOperation.SUBMISSION_DECISION:
            if role is not TeamRole.OWNER:
                raise AuthorizationError("只有 Owner 可以确认该高影响操作提案")
            if "project:write" not in identity.scopes:
                raise AuthorizationError("当前身份缺少 project:write scope")
            return
        if self._experiment_reviews is None or proposal.target_submission_id is None:
            raise ConflictError("Submission 审核提案服务或目标未装配")
        basis = self._experiment_reviews.load_proposal_basis_in_session(
            session,
            identity=identity,
            project_id=proposal.project_id,
            submission_id=proposal.target_submission_id,
            for_update=for_update,
        )
        payload = SubmissionDecisionRequest.model_validate(proposal.payload)
        if role is TeamRole.OWNER:
            return
        if basis.submission.submitted_by != identity.user_id:
            raise AuthorizationError("Researcher 只能确认自己 Submission 的审核提案")
        if (
            payload.decision is ApprovalDecision.APPROVED
            and basis.eligibility is not ReviewEligibility.RESEARCHER_OR_OWNER
        ):
            raise AuthorizationError("HIGH 风险 Submission 的批准提案只能由 Owner 确认")

    def _require_project(
        self,
        session: Session,
        project_id: UUID,
        identity: RequestIdentity,
    ) -> tuple[Project, TeamRole]:
        if identity.project_id is not None and identity.project_id != project_id:
            raise AuthorizationError("当前身份绑定到其他项目")
        project = self._projects.require_project_member(
            session,
            project_id=project_id,
            user_id=identity.user_id,
            team_id=identity.team_id,
        )
        role = self._projects.require_member(
            session,
            user_id=identity.user_id,
            team_id=project.team_id,
        )
        return project, role

    @staticmethod
    def _require_web_identity(
        identity: RequestIdentity,
        *,
        write: bool = False,
    ) -> None:
        if identity.authentication_method != "WEB_SESSION":
            raise AuthorizationError("操作提案只允许通过服务端 Web Session 使用")
        scope = "project:write" if write else "project:read"
        if scope not in identity.scopes:
            raise AuthorizationError(f"当前身份缺少 {scope} scope")

    @staticmethod
    def _idempotency(
        session: Session,
        *,
        identity: RequestIdentity,
        operation: str,
        idempotency_key: UUID,
        request_hash: str,
    ) -> IdempotencyRecord | None:
        row = session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.actor_id == identity.user_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
        )
        if row is not None and (row.request_hash != request_hash or row.response_snapshot is None):
            raise ConflictError("相同 Idempotency-Key 已用于不同的操作提案请求")
        return row

    @staticmethod
    def _save_idempotency(
        session: Session,
        *,
        identity: RequestIdentity,
        operation: str,
        idempotency_key: UUID,
        request_hash: str,
        response: dict[str, object],
    ) -> None:
        session.add(
            IdempotencyRecord(
                actor_id=identity.user_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response_snapshot=response,
                operation_status=IdempotencyOperationStatus.COMPLETED,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )

    @staticmethod
    def _audit_terminal(
        session: Session,
        *,
        proposal: AgentActionProposal,
        identity: RequestIdentity,
        action: str,
        after_value: dict[str, object],
    ) -> None:
        session.add(
            AuditLog(
                team_id=proposal.team_id,
                project_id=proposal.project_id,
                actor_type="USER",
                actor_id=identity.user_id,
                action=action,
                target_type="AGENT_ACTION_PROPOSAL",
                target_id=proposal.id,
                before_value={"status": ActionProposalStatus.PROPOSED.value},
                after_value={
                    "status": proposal.status.value,
                    "proposal_digest": proposal.proposal_digest,
                    "session_id": str(identity.token_id),
                    **after_value,
                },
            )
        )
