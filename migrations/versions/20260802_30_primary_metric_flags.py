"""repair formal experiment primary metric flags

Revision ID: 20260802_30
Revises: 20260801_29
Create Date: 2026-08-02
"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_30"
down_revision: str | Sequence[str] | None = "20260801_29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _primary_name(snapshot_value: Any, context_value: Any) -> str | None:
    snapshot = _object(snapshot_value)
    payload = _object(snapshot.get("payload"))
    primary = _object(payload.get("primary_metric")) or _object(
        snapshot.get("primary_metric")
    )
    context_primary = _object(context_value)
    candidate = primary.get("name") or context_primary.get("name")
    return candidate if isinstance(candidate, str) and candidate else None


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT e.id AS experiment_id,
                   pc.context_snapshot AS context_snapshot,
                   c.primary_metric AS context_primary_metric
            FROM experiments AS e
            JOIN run_manifests AS rm ON rm.id = e.run_manifest_id
            JOIN plan_checks AS pc ON pc.id = rm.plan_check_id
            JOIN project_contexts AS c ON c.id = e.project_context_id
            """
        )
    ).mappings()
    for row in rows:
        primary_name = _primary_name(
            row["context_snapshot"], row["context_primary_metric"]
        )
        if primary_name is None:
            continue
        connection.execute(
            sa.text(
                """
                UPDATE experiment_metrics
                SET is_primary = CASE WHEN name = :primary_name THEN TRUE ELSE FALSE END
                WHERE experiment_id = :experiment_id
                """
            ),
            {
                "experiment_id": row["experiment_id"],
                "primary_name": primary_name,
            },
        )


def downgrade() -> None:
    # 数据修复不可逆；降级 Schema 时保留已经校正的事实标记。
    pass
