"""CockroachDB/SQLite Outbox 本地队列的 claim、恢复和死信语义。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.async_summary import (
    OutboxDispatcher,
    SubmissionReviewScheduler,
    SubmissionSummaryProcessor,
)
from experiment_guardian.application.errors import ServiceUnavailableError
from experiment_guardian.domain.enums import OutboxStatus, WorkflowJobStatus
from experiment_guardian.infrastructure.models import OutboxEvent, WorkflowJob
from experiment_guardian.infrastructure.queue import DatabaseOutboxQueue
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemySubmissionRepository,
    SqlAlchemyWorkflowRepository,
)
from tests.integration.test_async_summary_slice import FakeSummaryGenerator
from tests.integration.test_submission_finalize_slice import (
    finalize_identity,
    prepare_draft,
)


def _processor(
    factory: sessionmaker[Session],
    queue: DatabaseOutboxQueue,
    generator: FakeSummaryGenerator,
    worker_id: str,
) -> SubmissionSummaryProcessor:
    workflows = SqlAlchemyWorkflowRepository()
    return SubmissionSummaryProcessor(
        factory,
        SqlAlchemySubmissionRepository(),
        workflows,
        queue,
        generator,
        SubmissionReviewScheduler(factory, workflows, max_attempts=5),
        worker_id=worker_id,
        lease_seconds=120,
    )


def _prepared_job(factory: sessionmaker[Session]) -> None:
    owner, project_id, storage, service, command, _ = prepare_draft(factory)
    storage.accept_declared_uploads()
    service.submission_finalize(command, finalize_identity(owner, project_id))


def test_database_queue_claims_once_and_marks_completed(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    _prepared_job(plan_check_session_factory)
    workflows = SqlAlchemyWorkflowRepository()
    queue = DatabaseOutboxQueue(
        plan_check_session_factory, worker_id="db-worker-1", lease_seconds=120
    )
    dispatcher = OutboxDispatcher(
        plan_check_session_factory,
        workflows,
        queue,
        worker_id="db-worker-1",
        lease_seconds=120,
    )
    assert dispatcher.dispatch_once()
    deliveries = queue.receive(max_messages=10)
    assert len(deliveries) == 1

    competing = DatabaseOutboxQueue(
        plan_check_session_factory, worker_id="db-worker-2", lease_seconds=120
    )
    assert competing.receive(max_messages=10) == []

    generator = FakeSummaryGenerator()
    assert _processor(plan_check_session_factory, queue, generator, "db-worker-1").process_delivery(
        deliveries[0]
    )
    with plan_check_session_factory() as session:
        event = session.scalar(select(OutboxEvent).order_by(OutboxEvent.created_at))
        job = session.scalar(select(WorkflowJob).order_by(WorkflowJob.created_at))
        assert event is not None and event.status is OutboxStatus.COMPLETED
        assert job is not None and job.status is WorkflowJobStatus.SUCCEEDED
    assert len(generator.calls) == 1


def test_database_queue_recovers_expired_delivery_lease(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    _prepared_job(plan_check_session_factory)
    queue1 = DatabaseOutboxQueue(
        plan_check_session_factory, worker_id="crashed-worker", lease_seconds=120
    )
    dispatcher = OutboxDispatcher(
        plan_check_session_factory,
        SqlAlchemyWorkflowRepository(),
        queue1,
        worker_id="crashed-worker",
        lease_seconds=120,
    )
    assert dispatcher.dispatch_once()
    assert len(queue1.receive()) == 1
    with plan_check_session_factory() as session, session.begin():
        event = session.scalar(select(OutboxEvent))
        assert event is not None
        event.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    queue2 = DatabaseOutboxQueue(
        plan_check_session_factory, worker_id="recovery-worker", lease_seconds=120
    )
    recovered = queue2.receive()
    assert len(recovered) == 1
    generator = FakeSummaryGenerator()
    assert _processor(
        plan_check_session_factory, queue2, generator, "recovery-worker"
    ).process_delivery(recovered[0])
    assert len(generator.calls) == 1


def test_database_queue_marks_permanent_model_failure_dead_letter(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    _prepared_job(plan_check_session_factory)
    with plan_check_session_factory() as session, session.begin():
        job = session.scalar(select(WorkflowJob))
        assert job is not None
        job.max_attempts = 1

    queue = DatabaseOutboxQueue(
        plan_check_session_factory, worker_id="failing-worker", lease_seconds=120
    )
    dispatcher = OutboxDispatcher(
        plan_check_session_factory,
        SqlAlchemyWorkflowRepository(),
        queue,
        worker_id="failing-worker",
        lease_seconds=120,
    )
    assert dispatcher.dispatch_once()
    delivery = queue.receive()[0]
    generator = FakeSummaryGenerator()
    generator.error = ServiceUnavailableError("model unavailable")
    assert not _processor(
        plan_check_session_factory, queue, generator, "failing-worker"
    ).process_delivery(delivery)
    with plan_check_session_factory() as session:
        event = session.scalar(select(OutboxEvent))
        job = session.scalar(select(WorkflowJob))
        assert event is not None and event.status is OutboxStatus.DEAD_LETTER
        assert job is not None and job.status is WorkflowJobStatus.DEAD_LETTER
        assert event.attempt_count == 1
        assert job.attempt_count == 1


def test_database_queue_retries_exactly_to_configured_max_attempts(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    _prepared_job(plan_check_session_factory)
    with plan_check_session_factory() as session, session.begin():
        job = session.scalar(select(WorkflowJob))
        assert job is not None
        job.max_attempts = 2
    queue = DatabaseOutboxQueue(
        plan_check_session_factory, worker_id="retry-worker", lease_seconds=120
    )
    dispatcher = OutboxDispatcher(
        plan_check_session_factory,
        SqlAlchemyWorkflowRepository(),
        queue,
        worker_id="retry-worker",
        lease_seconds=120,
    )
    assert dispatcher.dispatch_once()
    generator = FakeSummaryGenerator()
    generator.error = ServiceUnavailableError("model unavailable")
    processor = _processor(plan_check_session_factory, queue, generator, "retry-worker")

    assert not processor.process_delivery(queue.receive()[0])
    with plan_check_session_factory() as session, session.begin():
        event = session.scalar(select(OutboxEvent))
        job = session.scalar(select(WorkflowJob))
        assert event is not None and job is not None
        assert job.status is WorkflowJobStatus.RETRYABLE_FAILURE
        event.available_at = datetime.now(UTC) - timedelta(seconds=1)
        job.available_at = datetime.now(UTC) - timedelta(seconds=1)
    assert not processor.process_delivery(queue.receive()[0])
    with plan_check_session_factory() as session:
        event = session.scalar(select(OutboxEvent))
        job = session.scalar(select(WorkflowJob))
        assert event is not None and event.status is OutboxStatus.DEAD_LETTER
        assert job is not None and job.status is WorkflowJobStatus.DEAD_LETTER
        assert event.attempt_count == 2
        assert job.attempt_count == 2


def test_database_queue_stale_generation_cannot_call_model_or_write_new_job(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    _prepared_job(plan_check_session_factory)
    queue = DatabaseOutboxQueue(
        plan_check_session_factory, worker_id="old-worker", lease_seconds=120
    )
    dispatcher = OutboxDispatcher(
        plan_check_session_factory,
        SqlAlchemyWorkflowRepository(),
        queue,
        worker_id="old-worker",
        lease_seconds=120,
    )
    assert dispatcher.dispatch_once()
    delivery = queue.receive()[0]
    with plan_check_session_factory() as session, session.begin():
        job = session.scalar(select(WorkflowJob))
        assert job is not None
        job.generation += 1
        new_generation = job.generation

    generator = FakeSummaryGenerator()
    assert _processor(
        plan_check_session_factory, queue, generator, "old-worker"
    ).process_delivery(delivery)
    assert generator.calls == []
    with plan_check_session_factory() as session:
        event = session.scalar(select(OutboxEvent))
        job = session.scalar(select(WorkflowJob))
        assert event is not None and event.status is OutboxStatus.COMPLETED
        assert job is not None and job.generation == new_generation
