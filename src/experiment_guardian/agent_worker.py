"""持久化治理 Agent Run 的独立数据库轮询 Worker。"""

import os
import socket
import time
from dataclasses import dataclass

import structlog

from experiment_guardian.application.agent import AgentRunIdentityResolver
from experiment_guardian.application.agent_runtime import (
    AgentRunProcessor,
    GovernanceAgentRuntime,
)
from experiment_guardian.application.agent_tools import AgentToolRegistry
from experiment_guardian.application.container import (
    build_agent_chat_model,
    get_agent_tool_registry,
    get_experiment_plan_service,
    get_query_embedding_generator,
)
from experiment_guardian.application.research_memories import (
    ResearchMemoryEmbeddingProcessor,
)
from experiment_guardian.core.config import Settings, get_settings
from experiment_guardian.core.logging import configure_logging
from experiment_guardian.infrastructure.database import get_session_factory
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyAgentRepository,
)


@dataclass(slots=True)
class GovernanceAgentWorker:
    processor: AgentRunProcessor
    idle_wait_seconds: float
    memory_processor: ResearchMemoryEmbeddingProcessor | None = None

    def run_forever(self) -> None:
        logger = structlog.get_logger(__name__)
        logger.info("governance_agent_worker_started")
        while True:
            try:
                processed_run = self.processor.process_once()
                processed_memory = (
                    self.memory_processor.process_once()
                    if self.memory_processor is not None
                    else False
                )
                if not processed_run and not processed_memory:
                    time.sleep(self.idle_wait_seconds)
            except Exception:
                logger.exception("governance_agent_worker_iteration_failed")
                time.sleep(self.idle_wait_seconds)


def build_agent_worker(settings: Settings | None = None) -> GovernanceAgentWorker:
    current = settings or get_settings()
    if not current.agent_enabled:
        raise ValueError("AGENT_ENABLED=false，治理 Agent Worker 不应启动")
    factory = get_session_factory()
    repository = SqlAlchemyAgentRepository()
    tools: AgentToolRegistry = get_agent_tool_registry()
    model = build_agent_chat_model(current)
    runtime = GovernanceAgentRuntime(
        factory,
        repository,
        tools,
        model,
        current,
        get_experiment_plan_service(),
    )
    worker_id = f"{socket.gethostname()}:{os.getpid()}:agent"
    processor = AgentRunProcessor(
        factory,
        repository,
        AgentRunIdentityResolver(current),
        runtime,
        current,
        worker_id=worker_id,
    )
    memory_processor = ResearchMemoryEmbeddingProcessor(
        factory,
        get_query_embedding_generator(),
        current,
        worker_id=f"{socket.gethostname()}:{os.getpid()}:research-memory",
    )
    return GovernanceAgentWorker(
        processor=processor,
        idle_wait_seconds=current.agent_run_poll_interval_seconds,
        memory_processor=memory_processor,
    )


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        build_agent_worker(settings).run_forever()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    run()
