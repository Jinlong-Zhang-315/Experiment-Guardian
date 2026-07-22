"""R12a Outbox、SQS 摘要、状态查询和恢复的纵向测试。"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.async_summary import (
    OutboxDispatcher,
    SubmissionReviewScheduler,
    SubmissionSummaryProcessor,
    SubmissionSummaryScheduler,
)
from experiment_guardian.application.errors import AuthorizationError, ServiceUnavailableError
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.ports import (
    QueueDelivery,
    SummaryModelOutput,
)
from experiment_guardian.domain.contracts import SubmissionFinalizeCommand, SummaryQueueEnvelope
from experiment_guardian.domain.enums import (
    OutboxStatus,
    SubmissionStatus,
    TeamRole,
    WorkflowJobStatus,
    WorkflowJobType,
    WorkflowStatus,
    WorkflowStep,
)
from experiment_guardian.infrastructure.models import (
    AuditLog,
    ExperimentSubmission,
    OutboxEvent,
    TeamMember,
    User,
    WorkflowJob,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemySubmissionRepository,
    SqlAlchemyWorkflowRepository,
)
from tests.integration.test_submission_finalize_slice import (
    finalize_identity,
    prepare_draft,
)


class FakeQueue:
    def __init__(self) -> None:
        self.sent: list[SummaryQueueEnvelope] = []
        self.deleted: list[str] = []
        self.visibility: list[tuple[str, int]] = []
        self.send_error: Exception | None = None

    def send(self, envelope: SummaryQueueEnvelope) -> str:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(envelope)
        return f"message-{len(self.sent)}"

    def receive(self, *, max_messages: int = 1) -> Sequence[QueueDelivery]:
        del max_messages
        return []

    def delete(self, receipt_handle: str) -> None:
        self.deleted.append(receipt_handle)

    def change_visibility(self, receipt_handle: str, timeout_seconds: int) -> None:
        self.visibility.append((receipt_handle, timeout_seconds))

    def delivery(self, index: int = -1, *, receipt: str | None = None) -> QueueDelivery:
        envelope = self.sent[index]
        return QueueDelivery(
            message_id=f"delivery-{uuid4()}",
            receipt_handle=receipt or f"receipt-{uuid4()}",
            body=envelope.model_dump_json(),
            receive_count=1,
        )


class FakeSummaryGenerator:
    model_id = "fake.summary-v1"

    def __init__(self, text: str = "目标、运行条件、结果和既有风险摘要。") -> None:
        self.text = text
        self.error: Exception | None = None
        self.calls: list[tuple[str, str]] = []

    def generate(self, *, system_prompt: str, user_prompt: str) -> SummaryModelOutput:
        self.calls.append((system_prompt, user_prompt))
        if self.error is not None:
            raise self.error
        return SummaryModelOutput(text=self.text, input_tokens=120, output_tokens=30)


def build_async_components(
    factory: sessionmaker[Session],
    queue: FakeQueue,
    generator: FakeSummaryGenerator,
) -> tuple[OutboxDispatcher, SubmissionSummaryProcessor, SubmissionSummaryScheduler]:
    workflows = SqlAlchemyWorkflowRepository()
    scheduler = SubmissionSummaryScheduler(factory, workflows, max_attempts=5)
    review_scheduler = SubmissionReviewScheduler(factory, workflows, max_attempts=5)
    return (
        OutboxDispatcher(
            factory,
            workflows,
            queue,
            worker_id="test-worker",
            lease_seconds=120,
        ),
        SubmissionSummaryProcessor(
            factory,
            SqlAlchemySubmissionRepository(),
            workflows,
            queue,
            generator,
            review_scheduler,
            worker_id="test-worker",
            lease_seconds=120,
        ),
        scheduler,
    )


def test_outbox_dispatch_and_summary_are_idempotent(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, storage, service, command, _ = prepare_draft(plan_check_session_factory)
    storage.accept_declared_uploads()
    finalized = service.submission_finalize(command, finalize_identity(owner, project_id))
    assert finalized.analysis is not None
    assert finalized.analysis.workflow_status is WorkflowStatus.QUEUED

    queue = FakeQueue()
    generator = FakeSummaryGenerator()
    dispatcher, processor, _ = build_async_components(plan_check_session_factory, queue, generator)

    assert dispatcher.dispatch_once()
    assert not dispatcher.dispatch_once()
    delivery = queue.delivery(receipt="first-receipt")
    assert processor.process_delivery(delivery)

    with plan_check_session_factory() as session:
        submission = session.get(ExperimentSubmission, command.submission_id)
        job = session.scalar(
            select(WorkflowJob).where(WorkflowJob.job_type == WorkflowJobType.SUBMISSION_SUMMARY)
        )
        event = session.scalar(select(OutboxEvent))
        assert submission is not None and job is not None and event is not None
        assert submission.status is SubmissionStatus.PROCESSING
        assert submission.workflow_status is WorkflowStatus.QUEUED
        assert submission.processing_step is WorkflowStep.SUMMARY_GENERATION
        assert submission.generated_summary["text"] == generator.text
        assert submission.generated_summary["model_id"] == generator.model_id
        assert len(submission.generated_summary["source_hash"]) == 64
        assert job.status is WorkflowJobStatus.SUCCEEDED
        assert event.status is OutboxStatus.PUBLISHED

    system_prompt, user_prompt = generator.calls[0]
    assert "Do not create risks" in system_prompt
    assert "AUTO_FROM_INTENT" not in user_prompt
    assert "<UNTRUSTED_STRUCTURED_FACTS>" in user_prompt
    assert 'artifact_type":"LOG' not in user_prompt
    assert queue.deleted == ["first-receipt"]

    replay = service.submission_finalize(command, finalize_identity(owner, project_id))
    assert replay.analysis is not None
    assert replay.analysis.workflow_status is WorkflowStatus.QUEUED
    assert replay.analysis.processing_step is WorkflowStep.SUMMARY_GENERATION

    # Standard Queue 允许重复投递；成功记录不得再次调用 Bedrock。
    assert processor.process_delivery(queue.delivery(receipt="duplicate-receipt"))
    assert len(generator.calls) == 1
    assert queue.deleted[-1] == "duplicate-receipt"


def test_retryable_bedrock_failure_can_be_rearmed_with_new_finalize_key(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, storage, service, command, _ = prepare_draft(plan_check_session_factory)
    storage.accept_declared_uploads()
    identity = finalize_identity(owner, project_id)
    service.submission_finalize(command, identity)

    queue = FakeQueue()
    generator = FakeSummaryGenerator()
    generator.error = ServiceUnavailableError("bedrock throttled")
    dispatcher, processor, _ = build_async_components(plan_check_session_factory, queue, generator)
    assert dispatcher.dispatch_once()
    old_delivery = queue.delivery(receipt="old-generation")
    assert not processor.process_delivery(old_delivery)
    assert queue.visibility[-1] == ("old-generation", 30)

    with plan_check_session_factory() as session:
        job = session.scalar(select(WorkflowJob))
        submission = session.get(ExperimentSubmission, command.submission_id)
        assert job is not None and submission is not None
        assert job.status is WorkflowJobStatus.RETRYABLE_FAILURE
        assert job.attempt_count == 1
        assert submission.workflow_status is WorkflowStatus.RETRYABLE_FAILURE

    inspected = len(storage.inspection_calls)
    resumed = service.submission_finalize(
        SubmissionFinalizeCommand(
            submission_id=command.submission_id,
            idempotency_key=uuid4(),
        ),
        identity,
    )
    assert resumed.analysis is not None
    assert resumed.analysis.workflow_status is WorkflowStatus.QUEUED
    assert len(storage.inspection_calls) == inspected

    with plan_check_session_factory() as session:
        job = session.scalar(select(WorkflowJob))
        assert job is not None
        assert job.generation == 2
        assert job.attempt_count == 0
        assert job.status is WorkflowJobStatus.PENDING_DISPATCH
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 2
        audit = session.scalar(
            select(AuditLog).where(AuditLog.action == "submission.analysis.resume")
        )
        assert audit is not None
        assert audit.after_value["token_id"] == str(identity.token_id)

    # generation=1 的旧消息只会被确认，不会再次调用模型。
    assert processor.process_delivery(old_delivery)
    assert len(generator.calls) == 1


def test_terminal_source_corruption_does_not_retry_model(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, storage, service, command, _ = prepare_draft(plan_check_session_factory)
    storage.accept_declared_uploads()
    service.submission_finalize(command, finalize_identity(owner, project_id))
    with plan_check_session_factory() as session, session.begin():
        submission = session.get(ExperimentSubmission, command.submission_id)
        assert submission is not None
        submission.analysis_snapshot = {"schema_version": 1}

    queue = FakeQueue()
    generator = FakeSummaryGenerator()
    dispatcher, processor, _ = build_async_components(plan_check_session_factory, queue, generator)
    assert dispatcher.dispatch_once()
    assert processor.process_delivery(queue.delivery(receipt="terminal"))

    with plan_check_session_factory() as session:
        submission = session.get(ExperimentSubmission, command.submission_id)
        job = session.scalar(select(WorkflowJob))
        assert submission is not None and job is not None
        assert submission.status is SubmissionStatus.FAILED
        assert submission.workflow_status is WorkflowStatus.TERMINAL_FAILURE
        assert job.status is WorkflowJobStatus.FAILED
        assert not generator.calls


@pytest.mark.parametrize("invalid_text", ["", "x" * 3001])
def test_invalid_model_output_reaches_dead_letter_and_can_be_rearmed(
    plan_check_session_factory: sessionmaker[Session],
    invalid_text: str,
) -> None:
    owner, project_id, storage, service, command, _ = prepare_draft(plan_check_session_factory)
    storage.accept_declared_uploads()
    identity = finalize_identity(owner, project_id)
    service.submission_finalize(command, identity)
    with plan_check_session_factory() as session, session.begin():
        job = session.scalar(select(WorkflowJob))
        assert job is not None
        job.max_attempts = 1

    queue = FakeQueue()
    generator = FakeSummaryGenerator(invalid_text)
    dispatcher, processor, _ = build_async_components(plan_check_session_factory, queue, generator)
    assert dispatcher.dispatch_once()
    assert not processor.process_delivery(queue.delivery(receipt="dead-letter"))

    with plan_check_session_factory() as session:
        job = session.scalar(select(WorkflowJob))
        submission = session.get(ExperimentSubmission, command.submission_id)
        assert job is not None and submission is not None
        assert job.status is WorkflowJobStatus.DEAD_LETTER
        assert submission.workflow_status is WorkflowStatus.RETRYABLE_FAILURE
        assert submission.status is SubmissionStatus.PROCESSING
    assert queue.deleted == []
    assert queue.visibility[-1] == ("dead-letter", 120)

    service.submission_finalize(
        SubmissionFinalizeCommand(
            submission_id=command.submission_id,
            idempotency_key=uuid4(),
        ),
        identity,
    )
    with plan_check_session_factory() as session:
        job = session.scalar(select(WorkflowJob))
        assert job is not None
        assert job.generation == 2
        assert job.status is WorkflowJobStatus.PENDING_DISPATCH


def test_reconciliation_and_publish_crash_preserve_at_least_once_delivery(
    plan_check_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, project_id, storage, service, command, _ = prepare_draft(plan_check_session_factory)
    storage.accept_declared_uploads()
    service.submission_finalize(command, finalize_identity(owner, project_id))
    with plan_check_session_factory() as session, session.begin():
        session.execute(delete(OutboxEvent))
        session.execute(delete(WorkflowJob))
        submission = session.get(ExperimentSubmission, command.submission_id)
        assert submission is not None
        submission.workflow_status = WorkflowStatus.AWAITING_ENRICHMENT

    queue = FakeQueue()
    generator = FakeSummaryGenerator()
    dispatcher, _, scheduler = build_async_components(plan_check_session_factory, queue, generator)
    assert scheduler.reconcile() == 1
    assert scheduler.reconcile() == 0

    original_mark = dispatcher._mark_published

    def crash_after_send(*_: object) -> None:
        raise RuntimeError("worker crashed after SQS send")

    monkeypatch.setattr(dispatcher, "_mark_published", crash_after_send)
    with pytest.raises(RuntimeError, match="crashed"):
        dispatcher.dispatch_once()
    assert len(queue.sent) == 1
    with plan_check_session_factory() as session, session.begin():
        event = session.scalar(select(OutboxEvent))
        assert event is not None
        event.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    monkeypatch.setattr(dispatcher, "_mark_published", original_mark)
    assert dispatcher.dispatch_once()
    assert len(queue.sent) == 2
    assert queue.sent[0] == queue.sent[1]


def test_outbox_publish_failure_is_persisted_and_retried(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, storage, service, command, _ = prepare_draft(plan_check_session_factory)
    storage.accept_declared_uploads()
    service.submission_finalize(command, finalize_identity(owner, project_id))
    queue = FakeQueue()
    queue.send_error = ServiceUnavailableError("sqs unavailable")
    dispatcher, _, _ = build_async_components(
        plan_check_session_factory, queue, FakeSummaryGenerator()
    )

    assert dispatcher.dispatch_once()
    with plan_check_session_factory() as session, session.begin():
        event = session.scalar(select(OutboxEvent))
        assert event is not None
        assert event.status is OutboxStatus.PENDING
        assert event.attempt_count == 1
        assert event.last_error["code"] == "OUTBOX_PUBLISH_FAILED"
        event.available_at = datetime.now(UTC) - timedelta(seconds=1)

    queue.send_error = None
    assert dispatcher.dispatch_once()
    with plan_check_session_factory() as session:
        event = session.scalar(select(OutboxEvent))
        assert event is not None
        assert event.status is OutboxStatus.PUBLISHED


def test_late_outbox_receipt_does_not_regress_completed_job(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, storage, service, command, _ = prepare_draft(plan_check_session_factory)
    storage.accept_declared_uploads()
    service.submission_finalize(command, finalize_identity(owner, project_id))
    queue = FakeQueue()
    dispatcher, processor, _ = build_async_components(
        plan_check_session_factory, queue, FakeSummaryGenerator()
    )
    claim = dispatcher._claim()
    assert claim is not None
    queue.send(SummaryQueueEnvelope.model_validate(claim.payload))
    assert processor.process_delivery(queue.delivery(receipt="first-delivery"))

    dispatcher._mark_published(claim, "late-message-id")

    with plan_check_session_factory() as session:
        summary_job = session.scalar(
            select(WorkflowJob).where(WorkflowJob.job_type == WorkflowJobType.SUBMISSION_SUMMARY)
        )
        assert summary_job is not None
        assert summary_job.status is WorkflowJobStatus.SUCCEEDED


def test_crash_after_model_output_can_repeat_generation_but_persists_one_summary(
    plan_check_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, project_id, storage, service, command, _ = prepare_draft(plan_check_session_factory)
    storage.accept_declared_uploads()
    service.submission_finalize(command, finalize_identity(owner, project_id))
    queue = FakeQueue()
    generator = FakeSummaryGenerator()
    dispatcher, processor, _ = build_async_components(plan_check_session_factory, queue, generator)
    assert dispatcher.dispatch_once()
    original_persist = processor._persist_summary

    def crash_before_commit(*_: object) -> None:
        raise RuntimeError("database unavailable before summary commit")

    monkeypatch.setattr(processor, "_persist_summary", crash_before_commit)
    with pytest.raises(RuntimeError, match="before summary commit"):
        processor.process_delivery(queue.delivery(receipt="crashed-worker"))
    assert len(generator.calls) == 1
    assert queue.deleted == []
    with plan_check_session_factory() as session, session.begin():
        job = session.scalar(select(WorkflowJob))
        assert job is not None
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    monkeypatch.setattr(processor, "_persist_summary", original_persist)
    assert processor.process_delivery(queue.delivery(receipt="replacement-worker"))
    assert len(generator.calls) == 2
    with plan_check_session_factory() as session:
        submission = session.get(ExperimentSubmission, command.submission_id)
        assert submission is not None
        assert submission.generated_summary["text"] == generator.text
        assert session.scalar(select(func.count()).select_from(WorkflowJob)) == 2


def test_submission_status_requires_submitter_or_owner(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, storage, service, command, _ = prepare_draft(plan_check_session_factory)
    storage.accept_declared_uploads()
    service.submission_finalize(command, finalize_identity(owner, project_id))
    owner_read = RequestIdentity(
        user_id=owner.user_id,
        team_id=owner.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"submission:read"}),
    )
    status = service.submission_get_status(
        submission_id=command.submission_id,
        identity=owner_read,
    )
    assert status.workflow_status is WorkflowStatus.QUEUED
    assert status.job is not None
    assert status.generated_summary is None
    assert "不是验证证据" in status.disclaimer

    with plan_check_session_factory() as session, session.begin():
        other = User(name="Other", email=f"other-{uuid4()}@example.com")
        session.add(other)
        session.flush()
        session.add(
            TeamMember(
                team_id=owner.team_id,
                user_id=other.id,
                role=TeamRole.RESEARCHER,
            )
        )
        other_id = other.id
    other_read = RequestIdentity(
        user_id=other_id,
        team_id=owner.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"submission:read"}),
    )
    with pytest.raises(AuthorizationError, match="原提交者或项目 Owner"):
        service.submission_get_status(
            submission_id=command.submission_id,
            identity=other_read,
        )
