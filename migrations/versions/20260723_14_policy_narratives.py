"""add version-bound human-readable policy narratives

Revision ID: 20260723_14
Revises: 20260722_13
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_14"
down_revision: str | Sequence[str] | None = "20260722_13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "policy_narratives",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("context_id", sa.Uuid(), nullable=False),
        sa.Column("context_version", sa.Integer(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("intent_version", sa.Integer(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("format", sa.String(length=20), nullable=False),
        sa.Column("generator", sa.String(length=50), nullable=False),
        sa.Column("generator_version", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("generated_by", sa.Uuid(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('READY', 'FAILED')",
            name="ck_policy_narratives_status_valid",
        ),
        sa.CheckConstraint(
            "(status = 'READY' AND content IS NOT NULL AND source_hash IS NOT NULL "
            "AND generated_at IS NOT NULL AND error IS NULL) OR "
            "(status = 'FAILED' AND content IS NULL AND error IS NOT NULL)",
            name="ck_policy_narratives_state_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["context_id"],
            ["project_contexts.id"],
            name="fk_policy_narratives_context_id_project_contexts",
        ),
        sa.ForeignKeyConstraint(
            ["generated_by"],
            ["users.id"],
            name="fk_policy_narratives_generated_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["intent_id"],
            ["experiment_intents.id"],
            name="fk_policy_narratives_intent_id_experiment_intents",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_policy_narratives_project_id_projects",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_policy_narratives"),
        sa.UniqueConstraint(
            "context_id",
            "intent_id",
            name="uq_policy_narratives_source_version",
        ),
    )
    op.create_index(
        "ix_policy_narrative_project_version",
        "policy_narratives",
        ["project_id", "context_version"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_policy_narrative_project_version", table_name="policy_narratives")
    op.drop_table("policy_narratives")
