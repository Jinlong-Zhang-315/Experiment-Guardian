from collections.abc import Sequence
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.agent import AgentRunIdentityResolver
from experiment_guardian.application.agent_runtime import AgentRunProcessor, GovernanceAgentRuntime
from experiment_guardian.application.errors import ServiceUnavailableError
from experiment_guardian.application.ports import EmbeddingGenerator, EmbeddingModelOutput
from experiment_guardian.application.research_memories import (
    ResearchMemoryEmbeddingProcessor,
    ResearchMemoryService,
)
from experiment_guardian.domain.agent import AgentMessageCreateRequest, AgentThreadCreateRequest
from experiment_guardian.domain.enums import ResearchMemoryEmbeddingStatus
from experiment_guardian.domain.research_memory import ResearchMemorySearchRequest
from experiment_guardian.infrastructure.models import (
    AgentResearchMemory,
    AgentResearchMemoryEmbedding,
    AuditLog,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyAgentRepository,
    SqlAlchemyProjectRepository,
)
from tests.integration.test_agent_slice import (
    ResearchReportAgentModel,
    ResearchReportToolRegistry,
    _setup,
)


class StableEmbeddingGenerator(EmbeddingGenerator):
    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def provider(self) -> str:
        return "test"

    @property
    def model_id(self) -> str:
        return "test-embedding-1024"

    @property
    def dimension(self) -> int:
        return 1024

    def embed(self, input_text: str) -> EmbeddingModelOutput:
        self.calls.append(input_text)
        return EmbeddingModelOutput(vector=[1.0] + [0.0] * 1023, input_tokens=10)


class FailingEmbeddingGenerator(StableEmbeddingGenerator):
    def embed(self, input_text: str) -> EmbeddingModelOutput:
        self.calls.append(input_text)
        raise ServiceUnavailableError("provider unavailable")


def _create_report(factory: sessionmaker[Session]):  # type: ignore[no-untyped-def]
    service, identity, initialized, settings = _setup(factory)
    project_id = initialized.project_id
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
        request=AgentMessageCreateRequest(content="生成研究报告"),
    )
    experiment_ids = [str(uuid4()), str(uuid4())]
    repository = SqlAlchemyAgentRepository()
    runtime = GovernanceAgentRuntime(
        factory,
        repository,
        ResearchReportToolRegistry(experiment_ids),  # type: ignore[arg-type]
        ResearchReportAgentModel(experiment_ids),
        settings,
    )
    processor = AgentRunProcessor(
        factory,
        repository,
        AgentRunIdentityResolver(settings),
        runtime,
        settings,
        worker_id="report-worker",
    )
    assert processor.process_once()
    view = service.get_thread(
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
    )
    assert view.messages[-1].research_report_id is not None
    return identity, project_id, settings


def test_memory_embedding_and_stale_search_are_isolated_from_report(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    identity, project_id, settings = _create_report(plan_check_session_factory)
    generator = StableEmbeddingGenerator()
    processor = ResearchMemoryEmbeddingProcessor(
        plan_check_session_factory,
        generator,
        settings,
        worker_id="memory-worker",
    )
    assert processor.process_once()
    with plan_check_session_factory() as session:
        memories = list(session.scalars(select(AgentResearchMemory)).all())
        embeddings: Sequence[AgentResearchMemoryEmbedding] = list(
            session.scalars(select(AgentResearchMemoryEmbedding)).all()
        )
        assert len(memories) == 1
        assert len(embeddings) == 1
        assert embeddings[0].status is ResearchMemoryEmbeddingStatus.READY
        assert embeddings[0].embedding is not None

    service = ResearchMemoryService(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
        generator,
        settings,
    )
    before = len(generator.calls)
    current_only = service.search(
        project_id=project_id,
        identity=identity,
        request=ResearchMemorySearchRequest(query="阶段结论"),
    )
    assert current_only.items == []
    assert len(generator.calls) == before
    historical = service.search(
        project_id=project_id,
        identity=identity,
        request=ResearchMemorySearchRequest(query="阶段结论", include_stale=True),
    )
    assert len(historical.items) == 1
    assert historical.items[0].retrieval_role == "CANDIDATE_EVIDENCE"
    assert historical.items[0].source_freshness == "SOURCE_MISSING"


def test_dead_letter_can_be_idempotently_retried_by_owner(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    identity, project_id, settings = _create_report(plan_check_session_factory)
    settings = settings.model_copy(update={"agent_run_max_attempts": 1})
    generator = FailingEmbeddingGenerator()
    processor = ResearchMemoryEmbeddingProcessor(
        plan_check_session_factory,
        generator,
        settings,
        worker_id="failing-memory-worker",
    )
    assert processor.process_once()
    with plan_check_session_factory() as session:
        memory = session.scalar(select(AgentResearchMemory))
        embedding = session.scalar(select(AgentResearchMemoryEmbedding))
        assert memory is not None and embedding is not None
        assert embedding.status is ResearchMemoryEmbeddingStatus.DEAD_LETTER
        assert session.scalar(
            select(AuditLog).where(
                AuditLog.action == "agent.research_memory.embedding_failed"
            )
        ) is not None
        memory_id = memory.id

    service = ResearchMemoryService(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
        generator,
        settings,
    )
    key = uuid4()
    first = service.retry_embedding(
        project_id=project_id,
        memory_id=memory_id,
        identity=identity,
        idempotency_key=key,
    )
    replay = service.retry_embedding(
        project_id=project_id,
        memory_id=memory_id,
        identity=identity,
        idempotency_key=key,
    )
    assert first == replay
    assert replay.embedding_status is ResearchMemoryEmbeddingStatus.PENDING
