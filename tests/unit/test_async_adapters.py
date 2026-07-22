"""R12a SQS/Bedrock 适配器的严格 Fake 测试，不访问真实 AWS。"""

import json
from uuid import uuid4

import pytest

from experiment_guardian.application.errors import ServiceUnavailableError
from experiment_guardian.domain.contracts import SummaryQueueEnvelope
from experiment_guardian.infrastructure.bedrock import BedrockSummaryGenerator
from experiment_guardian.infrastructure.queue import SqsSubmissionQueue


class FakeSqsClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.deleted: list[dict[str, object]] = []
        self.changed: list[dict[str, object]] = []

    def send_message(self, **kwargs: object) -> dict[str, str]:
        self.sent.append(kwargs)
        return {"MessageId": "sqs-message-1"}

    def receive_message(self, **_: object) -> dict[str, object]:
        return {
            "Messages": [
                {
                    "MessageId": "sqs-message-1",
                    "ReceiptHandle": "receipt-1",
                    "Body": self.sent[0]["MessageBody"],
                    "Attributes": {"ApproximateReceiveCount": "3"},
                }
            ]
        }

    def delete_message(self, **kwargs: object) -> None:
        self.deleted.append(kwargs)

    def change_message_visibility(self, **kwargs: object) -> None:
        self.changed.append(kwargs)


class FakeBedrockClient:
    def __init__(self, response: dict[str, object] | None = None) -> None:
        self.response = response or {
            "output": {"message": {"content": [{"text": "summary"}]}},
            "usage": {"inputTokens": 12, "outputTokens": 3},
        }
        self.calls: list[dict[str, object]] = []
        self.error: Exception | None = None

    def converse(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def test_sqs_adapter_uses_minimal_envelope_and_visibility_contract() -> None:
    client = FakeSqsClient()
    queue = SqsSubmissionQueue(
        queue_url="https://sqs.example/queue",
        region="us-east-1",
        wait_time_seconds=20,
        visibility_timeout_seconds=120,
        client=client,
    )
    envelope = SummaryQueueEnvelope(
        job_id=uuid4(),
        submission_id=uuid4(),
        generation=2,
    )

    assert queue.send(envelope) == "sqs-message-1"
    body = json.loads(str(client.sent[0]["MessageBody"]))
    assert set(body) == {"schema_version", "job_id", "submission_id", "generation"}
    delivery = queue.receive()[0]
    assert delivery.receive_count == 3
    queue.change_visibility(delivery.receipt_handle, 30)
    queue.delete(delivery.receipt_handle)
    assert client.changed[0]["VisibilityTimeout"] == 30
    assert client.deleted[0]["ReceiptHandle"] == "receipt-1"


def test_bedrock_adapter_accepts_only_plain_text_converse_output() -> None:
    client = FakeBedrockClient()
    generator = BedrockSummaryGenerator(
        model_id="model-v1",
        region="us-east-1",
        client=client,
    )
    result = generator.generate(system_prompt="system", user_prompt="facts")
    assert result.text == "summary"
    assert result.input_tokens == 12
    assert result.output_tokens == 3
    assert client.calls[0]["modelId"] == "model-v1"
    assert "toolConfig" not in client.calls[0]


@pytest.mark.parametrize(
    "response",
    [
        {"output": {"message": {"content": [{"toolUse": {"name": "approve"}}]}}},
        {"output": {"message": {"content": [{"image": {}}]}}},
        {"output": {"message": {"content": "not-a-list"}}},
    ],
)
def test_bedrock_adapter_rejects_tool_or_unknown_content(
    response: dict[str, object],
) -> None:
    generator = BedrockSummaryGenerator(
        model_id="model-v1",
        region="us-east-1",
        client=FakeBedrockClient(response),
    )
    with pytest.raises(ServiceUnavailableError, match="无效"):
        generator.generate(system_prompt="system", user_prompt="facts")


def test_bedrock_dependency_errors_are_retryable_service_failures() -> None:
    client = FakeBedrockClient()
    client.error = PermissionError("access denied")
    generator = BedrockSummaryGenerator(
        model_id="model-v1",
        region="us-east-1",
        client=client,
    )
    with pytest.raises(ServiceUnavailableError, match="暂时不可用"):
        generator.generate(system_prompt="system", user_prompt="facts")


def test_cloud_adapters_fail_fast_when_required_configuration_is_missing() -> None:
    with pytest.raises(ValueError, match="SQS_SUBMISSION_QUEUE_URL"):
        SqsSubmissionQueue(queue_url="", region="us-east-1")
    with pytest.raises(ValueError, match="BEDROCK_SUMMARY_MODEL_ID"):
        BedrockSummaryGenerator(model_id="", region="us-east-1")
