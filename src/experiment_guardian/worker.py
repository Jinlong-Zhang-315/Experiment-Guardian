"""R12b 单并发 Worker：处理提交摘要、embedding 和审核回执。"""

import os
import socket
import time
from dataclasses import dataclass

import structlog

from experiment_guardian.application.async_review import (
    SubmissionJobProcessor,
    SubmissionReviewProcessor,
)
from experiment_guardian.application.async_summary import (
    OutboxDispatcher,
    SubmissionReviewScheduler,
    SubmissionSummaryProcessor,
    SubmissionSummaryScheduler,
)
from experiment_guardian.application.errors import ServiceUnavailableError
from experiment_guardian.application.ports import (
    EmbeddingGenerator,
    SubmissionQueue,
    SummaryTextGenerator,
)
from experiment_guardian.core.config import Settings, get_settings
from experiment_guardian.core.logging import configure_logging
from experiment_guardian.infrastructure.bailian import (
    BailianEmbeddingGenerator,
    BailianSummaryGenerator,
)
from experiment_guardian.infrastructure.bedrock import (
    BedrockSummaryGenerator,
    BedrockTitanV2EmbeddingGenerator,
)
from experiment_guardian.infrastructure.database import get_session_factory
from experiment_guardian.infrastructure.queue import DatabaseOutboxQueue, SqsSubmissionQueue
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemySubmissionRepository,
    SqlAlchemyWorkflowRepository,
)


@dataclass(slots=True)
class SubmissionWorker:
    summary_scheduler: SubmissionSummaryScheduler
    review_scheduler: SubmissionReviewScheduler
    dispatcher: OutboxDispatcher
    processor: SubmissionJobProcessor
    queue: SubmissionQueue
    batch_size: int = 1
    idle_wait_seconds: float = 1.0

    def run_forever(self) -> None:
        logger = structlog.get_logger(__name__)
        summary_reconciled = self.summary_scheduler.reconcile()
        review_reconciled = self.review_scheduler.reconcile()
        logger.info(
            "submission_worker_started",
            reconciled_summary_jobs=summary_reconciled,
            reconciled_review_jobs=review_reconciled,
        )
        while True:
            try:
                did_work = False
                for _ in range(100):
                    if not self.dispatcher.dispatch_once():
                        break
                    did_work = True
                deliveries = self.queue.receive(max_messages=self.batch_size)
                for delivery in deliveries:
                    did_work = True
                    self.processor.process_delivery(delivery)
                if not did_work:
                    time.sleep(self.idle_wait_seconds)
            except ServiceUnavailableError as exc:
                logger.warning("submission_summary_dependency_unavailable", error=str(exc))
                time.sleep(5)
            except Exception:
                # 未知异常必须保留 SQS 消息，让租约和可见性超时负责恢复。
                logger.exception("submission_summary_iteration_failed")
                time.sleep(1)


def build_worker(settings: Settings | None = None) -> SubmissionWorker:
    current = settings or get_settings()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    factory = get_session_factory()
    workflows = SqlAlchemyWorkflowRepository()
    submissions = SqlAlchemySubmissionRepository()
    queue: SubmissionQueue
    if current.queue_backend == "database":
        queue = DatabaseOutboxQueue(
            factory,
            worker_id=worker_id,
            lease_seconds=current.worker_lease_seconds,
        )
        batch_size = current.database_queue_batch_size
        idle_wait_seconds = current.database_queue_poll_interval_seconds
    else:
        if not current.sqs_submission_queue_url.strip():
            raise ValueError("SQS_SUBMISSION_QUEUE_URL 未配置，Worker 无法启动")
        queue = SqsSubmissionQueue(
            queue_url=current.sqs_submission_queue_url,
            region=current.aws_region,
            wait_time_seconds=current.sqs_wait_time_seconds,
            visibility_timeout_seconds=current.sqs_visibility_timeout_seconds,
        )
        batch_size = 1
        idle_wait_seconds = 1.0

    summary_generator: SummaryTextGenerator
    embedding_generator: EmbeddingGenerator
    if current.llm_provider == "bailian":
        api_key = current.bailian_api_key
        raw_api_key = api_key.get_secret_value() if api_key else ""
        summary_generator = BailianSummaryGenerator(
            api_key=raw_api_key,
            base_url=current.bailian_base_url,
            model_id=current.bailian_summary_model,
            connect_timeout_seconds=current.bailian_connect_timeout_seconds,
            read_timeout_seconds=current.bailian_read_timeout_seconds,
        )
        embedding_generator = BailianEmbeddingGenerator(
            api_key=raw_api_key,
            base_url=current.bailian_base_url,
            model_id=current.bailian_embedding_model,
            dimension=current.bailian_embedding_dimension,
            connect_timeout_seconds=current.bailian_connect_timeout_seconds,
            read_timeout_seconds=current.bailian_read_timeout_seconds,
        )
    else:
        if not current.bedrock_summary_model_id.strip():
            raise ValueError("BEDROCK_SUMMARY_MODEL_ID 未配置，Worker 无法启动")
        summary_generator = BedrockSummaryGenerator(
            model_id=current.bedrock_summary_model_id,
            region=current.aws_region,
            connect_timeout_seconds=current.bedrock_connect_timeout_seconds,
            read_timeout_seconds=current.bedrock_read_timeout_seconds,
        )
        embedding_generator = BedrockTitanV2EmbeddingGenerator(
            model_id=current.bedrock_embedding_model_id,
            region=current.aws_region,
            dimension=current.embedding_dimension,
            connect_timeout_seconds=current.bedrock_connect_timeout_seconds,
            read_timeout_seconds=current.bedrock_read_timeout_seconds,
        )
    scheduler = SubmissionSummaryScheduler(
        factory,
        workflows,
        max_attempts=current.worker_max_attempts,
    )
    review_scheduler = SubmissionReviewScheduler(
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
    summary_processor = SubmissionSummaryProcessor(
        factory,
        submissions,
        workflows,
        queue,
        summary_generator,
        review_scheduler,
        worker_id=worker_id,
        lease_seconds=current.worker_lease_seconds,
    )
    review_processor = SubmissionReviewProcessor(
        factory,
        submissions,
        workflows,
        queue,
        embedding_generator,
        worker_id=worker_id,
        lease_seconds=current.worker_lease_seconds,
    )
    processor = SubmissionJobProcessor(
        factory,
        workflows,
        queue,
        summary_processor=summary_processor,
        review_processor=review_processor,
    )
    return SubmissionWorker(
        scheduler,
        review_scheduler,
        dispatcher,
        processor,
        queue,
        batch_size=batch_size,
        idle_wait_seconds=idle_wait_seconds,
    )


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        build_worker(settings).run_forever()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    run()
