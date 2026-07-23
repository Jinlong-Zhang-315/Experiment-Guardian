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
from experiment_guardian.core.config import Settings, get_settings
from experiment_guardian.core.logging import configure_logging
from experiment_guardian.infrastructure.bailian import BailianAgentChatModel
from experiment_guardian.infrastructure.database import get_session_factory
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyAgentRepository,
    SqlAlchemyProjectRepository,
)


@dataclass(slots=True)
class GovernanceAgentWorker:
    processor: AgentRunProcessor
    idle_wait_seconds: float

    def run_forever(self) -> None:
        logger = structlog.get_logger(__name__)
        logger.info("governance_agent_worker_started")
        while True:
            try:
                if not self.processor.process_once():
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
    tools = AgentToolRegistry(factory, SqlAlchemyProjectRepository())
    api_key = current.bailian_api_key
    model = BailianAgentChatModel(
        api_key=api_key.get_secret_value() if api_key else "",
        base_url=current.bailian_base_url,
        model_id=current.bailian_agent_model,
        connect_timeout_seconds=current.bailian_connect_timeout_seconds,
        read_timeout_seconds=max(
            current.bailian_read_timeout_seconds,
            current.agent_max_wall_seconds,
        ),
    )
    runtime = GovernanceAgentRuntime(
        factory,
        repository,
        tools,
        model,
        current,
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
    return GovernanceAgentWorker(
        processor=processor,
        idle_wait_seconds=current.agent_run_poll_interval_seconds,
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
