"""add submission embedding and deterministic review receipt

Revision ID: 20260722_09
Revises: 20260722_08
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from experiment_guardian.infrastructure.models.base import VectorType

revision: str = "20260722_09"
down_revision: str | Sequence[str] | None = "20260722_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("experiment_submissions") as batch:
        batch.add_column(sa.Column("review_receipt", sa.JSON()))

    with op.batch_alter_table("workflow_jobs") as batch:
        batch.drop_constraint("workflow_job_type_summary_only", type_="check")
        batch.create_check_constraint(
            "workflow_job_type_valid",
            "job_type IN ('SUBMISSION_SUMMARY', 'SUBMISSION_REVIEW_PREPARATION')",
        )

    with op.batch_alter_table("outbox_events") as batch:
        batch.drop_constraint("outbox_event_type_summary_only", type_="check")
        batch.create_check_constraint(
            "outbox_event_type_valid",
            "event_type IN ('SUBMISSION_SUMMARY_REQUESTED', "
            "'SUBMISSION_REVIEW_PREPARATION_REQUESTED')",
        )

    op.create_table(
        "submission_embeddings",
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("embedding", VectorType(1024), nullable=False),
        sa.Column("model_id", sa.String(length=500), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("normalized", sa.Boolean(), nullable=False),
        sa.Column("document_version", sa.String(length=100), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("input_token_count", sa.Integer()),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("dimension = 1024", name="submission_embedding_dimension_1024"),
        sa.CheckConstraint("normalized", name="submission_embedding_normalized"),
        sa.CheckConstraint(
            "length(input_sha256) = 64",
            name="submission_embedding_input_sha256_length",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["experiment_submissions.id"],
            name="fk_submission_embeddings_submission_id_experiment_submissions",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_submission_embeddings_project_id_projects",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_submission_embeddings"),
        sa.UniqueConstraint("submission_id", name="uq_submission_embeddings_submission_id"),
    )


def downgrade() -> None:
    # R12a 只认识摘要终点。先回退业务游标，再删除 Review Job/Outbox。
    op.execute(
        sa.text(
            "UPDATE experiment_submissions SET "
            "status = 'PROCESSING', workflow_status = 'AWAITING_ENRICHMENT', "
            "processing_step = 'SUMMARY_GENERATION', processing_error = NULL, "
            "review_receipt = NULL "
            "WHERE review_receipt IS NOT NULL "
            "OR processing_step IN ('EMBEDDING_GENERATION', 'NEEDS_REVIEW') "
            "OR id IN (SELECT submission_id FROM workflow_jobs "
            "WHERE job_type = 'SUBMISSION_REVIEW_PREPARATION')"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM outbox_events WHERE workflow_job_id IN "
            "(SELECT id FROM workflow_jobs "
            "WHERE job_type = 'SUBMISSION_REVIEW_PREPARATION')"
        )
    )
    op.execute(
        sa.text("DELETE FROM workflow_jobs WHERE job_type = 'SUBMISSION_REVIEW_PREPARATION'")
    )
    op.drop_table("submission_embeddings")

    with op.batch_alter_table("outbox_events") as batch:
        batch.drop_constraint("outbox_event_type_valid", type_="check")
        batch.create_check_constraint(
            "outbox_event_type_summary_only",
            "event_type = 'SUBMISSION_SUMMARY_REQUESTED'",
        )

    with op.batch_alter_table("workflow_jobs") as batch:
        batch.drop_constraint("workflow_job_type_valid", type_="check")
        batch.create_check_constraint(
            "workflow_job_type_summary_only", "job_type = 'SUBMISSION_SUMMARY'"
        )

    with op.batch_alter_table("experiment_submissions") as batch:
        batch.drop_column("review_receipt")
