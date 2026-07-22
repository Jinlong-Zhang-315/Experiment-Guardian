"""R13 正式实验确认、幂等和结构化/向量查询纵向测试。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.api.dependencies import require_api_identity
from experiment_guardian.api.routes import submissions as submissions_route
from experiment_guardian.application.errors import AuthorizationError, ConflictError
from experiment_guardian.application.experiments import (
    ExperimentQueryService,
    ExperimentReviewService,
)
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.domain.administration import (
    SubmissionDecisionRequest,
    SubmissionDecisionResult,
)
from experiment_guardian.domain.contracts import ExperimentQueryCommand
from experiment_guardian.domain.enums import (
    ApprovalDecision,
    EvidenceType,
    ReviewEligibility,
    RiskSeverity,
    SubmissionStatus,
    TeamRole,
)
from experiment_guardian.infrastructure.models import (
    Artifact,
    Experiment,
    ExperimentMetric,
    ExperimentSubmission,
    Memory,
    Project,
    SubmissionEmbedding,
    SubmissionRisk,
    TeamMember,
    User,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyGovernanceRepository,
    SqlAlchemyProjectRepository,
    SqlAlchemySubmissionRepository,
)
from experiment_guardian.main import create_app
from tests.integration.test_async_review_slice import (
    FakeEmbeddingGenerator,
    build_review_processor,
    prepare_summary,
)


def prepare_needs_review(
    factory: sessionmaker[Session],
) -> tuple[SimpleNamespace, UUID, object, FakeEmbeddingGenerator]:
    owner, project_id, _, command, queue = prepare_summary(factory)
    generator = FakeEmbeddingGenerator()
    processor = build_review_processor(factory, queue, generator)
    assert processor.process_delivery(queue.delivery(receipt="r13-review"))
    return owner, project_id, command, generator


def review_service(factory: sessionmaker[Session]) -> ExperimentReviewService:
    return ExperimentReviewService(
        factory,
        SqlAlchemyProjectRepository(),
        SqlAlchemyGovernanceRepository(),
        SqlAlchemySubmissionRepository(),
    )


def identity(owner: SimpleNamespace, project_id: UUID, *scopes: str) -> RequestIdentity:
    return RequestIdentity(
        user_id=owner.user_id,
        team_id=owner.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset(scopes),
    )


def test_approval_creates_formal_trace_and_supports_both_query_modes(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, command, generator = prepare_needs_review(plan_check_session_factory)
    service = review_service(plan_check_session_factory)
    key = uuid4()
    request = SubmissionDecisionRequest(decision=ApprovalDecision.APPROVED)
    reviewer = identity(owner, project_id, "submission:review")

    result = service.decide(
        identity=reviewer,
        project_id=project_id,
        submission_id=command.submission_id,
        idempotency_key=key,
        request=request,
    )
    replay = service.decide(
        identity=reviewer,
        project_id=project_id,
        submission_id=command.submission_id,
        idempotency_key=key,
        request=request,
    )
    assert replay == result
    assert result.experiment_id is not None
    with pytest.raises(ConflictError, match="不同的 Submission 审核请求"):
        service.decide(
            identity=reviewer,
            project_id=project_id,
            submission_id=command.submission_id,
            idempotency_key=key,
            request=SubmissionDecisionRequest(
                decision=ApprovalDecision.REJECTED,
                decision_reason="同一 key 不能改变决定",
            ),
        )

    with plan_check_session_factory() as session:
        submission = session.get(ExperimentSubmission, command.submission_id)
        experiment = session.get(Experiment, result.experiment_id)
        memory = session.scalar(select(Memory))
        assert submission is not None and experiment is not None and memory is not None
        assert submission.status is SubmissionStatus.APPROVED
        assert experiment.submission_id == submission.id
        assert experiment.approval_record_id == result.approval_record_id
        assert experiment.summary_snapshot == submission.generated_summary
        assert memory.experiment_id == experiment.id
        embedding = session.scalar(select(SubmissionEmbedding))
        assert embedding is not None
        assert memory.content_sha256 == embedding.input_sha256
        assert session.scalar(select(func.count()).select_from(ExperimentMetric)) >= 1
        artifacts = list(session.scalars(select(Artifact)).all())
        assert artifacts and all(item.experiment_id == experiment.id for item in artifacts)

    query_service = ExperimentQueryService(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
        generator,
    )
    query_identity = identity(owner, project_id, "experiment:query")
    candidates = query_service.query(
        ExperimentQueryCommand(
            project_id=project_id,
            query="融合实验结果",
            protocol="40/20",
        ),
        query_identity,
    )
    assert len(candidates) == 1
    assert candidates[0].detail_level == "SUMMARY"
    assert candidates[0].retrieval_role == "CANDIDATE_EVIDENCE"
    assert candidates[0].vector_similarity == pytest.approx(1.0)
    assert candidates[0].config_snapshot is None

    calls_before_detail = len(generator.calls)
    details = query_service.query(
        ExperimentQueryCommand(project_id=project_id, experiment_id=result.experiment_id),
        query_identity,
    )
    assert len(generator.calls) == calls_before_detail
    assert len(details) == 1
    assert details[0].detail_level == "FULL"
    assert details[0].retrieval_role == "STRUCTURED_RECORD"
    assert details[0].config_snapshot is not None
    assert details[0].artifacts

    with plan_check_session_factory() as session, session.begin():
        other_project = Project(team_id=owner.team_id, name="Other Project")
        session.add(other_project)
        session.flush()
        experiment = session.get(Experiment, result.experiment_id)
        memory = session.scalar(select(Memory).where(Memory.experiment_id == result.experiment_id))
        assert experiment is not None and memory is not None
        experiment.project_id = other_project.id
        memory.project_id = other_project.id

    assert (
        query_service.query(
            ExperimentQueryCommand(project_id=project_id, experiment_id=result.experiment_id),
            query_identity,
        )
        == []
    )


def test_rejection_is_final_and_creates_no_formal_rows(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, command, _ = prepare_needs_review(plan_check_session_factory)
    result = review_service(plan_check_session_factory).decide(
        identity=identity(owner, project_id, "submission:review"),
        project_id=project_id,
        submission_id=command.submission_id,
        idempotency_key=uuid4(),
        request=SubmissionDecisionRequest(
            decision=ApprovalDecision.REJECTED,
            decision_reason="结果不进入正式记录",
        ),
    )
    assert result.experiment_id is None
    assert result.submission_status is SubmissionStatus.REJECTED
    with plan_check_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Experiment)) == 0
        assert session.scalar(select(func.count()).select_from(Memory)) == 0
        assert session.scalar(select(func.count()).select_from(ExperimentMetric)) == 0


def test_empty_structured_candidate_set_does_not_call_embedding_model(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, _, generator = prepare_needs_review(plan_check_session_factory)
    calls_before = len(generator.calls)
    result = ExperimentQueryService(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
        generator,
    ).query(
        ExperimentQueryCommand(
            project_id=project_id,
            query="不存在的正式实验",
            protocol="40/20",
        ),
        identity(owner, project_id, "experiment:query"),
    )
    assert result == []
    assert len(generator.calls) == calls_before


def test_critical_risk_cannot_be_approved_but_can_be_rejected(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, _, command, queue = prepare_summary(plan_check_session_factory)
    with plan_check_session_factory() as session, session.begin():
        session.add(
            SubmissionRisk(
                submission_id=command.submission_id,
                risk_fingerprint="c" * 64,
                risk_type="CRITICAL_TEST",
                severity=RiskSeverity.CRITICAL,
                field_path="result.metrics.top1",
                previous_value=None,
                current_value=2.0,
                expected_value="0..1",
                rule_id="R13.CRITICAL_TEST",
                message="关键指标不合法",
                impact="不能形成正式实验",
                evidence_type=EvidenceType.CLOUD_VERIFIED,
                evidence_source="pytest",
                collected_at=datetime.now(UTC),
                collection_tool="pytest",
                constraint_candidates=[],
                blocking=True,
                resolved=False,
            )
        )
    processor = build_review_processor(
        plan_check_session_factory, queue, FakeEmbeddingGenerator()
    )
    assert processor.process_delivery(queue.delivery(receipt="r13-critical"))
    service = review_service(plan_check_session_factory)
    reviewer = identity(owner, project_id, "submission:review")
    with pytest.raises(ConflictError, match="CRITICAL"):
        service.decide(
            identity=reviewer,
            project_id=project_id,
            submission_id=command.submission_id,
            idempotency_key=uuid4(),
            request=SubmissionDecisionRequest(decision=ApprovalDecision.APPROVED),
        )
    rejected = service.decide(
        identity=reviewer,
        project_id=project_id,
        submission_id=command.submission_id,
        idempotency_key=uuid4(),
        request=SubmissionDecisionRequest(
            decision=ApprovalDecision.REJECTED,
            decision_reason="CRITICAL 风险不能确认",
        ),
    )
    assert rejected.submission_status is SubmissionStatus.REJECTED


def test_high_risk_requires_owner_even_for_original_researcher(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, _, command, queue = prepare_summary(plan_check_session_factory)
    researcher_id = uuid4()
    with plan_check_session_factory() as session, session.begin():
        session.add(User(id=researcher_id, name="Researcher", email="high@example.com"))
        session.flush()
        session.add(
            TeamMember(
                team_id=owner.team_id,
                user_id=researcher_id,
                role=TeamRole.RESEARCHER,
            )
        )
        submission = session.get(ExperimentSubmission, command.submission_id)
        assert submission is not None
        submission.submitted_by = researcher_id
        session.add(
            SubmissionRisk(
                submission_id=command.submission_id,
                risk_fingerprint="h" * 64,
                risk_type="HIGH_TEST",
                severity=RiskSeverity.HIGH,
                field_path="model.backbone",
                previous_value="shift-gcn",
                current_value="transformer",
                expected_value="shift-gcn",
                rule_id="R13.HIGH_TEST",
                message="高风险模型变化",
                impact="需要 Owner 决策",
                evidence_type=EvidenceType.CLOUD_VERIFIED,
                evidence_source="pytest",
                collected_at=datetime.now(UTC),
                collection_tool="pytest",
                constraint_candidates=[],
                blocking=False,
                resolved=False,
            )
        )
    assert build_review_processor(
        plan_check_session_factory, queue, FakeEmbeddingGenerator()
    ).process_delivery(queue.delivery(receipt="r13-high"))
    researcher = RequestIdentity(
        user_id=researcher_id,
        team_id=owner.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"submission:review"}),
    )
    service = review_service(plan_check_session_factory)
    with pytest.raises(AuthorizationError, match="HIGH"):
        service.decide(
            identity=researcher,
            project_id=project_id,
            submission_id=command.submission_id,
            idempotency_key=uuid4(),
            request=SubmissionDecisionRequest(decision=ApprovalDecision.APPROVED),
        )
    approved = service.decide(
        identity=identity(owner, project_id, "submission:review"),
        project_id=project_id,
        submission_id=command.submission_id,
        idempotency_key=uuid4(),
        request=SubmissionDecisionRequest(decision=ApprovalDecision.APPROVED),
    )
    assert approved.submission_status is SubmissionStatus.APPROVED


def test_researcher_cannot_decide_another_members_submission(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, command, _ = prepare_needs_review(plan_check_session_factory)
    researcher_id = uuid4()
    with plan_check_session_factory() as session, session.begin():
        session.add(User(id=researcher_id, name="Researcher", email="r13@example.com"))
        session.flush()
        session.add(
            TeamMember(
                team_id=owner.team_id,
                user_id=researcher_id,
                role=TeamRole.RESEARCHER,
            )
        )
    researcher = RequestIdentity(
        user_id=researcher_id,
        team_id=owner.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"submission:review"}),
    )
    with pytest.raises(AuthorizationError, match="自己提交"):
        review_service(plan_check_session_factory).decide(
            identity=researcher,
            project_id=project_id,
            submission_id=command.submission_id,
            idempotency_key=uuid4(),
            request=SubmissionDecisionRequest(decision=ApprovalDecision.APPROVED),
        )


def test_embedding_source_drift_rolls_back_confirmation(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, command, _ = prepare_needs_review(plan_check_session_factory)
    with plan_check_session_factory() as session, session.begin():
        memory_source = session.scalar(select(Memory))
        assert memory_source is None
        embedding = session.scalar(select(SubmissionEmbedding))
        assert embedding is not None
        embedding.input_sha256 = "f" * 64

    with pytest.raises(ConflictError, match="embedding"):
        review_service(plan_check_session_factory).decide(
            identity=identity(owner, project_id, "submission:review"),
            project_id=project_id,
            submission_id=command.submission_id,
            idempotency_key=uuid4(),
            request=SubmissionDecisionRequest(decision=ApprovalDecision.APPROVED),
        )
    with plan_check_session_factory() as session:
        submission = session.get(ExperimentSubmission, command.submission_id)
        assert submission is not None and submission.status is SubmissionStatus.NEEDS_REVIEW
        assert session.scalar(select(func.count()).select_from(Experiment)) == 0


@pytest.mark.asyncio
async def test_submission_decision_rest_route_uses_authenticated_api_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, submission_id, approval_id, experiment_id = (uuid4() for _ in range(4))
    api_identity = RequestIdentity(
        user_id=uuid4(),
        team_id=uuid4(),
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"submission:review"}),
    )
    captured: dict[str, object] = {}

    class FakeReviewService:
        def decide(self, **kwargs: object) -> SubmissionDecisionResult:
            captured.update(kwargs)
            return SubmissionDecisionResult(
                approval_record_id=approval_id,
                project_id=project_id,
                submission_id=submission_id,
                experiment_id=experiment_id,
                decision=ApprovalDecision.APPROVED,
                submission_status=SubmissionStatus.APPROVED,
                review_eligibility=ReviewEligibility.RESEARCHER_OR_OWNER,
                requested_by=api_identity.user_id,
                decided_by=api_identity.user_id,
                decided_at=datetime.now(UTC),
            )

    async def override_identity() -> RequestIdentity:
        return api_identity

    monkeypatch.setattr(
        submissions_route, "get_experiment_review_service", lambda: FakeReviewService()
    )
    app = create_app()
    app.dependency_overrides[require_api_identity] = override_identity
    key = uuid4()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/projects/{project_id}/submissions/{submission_id}/decision",
            headers={"Idempotency-Key": str(key)},
            json={"decision": "APPROVED"},
        )

    assert response.status_code == 201
    assert captured["identity"] is api_identity
    assert captured["idempotency_key"] == key
    assert isinstance(captured["request"], SubmissionDecisionRequest)
