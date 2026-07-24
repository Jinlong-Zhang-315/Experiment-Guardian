"""治理 Agent 会话用例和后台身份恢复边界。"""

import base64
import binascii
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

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
from experiment_guardian.application.web_auth import OWNER_WEB_SCOPES, RESEARCHER_WEB_SCOPES
from experiment_guardian.core.config import Settings
from experiment_guardian.domain.agent import (
    AgentAnswerSection,
    AgentCitationView,
    AgentContextSummaryView,
    AgentMessageCreateRequest,
    AgentMessageView,
    AgentRunReceipt,
    AgentRunView,
    AgentThreadCreateRequest,
    AgentThreadPage,
    AgentThreadSummary,
    AgentThreadUpdateRequest,
    AgentThreadView,
)
from experiment_guardian.domain.enums import (
    AgentContextSummaryStatus,
    AgentEvidenceKind,
    AgentMessageRole,
    AgentRunStatus,
    AgentThreadStatus,
    TeamRole,
)
from experiment_guardian.infrastructure.models import (
    AgentCitation,
    AgentContextSummary,
    AgentMessage,
    AgentRun,
    AgentThread,
    AuditLog,
    TeamMember,
    WebSession,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyAgentRepository,
    SqlAlchemyProjectRepository,
)

PROMPT_VERSION = "r15d-b1-v1"
TOOL_CATALOG_VERSION = "r15d-b1-v1"
TERMINAL_RUN_STATUSES = {
    AgentRunStatus.SUCCEEDED,
    AgentRunStatus.FAILED,
    AgentRunStatus.DEAD_LETTER,
}
ACTIVE_RUN_STATUSES = {
    AgentRunStatus.PENDING,
    AgentRunStatus.RUNNING,
    AgentRunStatus.RETRYABLE_FAILURE,
}


