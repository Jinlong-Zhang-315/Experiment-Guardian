"""R15a 治理 Agent 的持久化、租约和证据闭环测试。"""

import json
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.agent import (
    AgentConversationService,
    AgentRunIdentityResolver,
)
from experiment_guardian.application.agent_observability import AgentObservabilityService
from experiment_guardian.application.agent_runtime import (
    AgentRunProcessor,
    GovernanceAgentRuntime,
)
from experiment_guardian.application.agent_tools import AgentToolRegistry
from experiment_guardian.application.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    InputValidationError,
    ServiceUnavailableError,
)
from experiment_guardian.application.experiment_plans import ExperimentPlanService
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.ports import AgentChatModel
from experiment_guardian.application.research_reports import ResearchReportService
from experiment_guardian.application.services import ProjectAdministrationService
from experiment_guardian.application.web_auth import OWNER_WEB_SCOPES
from experiment_guardian.core.config import Settings
from experiment_guardian.domain.agent import (
    AgentChatMessage,
    AgentEvidence,
    AgentMessageCreateRequest,
    AgentModelEvent,
    AgentModelUsage,
    AgentResponseFormat,
    AgentThreadCreateRequest,
    AgentToolRequest,
    AgentToolResult,
    AgentToolSpec,
    ExternalAgentQuestionRequest,
    ExternalAgentTaskStartRequest,
)
from experiment_guardian.domain.agent_research import research_report_source_hash
from experiment_guardian.domain.contracts import ConfigurationDocument
from experiment_guardian.domain.enums import (
    AgentContextSummaryStatus,
    AgentEvidenceKind,
    AgentModelCallPurpose,
    AgentRunAuthMethod,
    AgentRunStatus,
    AgentThreadOrigin,
    ExperimentPlanDecisionType,
    ExperimentPlanStatus,
    TeamRole,
    TokenAudience,
)
from experiment_guardian.domain.experiment_plan import (
    ExperimentPlanDecisionRequest,
    ExperimentPlanEvidence,
    ExperimentPlanSubmitRequest,
)
from experiment_guardian.infrastructure.models import (
    AccessToken,
    AgentCitation,
    AgentContextSummary,
    AgentMessage,
    AgentModelCall,
    AgentResearchReport,
    AgentRun,
    AgentRunEvent,
    AuditLog,
    ExperimentPlan,
    ExperimentPlanDecision,
    ExperimentPlanReview,
    ExperimentPlanRevision,
    McpOAuthClient,
    McpOAuthGrant,
    ProjectContext,
    TeamMember,
    User,
    WebSession,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyAgentRepository,
    SqlAlchemyProjectRepository,
)
from tests.integration.test_foundation_slice import initial_request, seed_owner


class ScriptedAgentModel(AgentChatModel):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def provider(self) -> str:
        return "bailian"

    @property
    def model_id(self) -> str:
        return "qwen-agent"

    @property
    def structured_final_requires_tool_choice_none(self) -> bool:
        return False

    def stream_turn(
        self,
        *,
        messages: Sequence[AgentChatMessage],
        tools: Sequence[AgentToolSpec],
        tool_choice: str,
        max_output_tokens: int,
        response_format: AgentResponseFormat | None = None,
    ) -> Iterator[AgentModelEvent]:
        del tools, max_output_tokens, response_format
        self.calls += 1
        if self.calls == 1:
            assert tool_choice == "auto"
            yield AgentModelEvent(
                event_type="tool_call",
                tool_call=AgentToolRequest(
                    call_id="call-status",
                    name="project_status_get_v1",
                    arguments={},
                ),
            )
            yield AgentModelEvent(event_type="completed", finish_reason="tool_calls")
            return
        assert any(item.role == "tool" for item in messages)
        answer = {
            "answer_markdown": "当前项目正式目标已读取。",
            "sections": [
                {
                    "evidence_kind": "CONFIRMED_FACT",
                    "title": "项目目标",
                    "content": "以当前正式 Context 为准。",
                    "citation_ids": ["ev_1_1"],
                }
            ],
            "citations": ["ev_1_1"],
            "follow_up_required": False,
        }
        yield AgentModelEvent(event_type="text_delta", text=json.dumps(answer))
        yield AgentModelEvent(
            event_type="usage",
            usage=AgentModelUsage(input_tokens=20, output_tokens=10),
        )
        yield AgentModelEvent(
            event_type="completed",
            finish_reason="stop",
            provider_request_id="scripted-2",
        )


class SeparateFinalTurnAgentModel(AgentChatModel):
    """模拟必须把 auto 工具选择与严格 JSON 最终回答拆开的 Provider。"""

    provider = "bailian"
    model_id = "qwen-agent"
    structured_final_requires_tool_choice_none = True

    def __init__(self) -> None:
        self.calls = 0

    def stream_turn(
        self,
        *,
        messages: Sequence[AgentChatMessage],
        tools: Sequence[AgentToolSpec],
        tool_choice: str,
        max_output_tokens: int,
        response_format: AgentResponseFormat | None = None,
    ) -> Iterator[AgentModelEvent]:
        del tools, max_output_tokens
        self.calls += 1
        if self.calls == 1:
            assert tool_choice == "auto"
            yield AgentModelEvent(
                event_type="tool_call",
                tool_call=AgentToolRequest(
                    call_id="call-status",
                    name="project_status_get_v1",
                    arguments={},
                ),
            )
            yield AgentModelEvent(event_type="completed", finish_reason="tool_calls")
            return
        if self.calls == 2:
            assert tool_choice == "auto"
            yield AgentModelEvent(event_type="text_delta", text="非结构化中间草稿")
            yield AgentModelEvent(event_type="completed", finish_reason="stop")
            return
        assert tool_choice == "none"
        assert response_format is not None
        assert "citations 必须是 evidence_id 字符串数组" in messages[-1].content
        answer = {
            "answer_markdown": "当前项目正式目标已读取。",
            "sections": [
                {
                    "evidence_kind": "CONFIRMED_FACT",
                    "title": "项目目标",
                    "content": "以当前正式 Context 为准。",
                    "citation_ids": ["ev_1_1"],
                }
            ],
            "citations": ["ev_1_1"],
            "follow_up_required": False,
        }
        yield AgentModelEvent(event_type="text_delta", text=json.dumps(answer))
        yield AgentModelEvent(event_type="completed", finish_reason="stop")


