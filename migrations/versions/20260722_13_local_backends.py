"""add local queue terminal states and model provider metadata

Revision ID: 20260722_13
Revises: 20260722_12
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_13"
down_revision: str | Sequence[str] | None = "20260722_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("outbox_events") as batch_op:
        batch_op.drop_constraint("outbox_status_valid", type_="check")
        batch_op.create_check_constraint(
            "outbox_status_valid",
            "status IN ('PENDING', 'PUBLISHING', 'PUBLISHED', 'COMPLETED', 'DEAD_LETTER')",
        )
    op.add_column(
        "submission_embeddings",
        sa.Column("provider", sa.String(length=50), server_default="bedrock", nullable=False),
    )
    op.add_column(
        "memories",
        sa.Column(
            "embedding_provider", sa.String(length=50), server_default="bedrock", nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("memories", "embedding_provider")
    op.drop_column("submission_embeddings", "provider")
    op.execute(
        "UPDATE outbox_events SET status = 'PUBLISHED' "
        "WHERE status IN ('COMPLETED', 'DEAD_LETTER')"
    )
    with op.batch_alter_table("outbox_events") as batch_op:
        batch_op.drop_constraint("outbox_status_valid", type_="check")
        batch_op.create_check_constraint(
            "outbox_status_valid", "status IN ('PENDING', 'PUBLISHING', 'PUBLISHED')"
        )
