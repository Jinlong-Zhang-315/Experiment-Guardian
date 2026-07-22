"""本地适配器组合后的 Submission -> 正式 Experiment 纵向闭环。"""

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.experiments import ExperimentReviewService
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.domain.administration import SubmissionDecisionRequest
from experiment_guardian.domain.enums import (
    ApprovalDecision,
    OutboxStatus,
    SubmissionStatus,
    WorkflowJobStatus,
)
from experiment_guardian.infrastructure.bailian import (
    BailianEmbeddingGenerator,
    BailianSummaryGenerator,
)
from experiment_guardian.infrastructure.models import (
    Artifact,
    Experiment,
    ExperimentSubmission,
    Memory,
    OutboxEvent,
    SubmissionEmbedding,
    WorkflowJob,
)
from experiment_guardian.infrastructure.queue import DatabaseOutboxQueue
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyGovernanceRepository,
    SqlAlchemyProjectRepository,
    SqlAlchemySubmissionRepository,
)
from tests.integration.test_async_review_slice import build_review_processor
from tests.integration.test_async_summary_slice import build_async_components
from tests.integration.test_submission_finalize_slice import finalize_identity, prepare_draft


def test_local_database_queue_and_bailian_mock_complete_formal_experiment(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, storage, guardian, finalize, _ = prepare_draft(
        plan_check_session_factory
    )
    storage.accept_declared_uploads()
    guardian.submission_finalize(finalize, finalize_identity(owner, project_id))

    summary_http = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "目标、受控条件、结果和既有风险的事实摘要。",
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 8},
                },
            )
        )
    )
    embedding_http = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "data": [{"embedding": [1.0, *([0.0] * 1023)]}],
                    "usage": {"total_tokens": 30},
                },
            )
        )
    )
    try:
        summary = BailianSummaryGenerator(
            api_key="mock-key",
            base_url="https://bailian.mock/v1",
            model_id="mock-summary",
            client=summary_http,
        )
        embedding = BailianEmbeddingGenerator(
            api_key="mock-key",
            base_url="https://bailian.mock/v1",
            model_id="mock-embedding-1024",
            client=embedding_http,
        )
        queue = DatabaseOutboxQueue(
            plan_check_session_factory,
            worker_id="test-worker",
            lease_seconds=120,
        )
        dispatcher, summary_processor, _ = build_async_components(
            plan_check_session_factory, queue, summary  # type: ignore[arg-type]
        )
        assert dispatcher.dispatch_once()
        summary_delivery = queue.receive(max_messages=10)
        assert len(summary_delivery) == 1
        assert summary_processor.process_delivery(summary_delivery[0])

        assert dispatcher.dispatch_once()
        review_delivery = queue.receive(max_messages=10)
        assert len(review_delivery) == 1
        review_processor = build_review_processor(
            plan_check_session_factory, queue, embedding  # type: ignore[arg-type]
        )
        assert review_processor.process_delivery(review_delivery[0])
    finally:
        summary_http.close()
        embedding_http.close()

    reviewer = RequestIdentity(
        user_id=owner.user_id,
        team_id=owner.team_id,
        token_id=owner.token_id,
        project_id=project_id,
        scopes=frozenset({"submission:review"}),
    )
    review_service = ExperimentReviewService(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
        SqlAlchemyGovernanceRepository(),
        SqlAlchemySubmissionRepository(),
    )
    key = finalize.idempotency_key
    result = review_service.decide(
        identity=reviewer,
        project_id=project_id,
        submission_id=finalize.submission_id,
        idempotency_key=key,
        request=SubmissionDecisionRequest(decision=ApprovalDecision.APPROVED),
    )
    replay = review_service.decide(
        identity=reviewer,
        project_id=project_id,
        submission_id=finalize.submission_id,
        idempotency_key=key,
        request=SubmissionDecisionRequest(decision=ApprovalDecision.APPROVED),
    )
    assert replay == result

    with plan_check_session_factory() as session:
        submission = session.get(ExperimentSubmission, finalize.submission_id)
        stored_embedding = session.scalar(select(SubmissionEmbedding))
        memory = session.scalar(select(Memory))
        events = session.scalars(select(OutboxEvent)).all()
        jobs = session.scalars(select(WorkflowJob)).all()
        assert submission is not None and submission.status is SubmissionStatus.APPROVED
        assert stored_embedding is not None and stored_embedding.provider == "bailian"
        assert memory is not None and memory.embedding_provider == "bailian"
        assert all(event.status is OutboxStatus.COMPLETED for event in events)
        assert all(job.status is WorkflowJobStatus.SUCCEEDED for job in jobs)
        assert session.scalar(select(func.count()).select_from(Experiment)) == 1
        assert session.scalar(select(func.count()).select_from(Memory)) == 1
        assert session.scalar(
            select(func.count()).select_from(Artifact).where(Artifact.experiment_id.is_not(None))
        ) == 2
