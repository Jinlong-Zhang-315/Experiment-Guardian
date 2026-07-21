"""persist complete plan check policy snapshots

Revision ID: 20260721_03
Revises: 20260721_02
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_03"
down_revision: str | Sequence[str] | None = "20260721_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Defaults keep the additive migration safe if an early R7 database already contains rows.
    # New application writes always replace these compatibility values with complete snapshots.
    op.add_column(
        "plan_checks",
        sa.Column(
            "input_document_hash",
            sa.String(length=64),
            server_default="UNAVAILABLE",
            nullable=False,
        ),
    )
    for column_name in ("configuration_document", "context_snapshot", "intent_snapshot"):
        op.add_column(
            "plan_checks",
            sa.Column(
                column_name,
                sa.JSON(),
                server_default=sa.text("'{}'"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    op.drop_column("plan_checks", "intent_snapshot")
    op.drop_column("plan_checks", "context_snapshot")
    op.drop_column("plan_checks", "configuration_document")
    op.drop_column("plan_checks", "input_document_hash")
