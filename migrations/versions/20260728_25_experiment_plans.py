"""add versioned natural-language experiment plans

Revision ID: 20260728_25
Revises: 20260728_24
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_25"
down_revision: str | Sequence[str] | None = "20260728_24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(*values: str, name: str, length: int) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=length)


def upgrade() -> None:
    op.create_table(
        "experiment_plans",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("source_thread_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            _enum(
                "REVIEW_QUEUED",
                "REVIEWING",
                "READY_FOR_APPROVAL",
                "NEEDS_USER_INPUT",
                "REVIEW_FAILED",
                "STALE",
                "APPROVED",
                "CONDITIONALLY_APPROVED",
                "REJECTED",
                "CHANGES_REQUESTED",
                name="experiment_plan_status",
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("current_revision >= 1", name="experiment_plan_revision_positive"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["source_thread_id"], ["agent_threads.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_thread_id", name="uq_experiment_plans_source_thread"),
    )
    op.create_index(
        "ix_experiment_plans_project_status_updated",
        "experiment_plans",
        ["project_id", "status", "updated_at"],
    )
    op.create_index(
        "ix_experiment_plans_creator_updated", "experiment_plans", ["created_by", "updated_at"]
    )

    op.create_table(
        "experiment_plan_revisions",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "author_type",
            _enum(
                "EXTERNAL_AGENT",
                "INTERNAL_AGENT",
                "WEB_USER",
                name="experiment_plan_revision_author",
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("author_id", sa.Uuid()),
        sa.Column("parent_revision_id", sa.Uuid()),
        sa.Column("source_run_id", sa.Uuid()),
        sa.Column("automatic_revision_round", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("plan_markdown", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("context_id", sa.Uuid(), nullable=False),
        sa.Column("context_version", sa.Integer(), nullable=False),
        sa.Column("intent_id", sa.Uuid()),
        sa.Column("intent_version", sa.Integer()),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("revision >= 1", name="experiment_plan_revision_number_positive"),
        sa.CheckConstraint(
            "automatic_revision_round >= 0 AND automatic_revision_round <= 2",
            name="experiment_plan_auto_round_range",
        ),
        sa.CheckConstraint("length(policy_hash) = 64", name="experiment_plan_policy_hash_length"),
        sa.CheckConstraint("length(content_hash) = 64", name="experiment_plan_content_hash_length"),
        sa.CheckConstraint(
            "length(evidence_hash) = 64", name="experiment_plan_evidence_hash_length"
        ),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["context_id"], ["project_contexts.id"]),
        sa.ForeignKeyConstraint(["intent_id"], ["experiment_intents.id"]),
        sa.ForeignKeyConstraint(["parent_revision_id"], ["experiment_plan_revisions.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["experiment_plans.id"]),
        sa.ForeignKeyConstraint(["source_run_id"], ["agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "revision", name="uq_experiment_plan_revision"),
    )
    op.create_index(
        "ix_experiment_plan_revisions_plan_created",
        "experiment_plan_revisions",
        ["plan_id", "created_at"],
    )

    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(
            sa.Column(
                "run_kind",
                _enum("CONVERSATION", "EXPERIMENT_PLAN_REVIEW", name="agent_run_kind", length=32),
                server_default="CONVERSATION",
                nullable=False,
            )
        )
        batch.add_column(sa.Column("target_experiment_plan_revision_id", sa.Uuid()))
        batch.create_check_constraint(
            "agent_run_kind_target_consistent",
            "(run_kind = 'CONVERSATION' AND target_experiment_plan_revision_id IS NULL) OR "
            "(run_kind = 'EXPERIMENT_PLAN_REVIEW' AND "
            "target_experiment_plan_revision_id IS NOT NULL)",
        )

    op.create_table(
        "experiment_plan_reviews",
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=False),
        sa.Column("final_message_id", sa.Uuid(), nullable=False),
        sa.Column("hard_check", sa.JSON(), nullable=False),
        sa.Column("semantic_review", sa.JSON(), nullable=False),
        sa.Column("candidate_invariants", sa.JSON(), nullable=False),
        sa.Column("approval_receipt", sa.JSON(), nullable=False),
        sa.Column("review_hash", sa.String(length=64), nullable=False),
        sa.Column("approval_digest", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model_id", sa.String(length=500), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("length(review_hash) = 64", name="experiment_plan_review_hash_length"),
        sa.CheckConstraint(
            "length(approval_digest) = 64", name="experiment_plan_approval_digest_length"
        ),
        sa.ForeignKeyConstraint(["final_message_id"], ["agent_messages.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["experiment_plan_revisions.id"]),
        sa.ForeignKeyConstraint(["source_run_id"], ["agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approval_digest"),
        sa.UniqueConstraint("revision_id", name="uq_experiment_plan_reviews_revision"),
        sa.UniqueConstraint("source_run_id", name="uq_experiment_plan_reviews_source_run"),
    )
    op.create_table(
        "experiment_plan_decisions",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=False),
        sa.Column("decided_session_id", sa.Uuid(), nullable=False),
        sa.Column(
            "decision",
            _enum(
                "APPROVED",
                "CONDITIONALLY_APPROVED",
                "REJECTED",
                "CHANGES_REQUESTED",
                name="experiment_plan_decision",
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("confirmed_candidate_invariants", sa.JSON(), nullable=False),
        sa.Column("rejected_candidate_invariants", sa.JSON(), nullable=False),
        sa.Column("approved_snapshot", sa.JSON(), nullable=False),
        sa.Column("review_hash", sa.String(length=64), nullable=False),
        sa.Column("decision_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("length(review_hash) = 64", name="experiment_plan_decision_review_hash"),
        sa.CheckConstraint("length(decision_hash) = 64", name="experiment_plan_decision_hash"),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["decided_session_id"], ["web_sessions.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["experiment_plans.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["experiment_plan_revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_id", name="uq_experiment_plan_decisions_revision"),
    )


def downgrade() -> None:
    op.drop_table("experiment_plan_decisions")
    op.drop_table("experiment_plan_reviews")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("agent_run_kind_target_consistent", type_="check")
        batch.drop_column("target_experiment_plan_revision_id")
        batch.drop_column("run_kind")
    op.drop_index(
        "ix_experiment_plan_revisions_plan_created", table_name="experiment_plan_revisions"
    )
    op.drop_table("experiment_plan_revisions")
    op.drop_index("ix_experiment_plans_creator_updated", table_name="experiment_plans")
    op.drop_index("ix_experiment_plans_project_status_updated", table_name="experiment_plans")
    op.drop_table("experiment_plans")
