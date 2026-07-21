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
