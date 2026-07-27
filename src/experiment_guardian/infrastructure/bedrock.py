"""Bedrock 摘要与 Titan V2 embedding 适配器。"""

import json
import math
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]

from experiment_guardian.application.errors import ServiceUnavailableError
from experiment_guardian.application.ports import (
    AgentChatModel,
    EmbeddingGenerator,
    EmbeddingModelOutput,
    SummaryModelOutput,
    SummaryTextGenerator,
)
from experiment_guardian.domain.agent import (
    AgentChatMessage,
    AgentModelEvent,
    AgentModelUsage,
    AgentResponseFormat,
    AgentToolRequest,
    AgentToolSpec,
)

TITAN_V2_MODEL_ID = "amazon.titan-embed-text-v2:0"
TITAN_V2_DIMENSION = 1024
MAX_EMBEDDING_RESPONSE_BYTES = 256 * 1024
MAX_AGENT_RESPONSE_BYTES = 512 * 1024


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
    def provider(self) -> str:
        return "bedrock"

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
    def provider(self) -> str:
        return "bedrock"

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


class BedrockAgentChatModel(AgentChatModel):
    """Bedrock ConverseStream 工具调用和严格结构化输出适配器。"""

    def __init__(
        self,
        *,
        model_id: str,
        region: str,
        connect_timeout_seconds: int = 5,
        read_timeout_seconds: int = 90,
        client: Any | None = None,
    ) -> None:
        if not model_id.strip():
            raise ValueError("BEDROCK_AGENT_MODEL_ID 未配置")
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
    def provider(self) -> str:
        return "bedrock"

    @property
    def model_id(self) -> str:
        return self._model_id

    def stream_turn(
        self,
        *,
        messages: Sequence[AgentChatMessage],
        tools: Sequence[AgentToolSpec],
        tool_choice: str,
        max_output_tokens: int,
        response_format: AgentResponseFormat | None = None,
    ) -> Iterator[AgentModelEvent]:
        if tool_choice not in {"auto", "none"}:
            raise ValueError("Bedrock Agent tool_choice 只允许 auto 或 none")
        if response_format is None:
            raise ValueError("Bedrock Agent 必须使用严格 Structured Outputs Schema")
        system, conversation = self._messages_payload(messages)
        request: dict[str, Any] = {
            "modelId": self._model_id,
            "messages": conversation,
            "inferenceConfig": {"maxTokens": max_output_tokens, "temperature": 0},
        }
        if system:
            request["system"] = [{"text": system}]
        if tool_choice == "auto" and tools:
            request["toolConfig"] = {
                "tools": [
                    {
                        "toolSpec": {
                            "name": item.name,
                            "description": item.description,
                            "inputSchema": {"json": item.input_schema},
                            "strict": True,
                        }
                    }
                    for item in tools
                ],
                "toolChoice": {"auto": {}},
            }
        request["outputConfig"] = {
            "textFormat": {
                "type": "json_schema",
                "structure": {
                    "jsonSchema": {
                        "schema": json.dumps(
                            response_format.json_schema,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        "name": response_format.name,
                        "description": response_format.description,
                    }
                },
            }
        }
        try:
            response = self._client.converse_stream(**request)
            stream = response["stream"]
            request_id = (response.get("ResponseMetadata") or {}).get("RequestId")
            if request_id is not None and not isinstance(request_id, str):
                raise ValueError("Bedrock RequestId 无效")
            yield from self._parse_stream(stream, provider_request_id=request_id)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            raise ServiceUnavailableError("Bedrock Agent 模型服务暂时不可用") from exc

    @staticmethod
    def _messages_payload(
        messages: Sequence[AgentChatMessage],
    ) -> tuple[str, list[dict[str, Any]]]:
        system_parts: list[str] = []
        result: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "system":
                if result:
                    raise ValueError("Bedrock System Message 必须位于对话开头")
                system_parts.append(message.content)
                continue
            blocks: list[dict[str, Any]]
            if message.role == "tool":
                if message.tool_call_id is None:
                    raise ValueError("Bedrock Tool Result 缺少 tool_call_id")
                try:
                    decoded = json.loads(message.content)
                    content: dict[str, Any] = {"json": decoded}
                except ValueError:
                    content = {"text": message.content}
                role = "user"
                blocks = [
                    {
                        "toolResult": {
                            "toolUseId": message.tool_call_id,
                            "content": [content],
                            "status": "success",
                        }
                    }
                ]
            else:
                role = message.role
                blocks = []
                if message.content:
                    blocks.append({"text": message.content})
                if message.tool_calls:
                    if message.content:
                        raise ValueError("Bedrock Assistant Message 不能混合正文和工具调用")
                    for item in message.tool_calls:
                        try:
                            function = item["function"]
                            arguments = json.loads(function["arguments"] or "{}")
                            if not isinstance(arguments, dict):
                                raise ValueError("工具参数不是对象")
                            blocks.append(
                                {
                                    "toolUse": {
                                        "toolUseId": item["id"],
                                        "name": function["name"],
                                        "input": arguments,
                                    }
                                }
                            )
                        except (KeyError, TypeError, ValueError) as exc:
                            raise ValueError("Bedrock Assistant Tool Call 无效") from exc
                if not blocks:
                    raise ValueError("Bedrock Message 内容为空")
            if result and result[-1]["role"] == role:
                result[-1]["content"].extend(blocks)
            else:
                result.append({"role": role, "content": blocks})
        if not result:
            raise ValueError("Bedrock 对话缺少用户消息")
        return "\n\n".join(system_parts), result

    @staticmethod
    def _parse_stream(
        stream: object,
        *,
        provider_request_id: str | None,
    ) -> Iterator[AgentModelEvent]:
        if not isinstance(stream, Iterable):
            raise ServiceUnavailableError("Bedrock Agent 响应缺少事件流")
        consumed = 0
        tool_fragments: dict[int, dict[str, str]] = {}
        usage: AgentModelUsage | None = None
        finish_reason: str | None = None
        saw_text = False
        saw_message_stop = False
        try:
            for raw_event in stream:
                if not isinstance(raw_event, dict):
                    raise ValueError("Bedrock 流事件不是对象")
                consumed += len(
                    json.dumps(raw_event, default=str, separators=(",", ":")).encode()
                )
                if consumed > MAX_AGENT_RESPONSE_BYTES:
                    raise ValueError("Bedrock Agent 流式响应超过大小上限")
                error_name = next(
                    (name for name in raw_event if name.lower().endswith("exception")),
                    None,
                )
                if error_name is not None:
                    raise ValueError(f"Bedrock 流返回错误事件: {error_name}")
                if "contentBlockStart" in raw_event:
                    start_event = raw_event["contentBlockStart"]
                    if not isinstance(start_event, dict):
                        raise ValueError("contentBlockStart 无效")
                    index = start_event.get("contentBlockIndex")
                    start = start_event.get("start")
                    if (
                        isinstance(index, bool)
                        or not isinstance(index, int)
                        or not isinstance(start, dict)
                    ):
                        raise ValueError("工具内容块索引无效")
                    tool = start.get("toolUse")
                    if tool is not None:
                        if not isinstance(tool, dict):
                            raise ValueError("toolUse start 无效")
                        call_id = tool.get("toolUseId")
                        name = tool.get("name")
                        if not isinstance(call_id, str) or not isinstance(name, str):
                            raise ValueError("toolUse 标识无效")
                        tool_fragments[index] = {
                            "id": call_id,
                            "name": name,
                            "arguments": "",
                        }
                elif "contentBlockDelta" in raw_event:
                    delta_event = raw_event["contentBlockDelta"]
                    if not isinstance(delta_event, dict):
                        raise ValueError("contentBlockDelta 无效")
                    index = delta_event.get("contentBlockIndex")
                    delta = delta_event.get("delta")
                    if (
                        isinstance(index, bool)
                        or not isinstance(index, int)
                        or not isinstance(delta, dict)
                    ):
                        raise ValueError("内容增量无效")
                    if "text" in delta:
                        text = delta["text"]
                        if not isinstance(text, str):
                            raise ValueError("文本增量不是字符串")
                        if text:
                            saw_text = True
                            yield AgentModelEvent(event_type="text_delta", text=text)
                    if "toolUse" in delta:
                        tool_delta = delta["toolUse"]
                        if not isinstance(tool_delta, dict):
                            raise ValueError("toolUse delta 无效")
                        fragment = tool_delta.get("input")
                        if not isinstance(fragment, str) or index not in tool_fragments:
                            raise ValueError("toolUse 参数增量无效")
                        tool_fragments[index]["arguments"] += fragment
                elif "messageStop" in raw_event:
                    stop = raw_event["messageStop"]
                    if not isinstance(stop, dict) or not isinstance(
                        stop.get("stopReason"), str
                    ):
                        raise ValueError("messageStop 无效")
                    finish_reason = stop["stopReason"]
                    saw_message_stop = True
                elif "metadata" in raw_event:
                    metadata = raw_event["metadata"]
                    if not isinstance(metadata, dict):
                        raise ValueError("metadata 无效")
                    raw_usage = metadata.get("usage")
                    if raw_usage is not None:
                        if not isinstance(raw_usage, dict):
                            raise ValueError("usage 无效")
                        usage = AgentModelUsage(
                            input_tokens=_optional_nonnegative_int(
                                raw_usage.get("inputTokens")
                            ),
                            output_tokens=_optional_nonnegative_int(
                                raw_usage.get("outputTokens")
                            ),
                        )
                elif not ({"messageStart", "contentBlockStop"} & raw_event.keys()):
                    raise ValueError("Bedrock 返回未知流事件")
            if not saw_message_stop:
                raise ValueError("Bedrock Agent 流在 messageStop 前中断")
            if tool_fragments and saw_text:
                raise ValueError("Bedrock Agent 响应同时包含正文和工具调用")
            for index in sorted(tool_fragments):
                fragment = tool_fragments[index]
                arguments = json.loads(fragment["arguments"] or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("工具参数不是对象")
                yield AgentModelEvent(
                    event_type="tool_call",
                    tool_call=AgentToolRequest(
                        call_id=fragment["id"],
                        name=fragment["name"],
                        arguments=arguments,
                    ),
                )
            if usage is not None:
                yield AgentModelEvent(event_type="usage", usage=usage)
            yield AgentModelEvent(
                event_type="completed",
                finish_reason=finish_reason,
                provider_request_id=provider_request_id,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ServiceUnavailableError("Bedrock 返回了无效的 Agent 流式响应") from exc


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
