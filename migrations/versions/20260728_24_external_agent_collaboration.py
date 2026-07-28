"""add external MCP Agent task and durable credential bindings

Revision ID: 20260728_24
Revises: 20260727_23
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_24"
down_revision: str | Sequence[str] | None = "20260727_23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_threads") as batch:
        batch.add_column(
            sa.Column(
                "origin",
                sa.Enum(
                    "WEB",
                    "EXTERNAL_MCP",
                    name="agent_thread_origin",
                    native_enum=False,
                    length=16,
                ),
                server_default="WEB",
                nullable=False,
            )
        )
        batch.add_column(sa.Column("start_idempotency_key", sa.Uuid()))
        batch.add_column(sa.Column("start_request_hash", sa.String(length=64)))
        batch.add_column(sa.Column("task_context_snapshot", sa.JSON()))
        batch.add_column(sa.Column("task_context_hash", sa.String(length=64)))
        batch.create_unique_constraint(
            "uq_agent_threads_external_start_idempotency",
            ["project_id", "created_by", "origin", "start_idempotency_key"],
        )
        batch.create_check_constraint(
            "agent_thread_origin_payload_consistent",
            "(origin = 'WEB' AND start_idempotency_key IS NULL "
            "AND start_request_hash IS NULL AND task_context_snapshot IS NULL "
            "AND task_context_hash IS NULL) OR "
            "(origin = 'EXTERNAL_MCP' AND start_idempotency_key IS NOT NULL "
            "AND start_request_hash IS NOT NULL AND task_context_snapshot IS NOT NULL "
            "AND task_context_hash IS NOT NULL)",
        )
        batch.create_check_constraint(
            "agent_thread_start_request_hash_length",
            "start_request_hash IS NULL OR length(start_request_hash) = 64",
        )
        batch.create_check_constraint(
            "agent_thread_task_context_hash_length",
            "task_context_hash IS NULL OR length(task_context_hash) = 64",
        )

    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(
            sa.Column(
                "auth_method",
                sa.Enum(
                    "WEB_SESSION",
                    "MCP_TOKEN",
                    "MCP_OAUTH",
                    name="agent_run_auth_method",
                    native_enum=False,
                    length=16,
                ),
                server_default="WEB_SESSION",
                nullable=False,
            )
        )
        batch.add_column(sa.Column("auth_access_token_id", sa.Uuid()))
        batch.add_column(sa.Column("auth_oauth_grant_id", sa.Uuid()))
        batch.add_column(
            sa.Column("auth_scopes_snapshot", sa.JSON(), server_default="[]", nullable=False)
        )
        batch.add_column(sa.Column("auth_expires_at", sa.DateTime(timezone=True)))
        batch.alter_column("auth_session_id", existing_type=sa.Uuid(), nullable=True)
        batch.create_foreign_key(
            "fk_agent_runs_auth_access_token_id_access_tokens",
            "access_tokens",
            ["auth_access_token_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_agent_runs_auth_oauth_grant_id_mcp_oauth_grants",
            "mcp_oauth_grants",
            ["auth_oauth_grant_id"],
            ["id"],
        )
        batch.create_check_constraint(
            "agent_run_auth_binding_consistent",
            "(auth_method = 'WEB_SESSION' AND auth_session_id IS NOT NULL "
            "AND auth_access_token_id IS NULL AND auth_oauth_grant_id IS NULL) OR "
            "(auth_method = 'MCP_TOKEN' AND auth_session_id IS NULL "
            "AND auth_access_token_id IS NOT NULL AND auth_oauth_grant_id IS NULL) OR "
            "(auth_method = 'MCP_OAUTH' AND auth_session_id IS NULL "
            "AND auth_access_token_id IS NULL AND auth_oauth_grant_id IS NOT NULL "
            "AND auth_expires_at IS NOT NULL)",
        )


def downgrade() -> None:
    connection = op.get_bind()
    external_task = connection.execute(
        sa.text("SELECT id FROM agent_threads WHERE origin = 'EXTERNAL_MCP' LIMIT 1")
    ).first()
    if external_task is not None:
        raise RuntimeError(
            "revision 24 已包含外部 MCP Agent 任务，不能降级为仅 Web Session 的模型"
        )

    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("agent_run_auth_binding_consistent", type_="check")
        batch.drop_constraint(
            "fk_agent_runs_auth_oauth_grant_id_mcp_oauth_grants", type_="foreignkey"
        )
        batch.drop_constraint(
            "fk_agent_runs_auth_access_token_id_access_tokens", type_="foreignkey"
        )
        batch.alter_column("auth_session_id", existing_type=sa.Uuid(), nullable=False)
        batch.drop_column("auth_expires_at")
        batch.drop_column("auth_scopes_snapshot")
        batch.drop_column("auth_oauth_grant_id")
        batch.drop_column("auth_access_token_id")
        batch.drop_column("auth_method")

    with op.batch_alter_table("agent_threads") as batch:
        batch.drop_constraint("agent_thread_task_context_hash_length", type_="check")
        batch.drop_constraint("agent_thread_start_request_hash_length", type_="check")
        batch.drop_constraint("agent_thread_origin_payload_consistent", type_="check")
        batch.drop_constraint(
            "uq_agent_threads_external_start_idempotency", type_="unique"
        )
        batch.drop_column("task_context_hash")
        batch.drop_column("task_context_snapshot")
        batch.drop_column("start_request_hash")
        batch.drop_column("start_idempotency_key")
        batch.drop_column("origin")
