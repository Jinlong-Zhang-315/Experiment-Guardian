"""AWS SQS Standard 适配器。消息体不携带配置、指标或模型摘要。"""

from collections.abc import Sequence
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]

from experiment_guardian.application.errors import ServiceUnavailableError
from experiment_guardian.application.ports import QueueDelivery, SubmissionQueue
from experiment_guardian.domain.contracts import WorkflowQueueEnvelope


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
