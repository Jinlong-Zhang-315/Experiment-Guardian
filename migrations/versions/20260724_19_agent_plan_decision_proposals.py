"""extend action proposals with Plan Check decisions

Revision ID: 20260724_19
Revises: 20260724_18
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260724_19"
down_revision: str | Sequence[str] | None = "20260724_18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY_FIELDS = (
    ("source_draft_id", sa.Uuid()),
    ("source_draft_revision_id", sa.Uuid()),
    ("source_draft_revision", sa.Integer()),
    ("source_candidate_hash", sa.String(length=64)),
    ("base_policy_hash", sa.String(length=64)),
    ("pending_state_hash", sa.String(length=64)),
)


def _upgrade_table() -> None:
    with op.batch_alter_table("agent_action_proposals") as batch:
        batch.drop_constraint(
            "ck_agent_action_proposals_operation_valid",
            type_="check",
        )
        for name, column_type in POLICY_FIELDS:
            batch.alter_column(
                name,
                existing_type=column_type,
                nullable=True,
            )
        batch.add_column(sa.Column("target_plan_check_id", sa.Uuid(), nullable=True))
        batch.add_column(
            sa.Column("target_state_hash", sa.String(length=64), nullable=True)
        )
        batch.add_column(
            sa.Column("executed_approval_record_id", sa.Uuid(), nullable=True)
        )
        batch.create_foreign_key(
            "fk_agent_action_proposals_target_plan",
            "plan_checks",
            ["target_plan_check_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_agent_action_proposals_executed_approval",
            "approval_records",
            ["executed_approval_record_id"],
            ["id"],
        )
        batch.create_check_constraint(
            "ck_agent_action_proposals_operation_valid",
            "operation IN ('POLICY_PUBLISH', 'PLAN_CHECK_DECISION')",
        )
        batch.create_check_constraint(
            "ck_agent_action_proposals_target_hash_length",
            "target_state_hash IS NULL OR length(target_state_hash) = 64",
        )
        batch.create_check_constraint(
            "ck_agent_action_proposals_policy_fields",
            "operation != 'POLICY_PUBLISH' OR "
            "(source_draft_id IS NOT NULL AND source_draft_revision_id IS NOT NULL "
            "AND source_draft_revision IS NOT NULL AND source_candidate_hash IS NOT NULL "
            "AND base_policy_hash IS NOT NULL AND pending_state_hash IS NOT NULL "
            "AND target_plan_check_id IS NULL AND target_state_hash IS NULL)",
        )
        batch.create_check_constraint(
            "ck_agent_action_proposals_plan_fields",
            "operation != 'PLAN_CHECK_DECISION' OR "
            "(source_draft_id IS NULL AND source_draft_revision_id IS NULL "
            "AND source_draft_revision IS NULL AND source_candidate_hash IS NULL "
            "AND base_policy_hash IS NULL AND pending_state_hash IS NULL "
            "AND target_plan_check_id IS NOT NULL AND target_state_hash IS NOT NULL)",
        )
        batch.create_index(
            "ix_agent_action_proposals_plan_status",
            ["target_plan_check_id", "status"],
        )


def upgrade() -> None:
    _upgrade_table()


def downgrade() -> None:
    # R15d-b1 提案本身可以丢弃；已经完成的正式 ApprovalRecord/Plan 状态不可回滚。
    op.execute(
        sa.text(
            "DELETE FROM agent_action_proposals "
            "WHERE operation = 'PLAN_CHECK_DECISION'"
        )
    )
    with op.batch_alter_table("agent_action_proposals") as batch:
        batch.drop_index("ix_agent_action_proposals_plan_status")
        batch.drop_constraint(
            "ck_agent_action_proposals_plan_fields",
            type_="check",
        )
        batch.drop_constraint(
            "ck_agent_action_proposals_policy_fields",
            type_="check",
        )
        batch.drop_constraint(
            "ck_agent_action_proposals_target_hash_length",
            type_="check",
        )
        batch.drop_constraint(
            "ck_agent_action_proposals_operation_valid",
            type_="check",
        )
        batch.drop_constraint(
            "fk_agent_action_proposals_executed_approval",
            type_="foreignkey",
        )
        batch.drop_constraint(
            "fk_agent_action_proposals_target_plan",
            type_="foreignkey",
        )
        batch.drop_column("executed_approval_record_id")
        batch.drop_column("target_state_hash")
        batch.drop_column("target_plan_check_id")
        for name, column_type in POLICY_FIELDS:
            batch.alter_column(
                name,
                existing_type=column_type,
                nullable=False,
            )
        batch.create_check_constraint(
            "ck_agent_action_proposals_operation_valid",
            "operation IN ('POLICY_PUBLISH')",
        )
