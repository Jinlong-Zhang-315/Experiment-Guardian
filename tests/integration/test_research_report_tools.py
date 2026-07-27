"""R15e-a 确定性报告来源工具的正式实验链路测试。"""

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.agent_tools import AgentToolRegistry
from experiment_guardian.application.errors import InputValidationError
from experiment_guardian.application.experiments import ExperimentReviewService
from experiment_guardian.domain.administration import SubmissionDecisionRequest
from experiment_guardian.domain.enums import ApprovalDecision, ExperimentStatus
from experiment_guardian.infrastructure.models import (
    ApprovalRecord,
    Experiment,
    ExperimentMetric,
    ExperimentSubmission,
    PlanCheck,
    RunManifest,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyGovernanceRepository,
    SqlAlchemyProjectRepository,
    SqlAlchemySubmissionRepository,
)
from tests.integration.test_experiment_confirmation_slice import (
    identity,
    prepare_needs_review,
)


def _clone(row: Any, **overrides: Any) -> Any:
    values = {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in {"id", "created_at", "updated_at"}
    }
    values.update(overrides)
    return type(row)(id=uuid4(), **values)


def _two_formal_experiments(
    factory: sessionmaker[Session],
) -> tuple[object, object, list[Experiment]]:
    owner, project_id, command, _ = prepare_needs_review(factory)
    review = ExperimentReviewService(
        factory,
        SqlAlchemyProjectRepository(),
        SqlAlchemyGovernanceRepository(),
        SqlAlchemySubmissionRepository(),
    )
    confirmed = review.decide(
        identity=identity(owner, project_id, "submission:review"),
        project_id=project_id,
        submission_id=command.submission_id,
        idempotency_key=uuid4(),
        request=SubmissionDecisionRequest(decision=ApprovalDecision.APPROVED),
    )
    assert confirmed.experiment_id is not None
    with factory() as session, session.begin():
        first = session.get(Experiment, confirmed.experiment_id)
        assert first is not None
        first_plan = session.get(
            PlanCheck,
            session.get(RunManifest, first.run_manifest_id).plan_check_id,  # type: ignore[union-attr]
        )
        first_manifest = session.get(RunManifest, first.run_manifest_id)
        first_submission = session.get(ExperimentSubmission, first.submission_id)
        first_approval = session.get(ApprovalRecord, first.approval_record_id)
        assert all(
            item is not None
            for item in (first_plan, first_manifest, first_submission, first_approval)
        )
        second_plan = _clone(
            first_plan,
            idempotency_key=uuid4(),
            request_hash="2" * 64,
        )
        session.add(second_plan)
        session.flush()
        second_manifest = _clone(
            first_manifest,
            plan_check_id=second_plan.id,
            approval_record_id=None,
            idempotency_key=uuid4(),
            manifest_hash="3" * 64,
        )
        session.add(second_manifest)
        session.flush()
        analysis_snapshot = dict(first_submission.analysis_snapshot or {})
        parsed_documents = dict(analysis_snapshot.get("parsed_documents") or {})
        result_document = dict(parsed_documents.get("result") or {})
        parsed_result = dict(result_document.get("parsed") or {})
        parsed_result.update({"status": "FAILED", "failure_reason": "显存不足"})
        result_document["parsed"] = parsed_result
        parsed_documents["result"] = result_document
        analysis_snapshot["parsed_documents"] = parsed_documents
        second_submission = _clone(
            first_submission,
            run_manifest_id=second_manifest.id,
            idempotency_key=uuid4(),
            request_hash="4" * 64,
            manifest_hash=second_manifest.manifest_hash,
            declared_experiment_status="FAILED",
            analysis_snapshot=analysis_snapshot,
        )
        session.add(second_submission)
        session.flush()
        second_approval = _clone(
            first_approval,
            target_id=second_submission.id,
            decided_at=first_approval.decided_at + timedelta(seconds=1),
        )
        session.add(second_approval)
        session.flush()
        second = _clone(
            first,
            run_manifest_id=second_manifest.id,
            submission_id=second_submission.id,
            approval_record_id=second_approval.id,
            name="失败重复实验",
            status=ExperimentStatus.FAILED,
            confirmed_at=first.confirmed_at + timedelta(seconds=1),
        )
        session.add(second)
        session.flush()
        metrics = list(
            session.scalars(
                select(ExperimentMetric).where(ExperimentMetric.experiment_id == first.id)
            ).all()
        )
        for metric in metrics:
            session.add(_clone(metric, experiment_id=second.id))
        first_id, second_id = first.id, second.id
    with factory() as session:
        rows = [session.get(Experiment, item) for item in (first_id, second_id)]
        assert all(item is not None for item in rows)
        return owner, project_id, rows  # type: ignore[return-value]


def test_report_source_is_stable_and_marks_failed_experiment(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, experiments = _two_formal_experiments(plan_check_session_factory)
    registry = AgentToolRegistry(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
    )
    actor = identity(owner, project_id, "experiment:read")
    arguments = {
        "experiment_ids": [str(item.id) for item in reversed(experiments)],
        "objective": "比较一次成功和一次失败运行",
        "include_historical": False,
    }
    first = registry.execute(
        tool_name="research_report_prepare_v1",
        arguments=arguments,
        project_id=project_id,
        identity=actor,
        evidence_prefix="ev_1",
        catalog_version="r15e-a-v1",
    )
    replay = registry.execute(
        tool_name="research_report_prepare_v1",
        arguments=arguments,
        project_id=project_id,
        identity=actor,
        evidence_prefix="ev_9",
        catalog_version="r15e-a-v1",
    )
    assert first.content["source_hash"] == replay.content["source_hash"]
    assert [item["status"] for item in first.content["experiments"]] == [
        "COMPLETED",
        "FAILED",
    ]
    assert first.content["experiments"][1]["failure_reason"] == "显存不足"
    assert first.content["comparisons"][0]["comparability"] == "NOT_COMPARABLE"
    assert first.content["repeated_group"]["analysis"]["accepted"] is False


def test_historical_experiment_requires_explicit_opt_in(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, experiments = _two_formal_experiments(plan_check_session_factory)
    with plan_check_session_factory() as session, session.begin():
        historical = session.get(Experiment, experiments[1].id)
        assert historical is not None
        historical.status = ExperimentStatus.SUPERSEDED
    registry = AgentToolRegistry(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
    )
    actor = identity(owner, project_id, "experiment:read")
    arguments = {
        "experiment_ids": [str(item.id) for item in experiments],
        "objective": "读取历史实验",
    }
    with pytest.raises(InputValidationError, match="include_historical"):
        registry.execute(
            tool_name="research_report_prepare_v1",
            arguments=arguments,
            project_id=project_id,
            identity=actor,
            evidence_prefix="ev_1",
            catalog_version="r15e-a-v1",
        )
    accepted = registry.execute(
        tool_name="research_report_prepare_v1",
        arguments={**arguments, "include_historical": True},
        project_id=project_id,
        identity=actor,
        evidence_prefix="ev_2",
        catalog_version="r15e-a-v1",
    )
    assert accepted.content["experiments"][1]["historical"] is True
