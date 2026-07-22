"""R12b embedding、审核回执和恢复语义的纵向测试。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.async_review import (
    SubmissionJobProcessor,
    SubmissionReviewProcessor,
)
from experiment_guardian.application.errors import ServiceUnavailableError
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.ports import EmbeddingModelOutput
from experiment_guardian.domain.contracts import SubmissionFinalizeCommand
from experiment_guardian.domain.enums import (
    EvidenceType,
    ReviewEligibility,
    RiskSeverity,
    SubmissionStatus,
    WorkflowJobStatus,
    WorkflowJobType,
    WorkflowStatus,
    WorkflowStep,
)
from experiment_guardian.infrastructure.models import (
    ExperimentSubmission,
    PlanCheck,
    RunManifest,
    SubmissionEmbedding,
    SubmissionRisk,
    WorkflowJob,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemySubmissionRepository,
    SqlAlchemyWorkflowRepository,
)
from tests.integration.test_async_summary_slice import (
    FakeQueue,
    FakeSummaryGenerator,
    build_async_components,
)
from tests.integration.test_submission_finalize_slice import (
    finalize_identity,
    prepare_draft,
)


class FakeEmbeddingGenerator:
    model_id = "amazon.titan-embed-text-v2:0"
    dimension = 1024

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.error: Exception | None = None
        self.vector: list[float] = [1.0, *([0.0] * 1023)]

    def embed(self, input_text: str) -> EmbeddingModelOutput:
        self.calls.append(input_text)
        if self.error is not None:
            raise self.error
        return EmbeddingModelOutput(vector=self.vector, input_tokens=42)


def prepare_summary(
    factory: sessionmaker[Session],
) -> tuple[object, object, object, SubmissionFinalizeCommand, FakeQueue]:
    owner, project_id, storage, service, command, _ = prepare_draft(factory)
    storage.accept_declared_uploads()
    service.submission_finalize(command, finalize_identity(owner, project_id))
    queue = FakeQueue()
    dispatcher, summary_processor, _ = build_async_components(
        factory, queue, FakeSummaryGenerator()
    )
    assert dispatcher.dispatch_once()
    assert summary_processor.process_delivery(queue.delivery(receipt="summary"))
    assert dispatcher.dispatch_once()
    return owner, project_id, service, command, queue


def build_review_processor(
    factory: sessionmaker[Session],
    queue: FakeQueue,
    generator: FakeEmbeddingGenerator,
) -> SubmissionReviewProcessor:
    return SubmissionReviewProcessor(
        factory,
        SqlAlchemySubmissionRepository(),
        SqlAlchemyWorkflowRepository(),
        queue,
        generator,
        worker_id="review-worker",
        lease_seconds=120,
    )


def test_review_job_persists_embedding_receipt_and_needs_review(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, service, command, queue = prepare_summary(plan_check_session_factory)
    generator = FakeEmbeddingGenerator()
    processor = build_review_processor(plan_check_session_factory, queue, generator)
    router = SubmissionJobProcessor(
        plan_check_session_factory,
        SqlAlchemyWorkflowRepository(),
        queue,
        summary_processor=None,
        review_processor=processor,
    )

    assert router.process_delivery(queue.delivery(receipt="review"))

    with plan_check_session_factory() as session:
        submission = session.get(ExperimentSubmission, command.submission_id)
        embedding = session.scalar(select(SubmissionEmbedding))
        jobs = list(session.scalars(select(WorkflowJob).order_by(WorkflowJob.created_at)).all())
        assert submission is not None and embedding is not None
        assert submission.status is SubmissionStatus.NEEDS_REVIEW
        assert submission.workflow_status is WorkflowStatus.COMPLETED
        assert submission.processing_step is WorkflowStep.NEEDS_REVIEW
        assert embedding.dimension == 1024
        assert embedding.normalized
        assert embedding.input_token_count == 42
        assert len(embedding.embedding) == 1024
        assert [item.job_type for item in jobs] == [
            WorkflowJobType.SUBMISSION_SUMMARY,
            WorkflowJobType.SUBMISSION_REVIEW_PREPARATION,
        ]
        assert all(item.status is WorkflowJobStatus.SUCCEEDED for item in jobs)
        receipt = submission.review_receipt
        assert receipt["review_eligibility"] == ReviewEligibility.RESEARCHER_OR_OWNER.value
        assert receipt["can_confirm"] is True
        assert receipt["requires_owner"] is False
        assert receipt["summary_available"] is True
        assert "完整验证" in receipt["disclaimer"]

    identity = RequestIdentity(
        user_id=owner.user_id,
        team_id=owner.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"submission:read"}),
    )
    status = service.submission_get_status(
        submission_id=command.submission_id,
        identity=identity,
    )
    assert status.submission_status is SubmissionStatus.NEEDS_REVIEW
    assert status.embedding is not None
    assert status.embedding.dimension == 1024
    assert status.review_receipt is not None
    assert status.review_receipt.review_eligibility is ReviewEligibility.RESEARCHER_OR_OWNER
    assert len(status.jobs) == 2
    assert status.job is not None
    assert status.job.job_type is WorkflowJobType.SUBMISSION_REVIEW_PREPARATION
    dumped = status.model_dump(mode="json")
    assert "embedding" not in dumped["embedding"]
    assert "input_text" not in dumped["embedding"]
    replay = service.submission_finalize(command, finalize_identity(owner, project_id))
    assert replay.analysis is not None
    assert replay.analysis.workflow_status is WorkflowStatus.COMPLETED
    assert replay.analysis.submission_status is SubmissionStatus.NEEDS_REVIEW


@pytest.mark.parametrize(
    ("severity", "blocking", "eligibility", "can_confirm", "requires_owner"),
    [
        (RiskSeverity.HIGH, False, ReviewEligibility.OWNER_ONLY, True, True),
        (RiskSeverity.CRITICAL, True, ReviewEligibility.BLOCKED, False, False),
    ],
)
def test_review_receipt_enforces_high_and_critical_permissions(
    plan_check_session_factory: sessionmaker[Session],
    severity: RiskSeverity,
    blocking: bool,
    eligibility: ReviewEligibility,
    can_confirm: bool,
    requires_owner: bool,
) -> None:
    _, _, _, command, queue = prepare_summary(plan_check_session_factory)
    with plan_check_session_factory() as session, session.begin():
        session.add(
            SubmissionRisk(
                submission_id=command.submission_id,
                risk_fingerprint=("a" if severity is RiskSeverity.HIGH else "b") * 64,
                risk_type=f"TEST_{severity.value}",
                severity=severity,
                field_path="model.backbone",
                previous_value="old",
                current_value="new",
                expected_value="old",
                rule_id="test-review-rule",
                message=f"{severity.value} risk must remain visible",
                impact="review permission changes",
                evidence_type=EvidenceType.CLOUD_VERIFIED,
                evidence_source="test deterministic rule",
                collected_at=datetime.now(UTC),
                collection_tool="pytest",
                constraint_candidates=[],
                blocking=blocking,
                resolved=False,
            )
        )
    processor = build_review_processor(plan_check_session_factory, queue, FakeEmbeddingGenerator())

    assert processor.process_delivery(queue.delivery(receipt="risk-review"))

    with plan_check_session_factory() as session:
        submission = session.get(ExperimentSubmission, command.submission_id)
        assert submission is not None
        receipt = submission.review_receipt
        assert receipt["review_eligibility"] == eligibility.value
        assert receipt["can_confirm"] is can_confirm
        assert receipt["requires_owner"] is requires_owner
        assert receipt["highlighted_risks"][0]["severity"] == severity.value
        assert submission.status is SubmissionStatus.NEEDS_REVIEW


def test_crash_after_embedding_commit_resumes_without_second_model_call(
    plan_check_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, _, command, queue = prepare_summary(plan_check_session_factory)
    generator = FakeEmbeddingGenerator()
    processor = build_review_processor(plan_check_session_factory, queue, generator)
    original = processor._persist_receipt

    def crash_before_receipt(*_: object) -> None:
        raise RuntimeError("database unavailable before receipt commit")

    monkeypatch.setattr(processor, "_persist_receipt", crash_before_receipt)
    with pytest.raises(RuntimeError, match="before receipt"):
        processor.process_delivery(queue.delivery(receipt="crashed-review"))
    assert len(generator.calls) == 1
    with plan_check_session_factory() as session, session.begin():
        embedding_count = session.scalar(select(func.count()).select_from(SubmissionEmbedding))
        job = session.scalar(
            select(WorkflowJob).where(
                WorkflowJob.job_type == WorkflowJobType.SUBMISSION_REVIEW_PREPARATION
            )
        )
        assert embedding_count == 1
        assert job is not None
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    monkeypatch.setattr(processor, "_persist_receipt", original)
    assert processor.process_delivery(queue.delivery(receipt="replacement-review"))
    assert len(generator.calls) == 1
    with plan_check_session_factory() as session:
        submission = session.get(ExperimentSubmission, command.submission_id)
        assert submission is not None
        assert submission.status is SubmissionStatus.NEEDS_REVIEW
        assert session.scalar(select(func.count()).select_from(SubmissionEmbedding)) == 1


def test_review_dependency_dead_letter_can_be_rearmed_without_rechecking_s3(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, service, command, queue = prepare_summary(plan_check_session_factory)
    with plan_check_session_factory() as session, session.begin():
        job = session.scalar(
            select(WorkflowJob).where(
                WorkflowJob.job_type == WorkflowJobType.SUBMISSION_REVIEW_PREPARATION
            )
        )
        assert job is not None
        job.max_attempts = 1
    generator = FakeEmbeddingGenerator()
    generator.error = ServiceUnavailableError("bedrock unavailable")
    processor = build_review_processor(plan_check_session_factory, queue, generator)

    assert not processor.process_delivery(queue.delivery(receipt="review-dead"))

    with plan_check_session_factory() as session:
        job = session.scalar(
            select(WorkflowJob).where(
                WorkflowJob.job_type == WorkflowJobType.SUBMISSION_REVIEW_PREPARATION
            )
        )
        assert job is not None and job.status is WorkflowJobStatus.DEAD_LETTER
    resumed = service.submission_finalize(
        SubmissionFinalizeCommand(
            submission_id=command.submission_id,
            idempotency_key=uuid4(),
        ),
        finalize_identity(owner, project_id),
    )
    assert resumed.analysis is not None
    assert resumed.analysis.workflow_status is WorkflowStatus.QUEUED
    with plan_check_session_factory() as session:
        job = session.scalar(
            select(WorkflowJob).where(
                WorkflowJob.job_type == WorkflowJobType.SUBMISSION_REVIEW_PREPARATION
            )
        )
        assert job is not None
        assert job.generation == 2
        assert job.status is WorkflowJobStatus.PENDING_DISPATCH


def test_invalid_embedding_never_enters_review_state(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    _, _, _, command, queue = prepare_summary(plan_check_session_factory)
    with plan_check_session_factory() as session, session.begin():
        job = session.scalar(
            select(WorkflowJob).where(
                WorkflowJob.job_type == WorkflowJobType.SUBMISSION_REVIEW_PREPARATION
            )
        )
        assert job is not None
        job.max_attempts = 1
    generator = FakeEmbeddingGenerator()
    generator.vector = [0.0] * 1024
    processor = build_review_processor(plan_check_session_factory, queue, generator)

    assert not processor.process_delivery(queue.delivery(receipt="invalid-vector"))

    with plan_check_session_factory() as session:
        submission = session.get(ExperimentSubmission, command.submission_id)
        job = session.scalar(
            select(WorkflowJob).where(
                WorkflowJob.job_type == WorkflowJobType.SUBMISSION_REVIEW_PREPARATION
            )
        )
        assert submission is not None and job is not None
        assert submission.status is SubmissionStatus.PROCESSING
        assert submission.workflow_status is WorkflowStatus.RETRYABLE_FAILURE
        assert submission.review_receipt is None
        assert job.status is WorkflowJobStatus.DEAD_LETTER
        assert session.scalar(select(func.count()).select_from(SubmissionEmbedding)) == 0


def test_embedding_source_drift_is_terminal_and_does_not_overwrite_vector(
    plan_check_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, _, command, queue = prepare_summary(plan_check_session_factory)
    generator = FakeEmbeddingGenerator()
    processor = build_review_processor(plan_check_session_factory, queue, generator)
    original = processor._persist_receipt

    monkeypatch.setattr(
        processor,
        "_persist_receipt",
        lambda *_: (_ for _ in ()).throw(RuntimeError("crash before receipt")),
    )
    with pytest.raises(RuntimeError, match="before receipt"):
        processor.process_delivery(queue.delivery(receipt="source-before-drift"))
    with plan_check_session_factory() as session, session.begin():
        submission = session.get(ExperimentSubmission, command.submission_id)
        assert submission is not None
        manifest = session.get(RunManifest, submission.run_manifest_id)
        assert manifest is not None
        plan = session.get(PlanCheck, manifest.plan_check_id)
        job = session.scalar(
            select(WorkflowJob).where(
                WorkflowJob.job_type == WorkflowJobType.SUBMISSION_REVIEW_PREPARATION
            )
        )
        assert plan is not None and job is not None
        plan.planned_changes = [
            *plan.planned_changes,
            {
                "parameter_path": "test.drift",
                "previous_value": 1,
                "current_value": 2,
                "protection_level": "EXPERIMENT_VARIABLE",
            },
        ]
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    monkeypatch.setattr(processor, "_persist_receipt", original)
    assert processor.process_delivery(queue.delivery(receipt="source-after-drift"))

    with plan_check_session_factory() as session:
        submission = session.get(ExperimentSubmission, command.submission_id)
        job = session.scalar(
            select(WorkflowJob).where(
                WorkflowJob.job_type == WorkflowJobType.SUBMISSION_REVIEW_PREPARATION
            )
        )
        assert submission is not None and job is not None
        assert submission.status is SubmissionStatus.FAILED
        assert submission.workflow_status is WorkflowStatus.TERMINAL_FAILURE
        assert submission.review_receipt is None
        assert job.status is WorkflowJobStatus.FAILED
        assert len(generator.calls) == 1
        assert session.scalar(select(func.count()).select_from(SubmissionEmbedding)) == 1
