"""persist recoverable submission analysis cursor and risks

Revision ID: 20260722_07
Revises: 20260722_06
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_07"
down_revision: str | Sequence[str] | None = "20260722_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("experiment_submissions") as batch:
        batch.add_column(
            sa.Column(
                "workflow_status",
                sa.String(length=32),
                nullable=False,
                server_default="NOT_STARTED",
            )
        )
        batch.add_column(sa.Column("processing_step", sa.String(length=32)))
        batch.add_column(sa.Column("processing_error", sa.JSON()))
        batch.add_column(sa.Column("analysis_snapshot", sa.JSON()))

    op.create_table(
        "submission_risks",
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("risk_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("risk_type", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("field_path", sa.String(length=1000)),
        sa.Column("previous_value", sa.JSON()),
        sa.Column("current_value", sa.JSON()),
        sa.Column("expected_value", sa.JSON()),
        sa.Column("rule_id", sa.String(length=200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("impact", sa.Text()),
        sa.Column("evidence_type", sa.String(length=32)),
        sa.Column("evidence_source", sa.String(length=500)),
        sa.Column("collected_at", sa.DateTime(timezone=True)),
        sa.Column("collection_tool", sa.String(length=500)),
        sa.Column("constraint_source", sa.String(length=16)),
        sa.Column("constraint_status", sa.String(length=16)),
        sa.Column("inference_basis", sa.Text()),
        sa.Column("confidence", sa.Float()),
        sa.Column("constraint_candidates", sa.JSON(), nullable=False),
        sa.Column("recommendation", sa.Text()),
        sa.Column("blocking", sa.Boolean(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["experiment_submissions.id"],
            name="fk_submission_risks_submission_id_experiment_submissions",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_submission_risks"),
        sa.UniqueConstraint(
            "submission_id",
            "risk_fingerprint",
            name="uq_submission_risks_submission_fingerprint",
        ),
    )
    op.create_index(
        "ix_submission_risks_submission_severity",
        "submission_risks",
        ["submission_id", "severity"],
        unique=False,
    )


def downgrade() -> None:
    # R10 不认识分析中的 PROCESSING/FAILED 状态；保留上传证据并退回 UPLOAD_VERIFIED。
    op.execute(
        sa.text(
            "UPDATE experiment_submissions SET status = 'UPLOAD_VERIFIED' "
            "WHERE status IN ('PROCESSING', 'FAILED') AND upload_verified_at IS NOT NULL"
        )
    )
    op.drop_index("ix_submission_risks_submission_severity", table_name="submission_risks")
    op.drop_table("submission_risks")
    with op.batch_alter_table("experiment_submissions") as batch:
        batch.drop_column("analysis_snapshot")
        batch.drop_column("processing_error")
        batch.drop_column("processing_step")
        batch.drop_column("workflow_status")
