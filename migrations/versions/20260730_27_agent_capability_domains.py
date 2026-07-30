"""add deterministic capability domains to governance Agent threads

Revision ID: 20260730_27
Revises: 20260728_26
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_27"
down_revision: str | Sequence[str] | None = "20260728_26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_threads") as batch:
        batch.add_column(
            sa.Column(
                "capability_domain",
                sa.String(length=32),
                server_default="GENERAL",
                nullable=False,
            )
        )
        batch.create_check_constraint(
            "agent_thread_capability_domain_valid",
            "capability_domain IN ('GENERAL', 'ANALYSIS', 'POLICY', 'RESEARCH', 'PROPOSAL')",
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_threads") as batch:
        batch.drop_constraint("agent_thread_capability_domain_valid", type_="check")
        batch.drop_column("capability_domain")
