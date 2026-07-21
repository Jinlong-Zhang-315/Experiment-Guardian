"""persist plan decisions and immutable run manifests

Revision ID: 20260721_04
Revises: 20260721_03
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_04"
down_revision: str | Sequence[str] | None = "20260721_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_records",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "target_type",
            sa.Enum(
                "PLAN_CHECK",
                "EXPERIMENT_SUBMISSION",
                name="approval_target_type",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("approval_type", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "APPROVED",
                "REJECTED",
                name="approval_decision",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=False),
        sa.Column("request_reason", sa.Text(), nullable=False),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('APPROVED', 'REJECTED')",
            name="approval_record_final_status",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name="fk_approval_records_decided_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_approval_records_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.id"], name="fk_approval_records_requested_by_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_approval_records"),
        sa.UniqueConstraint("target_type", "target_id", name="uq_approval_records_target"),
    )

    op.create_table(
        "run_manifests",
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("plan_check_id", sa.Uuid(), nullable=False),
        sa.Column("approval_record_id", sa.Uuid(), nullable=True),
        sa.Column("context_id", sa.Uuid(), nullable=False),
        sa.Column("context_version", sa.Integer(), nullable=False),
        sa.Column("intent_version", sa.Integer(), nullable=False),
        sa.Column(
            "experiment_mode",
            sa.Enum(
                "FORMAL",
                "EXPLORATORY",
                name="manifest_experiment_mode",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("config_document_hash", sa.String(length=64), nullable=False),
        sa.Column("git_branch", sa.String(length=500), nullable=False),
        sa.Column("git_commit", sa.String(length=64), nullable=False),
        sa.Column("git_diff_hash", sa.String(length=64), nullable=True),
        sa.Column("dataset", sa.String(length=200), nullable=False),
        sa.Column("protocol", sa.String(length=200), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("checkpoint", sa.String(length=1500), nullable=True),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("environment", sa.JSON(), nullable=False),
        sa.Column("evidence_snapshot", sa.JSON(), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("schema_version = 1", name="run_manifest_schema_version_one"),
        sa.ForeignKeyConstraint(
            ["approval_record_id"],
            ["approval_records.id"],
            name="fk_run_manifests_approval_record_id_approval_records",
        ),
        sa.ForeignKeyConstraint(
            ["context_id"], ["project_contexts.id"], name="fk_run_manifests_context_id_contexts"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_run_manifests_created_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["intent_id"], ["experiment_intents.id"], name="fk_run_manifests_intent_id_intents"
        ),
        sa.ForeignKeyConstraint(
            ["plan_check_id"], ["plan_checks.id"], name="fk_run_manifests_plan_check_id_checks"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_run_manifests_project_id_projects"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_run_manifests"),
        sa.UniqueConstraint("plan_check_id", name="uq_run_manifests_plan_check"),
        sa.UniqueConstraint(
            "project_id", "idempotency_key", name="uq_run_manifests_project_idempotency"
        ),
        sa.UniqueConstraint("project_id", "manifest_hash", name="uq_run_manifests_project_hash"),
    )


def downgrade() -> None:
    op.drop_table("run_manifests")
    op.drop_table("approval_records")
