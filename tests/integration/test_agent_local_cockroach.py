"""R16-L 在真实 CockroachDB 上验证 Agent 队列并发、恢复和死信语义。"""

import os
import time
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from experiment_guardian.application.agent import AgentRunIdentityResolver
from experiment_guardian.application.agent_runtime import (
    AgentRunProcessor,
    GovernanceAgentRuntime,
)
from experiment_guardian.application.agent_tools import AgentToolRegistry
from experiment_guardian.application.errors import ServiceUnavailableError
from experiment_guardian.application.ports import AgentChatModel
from experiment_guardian.core.config import get_settings
from experiment_guardian.domain.agent import (
    AgentChatMessage,
    AgentMessageCreateRequest,
    AgentModelEvent,
    AgentResponseFormat,
    AgentThreadCreateRequest,
    AgentToolSpec,
)
from experiment_guardian.domain.enums import AgentMessageRole, AgentRunStatus
from experiment_guardian.infrastructure.models import (
    AgentCitation,
    AgentMessage,
    AgentRun,
    AgentToolCall,
)
from experiment_guardian.infrastructure.repositories import (
    AgentRunClaim,
    SqlAlchemyAgentRepository,
    SqlAlchemyProjectRepository,
)
from tests.integration.test_agent_slice import _setup
from tests.integration.test_plan_check_cockroach import (
    _cleanup_test_database_jobs,
    _run_alembic,
)

RUN_COCKROACH_INTEGRATION = os.getenv("RUN_COCKROACH_INTEGRATION") == "1"


class _AlwaysUnavailableModel(AgentChatModel):
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
        del messages, tools, tool_choice, max_output_tokens, response_format
        raise ServiceUnavailableError("R16-L 模拟百炼持续不可用")
        yield  # pragma: no cover


def _queue_run(
    service: object,
    *,
    project_id: UUID,
    identity: object,
    content: str,
) -> UUID:
    thread = service.create_thread(  # type: ignore[attr-defined]
        project_id=project_id,
        identity=identity,
        request=AgentThreadCreateRequest(),
    )
    receipt = service.create_message(  # type: ignore[attr-defined]
        project_id=project_id,
        thread_id=thread.thread_id,
        identity=identity,
        idempotency_key=uuid4(),
        request=AgentMessageCreateRequest(content=content),
    )
    return receipt.run_id