class ScriptedExperimentPlanModel(AgentChatModel):
    provider = "bailian"
    model_id = "qwen-agent"
    structured_final_requires_tool_choice_none = False

    def __init__(self) -> None:
        self.calls = 0

    def stream_turn(
        self,
        *,
        messages: Sequence[AgentChatMessage],
        tools: Sequence[AgentToolSpec],
        tool_choice: str,
        max_output_tokens: int,
        response_format: AgentResponseFormat | None = None,
    ) -> Iterator[AgentModelEvent]:
        del tools, tool_choice, max_output_tokens, response_format
        self.calls += 1
        tool_messages = [item for item in messages if item.role == "tool"]
        if not tool_messages:
            yield AgentModelEvent(
                event_type="tool_call",
                tool_call=AgentToolRequest(
                    call_id=f"call-plan-status-{self.calls}",
                    name="project_status_get_v1",
                    arguments={},
                ),
            )
            yield AgentModelEvent(event_type="completed", finish_reason="tool_calls")
            return
        raw_plan_input = next(
            item.content for item in messages if "automatic_revision_round" in item.content
        )
        automatic_round = int(
            json.loads(raw_plan_input.split("\n", 1)[1])["automatic_revision_round"]
        )
        recommendation = "REVISE" if automatic_round == 0 else "READY"
        review = {
            "schema_version": 1,
            "recommendation": recommendation,
            "review_markdown": "计划与当前正式主线一致。",
            "findings": [
                {
                    "kind": "LOW_COST_VALIDATION",
                    "severity": "MEDIUM",
                    "statement": "先执行低成本冒烟验证。",
                    "rationale": "降低完整训练前的失败成本。",
                    "auto_fixable": automatic_round == 0,
                    "citation_ids": ["ev_1_1"],
                }
            ],
            "candidate_invariants": (
                []
                if automatic_round == 0
                else [
                    {
                        "statement": "保持正式协议不变。",
                        "rationale": "确保结果可比。",
                        "verification_method": "运行前执行正式 Plan Check。",
                        "representation": "STRUCTURED_PARAMETER",
                        "parameter_path": "dataset.protocol",
                        "expected_value": "40/20",
                        "citation_ids": ["ev_1_1"],
                    }
                ]
            ),
            "free_exploration": ["融合模块内部实现"],
            "user_decisions": [],
            "revised_plan_markdown": (
                "## 实验目标\n验证融合系数。\n\n## 低成本验证\n先运行单批次冒烟测试。"
                if automatic_round == 0
                else None
            ),
            "citations": ["ev_1_1"],
        }
        answer = {
            "answer_markdown": "实验计划审核已完成。",
            "sections": [
                {
                    "evidence_kind": "CONFIRMED_FACT",
                    "title": "正式策略",
                    "content": "审核使用当前 Context 和 Intent。",
                    "citation_ids": ["ev_1_1"],
                }
            ],
            "citations": ["ev_1_1"],
            "follow_up_required": False,
            "experiment_plan_review": review,
        }
        yield AgentModelEvent(event_type="text_delta", text=json.dumps(answer))
        yield AgentModelEvent(event_type="completed", finish_reason="stop")


class SummaryAwareAgentModel(AgentChatModel):
    def __init__(self, *, fail_summary: bool = False) -> None:
        self.fail_summary = fail_summary

    @property
    def provider(self) -> str:
        return "bailian"

    @property
    def model_id(self) -> str:
        return "qwen-agent"

    @property
    def structured_final_requires_tool_choice_none(self) -> bool:
        return False

    def stream_turn(
        self,
        *,
        messages: Sequence[AgentChatMessage],
        tools: Sequence[AgentToolSpec],
        tool_choice: str,
        max_output_tokens: int,
        response_format: AgentResponseFormat | None = None,
    ) -> Iterator[AgentModelEvent]:
        del tools, tool_choice, max_output_tokens, response_format
        if messages[0].content.startswith("你负责压缩"):
            if self.fail_summary:
                raise ServiceUnavailableError("summary provider unavailable")
            source = json.loads(messages[1].content.split("\n", 1)[1])
            schema_version = (
                7
                if "schema_version=7" in messages[1].content
                else 6
                if "schema_version=6" in messages[1].content
                else 5
                if "schema_version=5" in messages[1].content
                else 4
                if "schema_version=4" in messages[1].content
                else 3
                if "schema_version=3" in messages[1].content
                else 2
                if "schema_version=2" in messages[1].content
                else 1
            )
            payload = {
                "schema_version": schema_version,
                "covered_sequence_from": source["covered_sequence_from"],
                "covered_sequence_to": source["covered_sequence_to"],
                "user_requests_and_context": ["用户持续查询项目状态"],
                "prior_answers_and_analysis": ["此前回答为非权威对话历史"],
                "open_questions": [],
                "source_message_ids": source["source_message_ids"],
                "formal_reference_labels": [],
            }
            if schema_version >= 2:
                payload["draft_references"] = []
            if schema_version >= 3:
                payload["proposal_references"] = []
            if schema_version >= 6:
                payload["research_report_references"] = []
            if schema_version >= 7:
                payload["research_memory_references"] = []
            yield AgentModelEvent(event_type="text_delta", text=json.dumps(payload))
            yield AgentModelEvent(event_type="completed", finish_reason="stop")
            return
        answer = {
            "answer_markdown": "已收到本轮问题。",
            "sections": [
                {
                    "evidence_kind": "USER_PROVIDED",
                    "title": "本轮输入",
                    "content": "已收到本轮问题。",
                    "citation_ids": [],
                }
            ],
            "citations": [],
            "follow_up_required": False,
        }
        yield AgentModelEvent(event_type="text_delta", text=json.dumps(answer))
        yield AgentModelEvent(event_type="completed", finish_reason="stop")


class CatalogInspectingModel(SummaryAwareAgentModel):
    def stream_turn(
        self,
        *,
        messages: Sequence[AgentChatMessage],
        tools: Sequence[AgentToolSpec],
        tool_choice: str,
        max_output_tokens: int,
        response_format: AgentResponseFormat | None = None,
    ) -> Iterator[AgentModelEvent]:
        assert {item.name for item in tools} == {
            "project_status_get_v1",
            "experiments_list_v1",
            "experiment_get_v1",
            "pending_work_list_v1",
        }
        yield from super().stream_turn(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            max_output_tokens=max_output_tokens,
            response_format=response_format,
        )


class ResearchReportAgentModel(AgentChatModel):
    provider = "bailian"
    model_id = "qwen-agent"
    structured_final_requires_tool_choice_none = False

    def __init__(self, experiment_ids: list[str]) -> None:
        self.experiment_ids = experiment_ids
        self.calls = 0

    def stream_turn(
        self,
        *,
        messages: Sequence[AgentChatMessage],
        tools: Sequence[AgentToolSpec],
        tool_choice: str,
        max_output_tokens: int,
        response_format: AgentResponseFormat | None = None,
    ) -> Iterator[AgentModelEvent]:
        del tools, tool_choice, max_output_tokens, response_format
        self.calls += 1
        if self.calls == 1:
            yield AgentModelEvent(
                event_type="tool_call",
                tool_call=AgentToolRequest(
                    call_id="call-report",
                    name="research_report_prepare_v1",
                    arguments={
                        "experiment_ids": self.experiment_ids,
                        "objective": "总结两次显式实验",
                        "include_historical": False,
                    },
                ),
            )
            yield AgentModelEvent(event_type="completed", finish_reason="tool_calls")
            return
        assert any(item.role == "tool" for item in messages)
        answer = {
            "answer_markdown": "候选研究报告已生成，不属于正式事实。",
            "sections": [
                {
                    "evidence_kind": "ANALYSIS",
                    "title": "候选分析",
                    "content": "两个实验的确定性比较已完成。",
                    "citation_ids": ["ev_1_3"],
                }
            ],
            "citations": ["ev_1_3"],
            "follow_up_required": False,
            "research_report": {
                "schema_version": 1,
                "source_hash": json.loads(
                    next(item.content for item in reversed(messages) if item.role == "tool")
                )["content"]["source_hash"],
                "title": "两次实验阶段总结",
                "executive_summary": "两次实验均已纳入候选分析。",
                "executive_summary_citation_ids": ["ev_1_1", "ev_1_2", "ev_1_3"],
                "findings": [
                    {
                        "finding_id": "F001",
                        "kind": "SUPPORTED_CONCLUSION",
                        "statement": "两次实验的正式状态均已读取。",
                        "rationale": "该结论仅复述来源快照和确定性比较。",
                        "citation_ids": ["ev_1_1", "ev_1_2", "ev_1_3"],
                        "limitations": ["不构成因果结论"],
                    }
                ],
                "limitations": [],
                "selected_experiment_ids": self.experiment_ids,
            },
        }
        yield AgentModelEvent(event_type="text_delta", text=json.dumps(answer))
        yield AgentModelEvent(event_type="completed", finish_reason="stop")


