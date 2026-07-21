"""persist deterministic plan checks

Revision ID: 20260721_02
Revises: 20260721_01
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_02"
down_revision: str | Sequence[str] | None = "20260721_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plan_checks",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("context_id", sa.Uuid(), nullable=False),
        sa.Column("context_version", sa.Integer(), nullable=False),
        sa.Column("intent_version", sa.Integer(), nullable=False),
        sa.Column(
            "experiment_mode",
            sa.Enum("FORMAL", "EXPLORATORY", name="plan_experiment_mode", native_enum=False),
            nullable=False,
        ),
        sa.Column("requester_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("input_config_hash", sa.String(length=64), nullable=False),
        sa.Column("parsed_config", sa.JSON(), nullable=False),
        sa.Column("git_commit", sa.String(length=64), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("local_attestation", sa.JSON(), nullable=False),
        sa.Column("constraint_snapshot", sa.JSON(), nullable=False),
        sa.Column("planned_changes", sa.JSON(), nullable=False),
        sa.Column(
            "check_result",
            sa.Enum(
                "PASS",
                "NEEDS_APPROVAL",
                "BLOCKED",
                name="check_result",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "approval_status",
            sa.Enum(
                "NOT_REQUIRED",
                "PENDING",
                "APPROVED",
                "REJECTED",
                name="approval_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "risk_level",
            sa.Enum(
                "LOW",
                "MEDIUM",
                "HIGH",
                "CRITICAL",
                name="plan_risk_severity",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(check_result = 'PASS' AND approval_status = 'NOT_REQUIRED') OR "
            "(check_result = 'BLOCKED' AND approval_status = 'NOT_REQUIRED') OR "
            "(check_result = 'NEEDS_APPROVAL' AND "
            "approval_status IN ('PENDING', 'APPROVED', 'REJECTED'))",
            name="result_approval_consistent",
        ),
        sa.CheckConstraint(
            "approval_status != 'APPROVED' OR "
            "(approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="approved_requires_actor",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"], ["users.id"], name="fk_plan_checks_approved_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["context_id"], ["project_contexts.id"], name="fk_plan_checks_context_id_contexts"
        ),
        sa.ForeignKeyConstraint(
            ["intent_id"], ["experiment_intents.id"], name="fk_plan_checks_intent_id_intents"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_plan_checks_project_id_projects"
        ),
        sa.ForeignKeyConstraint(
            ["requester_id"], ["users.id"], name="fk_plan_checks_requester_id_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_plan_checks"),
        sa.UniqueConstraint("requester_id", "idempotency_key", name="uq_plan_checks_requester_id"),
    )
    op.create_index(
        "ix_plan_check_project_created",
        "plan_checks",
        ["project_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_plan_check_project_created", table_name="plan_checks")
    op.drop_table("plan_checks")
