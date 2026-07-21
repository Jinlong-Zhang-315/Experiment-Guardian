"""persist submission upload drafts and artifacts

Revision ID: 20260721_05
Revises: 20260721_04
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_05"
down_revision: str | Sequence[str] | None = "20260721_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experiment_submissions",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("run_manifest_id", sa.Uuid(), nullable=False),
        sa.Column("submitted_by", sa.Uuid(), nullable=False),
        sa.Column("source_agent", sa.String(length=300), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "declared_experiment_status",
            sa.Enum(
                "COMPLETED",
                "FAILED",
                name="submitted_run_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("declared_metrics", sa.JSON(), nullable=False),
        sa.Column("evidence_snapshot", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "RECEIVED",
                "PROCESSING",
                "NEEDS_REVIEW",
                "APPROVED",
                "REJECTED",
                "FAILED",
                name="submission_status",
                native_enum=False,
            ),
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "declared_experiment_status IN ('COMPLETED', 'FAILED')",
            name="submission_declared_status_final",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_experiment_submissions_project_id_projects",
        ),
        sa.ForeignKeyConstraint(
            ["run_manifest_id"],
            ["run_manifests.id"],
            name="fk_experiment_submissions_run_manifest_id_run_manifests",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by"],
            ["users.id"],
            name="fk_experiment_submissions_submitted_by_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_submissions"),
        sa.UniqueConstraint(
            "project_id",
            "submitted_by",
            "idempotency_key",
            name="uq_experiment_submissions_actor_idempotency",
        ),
    )

    op.create_table(
        "artifacts",
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        # experiments 表在后续 revision 创建，R9 保留 nullable 追溯列但不提前建立外键。
        sa.Column("experiment_id", sa.Uuid(), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=200), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("s3_key", sa.String(length=1500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "artifact_type",
            sa.Enum(
                "CONFIG",
                "LOG",
                "RESULT",
                "NOTE",
                "MANIFEST",
                name="artifact_type",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("cloud_hash_verified", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "size_bytes > 0 AND size_bytes <= 20971520",
            name="artifact_size_limit",
        ),
        sa.CheckConstraint("length(sha256) = 64", name="artifact_sha256_length"),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["experiment_submissions.id"],
            name="fk_artifacts_submission_id_experiment_submissions",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_artifacts"),
        sa.UniqueConstraint("submission_id", "filename", name="uq_artifacts_submission_filename"),
        sa.UniqueConstraint("s3_key", name="uq_artifacts_s3_key"),
    )
    op.create_index(
        "ix_artifact_submission_type",
        "artifacts",
        ["submission_id", "artifact_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_artifact_submission_type", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_table("experiment_submissions")
