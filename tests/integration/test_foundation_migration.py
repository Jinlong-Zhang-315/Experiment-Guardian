"""Alembic revision 按开发切片逐步增加正式表。"""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from experiment_guardian.domain.enums import WorkflowStatus, WorkflowStep
from experiment_guardian.infrastructure.models import ExperimentSubmission, WorkflowJob
from migrations.scope import (
    FORMAL_EXPERIMENT_TABLES,
    FOUNDATION_TABLES,
    GOVERNANCE_TABLES,
    MIGRATED_TABLES,
    PLAN_CHECK_TABLES,
    SUBMISSION_ANALYSIS_TABLES,
    SUBMISSION_PREPARE_TABLES,
)
from tests.integration.test_async_review_slice import (
    FakeEmbeddingGenerator,
    build_review_processor,
    prepare_summary,
)


def test_foundation_and_plan_check_migrations_are_independently_reversible(
    tmp_path: Path,
) -> None:
    database = tmp_path / "migration.db"
    database_url = f"sqlite+pysqlite:///{database}"
    environment = {**os.environ, "DATABASE_URL": database_url}

    def run_alembic(*arguments: str) -> None:
        subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            check=True,
            env=environment,
            capture_output=True,
            text=True,
        )

    run_alembic("upgrade", "20260721_01")
    engine = create_engine(database_url)
    assert set(inspect(engine).get_table_names()) == FOUNDATION_TABLES | {"alembic_version"}
    engine.dispose()

    run_alembic("upgrade", "20260721_02")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == (
        FOUNDATION_TABLES | PLAN_CHECK_TABLES | {"alembic_version"}
    )
    check_names = {item["name"] for item in inspector.get_check_constraints("plan_checks")}
    assert "ck_plan_checks_result_approval_consistent" in check_names
    assert "ck_plan_checks_approved_requires_actor" in check_names
    columns_at_r7_start = {item["name"] for item in inspector.get_columns("plan_checks")}
    assert "context_snapshot" not in columns_at_r7_start
    engine.dispose()

    run_alembic("upgrade", "head")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == MIGRATED_TABLES | {"alembic_version"}
    assert {
        "origin",
        "start_idempotency_key",
        "start_request_hash",
        "task_context_snapshot",
        "task_context_hash",
    } <= {item["name"] for item in inspector.get_columns("agent_threads")}
    assert {
        "auth_method",
        "auth_access_token_id",
        "auth_oauth_grant_id",
        "auth_scopes_snapshot",
        "auth_expires_at",
        "run_kind",
        "target_experiment_plan_revision_id",
    } <= {item["name"] for item in inspector.get_columns("agent_runs")}
    assert {
        "experiment_plans",
        "experiment_plan_revisions",
        "experiment_plan_reviews",
        "experiment_plan_decisions",
    } <= set(inspector.get_table_names())
    assert {
        "policy_snapshot",
        "policy_hash",
        "content_hash",
        "evidence_hash",
        "automatic_revision_round",
    } <= {item["name"] for item in inspector.get_columns("experiment_plan_revisions")}
    assert "uq_experiment_plan_reviews_revision" in {
        item["name"] for item in inspector.get_unique_constraints("experiment_plan_reviews")
    }
    assert "uq_experiment_plan_decisions_revision" in {
        item["name"] for item in inspector.get_unique_constraints("experiment_plan_decisions")
    }
    columns_at_head = {item["name"] for item in inspector.get_columns("plan_checks")}
    assert {
        "input_document_hash",
        "configuration_document",
        "context_snapshot",
        "intent_snapshot",
    } <= columns_at_head
    assert {
        "uq_run_manifests_plan_check",
        "uq_run_manifests_project_idempotency",
        "uq_run_manifests_project_hash",
    } <= {item["name"] for item in inspector.get_unique_constraints("run_manifests")}
    assert "uq_approval_records_target" in {
        item["name"] for item in inspector.get_unique_constraints("approval_records")
    }
    assert set(inspector.get_table_names()) >= SUBMISSION_PREPARE_TABLES
    assert {
        "uq_experiment_submissions_actor_idempotency",
    } <= {item["name"] for item in inspector.get_unique_constraints("experiment_submissions")}
    assert {
        "uq_artifacts_submission_filename",
        "uq_artifacts_s3_key",
    } <= {item["name"] for item in inspector.get_unique_constraints("artifacts")}
    assert {
        "upload_verified_at",
        "upload_verified_by",
        "upload_verification_snapshot",
    } <= {item["name"] for item in inspector.get_columns("experiment_submissions")}
    status_column = next(
        item for item in inspector.get_columns("experiment_submissions") if item["name"] == "status"
    )
    assert status_column["type"].length == 32
    assert {"verified_at", "verification_evidence", "s3_version_id"} <= {
        item["name"] for item in inspector.get_columns("artifacts")
    }
    assert set(inspector.get_table_names()) >= SUBMISSION_ANALYSIS_TABLES
    assert {
        "workflow_status",
        "processing_step",
        "processing_error",
        "analysis_snapshot",
        "generated_summary",
        "review_receipt",
    } <= {item["name"] for item in inspector.get_columns("experiment_submissions")}
    assert "submission_embeddings" in inspector.get_table_names()
    assert "uq_submission_embeddings_submission_id" in {
        item["name"] for item in inspector.get_unique_constraints("submission_embeddings")
    }
    assert "provider" in {item["name"] for item in inspector.get_columns("submission_embeddings")}
    assert set(inspector.get_table_names()) >= FORMAL_EXPERIMENT_TABLES
    assert "policy_narratives" in inspector.get_table_names()
    assert "agent_context_summaries" in inspector.get_table_names()
    assert {
        "agent_policy_drafts",
        "agent_policy_draft_revisions",
        "agent_action_proposals",
    } <= set(inspector.get_table_names())
    assert "current_summary_id" in {item["name"] for item in inspector.get_columns("agent_threads")}
    assert {
        "purpose",
        "provider",
        "model_id",
        "latency_ms",
        "estimated_cost",
    } <= {item["name"] for item in inspector.get_columns("agent_model_calls")}
    assert {
        "covered_sequence_from",
        "covered_sequence_to",
        "source_message_ids",
        "source_hash",
        "payload",
        "error",
    } <= {item["name"] for item in inspector.get_columns("agent_context_summaries")}
    assert {
        "base_policy_snapshot",
        "base_policy_hash",
        "current_revision",
        "abandoned_at",
    } <= {item["name"] for item in inspector.get_columns("agent_policy_drafts")}
    assert {
        "candidate_payload",
        "candidate_hash",
        "source_request_hash",
        "validation_report",
        "diff_snapshot",
        "impact_snapshot",
    } <= {
        item["name"]
        for item in inspector.get_columns("agent_policy_draft_revisions")
    }
    assert {
        "source_draft_revision_id",
        "payload",
        "payload_hash",
        "diff_snapshot",
        "impact_snapshot",
        "proposal_digest",
        "expires_at",
        "confirmed_session_id",
        "executed_context_id",
        "target_plan_check_id",
        "target_submission_id",
        "target_state_hash",
        "executed_approval_record_id",
        "executed_experiment_id",
        "execution_error",
    } <= {
        item["name"]
        for item in inspector.get_columns("agent_action_proposals")
    }
    assert {
        "context_id",
        "context_version",
        "intent_id",
        "intent_version",
        "source_hash",
        "generator_version",
        "status",
        "content",
        "error",
    } <= {item["name"] for item in inspector.get_columns("policy_narratives")}
    assert "uq_policy_narratives_source_version" in {
        item["name"] for item in inspector.get_unique_constraints("policy_narratives")
    }
    assert {
        "approval_record_id",
        "summary_snapshot",
        "review_receipt_snapshot",
    } <= {item["name"] for item in inspector.get_columns("experiments")}
    assert {
        "embedding_model_id",
        "embedding_provider",
        "embedding_dimension",
        "embedding_normalized",
        "document_version",
        "content_sha256",
    } <= {item["name"] for item in inspector.get_columns("memories")}
    assert "fk_artifacts_experiment_id_experiments" in {
        item["name"] for item in inspector.get_foreign_keys("artifacts")
    }
    assert "uq_submission_risks_submission_fingerprint" in {
        item["name"] for item in inspector.get_unique_constraints("submission_risks")
    }
    assert "uq_workflow_jobs_submission_type" in {
        item["name"] for item in inspector.get_unique_constraints("workflow_jobs")
    }
    assert "uq_outbox_events_job_generation" in {
        item["name"] for item in inspector.get_unique_constraints("outbox_events")
    }
    assert {
        "experiment_plan_decision_id",
        "experiment_plan_snapshot",
        "invariant_check",
    } <= {item["name"] for item in inspector.get_columns("plan_checks")}
    assert any(
        str(item["name"]).endswith("run_manifest_schema_version_supported")
        for item in inspector.get_check_constraints("run_manifests")
    )
    engine.dispose()

    run_alembic("downgrade", "20260728_25")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert {
        "experiment_plan_decision_id",
        "experiment_plan_snapshot",
        "invariant_check",
    }.isdisjoint({item["name"] for item in inspector.get_columns("plan_checks")})
    assert any(
        str(item["name"]).endswith("run_manifest_schema_version_one")
        for item in inspector.get_check_constraints("run_manifests")
    )
    engine.dispose()
    run_alembic("upgrade", "head")

    run_alembic("downgrade", "20260728_24")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert not (
        {
            "experiment_plans",
            "experiment_plan_revisions",
            "experiment_plan_reviews",
            "experiment_plan_decisions",
        }
        & set(inspector.get_table_names())
    )
    assert {"run_kind", "target_experiment_plan_revision_id"}.isdisjoint(
        {item["name"] for item in inspector.get_columns("agent_runs")}
    )
    engine.dispose()
    run_alembic("upgrade", "head")

    run_alembic("downgrade", "20260724_17")
    engine = create_engine(database_url)
    assert "agent_action_proposals" not in inspect(engine).get_table_names()
    assert "agent_policy_drafts" in inspect(engine).get_table_names()
    engine.dispose()
    run_alembic("upgrade", "head")

    run_alembic("downgrade", "20260723_16")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert not (
        {"agent_policy_drafts", "agent_policy_draft_revisions"}
        & set(inspector.get_table_names())
    )
    assert "agent_context_summaries" in inspector.get_table_names()
    engine.dispose()
    run_alembic("upgrade", "head")

    # revision 09 降级保留摘要与 Summary Job，但删除 R12b 的向量、回执和 Review Job。
    engine = create_engine(database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    _, _, _, r12b_command, queue = prepare_summary(factory)
    processor = build_review_processor(factory, queue, FakeEmbeddingGenerator())
    assert processor.process_delivery(queue.delivery(receipt="migration-review"))
    engine.dispose()

    run_alembic("downgrade", "20260722_08")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert "submission_embeddings" not in inspector.get_table_names()
    assert "review_receipt" not in {
        item["name"] for item in inspector.get_columns("experiment_submissions")
    }
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT status, workflow_status, processing_step, processing_error "
                "FROM experiment_submissions WHERE id = :submission_id"
            ),
            {"submission_id": r12b_command.submission_id.hex},
        ).one()
        assert tuple(row) == (
            "PROCESSING",
            "AWAITING_ENRICHMENT",
            "SUMMARY_GENERATION",
            None,
        )
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        assert {item.job_type.value for item in session.scalars(select(WorkflowJob)).all()} == {
            "SUBMISSION_SUMMARY"
        }
    engine.dispose()
    run_alembic("upgrade", "head")

    # revision 08 降级必须保留 R11 风险游标，并清除 R12a 的瞬时失败状态。
    engine = create_engine(database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    finalize = r12b_command
    with factory() as session, session.begin():
        submission = session.get(ExperimentSubmission, finalize.submission_id)
        assert submission is not None
        submission.workflow_status = WorkflowStatus.RETRYABLE_FAILURE
        submission.processing_step = WorkflowStep.SUMMARY_GENERATION
        submission.processing_error = {"code": "BEDROCK_TIMEOUT", "retryable": True}
        submission.generated_summary = {"schema_version": 1, "text": "temporary"}
    engine.dispose()

    run_alembic("downgrade", "20260722_07")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert not ({"workflow_jobs", "outbox_events"} & set(inspector.get_table_names()))
    assert "generated_summary" not in {
        item["name"] for item in inspector.get_columns("experiment_submissions")
    }
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT status, workflow_status, processing_step, processing_error "
                "FROM experiment_submissions WHERE id = :submission_id"
            ),
            {"submission_id": finalize.submission_id.hex},
        ).one()
        assert tuple(row) == (
            "PROCESSING",
            "AWAITING_ENRICHMENT",
            "RISK_ANALYSIS",
            None,
        )
    engine.dispose()
    run_alembic("upgrade", "head")

    run_alembic("downgrade", "20260722_06")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert not (set(inspector.get_table_names()) & SUBMISSION_ANALYSIS_TABLES)
    assert not (
        {"workflow_status", "processing_step", "processing_error", "analysis_snapshot"}
        & {item["name"] for item in inspector.get_columns("experiment_submissions")}
    )
    engine.dispose()

    run_alembic("upgrade", "head")
    run_alembic("downgrade", "20260721_05")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert not (
        {"upload_verified_at", "upload_verified_by", "upload_verification_snapshot"}
        & {item["name"] for item in inspector.get_columns("experiment_submissions")}
    )
    status_column = next(
        item for item in inspector.get_columns("experiment_submissions") if item["name"] == "status"
    )
    assert status_column["type"].length == 12
    assert not (
        {"verified_at", "verification_evidence", "s3_version_id"}
        & {item["name"] for item in inspector.get_columns("artifacts")}
    )
    engine.dispose()

    run_alembic("upgrade", "head")
    run_alembic("downgrade", "20260721_04")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert not (set(inspector.get_table_names()) & SUBMISSION_PREPARE_TABLES)
    assert set(inspector.get_table_names()) >= GOVERNANCE_TABLES
    engine.dispose()

    run_alembic("upgrade", "head")
    run_alembic("downgrade", "20260721_03")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert not (set(inspector.get_table_names()) & GOVERNANCE_TABLES)
    assert "context_snapshot" in {item["name"] for item in inspector.get_columns("plan_checks")}
    engine.dispose()

    run_alembic("downgrade", "20260721_02")
    engine = create_engine(database_url)
    columns_after_snapshot_downgrade = {
        item["name"] for item in inspect(engine).get_columns("plan_checks")
    }
    assert "context_snapshot" not in columns_after_snapshot_downgrade
    engine.dispose()

    run_alembic("downgrade", "20260721_01")
    engine = create_engine(database_url)
    assert set(inspect(engine).get_table_names()) == FOUNDATION_TABLES | {"alembic_version"}
    engine.dispose()

    run_alembic("downgrade", "base")
    engine = create_engine(database_url)
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    engine.dispose()
