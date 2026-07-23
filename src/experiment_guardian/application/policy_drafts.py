"""治理 Agent Policy Bundle 草稿的应用服务。

所有写入都落在独立候选表中。正式策略发布、Plan 审批和 Submission 审核继续由既有服务
负责；这里的影响分析只调用纯规则引擎并返回模拟结果。
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.errors import (
    AuthorizationError,
    ConflictError,
    InputValidationError,
    ResourceNotFoundError,
)
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.transactions import run_with_serialization_retry
from experiment_guardian.domain.administration import (
    InitialConstraintInput,
    InitialContextInput,
    InitialIntentInput,
)
from experiment_guardian.domain.contracts import (
    ConfigurationDocument,
    LocalAttestation,
    ParameterConstraint,
    PlanEvaluationInput,
    ProjectContextBundle,
)
from experiment_guardian.domain.enums import (
    ApprovalStatus,
    ConstraintSource,
    IdempotencyOperationStatus,
    PolicyDraftFreshness,
    PolicyDraftReadiness,
    PolicyDraftSource,
    PolicyDraftStatus,
    SubmissionStatus,
    TeamRole,
    VerificationStatus,
)
from experiment_guardian.domain.plan_check import evaluate_plan
from experiment_guardian.domain.policy_draft import (
    PolicyDraftAbandonRequest,
    PolicyDraftAmbiguity,
    PolicyDraftCandidate,
    PolicyDraftCreateInput,
    PolicyDraftDiffItem,
    PolicyDraftImpact,
    PolicyDraftPage,
    PolicyDraftPlanSimulation,
    PolicyDraftRevisionInput,
    PolicyDraftRevisionSummary,
    PolicyDraftRevisionView,
    PolicyDraftSubmissionImpact,
    PolicyDraftSummary,
    PolicyDraftValidation,
    PolicyDraftView,
    canonical_hash,
    diff_policy_candidates,
    max_attention,
    render_policy_draft_narrative,
    validate_policy_candidate,
)
from experiment_guardian.infrastructure.models import (
    AgentPolicyDraft,
    AgentPolicyDraftRevision,
    AgentRun,
    AgentThread,
    AgentToolCall,
    AuditLog,
    ExperimentSubmission,
    IdempotencyRecord,
    PlanCheck,
    Project,
    RunManifest,
)
from experiment_guardian.infrastructure.repositories import SqlAlchemyProjectRepository

POLICY_DRAFT_REVISE_OPERATION = "agent.policy_draft.revise"
POLICY_DRAFT_ABANDON_OPERATION = "agent.policy_draft.abandon"
ACTIVE_SUBMISSION_STATUSES = {
    SubmissionStatus.RECEIVED,
    SubmissionStatus.UPLOAD_VERIFIED,
    SubmissionStatus.PROCESSING,
    SubmissionStatus.NEEDS_REVIEW,
}
IMPACT_ITEM_LIMIT = 20


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        value = int(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise InputValidationError("治理草稿分页 cursor 无效") from exc
    if value < 0:
        raise InputValidationError("治理草稿分页 cursor 无效")
    return value


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii")


class PolicyDraftService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        projects: SqlAlchemyProjectRepository,
    ) -> None:
        self._session_factory = session_factory
        self._projects = projects

    def list_drafts(
        self,
        *,
        project_id: UUID,
        identity: RequestIdentity,
        status: PolicyDraftStatus,
        cursor: str | None,
        limit: int,
    ) -> PolicyDraftPage:
        self._require_web_identity(identity)
        offset = _decode_cursor(cursor)
        with self._session_factory() as session:
            _, role = self._require_project(session, project_id, identity)
            statement = select(AgentPolicyDraft).where(
                AgentPolicyDraft.project_id == project_id,
                AgentPolicyDraft.status == status,
            )
            if role is TeamRole.RESEARCHER:
                statement = statement.where(AgentPolicyDraft.created_by == identity.user_id)
            rows = list(
                session.scalars(
                    statement.order_by(
                        AgentPolicyDraft.updated_at.desc(),
                        AgentPolicyDraft.id.desc(),
                    )
                    .offset(offset)
                    .limit(limit + 1)
                ).all()
            )
            current_bundle = self._projects.load_context_bundle(
                session,
                project_id=project_id,
                user_id=identity.user_id,
                team_id=identity.team_id,
            )
            return PolicyDraftPage(
                items=[
                    self._summary(
                        session,
                        item,
                        current_bundle=current_bundle,
                    )
                    for item in rows[:limit]
                ],
                next_cursor=_encode_cursor(offset + limit) if len(rows) > limit else None,
            )

    def get_draft(
        self,
        *,
        project_id: UUID,
        draft_id: UUID,
        identity: RequestIdentity,
    ) -> PolicyDraftView:
        self._require_web_identity(identity)
        with self._session_factory() as session:
            draft = self._require_draft(
                session,
                project_id=project_id,
                draft_id=draft_id,
                identity=identity,
            )
            return self._view(session, draft, identity=identity)

    def get_revision(
        self,
        *,
        project_id: UUID,
        draft_id: UUID,
        revision: int,
        identity: RequestIdentity,
    ) -> PolicyDraftRevisionView:
        self._require_web_identity(identity)
        with self._session_factory() as session:
            draft = self._require_draft(
                session,
                project_id=project_id,
                draft_id=draft_id,
                identity=identity,
            )
            row = self._require_revision(session, draft.id, revision)
            return self._revision_view(session, draft, row)

    def create_from_agent(
        self,
        *,
        project_id: UUID,
        identity: RequestIdentity,
        run_id: UUID,
        tool_call_id: UUID,
        request: PolicyDraftCreateInput,
    ) -> PolicyDraftView:
        self._require_web_identity(identity)
        request_hash = canonical_hash(request.model_dump(mode="json"))

        def operation() -> PolicyDraftView:
            with self._session_factory() as session, session.begin():
                existing = session.scalar(
                    select(AgentPolicyDraftRevision).where(
                        AgentPolicyDraftRevision.source_run_id == run_id
                    )
                )
                if existing is not None:
                    if existing.source_request_hash != request_hash:
                        raise ConflictError("同一 Agent Run 已执行不同的草稿写操作")
                    draft = session.get(AgentPolicyDraft, existing.draft_id)
                    if draft is None:
                        raise ConflictError("Agent 草稿幂等记录不完整")
                    return self._view(session, draft, identity=identity)

                project, _ = self._require_project(session, project_id, identity)
                run, thread = self._require_agent_source(
                    session,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    project_id=project_id,
                    identity=identity,
                )
                bundle = self._projects.load_context_bundle(
                    session,
                    project_id=project_id,
                    user_id=identity.user_id,
                    team_id=identity.team_id,
                )
                self._require_base_reference(bundle, request)
                base_candidate = self._candidate_from_bundle(bundle)
                base_snapshot = self._base_snapshot(bundle, base_candidate)
                diff = diff_policy_candidates(base_candidate, request.candidate)
                if not diff and not request.unresolved_ambiguities:
                    raise InputValidationError("无变更且无歧义的空治理草稿不会被保存")

                draft = AgentPolicyDraft(
                    team_id=project.team_id,
                    project_id=project.id,
                    created_by=identity.user_id,
                    originating_thread_id=thread.id,
                    status=PolicyDraftStatus.ACTIVE,
                    base_context_id=bundle.context.context_id,
                    base_context_version=bundle.context.version,
                    base_intent_id=request.base_intent_id,
                    base_intent_version=request.base_intent_version,
                    base_policy_snapshot=base_snapshot,
                    base_policy_hash=canonical_hash(base_snapshot),
                    current_revision=1,
                )
                session.add(draft)
                session.flush()
                revision = self._build_revision(
                    session,
                    draft=draft,
                    revision_number=1,
                    author_id=identity.user_id,
                    source=PolicyDraftSource.AGENT,
                    source_run_id=run.id,
                    source_tool_call_id=tool_call_id,
                    source_request_hash=request_hash,
                    candidate=request.candidate,
                    change_summary=request.change_summary,
                    unresolved_ambiguities=request.unresolved_ambiguities,
                )
                session.add(revision)
                session.flush()
                session.add(
                    AuditLog(
                        team_id=project.team_id,
                        project_id=project.id,
                        actor_type="USER",
                        actor_id=identity.user_id,
                        action="agent.policy_draft.created",
                        target_type="AGENT_POLICY_DRAFT",
                        target_id=draft.id,
                        before_value=None,
                        after_value={
                            "revision": 1,
                            "readiness": revision.readiness.value,
                            "run_id": str(run.id),
                            "tool_call_id": str(tool_call_id),
                            "session_id": str(identity.token_id),
                        },
                    )
                )
                return self._view(session, draft, identity=identity)

        return run_with_serialization_retry(operation)

    def revise_from_agent(
        self,
        *,
        project_id: UUID,
        draft_id: UUID,
        identity: RequestIdentity,
        run_id: UUID,
        tool_call_id: UUID,
        request: PolicyDraftRevisionInput,
    ) -> PolicyDraftRevisionView:
        return self._revise(
            project_id=project_id,
            draft_id=draft_id,
            identity=identity,
            request=request,
            source=PolicyDraftSource.AGENT,
            source_run_id=run_id,
            source_tool_call_id=tool_call_id,
            idempotency_key=None,
        )

    def revise_from_web(
        self,
        *,
        project_id: UUID,
        draft_id: UUID,
        identity: RequestIdentity,
        idempotency_key: UUID,
        request: PolicyDraftRevisionInput,
    ) -> PolicyDraftRevisionView:
        self._require_web_identity(identity)
        return self._revise(
            project_id=project_id,
            draft_id=draft_id,
            identity=identity,
            request=request,
            source=PolicyDraftSource.WEB,
            source_run_id=None,
            source_tool_call_id=None,
            idempotency_key=idempotency_key,
        )

    def abandon(
        self,
        *,
        project_id: UUID,
        draft_id: UUID,
        identity: RequestIdentity,
        idempotency_key: UUID,
        request: PolicyDraftAbandonRequest,
    ) -> PolicyDraftSummary:
        self._require_web_identity(identity)
        request_hash = canonical_hash(
            {
                "project_id": str(project_id),
                "draft_id": str(draft_id),
                **request.model_dump(mode="json"),
            }
        )

        def operation() -> PolicyDraftSummary:
            with self._session_factory() as session, session.begin():
                existing = self._idempotency(
                    session,
                    identity=identity,
                    operation=POLICY_DRAFT_ABANDON_OPERATION,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
                if existing is not None:
                    return PolicyDraftSummary.model_validate(existing.response_snapshot)
                draft = self._require_draft(
                    session,
                    project_id=project_id,
                    draft_id=draft_id,
                    identity=identity,
                    for_update=True,
                )
                if draft.status is PolicyDraftStatus.ABANDONED:
                    raise ConflictError("治理草稿已经取消")
                if draft.current_revision != request.expected_revision:
                    raise ConflictError(
                        f"草稿 revision 已变化，当前为 {draft.current_revision}，请刷新后重试"
                    )
                now = datetime.now(UTC)
                draft.status = PolicyDraftStatus.ABANDONED
                draft.abandoned_at = now
                draft.abandoned_by = identity.user_id
                draft.abandon_reason = request.reason
                draft.updated_at = now
                current_bundle = self._projects.load_context_bundle(
                    session,
                    project_id=project_id,
                    user_id=identity.user_id,
                    team_id=identity.team_id,
                )
                result = self._summary(session, draft, current_bundle=current_bundle)
                self._save_idempotency(
                    session,
                    identity=identity,
                    operation=POLICY_DRAFT_ABANDON_OPERATION,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    response=result.model_dump(mode="json"),
                )
                session.add(
                    AuditLog(
                        team_id=draft.team_id,
                        project_id=draft.project_id,
                        actor_type="USER",
                        actor_id=identity.user_id,
                        action="agent.policy_draft.abandoned",
                        target_type="AGENT_POLICY_DRAFT",
                        target_id=draft.id,
                        before_value={"status": PolicyDraftStatus.ACTIVE.value},
                        after_value={
                            "status": PolicyDraftStatus.ABANDONED.value,
                            "reason": request.reason,
                            "session_id": str(identity.token_id),
                        },
                    )
                )
                return result

        return run_with_serialization_retry(operation)

    def validate_for_agent(
        self,
        *,
        project_id: UUID,
        draft_id: UUID,
        revision: int | None,
        identity: RequestIdentity,
    ) -> tuple[PolicyDraftSummary, PolicyDraftValidation]:
        self._require_web_identity(identity)
        with self._session_factory() as session:
            draft = self._require_draft(
                session,
                project_id=project_id,
                draft_id=draft_id,
                identity=identity,
            )
            row = self._require_revision(session, draft.id, revision or draft.current_revision)
            current_bundle = self._projects.load_context_bundle(
                session,
                project_id=project_id,
                user_id=identity.user_id,
                team_id=identity.team_id,
            )
            return (
                self._summary(session, draft, current_bundle=current_bundle),
                PolicyDraftValidation.model_validate(row.validation_report),
            )

    def impact_for_agent(
        self,
        *,
        project_id: UUID,
        draft_id: UUID,
        revision: int | None,
        identity: RequestIdentity,
    ) -> tuple[PolicyDraftSummary, PolicyDraftImpact, bool]:
        self._require_web_identity(identity)
        with self._session_factory() as session:
            draft = self._require_draft(
                session,
                project_id=project_id,
                draft_id=draft_id,
                identity=identity,
            )
            row = self._require_revision(session, draft.id, revision or draft.current_revision)
            candidate = PolicyDraftCandidate.model_validate(row.candidate_payload)
            validation = PolicyDraftValidation.model_validate(row.validation_report)
            diff = diff_policy_candidates(
                self._base_candidate(draft),
                candidate,
            )
            current = self._build_impact(
                session,
                draft=draft,
                candidate=candidate,
                validation=validation,
                diff=diff,
            )
            bundle = self._projects.load_context_bundle(
                session,
                project_id=project_id,
                user_id=identity.user_id,
                team_id=identity.team_id,
            )
            return (
                self._summary(session, draft, current_bundle=bundle),
                current,
                current.pending_state_hash != row.pending_state_hash,
            )

    def _revise(
        self,
        *,
        project_id: UUID,
        draft_id: UUID,
        identity: RequestIdentity,
        request: PolicyDraftRevisionInput,
        source: PolicyDraftSource,
        source_run_id: UUID | None,
        source_tool_call_id: UUID | None,
        idempotency_key: UUID | None,
    ) -> PolicyDraftRevisionView:
        self._require_web_identity(identity)
        request_hash = canonical_hash(
            {
                "project_id": str(project_id),
                "draft_id": str(draft_id),
                **request.model_dump(mode="json"),
            }
        )

        def operation() -> PolicyDraftRevisionView:
            with self._session_factory() as session, session.begin():
                if source_run_id is not None:
                    existing_revision = session.scalar(
                        select(AgentPolicyDraftRevision).where(
                            AgentPolicyDraftRevision.source_run_id == source_run_id
                        )
                    )
                    if existing_revision is not None:
                        if (
                            existing_revision.source_request_hash != request_hash
                            or existing_revision.draft_id != draft_id
                        ):
                            raise ConflictError("同一 Agent Run 已执行不同的草稿写操作")
                        draft = self._require_draft(
                            session,
                            project_id=project_id,
                            draft_id=draft_id,
                            identity=identity,
                        )
                        return self._revision_view(session, draft, existing_revision)
                elif idempotency_key is not None:
                    existing = self._idempotency(
                        session,
                        identity=identity,
                        operation=POLICY_DRAFT_REVISE_OPERATION,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                    )
                    if existing is not None:
                        return PolicyDraftRevisionView.model_validate(existing.response_snapshot)

                draft = self._require_draft(
                    session,
                    project_id=project_id,
                    draft_id=draft_id,
                    identity=identity,
                    for_update=True,
                )
                if draft.status is not PolicyDraftStatus.ACTIVE:
                    raise ConflictError("已取消的治理草稿不能继续编辑")
                if draft.current_revision != request.expected_revision:
                    raise ConflictError(
                        f"草稿 revision 已变化，当前为 {draft.current_revision}，请刷新后重试"
                    )
                current_bundle = self._projects.load_context_bundle(
                    session,
                    project_id=project_id,
                    user_id=identity.user_id,
                    team_id=identity.team_id,
                )
                if self._freshness(draft, current_bundle) is PolicyDraftFreshness.STALE:
                    raise ConflictError("草稿基准正式版本已变化，请基于当前版本创建新草稿")
                if source_run_id is not None:
                    self._require_agent_source(
                        session,
                        run_id=source_run_id,
                        tool_call_id=source_tool_call_id,
                        project_id=project_id,
                        identity=identity,
                    )

                previous = self._require_revision(
                    session,
                    draft.id,
                    draft.current_revision,
                )
                previous_state = {
                    "candidate": previous.candidate_payload,
                    "change_summary": previous.change_summary,
                    "unresolved_ambiguities": previous.unresolved_ambiguities,
                }
                current_state = {
                    "candidate": request.candidate.model_dump(mode="json"),
                    "change_summary": request.change_summary,
                    "unresolved_ambiguities": [
                        item.model_dump(mode="json") for item in request.unresolved_ambiguities
                    ],
                }
                if canonical_hash(previous_state) == canonical_hash(current_state):
                    raise InputValidationError("草稿 revision 没有任何实际变化")

                next_revision = draft.current_revision + 1
                row = self._build_revision(
                    session,
                    draft=draft,
                    revision_number=next_revision,
                    author_id=identity.user_id,
                    source=source,
                    source_run_id=source_run_id,
                    source_tool_call_id=source_tool_call_id,
                    source_request_hash=request_hash,
                    candidate=request.candidate,
                    change_summary=request.change_summary,
                    unresolved_ambiguities=request.unresolved_ambiguities,
                )
                session.add(row)
                session.flush()
                draft.current_revision = next_revision
                draft.updated_at = datetime.now(UTC)
                result = self._revision_view(session, draft, row)
                if idempotency_key is not None:
                    self._save_idempotency(
                        session,
                        identity=identity,
                        operation=POLICY_DRAFT_REVISE_OPERATION,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                        response=result.model_dump(mode="json"),
                    )
                session.add(
                    AuditLog(
                        team_id=draft.team_id,
                        project_id=draft.project_id,
                        actor_type="USER",
                        actor_id=identity.user_id,
                        action="agent.policy_draft.revision_created",
                        target_type="AGENT_POLICY_DRAFT",
                        target_id=draft.id,
                        before_value={"revision": request.expected_revision},
                        after_value={
                            "revision": next_revision,
                            "readiness": row.readiness.value,
                            "source": source.value,
                            "run_id": str(source_run_id) if source_run_id else None,
                            "session_id": str(identity.token_id),
                        },
                    )
                )
                return result

        return run_with_serialization_retry(operation)

    def _build_revision(
        self,
        session: Session,
        *,
        draft: AgentPolicyDraft,
        revision_number: int,
        author_id: UUID,
        source: PolicyDraftSource,
        source_run_id: UUID | None,
        source_tool_call_id: UUID | None,
        source_request_hash: str,
        candidate: PolicyDraftCandidate,
        change_summary: str,
        unresolved_ambiguities: list[PolicyDraftAmbiguity],
    ) -> AgentPolicyDraftRevision:
        validation = validate_policy_candidate(candidate, unresolved_ambiguities)
        diff = diff_policy_candidates(self._base_candidate(draft), candidate)
        narrative = render_policy_draft_narrative(candidate, diff, validation)
        impact = self._build_impact(
            session,
            draft=draft,
            candidate=candidate,
            validation=validation,
            diff=diff,
        )
        return AgentPolicyDraftRevision(
            draft_id=draft.id,
            revision=revision_number,
            author_id=author_id,
            source=source,
            source_run_id=source_run_id,
            source_tool_call_id=source_tool_call_id,
            candidate_payload=candidate.model_dump(mode="json"),
            candidate_hash=canonical_hash(candidate.model_dump(mode="json")),
            source_request_hash=source_request_hash,
            change_summary=change_summary,
            unresolved_ambiguities=[
                item.model_dump(mode="json") for item in unresolved_ambiguities
            ],
            readiness=validation.readiness,
            validation_report=validation.model_dump(mode="json"),
            diff_snapshot=[item.model_dump(mode="json") for item in diff],
            narrative_snapshot=narrative.model_dump(mode="json"),
            impact_snapshot=impact.model_dump(mode="json"),
            pending_state_hash=impact.pending_state_hash,
        )

    def _build_impact(
        self,
        session: Session,
        *,
        draft: AgentPolicyDraft,
        candidate: PolicyDraftCandidate,
        validation: PolicyDraftValidation,
        diff: list[PolicyDraftDiffItem],
    ) -> PolicyDraftImpact:
        plans = list(
            session.scalars(
                select(PlanCheck)
                .where(
                    PlanCheck.project_id == draft.project_id,
                    PlanCheck.approval_status == ApprovalStatus.PENDING,
                )
                .order_by(PlanCheck.created_at.desc(), PlanCheck.id.desc())
                .limit(IMPACT_ITEM_LIMIT + 1)
            ).all()
        )
        submissions = list(
            session.scalars(
                select(ExperimentSubmission)
                .where(
                    ExperimentSubmission.project_id == draft.project_id,
                    ExperimentSubmission.status.in_(ACTIVE_SUBMISSION_STATUSES),
                )
                .order_by(
                    ExperimentSubmission.created_at.desc(),
                    ExperimentSubmission.id.desc(),
                )
                .limit(IMPACT_ITEM_LIMIT + 1)
            ).all()
        )
        pending_state = {
            "plans": [
                {
                    "id": str(item.id),
                    "approval_status": item.approval_status.value,
                    "check_result": item.check_result.value,
                    "updated": item.approved_at.isoformat() if item.approved_at else None,
                }
                for item in plans
            ],
            "submissions": [
                {
                    "id": str(item.id),
                    "status": item.status.value,
                    "updated_at": item.updated_at.isoformat(),
                }
                for item in submissions
            ],
        }
        pending_hash = canonical_hash(pending_state)
        warnings: list[str] = []
        if validation.readiness is PolicyDraftReadiness.INVALID:
            return PolicyDraftImpact(
                status="NOT_EVALUATED",
                generated_at=datetime.now(UTC),
                pending_state_hash=pending_hash,
                attention_level=max_attention(diff),
                future_policy_effects=self._future_effects(diff),
                plan_simulations_truncated=len(plans) > IMPACT_ITEM_LIMIT,
                submission_impacts=self._submission_impacts(
                    session, submissions[:IMPACT_ITEM_LIMIT], warnings
                ),
                submission_impacts_truncated=len(submissions) > IMPACT_ITEM_LIMIT,
                warnings=["候选 Bundle 未通过确定性校验，未执行 Plan 模拟。", *warnings],
            )

        simulations: list[PolicyDraftPlanSimulation] = []
        for plan in plans[:IMPACT_ITEM_LIMIT]:
            try:
                evaluation = evaluate_plan(
                    PlanEvaluationInput(
                        baseline_config=candidate.context.active_config,
                        candidate=ConfigurationDocument.model_validate(plan.configuration_document),
                        constraints=self._simulation_constraints(
                            candidate,
                            actor_id=draft.created_by,
                        ),
                        allowed_variable_paths=set(candidate.intent.allowed_variables),
                        local_attestation=LocalAttestation.model_validate(plan.local_attestation),
                        experiment_mode=plan.experiment_mode,
                        git_commit=plan.git_commit,
                        run_command=plan.command,
                    )
                )
                simulations.append(
                    PolicyDraftPlanSimulation(
                        plan_check_id=plan.id,
                        context_version=plan.context_version,
                        intent_version=plan.intent_version,
                        original_check_result=plan.check_result,
                        original_approval_status=plan.approval_status,
                        simulated_check_result=evaluation.check_result,
                        simulated_approval_status=evaluation.approval_status,
                        simulated_risk_codes=sorted({item.code for item in evaluation.risks}),
                        changed=(
                            evaluation.check_result != plan.check_result
                            or evaluation.approval_status != plan.approval_status
                        ),
                    )
                )
            except Exception as exc:
                simulations.append(
                    PolicyDraftPlanSimulation(
                        plan_check_id=plan.id,
                        context_version=plan.context_version,
                        intent_version=plan.intent_version,
                        original_check_result=plan.check_result,
                        original_approval_status=plan.approval_status,
                        status="FAILED",
                        error=f"无法模拟该 Plan 的冻结输入：{str(exc)[:1000]}",
                    )
                )
                warnings.append(f"Plan {plan.id} 的候选规则模拟失败。")

        submission_impacts = self._submission_impacts(
            session,
            submissions[:IMPACT_ITEM_LIMIT],
            warnings,
        )
        return PolicyDraftImpact(
            status="PARTIAL" if warnings else "COMPLETE",
            generated_at=datetime.now(UTC),
            pending_state_hash=pending_hash,
            attention_level=max_attention(diff),
            future_policy_effects=self._future_effects(diff),
            plan_simulations=simulations,
            plan_simulations_truncated=len(plans) > IMPACT_ITEM_LIMIT,
            submission_impacts=submission_impacts,
            submission_impacts_truncated=len(submissions) > IMPACT_ITEM_LIMIT,
            warnings=warnings,
        )

    @staticmethod
    def _submission_impacts(
        session: Session,
        submissions: list[ExperimentSubmission],
        warnings: list[str],
    ) -> list[PolicyDraftSubmissionImpact]:
        impacts: list[PolicyDraftSubmissionImpact] = []
        for submission in submissions:
            manifest = session.get(RunManifest, submission.run_manifest_id)
            if manifest is None:
                warnings.append(f"Submission {submission.id} 缺少 Run Manifest。")
                continue
            impacts.append(
                PolicyDraftSubmissionImpact(
                    submission_id=submission.id,
                    status=submission.status,
                    context_version=manifest.context_version,
                    intent_version=manifest.intent_version,
                )
            )
        return impacts

    @staticmethod
    def _simulation_constraints(
        candidate: PolicyDraftCandidate,
        *,
        actor_id: UUID,
    ) -> list[ParameterConstraint]:
        now = datetime.now(UTC)
        return [
            ParameterConstraint(
                parameter_path=item.parameter_path,
                protection_level=item.protection_level,
                expected_value=item.expected_value,
                allowed_values=item.allowed_values,
                minimum=item.minimum,
                maximum=item.maximum,
                reason=item.reason,
                source_type=ConstraintSource.EXPLICIT,
                verification_status=VerificationStatus.CONFIRMED,
                original_message=item.original_message,
                confirmed_by=actor_id,
                confirmed_at=now,
            )
            for item in candidate.constraints
        ]

    @staticmethod
    def _future_effects(diff: list[PolicyDraftDiffItem]) -> list[str]:
        effects: list[str] = []
        for item in diff:
            if item.attention_level == "HIGH":
                effects.append(f"{item.field_path}：{item.impact}")
            elif item.field_path.startswith("constraints."):
                effects.append(f"{item.field_path}：后续 Plan 的参数判断可能变化。")
        return list(dict.fromkeys(effects))[:50]

    def _view(
        self,
        session: Session,
        draft: AgentPolicyDraft,
        *,
        identity: RequestIdentity,
    ) -> PolicyDraftView:
        bundle = self._projects.load_context_bundle(
            session,
            project_id=draft.project_id,
            user_id=identity.user_id,
            team_id=identity.team_id,
        )
        current = self._require_revision(session, draft.id, draft.current_revision)
        rows = list(
            session.scalars(
                select(AgentPolicyDraftRevision)
                .where(AgentPolicyDraftRevision.draft_id == draft.id)
                .order_by(AgentPolicyDraftRevision.revision.desc())
            ).all()
        )
        return PolicyDraftView(
            summary=self._summary(session, draft, current_bundle=bundle),
            current=self._revision_view(session, draft, current),
            revisions=[
                PolicyDraftRevisionSummary(
                    revision_id=item.id,
                    revision=item.revision,
                    author_id=item.author_id,
                    source=item.source,
                    readiness=item.readiness,
                    candidate_hash=item.candidate_hash,
                    change_summary=item.change_summary,
                    ambiguity_count=len(item.unresolved_ambiguities),
                    created_at=item.created_at,
                )
                for item in rows
            ],
        )

    def _revision_view(
        self,
        session: Session,
        draft: AgentPolicyDraft,
        row: AgentPolicyDraftRevision,
    ) -> PolicyDraftRevisionView:
        candidate = PolicyDraftCandidate.model_validate(row.candidate_payload)
        validation = PolicyDraftValidation.model_validate(row.validation_report)
        current_impact = self._build_impact(
            session,
            draft=draft,
            candidate=candidate,
            validation=validation,
            diff=diff_policy_candidates(self._base_candidate(draft), candidate),
        )
        return PolicyDraftRevisionView(
            revision_id=row.id,
            draft_id=draft.id,
            revision=row.revision,
            author_id=row.author_id,
            source=row.source,
            source_run_id=row.source_run_id,
            candidate=candidate,
            candidate_hash=row.candidate_hash,
            change_summary=row.change_summary,
            unresolved_ambiguities=row.unresolved_ambiguities,
            validation=validation,
            diff=row.diff_snapshot,
            narrative=row.narrative_snapshot,
            stored_impact=row.impact_snapshot,
            current_impact=current_impact,
            impact_changed_since_revision=(
                current_impact.pending_state_hash != row.pending_state_hash
            ),
            created_at=row.created_at,
        )

    def _summary(
        self,
        session: Session,
        draft: AgentPolicyDraft,
        *,
        current_bundle: ProjectContextBundle,
    ) -> PolicyDraftSummary:
        row = self._require_revision(session, draft.id, draft.current_revision)
        return PolicyDraftSummary(
            draft_id=draft.id,
            project_id=draft.project_id,
            created_by=draft.created_by,
            status=draft.status,
            freshness=self._freshness(draft, current_bundle),
            base_context_id=draft.base_context_id,
            base_context_version=draft.base_context_version,
            base_intent_id=draft.base_intent_id,
            base_intent_version=draft.base_intent_version,
            current_revision=draft.current_revision,
            readiness=row.readiness,
            ambiguity_count=len(row.unresolved_ambiguities),
            change_summary=row.change_summary,
            created_at=draft.created_at,
            updated_at=draft.updated_at,
            abandoned_at=draft.abandoned_at,
        )

    @staticmethod
    def _candidate_from_bundle(bundle: ProjectContextBundle) -> PolicyDraftCandidate:
        context = bundle.context_payload
        intent = bundle.intent_payload
        intent_ref = bundle.active_intent
        if intent is None or intent_ref is None:
            raise ConflictError("项目没有完整的 Active Intent")
        return PolicyDraftCandidate(
            context=InitialContextInput(
                goal=context.goal,
                non_goals=context.non_goals,
                mainline_model=context.mainline_model,
                baseline=context.baseline,
                dataset=context.dataset,
                protocol=context.protocol,
                primary_metric=context.primary_metric,
                default_seeds=context.default_seeds,
                active_branch=context.active_branch,
                active_config=context.active_config,
                deprecated_items=context.deprecated_items,
                key_decisions=context.key_decisions,
                change_reason=bundle.context.change_reason,
            ),
            intent=InitialIntentInput(
                name=intent.name,
                objective=intent.objective,
                hypothesis=intent.hypothesis,
                allowed_variables=intent.allowed_variables,
                controlled_variables=intent.controlled_variables,
                expected_outputs=intent.expected_outputs,
                acceptance_criteria=intent.acceptance_criteria,
                original_message=intent.original_message,
            ),
            constraints=[
                InitialConstraintInput(
                    parameter_path=item.parameter_path,
                    protection_level=item.protection_level,
                    expected_value=item.expected_value,
                    allowed_values=item.allowed_values,
                    minimum=item.minimum,
                    maximum=item.maximum,
                    reason=item.reason,
                    original_message=item.original_message,
                )
                for item in bundle.constraints
            ],
        )

    @staticmethod
    def _base_snapshot(
        bundle: ProjectContextBundle,
        candidate: PolicyDraftCandidate,
    ) -> dict[str, Any]:
        intent = bundle.active_intent
        if intent is None:
            raise ConflictError("项目没有 Active Intent")
        return {
            "context": bundle.context.model_dump(mode="json"),
            "intent": intent.model_dump(mode="json"),
            "candidate": candidate.model_dump(mode="json"),
        }

    @staticmethod
    def _base_candidate(draft: AgentPolicyDraft) -> PolicyDraftCandidate:
        raw = draft.base_policy_snapshot.get("candidate")
        if not isinstance(raw, dict):
            raise ConflictError("治理草稿缺少完整基准 Policy Bundle 快照")
        return PolicyDraftCandidate.model_validate(raw)

    def _freshness(
        self,
        draft: AgentPolicyDraft,
        bundle: ProjectContextBundle,
    ) -> PolicyDraftFreshness:
        intent = bundle.active_intent
        if (
            intent is None
            or bundle.context.context_id != draft.base_context_id
            or bundle.context.version != draft.base_context_version
            or intent.intent_id != draft.base_intent_id
            or intent.version != draft.base_intent_version
        ):
            return PolicyDraftFreshness.STALE
        snapshot = self._base_snapshot(bundle, self._candidate_from_bundle(bundle))
        return (
            PolicyDraftFreshness.CURRENT
            if canonical_hash(snapshot) == draft.base_policy_hash
            else PolicyDraftFreshness.STALE
        )

    @staticmethod
    def _require_base_reference(
        bundle: ProjectContextBundle,
        request: PolicyDraftCreateInput,
    ) -> None:
        intent = bundle.active_intent
        if intent is None:
            raise ConflictError("项目没有 Active Intent")
        if (
            bundle.context.context_id != request.base_context_id
            or bundle.context.version != request.base_context_version
            or intent.intent_id != request.base_intent_id
            or intent.version != request.base_intent_version
        ):
            raise ConflictError("正式 Policy Bundle 版本已变化，请重新读取后创建草稿")

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

    def _require_draft(
        self,
        session: Session,
        *,
        project_id: UUID,
        draft_id: UUID,
        identity: RequestIdentity,
        for_update: bool = False,
    ) -> AgentPolicyDraft:
        _, role = self._require_project(session, project_id, identity)
        statement = select(AgentPolicyDraft).where(
            AgentPolicyDraft.id == draft_id,
            AgentPolicyDraft.project_id == project_id,
        )
        if for_update:
            statement = statement.with_for_update()
        draft = session.scalar(statement)
        if draft is None or (role is TeamRole.RESEARCHER and draft.created_by != identity.user_id):
            raise ResourceNotFoundError("项目中不存在当前用户可访问的治理草稿")
        return draft

    @staticmethod
    def _require_revision(
        session: Session,
        draft_id: UUID,
        revision: int,
    ) -> AgentPolicyDraftRevision:
        row = session.scalar(
            select(AgentPolicyDraftRevision).where(
                AgentPolicyDraftRevision.draft_id == draft_id,
                AgentPolicyDraftRevision.revision == revision,
            )
        )
        if row is None:
            raise ResourceNotFoundError("治理草稿 revision 不存在")
        return row

    @staticmethod
    def _require_agent_source(
        session: Session,
        *,
        run_id: UUID,
        tool_call_id: UUID | None,
        project_id: UUID,
        identity: RequestIdentity,
    ) -> tuple[AgentRun, AgentThread]:
        if tool_call_id is None:
            raise ConflictError("Agent 草稿写入缺少 ToolCall 来源")
        run = session.get(AgentRun, run_id)
        call = session.get(AgentToolCall, tool_call_id)
        if (
            run is None
            or call is None
            or call.run_id != run.id
            or run.project_id != project_id
            or run.created_by != identity.user_id
        ):
            raise AuthorizationError("Agent Run 或 ToolCall 来源与当前身份不一致")
        thread = session.get(AgentThread, run.thread_id)
        if thread is None or thread.project_id != project_id:
            raise ConflictError("Agent Run 缺少有效 Thread")
        return run, thread

    @staticmethod
    def _require_web_identity(identity: RequestIdentity) -> None:
        if identity.authentication_method != "WEB_SESSION":
            raise AuthorizationError("治理草稿只允许通过服务端 Web Session 操作")
        if "project:read" not in identity.scopes:
            raise AuthorizationError("当前身份缺少 project:read scope")

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
            raise ConflictError("相同 Idempotency-Key 已用于不同的治理草稿请求")
        return row

    @staticmethod
    def _save_idempotency(
        session: Session,
        *,
        identity: RequestIdentity,
        operation: str,
        idempotency_key: UUID,
        request_hash: str,
        response: dict[str, Any],
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
