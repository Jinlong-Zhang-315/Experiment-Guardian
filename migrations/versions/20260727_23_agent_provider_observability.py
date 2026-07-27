"""add Agent provider parity observability fields

Revision ID: 20260727_23
Revises: 20260727_22
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_23"
down_revision: str | Sequence[str] | None = "20260727_22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_model_calls") as batch:
        batch.add_column(sa.Column("provider", sa.String(length=50)))
        batch.add_column(sa.Column("model_id", sa.String(length=500)))
        batch.add_column(sa.Column("latency_ms", sa.Integer()))
        batch.add_column(sa.Column("cost_currency", sa.String(length=3)))
        batch.add_column(sa.Column("input_cost_per_million", sa.Numeric(20, 8)))
        batch.add_column(sa.Column("output_cost_per_million", sa.Numeric(20, 8)))
        batch.add_column(sa.Column("estimated_cost", sa.Numeric(20, 10)))
    op.execute(
        sa.text(
            "UPDATE agent_model_calls AS model_call "
            "SET provider = run.provider, model_id = run.model_id "
            "FROM agent_runs AS run WHERE model_call.run_id = run.id"
        )
    )
    with op.batch_alter_table("agent_model_calls") as batch:
        batch.alter_column("provider", existing_type=sa.String(length=50), nullable=False)
        batch.alter_column("model_id", existing_type=sa.String(length=500), nullable=False)
        batch.create_check_constraint(
            "latency_nonnegative",
            "latency_ms IS NULL OR latency_ms >= 0",
        )
        batch.create_check_constraint(
            "estimated_cost_nonnegative",
            "estimated_cost IS NULL OR estimated_cost >= 0",
        )
        batch.create_index(
            "ix_agent_model_calls_observability",
            ["provider", "model_id", "created_at", "status"],
        )
    op.create_index(
        "ix_agent_runs_project_observability",
        "agent_runs",
        ["project_id", "created_at", "provider", "model_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_project_observability", table_name="agent_runs")
    with op.batch_alter_table("agent_model_calls") as batch:
        batch.drop_index("ix_agent_model_calls_observability")
        batch.drop_constraint("estimated_cost_nonnegative", type_="check")
        batch.drop_constraint("latency_nonnegative", type_="check")
        batch.drop_column("estimated_cost")
        batch.drop_column("output_cost_per_million")
        batch.drop_column("input_cost_per_million")
        batch.drop_column("cost_currency")
        batch.drop_column("latency_ms")
        batch.drop_column("model_id")
        batch.drop_column("provider")
