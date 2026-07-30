"""align Agent Citation constraint with current Evidence kinds

Revision ID: 20260730_28
Revises: 20260730_27
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_28"
down_revision: str | Sequence[str] | None = "20260730_27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "ck_agent_citations_evidence_kind_valid"
CURRENT_VALUES = (
    "evidence_kind IN ('CONFIRMED_FACT', 'USER_PROVIDED', 'CANDIDATE_DRAFT', "
    "'ACTION_PROPOSAL', 'ANALYSIS', 'HYPOTHESIS')"
)
LEGACY_VALUES = (
    "evidence_kind IN ('CONFIRMED_FACT', 'USER_PROVIDED', 'ANALYSIS', 'HYPOTHESIS')"
)


def _current_constraint_name() -> str:
    constraints = sa.inspect(op.get_bind()).get_check_constraints("agent_citations")
    matches = [
        item.get("name")
        for item in constraints
        if "evidence_kind IN" in str(item.get("sqltext", ""))
    ]
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise RuntimeError("无法唯一定位 agent_citations Evidence 类型约束")
    return matches[0]


def upgrade() -> None:
    existing_name = _current_constraint_name()
    with op.batch_alter_table("agent_citations") as batch:
        batch.drop_constraint(op.f(existing_name), type_="check")
        batch.create_check_constraint(op.f(CONSTRAINT_NAME), CURRENT_VALUES)


def downgrade() -> None:
    connection = op.get_bind()
    incompatible = connection.scalar(
        sa.text(
            "SELECT count(*) FROM agent_citations "
            "WHERE evidence_kind IN ('CANDIDATE_DRAFT', 'ACTION_PROPOSAL')"
        )
    )
    if incompatible:
        raise RuntimeError(
            "存在 CANDIDATE_DRAFT 或 ACTION_PROPOSAL Citation，不能降级到 revision 27"
        )
    existing_name = _current_constraint_name()
    with op.batch_alter_table("agent_citations") as batch:
        batch.drop_constraint(op.f(existing_name), type_="check")
        batch.create_check_constraint(op.f(CONSTRAINT_NAME), LEGACY_VALUES)
