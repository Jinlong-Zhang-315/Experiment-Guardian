"""bind approved experiment plans to invariant checkpoints

Revision ID: 20260728_26
Revises: 20260728_25
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_26"
down_revision: str | Sequence[str] | None = "20260728_25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("plan_checks") as batch:
        batch.add_column(sa.Column("experiment_plan_decision_id", sa.Uuid()))
        batch.add_column(sa.Column("experiment_plan_snapshot", sa.JSON()))
        batch.add_column(sa.Column("invariant_check", sa.JSON()))
        batch.create_foreign_key(
            "fk_plan_checks_experiment_plan_decision",
            "experiment_plan_decisions",
            ["experiment_plan_decision_id"],
            ["id"],
        )
        batch.create_index(
            "ix_plan_check_experiment_plan_decision", ["experiment_plan_decision_id"]
        )

    with op.batch_alter_table("run_manifests") as batch:
        batch.drop_constraint("run_manifest_schema_version_one", type_="check")
        batch.create_check_constraint(
            "run_manifest_schema_version_supported", "schema_version IN (1, 2)"
        )


def downgrade() -> None:
    connection = op.get_bind()
    v2_count = connection.scalar(
        sa.text("SELECT count(*) FROM run_manifests WHERE schema_version = 2")
    )
    if v2_count:
        raise RuntimeError(
            "存在 schema v2 Run Manifest，降级会破坏不可变证据链；请保留 R17c migration"
        )

    with op.batch_alter_table("run_manifests") as batch:
        batch.drop_constraint("run_manifest_schema_version_supported", type_="check")
        batch.create_check_constraint("run_manifest_schema_version_one", "schema_version = 1")

    with op.batch_alter_table("plan_checks") as batch:
        batch.drop_index("ix_plan_check_experiment_plan_decision")
        batch.drop_constraint("fk_plan_checks_experiment_plan_decision", type_="foreignkey")
        batch.drop_column("invariant_check")
        batch.drop_column("experiment_plan_snapshot")
        batch.drop_column("experiment_plan_decision_id")
