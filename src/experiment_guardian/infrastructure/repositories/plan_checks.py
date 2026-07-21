"""训练前检查记录的持久化仓储。"""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from experiment_guardian.application.errors import ConflictError
from experiment_guardian.domain.contracts import ExperimentCheckPlanResult
from experiment_guardian.domain.enums import ApprovalStatus, CheckResult
from experiment_guardian.infrastructure.models import PlanCheck


class SqlAlchemyPlanCheckRepository:
    @staticmethod
    def find_by_idempotency(
        session: Session, *, requester_id: UUID, idempotency_key: UUID
    ) -> PlanCheck | None:
        return session.scalar(
            select(PlanCheck).where(
                PlanCheck.requester_id == requester_id,
                PlanCheck.idempotency_key == idempotency_key,
            )
        )

    @staticmethod
    def replay(record: PlanCheck, *, request_hash: str) -> ExperimentCheckPlanResult:
        if record.request_hash != request_hash:
            raise ConflictError("相同 Idempotency-Key 已用于不同的配置检查请求")
        if (
            record.input_document_hash == "UNAVAILABLE"
            or not record.configuration_document
            or not record.context_snapshot
            or not record.intent_snapshot
        ):
            raise ConflictError("历史 Plan Check 缺少完整策略快照，必须使用新 key 重新检查")
        can_create_manifest = (
            record.check_result is CheckResult.PASS
            and record.approval_status is ApprovalStatus.NOT_REQUIRED
        ) or (
            record.check_result is CheckResult.NEEDS_APPROVAL
            and record.approval_status is ApprovalStatus.APPROVED
        )
        payload = {
            **record.report,
            "approval_status": record.approval_status,
            "can_create_manifest": can_create_manifest,
            "document_sha256": record.input_document_hash,
        }
        try:
            result = ExperimentCheckPlanResult.model_validate(payload)
        except ValidationError as exc:
            raise ConflictError("已保存的 Plan Check 回执不完整") from exc
        if (
            result.plan_check_id != record.id
            or result.project_id != record.project_id
            or result.context_id != record.context_id
            or result.experiment_intent_id != record.intent_id
            or result.context_version != record.context_version
            or result.intent_version != record.intent_version
            or result.check_result is not record.check_result
            or result.document_sha256 != record.input_document_hash
        ):
            raise ConflictError("Plan Check 回执与数据库追溯字段不一致")
        return result
