"""可选真实 AWS 验收；默认测试不会访问网络或产生云端调用。"""

import os
from uuid import uuid4

import pytest

from experiment_guardian.core.config import Settings
from experiment_guardian.domain.contracts import SummaryQueueEnvelope
from experiment_guardian.infrastructure.bedrock import BedrockSummaryGenerator
from experiment_guardian.infrastructure.queue import SqsSubmissionQueue


@pytest.mark.skipif(
    os.getenv("RUN_SQS_INTEGRATION") != "1",
    reason="设置 RUN_SQS_INTEGRATION=1 后才执行真实 SQS 验收",
)
def test_real_sqs_minimal_envelope_round_trip() -> None:
    settings = Settings()
    if not settings.sqs_submission_queue_url:
        pytest.fail("RUN_SQS_INTEGRATION=1 时必须配置 SQS_SUBMISSION_QUEUE_URL")
    queue = SqsSubmissionQueue(
        queue_url=settings.sqs_submission_queue_url,
        region=settings.aws_region,
        wait_time_seconds=settings.sqs_wait_time_seconds,
        visibility_timeout_seconds=settings.sqs_visibility_timeout_seconds,
    )
    envelope = SummaryQueueEnvelope(
        job_id=uuid4(),
        submission_id=uuid4(),
        generation=1,
    )
    message_id = queue.send(envelope)
    for _ in range(3):
        for delivery in queue.receive(max_messages=10):
            if delivery.message_id == message_id:
                assert SummaryQueueEnvelope.model_validate_json(delivery.body) == envelope
                queue.delete(delivery.receipt_handle)
                return
            queue.change_visibility(delivery.receipt_handle, 0)
    pytest.fail("未在真实 SQS 队列中收到刚发布的验收消息")


@pytest.mark.skipif(
    os.getenv("RUN_BEDROCK_INTEGRATION") != "1",
    reason="设置 RUN_BEDROCK_INTEGRATION=1 后才执行真实 Bedrock 验收",
)
def test_real_bedrock_converse_returns_plain_text() -> None:
    settings = Settings()
    if not settings.bedrock_summary_model_id:
        pytest.fail("RUN_BEDROCK_INTEGRATION=1 时必须配置 BEDROCK_SUMMARY_MODEL_ID")
    generator = BedrockSummaryGenerator(
        model_id=settings.bedrock_summary_model_id,
        region=settings.aws_region,
        connect_timeout_seconds=settings.bedrock_connect_timeout_seconds,
        read_timeout_seconds=settings.bedrock_read_timeout_seconds,
    )
    result = generator.generate(
        system_prompt="Return a short plain-text summary. Do not use tools.",
        user_prompt="Objective: verify the configured Bedrock summary model is callable.",
    )
    assert result.text.strip()
