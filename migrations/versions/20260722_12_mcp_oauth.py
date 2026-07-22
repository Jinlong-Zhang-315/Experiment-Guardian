"""add pre-registered remote MCP OAuth client and grant state

Revision ID: 20260722_12
Revises: 20260722_11
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_12"
down_revision: str | Sequence[str] | None = "20260722_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_oauth_clients",
        sa.Column("cognito_client_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("allowed_scopes", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoke_reason", sa.String(length=500)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name="fk_mcp_oauth_clients_team"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_mcp_oauth_clients_project"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_mcp_oauth_clients_created_by"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mcp_oauth_clients"),
        sa.UniqueConstraint("cognito_client_id", name="uq_mcp_oauth_clients_cognito_client_id"),
    )
    op.create_table(
        "mcp_oauth_grants",
        sa.Column("mcp_oauth_client_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("granted_scopes", sa.JSON(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoke_reason", sa.String(length=500)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["mcp_oauth_client_id"],
            ["mcp_oauth_clients.id"],
            name="fk_mcp_oauth_grants_client",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_mcp_oauth_grants_user"),
        sa.PrimaryKeyConstraint("id", name="pk_mcp_oauth_grants"),
        sa.UniqueConstraint(
            "mcp_oauth_client_id", "user_id", name="uq_mcp_oauth_grant_principal"
        ),
    )
    op.create_index(
        "ix_mcp_oauth_grant_active", "mcp_oauth_grants", ["user_id", "revoked_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_mcp_oauth_grant_active", table_name="mcp_oauth_grants")
    op.drop_table("mcp_oauth_grants")
    op.drop_table("mcp_oauth_clients")
