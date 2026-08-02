"""persist structured Artifact material provenance

Revision ID: 20260801_29
Revises: 20260730_28
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_29"
down_revision: str | Sequence[str] | None = "20260730_28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORIGIN_VALUES = (
    "material_origin IN ('UNSPECIFIED', 'CURRENT_RUN', 'HISTORICAL_SOURCE', "
    "'TEST_FIXTURE', 'DERIVED_FROM_LOG')"
)


def upgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.add_column(
            sa.Column(
                "material_origin",
                sa.String(length=32),
                server_default="UNSPECIFIED",
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column("provenance", sa.JSON(), server_default=sa.text("'{}'"), nullable=False)
        )
        batch.create_check_constraint("artifact_material_origin_valid", ORIGIN_VALUES)
        batch.create_index(
            "ix_artifact_submission_origin", ["submission_id", "material_origin"]
        )


def downgrade() -> None:
    connection = op.get_bind()
    classified = connection.scalar(
        sa.text("SELECT count(*) FROM artifacts WHERE material_origin != 'UNSPECIFIED'")
    )
    if classified:
        raise RuntimeError("存在已分类材料来源，降级会丢失审计语义")
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_index("ix_artifact_submission_origin")
        batch.drop_constraint("artifact_material_origin_valid", type_="check")
        batch.drop_column("provenance")
        batch.drop_column("material_origin")
