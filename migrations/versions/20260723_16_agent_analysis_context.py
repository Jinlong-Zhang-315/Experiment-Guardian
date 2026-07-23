"""add agent analysis context summaries

Revision ID: 20260723_16
Revises: 20260723_15
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_16"
down_revision: str | Sequence[str] | None = "20260723_15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # current_summary_id 是应用维护的 READY 指针。刻意不建立循环外键，使 SQLite
    # 开发迁移和 CockroachDB 回滚都保持简单；摘要自身仍完整外键到 Thread/Run/Call。
    with op.batch_alter_table("agent_threads") as batch:
        batch.add_column(sa.Column("current_summary_id", sa.Uuid()))
    with op.batch_alter_table("agent_model_calls") as batch:
        batch.add_column(
            sa.Column(
                "purpose",
                sa.String(length=24),
                server_default="AGENT_TURN",
                nullable=False,
            )
        )
        batch.create_check_constraint(
            "purpose_valid",
            "purpose IN ('AGENT_TURN', 'CONTEXT_SUMMARY')",
        )

    op.create_table(
        "agent_context_summaries",
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("covered_sequence_from", sa.Integer(), nullable=False),
        sa.Column("covered_sequence_to", sa.Integer(), nullable=False),
        sa.Column("source_message_ids", sa.JSON(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model_id", sa.String(length=500), nullable=False),
        sa.Column("model_call_id", sa.Uuid()),
        sa.Column("payload", sa.JSON()),
        sa.Column("error", sa.JSON()),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('READY', 'FAILED')",
            name="ck_agent_context_summaries_status_valid",
        ),
        sa.CheckConstraint(
            "(status = 'READY' AND payload IS NOT NULL AND error IS NULL) OR "
            "(status = 'FAILED' AND payload IS NULL AND error IS NOT NULL)",
            name="ck_agent_context_summaries_state_consistent",
        ),
        sa.CheckConstraint(
            "covered_sequence_to >= covered_sequence_from",
            name="ck_agent_context_summaries_sequence_range_valid",
        ),
        sa.CheckConstraint(
            "length(source_hash) = 64",
            name="ck_agent_context_summaries_source_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["agent_threads.id"],
            name="fk_agent_context_summaries_thread_id_agent_threads",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_agent_context_summaries_run_id_agent_runs",
        ),
        sa.ForeignKeyConstraint(
            ["model_call_id"],
            ["agent_model_calls.id"],
            name="fk_agent_context_summaries_model_call_id_agent_model_calls",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_context_summaries"),
        sa.UniqueConstraint(
            "run_id",
            "generation",
            name="uq_agent_context_summaries_run_generation",
        ),
    )
    op.create_index(
        "ix_agent_context_summaries_thread_created",
        "agent_context_summaries",
        ["thread_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_context_summaries_thread_created",
        table_name="agent_context_summaries",
    )
    op.drop_table("agent_context_summaries")
    with op.batch_alter_table("agent_model_calls") as batch:
        batch.drop_constraint("purpose_valid", type_="check")
        batch.drop_column("purpose")
    with op.batch_alter_table("agent_threads") as batch:
        batch.drop_column("current_summary_id")
