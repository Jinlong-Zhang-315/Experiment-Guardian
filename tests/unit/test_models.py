"""验证关键表已经加入统一 metadata。"""

from experiment_guardian.infrastructure.models import Base


def test_p0_tables_are_registered() -> None:
    expected = {
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

    assert {"context_id", "context_version", "intent_id", "intent_version"} <= set(
        plan_check.columns.keys()
    )
    assert {"plan_check_id", "context_version", "intent_version", "evidence_snapshot"} <= set(
        manifest.columns.keys()
    )
    assert {"run_manifest_id", "manifest_hash", "evidence_snapshot"} <= set(
        submission.columns.keys()
    )


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