class ResearchReportToolRegistry:
    def __init__(self, experiment_ids: list[str]) -> None:
        self.experiment_ids = experiment_ids

    def specs_for_version(self, catalog_version: str) -> list[AgentToolSpec]:
        assert catalog_version == "r15e-b-v1"
        return [
            AgentToolSpec(
                name="research_report_prepare_v1",
                version="1",
                description="测试报告来源",
                input_schema={"type": "object"},
            )
        ]

    def execute(self, **kwargs: object) -> AgentToolResult:
        assert kwargs["tool_name"] == "research_report_prepare_v1"
        experiments = [
            {
                "experiment_id": experiment_id,
                "status": "COMPLETED",
                "evidence_id": f"ev_1_{index}",
            }
            for index, experiment_id in enumerate(self.experiment_ids, start=1)
        ]
        comparison = {
            "comparability": "COMPARABLE",
            "evidence_id": "ev_1_3",
        }
        content = {
            "schema_version": 1,
            "objective": "总结两次显式实验",
            "metric_name": None,
            "include_historical": False,
            "experiment_ids": self.experiment_ids,
            "experiments": experiments,
            "comparisons": [comparison],
            "repeated_group": {"analysis": {"accepted": False}},
        }
        content["source_hash"] = research_report_source_hash(content)
        return AgentToolResult(
            content=content,
            evidence=[
                AgentEvidence(
                    evidence_id="ev_1_1",
                    evidence_kind=AgentEvidenceKind.CONFIRMED_FACT,
                    entity_type="EXPERIMENT",
                    entity_id=self.experiment_ids[0],
                    label="实验一",
                    excerpt="COMPLETED",
                    payload={},
                ),
                AgentEvidence(
                    evidence_id="ev_1_2",
                    evidence_kind=AgentEvidenceKind.CONFIRMED_FACT,
                    entity_type="EXPERIMENT",
                    entity_id=self.experiment_ids[1],
                    label="实验二",
                    excerpt="COMPLETED",
                    payload={},
                ),
                AgentEvidence(
                    evidence_id="ev_1_3",
                    evidence_kind=AgentEvidenceKind.ANALYSIS,
                    entity_type="EXPERIMENT_COMPARISON",
                    label="确定性比较",
                    excerpt="COMPARABLE",
                    payload={},
                ),
            ],
        )


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        deployment_mode="cloud",
        web_auth_mode="cognito",
        object_storage_backend="aws_s3",
        queue_backend="sqs",
        llm_provider="bedrock",
        agent_enabled=True,
        agent_provider="bailian",
        bailian_api_key=SecretStr("test"),
        bailian_base_url="https://bailian.example/v1",
        bailian_agent_model="qwen-agent",
    )


def _setup(
    factory: sessionmaker[Session],
) -> tuple[AgentConversationService, object, object, Settings]:
    owner = seed_owner(factory)
    projects = SqlAlchemyProjectRepository()
    initialized = ProjectAdministrationService(factory, projects).initialize_project(
        identity=owner,
        idempotency_key=uuid4(),
        request=initial_request(),
    )
    now = datetime.now(UTC)
    session_id = uuid4()
    with factory() as session, session.begin():
        session.add(
            WebSession(
                id=session_id,
                user_id=owner.user_id,
                team_id=owner.team_id,
                session_hash="a" * 64,
                authenticated_at=now,
                reauthenticated_at=now,
                last_seen_at=now,
                absolute_expires_at=now + timedelta(hours=8),
            )
        )
    identity = owner.__class__(
        user_id=owner.user_id,
        team_id=owner.team_id,
        token_id=session_id,
        scopes=OWNER_WEB_SCOPES,
        authentication_method="WEB_SESSION",
        recent_authentication=True,
    )
    settings = _settings()
    return (
        AgentConversationService(factory, projects, SqlAlchemyAgentRepository(), settings),
        identity,
        initialized,
        settings,
    )


