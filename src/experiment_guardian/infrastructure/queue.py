"""AWS SQS 与 CockroachDB Outbox 队列适配器。"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.errors import ServiceUnavailableError
from experiment_guardian.application.ports import QueueDelivery, SubmissionQueue
from experiment_guardian.application.transactions import run_with_serialization_retry
from experiment_guardian.domain.contracts import WorkflowQueueEnvelope
from experiment_guardian.domain.enums import OutboxStatus, WorkflowJobStatus
from experiment_guardian.infrastructure.models import OutboxEvent, WorkflowJob


class SqsSubmissionQueue(SubmissionQueue):
    def __init__(
        self,
        *,
        queue_url: str,
        region: str,
        wait_time_seconds: int = 20,
        visibility_timeout_seconds: int = 120,
        client: Any | None = None,
    ) -> None:
        if not queue_url.strip():
            raise ValueError("SQS_SUBMISSION_QUEUE_URL 未配置")
        self._queue_url = queue_url
        self._wait_time_seconds = wait_time_seconds
        self._visibility_timeout_seconds = visibility_timeout_seconds
        self._client = client or boto3.client(
            "sqs",
            region_name=region,
            config=Config(retries={"max_attempts": 2, "mode": "standard"}),
        )

    def send(self, envelope: WorkflowQueueEnvelope) -> str:
        try:
            response = self._client.send_message(
                QueueUrl=self._queue_url,
                MessageBody=envelope.model_dump_json(),
            )
        except Exception as exc:
            raise ServiceUnavailableError("SQS 消息发布失败") from exc
        message_id = response.get("MessageId")
        if not isinstance(message_id, str) or not message_id:
            raise ServiceUnavailableError("SQS 发布回执缺少 MessageId")
        return message_id

    def receive(self, *, max_messages: int = 1) -> Sequence[QueueDelivery]:
        try:
            response = self._client.receive_message(
                QueueUrl=self._queue_url,
                MaxNumberOfMessages=max(1, min(max_messages, 10)),
                WaitTimeSeconds=self._wait_time_seconds,
                VisibilityTimeout=self._visibility_timeout_seconds,
                AttributeNames=["ApproximateReceiveCount"],
            )
        except Exception as exc:
            raise ServiceUnavailableError("SQS 消息接收失败") from exc
        deliveries: list[QueueDelivery] = []
        for raw in response.get("Messages", []):
            attributes = raw.get("Attributes") or {}
            try:
                deliveries.append(
                    QueueDelivery(
                        message_id=str(raw["MessageId"]),
                        receipt_handle=str(raw["ReceiptHandle"]),
                        body=str(raw["Body"]),
                        receive_count=max(1, int(attributes.get("ApproximateReceiveCount", "1"))),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return deliveries

    def delete(self, receipt_handle: str) -> None:
        try:
            self._client.delete_message(
                QueueUrl=self._queue_url,
                ReceiptHandle=receipt_handle,
            )
        except Exception as exc:
            raise ServiceUnavailableError("SQS 消息确认失败") from exc

    def change_visibility(self, receipt_handle: str, timeout_seconds: int) -> None:
        try:
            self._client.change_message_visibility(
                QueueUrl=self._queue_url,
                ReceiptHandle=receipt_handle,
                VisibilityTimeout=max(0, min(timeout_seconds, 43200)),
            )
        except Exception as exc:
            raise ServiceUnavailableError("SQS 可见性更新失败") from exc


class DatabaseOutboxQueue(SubmissionQueue):
    """把已发布 Outbox 直接作为本地队列，业务事实仍只从 CockroachDB 读取。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        worker_id: str,
        lease_seconds: int = 120,
    ) -> None:
        self._session_factory = session_factory
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds

    def send(self, envelope: WorkflowQueueEnvelope) -> str:
        with self._session_factory() as session:
            event = session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.workflow_job_id == envelope.job_id,
                    OutboxEvent.generation == envelope.generation,
                )
            )
            if event is None:
                raise ServiceUnavailableError("数据库 Outbox 事件不存在")
        return f"database:{envelope.job_id}:{envelope.generation}"

    def receive(self, *, max_messages: int = 1) -> Sequence[QueueDelivery]:
        return run_with_serialization_retry(lambda: self._receive_once(max_messages=max_messages))

    def _receive_once(self, *, max_messages: int) -> Sequence[QueueDelivery]:
        now = datetime.now(UTC)
        limit = max(1, min(max_messages, 100))
        with self._session_factory() as session, session.begin():
            events = list(
                session.scalars(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.status == OutboxStatus.PUBLISHED,
                        OutboxEvent.available_at <= now,
                        or_(
                            OutboxEvent.lease_owner.is_(None),
                            OutboxEvent.lease_expires_at <= now,
                        ),
                    )
                    .order_by(OutboxEvent.available_at, OutboxEvent.created_at, OutboxEvent.id)
                    # 先只锁 Outbox 行。CockroachDB 对带 JOIN 的 SKIP LOCKED 行为与
                    # PostgreSQL 不完全一致；Job 条件在锁内逐条验证，不改变事实来源。
                    .limit(min(100, max(10, limit * 4)))
                    .with_for_update(skip_locked=True)
                ).all()
            )
            deliveries: list[QueueDelivery] = []
            for event in events:
                job = session.get(WorkflowJob, event.workflow_job_id)
                if job is None:
                    event.status = OutboxStatus.DEAD_LETTER
                    event.last_error = {"code": "WORKFLOW_JOB_MISSING", "retryable": False}
                    continue
                if event.generation != job.generation:
                    event.status = OutboxStatus.COMPLETED
                    event.last_error = {
                        "code": "WORKFLOW_GENERATION_SUPERSEDED",
                        "retryable": False,
                    }
                    continue
                ready_job = session.scalar(
                    select(WorkflowJob).where(
                        WorkflowJob.id == job.id,
                        WorkflowJob.available_at <= now,
                        or_(
                            WorkflowJob.status.in_(
                                {
                                    WorkflowJobStatus.QUEUED,
                                    WorkflowJobStatus.RETRYABLE_FAILURE,
                                }
                            ),
                            (
                                (WorkflowJob.status == WorkflowJobStatus.RUNNING)
                                & (WorkflowJob.lease_expires_at <= now)
                            ),
                        ),
                    )
                )
                if ready_job is None:
                    if job.status in {WorkflowJobStatus.DEAD_LETTER, WorkflowJobStatus.FAILED}:
                        event.status = OutboxStatus.DEAD_LETTER
                    elif job.status is WorkflowJobStatus.SUCCEEDED:
                        event.status = OutboxStatus.COMPLETED
                    continue
                event.lease_owner = self._worker_id
                event.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
                event.attempt_count += 1
                deliveries.append(
                    QueueDelivery(
                        message_id=str(event.id),
                        receipt_handle=f"{event.id}:{event.generation}",
                        body=json.dumps(event.payload, separators=(",", ":"), sort_keys=True),
                        receive_count=max(1, job.attempt_count + 1),
                    )
                )
                if len(deliveries) >= limit:
                    break
            return deliveries

    def delete(self, receipt_handle: str) -> None:
        event_id, generation = self._parse_receipt(receipt_handle)
        with self._session_factory() as session, session.begin():
            event = session.get(OutboxEvent, event_id, with_for_update=True)
            if event is None or event.generation != generation:
                return
            if event.lease_owner not in {None, self._worker_id}:
                return
            job = session.get(WorkflowJob, event.workflow_job_id)
            event.status = (
                OutboxStatus.DEAD_LETTER
                if job is None
                or job.status in {WorkflowJobStatus.DEAD_LETTER, WorkflowJobStatus.FAILED}
                else OutboxStatus.COMPLETED
            )
            event.lease_owner = None
            event.lease_expires_at = None

    def change_visibility(self, receipt_handle: str, timeout_seconds: int) -> None:
        event_id, generation = self._parse_receipt(receipt_handle)
        now = datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            event = session.get(OutboxEvent, event_id, with_for_update=True)
            if (
                event is None
                or event.generation != generation
                or event.lease_owner != self._worker_id
            ):
                return
            job = session.get(WorkflowJob, event.workflow_job_id)
            if job is None or job.status in {
                WorkflowJobStatus.DEAD_LETTER,
                WorkflowJobStatus.FAILED,
            }:
                event.status = OutboxStatus.DEAD_LETTER
            elif job.status is WorkflowJobStatus.SUCCEEDED:
                event.status = OutboxStatus.COMPLETED
            else:
                event.available_at = now + timedelta(seconds=max(1, timeout_seconds))
            event.lease_owner = None
            event.lease_expires_at = None

    @staticmethod
    def _parse_receipt(receipt_handle: str) -> tuple[UUID, int]:
        try:
            raw_id, raw_generation = receipt_handle.split(":", 1)
            return UUID(raw_id), int(raw_generation)
        except (ValueError, TypeError) as exc:
            raise ServiceUnavailableError("数据库队列回执无效") from exc
