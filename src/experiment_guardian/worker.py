"""R12a 单并发 Worker：调度 Outbox，并消费 SQS 生成提交摘要。"""

import os
import socket
import time
from dataclasses import dataclass

import structlog

from experiment_guardian.application.async_summary import (
    OutboxDispatcher,
    SubmissionSummaryProcessor,
    SubmissionSummaryScheduler,
)
from experiment_guardian.application.errors import ServiceUnavailableError
from experiment_guardian.core.config import Settings, get_settings
from experiment_guardian.core.logging import configure_logging
from experiment_guardian.infrastructure.bedrock import BedrockSummaryGenerator
from experiment_guardian.infrastructure.database import get_session_factory
from experiment_guardian.infrastructure.queue import SqsSubmissionQueue
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemySubmissionRepository,
    SqlAlchemyWorkflowRepository,
)


@dataclass(slots=True)
class SubmissionSummaryWorker:
    scheduler: SubmissionSummaryScheduler
    dispatcher: OutboxDispatcher
    processor: SubmissionSummaryProcessor
    queue: SqsSubmissionQueue

    def run_forever(self) -> None:
        logger = structlog.get_logger(__name__)
        reconciled = self.scheduler.reconcile()
        logger.info("submission_summary_worker_started", reconciled_jobs=reconciled)
        while True:
            try:
                for _ in range(100):
                    if not self.dispatcher.dispatch_once():
                        break
                for delivery in self.queue.receive(max_messages=1):
                    self.processor.process_delivery(delivery)
            except ServiceUnavailableError as exc:
                logger.warning("submission_summary_dependency_unavailable", error=str(exc))
                time.sleep(5)
            except Exception:
                # 未知异常必须保留 SQS 消息，让租约和可见性超时负责恢复。
                logger.exception("submission_summary_iteration_failed")
                time.sleep(1)


def build_worker(settings: Settings | None = None) -> SubmissionSummaryWorker:
    current = settings or get_settings()
    if not current.sqs_submission_queue_url.strip():
        raise ValueError("SQS_SUBMISSION_QUEUE_URL 未配置，R12a Worker 无法启动")
    if not current.bedrock_summary_model_id.strip():
        raise ValueError("BEDROCK_SUMMARY_MODEL_ID 未配置，R12a Worker 无法启动")

    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    factory = get_session_factory()
    workflows = SqlAlchemyWorkflowRepository()
    submissions = SqlAlchemySubmissionRepository()
    queue = SqsSubmissionQueue(
        queue_url=current.sqs_submission_queue_url,
        region=current.aws_region,
        wait_time_seconds=current.sqs_wait_time_seconds,
        visibility_timeout_seconds=current.sqs_visibility_timeout_seconds,
    )
    generator = BedrockSummaryGenerator(
        model_id=current.bedrock_summary_model_id,
        region=current.aws_region,
        connect_timeout_seconds=current.bedrock_connect_timeout_seconds,
        read_timeout_seconds=current.bedrock_read_timeout_seconds,
    )
    scheduler = SubmissionSummaryScheduler(
        factory,
        workflows,
        max_attempts=current.worker_max_attempts,
    )
    dispatcher = OutboxDispatcher(
        factory,
        workflows,
        queue,
        worker_id=worker_id,
        lease_seconds=current.worker_lease_seconds,
    )
    processor = SubmissionSummaryProcessor(
        factory,
        submissions,
        workflows,
        queue,
        generator,
        worker_id=worker_id,
        lease_seconds=current.worker_lease_seconds,
    )
    return SubmissionSummaryWorker(scheduler, dispatcher, processor, queue)


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        build_worker(settings).run_forever()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    run()
