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
    AgentModelCallView,
    AgentRunReceipt,
    AgentRunView,
    AgentThreadCreateRequest,
    AgentThreadPage,
    AgentThreadSummary,
    AgentThreadUpdateRequest,
    AgentThreadView,
    ExternalAgentQuestionRequest,
    ExternalAgentTaskContextView,
    ExternalAgentTaskPollResult,
    ExternalAgentTaskStartRequest,
    ExternalAgentTaskStartResult,
)
from experiment_guardian.domain.enums import (
    AgentContextSummaryStatus,
    AgentEvidenceKind,
    AgentMessageRole,
    AgentRunAuthMethod,
    AgentRunStatus,
    AgentThreadOrigin,
    AgentThreadStatus,
    TeamRole,
    TokenAudience,
)
from experiment_guardian.infrastructure.models import (
    AccessToken,
    AgentCitation,
    AgentContextSummary,
    AgentMessage,
    AgentModelCall,
    AgentResearchReport,
    AgentRun,
    AgentThread,
    AuditLog,
    McpOAuthClient,
    McpOAuthGrant,
    TeamMember,
    WebSession,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyAgentRepository,
    SqlAlchemyProjectRepository,
)

PROMPT_VERSION = "r15e-b-v1"
TOOL_CATALOG_VERSION = "r15e-b-v1"
EXTERNAL_PROMPT_VERSION = "r17a-external-v2"
EXTERNAL_TOOL_CATALOG_VERSION = "r17a-external-v2"
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
                origin=AgentThreadOrigin.WEB,
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

    def start_external_task(
        self,
        *,
        project_id: UUID,
        identity: RequestIdentity,
        idempotency_key: UUID,
        request: ExternalAgentTaskStartRequest,
    ) -> ExternalAgentTaskStartResult:
        """创建一个 MCP 任务，并在模型运行前冻结当前正式策略快照。"""

        self._require_enabled()
        self._require_external_identity(identity, project_id)
        request_hash = _hash_json(
            {"task_description": request.task_description, "title": request.title}
        )

        def operation() -> ExternalAgentTaskStartResult:
            with self._session_factory() as session, session.begin():
                project = self._projects.require_project_member(
                    session,
                    project_id=project_id,
                    user_id=identity.user_id,
                    team_id=identity.team_id,
                )
                existing = session.scalar(
                    select(AgentThread).where(
                        AgentThread.project_id == project_id,
                        AgentThread.created_by == identity.user_id,
                        AgentThread.origin == AgentThreadOrigin.EXTERNAL_MCP,
                        AgentThread.start_idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    if existing.start_request_hash != request_hash:
                        raise ConflictError("相同 Idempotency-Key 已用于不同的外部 Agent 任务")
                    return self._external_start_result(session, existing)

                active = session.scalar(
                    select(AgentRun.id)
                    .join(AgentThread, AgentThread.id == AgentRun.thread_id)
                    .where(
                        AgentRun.project_id == project_id,
                        AgentRun.created_by == identity.user_id,
                        AgentRun.status.in_(ACTIVE_RUN_STATUSES),
                        AgentThread.origin == AgentThreadOrigin.EXTERNAL_MCP,
                    )
                    .limit(1)
                )
                if active is not None:
                    raise ConflictError("当前项目已有运行中的外部 Agent 请求")

                bundle = self._projects.load_context_bundle(
                    session,
                    project_id=project_id,
                    user_id=identity.user_id,
                    team_id=identity.team_id,
                )
                formal_source = bundle.model_dump(mode="json", exclude={"human_readable"})
                source_hash = _hash_json(formal_source)
                context = ExternalAgentTaskContextView(
                    captured_at=datetime.now(UTC),
                    source_hash=source_hash,
                    policy=bundle,
                    governance_notice=(
                        "该快照只包含启动时的正式策略。执行和治理判断必须使用结构化字段；"
                        "内部 Agent 的历史关联和建议属于分析，不是新的正式事实。"
                    ),
                    context_freshness="CURRENT",
                    current_context_id=bundle.context.context_id,
                    current_context_version=bundle.context.version,
                    current_intent_id=(
                        bundle.active_intent.intent_id if bundle.active_intent else None
                    ),
                    current_intent_version=(
                        bundle.active_intent.version if bundle.active_intent else None
                    ),
                )
                thread = AgentThread(
                    team_id=project.team_id,
                    project_id=project.id,
                    created_by=identity.user_id,
                    origin=AgentThreadOrigin.EXTERNAL_MCP,
                    title=request.title or request.task_description[:40],
                    status=AgentThreadStatus.ACTIVE,
                    last_sequence=1,
                    start_idempotency_key=idempotency_key,
                    start_request_hash=request_hash,
                    task_context_snapshot=context.model_dump(mode="json"),
                    task_context_hash=source_hash,
                )
                session.add(thread)
                session.flush()
                message = AgentMessage(
                    thread_id=thread.id,
                    sequence=1,
                    role=AgentMessageRole.USER,
                    content=request.task_description,
                    content_sha256=_content_hash(request.task_description),
                    created_by=identity.user_id,
                )
                session.add(message)
                session.flush()
                run = AgentRun(
                    thread_id=thread.id,
                    team_id=thread.team_id,
                    project_id=thread.project_id,
                    created_by=identity.user_id,
                    **self._auth_binding(identity),
                    trigger_message_id=message.id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    status=AgentRunStatus.PENDING,
                    provider=self._settings.agent_provider,
                    model_id=self._settings.agent_model_id,
                    prompt_version=EXTERNAL_PROMPT_VERSION,
                    tool_catalog_version=EXTERNAL_TOOL_CATALOG_VERSION,
                    context_snapshot={
                        "external_task": {
                            "task_context_hash": source_hash,
                            "context_id": str(bundle.context.context_id),
                            "context_version": bundle.context.version,
                            "intent_id": (
                                str(bundle.active_intent.intent_id)
                                if bundle.active_intent
                                else None
                            ),
                            "intent_version": (
                                bundle.active_intent.version if bundle.active_intent else None
                            ),
                        }
                    },
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
                    payload={"status": run.status.value, "origin": thread.origin.value},
                )
                session.add(
                    AuditLog(
                        team_id=thread.team_id,
                        project_id=thread.project_id,
                        actor_type="USER",
                        actor_id=identity.user_id,
                        action="agent.external_task.started",
                        target_type="AGENT_THREAD",
                        target_id=thread.id,
                        before_value=None,
                        after_value={
                            **self._auth_audit(identity),
                            "run_id": str(run.id),
                            "context_hash": source_hash,
                            "context_version": bundle.context.version,
                            "intent_version": (
                                bundle.active_intent.version if bundle.active_intent else None
                            ),
                        },
                    )
                )
                return ExternalAgentTaskStartResult(
                    task_id=thread.id,
                    thread_id=thread.id,
                    task_status=thread.status,
                    initial_context=context,
                    run=self._run_receipt(run),
                    poll_after_seconds=self._settings.agent_run_poll_interval_seconds,
                )

        return run_with_serialization_retry(operation)

    def ask_external_task(
        self,
        *,
        task_id: UUID,
        identity: RequestIdentity,
        idempotency_key: UUID,
        request: ExternalAgentQuestionRequest,
    ) -> AgentRunReceipt:
        self._require_external_identity(identity, identity.project_id)
        project_id = identity.project_id
        if project_id is None:  # 帮助静态类型检查器保留已验证事实。
            raise AuthorizationError("MCP 身份未绑定项目")
        return self.create_message(
            project_id=project_id,
            thread_id=task_id,
            identity=identity,
            idempotency_key=idempotency_key,
            request=AgentMessageCreateRequest(content=request.question),
        )

    def get_external_task(
        self,
        *,
        task_id: UUID,
        identity: RequestIdentity,
        after_sequence: int,
        limit: int,
    ) -> ExternalAgentTaskPollResult:
        self._require_enabled()
        self._require_external_identity(identity, identity.project_id)
        project_id = identity.project_id
        if project_id is None:
            raise AuthorizationError("MCP 身份未绑定项目")
        thread_view = self.get_thread(
            project_id=project_id,
            thread_id=task_id,
            identity=identity,
        )
        messages = [
            item for item in thread_view.messages if item.sequence > max(0, after_sequence)
        ][:limit]
        next_sequence = messages[-1].sequence if messages else max(0, after_sequence)

        with self._session_factory() as session:
            thread = self._require_owned_thread(
                session,
                project_id=project_id,
                thread_id=task_id,
                identity=identity,
            )
            self._require_external_thread_for_mcp(thread, identity)
            initial_context = ExternalAgentTaskContextView.model_validate(
                thread.task_context_snapshot
            )
            bundle = self._projects.load_context_bundle(
                session,
                project_id=project_id,
                user_id=identity.user_id,
                team_id=identity.team_id,
            )
            current_hash = _hash_json(
                bundle.model_dump(mode="json", exclude={"human_readable"})
            )
            freshness = "CURRENT" if current_hash == thread.task_context_hash else "STALE"
            warning = (
                None
                if freshness == "CURRENT"
                else (
                    "任务启动后的正式 Context、Intent 或 Constraints 已变化；"
                    "初始快照仅用于追溯，请重新调用 project_get_context。"
                )
            )
            initial_context = initial_context.model_copy(
                update={
                    "context_freshness": freshness,
                    "current_context_id": bundle.context.context_id,
                    "current_context_version": bundle.context.version,
                    "current_intent_id": (
                        bundle.active_intent.intent_id if bundle.active_intent else None
                    ),
                    "current_intent_version": (
                        bundle.active_intent.version if bundle.active_intent else None
                    ),
                    "warning": warning,
                }
            )
            latest_run = session.scalar(
                select(AgentRun)
                .where(AgentRun.thread_id == thread.id)
                .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
                .limit(1)
            )
            latest_run_view: AgentRunView | None = None
            if latest_run is not None:
                calls = list(
                    session.scalars(
                        select(AgentModelCall)
                        .where(AgentModelCall.run_id == latest_run.id)
                        .order_by(
                            AgentModelCall.generation.desc(),
                            AgentModelCall.ordinal.desc(),
                        )
                        .limit(50)
                    ).all()
                )
                calls.reverse()
                latest_run_view = self._run_view(latest_run, calls)
            return ExternalAgentTaskPollResult(
                task=self._thread_summary(thread),
                initial_context=initial_context,
                context_freshness=freshness,
                current_context_id=bundle.context.context_id,
                current_context_version=bundle.context.version,
                current_intent_id=(
                    bundle.active_intent.intent_id if bundle.active_intent else None
                ),
                current_intent_version=(
                    bundle.active_intent.version if bundle.active_intent else None
                ),
                warning=warning,
                messages=messages,
                latest_run=latest_run_view,
                next_sequence=next_sequence,
            )

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
        self._require_conversation_identity(identity, project_id)
        with self._session_factory() as session:
            thread = self._require_owned_thread(
                session, project_id=project_id, thread_id=thread_id, identity=identity
            )
            self._require_external_thread_for_mcp(thread, identity)
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
            reports = (
                {
                    item.final_message_id: item.id
                    for item in session.scalars(
                        select(AgentResearchReport).where(
                            AgentResearchReport.final_message_id.in_(
                                [item.id for item in messages]
                            )
                        )
                    ).all()
                }
                if messages
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
            external_context: ExternalAgentTaskContextView | None = None
            if thread.task_context_snapshot is not None:
                bundle = self._projects.load_context_bundle(
                    session,
                    project_id=project_id,
                    user_id=identity.user_id,
                    team_id=identity.team_id,
                )
                current_hash = _hash_json(
                    bundle.model_dump(mode="json", exclude={"human_readable"})
                )
                freshness = (
                    "CURRENT" if current_hash == thread.task_context_hash else "STALE"
                )
                external_context = ExternalAgentTaskContextView.model_validate(
                    thread.task_context_snapshot
                ).model_copy(
                    update={
                        "context_freshness": freshness,
                        "current_context_id": bundle.context.context_id,
                        "current_context_version": bundle.context.version,
                        "current_intent_id": (
                            bundle.active_intent.intent_id if bundle.active_intent else None
                        ),
                        "current_intent_version": (
                            bundle.active_intent.version if bundle.active_intent else None
                        ),
                        "warning": (
                            None
                            if freshness == "CURRENT"
                            else "正式策略已变化；该任务的初始上下文仅用于历史追溯。"
                        ),
                    }
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
                        research_report_id=reports.get(item.id),
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
                external_task_context=external_context,
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
        self._require_conversation_identity(identity, project_id)
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
                self._require_external_thread_for_mcp(thread, identity)
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
                    **self._auth_binding(identity),
                    trigger_message_id=message.id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    status=AgentRunStatus.PENDING,
                    provider=self._settings.agent_provider,
                    model_id=self._settings.agent_model_id,
                    prompt_version=(
                        EXTERNAL_PROMPT_VERSION
                        if thread.origin is AgentThreadOrigin.EXTERNAL_MCP
                        else PROMPT_VERSION
                    ),
                    tool_catalog_version=(
                        EXTERNAL_TOOL_CATALOG_VERSION
                        if thread.origin is AgentThreadOrigin.EXTERNAL_MCP
                        else TOOL_CATALOG_VERSION
                    ),
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
                        action=(
                            "agent.external_message.requested"
                            if thread.origin is AgentThreadOrigin.EXTERNAL_MCP
                            else "agent.run.requested"
                        ),
                        target_type="AGENT_RUN",
                        target_id=run.id,
                        before_value=None,
                        after_value={
                            "thread_id": str(thread.id),
                            "message_id": str(message.id),
                            **self._auth_audit(identity),
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
            calls = list(
                session.scalars(
                    select(AgentModelCall)
                    .where(AgentModelCall.run_id == run.id)
                    .order_by(
                        AgentModelCall.generation.desc(),
                        AgentModelCall.ordinal.desc(),
                    )
                    .limit(50)
                ).all()
            )
            calls.reverse()
            return self._run_view(run, calls)

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
                    **self._auth_binding(identity),
                    trigger_message_id=old.trigger_message_id,
                    idempotency_key=idempotency_key,
                    request_hash=retry_hash,
                    status=AgentRunStatus.PENDING,
                    run_kind=old.run_kind,
                    target_experiment_plan_revision_id=(
                        old.target_experiment_plan_revision_id
                    ),
                    provider=self._settings.agent_provider,
                    model_id=self._settings.agent_model_id,
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
                            **self._auth_audit(identity),
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

    def _require_conversation_identity(
        self, identity: RequestIdentity, project_id: UUID
    ) -> None:
        if identity.authentication_method == "WEB_SESSION":
            return
        self._require_external_identity(identity, project_id)

    @staticmethod
    def _require_external_identity(
        identity: RequestIdentity, project_id: UUID | None
    ) -> None:
        if identity.authentication_method not in {"MCP_TOKEN", "MCP_OAUTH"}:
            raise AuthorizationError("外部 Agent 协作只接受 MCP Token 或 MCP OAuth")
        if project_id is None or identity.project_id != project_id:
            raise AuthorizationError("MCP 身份未绑定当前项目")
        required = {"project:read", "experiment:query"}
        if not required.issubset(identity.scopes):
            raise AuthorizationError("MCP Token 缺少外部 Agent 协作所需读取权限")
        if (
            identity.credential_expires_at is not None
            and _aware(identity.credential_expires_at) <= datetime.now(UTC)
        ):
            raise AuthenticationError("MCP 凭据已过期")

    @staticmethod
    def _require_external_thread_for_mcp(
        thread: AgentThread, identity: RequestIdentity
    ) -> None:
        if (
            identity.authentication_method in {"MCP_TOKEN", "MCP_OAUTH"}
            and thread.origin is not AgentThreadOrigin.EXTERNAL_MCP
        ):
            raise ResourceNotFoundError("项目中不存在该外部 Agent 任务")

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
            origin=thread.origin,
            status=thread.status,
            created_at=thread.created_at,
            updated_at=thread.updated_at,
            archived_at=thread.archived_at,
        )

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
                raise AuthenticationError("MCP OAuth 身份缺少访问令牌过期时间")
            return {
                **common,
                "auth_method": AgentRunAuthMethod.MCP_OAUTH,
                "auth_session_id": None,
                "auth_access_token_id": None,
                "auth_oauth_grant_id": identity.token_id,
            }
        raise AuthorizationError("当前认证方式不能创建治理 Agent Run")

    @staticmethod
    def _auth_audit(identity: RequestIdentity) -> dict[str, object]:
        key = "session_id" if identity.authentication_method == "WEB_SESSION" else "credential_id"
        return {
            "authentication_method": identity.authentication_method,
            key: str(identity.token_id),
            "client_id": identity.client_id,
        }

    def _external_start_result(
        self, session: Session, thread: AgentThread
    ) -> ExternalAgentTaskStartResult:
        run = session.scalar(
            select(AgentRun)
            .where(AgentRun.thread_id == thread.id)
            .order_by(AgentRun.created_at, AgentRun.id)
            .limit(1)
        )
        if run is None or thread.task_context_snapshot is None:
            raise ServiceUnavailableError("外部 Agent 任务的初始运行或上下文不存在")
        return ExternalAgentTaskStartResult(
            task_id=thread.id,
            thread_id=thread.id,
            task_status=thread.status,
            initial_context=ExternalAgentTaskContextView.model_validate(
                thread.task_context_snapshot
            ),
            run=self._run_receipt(run),
            poll_after_seconds=self._settings.agent_run_poll_interval_seconds,
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

    def _run_view(
        self,
        run: AgentRun,
        calls: list[AgentModelCall],
    ) -> AgentRunView:
        receipt = self._run_receipt(run)
        return AgentRunView(
            **receipt.model_dump(),
            attempt_count=run.attempt_count,
            max_attempts=run.max_attempts,
            provider=run.provider,
            model_id=run.model_id,
            usage=run.usage,
            model_calls=[self._model_call_view(item) for item in calls],
            error=run.error,
            final_message_id=run.final_message_id,
            created_at=run.created_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )

    @staticmethod
    def _model_call_view(call: AgentModelCall) -> AgentModelCallView:
        input_tokens = call.usage.get("input_tokens")
        output_tokens = call.usage.get("output_tokens")
        return AgentModelCallView(
            call_id=call.id,
            generation=call.generation,
            ordinal=call.ordinal,
            purpose=call.purpose,
            status=call.status,
            provider=call.provider,
            model_id=call.model_id,
            input_tokens=(
                input_tokens
                if isinstance(input_tokens, int) and not isinstance(input_tokens, bool)
                else None
            ),
            output_tokens=(
                output_tokens
                if isinstance(output_tokens, int) and not isinstance(output_tokens, bool)
                else None
            ),
            latency_ms=call.latency_ms,
            estimated_cost=call.estimated_cost,
            cost_currency=call.cost_currency,
            finish_reason=call.finish_reason,
            error_code=(
                str(call.error["code"])
                if call.error is not None and "code" in call.error
                else None
            ),
            started_at=call.started_at,
            completed_at=call.completed_at,
        )


class AgentRunIdentityResolver:
    """Worker 从持久化凭据重建身份，并实时检查撤销、项目和成员关系。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def resolve(self, session: Session, run: AgentRun) -> RequestIdentity:
        now = datetime.now(UTC)
        membership = session.get(TeamMember, (run.team_id, run.created_by))
        if membership is None:
            raise AuthorizationError("发起 Agent Run 的团队成员关系已失效")

        if run.auth_method is AgentRunAuthMethod.WEB_SESSION:
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
            role_scopes = (
                OWNER_WEB_SCOPES
                if membership.role is TeamRole.OWNER
                else RESEARCHER_WEB_SCOPES
            )
            snapshot = set(run.auth_scopes_snapshot or role_scopes)
            return RequestIdentity(
                user_id=run.created_by,
                team_id=run.team_id,
                token_id=web_session.id,
                scopes=frozenset(role_scopes & snapshot),
                authentication_method="WEB_SESSION",
                recent_authentication=False,
            )

        if run.auth_method is AgentRunAuthMethod.MCP_TOKEN:
            token = session.get(AccessToken, run.auth_access_token_id)
            if (
                token is None
                or token.user_id != run.created_by
                or token.team_id != run.team_id
                or token.project_id != run.project_id
                or token.audience is not TokenAudience.MCP
                or token.revoked_at is not None
                or _aware(token.expires_at) <= now
            ):
                raise AuthenticationError("发起 Agent Run 的 MCP Token 已失效")
            scopes = set(token.scopes) & set(run.auth_scopes_snapshot)
            self._require_external_scopes(scopes)
            return RequestIdentity(
                user_id=run.created_by,
                team_id=run.team_id,
                token_id=token.id,
                project_id=run.project_id,
                scopes=self._agent_effective_scopes(scopes),
                authentication_method="MCP_TOKEN",
                recent_authentication=False,
                credential_expires_at=_aware(token.expires_at),
            )

        if run.auth_method is AgentRunAuthMethod.MCP_OAUTH:
            grant = session.get(McpOAuthGrant, run.auth_oauth_grant_id)
            client = (
                session.get(McpOAuthClient, grant.mcp_oauth_client_id)
                if grant is not None
                else None
            )
            if (
                grant is None
                or client is None
                or grant.user_id != run.created_by
                or client.team_id != run.team_id
                or client.project_id != run.project_id
                or grant.revoked_at is not None
                or client.revoked_at is not None
                or run.auth_expires_at is None
                or _aware(run.auth_expires_at) <= now
            ):
                raise AuthenticationError("发起 Agent Run 的 MCP OAuth 授权已失效")
            scopes = (
                set(run.auth_scopes_snapshot)
                & set(grant.granted_scopes)
                & set(client.allowed_scopes)
            )
            self._require_external_scopes(scopes)
            return RequestIdentity(
                user_id=run.created_by,
                team_id=run.team_id,
                token_id=grant.id,
                project_id=run.project_id,
                scopes=self._agent_effective_scopes(scopes),
                authentication_method="MCP_OAUTH",
                recent_authentication=False,
                client_id=client.cognito_client_id,
                credential_expires_at=_aware(run.auth_expires_at),
            )

        raise AuthenticationError("Agent Run 的认证方式无效")

    @staticmethod
    def _require_external_scopes(scopes: set[str]) -> None:
        if not {"project:read", "experiment:query"}.issubset(scopes):
            raise AuthorizationError("外部 Agent Run 的读取权限已失效")

    @staticmethod
    def _agent_effective_scopes(scopes: set[str]) -> frozenset[str]:
        effective = set(scopes)
        if "experiment:query" in scopes:
            # 只映射内部 Agent 既有的只读命名，不产生计划、提交或正式写权限。
            effective.add("experiment:read")
        return frozenset(effective)
