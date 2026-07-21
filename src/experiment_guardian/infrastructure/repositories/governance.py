"""审批和 Run Manifest 的持久化查询。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from experiment_guardian.domain.enums import ApprovalTargetType
from experiment_guardian.infrastructure.models import (
    ApprovalRecord,
    IdempotencyRecord,
    PlanCheck,
    RunManifest,
)


class SqlAlchemyGovernanceRepository:
    @staticmethod
    def get_plan_for_update(session: Session, plan_check_id: UUID) -> PlanCheck | None:
        return session.scalar(
            select(PlanCheck).where(PlanCheck.id == plan_check_id).with_for_update()
        )

    @staticmethod
    def find_idempotency(
        session: Session,
        *,
        actor_id: UUID,
        operation: str,
        idempotency_key: UUID,
    ) -> IdempotencyRecord | None:
        return session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.actor_id == actor_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
        )

    @staticmethod
    def find_plan_approval(session: Session, plan_check_id: UUID) -> ApprovalRecord | None:
        return session.scalar(
            select(ApprovalRecord).where(
                ApprovalRecord.target_type == ApprovalTargetType.PLAN_CHECK,
                ApprovalRecord.target_id == plan_check_id,
            )
        )

    @staticmethod
    def find_manifest_by_plan(session: Session, plan_check_id: UUID) -> RunManifest | None:
        return session.scalar(select(RunManifest).where(RunManifest.plan_check_id == plan_check_id))