@pytest.mark.skipif(
    not RUN_COCKROACH_INTEGRATION,
    reason="set RUN_COCKROACH_INTEGRATION=1 to run the isolated CockroachDB test",
)
def test_agent_claim_recovery_retry_and_idempotency_on_cockroach() -> None:
    base_url = make_url(get_settings().database_url)
    assert base_url.get_backend_name() == "cockroachdb"
    database_name = f"eg_agent_r16_{uuid4().hex}"
    test_url = base_url.set(database=database_name)
    rendered_test_url = test_url.render_as_string(hide_password=False)
    admin_engine = create_engine(base_url, isolation_level="AUTOCOMMIT")
    test_engine = None

    try:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
        _run_alembic(rendered_test_url, "upgrade", "head")
        test_engine = create_engine(test_url, pool_size=5, max_overflow=5)
        factory = sessionmaker(
            bind=test_engine,
            expire_on_commit=False,
            autoflush=False,
        )
        service, identity, initialized, settings = _setup(factory)
        project_id = initialized.project_id  # type: ignore[attr-defined]
        repository = SqlAlchemyAgentRepository()

        def claim_eventually(worker_id: str) -> AgentRunClaim:
            for _ in range(100):
                with factory() as session, session.begin():
                    claim = repository.claim_next(
                        session,
                        worker_id=worker_id,
                        lease_seconds=settings.agent_run_lease_seconds,
                    )
                if claim is not None:
                    return claim
                time.sleep(0.02)
            raise AssertionError(f"{worker_id} 未能在轮询窗口内 claim Agent Run")

        queued_ids = {
            _queue_run(
                service,
                project_id=project_id,
                identity=identity,
                content=f"R16-L 并发 claim {index}",
            )
            for index in range(10)
        }
        barrier = Barrier(2)
        claims_lock = Lock()
        all_claimed: set[UUID] = set()

        def claim_all(worker_id: str) -> list[UUID]:
            claimed: list[UUID] = []
            barrier.wait(timeout=10)
            for _ in range(100):
                with factory() as session, session.begin():
                    claim = repository.claim_next(
                        session,
                        worker_id=worker_id,
                        lease_seconds=settings.agent_run_lease_seconds,
                    )
                if claim is None:
                    with claims_lock:
                        if len(all_claimed) == len(queued_ids):
                            return claimed
                    time.sleep(0.02)
                    continue
                claimed.append(claim.run_id)
                with claims_lock:
                    all_claimed.add(claim.run_id)
            return claimed

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(claim_all, ["r16-worker-a", "r16-worker-b"]))
        claimed_ids = [run_id for worker_claims in claims for run_id in worker_claims]
        assert set(claimed_ids) == queued_ids
        assert len(claimed_ids) == len(set(claimed_ids)) == 10
        with factory() as session:
            claimed_runs = list(
                session.scalars(select(AgentRun).where(AgentRun.id.in_(queued_ids))).all()
            )
            assert all(item.generation == 1 for item in claimed_runs)
            assert all(item.attempt_count == 1 for item in claimed_runs)
            assert all(item.status is AgentRunStatus.RUNNING for item in claimed_runs)

        recovery_run_id = _queue_run(
            service,
            project_id=project_id,
            identity=identity,
            content="R16-L 租约恢复",
        )
        first = claim_eventually("r16-crashed-worker")
        assert first.run_id == recovery_run_id
        with factory() as session, session.begin():
            run = session.get(AgentRun, recovery_run_id)
            assert run is not None
            run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        recovered = claim_eventually("r16-recovery-worker")
        assert recovered.run_id == recovery_run_id
        assert recovered.generation == first.generation + 1
        with factory() as session, session.begin():
            assert not repository.renew_lease(
                session,
                claim=first,
                lease_seconds=settings.agent_run_lease_seconds,
            )
            assert not repository.owns_claim(session, first)
            assert repository.owns_claim(session, recovered)
        with factory() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AgentMessage)
                    .where(
                        AgentMessage.run_id == recovery_run_id,
                        AgentMessage.role == AgentMessageRole.ASSISTANT,
                    )
                )
                == 0
            )

        failure_run_id = _queue_run(
            service,
            project_id=project_id,
            identity=identity,
            content="R16-L 最大重试",
        )
        processor = AgentRunProcessor(
            factory,
            repository,
            AgentRunIdentityResolver(settings),
            GovernanceAgentRuntime(
                factory,
                repository,
                AgentToolRegistry(factory, SqlAlchemyProjectRepository()),
                _AlwaysUnavailableModel(),
                settings,
            ),
            settings,
            worker_id="r16-failing-worker",
        )
        for attempt in range(settings.agent_run_max_attempts):
            for _ in range(100):
                if processor.process_once():
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("Agent Processor 未能在轮询窗口内取得失败测试 Run")
            with factory() as session, session.begin():
                run = session.get(AgentRun, failure_run_id)
                assert run is not None
                if attempt < settings.agent_run_max_attempts - 1:
                    assert run.status is AgentRunStatus.RETRYABLE_FAILURE
                    run.available_at = datetime.now(UTC) - timedelta(seconds=1)

        with factory() as session:
            failed = session.get(AgentRun, failure_run_id)
            assert failed is not None
            assert failed.status is AgentRunStatus.DEAD_LETTER
            assert failed.attempt_count == settings.agent_run_max_attempts
            assert failed.error is not None
            assert failed.error["retryable"] is False
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AgentMessage)
                    .where(
                        AgentMessage.run_id == failure_run_id,
                        AgentMessage.role == AgentMessageRole.ASSISTANT,
                    )
                )
                == 0
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AgentCitation)
                    .where(AgentCitation.run_id == failure_run_id)
                )
                == 0
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AgentToolCall)
                    .where(AgentToolCall.run_id == failure_run_id)
                )
                == 0
            )
    finally:
        if test_engine is not None:
            test_engine.dispose()
        with admin_engine.connect() as connection:
            _cleanup_test_database_jobs(connection, database_name)
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}" CASCADE')
        admin_engine.dispose()
