"""R12a 异步摘要 Job 与事务 Outbox 的持久化操作。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from experiment_guardian.domain.enums import (
    OutboxStatus,
    WorkflowJobStatus,
    WorkflowJobType,
    WorkflowStatus,
    WorkflowStep,
)
from experiment_guardian.infrastructure.models import (
    ExperimentSubmission,
    OutboxEvent,
    WorkflowJob,
)

SUMMARY_EVENT_TYPE = "SUBMISSION_SUMMARY_REQUESTED"
SUMMARY_SCHEMA_VERSION = 1


class SqlAlchemyWorkflowRepository:
    """所有方法都在调用方提供的显式事务中运行。"""

    @staticmethod
    def get_summary_job(
        session: Session, submission_id: UUID, *, for_update: bool = False
    ) -> WorkflowJob | None:
        statement = select(WorkflowJob).where(
            WorkflowJob.submission_id == submission_id,
            WorkflowJob.job_type == WorkflowJobType.SUBMISSION_SUMMARY,
        )
        if for_update:
            statement = statement.with_for_update()
        return session.scalar(statement)

    @staticmethod
    def get_job(session: Session, job_id: UUID, *, for_update: bool = False) -> WorkflowJob | None:
        statement = select(WorkflowJob).where(WorkflowJob.id == job_id)
        if for_update:
            statement = statement.with_for_update()
        return session.scalar(statement)

    @staticmethod
    def get_outbox(session: Session, workflow_job_id: UUID, generation: int) -> OutboxEvent | None:
        return session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.workflow_job_id == workflow_job_id,
                OutboxEvent.generation == generation,
            )
        )

    def ensure_summary_job(
        self,
        session: Session,
        submission: ExperimentSubmission,
        *,
        max_attempts: int,
        now: datetime | None = None,
    ) -> tuple[WorkflowJob, bool]:
        """在风险事务内创建唯一 Job/Outbox；已存在时只修复缺失 Outbox。"""

        current_time = now or datetime.now(UTC)
        job = self.get_summary_job(session, submission.id, for_update=True)
        created = False
        if job is None:
            job = WorkflowJob(
                submission_id=submission.id,
                job_type=WorkflowJobType.SUBMISSION_SUMMARY,
                status=WorkflowJobStatus.PENDING_DISPATCH,
                generation=1,
                attempt_count=0,
                max_attempts=max_attempts,
                available_at=current_time,
            )
            session.add(job)
            session.flush()
            created = True
        if job.status is WorkflowJobStatus.PENDING_DISPATCH:
            self._ensure_outbox(session, job, current_time)
        return job, created

    def rearm_summary_job(
        self,
        session: Session,
        submission: ExperimentSubmission,
        *,
        max_attempts: int,
        now: datetime | None = None,
    ) -> tuple[WorkflowJob, bool]:
        """仅失败或死信 Job 可增加 generation；活跃 Job 保持单例。"""

        current_time = now or datetime.now(UTC)
        job = self.get_summary_job(session, submission.id, for_update=True)
        if job is None:
            return self.ensure_summary_job(
                session, submission, max_attempts=max_attempts, now=current_time
            )
        if job.status not in {
            WorkflowJobStatus.RETRYABLE_FAILURE,
            WorkflowJobStatus.DEAD_LETTER,
        }:
            return job, False
        job.generation += 1
        job.status = WorkflowJobStatus.PENDING_DISPATCH
        job.attempt_count = 0
        job.max_attempts = max_attempts
        job.available_at = current_time
        job.lease_owner = None
        job.lease_expires_at = None
        job.last_error = None
        job.sqs_message_id = None
        job.started_at = None
        job.completed_at = None
        self._ensure_outbox(session, job, current_time)
        return job, True

    @staticmethod
    def _ensure_outbox(session: Session, job: WorkflowJob, now: datetime) -> OutboxEvent:
        existing = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.workflow_job_id == job.id,
                OutboxEvent.generation == job.generation,
            )
        )
        if existing is not None:
            return existing
        event = OutboxEvent(
            workflow_job_id=job.id,
            generation=job.generation,
            event_type=SUMMARY_EVENT_TYPE,
            payload={
                "schema_version": SUMMARY_SCHEMA_VERSION,
                "job_id": str(job.id),
                "submission_id": str(job.submission_id),
                "generation": job.generation,
            },
            status=OutboxStatus.PENDING,
            attempt_count=0,
            available_at=now,
        )
        session.add(event)
        return event

    @staticmethod
    def list_reconciliation_submissions(
        session: Session, *, limit: int = 100
    ) -> list[ExperimentSubmission]:
        """找到 R11 已到风险终点但尚无摘要 Job 的历史记录。"""

        return list(
            session.scalars(
                select(ExperimentSubmission)
                .where(
                    ExperimentSubmission.workflow_status == WorkflowStatus.AWAITING_ENRICHMENT,
                    ExperimentSubmission.processing_step == WorkflowStep.RISK_ANALYSIS,
                    ~select(WorkflowJob.id)
                    .where(
                        WorkflowJob.submission_id == ExperimentSubmission.id,
                        WorkflowJob.job_type == WorkflowJobType.SUBMISSION_SUMMARY,
                    )
                    .exists(),
                )
                .order_by(ExperimentSubmission.updated_at, ExperimentSubmission.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
        )

    @staticmethod
    def claim_outbox(
        session: Session,
        *,
        lease_owner: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> OutboxEvent | None:
        current_time = now or datetime.now(UTC)
        event = session.scalar(
            select(OutboxEvent)
            .where(
                OutboxEvent.available_at <= current_time,
                or_(
                    OutboxEvent.status == OutboxStatus.PENDING,
                    (
                        (OutboxEvent.status == OutboxStatus.PUBLISHING)
                        & (OutboxEvent.lease_expires_at <= current_time)
                    ),
                ),
            )
            .order_by(OutboxEvent.available_at, OutboxEvent.created_at, OutboxEvent.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if event is None:
            return None
        event.status = OutboxStatus.PUBLISHING
        event.lease_owner = lease_owner
        event.lease_expires_at = current_time + timedelta(seconds=lease_seconds)
        return event
