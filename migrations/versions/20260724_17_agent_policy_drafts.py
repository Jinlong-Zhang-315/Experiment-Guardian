"""add append-only agent policy drafts

Revision ID: 20260724_17
Revises: 20260723_16
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260724_17"
down_revision: str | Sequence[str] | None = "20260723_16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_policy_drafts",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("originating_thread_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("base_context_id", sa.Uuid(), nullable=False),
        sa.Column("base_context_version", sa.Integer(), nullable=False),
        sa.Column("base_intent_id", sa.Uuid(), nullable=False),
        sa.Column("base_intent_version", sa.Integer(), nullable=False),
        sa.Column("base_policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("base_policy_hash", sa.String(length=64), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("abandoned_at", sa.DateTime(timezone=True)),
        sa.Column("abandoned_by", sa.Uuid()),
        sa.Column("abandon_reason", sa.Text()),
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
            "status IN ('ACTIVE', 'ABANDONED')",
            name="ck_agent_policy_drafts_status_valid",
        ),
        sa.CheckConstraint(
            "current_revision >= 1",
            name="ck_agent_policy_drafts_current_revision_positive",
        ),
        sa.CheckConstraint(
            "length(base_policy_hash) = 64",
            name="ck_agent_policy_drafts_base_policy_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_agent_policy_drafts_team_id_teams",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_agent_policy_drafts_project_id_projects",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_agent_policy_drafts_created_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["originating_thread_id"],
            ["agent_threads.id"],
            name="fk_agent_policy_drafts_originating_thread_id_agent_threads",
        ),
        sa.ForeignKeyConstraint(
            ["base_context_id"],
            ["project_contexts.id"],
            name="fk_agent_policy_drafts_base_context_id_project_contexts",
        ),
        sa.ForeignKeyConstraint(
            ["base_intent_id"],
            ["experiment_intents.id"],
            name="fk_agent_policy_drafts_base_intent_id_experiment_intents",
        ),
        sa.ForeignKeyConstraint(
            ["abandoned_by"],
            ["users.id"],
            name="fk_agent_policy_drafts_abandoned_by_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_policy_drafts"),
    )
    op.create_index(
        "ix_agent_policy_drafts_project_status_updated",
        "agent_policy_drafts",
        ["project_id", "status", "updated_at"],
    )
    op.create_index(
        "ix_agent_policy_drafts_creator_status_updated",
        "agent_policy_drafts",
        ["created_by", "status", "updated_at"],
    )

    op.create_table(
        "agent_policy_draft_revisions",
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_run_id", sa.Uuid()),
        sa.Column("source_tool_call_id", sa.Uuid()),
        sa.Column("candidate_payload", sa.JSON(), nullable=False),
        sa.Column("candidate_hash", sa.String(length=64), nullable=False),
        sa.Column("source_request_hash", sa.String(length=64), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=False),
        sa.Column("unresolved_ambiguities", sa.JSON(), nullable=False),
        sa.Column("readiness", sa.String(length=32), nullable=False),
        sa.Column("validation_report", sa.JSON(), nullable=False),
        sa.Column("diff_snapshot", sa.JSON(), nullable=False),
        sa.Column("narrative_snapshot", sa.JSON(), nullable=False),
        sa.Column("impact_snapshot", sa.JSON(), nullable=False),
        sa.Column("pending_state_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('AGENT', 'WEB')",
            name="ck_agent_policy_draft_revisions_source_valid",
        ),
        sa.CheckConstraint(
            "readiness IN ('READY', 'NEEDS_CLARIFICATION', 'INVALID')",
            name="ck_agent_policy_draft_revisions_readiness_valid",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_agent_policy_draft_revisions_revision_positive",
        ),
        sa.CheckConstraint(
            "length(candidate_hash) = 64",
            name="ck_agent_policy_draft_revisions_candidate_hash_length",
        ),
        sa.CheckConstraint(
            "length(source_request_hash) = 64",
            name="ck_agent_policy_draft_revisions_source_request_hash_length",
        ),
        sa.CheckConstraint(
            "length(pending_state_hash) = 64",
            name="ck_agent_policy_draft_revisions_pending_state_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["agent_policy_drafts.id"],
            name="fk_agent_policy_draft_revisions_draft_id_agent_policy_drafts",
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name="fk_agent_policy_draft_revisions_author_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["agent_runs.id"],
            name="fk_agent_policy_draft_revisions_source_run_id_agent_runs",
        ),
        sa.ForeignKeyConstraint(
            ["source_tool_call_id"],
            ["agent_tool_calls.id"],
            name="fk_agent_policy_draft_revisions_source_tool_call",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_policy_draft_revisions"),
        sa.UniqueConstraint(
            "draft_id",
            "revision",
            name="uq_agent_policy_draft_revisions_draft_revision",
        ),
        sa.UniqueConstraint(
            "source_run_id",
            name="uq_agent_policy_draft_revisions_source_run",
        ),
    )
    op.create_index(
        "ix_agent_policy_draft_revisions_draft_created",
        "agent_policy_draft_revisions",
        ["draft_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_policy_draft_revisions_draft_created",
        table_name="agent_policy_draft_revisions",
    )
    op.drop_table("agent_policy_draft_revisions")
    op.drop_index(
        "ix_agent_policy_drafts_creator_status_updated",
        table_name="agent_policy_drafts",
    )
    op.drop_index(
        "ix_agent_policy_drafts_project_status_updated",
        table_name="agent_policy_drafts",
    )
    op.drop_table("agent_policy_drafts")