def test_agent_run_is_idempotent_and_persists_verified_answer(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    service, identity, initialized, settings = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    thread = service.create_thread(
        project_id=project_id,
        identity=identity,
        request=AgentThreadCreateRequest(),
    )
    key = uuid4()
    request = AgentMessageCreateRequest(content="当前项目目标是什么？")
    receipt = service.create_message(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
        idempotency_key=key,
        request=request,
    )
    replay = service.create_message(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
        idempotency_key=key,
        request=request,
    )
    assert replay.run_id == receipt.run_id

    repository = SqlAlchemyAgentRepository()
    runtime = GovernanceAgentRuntime(
        plan_check_session_factory,
        repository,
        AgentToolRegistry(plan_check_session_factory, SqlAlchemyProjectRepository()),
        ScriptedAgentModel(),
        settings,
    )
    processor = AgentRunProcessor(
        plan_check_session_factory,
        repository,
        AgentRunIdentityResolver(settings),
        runtime,
        settings,
        worker_id="test-agent-worker",
    )
    assert processor.process_once()

    run = service.get_run(project_id=project_id, run_id=receipt.run_id, identity=identity)
    view = service.get_thread(project_id=project_id, thread_id=thread.thread_id, identity=identity)
    assert run.status is AgentRunStatus.SUCCEEDED
    assert len(view.messages) == 2
    assert view.messages[-1].content == "当前项目正式目标已读取。"
    assert view.messages[-1].citations[0].evidence_id == "ev_1_1"
    with plan_check_session_factory() as session:
        assert session.scalar(select(AgentCitation)) is not None
        events = list(
            session.scalars(
                select(AgentRunEvent)
                .where(AgentRunEvent.run_id == receipt.run_id)
                .order_by(AgentRunEvent.sequence)
            ).all()
        )
        assert [item.event_type for item in events] == [
            "run.queued",
            "run.started",
            "tool.started",
            "tool.completed",
            "answer.delta",
            "run.completed",
        ]


def test_external_mcp_task_reuses_agent_runtime_and_is_visible_from_web(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    service, web_identity, initialized, settings = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    now = datetime.now(UTC)
    token_id = uuid4()
    scopes = frozenset({"project:read", "experiment:query"})
    external_tool_names = {
        item.name
        for item in AgentToolRegistry(
            plan_check_session_factory, SqlAlchemyProjectRepository()
        ).specs_for_version("r17a-external-v1")
    }
    assert external_tool_names == {
        "project_status_get_v1",
        "experiments_list_v1",
        "experiment_get_v1",
        "experiments_compare_v1",
        "experiment_group_stats_v1",
        "research_reports_list_v1",
        "research_report_get_v1",
        "research_memories_search_v1",
    }
    assert not any(
        name.startswith(("policy_draft_", "action_proposal_")) for name in external_tool_names
    )
    with plan_check_session_factory() as session, session.begin():
        session.add(
            AccessToken(
                id=token_id,
                user_id=web_identity.user_id,  # type: ignore[attr-defined]
                team_id=web_identity.team_id,  # type: ignore[attr-defined]
                project_id=project_id,
                audience=TokenAudience.MCP,
                name="external-agent",
                token_prefix="eg_external",
                token_hash="1" * 64,
                scopes=sorted(scopes),
                expires_at=now + timedelta(hours=1),
                created_by=web_identity.user_id,  # type: ignore[attr-defined]
            )
        )
    mcp_identity = RequestIdentity(
        user_id=web_identity.user_id,  # type: ignore[attr-defined]
        team_id=web_identity.team_id,  # type: ignore[attr-defined]
        token_id=token_id,
        project_id=project_id,
        scopes=scopes,
        authentication_method="MCP_TOKEN",
        credential_expires_at=now + timedelta(hours=1),
    )
    key = uuid4()
    request = ExternalAgentTaskStartRequest(
        title="外部消融任务",
        task_description="分析当前正式目标，并指出本次辅助模块消融需要保持的条件。",
    )
    started = service.start_external_task(
        project_id=project_id,
        identity=mcp_identity,
        idempotency_key=key,
        request=request,
    )
    replay = service.start_external_task(
        project_id=project_id,
        identity=mcp_identity,
        idempotency_key=key,
        request=request,
    )
    assert replay.task_id == started.task_id
    assert replay.run.run_id == started.run.run_id
    assert started.initial_context.policy.context.version == 1
    assert started.initial_context.authoritative_scope == "FORMAL_POLICY_ONLY"
    with pytest.raises(ConflictError, match="运行中的外部 Agent"):
        service.start_external_task(
            project_id=project_id,
            identity=mcp_identity,
            idempotency_key=uuid4(),
            request=ExternalAgentTaskStartRequest(task_description="并发创建另一个任务"),
        )

    repository = SqlAlchemyAgentRepository()
    processor = AgentRunProcessor(
        plan_check_session_factory,
        repository,
        AgentRunIdentityResolver(settings),
        GovernanceAgentRuntime(
            plan_check_session_factory,
            repository,
            AgentToolRegistry(plan_check_session_factory, SqlAlchemyProjectRepository()),
            ScriptedAgentModel(),
            settings,
        ),
        settings,
        worker_id="external-agent-worker",
    )
    assert processor.process_once()

    polled = service.get_external_task(
        task_id=started.task_id,
        identity=mcp_identity,
        after_sequence=0,
        limit=50,
    )
    assert polled.task.origin is AgentThreadOrigin.EXTERNAL_MCP
    assert polled.context_freshness == "CURRENT"
    assert polled.latest_run is not None
    assert polled.latest_run.status is AgentRunStatus.SUCCEEDED
    assert polled.messages[-1].citations[0].entity_type == "POLICY_BUNDLE"

    web_view = service.get_thread(
        project_id=project_id,
        thread_id=started.task_id,
        identity=web_identity,  # type: ignore[arg-type]
    )
    assert web_view.thread.origin is AgentThreadOrigin.EXTERNAL_MCP
    assert web_view.external_task_context is not None
    assert web_view.external_task_context.context_freshness == "CURRENT"

    web_question = service.create_message(
        project_id=project_id,
        thread_id=started.task_id,
        identity=web_identity,  # type: ignore[arg-type]
        idempotency_key=uuid4(),
        request=AgentMessageCreateRequest(content="从 Web 继续解释当前实验边界。"),
    )
    with plan_check_session_factory() as session:
        web_run = session.get(AgentRun, web_question.run_id)
        assert web_run is not None
        assert web_run.auth_method is AgentRunAuthMethod.WEB_SESSION
        assert web_run.auth_session_id == web_identity.token_id  # type: ignore[union-attr]
        assert web_run.prompt_version == "r17a-external-v1"
        assert web_run.tool_catalog_version == "r17a-external-v1"
    web_processor = AgentRunProcessor(
        plan_check_session_factory,
        repository,
        AgentRunIdentityResolver(settings),
        GovernanceAgentRuntime(
            plan_check_session_factory,
            repository,
            AgentToolRegistry(plan_check_session_factory, SqlAlchemyProjectRepository()),
            ScriptedAgentModel(),
            settings,
        ),
        settings,
        worker_id="external-web-continuation-worker",
    )
    assert web_processor.process_once()

    question = service.ask_external_task(
        task_id=started.task_id,
        identity=mcp_identity,
        idempotency_key=uuid4(),
        request=ExternalAgentQuestionRequest(question="当前 baseline 是什么？"),
    )
    with plan_check_session_factory() as session:
        run = session.get(AgentRun, question.run_id)
        assert run is not None
        assert run.auth_method is AgentRunAuthMethod.MCP_TOKEN
        assert run.auth_access_token_id == token_id
        assert run.auth_session_id is None
        assert run.prompt_version == "r17a-external-v1"

    with plan_check_session_factory() as session, session.begin():
        context = session.scalar(
            select(ProjectContext).where(ProjectContext.project_id == project_id)
        )
        assert context is not None
        context.goal = context.goal + "（测试策略变化）"
    stale = service.get_external_task(
        task_id=started.task_id,
        identity=mcp_identity,
        after_sequence=polled.next_sequence,
        limit=50,
    )
    assert stale.context_freshness == "STALE"
    assert stale.warning is not None
    assert stale.initial_context.policy.context_payload.goal != context.goal

    with pytest.raises(ConflictError, match="不同的外部 Agent 任务"):
        service.start_external_task(
            project_id=project_id,
            identity=mcp_identity,
            idempotency_key=key,
            request=ExternalAgentTaskStartRequest(task_description="不同任务"),
        )


def test_experiment_plan_auto_revision_and_human_approval_are_versioned(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    conversation, web_identity, initialized, settings = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    now = datetime.now(UTC)
    token_id = uuid4()
    scopes = frozenset({"project:read", "experiment:query", "experiment:check"})
    with plan_check_session_factory() as session, session.begin():
        session.add(
            AccessToken(
                id=token_id,
                user_id=web_identity.user_id,  # type: ignore[attr-defined]
                team_id=web_identity.team_id,  # type: ignore[attr-defined]
                project_id=project_id,
                audience=TokenAudience.MCP,
                name="experiment-plan-agent",
                token_prefix="eg_plan",
                token_hash="9" * 64,
                scopes=sorted(scopes),
                expires_at=now + timedelta(hours=1),
                created_by=web_identity.user_id,  # type: ignore[attr-defined]
            )
        )
    mcp_identity = RequestIdentity(
        user_id=web_identity.user_id,  # type: ignore[attr-defined]
        team_id=web_identity.team_id,  # type: ignore[attr-defined]
        token_id=token_id,
        project_id=project_id,
        scopes=scopes,
        authentication_method="MCP_TOKEN",
        credential_expires_at=now + timedelta(hours=1),
    )
    started = conversation.start_external_task(
        project_id=project_id,
        identity=mcp_identity,
        idempotency_key=uuid4(),
        request=ExternalAgentTaskStartRequest(
            title="融合消融",
            task_description="先读取正式策略，然后形成单变量融合实验计划。",
        ),
    )
    repository = SqlAlchemyAgentRepository()
    initial_processor = AgentRunProcessor(
        plan_check_session_factory,
        repository,
        AgentRunIdentityResolver(settings),
        GovernanceAgentRuntime(
            plan_check_session_factory,
            repository,
            AgentToolRegistry(plan_check_session_factory, SqlAlchemyProjectRepository()),
            ScriptedAgentModel(),
            settings,
        ),
        settings,
        worker_id="plan-initial-worker",
    )
    assert initial_processor.process_once()

    plans = ExperimentPlanService(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
        repository,
        settings,
    )
    submitted = plans.submit_external(
        task_id=started.task_id,
        identity=mcp_identity,
        idempotency_key=uuid4(),
        request=ExperimentPlanSubmitRequest(
            title="融合系数单变量消融",
            plan_markdown="## 目标\n仅调整融合系数，保持协议和主干模型不变。",
            evidence=ExperimentPlanEvidence(
                configuration=ConfigurationDocument(
                    format="yaml",
                    content=(
                        "dataset:\n  protocol: 40/20\nmodel:\n"
                        "  backbone: shift-gcn\n  fusion: 0.3\n"
                    ),
                ),
                run_command="python train.py --config fusion.yaml",
                git_commit="abc1234",
            ),
        ),
    )
    plan_model = ScriptedExperimentPlanModel()
    plan_processor = AgentRunProcessor(
        plan_check_session_factory,
        repository,
        AgentRunIdentityResolver(settings),
        GovernanceAgentRuntime(
            plan_check_session_factory,
            repository,
            AgentToolRegistry(plan_check_session_factory, SqlAlchemyProjectRepository()),
            plan_model,
            settings,
            plans,
        ),
        settings,
        worker_id="plan-review-worker",
    )
    assert plan_processor.process_once()
    assert plan_processor.process_once()

    reviewed = plans.get_external(plan_id=submitted.plan_id, identity=mcp_identity)
    assert reviewed.summary.status is ExperimentPlanStatus.READY_FOR_APPROVAL
    assert reviewed.summary.current_revision == 2
    assert reviewed.current.author_type.value == "INTERNAL_AGENT"
    assert reviewed.current.automatic_revision_round == 1
    assert reviewed.review is not None
    assert reviewed.review.semantic_review.recommendation.value == "READY"
    assert reviewed.review.hard_check.status == "PASS"
    assert len(reviewed.review.candidate_invariants) == 1
    candidate_id = str(reviewed.review.candidate_invariants[0]["candidate_id"])

    approved = plans.decide(
        project_id=project_id,
        plan_id=submitted.plan_id,
        identity=web_identity,  # type: ignore[arg-type]
        idempotency_key=uuid4(),
        request=ExperimentPlanDecisionRequest(
            expected_revision=2,
            review_hash=reviewed.review.review_hash,
            approval_digest=reviewed.review.approval_digest,
            decision=ExperimentPlanDecisionType.APPROVED,
            reason="已核对单变量范围、正式协议和低成本验证。",
            confirmed_candidate_ids=[candidate_id],
        ),
    )
    assert approved.summary.status is ExperimentPlanStatus.APPROVED
    assert approved.decision is not None
    assert approved.decision["approved_snapshot"]["governance_notice"].startswith(
        "该决定只冻结计划级授权"
    )

    with plan_check_session_factory() as session:
        assert (
            len(
                session.scalars(
                    select(ExperimentPlanRevision).where(
                        ExperimentPlanRevision.plan_id == submitted.plan_id
                    )
                ).all()
            )
            == 2
        )
        assert (
            len(
                session.scalars(
                    select(ExperimentPlanReview)
                    .join(ExperimentPlanRevision)
                    .where(ExperimentPlanRevision.plan_id == submitted.plan_id)
                ).all()
            )
            == 2
        )
        assert session.scalar(
            select(ExperimentPlanDecision).where(
                ExperimentPlanDecision.plan_id == submitted.plan_id
            )
        )


def test_experiment_plan_policy_drift_stops_before_model_call(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    conversation, web_identity, initialized, settings = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    now = datetime.now(UTC)
    token_id = uuid4()
    scopes = frozenset({"project:read", "experiment:query", "experiment:check"})
    with plan_check_session_factory() as session, session.begin():
        session.add(
            AccessToken(
                id=token_id,
                user_id=web_identity.user_id,  # type: ignore[attr-defined]
                team_id=web_identity.team_id,  # type: ignore[attr-defined]
                project_id=project_id,
                audience=TokenAudience.MCP,
                name="stale-plan-agent",
                token_prefix="eg_stale",
                token_hash="8" * 64,
                scopes=sorted(scopes),
                expires_at=now + timedelta(hours=1),
                created_by=web_identity.user_id,  # type: ignore[attr-defined]
            )
        )
    mcp_identity = RequestIdentity(
        user_id=web_identity.user_id,  # type: ignore[attr-defined]
        team_id=web_identity.team_id,  # type: ignore[attr-defined]
        token_id=token_id,
        project_id=project_id,
        scopes=scopes,
        authentication_method="MCP_TOKEN",
        credential_expires_at=now + timedelta(hours=1),
    )
    started = conversation.start_external_task(
        project_id=project_id,
        identity=mcp_identity,
        idempotency_key=uuid4(),
        request=ExternalAgentTaskStartRequest(task_description="准备实验计划"),
    )
    repository = SqlAlchemyAgentRepository()
    assert AgentRunProcessor(
        plan_check_session_factory,
        repository,
        AgentRunIdentityResolver(settings),
        GovernanceAgentRuntime(
            plan_check_session_factory,
            repository,
            AgentToolRegistry(plan_check_session_factory, SqlAlchemyProjectRepository()),
            ScriptedAgentModel(),
            settings,
        ),
        settings,
        worker_id="stale-initial-worker",
    ).process_once()
    plans = ExperimentPlanService(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
        repository,
        settings,
    )
    submitted = plans.submit_external(
        task_id=started.task_id,
        identity=mcp_identity,
        idempotency_key=uuid4(),
        request=ExperimentPlanSubmitRequest(
            title="待过期计划",
            plan_markdown="验证策略漂移保护。",
        ),
    )
    with plan_check_session_factory() as session, session.begin():
        context = session.scalar(
            select(ProjectContext).where(ProjectContext.project_id == project_id)
        )
        assert context is not None
        context.goal = context.goal + "（正式版本内容漂移）"

    plan_model = ScriptedExperimentPlanModel()
    processor = AgentRunProcessor(
        plan_check_session_factory,
        repository,
        AgentRunIdentityResolver(settings),
        GovernanceAgentRuntime(
            plan_check_session_factory,
            repository,
            AgentToolRegistry(plan_check_session_factory, SqlAlchemyProjectRepository()),
            plan_model,
            settings,
            plans,
        ),
        settings,
        worker_id="stale-plan-worker",
    )
    assert processor.process_once()
    assert plan_model.calls == 0
    with plan_check_session_factory() as session:
        plan = session.get(ExperimentPlan, submitted.plan_id)
        run = session.get(AgentRun, submitted.run_id)
        assert plan is not None and plan.status is ExperimentPlanStatus.STALE
        assert run is not None and run.status is AgentRunStatus.FAILED
        assert run.error is not None
        assert run.error["code"] == "EXPERIMENT_PLAN_POLICY_STALE"


def test_external_agent_worker_rejects_revoked_token(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    service, web_identity, initialized, settings = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    now = datetime.now(UTC)
    token_id = uuid4()
    with plan_check_session_factory() as session, session.begin():
        session.add(
            AccessToken(
                id=token_id,
                user_id=web_identity.user_id,  # type: ignore[attr-defined]
                team_id=web_identity.team_id,  # type: ignore[attr-defined]
                project_id=project_id,
                audience=TokenAudience.MCP,
                name="revoked-agent",
                token_prefix="eg_revoked",
                token_hash="2" * 64,
                scopes=["experiment:query", "project:read"],
                expires_at=now + timedelta(hours=1),
                created_by=web_identity.user_id,  # type: ignore[attr-defined]
            )
        )
    identity = RequestIdentity(
        user_id=web_identity.user_id,  # type: ignore[attr-defined]
        team_id=web_identity.team_id,  # type: ignore[attr-defined]
        token_id=token_id,
        project_id=project_id,
        scopes=frozenset({"project:read", "experiment:query"}),
        authentication_method="MCP_TOKEN",
        credential_expires_at=now + timedelta(hours=1),
    )
    started = service.start_external_task(
        project_id=project_id,
        identity=identity,
        idempotency_key=uuid4(),
        request=ExternalAgentTaskStartRequest(task_description="读取当前正式项目状态"),
    )
    with plan_check_session_factory() as session, session.begin():
        token = session.get(AccessToken, token_id)
        assert token is not None
        token.revoked_at = datetime.now(UTC)

    repository = SqlAlchemyAgentRepository()
    processor = AgentRunProcessor(
        plan_check_session_factory,
        repository,
        AgentRunIdentityResolver(settings),
        GovernanceAgentRuntime(
            plan_check_session_factory,
            repository,
            AgentToolRegistry(plan_check_session_factory, SqlAlchemyProjectRepository()),
            ScriptedAgentModel(),
            settings,
        ),
        settings,
        worker_id="revoked-agent-worker",
    )
    assert processor.process_once()
    with plan_check_session_factory() as session:
        run = session.get(AgentRun, started.run.run_id)
        assert run is not None
        assert run.status is AgentRunStatus.FAILED
        assert run.error is not None
        assert "MCP Token 已失效" in str(run.error["message"])


def test_external_oauth_run_uses_live_grant_and_client_permissions(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    service, web_identity, initialized, settings = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    now = datetime.now(UTC)
    grant_id = uuid4()
    client_id = uuid4()
    scopes = ["experiment:query", "project:read"]
    with plan_check_session_factory() as session, session.begin():
        session.add(
            McpOAuthClient(
                id=client_id,
                cognito_client_id="external-coding-agent",
                name="External Coding Agent",
                team_id=web_identity.team_id,  # type: ignore[attr-defined]
                project_id=project_id,
                allowed_scopes=scopes,
                created_by=web_identity.user_id,  # type: ignore[attr-defined]
            )
        )
        session.add(
            McpOAuthGrant(
                id=grant_id,
                mcp_oauth_client_id=client_id,
                user_id=web_identity.user_id,  # type: ignore[attr-defined]
                granted_scopes=scopes,
                last_used_at=now,
            )
        )
    identity = RequestIdentity(
        user_id=web_identity.user_id,  # type: ignore[attr-defined]
        team_id=web_identity.team_id,  # type: ignore[attr-defined]
        token_id=grant_id,
        project_id=project_id,
        scopes=frozenset(scopes),
        authentication_method="MCP_OAUTH",
        client_id="external-coding-agent",
        credential_expires_at=now + timedelta(minutes=15),
    )
    started = service.start_external_task(
        project_id=project_id,
        identity=identity,
        idempotency_key=uuid4(),
        request=ExternalAgentTaskStartRequest(task_description="读取当前实验主线"),
    )
    resolver = AgentRunIdentityResolver(settings)
    with plan_check_session_factory() as session:
        run = session.get(AgentRun, started.run.run_id)
        assert run is not None
        assert run.auth_method is AgentRunAuthMethod.MCP_OAUTH
        resolved = resolver.resolve(session, run)
        assert resolved.authentication_method == "MCP_OAUTH"
        assert "experiment:read" in resolved.scopes
        assert "project:write" not in resolved.scopes

    with plan_check_session_factory() as session, session.begin():
        grant = session.get(McpOAuthGrant, grant_id)
        assert grant is not None
        grant.revoked_at = datetime.now(UTC)
    with plan_check_session_factory() as session:
        run = session.get(AgentRun, started.run.run_id)
        assert run is not None
        with pytest.raises(AuthenticationError, match="OAuth 授权已失效"):
            resolver.resolve(session, run)


def test_agent_provider_can_require_a_separate_structured_final_turn(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    service, identity, initialized, settings = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    thread = service.create_thread(
        project_id=project_id,
        identity=identity,
        request=AgentThreadCreateRequest(),
    )
    receipt = service.create_message(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
        idempotency_key=uuid4(),
        request=AgentMessageCreateRequest(content="当前项目目标是什么？"),
    )
    repository = SqlAlchemyAgentRepository()
    model = SeparateFinalTurnAgentModel()
    processor = AgentRunProcessor(
        plan_check_session_factory,
        repository,
        AgentRunIdentityResolver(settings),
        GovernanceAgentRuntime(
            plan_check_session_factory,
            repository,
            AgentToolRegistry(plan_check_session_factory, SqlAlchemyProjectRepository()),
            model,
            settings,
        ),
        settings,
        worker_id="separate-final-worker",
    )

    assert processor.process_once()
    run = service.get_run(project_id=project_id, run_id=receipt.run_id, identity=identity)
    assert run.status is AgentRunStatus.SUCCEEDED
    assert model.calls == 3
    assert len(run.model_calls) == 3


def test_expired_agent_lease_increments_generation_and_blocks_stale_owner(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    service, identity, initialized, settings = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    thread = service.create_thread(
        project_id=project_id,
        identity=identity,
        request=AgentThreadCreateRequest(),
    )
    receipt = service.create_message(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
        idempotency_key=uuid4(),
        request=AgentMessageCreateRequest(content="状态"),
    )
    repository = SqlAlchemyAgentRepository()
    with plan_check_session_factory() as session, session.begin():
        first = repository.claim_next(
            session, worker_id="worker-1", lease_seconds=settings.agent_run_lease_seconds
        )
    assert first is not None
    with plan_check_session_factory() as session, session.begin():
        run = session.get(AgentRun, receipt.run_id)
        assert run is not None
        run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with plan_check_session_factory() as session, session.begin():
        second = repository.claim_next(
            session, worker_id="worker-2", lease_seconds=settings.agent_run_lease_seconds
        )
    assert second is not None and second.generation == first.generation + 1
    with plan_check_session_factory() as session, session.begin():
        assert not repository.renew_lease(
            session, claim=first, lease_seconds=settings.agent_run_lease_seconds
        )
        assert repository.renew_lease(
            session, claim=second, lease_seconds=settings.agent_run_lease_seconds
        )
    with plan_check_session_factory() as session:
        assert session.scalar(select(AgentMessage).where(AgentMessage.role == "ASSISTANT")) is None


def test_agent_conversations_require_web_session_and_validate_cursor(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    service, identity, initialized, _ = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    bearer_identity = identity.__class__(
        user_id=identity.user_id,
        team_id=identity.team_id,
        token_id=uuid4(),
        scopes=identity.scopes,
        authentication_method="ACCESS_TOKEN",
        recent_authentication=False,
    )
    with pytest.raises(AuthorizationError, match="Web Session"):
        service.list_threads(
            project_id=project_id,
            identity=bearer_identity,
            archived=False,
            cursor=None,
            limit=20,
        )
    with pytest.raises(InputValidationError, match="cursor"):
        service.list_threads(
            project_id=project_id,
            identity=identity,
            archived=False,
            cursor="a",
            limit=20,
        )


@pytest.mark.parametrize("fail_summary", [False, True])
def test_agent_rolling_summary_is_audited_and_failure_degrades_safely(
    plan_check_session_factory: sessionmaker[Session],
    fail_summary: bool,
) -> None:
    service, identity, initialized, base_settings = _setup(plan_check_session_factory)
    settings = base_settings.model_copy(
        update={
            "agent_recent_message_limit": 2,
            "agent_summary_min_new_messages": 2,
        }
    )
    # Service 只读取 Feature 和 Run 配置；替换后保证新 Run 使用相同阈值。
    service = AgentConversationService(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
        SqlAlchemyAgentRepository(),
        settings,
    )
    project_id = initialized.project_id  # type: ignore[attr-defined]
    thread = service.create_thread(
        project_id=project_id,
        identity=identity,
        request=AgentThreadCreateRequest(),
    )
    repository = SqlAlchemyAgentRepository()
    model = SummaryAwareAgentModel(fail_summary=fail_summary)
    runtime = GovernanceAgentRuntime(
        plan_check_session_factory,
        repository,
        AgentToolRegistry(plan_check_session_factory, SqlAlchemyProjectRepository()),
        model,
        settings,
    )
    processor = AgentRunProcessor(
        plan_check_session_factory,
        repository,
        AgentRunIdentityResolver(settings),
        runtime,
        settings,
        worker_id="summary-agent-worker",
    )
    receipts = []
    for index in range(3):
        receipts.append(
            service.create_message(
                project_id=project_id,
                thread_id=thread.thread_id,
                identity=identity,
                idempotency_key=uuid4(),
                request=AgentMessageCreateRequest(content=f"第 {index + 1} 轮问题"),
            )
        )
        assert processor.process_once()

    final_run = service.get_run(
        project_id=project_id,
        run_id=receipts[-1].run_id,
        identity=identity,
    )
    view = service.get_thread(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
    )
    assert final_run.status is AgentRunStatus.SUCCEEDED
    assert view.messages[-1].sections[0].evidence_kind.value == "USER_PROVIDED"
    with plan_check_session_factory() as session:
        summary = session.scalar(
            select(AgentContextSummary).order_by(AgentContextSummary.created_at.desc())
        )
        summary_call = session.scalar(
            select(AgentModelCall).where(
                AgentModelCall.purpose == AgentModelCallPurpose.CONTEXT_SUMMARY
            )
        )
        assert summary is not None and summary_call is not None
        assert summary.covered_sequence_from == 1
        assert summary.covered_sequence_to == 3
        events = list(
            session.scalars(
                select(AgentRunEvent)
                .where(AgentRunEvent.run_id == receipts[-1].run_id)
                .order_by(AgentRunEvent.sequence)
            ).all()
        )
        event_types = [item.event_type for item in events]
        stored_run = session.get(AgentRun, receipts[-1].run_id)
        assert stored_run is not None
        context_mode = stored_run.context_snapshot["context_mode"]
        second_run = session.get(AgentRun, receipts[1].run_id)
        assert second_run is not None
        assert second_run.context_snapshot["context_mode"] == "DEGRADED_TRIM"
    if fail_summary:
        assert summary.status is AgentContextSummaryStatus.FAILED
        assert view.context_summary is not None
        assert view.context_summary.degraded
        assert "summary.failed" in event_types
        assert context_mode == "DEGRADED_TRIM"
    else:
        assert summary.status is AgentContextSummaryStatus.READY
        assert view.context_summary is not None
        assert not view.context_summary.authoritative
        assert not view.context_summary.degraded
        assert "summary.completed" in event_types
        assert context_mode == "ROLLING_SUMMARY"


def test_agent_retry_writes_user_audit_record(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    service, identity, initialized, _ = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    thread = service.create_thread(
        project_id=project_id,
        identity=identity,
        request=AgentThreadCreateRequest(),
    )
    original = service.create_message(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
        idempotency_key=uuid4(),
        request=AgentMessageCreateRequest(content="读取项目状态"),
    )
    with plan_check_session_factory() as session, session.begin():
        run = session.get(AgentRun, original.run_id)
        assert run is not None
        run.status = AgentRunStatus.FAILED
        run.completed_at = datetime.now(UTC)

    retried = service.retry_run(
        project_id=project_id,
        run_id=original.run_id,
        identity=identity,
        idempotency_key=uuid4(),
    )
    view = service.get_thread(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
    )
    assert view.messages[-1].run_id == retried.run_id
    with plan_check_session_factory() as session:
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "agent.run.retried",
                AuditLog.target_id == retried.run_id,
            )
        )
        assert audit is not None
        assert audit.actor_id == identity.user_id


def test_pending_r15a_run_keeps_original_prompt_and_tool_catalog(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    service, identity, initialized, settings = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    thread = service.create_thread(
        project_id=project_id,
        identity=identity,
        request=AgentThreadCreateRequest(),
    )
    receipt = service.create_message(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
        idempotency_key=uuid4(),
        request=AgentMessageCreateRequest(content="旧 Run 继续执行"),
    )
    with plan_check_session_factory() as session, session.begin():
        run = session.get(AgentRun, receipt.run_id)
        assert run is not None
        run.prompt_version = "r15a-v1"
        run.tool_catalog_version = "r15a-v1"
    repository = SqlAlchemyAgentRepository()
    runtime = GovernanceAgentRuntime(
        plan_check_session_factory,
        repository,
        AgentToolRegistry(plan_check_session_factory, SqlAlchemyProjectRepository()),
        CatalogInspectingModel(),
        settings,
    )
    processor = AgentRunProcessor(
        plan_check_session_factory,
        repository,
        AgentRunIdentityResolver(settings),
        runtime,
        settings,
        worker_id="catalog-agent-worker",
    )
    assert processor.process_once()
    assert (
        service.get_run(
            project_id=project_id,
            run_id=receipt.run_id,
            identity=identity,
        ).status
        is AgentRunStatus.SUCCEEDED
    )


def test_research_report_is_persisted_atomically_and_shared_as_analysis(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    service, identity, initialized, settings = _setup(plan_check_session_factory)
    project_id = initialized.project_id  # type: ignore[attr-defined]
    thread = service.create_thread(
        project_id=project_id,
        identity=identity,
        request=AgentThreadCreateRequest(),
    )
    service.create_message(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
        idempotency_key=uuid4(),
        request=AgentMessageCreateRequest(content="请基于两个显式实验生成阶段研究报告"),
    )
    experiment_ids = [str(uuid4()), str(uuid4())]
    repository = SqlAlchemyAgentRepository()
    runtime = GovernanceAgentRuntime(
        plan_check_session_factory,
        repository,
        ResearchReportToolRegistry(experiment_ids),  # type: ignore[arg-type]
        ResearchReportAgentModel(experiment_ids),
        settings,
    )
    processor = AgentRunProcessor(
        plan_check_session_factory,
        repository,
        AgentRunIdentityResolver(settings),
        runtime,
        settings,
        worker_id="research-report-worker",
    )
    assert processor.process_once()

    view = service.get_thread(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
    )
    assert view.messages[-1].research_report_id is not None
    report_service = ResearchReportService(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
    )
    report = report_service.get_report(
        project_id=project_id,
        report_id=view.messages[-1].research_report_id,
        identity=identity,
    )
    assert report.authoritative is False
    assert report.evidence_classification == "ANALYSIS"
    assert [str(item) for item in report.experiment_ids] == experiment_ids
    assert len(report.source_warnings) == 2
    researcher_id = uuid4()
    researcher_session_id = uuid4()
    now = datetime.now(UTC)
    with plan_check_session_factory() as session, session.begin():
        session.add(
            User(
                id=researcher_id,
                name="Report Researcher",
                email="report-reader@example.com",
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
    researcher = identity.__class__(
        user_id=researcher_id,
        team_id=identity.team_id,
        token_id=researcher_session_id,
        scopes=frozenset({"experiment:read"}),
        authentication_method="WEB_SESSION",
    )
    assert (
        report_service.list_reports(
            project_id=project_id,
            identity=researcher,
            cursor=None,
            limit=20,
        )
        .items[0]
        .report_id
        == report.report_id
    )
    with plan_check_session_factory() as session:
        assert session.scalar(select(AgentResearchReport)) is not None
        assert (
            session.scalar(
                select(AuditLog).where(AuditLog.action == "agent.research_report.created")
            )
            is not None
        )
    with plan_check_session_factory() as session, session.begin():
        persisted_report = session.get(AgentResearchReport, report.report_id)
        assert persisted_report is not None
        persisted_report.title = "与不可变正文不一致的标题"
    with pytest.raises(ConflictError, match="来源或内容哈希不一致"):
        report_service.get_report(
            project_id=project_id,
            report_id=report.report_id,
            identity=researcher,
        )


def test_agent_model_calls_freeze_pricing_and_owner_observability(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    _, identity, initialized, base_settings = _setup(plan_check_session_factory)
    settings = base_settings.model_copy(
        update={
            "agent_input_cost_per_million_tokens": Decimal("2"),
            "agent_output_cost_per_million_tokens": Decimal("4"),
            "agent_cost_currency": "USD",
        }
    )
    projects = SqlAlchemyProjectRepository()
    service = AgentConversationService(
        plan_check_session_factory,
        projects,
        SqlAlchemyAgentRepository(),
        settings,
    )
    project_id = initialized.project_id  # type: ignore[attr-defined]
    thread = service.create_thread(
        project_id=project_id,
        identity=identity,  # type: ignore[arg-type]
        request=AgentThreadCreateRequest(),
    )
    receipt = service.create_message(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,  # type: ignore[arg-type]
        idempotency_key=uuid4(),
        request=AgentMessageCreateRequest(content="读取状态并记录模型观测"),
    )
    repository = SqlAlchemyAgentRepository()
    processor = AgentRunProcessor(
        plan_check_session_factory,
        repository,
        AgentRunIdentityResolver(settings),
        GovernanceAgentRuntime(
            plan_check_session_factory,
            repository,
            AgentToolRegistry(plan_check_session_factory, projects),
            ScriptedAgentModel(),
            settings,
        ),
        settings,
        worker_id="observability-agent-worker",
    )
    assert processor.process_once()

    run = service.get_run(
        project_id=project_id,
        run_id=receipt.run_id,
        identity=identity,  # type: ignore[arg-type]
    )
    assert len(run.model_calls) == 2
    assert run.model_calls[0].estimated_cost is None
    assert run.model_calls[1].estimated_cost == Decimal("0.0000800000")
    assert run.model_calls[1].cost_currency == "USD"
    assert run.model_calls[1].latency_ms is not None

    view = AgentObservabilityService(
        plan_check_session_factory,
        projects,
        settings,
    ).get_project_observability(
        project_id=project_id,
        identity=identity,  # type: ignore[arg-type]
        window_days=7,
        provider=None,
        model_id=None,
    )
    assert view.totals.run_count == 1
    assert view.totals.model_call_count == 2
    assert view.totals.input_tokens == 20
    assert view.totals.output_tokens == 10
    assert view.totals.unpriced_call_count == 1
    assert view.costs[0].estimated_cost == Decimal("0.0000800000")

    observability = AgentObservabilityService(
        plan_check_session_factory,
        projects,
        settings,
    )
    with pytest.raises(AuthorizationError, match="Web Session"):
        observability.get_project_observability(
            project_id=project_id,
            identity=RequestIdentity(
                user_id=identity.user_id,  # type: ignore[union-attr]
                team_id=identity.team_id,  # type: ignore[union-attr]
                token_id=identity.token_id,  # type: ignore[union-attr]
                scopes=OWNER_WEB_SCOPES,
                authentication_method="MCP_OAUTH",
            ),
            window_days=7,
            provider=None,
            model_id=None,
        )
    with pytest.raises(AuthorizationError, match="project:write"):
        observability.get_project_observability(
            project_id=project_id,
            identity=RequestIdentity(
                user_id=identity.user_id,  # type: ignore[union-attr]
                team_id=identity.team_id,  # type: ignore[union-attr]
                token_id=identity.token_id,  # type: ignore[union-attr]
                scopes=frozenset({"project:read"}),
                authentication_method="WEB_SESSION",
            ),
            window_days=7,
            provider=None,
            model_id=None,
        )
