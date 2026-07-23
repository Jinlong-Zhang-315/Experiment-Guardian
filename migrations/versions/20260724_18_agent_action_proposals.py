"""add agent action proposals

Revision ID: 20260724_18
Revises: 20260724_17
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260724_18"
down_revision: str | Sequence[str] | None = "20260724_17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_action_proposals",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("source_thread_id", sa.Uuid(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=False),
        sa.Column("source_tool_call_id", sa.Uuid(), nullable=False),
        sa.Column("source_draft_id", sa.Uuid(), nullable=False),
        sa.Column("source_draft_revision_id", sa.Uuid(), nullable=False),
        sa.Column("source_draft_revision", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("source_candidate_hash", sa.String(length=64), nullable=False),
        sa.Column("base_context_id", sa.Uuid(), nullable=False),
        sa.Column("base_context_version", sa.Integer(), nullable=False),
        sa.Column("base_intent_id", sa.Uuid(), nullable=False),
        sa.Column("base_intent_version", sa.Integer(), nullable=False),
        sa.Column("base_policy_hash", sa.String(length=64), nullable=False),
        sa.Column("diff_snapshot", sa.JSON(), nullable=False),
        sa.Column("impact_snapshot", sa.JSON(), nullable=False),
        sa.Column("pending_state_hash", sa.String(length=64), nullable=False),
        sa.Column("proposal_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_by", sa.Uuid()),
        sa.Column("confirmed_session_id", sa.Uuid()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("canceled_by", sa.Uuid()),
        sa.Column("canceled_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_reason", sa.Text()),
        sa.Column("executed_context_id", sa.Uuid()),
        sa.Column("executed_context_version", sa.Integer()),
        sa.Column("execution_result", sa.JSON()),
        sa.Column("execution_error", sa.JSON()),
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
            "operation IN ('POLICY_PUBLISH')",
            name="ck_agent_action_proposals_operation_valid",
        ),
        sa.CheckConstraint(
            "status IN ('PROPOSED', 'EXECUTED', 'CANCELED', 'STALE', "
            "'EXPIRED', 'FAILED')",
            name="ck_agent_action_proposals_status_valid",
        ),
        sa.CheckConstraint(
            "source_draft_revision >= 1",
            name="ck_agent_action_proposals_source_revision_positive",
        ),
        sa.CheckConstraint(
            "length(payload_hash) = 64",
            name="ck_agent_action_proposals_payload_hash_length",
        ),
        sa.CheckConstraint(
            "length(source_candidate_hash) = 64",
            name="ck_agent_action_proposals_candidate_hash_length",
        ),
        sa.CheckConstraint(
            "length(base_policy_hash) = 64",
            name="ck_agent_action_proposals_base_hash_length",
        ),
        sa.CheckConstraint(
            "length(pending_state_hash) = 64",
            name="ck_agent_action_proposals_pending_hash_length",
        ),
        sa.CheckConstraint(
            "length(proposal_digest) = 64",
            name="ck_agent_action_proposals_digest_length",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name="fk_agent_action_proposals_team"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_agent_action_proposals_project"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_agent_action_proposals_creator"
        ),
        sa.ForeignKeyConstraint(
            ["source_thread_id"],
            ["agent_threads.id"],
            name="fk_agent_action_proposals_thread",
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"], ["agent_runs.id"], name="fk_agent_action_proposals_run"
        ),
        sa.ForeignKeyConstraint(
            ["source_tool_call_id"],
            ["agent_tool_calls.id"],
            name="fk_agent_action_proposals_tool_call",
        ),
        sa.ForeignKeyConstraint(
            ["source_draft_id"],
            ["agent_policy_drafts.id"],
            name="fk_agent_action_proposals_draft",
        ),
        sa.ForeignKeyConstraint(
            ["source_draft_revision_id"],
            ["agent_policy_draft_revisions.id"],
            name="fk_agent_action_proposals_draft_revision",
        ),
        sa.ForeignKeyConstraint(
            ["base_context_id"],
            ["project_contexts.id"],
            name="fk_agent_action_proposals_base_context",
        ),
        sa.ForeignKeyConstraint(
            ["base_intent_id"],
            ["experiment_intents.id"],
            name="fk_agent_action_proposals_base_intent",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"], ["users.id"], name="fk_agent_action_proposals_confirmer"
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_session_id"],
            ["web_sessions.id"],
            name="fk_agent_action_proposals_session",
        ),
        sa.ForeignKeyConstraint(
            ["canceled_by"], ["users.id"], name="fk_agent_action_proposals_canceler"
        ),
        sa.ForeignKeyConstraint(
            ["executed_context_id"],
            ["project_contexts.id"],
            name="fk_agent_action_proposals_executed_context",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_action_proposals"),
        sa.UniqueConstraint(
            "source_run_id",
            name="uq_agent_action_proposals_source_run",
        ),
        sa.UniqueConstraint(
            "proposal_digest",
            name="uq_agent_action_proposals_digest",
        ),
    )
    op.create_index(
        "ix_agent_action_proposals_project_status_created",
        "agent_action_proposals",
        ["project_id", "status", "created_at"],
    )
    op.create_index(
        "ix_agent_action_proposals_creator_status_created",
        "agent_action_proposals",
        ["created_by", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_action_proposals_creator_status_created",
        table_name="agent_action_proposals",
    )
    op.drop_index(
        "ix_agent_action_proposals_project_status_created",
        table_name="agent_action_proposals",
    )
    op.drop_table("agent_action_proposals")
