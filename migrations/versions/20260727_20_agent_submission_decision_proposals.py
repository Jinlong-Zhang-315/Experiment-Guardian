"""extend action proposals with Submission decisions

Revision ID: 20260727_20
Revises: 20260724_19
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_20"
down_revision: str | Sequence[str] | None = "20260724_19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_action_proposals") as batch:
        batch.drop_constraint(
            "ck_agent_action_proposals_operation_valid",
            type_="check",
        )
        batch.drop_constraint(
            "ck_agent_action_proposals_policy_fields",
            type_="check",
        )
        batch.drop_constraint(
            "ck_agent_action_proposals_plan_fields",
            type_="check",
        )
        batch.add_column(sa.Column("target_submission_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("executed_experiment_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_agent_action_proposals_target_submission",
            "experiment_submissions",
            ["target_submission_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_agent_action_proposals_executed_experiment",
            "experiments",
            ["executed_experiment_id"],
            ["id"],
        )
        batch.create_check_constraint(
            "ck_agent_action_proposals_operation_valid",
            "operation IN ('POLICY_PUBLISH', 'PLAN_CHECK_DECISION', 'SUBMISSION_DECISION')",
        )
        batch.create_check_constraint(
            "ck_agent_action_proposals_policy_fields",
            "operation != 'POLICY_PUBLISH' OR "
            "(source_draft_id IS NOT NULL AND source_draft_revision_id IS NOT NULL "
            "AND source_draft_revision IS NOT NULL AND source_candidate_hash IS NOT NULL "
            "AND base_policy_hash IS NOT NULL AND pending_state_hash IS NOT NULL "
            "AND target_plan_check_id IS NULL AND target_submission_id IS NULL "
            "AND target_state_hash IS NULL)",
        )
        batch.create_check_constraint(
            "ck_agent_action_proposals_plan_fields",
            "operation != 'PLAN_CHECK_DECISION' OR "
            "(source_draft_id IS NULL AND source_draft_revision_id IS NULL "
            "AND source_draft_revision IS NULL AND source_candidate_hash IS NULL "
            "AND base_policy_hash IS NULL AND pending_state_hash IS NULL "
            "AND target_plan_check_id IS NOT NULL AND target_submission_id IS NULL "
            "AND target_state_hash IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_agent_action_proposals_submission_fields",
            "operation != 'SUBMISSION_DECISION' OR "
            "(source_draft_id IS NULL AND source_draft_revision_id IS NULL "
            "AND source_draft_revision IS NULL AND source_candidate_hash IS NULL "
            "AND base_policy_hash IS NULL AND pending_state_hash IS NULL "
            "AND target_plan_check_id IS NULL AND target_submission_id IS NOT NULL "
            "AND target_state_hash IS NOT NULL)",
        )
        batch.create_index(
            "ix_agent_action_proposals_submission_status",
            ["target_submission_id", "status"],
        )


def downgrade() -> None:
    # 提案可丢弃；已生效的 ApprovalRecord/Experiment 不属于可回滚数据。
    op.execute(
        sa.text("DELETE FROM agent_action_proposals WHERE operation = 'SUBMISSION_DECISION'")
    )
    with op.batch_alter_table("agent_action_proposals") as batch:
        batch.drop_index("ix_agent_action_proposals_submission_status")
        batch.drop_constraint(
            "ck_agent_action_proposals_submission_fields",
            type_="check",
        )
        batch.drop_constraint(
            "ck_agent_action_proposals_plan_fields",
            type_="check",
        )
        batch.drop_constraint(
            "ck_agent_action_proposals_policy_fields",
            type_="check",
        )
        batch.drop_constraint(
            "ck_agent_action_proposals_operation_valid",
            type_="check",
        )
        batch.drop_constraint(
            "fk_agent_action_proposals_executed_experiment",
            type_="foreignkey",
        )
        batch.drop_constraint(
            "fk_agent_action_proposals_target_submission",
            type_="foreignkey",
        )
        batch.drop_column("executed_experiment_id")
        batch.drop_column("target_submission_id")
        batch.create_check_constraint(
            "ck_agent_action_proposals_operation_valid",
            "operation IN ('POLICY_PUBLISH', 'PLAN_CHECK_DECISION')",
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
