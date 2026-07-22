"""Bedrock Converse 摘要适配器。模型只返回解释性纯文本。"""

from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]

from experiment_guardian.application.errors import ServiceUnavailableError
from experiment_guardian.application.ports import SummaryModelOutput, SummaryTextGenerator


class BedrockSummaryGenerator(SummaryTextGenerator):
    def __init__(
        self,
        *,
        model_id: str,
        region: str,
        connect_timeout_seconds: int = 5,
        read_timeout_seconds: int = 60,
        client: Any | None = None,
    ) -> None:
        if not model_id.strip():
            raise ValueError("BEDROCK_SUMMARY_MODEL_ID 未配置")
        self._model_id = model_id.strip()
        self._client = client or boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(
                connect_timeout=connect_timeout_seconds,
                read_timeout=read_timeout_seconds,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )

    @property
    def model_id(self) -> str:
        return self._model_id

    def generate(self, *, system_prompt: str, user_prompt: str) -> SummaryModelOutput:
        try:
            response = self._client.converse(
                modelId=self._model_id,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                inferenceConfig={"maxTokens": 1200, "temperature": 0},
            )
        except Exception as exc:
            # 权限、限流、网络和模型暂不可用都由 Worker 持久化为可重试暂停。
            raise ServiceUnavailableError("Bedrock 摘要生成暂时不可用") from exc

        try:
            content = response["output"]["message"]["content"]
            if not isinstance(content, list) or any("toolUse" in item for item in content):
                raise ValueError("模型返回了非文本内容")
            text_parts = [item["text"] for item in content if isinstance(item.get("text"), str)]
            if len(text_parts) != len(content):
                raise ValueError("模型响应包含未知内容块")
            usage = response.get("usage") or {}
            return SummaryModelOutput(
                text="\n".join(text_parts).strip(),
                input_tokens=_optional_nonnegative_int(usage.get("inputTokens")),
                output_tokens=_optional_nonnegative_int(usage.get("outputTokens")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ServiceUnavailableError("Bedrock 返回了无效的摘要响应") from exc


def _optional_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
