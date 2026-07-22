"""add transactional outbox and asynchronous submission summary

Revision ID: 20260722_08
Revises: 20260722_07
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_08"
down_revision: str | Sequence[str] | None = "20260722_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("experiment_submissions") as batch:
        batch.add_column(sa.Column("generated_summary", sa.JSON()))

    op.create_table(
        "workflow_jobs",
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=300)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.JSON()),
        sa.Column("sqs_message_id", sa.String(length=300)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("generation >= 1", name="workflow_job_generation_positive"),
        sa.CheckConstraint(
            "job_type = 'SUBMISSION_SUMMARY'",
            name="workflow_job_type_summary_only",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_DISPATCH', 'QUEUED', 'RUNNING', 'RETRYABLE_FAILURE', "
            "'SUCCEEDED', 'DEAD_LETTER', 'FAILED')",
            name="workflow_job_status_valid",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="workflow_job_attempt_count_nonnegative"),
        sa.CheckConstraint("max_attempts >= 1", name="workflow_job_max_attempts_positive"),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["experiment_submissions.id"],
            name="fk_workflow_jobs_submission_id_experiment_submissions",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_jobs"),
        sa.UniqueConstraint("submission_id", "job_type", name="uq_workflow_jobs_submission_type"),
    )
    op.create_index(
        "ix_workflow_jobs_status_available",
        "workflow_jobs",
        ["status", "available_at"],
        unique=False,
    )

    op.create_table(
        "outbox_events",
        sa.Column("workflow_job_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=300)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.JSON()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("sqs_message_id", sa.String(length=300)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("generation >= 1", name="outbox_generation_positive"),
        sa.CheckConstraint(
            "event_type = 'SUBMISSION_SUMMARY_REQUESTED'",
            name="outbox_event_type_summary_only",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PUBLISHING', 'PUBLISHED')",
            name="outbox_status_valid",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="outbox_attempt_count_nonnegative"),
        sa.ForeignKeyConstraint(
            ["workflow_job_id"],
            ["workflow_jobs.id"],
            name="fk_outbox_events_workflow_job_id_workflow_jobs",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_events"),
        sa.UniqueConstraint(
            "workflow_job_id", "generation", name="uq_outbox_events_job_generation"
        ),
    )
    op.create_index(
        "ix_outbox_events_status_available",
        "outbox_events",
        ["status", "available_at"],
        unique=False,
    )


def downgrade() -> None:
    # R11 不认识异步摘要状态；保留已经完成的确定性风险分析结果。
    op.execute(
        sa.text(
            "UPDATE experiment_submissions SET "
            "status = 'PROCESSING', workflow_status = 'AWAITING_ENRICHMENT', "
            "processing_step = 'RISK_ANALYSIS', processing_error = NULL "
            "WHERE id IN (SELECT submission_id FROM workflow_jobs) "
            "OR processing_step = 'SUMMARY_GENERATION' OR workflow_status = 'QUEUED'"
        )
    )
    op.drop_index("ix_outbox_events_status_available", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_workflow_jobs_status_available", table_name="workflow_jobs")
    op.drop_table("workflow_jobs")
    with op.batch_alter_table("experiment_submissions") as batch:
        batch.drop_column("generated_summary")