def _hash_json(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    return hashlib.sha256(payload).hexdigest()


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        offset = int(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii"))
    except (binascii.Error, ValueError, UnicodeError) as exc:
        raise InputValidationError("Agent 会话分页 cursor 无效") from exc
    if offset < 0:
        raise InputValidationError("Agent 会话分页 cursor 无效")
    return offset


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii")


class AgentConversationService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        projects: SqlAlchemyProjectRepository,
        repository: SqlAlchemyAgentRepository,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._projects = projects
        self._repository = repository
        self._settings = settings

    def create_thread(
        self,
        *,
        project_id: UUID,
        identity: RequestIdentity,
        request: AgentThreadCreateRequest,
    ) -> AgentThreadSummary:
        self._require_enabled()
        self._require_web_identity(identity)
        with self._session_factory() as session, session.begin():
            project = self._projects.require_project_member(
                session,
                project_id=project_id,
                user_id=identity.user_id,
                team_id=identity.team_id,
            )
            thread = AgentThread(
                team_id=project.team_id,
                project_id=project.id,
                created_by=identity.user_id,
                title=request.title or "新对话",
                status=AgentThreadStatus.ACTIVE,
                last_sequence=0,
            )
            session.add(thread)
            session.flush()
            session.add(
                AuditLog(
                    team_id=project.team_id,
                    project_id=project.id,
                    actor_type="USER",
                    actor_id=identity.user_id,
                    action="agent.thread.created",
                    target_type="AGENT_THREAD",
                    target_id=thread.id,
                    before_value=None,
                    after_value={"session_id": str(identity.token_id)},
                )
            )
            return self._thread_summary(thread)

    def list_threads(
        self,
        *,
        project_id: UUID,
        identity: RequestIdentity,
        archived: bool,
        cursor: str | None,
        limit: int,
    ) -> AgentThreadPage:
        self._require_enabled()
        self._require_web_identity(identity)
        offset = _decode_cursor(cursor)
        with self._session_factory() as session:
            self._projects.require_project_member(
                session,
                project_id=project_id,
                user_id=identity.user_id,
                team_id=identity.team_id,
            )
            target_status = AgentThreadStatus.ARCHIVED if archived else AgentThreadStatus.ACTIVE
            rows = session.scalars(
                select(AgentThread)
                .where(
                    AgentThread.project_id == project_id,
                    AgentThread.created_by == identity.user_id,
                    AgentThread.status == target_status,
                )
                .order_by(AgentThread.updated_at.desc(), AgentThread.id.desc())
                .offset(offset)
                .limit(limit + 1)
            ).all()
            return AgentThreadPage(
                items=[self._thread_summary(item) for item in rows[:limit]],
                next_cursor=_encode_cursor(offset + limit) if len(rows) > limit else None,
            )

    def get_thread(
        self, *, project_id: UUID, thread_id: UUID, identity: RequestIdentity
    ) -> AgentThreadView:
        self._require_enabled()
        self._require_web_identity(identity)
        with self._session_factory() as session:
            thread = self._require_owned_thread(
                session, project_id=project_id, thread_id=thread_id, identity=identity
            )
            messages = list(
                session.scalars(
                    select(AgentMessage)
                    .where(AgentMessage.thread_id == thread.id)
                    .order_by(AgentMessage.sequence)
                ).all()
            )
            citations = (
                list(
                    session.scalars(
                        select(AgentCitation).where(
                            AgentCitation.message_id.in_([item.id for item in messages])
                        )
                    ).all()
                )
                if messages
                else []
            )
            run_ids = {item.run_id for item in messages if item.run_id is not None}
            runs = (
                {
                    item.id: item
                    for item in session.scalars(
                        select(AgentRun).where(AgentRun.id.in_(run_ids))
                    ).all()
                }
                if run_ids
                else {}
            )
            by_message: dict[UUID, list[AgentCitationView]] = {}
            for item in citations:
                by_message.setdefault(item.message_id, []).append(
                    AgentCitationView(
                        evidence_id=item.evidence_id,
                        evidence_kind=AgentEvidenceKind(item.evidence_kind),
                        entity_type=item.entity_type,
                        entity_id=item.entity_id,
                        entity_version=item.entity_version,
                        label=item.label,
                        excerpt=item.excerpt,
                    )
                )
            current_summary = (
                session.get(AgentContextSummary, thread.current_summary_id)
                if thread.current_summary_id is not None
                else None
            )
            latest_summary = session.scalar(
                select(AgentContextSummary)
                .where(AgentContextSummary.thread_id == thread.id)
                .order_by(
                    AgentContextSummary.created_at.desc(),
                    AgentContextSummary.id.desc(),
                )
                .limit(1)
            )
            summary_view: AgentContextSummaryView | None = None
            if current_summary is not None:
                degraded = bool(
                    latest_summary is not None
                    and latest_summary.id != current_summary.id
                    and latest_summary.status is AgentContextSummaryStatus.FAILED
                )
                summary_view = AgentContextSummaryView(
                    summary_id=current_summary.id,
                    status=current_summary.status,
                    covered_sequence_from=current_summary.covered_sequence_from,
                    covered_sequence_to=current_summary.covered_sequence_to,
                    provider=current_summary.provider,
                    model_id=current_summary.model_id,
                    generated_at=current_summary.created_at,
                    degraded=degraded,
                    warning=(
                        "最近一次摘要更新失败，当前对话继续使用上一版非权威摘要。"
                        if degraded
                        else None
                    ),
                )
            elif latest_summary is not None:
                summary_view = AgentContextSummaryView(
                    status=latest_summary.status,
                    covered_sequence_from=latest_summary.covered_sequence_from,
                    covered_sequence_to=latest_summary.covered_sequence_to,
                    provider=latest_summary.provider,
                    model_id=latest_summary.model_id,
                    generated_at=latest_summary.created_at,
                    degraded=True,
                    warning="对话摘要生成失败，当前回答使用最近消息降级上下文。",
                )
            view = AgentThreadView(
                thread=self._thread_summary(thread),
                messages=[
                    AgentMessageView(
                        message_id=item.id,
                        sequence=item.sequence,
                        role=item.role,
                        content=item.content,
                        run_id=item.run_id,
                        sections=[
                            AgentAnswerSection.model_validate(section)
                            for section in (
                                runs[item.run_id].context_snapshot.get("answer_sections", [])
                                if item.run_id in runs
                                else []
                            )
                        ],
                        citations=by_message.get(item.id, []),
                        created_at=item.created_at,
                    )
                    for item in messages
                ],
                context_summary=summary_view,
            )
            return view

    def update_thread(
        self,
        *,
        project_id: UUID,
        thread_id: UUID,
        identity: RequestIdentity,
        request: AgentThreadUpdateRequest,
    ) -> AgentThreadSummary:
        self._require_enabled()
        self._require_web_identity(identity)
        with self._session_factory() as session, session.begin():
            thread = self._require_owned_thread(
                session,
                project_id=project_id,
                thread_id=thread_id,
                identity=identity,
                for_update=True,
            )
            if request.archived:
                active = session.scalar(
                    select(AgentRun.id).where(
                        AgentRun.thread_id == thread.id,
                        AgentRun.status.in_(ACTIVE_RUN_STATUSES),
                    )
                )
                if active is not None:
                    raise ConflictError("运行中的 Agent 会话不能归档")
                thread.status = AgentThreadStatus.ARCHIVED
                thread.archived_at = datetime.now(UTC)
            else:
                thread.status = AgentThreadStatus.ACTIVE
                thread.archived_at = None
            session.add(
                AuditLog(
                    team_id=thread.team_id,
                    project_id=thread.project_id,
                    actor_type="USER",
                    actor_id=identity.user_id,
                    action="agent.thread.archived" if request.archived else "agent.thread.restored",
                    target_type="AGENT_THREAD",
                    target_id=thread.id,
                    before_value=None,
                    after_value={"session_id": str(identity.token_id)},
                )
            )
            return self._thread_summary(thread)

    def create_message(
        self,
        *,
        project_id: UUID,
        thread_id: UUID,
        identity: RequestIdentity,
        idempotency_key: UUID,
        request: AgentMessageCreateRequest,
    ) -> AgentRunReceipt:
        self._require_enabled()
        self._require_web_identity(identity)
        request_hash = _hash_json({"content": request.content})

        def operation() -> AgentRunReceipt:
            with self._session_factory() as session, session.begin():
                thread = self._require_owned_thread(
                    session,
                    project_id=project_id,
                    thread_id=thread_id,
                    identity=identity,
                    for_update=True,
                )
                existing = session.scalar(
                    select(AgentRun).where(
                        AgentRun.thread_id == thread.id,
                        AgentRun.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise ConflictError("相同 Idempotency-Key 已用于不同的 Agent 消息")
                    return self._run_receipt(existing)
                if thread.status is AgentThreadStatus.ARCHIVED:
                    raise ConflictError("归档会话不能发送消息，请先恢复")
                active = session.scalar(
                    select(AgentRun.id).where(
                        AgentRun.thread_id == thread.id,
                        AgentRun.status.in_(ACTIVE_RUN_STATUSES),
                    )
                )
                if active is not None:
                    raise ConflictError("当前会话已有运行中的 Agent 请求")

                thread.last_sequence += 1
                message = AgentMessage(
                    thread_id=thread.id,
                    sequence=thread.last_sequence,
                    role=AgentMessageRole.USER,
                    content=request.content,
                    content_sha256=_content_hash(request.content),
                    created_by=identity.user_id,
                )
                session.add(message)
                session.flush()
                if thread.title == "新对话":
                    thread.title = request.content[:40]

                run = AgentRun(
                    thread_id=thread.id,
                    team_id=thread.team_id,
                    project_id=thread.project_id,
                    created_by=identity.user_id,
                    auth_session_id=identity.token_id,
                    trigger_message_id=message.id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    status=AgentRunStatus.PENDING,
                    provider=self._settings.agent_provider,
                    model_id=self._settings.bailian_agent_model,
                    prompt_version=PROMPT_VERSION,
                    tool_catalog_version=TOOL_CATALOG_VERSION,
                    context_snapshot={},
                    usage={},
                    generation=0,
                    attempt_count=0,
                    max_attempts=self._settings.agent_run_max_attempts,
                )
                session.add(run)
                session.flush()
                message.run_id = run.id
                self._repository.append_event(
                    session,
                    run=run,
                    event_type="run.queued",
                    payload={"status": run.status.value},
                )
                session.add(
                    AuditLog(
                        team_id=thread.team_id,
                        project_id=thread.project_id,
                        actor_type="USER",
                        actor_id=identity.user_id,
                        action="agent.run.requested",
                        target_type="AGENT_RUN",
                        target_id=run.id,
                        before_value=None,
                        after_value={
                            "thread_id": str(thread.id),
                            "message_id": str(message.id),
                            "session_id": str(identity.token_id),
                        },
                    )
                )
                return self._run_receipt(run)

        return run_with_serialization_retry(operation)

    def get_run(self, *, project_id: UUID, run_id: UUID, identity: RequestIdentity) -> AgentRunView:
        self._require_enabled()
        self._require_web_identity(identity)
        with self._session_factory() as session:
            run = self._require_owned_run(
                session, project_id=project_id, run_id=run_id, identity=identity
            )
            return self._run_view(run)

    def retry_run(
        self,
        *,
        project_id: UUID,
        run_id: UUID,
        identity: RequestIdentity,
        idempotency_key: UUID,
    ) -> AgentRunReceipt:
        self._require_enabled()
        self._require_web_identity(identity)

        def operation() -> AgentRunReceipt:
            with self._session_factory() as session, session.begin():
                old = self._require_owned_run(
                    session,
                    project_id=project_id,
                    run_id=run_id,
                    identity=identity,
                    for_update=True,
                )
                if old.status not in {AgentRunStatus.FAILED, AgentRunStatus.DEAD_LETTER}:
                    raise ConflictError("只有失败的 Agent Run 可以重试")
                thread = session.get(AgentThread, old.thread_id, with_for_update=True)
                if thread is None or thread.status is AgentThreadStatus.ARCHIVED:
                    raise ConflictError("归档会话不能重试")
                existing = session.scalar(
                    select(AgentRun).where(
                        AgentRun.thread_id == old.thread_id,
                        AgentRun.idempotency_key == idempotency_key,
                    )
                )
                retry_hash = _hash_json({"retry_of": str(old.id)})
                if existing is not None:
                    if existing.request_hash != retry_hash:
                        raise ConflictError("相同 Idempotency-Key 已用于不同的 Agent 重试")
                    return self._run_receipt(existing)
                active = session.scalar(
                    select(AgentRun.id).where(
                        AgentRun.thread_id == old.thread_id,
                        AgentRun.status.in_(ACTIVE_RUN_STATUSES),
                    )
                )
                if active is not None:
                    raise ConflictError("当前会话已有运行中的 Agent 请求")
                run = AgentRun(
                    thread_id=old.thread_id,
                    team_id=old.team_id,
                    project_id=old.project_id,
                    created_by=identity.user_id,
                    auth_session_id=identity.token_id,
                    trigger_message_id=old.trigger_message_id,
                    idempotency_key=idempotency_key,
                    request_hash=retry_hash,
                    status=AgentRunStatus.PENDING,
                    provider=self._settings.agent_provider,
                    model_id=self._settings.bailian_agent_model,
                    prompt_version=old.prompt_version,
                    tool_catalog_version=old.tool_catalog_version,
                    context_snapshot={"retry_of": str(old.id)},
                    usage={},
                    generation=0,
                    attempt_count=0,
                    max_attempts=self._settings.agent_run_max_attempts,
                )
                session.add(run)
                session.flush()
                trigger_message = session.get(
                    AgentMessage, old.trigger_message_id, with_for_update=True
                )
                if trigger_message is None:
                    raise ServiceUnavailableError("Agent Run 的触发消息不存在")
                # AgentRun.trigger_message_id 保留完整历史；消息反向指针指向最新一次处理。
                trigger_message.run_id = run.id
                self._repository.append_event(
                    session,
                    run=run,
                    event_type="run.queued",
                    payload={"status": run.status.value, "retry_of": str(old.id)},
                )
                session.add(
                    AuditLog(
                        team_id=run.team_id,
                        project_id=run.project_id,
                        actor_type="USER",
                        actor_id=identity.user_id,
                        action="agent.run.retried",
                        target_type="AGENT_RUN",
                        target_id=run.id,
                        before_value={"run_id": str(old.id)},
                        after_value={
                            "session_id": str(identity.token_id),
                            "trigger_message_id": str(run.trigger_message_id),
                        },
                    )
                )
                return self._run_receipt(run)

        return run_with_serialization_retry(operation)

    def list_run_events(
        self,
        *,
        project_id: UUID,
        run_id: UUID,
        identity: RequestIdentity,
        after: int,
    ) -> tuple[list[dict[str, object]], AgentRunStatus]:
        self._require_enabled()
        self._require_web_identity(identity)
        with self._session_factory() as session:
            run = self._require_owned_run(
                session, project_id=project_id, run_id=run_id, identity=identity
            )
            events = self._repository.list_events(
                session, run_id=run_id, after=max(0, after), limit=100
            )
            return (
                [
                    {
                        "id": item.sequence,
                        "event": item.event_type,
                        "data": item.payload,
                    }
                    for item in events
                ],
                run.status,
            )

    def _require_owned_thread(
        self,
        session: Session,
        *,
        project_id: UUID,
        thread_id: UUID,
        identity: RequestIdentity,
        for_update: bool = False,
    ) -> AgentThread:
        self._projects.require_project_member(
            session,
            project_id=project_id,
            user_id=identity.user_id,
            team_id=identity.team_id,
        )
        statement = select(AgentThread).where(
            AgentThread.id == thread_id,
            AgentThread.project_id == project_id,
            AgentThread.created_by == identity.user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        thread = session.scalar(statement)
        if thread is None:
            raise ResourceNotFoundError("项目中不存在该 Agent 会话")
        return thread

    def _require_owned_run(
        self,
        session: Session,
        *,
        project_id: UUID,
        run_id: UUID,
        identity: RequestIdentity,
        for_update: bool = False,
    ) -> AgentRun:
        self._projects.require_project_member(
            session,
            project_id=project_id,
            user_id=identity.user_id,
            team_id=identity.team_id,
        )
        statement = select(AgentRun).where(
            AgentRun.id == run_id,
            AgentRun.project_id == project_id,
            AgentRun.created_by == identity.user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        run = session.scalar(statement)
        if run is None:
            raise ResourceNotFoundError("项目中不存在该 Agent Run")
        return run

    def _require_enabled(self) -> None:
        if not self._settings.agent_enabled:
            raise ServiceUnavailableError("内部治理 Agent 当前未启用")

    @staticmethod
    def _require_web_identity(identity: RequestIdentity) -> None:
        if identity.authentication_method != "WEB_SESSION":
            raise AuthorizationError("内部治理 Agent 只接受 Web Session")

    @staticmethod
    def _thread_summary(thread: AgentThread) -> AgentThreadSummary:
        return AgentThreadSummary(
            thread_id=thread.id,
            project_id=thread.project_id,
            title=thread.title,
            status=thread.status,
            created_at=thread.created_at,
            updated_at=thread.updated_at,
            archived_at=thread.archived_at,
        )

    def _run_receipt(self, run: AgentRun) -> AgentRunReceipt:
        return AgentRunReceipt(
            run_id=run.id,
            thread_id=run.thread_id,
            trigger_message_id=run.trigger_message_id,
            status=run.status,
            events_url=(
                f"{self._settings.api_prefix}/projects/{run.project_id}/agent/runs/{run.id}/events"
            ),
        )

    def _run_view(self, run: AgentRun) -> AgentRunView:
        receipt = self._run_receipt(run)
        return AgentRunView(
            **receipt.model_dump(),
            attempt_count=run.attempt_count,
            max_attempts=run.max_attempts,
            provider=run.provider,
            model_id=run.model_id,
            error=run.error,
            final_message_id=run.final_message_id,
            created_at=run.created_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )


class AgentRunIdentityResolver:
    """Worker 按 Run 保存的 Session ID 重建身份，并实时检查会话与成员关系。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def resolve(self, session: Session, run: AgentRun) -> RequestIdentity:
        now = datetime.now(UTC)
        web_session = session.get(WebSession, run.auth_session_id)
        if (
            web_session is None
            or web_session.user_id != run.created_by
            or web_session.team_id != run.team_id
            or web_session.revoked_at is not None
            or _aware(web_session.absolute_expires_at) <= now
            or _aware(web_session.last_seen_at)
            + timedelta(seconds=self._settings.web_session_idle_seconds)
            <= now
        ):
            raise AuthenticationError("发起 Agent Run 的 Web Session 已失效")
        membership = session.get(TeamMember, (run.team_id, run.created_by))
        if membership is None:
            raise AuthorizationError("发起 Agent Run 的团队成员关系已失效")
        scopes = OWNER_WEB_SCOPES if membership.role is TeamRole.OWNER else RESEARCHER_WEB_SCOPES
        return RequestIdentity(
            user_id=run.created_by,
            team_id=run.team_id,
            token_id=web_session.id,
            scopes=scopes,
            authentication_method="WEB_SESSION",
            recent_authentication=False,
        )
