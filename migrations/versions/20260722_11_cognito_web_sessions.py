"""add Cognito subject binding and server-side web sessions

Revision ID: 20260722_11
Revises: 20260722_10
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_11"
down_revision: str | Sequence[str] | None = "20260722_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("cognito_sub", sa.String(length=128)))
        batch.create_unique_constraint("uq_users_cognito_sub", ["cognito_sub"])

    op.create_table(
        "web_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("session_hash", sa.String(length=64), nullable=False),
        sa.Column("authenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reauthenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoke_reason", sa.String(length=500)),
        sa.Column("user_agent_hash", sa.String(length=64)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_web_sessions_user_id_users"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name="fk_web_sessions_team_id_teams"),
        sa.PrimaryKeyConstraint("id", name="pk_web_sessions"),
        sa.UniqueConstraint("session_hash", name="uq_web_sessions_session_hash"),
    )
    op.create_index(
        "ix_web_session_user_active",
        "web_sessions",
        ["user_id", "revoked_at", "absolute_expires_at"],
    )

    op.create_table(
        "oidc_transactions",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("session_id", sa.Uuid()),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("purpose IN ('LOGIN', 'REAUTH')", name="oidc_purpose_valid"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["web_sessions.id"], name="fk_oidc_transactions_session_id_web_sessions"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_oidc_transactions"),
        sa.UniqueConstraint("state_hash", name="uq_oidc_transactions_state_hash"),
    )
    op.create_index(
        "ix_oidc_transaction_expiry", "oidc_transactions", ["expires_at", "consumed_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_oidc_transaction_expiry", table_name="oidc_transactions")
    op.drop_table("oidc_transactions")
    op.drop_index("ix_web_session_user_active", table_name="web_sessions")
    op.drop_table("web_sessions")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_cognito_sub", type_="unique")
        batch.drop_column("cognito_sub")
