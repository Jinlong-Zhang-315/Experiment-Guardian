"""add immutable agent research reports

Revision ID: 20260727_21
Revises: 20260727_20
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_21"
down_revision: str | Sequence[str] | None = "20260727_20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_research_reports",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("source_thread_id", sa.Uuid(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=False),
        sa.Column("source_tool_call_id", sa.Uuid(), nullable=False),
        sa.Column("final_message_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("experiment_ids", sa.JSON(), nullable=False),
        sa.Column("metric_name", sa.String(length=200)),
        sa.Column("include_historical", sa.Boolean(), nullable=False),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("report_payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model_id", sa.String(length=500), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(source_hash) = 64",
            name="ck_agent_research_reports_source_hash_length",
        ),
        sa.CheckConstraint(
            "length(payload_hash) = 64",
            name="ck_agent_research_reports_payload_hash_length",
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name="ck_agent_research_reports_schema_version",
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_thread_id"], ["agent_threads.id"]),
        sa.ForeignKeyConstraint(["source_run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["source_tool_call_id"], ["agent_tool_calls.id"]),
        sa.ForeignKeyConstraint(["final_message_id"], ["agent_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_run_id", name="uq_agent_research_reports_source_run"),
        sa.UniqueConstraint(
            "source_tool_call_id", name="uq_agent_research_reports_source_tool_call"
        ),
        sa.UniqueConstraint(
            "final_message_id", name="uq_agent_research_reports_final_message"
        ),
    )
    op.create_index(
        "ix_agent_research_reports_project_created",
        "agent_research_reports",
        ["project_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_research_reports_project_created",
        table_name="agent_research_reports",
    )
    op.drop_table("agent_research_reports")
