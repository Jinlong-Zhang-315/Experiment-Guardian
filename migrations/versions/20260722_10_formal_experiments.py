"""add formal experiments, metrics, and confirmed memories

Revision ID: 20260722_10
Revises: 20260722_09
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from experiment_guardian.infrastructure.models.base import VectorType

revision: str = "20260722_10"
down_revision: str | Sequence[str] | None = "20260722_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("run_manifest_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("project_context_id", sa.Uuid(), nullable=False),
        sa.Column("project_context_version", sa.Integer(), nullable=False),
        sa.Column("intent_version", sa.Integer(), nullable=False),
        sa.Column("approval_record_id", sa.Uuid(), nullable=False),
        sa.Column(
            "experiment_mode",
            sa.Enum("FORMAL", "EXPLORATORY", name="experiment_mode", native_enum=False),
            nullable=False,
        ),
        sa.Column("eligible_as_baseline", sa.Boolean(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("model_name", sa.String(length=300), nullable=False),
        sa.Column("dataset", sa.String(length=200), nullable=False),
        sa.Column("protocol", sa.String(length=200), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "COMPLETED",
                "FAILED",
                "DEPRECATED",
                "SUPERSEDED",
                name="experiment_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("git_commit", sa.String(length=64), nullable=False),
        sa.Column("checkpoint", sa.String(length=1500)),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("summary_snapshot", sa.JSON(), nullable=False),
        sa.Column("review_receipt_snapshot", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_by", sa.Uuid(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "NOT (experiment_mode = 'EXPLORATORY' AND eligible_as_baseline)",
            name="exploratory_not_eligible_as_baseline",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_experiments_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["intent_id"],
            ["experiment_intents.id"],
            name="fk_experiments_intent_id_experiment_intents",
        ),
        sa.ForeignKeyConstraint(
            ["run_manifest_id"],
            ["run_manifests.id"],
            name="fk_experiments_run_manifest_id_run_manifests",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["experiment_submissions.id"],
            name="fk_experiments_submission_id_experiment_submissions",
        ),
        sa.ForeignKeyConstraint(
            ["project_context_id"],
            ["project_contexts.id"],
            name="fk_experiments_project_context_id_project_contexts",
        ),
        sa.ForeignKeyConstraint(
            ["approval_record_id"],
            ["approval_records.id"],
            name="fk_experiments_approval_record_id_approval_records",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"], ["users.id"], name="fk_experiments_confirmed_by_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experiments"),
        sa.UniqueConstraint("submission_id", name="uq_experiments_submission_id"),
    )
    op.create_index(
        "ix_experiment_project_status", "experiments", ["project_id", "status"]
    )

    op.create_table(
        "experiment_metrics",
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("split", sa.String(length=100), nullable=False),
        sa.Column("aggregation_type", sa.String(length=100), nullable=False),
        sa.Column("epoch", sa.Integer()),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.id"],
            name="fk_experiment_metrics_experiment_id_experiments",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_metrics"),
        sa.UniqueConstraint(
            "experiment_id", "name", name="uq_experiment_metrics_name"
        ),
    )

    op.create_table(
        "memories",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("protocol", sa.String(length=200), nullable=False),
        sa.Column("model_name", sa.String(length=300), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column(
            "experiment_status",
            sa.Enum(
                "COMPLETED",
                "FAILED",
                "DEPRECATED",
                "SUPERSEDED",
                name="memory_experiment_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("current_valid", sa.Boolean(), nullable=False),
        sa.Column("memory_type", sa.String(length=100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", VectorType(1024), nullable=False),
        sa.Column("embedding_model_id", sa.String(length=500), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("embedding_normalized", sa.Boolean(), nullable=False),
        sa.Column("document_version", sa.String(length=100), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "verification_status",
            sa.Enum(
                "PENDING",
                "CONFIRMED",
                "REJECTED",
                "SUPERSEDED",
                name="memory_verification_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(length=100), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "embedding_dimension = 1024", name="memory_embedding_dimension_1024"
        ),
        sa.CheckConstraint("embedding_normalized", name="memory_embedding_normalized"),
        sa.CheckConstraint(
            "length(content_sha256) = 64", name="memory_content_sha256_length"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_memories_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.id"],
            name="fk_memories_experiment_id_experiments",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memories"),
        sa.UniqueConstraint(
            "experiment_id", "memory_type", name="uq_memories_experiment_type"
        ),
    )
    op.create_index(
        "ix_memory_structured_filter",
        "memories",
        [
            "project_id",
            "verification_status",
            "experiment_status",
            "protocol",
            "current_valid",
        ],
    )

    with op.batch_alter_table("artifacts") as batch:
        batch.create_foreign_key(
            "fk_artifacts_experiment_id_experiments",
            "experiments",
            ["experiment_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_constraint("fk_artifacts_experiment_id_experiments", type_="foreignkey")
    op.drop_index("ix_memory_structured_filter", table_name="memories")
    op.drop_table("memories")
    op.drop_table("experiment_metrics")
    op.drop_index("ix_experiment_project_status", table_name="experiments")
    op.drop_table("experiments")
