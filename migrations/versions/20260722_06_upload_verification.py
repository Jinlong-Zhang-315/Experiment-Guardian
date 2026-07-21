"""persist S3 upload verification evidence

Revision ID: 20260722_06
Revises: 20260721_05
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_06"
down_revision: str | Sequence[str] | None = "20260721_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("experiment_submissions") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(length=12),
            type_=sa.String(length=32),
            existing_nullable=False,
        )
        batch.add_column(sa.Column("upload_verified_at", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column(
                "upload_verified_by",
                sa.Uuid(),
                sa.ForeignKey(
                    "users.id",
                    name="fk_experiment_submissions_upload_verified_by_users",
                ),
            )
        )
        batch.add_column(sa.Column("upload_verification_snapshot", sa.JSON()))
    with op.batch_alter_table("artifacts") as batch:
        batch.add_column(sa.Column("verified_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("verification_evidence", sa.JSON()))
        batch.add_column(sa.Column("s3_version_id", sa.String(length=1024)))


def downgrade() -> None:
    # R9 不认识 UPLOAD_VERIFIED；回滚时退回可重新签发上传地址的 RECEIVED。
    op.execute(
        sa.text(
            "UPDATE experiment_submissions "
            "SET status = 'RECEIVED' WHERE status = 'UPLOAD_VERIFIED'"
        )
    )
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_column("s3_version_id")
        batch.drop_column("verification_evidence")
        batch.drop_column("verified_at")
    with op.batch_alter_table("experiment_submissions") as batch:
        batch.drop_column("upload_verification_snapshot")
        batch.drop_column("upload_verified_by")
        batch.drop_column("upload_verified_at")
        batch.alter_column(
            "status",
            existing_type=sa.String(length=32),
            type_=sa.String(length=12),
            existing_nullable=False,
        )
