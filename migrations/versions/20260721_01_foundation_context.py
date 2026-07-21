"""foundation identity and formal context tables

Revision ID: 20260721_01
Revises:
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_01"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps(*, updated: bool = False) -> list[sa.Column]:
    columns = [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        )
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
        "users",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "teams",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_teams_owner_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_teams"),
    )
    op.create_table(
        "team_members",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("OWNER", "RESEARCHER", name="team_role", native_enum=False),
            nullable=False,
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name="fk_team_members_team_id_teams"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_team_members_user_id_users"),
        sa.PrimaryKeyConstraint("team_id", "user_id", name="pk_team_members"),
    )
    op.create_table(
        "projects",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("repository_url", sa.String(length=1000), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(updated=True),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name="fk_projects_team_id_teams"),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
        sa.UniqueConstraint("team_id", "name", name="uq_projects_team_id"),
    )
    op.create_index("ix_projects_team_id", "projects", ["team_id"], unique=False)

    op.create_table(
        "project_contexts",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("non_goals", sa.JSON(), nullable=False),
        sa.Column("mainline_model", sa.String(length=500), nullable=False),
        sa.Column("baseline", sa.JSON(), nullable=False),
        sa.Column("dataset", sa.String(length=200), nullable=False),
        sa.Column("protocol", sa.String(length=200), nullable=False),
        sa.Column("primary_metric", sa.JSON(), nullable=False),
        sa.Column("default_seeds", sa.JSON(), nullable=False),
        sa.Column("active_branch", sa.String(length=500), nullable=False),
        sa.Column("active_config", sa.JSON(), nullable=False),
        sa.Column("deprecated_items", sa.JSON(), nullable=False),
        sa.Column("key_decisions", sa.JSON(), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "ACTIVE", "SUPERSEDED", name="context_status", native_enum=False),
            nullable=False,
        ),
        sa.Column("supersedes_context_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "status != 'ACTIVE' OR "
            "(confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL AND effective_at IS NOT NULL)",
            name="ck_project_contexts_active_context_requires_confirmation",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"], ["users.id"], name="fk_project_contexts_confirmed_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_project_contexts_created_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_project_contexts_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_context_id"],
            ["project_contexts.id"],
            name="fk_project_contexts_supersedes_context_id_project_contexts",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_project_contexts"),
        sa.UniqueConstraint("project_id", "version", name="uq_project_contexts_project_id"),
    )
    op.create_index(
        "ix_project_context_active", "project_contexts", ["project_id", "status"], unique=False
    )

    op.create_table(
        "experiment_intents",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("context_id", sa.Uuid(), nullable=False),
        sa.Column("context_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_intent_id", sa.Uuid(), nullable=True),
        sa.Column(
            "experiment_mode",
            sa.Enum("FORMAL", "EXPLORATORY", name="intent_experiment_mode", native_enum=False),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("allowed_variables", sa.JSON(), nullable=False),
        sa.Column("controlled_variables", sa.JSON(), nullable=False),
        sa.Column("expected_outputs", sa.JSON(), nullable=False),
        sa.Column("acceptance_criteria", sa.JSON(), nullable=False),
        sa.Column(
            "source_type",
            sa.Enum("EXPLICIT", "INFERRED", name="intent_source_type", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "verification_status",
            sa.Enum(
                "PENDING",
                "CONFIRMED",
                "REJECTED",
                "SUPERSEDED",
                name="intent_verification_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("original_message", sa.Text(), nullable=False),
        sa.Column("inference_basis", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("unresolved_ambiguities", sa.JSON(), nullable=False),
        sa.Column("intent_receipt", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT", "ACTIVE", "CLOSED", "CANCELLED", name="intent_status", native_enum=False
            ),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_by", sa.Uuid(), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "verification_status != 'CONFIRMED' OR "
            "(confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)",
            name="ck_experiment_intents_confirmed_intent_requires_actor",
        ),
        sa.CheckConstraint(
            "status != 'ACTIVE' OR "
            "(verification_status = 'CONFIRMED' AND activated_by IS NOT NULL "
            "AND activated_at IS NOT NULL)",
            name="ck_experiment_intents_active_intent_requires_confirmed_version",
        ),
        sa.ForeignKeyConstraint(
            ["activated_by"], ["users.id"], name="fk_experiment_intents_activated_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"], ["users.id"], name="fk_experiment_intents_confirmed_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["context_id"],
            ["project_contexts.id"],
            name="fk_experiment_intents_context_id_project_contexts",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_experiment_intents_created_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_experiment_intents_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_intent_id"],
            ["experiment_intents.id"],
            name="fk_experiment_intents_supersedes_intent_id_experiment_intents",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_intents"),
        sa.UniqueConstraint("project_id", "version", name="uq_experiment_intents_project_id"),
    )
    op.create_index(
        "ix_experiment_intent_active",
        "experiment_intents",
        ["project_id", "status"],
        unique=False,
    )

    op.create_table(
        "protected_parameters",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("context_id", sa.Uuid(), nullable=False),
        sa.Column("context_version", sa.Integer(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=True),
        sa.Column("intent_version", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_constraint_id", sa.Uuid(), nullable=True),
        sa.Column("parameter_path", sa.String(length=1000), nullable=False),
        sa.Column(
            "protection_level",
            sa.Enum(
                "LOCKED",
                "APPROVAL_REQUIRED",
                "EXPERIMENT_VARIABLE",
                name="protection_level",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("expected_value", sa.JSON(), nullable=False),
        sa.Column("allowed_range", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "source_type",
            sa.Enum("EXPLICIT", "INFERRED", name="constraint_source_type", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "verification_status",
            sa.Enum(
                "PENDING",
                "CONFIRMED",
                "REJECTED",
                "SUPERSEDED",
                name="constraint_verification_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("original_message", sa.Text(), nullable=False),
        sa.Column("inference_basis", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "verification_status != 'CONFIRMED' OR "
            "(confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)",
            name="ck_protected_parameters_confirmed_constraint_requires_actor",
        ),
        sa.CheckConstraint(
            "verification_status NOT IN ('REJECTED', 'SUPERSEDED') OR NOT active",
            name="ck_protected_parameters_inactive_rejected_constraint",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"], ["users.id"], name="fk_protected_parameters_confirmed_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["context_id"],
            ["project_contexts.id"],
            name="fk_protected_parameters_context_id_project_contexts",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_protected_parameters_created_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["intent_id"],
            ["experiment_intents.id"],
            name="fk_protected_parameters_intent_id_experiment_intents",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_protected_parameters_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_constraint_id"],
            ["protected_parameters.id"],
            name="fk_protected_parameters_supersedes",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_protected_parameters"),
        sa.UniqueConstraint(
            "project_id",
            "context_version",
            "parameter_path",
            "version",
            name="uq_protected_parameters_project_id",
        ),
    )
    op.create_index(
        "ix_protected_parameter_effective",
        "protected_parameters",
        ["project_id", "context_version", "verification_status", "active"],
        unique=False,
    )

    op.create_table(
        "access_tokens",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column(
            "audience",
            sa.Enum("API", "MCP", name="token_audience", native_enum=False),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("token_prefix", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "audience != 'MCP' OR project_id IS NOT NULL",
            name="ck_access_tokens_mcp_token_requires_project",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_access_tokens_created_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_access_tokens_project_id_projects"
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name="fk_access_tokens_team_id_teams"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_access_tokens_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_access_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_access_tokens_token_hash"),
    )
    op.create_index(
        "ix_access_token_principal",
        "access_tokens",
        ["user_id", "audience", "project_id"],
        unique=False,
    )
    op.create_index(
        "ix_access_tokens_token_prefix", "access_tokens", ["token_prefix"], unique=False
    )

    op.create_table(
        "audit_logs",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("actor_type", sa.String(length=50), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=200), nullable=False),
        sa.Column("target_type", sa.String(length=100), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("before_value", sa.JSON(), nullable=True),
        sa.Column("after_value", sa.JSON(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_audit_logs_project_id_projects"
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name="fk_audit_logs_team_id_teams"),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index(
        "ix_audit_project_created",
        "audit_logs",
        ["project_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "idempotency_records",
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_snapshot", sa.JSON(), nullable=True),
        sa.Column(
            "operation_status",
            sa.Enum(
                "IN_PROGRESS",
                "COMPLETED",
                "FAILED",
                name="idempotency_operation_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(updated=True),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_records"),
        sa.UniqueConstraint(
            "actor_id", "operation", "idempotency_key", name="uq_idempotency_records_actor_id"
        ),
    )
    op.create_index("ix_idempotency_expiry", "idempotency_records", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_idempotency_expiry", table_name="idempotency_records")
    op.drop_table("idempotency_records")
    op.drop_index("ix_audit_project_created", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_access_tokens_token_prefix", table_name="access_tokens")
    op.drop_index("ix_access_token_principal", table_name="access_tokens")
    op.drop_table("access_tokens")
    op.drop_index("ix_protected_parameter_effective", table_name="protected_parameters")
    op.drop_table("protected_parameters")
    op.drop_index("ix_experiment_intent_active", table_name="experiment_intents")
    op.drop_table("experiment_intents")
    op.drop_index("ix_project_context_active", table_name="project_contexts")
    op.drop_table("project_contexts")
    op.drop_index("ix_projects_team_id", table_name="projects")
    op.drop_table("projects")
    op.drop_table("team_members")
    op.drop_table("teams")
    op.drop_table("users")
