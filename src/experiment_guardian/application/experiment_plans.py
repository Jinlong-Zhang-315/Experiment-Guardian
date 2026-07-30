"""版本化自然语言实验计划的提交、读取、审核落库和人类决定。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    InputValidationError,
    ResourceNotFoundError,
    ServiceUnavailableError,
)
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.transactions import run_with_serialization_retry
from experiment_guardian.core.config import Settings
from experiment_guardian.domain.enums import (
    AgentMessageRole,
    AgentRunAuthMethod,
    AgentRunKind,
    AgentRunStatus,
    AgentThreadOrigin,
    AgentThreadStatus,
    ExperimentPlanDecisionType,
    ExperimentPlanReviewRecommendation,
    ExperimentPlanRevisionAuthor,
    ExperimentPlanStatus,
    IdempotencyOperationStatus,
    TeamRole,
)
from experiment_guardian.domain.experiment_plan import (
    ExperimentPlanDecisionRequest,
    ExperimentPlanEvidence,
    ExperimentPlanHardCheck,
    ExperimentPlanPage,
    ExperimentPlanReceipt,
    ExperimentPlanReviewPayload,
    ExperimentPlanReviewView,
    ExperimentPlanRevisionRequest,
    ExperimentPlanRevisionView,
    ExperimentPlanSubmitRequest,
    ExperimentPlanSummary,
    ExperimentPlanView,
    canonical_hash,
    evaluate_plan_evidence,
    formal_policy_snapshot,
)
from experiment_guardian.infrastructure.models import (
    AgentMessage,
    AgentRun,
    AgentThread,
    AuditLog,
    ExperimentPlan,
    ExperimentPlanDecision,
    ExperimentPlanReview,
    ExperimentPlanRevision,
    IdempotencyRecord,
    TeamMember,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyAgentRepository,
    SqlAlchemyProjectRepository,
)

PLAN_REVIEW_PROMPT_VERSION = "r17b-plan-review-v1"
PLAN_REVIEW_TOOL_CATALOG_VERSION = "r17b-plan-review-v2"
PLAN_SUBMIT_OPERATION = "experiment_plan.submit"
PLAN_REVISE_OPERATION = "experiment_plan.revise"
PLAN_RETRY_OPERATION = "experiment_plan.retry_review"
PLAN_DECIDE_OPERATION = "experiment_plan.decide"
ACTIVE_RUN_STATUSES = {
    AgentRunStatus.PENDING,
    AgentRunStatus.RUNNING,
    AgentRunStatus.RETRYABLE_FAILURE,
}


class _FormalPolicyDrift(Exception):
    """触发事务回滚后，在独立事务中持久化 STALE 状态。"""


class ExperimentPlanService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        projects: SqlAlchemyProjectRepository,
        agent_repository: SqlAlchemyAgentRepository,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._projects = projects
        self._agent_repository = agent_repository
        self._settings = settings

    def submit_external(
        self,
        *,
        task_id: UUID,
        identity: RequestIdentity,
        idempotency_key: UUID,
        request: ExperimentPlanSubmitRequest,
    ) -> ExperimentPlanReceipt:
        self._require_enabled()
        self._require_external_identity(identity)
        request_hash = canonical_hash(request.model_dump(mode="json"))

        def operation() -> ExperimentPlanReceipt:
            with self._session_factory() as session, session.begin():
                replay = self._find_idempotency(
                    session, identity.user_id, PLAN_SUBMIT_OPERATION, idempotency_key
                )
                if replay is not None:
                    return self._replay_receipt(replay, request_hash)
                thread = self._require_task(session, task_id, identity, for_update=True)
                if (
                    session.scalar(
                        select(ExperimentPlan.id).where(
                            ExperimentPlan.source_thread_id == thread.id
                        )
                    )
                    is not None
                ):
                    raise ConflictError("该外部任务已经存在实验计划，请提交新 revision")
                self._require_idle_thread(session, thread.id)
                bundle = self._projects.load_context_bundle(
                    session,
                    project_id=thread.project_id,
                    user_id=identity.user_id,
                    team_id=identity.team_id,
                )
                snapshot, policy_hash = formal_policy_snapshot(bundle)
                plan = ExperimentPlan(
                    team_id=thread.team_id,
                    project_id=thread.project_id,
                    created_by=identity.user_id,
                    source_thread_id=thread.id,
                    status=ExperimentPlanStatus.REVIEW_QUEUED,
                    current_revision=1,
                )
                session.add(plan)
                session.flush()
                revision = self._new_revision(
                    plan=plan,
                    revision=1,
                    author_type=ExperimentPlanRevisionAuthor.EXTERNAL_AGENT,
                    author_id=identity.user_id,
                    parent=None,
                    source_run_id=None,
                    automatic_round=0,
                    request=request,
                    policy_snapshot=snapshot,
                    policy_hash=policy_hash,
                    context_id=bundle.context.context_id,
                    context_version=bundle.context.version,
                    intent_id=bundle.active_intent.intent_id if bundle.active_intent else None,
                    intent_version=(bundle.active_intent.version if bundle.active_intent else None),
                )
                session.add(revision)
                session.flush()
                hard_check = evaluate_plan_evidence(request.evidence, bundle)
                run = self._queue_review(
                    session,
                    thread=thread,
                    plan=plan,
                    revision=revision,
                    hard_check=hard_check,
                    auth=self._auth_binding(identity),
                    created_by=identity.user_id,
                )
                receipt = self._receipt(plan, revision, run)
                self._save_idempotency(
                    session,
                    actor_id=identity.user_id,
                    operation=PLAN_SUBMIT_OPERATION,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    response=receipt.model_dump(mode="json"),
                )
                session.add(
                    AuditLog(
                        team_id=plan.team_id,
                        project_id=plan.project_id,
                        actor_type="EXTERNAL_AGENT",
                        actor_id=identity.user_id,
                        action="experiment_plan.submitted",
                        target_type="EXPERIMENT_PLAN",
                        target_id=plan.id,
                        before_value=None,
                        after_value={
                            "revision": revision.revision,
                            "revision_id": str(revision.id),
                            "policy_hash": revision.policy_hash,
                            **self._auth_audit(identity),
                        },
                    )
                )
                return receipt

        return run_with_serialization_retry(operation)

    def revise_external(
        self,
        *,
        plan_id: UUID,
        identity: RequestIdentity,
        idempotency_key: UUID,
        request: ExperimentPlanRevisionRequest,
    ) -> ExperimentPlanReceipt:
        self._require_enabled()
        self._require_external_identity(identity)
        return self._revise(
            plan_id=plan_id,
            identity=identity,
            idempotency_key=idempotency_key,
            request=request,
            author_type=ExperimentPlanRevisionAuthor.EXTERNAL_AGENT,
            operation_name=PLAN_REVISE_OPERATION,
        )

    def revise_web(
        self,
        *,
        project_id: UUID,
        plan_id: UUID,
        identity: RequestIdentity,
        idempotency_key: UUID,
        request: ExperimentPlanRevisionRequest,
    ) -> ExperimentPlanReceipt:
        self._require_enabled()
        self._require_web(identity)
        if "experiment-plan:write" not in identity.scopes:
            raise AuthorizationError("当前身份缺少 experiment-plan:write scope")
        return self._revise(
            plan_id=plan_id,
            identity=identity,
            idempotency_key=idempotency_key,
            request=request,
            author_type=ExperimentPlanRevisionAuthor.WEB_USER,
            operation_name=PLAN_REVISE_OPERATION,
            expected_project_id=project_id,
        )

    def _revise(
        self,
        *,
        plan_id: UUID,
        identity: RequestIdentity,
        idempotency_key: UUID,
        request: ExperimentPlanRevisionRequest,
        author_type: ExperimentPlanRevisionAuthor,
        operation_name: str,
        expected_project_id: UUID | None = None,
    ) -> ExperimentPlanReceipt:
        request_hash = canonical_hash({"plan_id": str(plan_id), **request.model_dump(mode="json")})

        def operation() -> ExperimentPlanReceipt:
            with self._session_factory() as session, session.begin():
                replay = self._find_idempotency(
                    session, identity.user_id, operation_name, idempotency_key
                )
                if replay is not None:
                    return self._replay_receipt(replay, request_hash)
                plan, role = self._require_plan(
                    session,
                    plan_id=plan_id,
                    identity=identity,
                    project_id=expected_project_id or identity.project_id,
                    for_update=True,
                )
                if role is TeamRole.RESEARCHER and plan.created_by != identity.user_id:
                    raise AuthorizationError("Researcher 只能修改自己创建的实验计划")
                if plan.status in {
                    ExperimentPlanStatus.APPROVED,
                    ExperimentPlanStatus.CONDITIONALLY_APPROVED,
                    ExperimentPlanStatus.REJECTED,
                }:
                    raise ConflictError("终态计划不能继续修改，请创建新的外部任务")
                if plan.current_revision != request.expected_revision:
                    raise ConflictError("计划 revision 已变化，请刷新后重试")
                self._require_idle_thread(session, plan.source_thread_id)
                parent = self._current_revision(session, plan)
                bundle = self._projects.load_context_bundle(
                    session,
                    project_id=plan.project_id,
                    user_id=identity.user_id,
                    team_id=identity.team_id,
                )
                snapshot, policy_hash = formal_policy_snapshot(bundle)
                revision = self._new_revision(
                    plan=plan,
                    revision=plan.current_revision + 1,
                    author_type=author_type,
                    author_id=identity.user_id,
                    parent=parent,
                    source_run_id=None,
                    automatic_round=0,
                    request=request,
                    policy_snapshot=snapshot,
                    policy_hash=policy_hash,
                    context_id=bundle.context.context_id,
                    context_version=bundle.context.version,
                    intent_id=bundle.active_intent.intent_id if bundle.active_intent else None,
                    intent_version=(bundle.active_intent.version if bundle.active_intent else None),
                )
                session.add(revision)
                session.flush()
                plan.current_revision = revision.revision
                plan.status = ExperimentPlanStatus.REVIEW_QUEUED
                thread = session.get(AgentThread, plan.source_thread_id, with_for_update=True)
                if thread is None or thread.status is AgentThreadStatus.ARCHIVED:
                    raise ConflictError("归档或缺失的外部任务不能审核计划")
                hard_check = evaluate_plan_evidence(request.evidence, bundle)
                run = self._queue_review(
                    session,
                    thread=thread,
                    plan=plan,
                    revision=revision,
                    hard_check=hard_check,
                    auth=self._auth_binding(identity),
                    created_by=identity.user_id,
                )
                receipt = self._receipt(plan, revision, run)
                self._save_idempotency(
                    session,
                    actor_id=identity.user_id,
                    operation=operation_name,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    response=receipt.model_dump(mode="json"),
                )
                session.add(
                    AuditLog(
                        team_id=plan.team_id,
                        project_id=plan.project_id,
                        actor_type=author_type.value,
                        actor_id=identity.user_id,
                        action="experiment_plan.revised",
                        target_type="EXPERIMENT_PLAN_REVISION",
                        target_id=revision.id,
                        before_value={"revision": parent.revision},
                        after_value={
                            "revision": revision.revision,
                            "policy_hash": revision.policy_hash,
                        },
                    )
                )
                return receipt

        return run_with_serialization_retry(operation)

    def get_external(self, *, plan_id: UUID, identity: RequestIdentity) -> ExperimentPlanView:
        self._require_external_identity(identity, require_write=False)
        project_id = identity.project_id
        if project_id is None:
            raise AuthorizationError("MCP 身份未绑定项目")
        return self.get(project_id=project_id, plan_id=plan_id, identity=identity)

    def list_plans(
        self,
        *,
        project_id: UUID,
        identity: RequestIdentity,
        limit: int,
    ) -> ExperimentPlanPage:
        with self._session_factory() as session:
            _, role = self._require_project(session, project_id, identity)
            statement = select(ExperimentPlan).where(ExperimentPlan.project_id == project_id)
            if role is TeamRole.RESEARCHER:
                statement = statement.where(ExperimentPlan.created_by == identity.user_id)
            rows = list(
                session.scalars(
                    statement.order_by(
                        ExperimentPlan.updated_at.desc(), ExperimentPlan.id.desc()
                    ).limit(limit)
                ).all()
            )
            return ExperimentPlanPage(
                items=[self._summary(session, item, identity) for item in rows]
            )

    def get(
        self,
        *,
        project_id: UUID,
        plan_id: UUID,
        identity: RequestIdentity,
    ) -> ExperimentPlanView:
        with self._session_factory() as session:
            plan, role = self._require_plan(
                session,
                plan_id=plan_id,
                identity=identity,
                project_id=project_id,
            )
            if role is TeamRole.RESEARCHER and plan.created_by != identity.user_id:
                raise ResourceNotFoundError("项目中不存在该实验计划")
            revisions = list(
                session.scalars(
                    select(ExperimentPlanRevision)
                    .where(ExperimentPlanRevision.plan_id == plan.id)
                    .order_by(ExperimentPlanRevision.revision)
                ).all()
            )
            current = revisions[-1]
            review = session.scalar(
                select(ExperimentPlanReview).where(ExperimentPlanReview.revision_id == current.id)
            )
            decision = session.scalar(
                select(ExperimentPlanDecision).where(
                    ExperimentPlanDecision.revision_id == current.id
                )
            )
            return ExperimentPlanView(
                summary=self._summary(session, plan, identity),
                current=self._revision_view(current),
                review=self._review_view(review) if review is not None else None,
                decision=self._decision_view(decision) if decision is not None else None,
                revisions=[self._revision_view(item) for item in revisions],
                allowed_actions=self._allowed_actions(
                    plan=plan,
                    role=role,
                    identity=identity,
                    review=review,
                    current_policy=self._current_policy_hash(session, plan, identity),
                    current_revision=current,
                ),
            )

    def get_revision(
        self,
        *,
        project_id: UUID,
        plan_id: UUID,
        revision_number: int,
        identity: RequestIdentity,
    ) -> ExperimentPlanRevisionView:
        with self._session_factory() as session:
            plan, role = self._require_plan(
                session,
                plan_id=plan_id,
                identity=identity,
                project_id=project_id,
            )
            if role is TeamRole.RESEARCHER and plan.created_by != identity.user_id:
                raise ResourceNotFoundError("项目中不存在该实验计划")
            row = session.scalar(
                select(ExperimentPlanRevision).where(
                    ExperimentPlanRevision.plan_id == plan.id,
                    ExperimentPlanRevision.revision == revision_number,
                )
            )
            if row is None:
                raise ResourceNotFoundError("计划 revision 不存在")
            return self._revision_view(row)

    def retry_review(
        self,
        *,
        project_id: UUID,
        plan_id: UUID,
        identity: RequestIdentity,
        idempotency_key: UUID,
    ) -> ExperimentPlanReceipt:
        self._require_enabled()
        self._require_web(identity)
        request_hash = canonical_hash({"plan_id": str(plan_id), "action": "retry"})

        def operation() -> ExperimentPlanReceipt:
            with self._session_factory() as session, session.begin():
                replay = self._find_idempotency(
                    session, identity.user_id, PLAN_RETRY_OPERATION, idempotency_key
                )
                if replay is not None:
                    return self._replay_receipt(replay, request_hash)
                plan, role = self._require_plan(
                    session,
                    plan_id=plan_id,
                    project_id=project_id,
                    identity=identity,
                    for_update=True,
                )
                if role is TeamRole.RESEARCHER and plan.created_by != identity.user_id:
                    raise AuthorizationError("Researcher 只能重试自己的实验计划")
                if plan.status is not ExperimentPlanStatus.REVIEW_FAILED:
                    raise ConflictError("只有审核失败的计划可以重试")
                self._require_idle_thread(session, plan.source_thread_id)
                revision = self._current_revision(session, plan)
                thread = session.get(AgentThread, plan.source_thread_id, with_for_update=True)
                if thread is None:
                    raise ResourceNotFoundError("计划来源任务不存在")
                bundle = self._projects.load_context_bundle(
                    session,
                    project_id=plan.project_id,
                    user_id=identity.user_id,
                    team_id=identity.team_id,
                )
                _, current_hash = formal_policy_snapshot(bundle)
                if current_hash != revision.policy_hash:
                    raise _FormalPolicyDrift
                hard_check = evaluate_plan_evidence(
                    ExperimentPlanEvidence.model_validate(revision.evidence), bundle
                )
                plan.status = ExperimentPlanStatus.REVIEW_QUEUED
                run = self._queue_review(
                    session,
                    thread=thread,
                    plan=plan,
                    revision=revision,
                    hard_check=hard_check,
                    auth=self._auth_binding(identity),
                    created_by=identity.user_id,
                )
                receipt = self._receipt(plan, revision, run)
                self._save_idempotency(
                    session,
                    actor_id=identity.user_id,
                    operation=PLAN_RETRY_OPERATION,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    response=receipt.model_dump(mode="json"),
                )
                return receipt

        try:
            return run_with_serialization_retry(operation)
        except _FormalPolicyDrift:
            self._persist_stale_status(
                project_id=project_id,
                plan_id=plan_id,
                identity=identity,
            )
            raise ConflictError("正式策略已变化，请提交新 revision 重新审核") from None

    def decide(
        self,
        *,
        project_id: UUID,
        plan_id: UUID,
        identity: RequestIdentity,
        idempotency_key: UUID,
        request: ExperimentPlanDecisionRequest,
    ) -> ExperimentPlanView:
        self._require_web(identity)
        if "experiment-plan:approve" not in identity.scopes:
            raise AuthorizationError("当前身份缺少 experiment-plan:approve scope")
        if not identity.recent_authentication:
            raise AuthenticationError("实验计划决定需要近期认证")
        request_hash = canonical_hash({"plan_id": str(plan_id), **request.model_dump(mode="json")})

        def operation() -> ExperimentPlanView:
            with self._session_factory() as session, session.begin():
                replay = self._find_idempotency(
                    session, identity.user_id, PLAN_DECIDE_OPERATION, idempotency_key
                )
                if replay is not None:
                    if replay.request_hash != request_hash:
                        raise ConflictError("相同 Idempotency-Key 已用于不同的计划决定")
                    if replay.response_snapshot is None:
                        raise ServiceUnavailableError("计划决定幂等结果不完整")
                    replay_plan_id = UUID(str(replay.response_snapshot["plan_id"]))
                    if replay_plan_id != plan_id:
                        raise ConflictError("幂等结果与当前计划不一致")
                    # 事务完成后统一走公开读取构造动态视图。
                    break_replay = True
                else:
                    break_replay = False
                if not break_replay:
                    plan, role = self._require_plan(
                        session,
                        plan_id=plan_id,
                        project_id=project_id,
                        identity=identity,
                        for_update=True,
                    )
                    if role is TeamRole.RESEARCHER and plan.created_by != identity.user_id:
                        raise AuthorizationError("Researcher 只能决定自己创建的实验计划")
                    revision = self._current_revision(session, plan)
                    if revision.revision != request.expected_revision:
                        raise ConflictError("计划 revision 已变化，请刷新后重试")
                    review = session.scalar(
                        select(ExperimentPlanReview)
                        .where(ExperimentPlanReview.revision_id == revision.id)
                        .with_for_update()
                    )
                    if review is None:
                        raise ConflictError("当前 revision 尚未形成成功审核")
                    if review.review_hash != request.review_hash or (
                        review.approval_digest != request.approval_digest
                    ):
                        raise ConflictError("审核摘要已经变化，请重新核对")
                    allowed_states = {
                        ExperimentPlanStatus.READY_FOR_APPROVAL,
                        ExperimentPlanStatus.NEEDS_USER_INPUT,
                    }
                    if plan.status not in allowed_states:
                        raise ConflictError("当前计划状态不允许作出该决定")
                    _, current_hash = self._load_current_policy(session, plan, identity)
                    if current_hash != revision.policy_hash:
                        raise _FormalPolicyDrift
                    candidates = {
                        str(item["candidate_id"]): item for item in review.candidate_invariants
                    }
                    confirmed_ids = set(request.confirmed_candidate_ids)
                    rejected_ids = set(request.rejected_candidate_ids)
                    if not (confirmed_ids | rejected_ids).issubset(candidates):
                        raise InputValidationError("计划决定引用了未知候选不变量")
                    approval_decisions = {
                        ExperimentPlanDecisionType.APPROVED,
                        ExperimentPlanDecisionType.CONDITIONALLY_APPROVED,
                    }
                    if request.decision in approval_decisions and (
                        confirmed_ids | rejected_ids != set(candidates)
                    ):
                        raise InputValidationError("批准前必须逐项确认或拒绝全部候选不变量")
                    if request.decision not in approval_decisions and (
                        confirmed_ids or rejected_ids or request.conditions
                    ):
                        raise InputValidationError("拒绝或要求修改不能冻结候选不变量或批准条件")
                    if request.decision in approval_decisions and (
                        plan.status is not ExperimentPlanStatus.READY_FOR_APPROVAL
                    ):
                        raise ConflictError("存在待用户处理的问题，不能直接批准")
                    confirmed = [candidates[item] for item in request.confirmed_candidate_ids]
                    rejected = [candidates[item] for item in request.rejected_candidate_ids]
                    approved_snapshot = {
                        "checkpoint_schema_version": 1,
                        "plan": self._revision_view(revision).model_dump(mode="json"),
                        "review_hash": review.review_hash,
                        "approval_digest": review.approval_digest,
                        "hard_check": review.hard_check,
                        "existing_formal_invariants": revision.policy_snapshot.get(
                            "constraints", []
                        ),
                        "confirmed_candidate_invariants": confirmed,
                        "rejected_candidate_invariants": rejected,
                        "conditions": request.conditions,
                        "governance_notice": (
                            "该决定只冻结计划级授权；正式 Plan Check 和 LOCKED 规则仍独立生效。"
                        ),
                    }
                    decision_payload = {
                        "plan_id": str(plan.id),
                        "revision_id": str(revision.id),
                        "decision": request.decision.value,
                        "reason": request.reason,
                        "approved_snapshot": approved_snapshot,
                        "decided_by": str(identity.user_id),
                    }
                    decision = ExperimentPlanDecision(
                        plan_id=plan.id,
                        revision_id=revision.id,
                        decided_by=identity.user_id,
                        decided_session_id=identity.token_id,
                        decision=request.decision,
                        reason=request.reason,
                        conditions=request.conditions,
                        confirmed_candidate_invariants=confirmed,
                        rejected_candidate_invariants=rejected,
                        approved_snapshot=approved_snapshot,
                        review_hash=review.review_hash,
                        decision_hash=canonical_hash(decision_payload),
                    )
                    session.add(decision)
                    plan.status = ExperimentPlanStatus(request.decision.value)
                    self._save_idempotency(
                        session,
                        actor_id=identity.user_id,
                        operation=PLAN_DECIDE_OPERATION,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                        response={
                            "plan_id": str(plan.id),
                            "revision": revision.revision,
                            "decision": request.decision.value,
                        },
                    )
                    session.add(
                        AuditLog(
                            team_id=plan.team_id,
                            project_id=plan.project_id,
                            actor_type="USER",
                            actor_id=identity.user_id,
                            action="experiment_plan.decided",
                            target_type="EXPERIMENT_PLAN",
                            target_id=plan.id,
                            before_value={"status": "READY_FOR_APPROVAL"},
                            after_value={
                                "status": plan.status.value,
                                "revision": revision.revision,
                                "decision_hash": decision.decision_hash,
                                "session_id": str(identity.token_id),
                            },
                        )
                    )
            return self.get(project_id=project_id, plan_id=plan_id, identity=identity)

        try:
            return run_with_serialization_retry(operation)
        except _FormalPolicyDrift:
            self._persist_stale_status(
                project_id=project_id,
                plan_id=plan_id,
                identity=identity,
            )
            raise ConflictError("正式策略已变化，必须提交新 revision 重新审核") from None

    def persist_review(
        self,
        *,
        session: Session,
        run: AgentRun,
        final_message_id: UUID,
        payload: ExperimentPlanReviewPayload,
        evidence_ids: list[str],
    ) -> tuple[ExperimentPlanReview, AgentRun | None]:
        """在 Agent 最终回答事务中写入审核，并按上限排队下一轮修订。"""

        revision = session.get(
            ExperimentPlanRevision,
            run.target_experiment_plan_revision_id,
            with_for_update=True,
        )
        if revision is None:
            raise ServiceUnavailableError("计划审核目标 revision 不存在")
        plan = session.get(ExperimentPlan, revision.plan_id, with_for_update=True)
        thread = session.get(AgentThread, run.thread_id, with_for_update=True)
        if plan is None or thread is None or plan.current_revision != revision.revision:
            raise ConflictError("计划审核目标已经变化")
        if (
            session.scalar(
                select(ExperimentPlanReview.id).where(
                    ExperimentPlanReview.revision_id == revision.id
                )
            )
            is not None
        ):
            raise ConflictError("该计划 revision 已经完成审核")
        hard_check = ExperimentPlanHardCheck.model_validate(
            run.context_snapshot.get("experiment_plan_hard_check")
        )
        semantic = payload.model_dump(mode="json")
        candidates: list[dict[str, Any]] = []
        for index, item in enumerate(payload.candidate_invariants, start=1):
            candidate = item.model_dump(mode="json")
            candidate_id = (
                "ci_"
                + canonical_hash(
                    {"revision_id": str(revision.id), "ordinal": index, "payload": candidate}
                )[:24]
            )
            candidates.append({"candidate_id": candidate_id, **candidate})
        receipt = {
            "title": revision.title,
            "objective_and_approach": payload.review_markdown,
            "formal_policy": {
                "context_id": str(revision.context_id),
                "context_version": revision.context_version,
                "intent_id": str(revision.intent_id) if revision.intent_id else None,
                "intent_version": revision.intent_version,
                "policy_hash": revision.policy_hash,
            },
            "hard_check": hard_check.model_dump(mode="json"),
            "recommendation": payload.recommendation.value,
            "candidate_invariants": candidates,
            "free_exploration": payload.free_exploration,
            "user_decisions": payload.user_decisions,
            "citation_ids": evidence_ids,
            "notice": ("Agent 审核属于分析；只有人类对精确 revision 的决定才能冻结计划级条件。"),
        }
        review_hash = canonical_hash(
            {
                "revision_content_hash": revision.content_hash,
                "revision_evidence_hash": revision.evidence_hash,
                "hard_check": hard_check.model_dump(mode="json"),
                "semantic_review": semantic,
                "candidate_invariants": candidates,
            }
        )
        approval_digest = canonical_hash({"review_hash": review_hash, "approval_receipt": receipt})
        review = ExperimentPlanReview(
            revision_id=revision.id,
            source_run_id=run.id,
            final_message_id=final_message_id,
            hard_check=hard_check.model_dump(mode="json"),
            semantic_review=semantic,
            candidate_invariants=candidates,
            approval_receipt=receipt,
            review_hash=review_hash,
            approval_digest=approval_digest,
            provider=run.provider,
            model_id=run.model_id,
            prompt_version=run.prompt_version,
            schema_version=payload.schema_version,
        )
        session.add(review)
        session.flush()
        next_run: AgentRun | None = None
        can_auto_revise = (
            hard_check.status != "BLOCKED"
            and payload.recommendation is ExperimentPlanReviewRecommendation.REVISE
            and revision.automatic_revision_round < 2
            and not payload.user_decisions
            and all(item.auto_fixable for item in payload.findings)
        )
        if can_auto_revise:
            request = ExperimentPlanSubmitRequest(
                title=revision.title,
                plan_markdown=payload.revised_plan_markdown or revision.plan_markdown,
                evidence=ExperimentPlanEvidence.model_validate(revision.evidence),
            )
            next_revision = self._new_revision(
                plan=plan,
                revision=revision.revision + 1,
                author_type=ExperimentPlanRevisionAuthor.INTERNAL_AGENT,
                author_id=None,
                parent=revision,
                source_run_id=run.id,
                automatic_round=revision.automatic_revision_round + 1,
                request=request,
                policy_snapshot=revision.policy_snapshot,
                policy_hash=revision.policy_hash,
                context_id=revision.context_id,
                context_version=revision.context_version,
                intent_id=revision.intent_id,
                intent_version=revision.intent_version,
            )
            session.add(next_revision)
            session.flush()
            plan.current_revision = next_revision.revision
            plan.status = ExperimentPlanStatus.REVIEW_QUEUED
            next_run = self._queue_review(
                session,
                thread=thread,
                plan=plan,
                revision=next_revision,
                hard_check=hard_check,
                auth=self._auth_binding_from_run(run),
                created_by=run.created_by,
            )
        elif hard_check.status == "BLOCKED" or payload.recommendation in {
            ExperimentPlanReviewRecommendation.BLOCKED,
            ExperimentPlanReviewRecommendation.NEEDS_USER_INPUT,
        }:
            plan.status = ExperimentPlanStatus.NEEDS_USER_INPUT
        elif payload.recommendation is ExperimentPlanReviewRecommendation.READY:
            plan.status = ExperimentPlanStatus.READY_FOR_APPROVAL
        else:
            plan.status = ExperimentPlanStatus.NEEDS_USER_INPUT
        session.add(
            AuditLog(
                team_id=plan.team_id,
                project_id=plan.project_id,
                actor_type="AGENT",
                actor_id=run.created_by,
                action="experiment_plan.reviewed",
                target_type="EXPERIMENT_PLAN_REVISION",
                target_id=revision.id,
                before_value=None,
                after_value={
                    "review_id": str(review.id),
                    "recommendation": payload.recommendation.value,
                    "review_hash": review_hash,
                    "auto_revision_run_id": str(next_run.id) if next_run else None,
                },
            )
        )
        return review, next_run

    def review_policy_is_current(
        self,
        *,
        session: Session,
        run: AgentRun,
        identity: RequestIdentity,
    ) -> bool:
        """在调用模型前核对计划绑定的正式策略，避免为过期依据生成审核回执。"""

        if (
            run.run_kind is not AgentRunKind.EXPERIMENT_PLAN_REVIEW
            or run.target_experiment_plan_revision_id is None
        ):
            return True
        revision = session.get(ExperimentPlanRevision, run.target_experiment_plan_revision_id)
        if revision is None:
            return False
        plan = session.get(ExperimentPlan, revision.plan_id, with_for_update=True)
        if plan is None or plan.current_revision != revision.revision:
            return False
        _, current_hash = self._load_current_policy(session, plan, identity)
        if current_hash == revision.policy_hash:
            return True
        previous_status = plan.status.value
        plan.status = ExperimentPlanStatus.STALE
        session.add(
            AuditLog(
                team_id=plan.team_id,
                project_id=plan.project_id,
                actor_type="SYSTEM",
                actor_id=run.created_by,
                action="experiment_plan.review_blocked_by_policy_drift",
                target_type="EXPERIMENT_PLAN_REVISION",
                target_id=revision.id,
                before_value={"status": previous_status, "policy_hash": revision.policy_hash},
                after_value={"status": plan.status.value, "policy_hash": current_hash},
            )
        )
        return False

    def _persist_stale_status(
        self,
        *,
        project_id: UUID,
        plan_id: UUID,
        identity: RequestIdentity,
    ) -> None:
        def operation() -> None:
            with self._session_factory() as session, session.begin():
                plan, _ = self._require_plan(
                    session,
                    plan_id=plan_id,
                    project_id=project_id,
                    identity=identity,
                    for_update=True,
                )
                revision = self._current_revision(session, plan)
                _, current_hash = self._load_current_policy(session, plan, identity)
                if (
                    current_hash == revision.policy_hash
                    or plan.status is ExperimentPlanStatus.STALE
                ):
                    return
                previous_status = plan.status.value
                plan.status = ExperimentPlanStatus.STALE
                session.add(
                    AuditLog(
                        team_id=plan.team_id,
                        project_id=plan.project_id,
                        actor_type="SYSTEM",
                        actor_id=identity.user_id,
                        action="experiment_plan.marked_stale",
                        target_type="EXPERIMENT_PLAN_REVISION",
                        target_id=revision.id,
                        before_value={
                            "status": previous_status,
                            "policy_hash": revision.policy_hash,
                        },
                        after_value={
                            "status": plan.status.value,
                            "policy_hash": current_hash,
                        },
                    )
                )

        run_with_serialization_retry(operation)

    def _queue_review(
        self,
        session: Session,
        *,
        thread: AgentThread,
        plan: ExperimentPlan,
        revision: ExperimentPlanRevision,
        hard_check: ExperimentPlanHardCheck,
        auth: dict[str, object],
        created_by: UUID,
    ) -> AgentRun:
        thread.last_sequence += 1
        message = AgentMessage(
            thread_id=thread.id,
            sequence=thread.last_sequence,
            role=AgentMessageRole.USER,
            content=(
                f"[实验计划审核] revision {revision.revision} 已提交。"
                "请以计划工作台中的不可变正文和证据为准。"
            ),
            content_sha256=canonical_hash(
                {"revision_id": str(revision.id), "content_hash": revision.content_hash}
            ),
            created_by=created_by,
        )
        session.add(message)
        session.flush()
        run = AgentRun(
            thread_id=thread.id,
            team_id=plan.team_id,
            project_id=plan.project_id,
            created_by=created_by,
            **auth,
            trigger_message_id=message.id,
            idempotency_key=uuid5(NAMESPACE_URL, f"experiment-plan-review:{revision.id}"),
            request_hash=canonical_hash(
                {"revision_id": str(revision.id), "policy_hash": revision.policy_hash}
            ),
            status=AgentRunStatus.PENDING,
            run_kind=AgentRunKind.EXPERIMENT_PLAN_REVIEW,
            target_experiment_plan_revision_id=revision.id,
            provider=self._settings.agent_provider,
            model_id=self._settings.agent_model_id,
            prompt_version=PLAN_REVIEW_PROMPT_VERSION,
            tool_catalog_version=PLAN_REVIEW_TOOL_CATALOG_VERSION,
            context_snapshot={
                "capability_domain": "PLAN_REVIEW",
                "experiment_plan_revision_id": str(revision.id),
                "experiment_plan_hard_check": hard_check.model_dump(mode="json"),
            },
            usage={},
            generation=0,
            attempt_count=0,
            max_attempts=self._settings.agent_run_max_attempts,
        )
        session.add(run)
        session.flush()
        message.run_id = run.id
        self._agent_repository.append_event(
            session,
            run=run,
            event_type="run.queued",
            payload={
                "status": run.status.value,
                "kind": run.run_kind.value,
                "plan_id": str(plan.id),
                "revision": revision.revision,
            },
        )
        return run

    @staticmethod
    def _new_revision(
        *,
        plan: ExperimentPlan,
        revision: int,
        author_type: ExperimentPlanRevisionAuthor,
        author_id: UUID | None,
        parent: ExperimentPlanRevision | None,
        source_run_id: UUID | None,
        automatic_round: int,
        request: ExperimentPlanSubmitRequest,
        policy_snapshot: dict[str, Any],
        policy_hash: str,
        context_id: UUID,
        context_version: int,
        intent_id: UUID | None,
        intent_version: int | None,
    ) -> ExperimentPlanRevision:
        evidence = request.evidence.model_dump(mode="json")
        return ExperimentPlanRevision(
            plan_id=plan.id,
            revision=revision,
            author_type=author_type,
            author_id=author_id,
            parent_revision_id=parent.id if parent else None,
            source_run_id=source_run_id,
            automatic_revision_round=automatic_round,
            title=request.title,
            plan_markdown=request.plan_markdown,
            evidence=evidence,
            context_id=context_id,
            context_version=context_version,
            intent_id=intent_id,
            intent_version=intent_version,
            policy_snapshot=policy_snapshot,
            policy_hash=policy_hash,
            content_hash=canonical_hash(
                {"title": request.title, "plan_markdown": request.plan_markdown}
            ),
            evidence_hash=canonical_hash(evidence),
        )

    def _summary(
        self, session: Session, plan: ExperimentPlan, identity: RequestIdentity
    ) -> ExperimentPlanSummary:
        revision = self._current_revision(session, plan)
        current_hash = self._current_policy_hash(session, plan, identity)
        return ExperimentPlanSummary(
            plan_id=plan.id,
            project_id=plan.project_id,
            task_id=plan.source_thread_id,
            created_by=plan.created_by,
            title=revision.title,
            status=plan.status,
            current_revision=plan.current_revision,
            freshness="CURRENT" if current_hash == revision.policy_hash else "STALE",
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        )

    @staticmethod
    def _revision_view(row: ExperimentPlanRevision) -> ExperimentPlanRevisionView:
        return ExperimentPlanRevisionView(
            revision_id=row.id,
            plan_id=row.plan_id,
            revision=row.revision,
            author_type=row.author_type,
            author_id=row.author_id,
            parent_revision_id=row.parent_revision_id,
            source_run_id=row.source_run_id,
            automatic_revision_round=row.automatic_revision_round,
            title=row.title,
            plan_markdown=row.plan_markdown,
            evidence=ExperimentPlanEvidence.model_validate(row.evidence),
            context_id=row.context_id,
            context_version=row.context_version,
            intent_id=row.intent_id,
            intent_version=row.intent_version,
            policy_snapshot=row.policy_snapshot,
            policy_hash=row.policy_hash,
            content_hash=row.content_hash,
            evidence_hash=row.evidence_hash,
            created_at=row.created_at,
        )

    @staticmethod
    def _review_view(row: ExperimentPlanReview) -> ExperimentPlanReviewView:
        return ExperimentPlanReviewView(
            review_id=row.id,
            revision_id=row.revision_id,
            source_run_id=row.source_run_id,
            hard_check=ExperimentPlanHardCheck.model_validate(row.hard_check),
            semantic_review=ExperimentPlanReviewPayload.model_validate(row.semantic_review),
            candidate_invariants=row.candidate_invariants,
            approval_receipt=row.approval_receipt,
            review_hash=row.review_hash,
            approval_digest=row.approval_digest,
            provider=row.provider,
            model_id=row.model_id,
            prompt_version=row.prompt_version,
            created_at=row.created_at,
        )

    @staticmethod
    def _decision_view(row: ExperimentPlanDecision) -> dict[str, Any]:
        return {
            "decision_id": str(row.id),
            "decision": row.decision.value,
            "decided_by": str(row.decided_by),
            "reason": row.reason,
            "conditions": row.conditions,
            "confirmed_candidate_invariants": row.confirmed_candidate_invariants,
            "rejected_candidate_invariants": row.rejected_candidate_invariants,
            "approved_snapshot": row.approved_snapshot,
            "review_hash": row.review_hash,
            "decision_hash": row.decision_hash,
            "created_at": row.created_at.isoformat(),
        }

    @staticmethod
    def _allowed_actions(
        *,
        plan: ExperimentPlan,
        role: TeamRole,
        identity: RequestIdentity,
        review: ExperimentPlanReview | None,
        current_policy: str,
        current_revision: ExperimentPlanRevision,
    ) -> list[str]:
        owns = plan.created_by == identity.user_id
        can_manage = role is TeamRole.OWNER or owns
        actions: list[str] = []
        if not can_manage:
            return actions
        if plan.status in {
            ExperimentPlanStatus.NEEDS_USER_INPUT,
            ExperimentPlanStatus.REVIEW_FAILED,
            ExperimentPlanStatus.STALE,
            ExperimentPlanStatus.CHANGES_REQUESTED,
        }:
            actions.append("REVISE")
        if plan.status is ExperimentPlanStatus.REVIEW_FAILED:
            actions.append("RETRY_REVIEW")
        if (
            review is not None
            and current_policy == current_revision.policy_hash
            and plan.status
            in {
                ExperimentPlanStatus.READY_FOR_APPROVAL,
                ExperimentPlanStatus.NEEDS_USER_INPUT,
            }
        ):
            actions.extend(["REJECT", "REQUEST_CHANGES"])
            if plan.status is ExperimentPlanStatus.READY_FOR_APPROVAL:
                actions.extend(["APPROVE", "CONDITIONAL_APPROVE"])
        return actions

    def _current_policy_hash(
        self, session: Session, plan: ExperimentPlan, identity: RequestIdentity
    ) -> str:
        _, value = self._load_current_policy(session, plan, identity)
        return value

    def _load_current_policy(
        self, session: Session, plan: ExperimentPlan, identity: RequestIdentity
    ) -> tuple[dict[str, Any], str]:
        bundle = self._projects.load_context_bundle(
            session,
            project_id=plan.project_id,
            user_id=identity.user_id,
            team_id=identity.team_id,
        )
        return formal_policy_snapshot(bundle)

    @staticmethod
    def _current_revision(session: Session, plan: ExperimentPlan) -> ExperimentPlanRevision:
        row = session.scalar(
            select(ExperimentPlanRevision).where(
                ExperimentPlanRevision.plan_id == plan.id,
                ExperimentPlanRevision.revision == plan.current_revision,
            )
        )
        if row is None:
            raise ServiceUnavailableError("计划当前 revision 不存在")
        return row

    def _require_plan(
        self,
        session: Session,
        *,
        plan_id: UUID,
        identity: RequestIdentity,
        project_id: UUID | None,
        for_update: bool = False,
    ) -> tuple[ExperimentPlan, TeamRole]:
        if project_id is None:
            raise AuthorizationError("身份未绑定项目")
        _, role = self._require_project(session, project_id, identity)
        statement = select(ExperimentPlan).where(
            ExperimentPlan.id == plan_id,
            ExperimentPlan.project_id == project_id,
        )
        if for_update:
            statement = statement.with_for_update()
        plan = session.scalar(statement)
        if plan is None:
            raise ResourceNotFoundError("项目中不存在该实验计划")
        if identity.authentication_method in {"MCP_TOKEN", "MCP_OAUTH"} and (
            plan.created_by != identity.user_id
        ):
            raise ResourceNotFoundError("项目中不存在该实验计划")
        return plan, role

    def _require_project(
        self, session: Session, project_id: UUID, identity: RequestIdentity
    ) -> tuple[object, TeamRole]:
        project = self._projects.require_project_member(
            session,
            project_id=project_id,
            user_id=identity.user_id,
            team_id=identity.team_id,
        )
        membership = session.get(TeamMember, (identity.team_id, identity.user_id))
        if membership is None:
            raise AuthorizationError("当前用户不是团队成员")
        return project, membership.role

    def _require_task(
        self,
        session: Session,
        task_id: UUID,
        identity: RequestIdentity,
        *,
        for_update: bool,
    ) -> AgentThread:
        statement = select(AgentThread).where(
            AgentThread.id == task_id,
            AgentThread.project_id == identity.project_id,
            AgentThread.created_by == identity.user_id,
            AgentThread.origin == AgentThreadOrigin.EXTERNAL_MCP,
        )
        if for_update:
            statement = statement.with_for_update()
        thread = session.scalar(statement)
        if thread is None:
            raise ResourceNotFoundError("项目中不存在该外部 Agent 任务")
        if thread.status is AgentThreadStatus.ARCHIVED:
            raise ConflictError("归档任务不能提交实验计划")
        self._require_project(session, thread.project_id, identity)
        return thread

    @staticmethod
    def _require_idle_thread(session: Session, thread_id: UUID) -> None:
        active = session.scalar(
            select(AgentRun.id).where(
                AgentRun.thread_id == thread_id,
                AgentRun.status.in_(ACTIVE_RUN_STATUSES),
            )
        )
        if active is not None:
            raise ConflictError("当前任务已有运行中的 Agent 请求")

    def _require_enabled(self) -> None:
        if not self._settings.agent_enabled:
            raise ServiceUnavailableError("内部治理 Agent 当前未启用")

    @staticmethod
    def _require_external_identity(
        identity: RequestIdentity, *, require_write: bool = True
    ) -> None:
        if identity.authentication_method not in {"MCP_TOKEN", "MCP_OAUTH"}:
            raise AuthorizationError("外部实验计划只接受 MCP 身份")
        if identity.project_id is None:
            raise AuthorizationError("MCP 身份未绑定项目")
        required = {"project:read", "experiment:query"}
        if require_write:
            required.add("experiment:check")
        if not required.issubset(identity.scopes):
            raise AuthorizationError("MCP 身份缺少实验计划所需权限")
        if identity.credential_expires_at is not None and (
            identity.credential_expires_at <= datetime.now(UTC)
        ):
            raise AuthenticationError("MCP 凭据已过期")

    @staticmethod
    def _require_web(identity: RequestIdentity) -> None:
        if identity.authentication_method != "WEB_SESSION":
            raise AuthorizationError("该操作只接受 Web Session")

    @staticmethod
    def _auth_binding(identity: RequestIdentity) -> dict[str, object]:
        common: dict[str, object] = {
            "auth_scopes_snapshot": sorted(identity.scopes),
            "auth_expires_at": identity.credential_expires_at,
        }
        if identity.authentication_method == "WEB_SESSION":
            return {
                **common,
                "auth_method": AgentRunAuthMethod.WEB_SESSION,
                "auth_session_id": identity.token_id,
                "auth_access_token_id": None,
                "auth_oauth_grant_id": None,
            }
        if identity.authentication_method == "MCP_TOKEN":
            return {
                **common,
                "auth_method": AgentRunAuthMethod.MCP_TOKEN,
                "auth_session_id": None,
                "auth_access_token_id": identity.token_id,
                "auth_oauth_grant_id": None,
            }
        if identity.authentication_method == "MCP_OAUTH":
            if identity.credential_expires_at is None:
                raise AuthenticationError("MCP OAuth 身份缺少过期时间")
            return {
                **common,
                "auth_method": AgentRunAuthMethod.MCP_OAUTH,
                "auth_session_id": None,
                "auth_access_token_id": None,
                "auth_oauth_grant_id": identity.token_id,
            }
        raise AuthorizationError("当前认证方式不能创建 Agent Run")

    @staticmethod
    def _auth_binding_from_run(run: AgentRun) -> dict[str, object]:
        return {
            "auth_method": run.auth_method,
            "auth_session_id": run.auth_session_id,
            "auth_access_token_id": run.auth_access_token_id,
            "auth_oauth_grant_id": run.auth_oauth_grant_id,
            "auth_scopes_snapshot": list(run.auth_scopes_snapshot),
            "auth_expires_at": run.auth_expires_at,
        }

    @staticmethod
    def _auth_audit(identity: RequestIdentity) -> dict[str, object]:
        return {
            "authentication_method": identity.authentication_method,
            "credential_id": str(identity.token_id),
            "client_id": identity.client_id,
        }

    def _receipt(
        self,
        plan: ExperimentPlan,
        revision: ExperimentPlanRevision,
        run: AgentRun,
    ) -> ExperimentPlanReceipt:
        return ExperimentPlanReceipt(
            plan_id=plan.id,
            task_id=plan.source_thread_id,
            revision_id=revision.id,
            revision=revision.revision,
            status=plan.status,
            run_id=run.id,
            poll_after_seconds=self._settings.agent_run_poll_interval_seconds,
        )

    @staticmethod
    def _find_idempotency(
        session: Session, actor_id: UUID, operation: str, key: UUID
    ) -> IdempotencyRecord | None:
        return session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.actor_id == actor_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.idempotency_key == key,
            )
        )

    @staticmethod
    def _save_idempotency(
        session: Session,
        *,
        actor_id: UUID,
        operation: str,
        idempotency_key: UUID,
        request_hash: str,
        response: dict[str, Any],
    ) -> None:
        session.add(
            IdempotencyRecord(
                actor_id=actor_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response_snapshot=response,
                operation_status=IdempotencyOperationStatus.COMPLETED,
            )
        )

    @staticmethod
    def _replay_receipt(record: IdempotencyRecord, request_hash: str) -> ExperimentPlanReceipt:
        if record.request_hash != request_hash:
            raise ConflictError("相同 Idempotency-Key 已用于不同请求")
        if (
            record.operation_status is not IdempotencyOperationStatus.COMPLETED
            or record.response_snapshot is None
        ):
            raise ServiceUnavailableError("实验计划幂等结果尚未完成")
        return ExperimentPlanReceipt.model_validate(record.response_snapshot)
