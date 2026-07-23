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
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.policy_drafts import (
    PolicyDraftProposalSource,
    PolicyDraftService,
)
from experiment_guardian.application.transactions import run_with_serialization_retry
from experiment_guardian.application.web_management import WebManagementService
from experiment_guardian.domain.action_proposal import (
    ACTION_PROPOSAL_TTL_HOURS,
    ActionProposalCancelRequest,
    ActionProposalConfirmRequest,
    ActionProposalPage,
    ActionProposalPrepareInput,
    ActionProposalView,
    build_action_proposal_digest,
)
from experiment_guardian.domain.enums import (
    ActionProposalConfirmability,
    ActionProposalOperation,
    ActionProposalStatus,
    IdempotencyOperationStatus,
    PolicyDraftFreshness,
    PolicyDraftReadiness,
    PolicyDraftStatus,
    TeamRole,
)
from experiment_guardian.domain.policy_draft import canonical_hash
from experiment_guardian.domain.web_management import PolicyPublishRequest
from experiment_guardian.infrastructure.models import (
    AgentActionProposal,
    AgentPolicyDraftRevision,
    AuditLog,
    IdempotencyRecord,
    Project,
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
    ) -> None:
        self._session_factory = session_factory
        self._projects = projects
        self._policy_drafts = policy_drafts
        self._web_management = web_management

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
                existing_run = session.scalar(
                    select(AgentActionProposal).where(
                        AgentActionProposal.source_run_id == run_id
                    )
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

                run, thread = self._policy_drafts.require_agent_source(
                    session,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    project_id=project_id,
                    identity=identity,
                )
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
                        AgentActionProposal.source_draft_revision_id
                        == source.revision.id,
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
                statement = statement.where(
                    AgentActionProposal.created_by == identity.user_id
                )
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
                    self._view(session, row, identity=identity, role=role)
                    for row in rows[:limit]
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
        self._require_web_identity(identity, write=True)
        if not identity.recent_authentication:
            raise RecentAuthenticationRequiredError(
                "确认正式策略发布提案前需要完成近期身份认证"
            )
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
                    owner=True,
                )
                replay = self._idempotency(
                    session,
                    identity=identity,
                    operation=ACTION_PROPOSAL_CONFIRM_OPERATION,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
                if replay is not None:
                    return self._view(session, proposal, identity=identity, role=role)
                if proposal.proposal_digest != request.proposal_digest:
                    raise ConflictError("提案摘要不匹配，请刷新完整内容后重新确认")

                confirmability, reasons = self._confirmability(
                    session, proposal, identity=identity
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
                now = datetime.now(UTC)
                proposal.status = ActionProposalStatus.EXECUTED
                proposal.confirmed_by = identity.user_id
                proposal.confirmed_session_id = identity.token_id
                proposal.confirmed_at = now
                proposal.executed_context_id = publish_result.context_bundle.context.context_id
                proposal.executed_context_version = (
                    publish_result.context_bundle.context.version
                )
                proposal.execution_result = publish_result.model_dump(mode="json")
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
                    after_value={
                        "context_id": str(proposal.executed_context_id),
                        "context_version": proposal.executed_context_version,
                        "policy_idempotency_key": str(policy_key),
                    },
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

    def _confirmability(
        self,
        session: Session,
        proposal: AgentActionProposal,
        *,
        identity: RequestIdentity,
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

        reasons: list[str] = []
        try:
            source = self._policy_drafts.load_proposal_source(
                session,
                project_id=proposal.project_id,
                draft_id=proposal.source_draft_id,
                identity=identity,
                for_update=False,
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
        confirmability, reasons = self._confirmability(
            session, proposal, identity=identity
        )
        actions: list[str] = []
        if proposal.status is ActionProposalStatus.PROPOSED:
            if role is TeamRole.OWNER and confirmability is ActionProposalConfirmability.READY:
                actions.append("CONFIRM")
            if role is TeamRole.OWNER or proposal.created_by == identity.user_id:
                actions.append("CANCEL")
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
            payload=PolicyPublishRequest.model_validate(proposal.payload),
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
            raise AuthorizationError("只有 Owner 可以确认正式策略发布提案")
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
        if row is not None and (
            row.request_hash != request_hash or row.response_snapshot is None
        ):
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
