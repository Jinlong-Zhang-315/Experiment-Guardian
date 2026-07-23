"""add durable read-only governance agent conversations

Revision ID: 20260723_15
Revises: 20260723_14
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_15"
down_revision: str | Sequence[str] | None = "20260723_14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_and_timestamps(*, updated: bool = False) -> list[sa.Column]:
    columns = [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]
    if updated:
        columns.append(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            )
        )
    return columns


def upgrade() -> None:
    op.create_table(
        "agent_threads",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        *_id_and_timestamps(updated=True),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'ARCHIVED')", name="ck_agent_threads_status_valid"
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name="fk_agent_threads_team_id_teams"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_agent_threads_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_agent_threads_created_by_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_threads"),
    )
    op.create_index(
        "ix_agent_threads_owner_status_updated",
        "agent_threads",
        ["project_id", "created_by", "status", "updated_at"],
    )

    op.create_table(
        "agent_messages",
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("run_id", sa.Uuid()),
        *_id_and_timestamps(),
        sa.CheckConstraint(
            "role IN ('USER', 'ASSISTANT')", name="ck_agent_messages_role_valid"
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_agent_messages_content_sha256_length",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["agent_threads.id"], name="fk_agent_messages_thread_id_agent_threads"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_agent_messages_created_by_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_messages"),
        sa.UniqueConstraint(
            "thread_id", "sequence", name="uq_agent_messages_thread_sequence"
        ),
    )
    op.create_index(
        "ix_agent_messages_thread_created", "agent_messages", ["thread_id", "created_at"]
    )

    op.create_table(
        "agent_runs",
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("auth_session_id", sa.Uuid(), nullable=False),
        sa.Column("trigger_message_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model_id", sa.String(length=500), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=False),
        sa.Column("tool_catalog_version", sa.String(length=50), nullable=False),
        sa.Column("context_snapshot", sa.JSON(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("error", sa.JSON()),
        sa.Column("final_message_id", sa.Uuid()),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("lease_owner", sa.String(length=300)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        *_id_and_timestamps(updated=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'RETRYABLE_FAILURE', 'SUCCEEDED', "
            "'FAILED', 'DEAD_LETTER')",
            name="ck_agent_runs_status_valid",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_agent_runs_attempt_count_nonnegative"
        ),
        sa.CheckConstraint("max_attempts >= 1", name="ck_agent_runs_max_attempts_positive"),
        sa.CheckConstraint("generation >= 0", name="ck_agent_runs_generation_nonnegative"),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["agent_threads.id"], name="fk_agent_runs_thread_id_agent_threads"
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name="fk_agent_runs_team_id_teams"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_agent_runs_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_agent_runs_created_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["auth_session_id"],
            ["web_sessions.id"],
            name="fk_agent_runs_auth_session_id_web_sessions",
        ),
        sa.ForeignKeyConstraint(
            ["trigger_message_id"],
            ["agent_messages.id"],
            name="fk_agent_runs_trigger_message_id_agent_messages",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_runs"),
        sa.UniqueConstraint(
            "thread_id", "idempotency_key", name="uq_agent_runs_thread_idempotency"
        ),
    )
    op.create_index(
        "ix_agent_runs_claim",
        "agent_runs",
        ["status", "available_at", "lease_expires_at", "created_at"],
    )
    op.create_index(
        "ix_agent_runs_thread_created", "agent_runs", ["thread_id", "created_at"]
    )

    op.create_table(
        "agent_model_calls",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("request_snapshot", sa.JSON(), nullable=False),
        sa.Column("response_snapshot", sa.JSON()),
        sa.Column("provider_request_id", sa.String(length=500)),
        sa.Column("finish_reason", sa.String(length=100)),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("error", sa.JSON()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        *_id_and_timestamps(),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'ABANDONED')",
            name="ck_agent_model_calls_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.id"], name="fk_agent_model_calls_run_id_agent_runs"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_model_calls"),
        sa.UniqueConstraint(
            "run_id", "generation", "ordinal", name="uq_agent_model_call_order"
        ),
    )
    op.create_index(
        "ix_agent_model_calls_run",
        "agent_model_calls",
        ["run_id", "generation", "ordinal"],
    )

    op.create_table(
        "agent_tool_calls",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("call_id", sa.String(length=200), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("tool_version", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("arguments_hash", sa.String(length=64), nullable=False),
        sa.Column("output", sa.JSON()),
        sa.Column("output_hash", sa.String(length=64)),
        sa.Column("error", sa.JSON()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        *_id_and_timestamps(),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'ABANDONED')",
            name="ck_agent_tool_calls_status_valid",
        ),
        sa.CheckConstraint(
            "length(arguments_hash) = 64",
            name="ck_agent_tool_calls_arguments_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.id"], name="fk_agent_tool_calls_run_id_agent_runs"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_tool_calls"),
        sa.UniqueConstraint(
            "run_id", "generation", "call_id", name="uq_agent_tool_calls_provider_call"
        ),
        sa.UniqueConstraint(
            "run_id", "generation", "sequence", name="uq_agent_tool_calls_order"
        ),
    )

    op.create_table(
        "agent_citations",
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tool_call_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.String(length=100), nullable=False),
        sa.Column("evidence_kind", sa.String(length=32), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.Uuid()),
        sa.Column("entity_version", sa.String(length=100)),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        *_id_and_timestamps(),
        sa.CheckConstraint(
            "evidence_kind IN ('CONFIRMED_FACT', 'USER_PROVIDED', 'ANALYSIS', 'HYPOTHESIS')",
            name="ck_agent_citations_evidence_kind_valid",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["agent_messages.id"],
            name="fk_agent_citations_message_id_agent_messages",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.id"], name="fk_agent_citations_run_id_agent_runs"
        ),
        sa.ForeignKeyConstraint(
            ["tool_call_id"],
            ["agent_tool_calls.id"],
            name="fk_agent_citations_tool_call_id_agent_tool_calls",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_citations"),
        sa.UniqueConstraint(
            "message_id", "evidence_id", name="uq_agent_citations_message_evidence"
        ),
    )
    op.create_index("ix_agent_citations_run", "agent_citations", ["run_id"])

    op.create_table(
        "agent_run_events",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        *_id_and_timestamps(),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.id"], name="fk_agent_run_events_run_id_agent_runs"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_run_events"),
        sa.UniqueConstraint(
            "run_id", "sequence", name="uq_agent_run_events_sequence"
        ),
    )
    op.create_index(
        "ix_agent_run_events_replay", "agent_run_events", ["run_id", "sequence"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_run_events_replay", table_name="agent_run_events")
    op.drop_table("agent_run_events")
    op.drop_index("ix_agent_citations_run", table_name="agent_citations")
    op.drop_table("agent_citations")
    op.drop_table("agent_tool_calls")
    op.drop_index("ix_agent_model_calls_run", table_name="agent_model_calls")
    op.drop_table("agent_model_calls")
    op.drop_index("ix_agent_runs_thread_created", table_name="agent_runs")
    op.drop_index("ix_agent_runs_claim", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_agent_messages_thread_created", table_name="agent_messages")
    op.drop_table("agent_messages")
    op.drop_index("ix_agent_threads_owner_status_updated", table_name="agent_threads")
    op.drop_table("agent_threads")
