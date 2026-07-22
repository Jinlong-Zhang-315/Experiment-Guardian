"""验证关键表已经加入统一 metadata。"""

from experiment_guardian.infrastructure.models import Base


def test_p0_tables_are_registered() -> None:
    expected = {
        "access_tokens",
        "projects",
        "project_contexts",
        "experiment_intents",
        "protected_parameters",
        "plan_checks",
        "approval_records",
        "run_manifests",
        "experiment_submissions",
        "artifacts",
        "experiments",
        "experiment_metrics",
        "memories",
        "audit_logs",
        "idempotency_records",
    }

    assert expected <= set(Base.metadata.tables)


def test_formal_experiment_has_traceability_foreign_keys() -> None:
    table = Base.metadata.tables["experiments"]
    foreign_key_targets = {foreign_key.target_fullname for foreign_key in table.foreign_keys}

    assert "experiment_submissions.id" in foreign_key_targets
    assert "run_manifests.id" in foreign_key_targets
    assert "project_contexts.id" in foreign_key_targets


def test_version_and_evidence_snapshots_are_persisted() -> None:
    plan_check = Base.metadata.tables["plan_checks"]
    manifest = Base.metadata.tables["run_manifests"]
    submission = Base.metadata.tables["experiment_submissions"]

    assert {
        "context_id",
        "context_version",
        "intent_id",
        "intent_version",
        "configuration_document",
        "input_document_hash",
        "context_snapshot",
        "intent_snapshot",
    } <= set(plan_check.columns.keys())
    assert {"plan_check_id", "context_version", "intent_version", "evidence_snapshot"} <= set(
        manifest.columns.keys()
    )
    assert {"run_manifest_id", "manifest_hash", "evidence_snapshot"} <= set(
        submission.columns.keys()
    )


def test_plan_check_result_and_approval_status_are_database_constrained() -> None:
    plan_check = Base.metadata.tables["plan_checks"]
    check_names = {constraint.name for constraint in plan_check.constraints}

    assert "ck_plan_checks_result_approval_consistent" in check_names
    assert "ck_plan_checks_approved_requires_actor" in check_names


def test_approval_and_manifest_are_immutable_by_schema() -> None:
    approval = Base.metadata.tables["approval_records"]
    manifest = Base.metadata.tables["run_manifests"]

    assert "uq_approval_records_target" in {constraint.name for constraint in approval.constraints}
    manifest_constraints = {constraint.name for constraint in manifest.constraints}
    assert {
        "uq_run_manifests_plan_check",
        "uq_run_manifests_project_idempotency",
        "uq_run_manifests_project_hash",
        "ck_run_manifests_run_manifest_schema_version_one",
    } <= manifest_constraints
    assert {"schema_version", "config_document_hash"} <= set(manifest.columns.keys())


def test_submission_schema_keeps_r12b_embedding_separate() -> None:
    submission = Base.metadata.tables["experiment_submissions"]
    artifact = Base.metadata.tables["artifacts"]
    risk = Base.metadata.tables["submission_risks"]

    assert {
        "declared_experiment_status",
        "declared_metrics",
        "evidence_snapshot",
        "status",
        "upload_verified_at",
        "upload_verified_by",
        "upload_verification_snapshot",
        "workflow_status",
        "processing_step",
        "processing_error",
        "analysis_snapshot",
        "generated_summary",
    } <= set(submission.columns.keys())
    assert "embedding" not in submission.columns
    assert "review_receipt" in submission.columns
    embedding = Base.metadata.tables["submission_embeddings"]
    assert {
        "submission_id",
        "project_id",
        "embedding",
        "model_id",
        "dimension",
        "normalized",
        "document_version",
        "input_text",
        "input_sha256",
        "input_token_count",
        "generated_at",
    } <= set(embedding.columns.keys())
    assert "uq_submission_embeddings_submission_id" in {
        constraint.name for constraint in embedding.constraints
    }
    assert "fk_artifacts_experiment_id_experiments" not in {
        constraint.name for constraint in artifact.constraints
    }
    assert {
        "uq_artifacts_submission_filename",
        "uq_artifacts_s3_key",
        "ck_artifacts_artifact_size_limit",
        "ck_artifacts_artifact_sha256_length",
    } <= {constraint.name for constraint in artifact.constraints}
    assert {"verified_at", "verification_evidence", "s3_version_id"} <= set(artifact.columns.keys())
    assert "risk_fingerprint" in risk.columns
    assert "uq_submission_risks_submission_fingerprint" in {
        constraint.name for constraint in risk.constraints
    }
    job = Base.metadata.tables["workflow_jobs"]
    outbox = Base.metadata.tables["outbox_events"]
    assert {
        "generation",
        "attempt_count",
        "max_attempts",
        "lease_owner",
        "lease_expires_at",
        "last_error",
    } <= set(job.columns.keys())
    assert "uq_workflow_jobs_submission_type" in {constraint.name for constraint in job.constraints}
    assert "uq_outbox_events_job_generation" in {
        constraint.name for constraint in outbox.constraints
    }


def test_memory_has_structured_vector_filters() -> None:
    memory = Base.metadata.tables["memories"]

    assert {
        "project_id",
        "protocol",
        "model_name",
        "seed",
        "verification_status",
        "experiment_status",
        "current_valid",
    } <= set(memory.columns.keys())
    assert memory.columns["embedding"].type.dimension == 1024


def test_exploratory_experiment_cannot_be_baseline() -> None:
    experiment = Base.metadata.tables["experiments"]
    check_names = {constraint.name for constraint in experiment.constraints}

    assert "ck_experiments_exploratory_not_eligible_as_baseline" in check_names


def test_context_intent_and_constraint_keep_confirmation_provenance() -> None:
    context = Base.metadata.tables["project_contexts"]
    intent = Base.metadata.tables["experiment_intents"]
    constraint = Base.metadata.tables["protected_parameters"]

    assert {"version", "change_reason", "confirmed_by", "confirmed_at", "effective_at"} <= set(
        context.columns.keys()
    )
    assert {
        "context_id",
        "context_version",
        "experiment_mode",
        "original_message",
        "inference_basis",
        "confidence",
        "unresolved_ambiguities",
        "intent_receipt",
        "confirmed_by",
        "confirmed_at",
    } <= set(intent.columns.keys())
    assert {
        "version",
        "source_type",
        "verification_status",
        "original_message",
        "inference_basis",
        "confidence",
        "confirmed_by",
        "confirmed_at",
    } <= set(constraint.columns.keys())
