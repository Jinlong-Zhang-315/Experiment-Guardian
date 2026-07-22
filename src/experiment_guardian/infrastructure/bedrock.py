"""Bedrock 摘要与 Titan V2 embedding 适配器。"""

import json
import math
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]

from experiment_guardian.application.errors import ServiceUnavailableError
from experiment_guardian.application.ports import (
    EmbeddingGenerator,
    EmbeddingModelOutput,
    SummaryModelOutput,
    SummaryTextGenerator,
)

TITAN_V2_MODEL_ID = "amazon.titan-embed-text-v2:0"
TITAN_V2_DIMENSION = 1024
MAX_EMBEDDING_RESPONSE_BYTES = 256 * 1024


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


class BedrockTitanV2EmbeddingGenerator(EmbeddingGenerator):
    """调用 Titan Text Embeddings V2，并拒绝不满足固定向量契约的响应。"""

    def __init__(
        self,
        *,
        model_id: str,
        region: str,
        dimension: int = TITAN_V2_DIMENSION,
        connect_timeout_seconds: int = 5,
        read_timeout_seconds: int = 60,
        client: Any | None = None,
    ) -> None:
        if model_id.strip() != TITAN_V2_MODEL_ID:
            raise ValueError(f"BEDROCK_EMBEDDING_MODEL_ID 必须为 {TITAN_V2_MODEL_ID}")
        if dimension != TITAN_V2_DIMENSION:
            raise ValueError("R12b embedding 维度必须为 1024")
        self._model_id = model_id.strip()
        self._dimension = dimension
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

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, input_text: str) -> EmbeddingModelOutput:
        if not input_text or len(input_text) > 16000:
            raise ValueError("embedding 输入必须为 1 到 16000 个字符")
        body = json.dumps(
            {
                "inputText": input_text,
                "dimensions": self._dimension,
                "normalize": True,
                "embeddingTypes": ["float"],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            response = self._client.invoke_model(
                modelId=self._model_id,
                contentType="application/json",
                accept="application/json",
                body=body,
            )
            payload = json.loads(_read_bounded_body(response["body"]))
            raw_vector = payload["embedding"]
            if not isinstance(raw_vector, list) or len(raw_vector) != self._dimension:
                raise ValueError("embedding 维度错误")
            vector: list[float] = []
            for item in raw_vector:
                if isinstance(item, bool) or not isinstance(item, (int, float)):
                    raise ValueError("embedding 包含非数值元素")
                number = float(item)
                if not math.isfinite(number):
                    raise ValueError("embedding 包含非有限数值")
                vector.append(number)
            norm = math.sqrt(sum(item * item for item in vector))
            if not math.isclose(norm, 1.0, rel_tol=1e-3, abs_tol=1e-3):
                raise ValueError("embedding 未按请求归一化")
            return EmbeddingModelOutput(
                vector=vector,
                input_tokens=_optional_nonnegative_int(payload.get("inputTextTokenCount")),
            )
        except ValueError:
            raise
        except Exception as exc:
            raise ServiceUnavailableError("Bedrock embedding 生成暂时不可用") from exc


def _read_bounded_body(body: object) -> str:
    stream = body
    try:
        if isinstance(stream, bytes):
            raw = stream
        elif hasattr(stream, "read"):
            raw = stream.read(MAX_EMBEDDING_RESPONSE_BYTES + 1)
        else:
            raise ValueError("Bedrock 响应缺少可读 body")
        if not isinstance(raw, bytes) or len(raw) > MAX_EMBEDDING_RESPONSE_BYTES:
            raise ValueError("Bedrock embedding 响应超过大小上限")
        return raw.decode("utf-8")
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()


def _optional_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
