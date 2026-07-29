"""阿里云百炼 OpenAI-compatible 摘要与 embedding 适配器。"""

import json
import math
from collections.abc import Iterator, Sequence
from typing import Any
from urllib.parse import urlparse

import httpx

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

MAX_SUMMARY_RESPONSE_BYTES = 64 * 1024
MAX_EMBEDDING_RESPONSE_BYTES = 512 * 1024
MAX_AGENT_RESPONSE_BYTES = 512 * 1024
REQUIRED_DIMENSION = 1024


class _BailianClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        connect_timeout_seconds: int,
        read_timeout_seconds: int,
        client: httpx.Client | None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("BAILIAN_API_KEY 未配置")
        normalized_url = base_url.strip().rstrip("/")
        parsed = urlparse(normalized_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("BAILIAN_BASE_URL 必须是有效的 HTTP(S) URL")
        self._base_url = normalized_url
        self._client = client or httpx.Client(
            headers={"Authorization": f"Bearer {api_key.strip()}"},
            timeout=httpx.Timeout(
                timeout=read_timeout_seconds,
                connect=connect_timeout_seconds,
            ),
        )

    def post(self, path: str, payload: dict[str, Any], *, max_bytes: int) -> dict[str, Any]:
        try:
            response = self._client.post(f"{self._base_url}{path}", json=payload)
            response.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            raise ServiceUnavailableError("百炼模型服务暂时不可用") from exc
        if len(response.content) > max_bytes:
            raise ServiceUnavailableError("百炼模型响应超过大小上限")
        try:
            decoded = response.json()
        except ValueError as exc:
            raise ServiceUnavailableError("百炼返回了非 JSON 响应") from exc
        if not isinstance(decoded, dict):
            raise ServiceUnavailableError("百炼返回了无效的 JSON 对象")
        return decoded

    def stream_sse(
        self, path: str, payload: dict[str, Any], *, max_bytes: int
    ) -> Iterator[dict[str, Any]]:
        consumed = 0
        completed = False
        terminal_chunk_received = False
        try:
            with self._client.stream(
                "POST", f"{self._base_url}{path}", json=payload
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    consumed += len(line.encode("utf-8"))
                    if consumed > max_bytes:
                        raise ServiceUnavailableError("百炼流式响应超过大小上限")
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        completed = True
                        break
                    try:
                        decoded = json.loads(raw)
                    except ValueError as exc:
                        raise ServiceUnavailableError("百炼返回了无效 SSE JSON") from exc
                    if not isinstance(decoded, dict):
                        raise ServiceUnavailableError("百炼 SSE 数据不是 JSON 对象")
                    choices = decoded.get("choices")
                    if isinstance(choices, list) and any(
                        isinstance(choice, dict)
                        and isinstance(choice.get("finish_reason"), str)
                        and bool(choice["finish_reason"])
                        for choice in choices
                    ):
                        terminal_chunk_received = True
                    yield decoded
            # DashScope 偶尔在已发送 OpenAI 终态 finish_reason 后直接结束 HTTP 流，
            # 不再补 `[DONE]`。终态 chunk 足以证明语义完成；两者都没有时仍按截断失败。
            if not completed and not terminal_chunk_received:
                raise ServiceUnavailableError("百炼流式响应在完成标记前中断")
        except ServiceUnavailableError:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise ServiceUnavailableError("百炼模型流式服务暂时不可用") from exc


class BailianSummaryGenerator(SummaryTextGenerator):
    """调用百炼 chat/completions；不声明工具且固定 temperature=0。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model_id: str,
        connect_timeout_seconds: int = 5,
        read_timeout_seconds: int = 60,
        client: httpx.Client | None = None,
    ) -> None:
        if not model_id.strip():
            raise ValueError("BAILIAN_SUMMARY_MODEL 未配置")
        self._model_id = model_id.strip()
        self._api = _BailianClient(
            api_key=api_key,
            base_url=base_url,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            client=client,
        )

    @property
    def provider(self) -> str:
        return "bailian"

    @property
    def model_id(self) -> str:
        return self._model_id

    def generate(self, *, system_prompt: str, user_prompt: str) -> SummaryModelOutput:
        payload = self._api.post(
            "/chat/completions",
            {
                "model": self._model_id,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0,
                "max_tokens": 1200,
            },
            max_bytes=MAX_SUMMARY_RESPONSE_BYTES,
        )
        try:
            choices = payload["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError("choices 数量无效")
            choice = choices[0]
            if not isinstance(choice, dict):
                raise ValueError("choice 不是对象")
            message = choice.get("message")
            if not isinstance(message, dict):
                raise ValueError("message 不是对象")
            if message.get("tool_calls"):
                raise ValueError("响应包含工具调用")
            text = message.get("content")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("摘要为空")
            raw_usage = payload.get("usage")
            if raw_usage is not None and not isinstance(raw_usage, dict):
                raise ValueError("usage 不是对象")
            usage = raw_usage or {}
            return SummaryModelOutput(
                text=text.strip(),
                input_tokens=_optional_nonnegative_int(usage.get("prompt_tokens")),
                output_tokens=_optional_nonnegative_int(usage.get("completion_tokens")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ServiceUnavailableError("百炼返回了无效的摘要响应") from exc


class BailianEmbeddingGenerator(EmbeddingGenerator):
    """调用百炼 embeddings，并将合法非零向量归一化为现有 1024 维契约。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model_id: str,
        dimension: int = REQUIRED_DIMENSION,
        connect_timeout_seconds: int = 5,
        read_timeout_seconds: int = 60,
        client: httpx.Client | None = None,
    ) -> None:
        if not model_id.strip():
            raise ValueError("BAILIAN_EMBEDDING_MODEL 未配置")
        if dimension != REQUIRED_DIMENSION:
            raise ValueError("百炼 embedding 维度必须为 1024")
        self._model_id = model_id.strip()
        self._dimension = dimension
        self._api = _BailianClient(
            api_key=api_key,
            base_url=base_url,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            client=client,
        )

    @property
    def provider(self) -> str:
        return "bailian"

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, input_text: str) -> EmbeddingModelOutput:
        if not input_text or len(input_text) > 16000:
            raise ValueError("embedding 输入必须为 1 到 16000 个字符")
        payload = self._api.post(
            "/embeddings",
            {
                "model": self._model_id,
                "input": input_text,
                "dimensions": self._dimension,
                "encoding_format": "float",
            },
            max_bytes=MAX_EMBEDDING_RESPONSE_BYTES,
        )
        try:
            data = payload["data"]
            if not isinstance(data, list) or len(data) != 1:
                raise ValueError("embedding data 数量无效")
            item = data[0]
            if not isinstance(item, dict):
                raise ValueError("embedding item 不是对象")
            raw_vector = item.get("embedding")
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
            if not math.isfinite(norm) or norm <= 1e-12:
                raise ValueError("embedding 范数无效")
            normalized = [item / norm for item in vector]
            raw_usage = payload.get("usage")
            if raw_usage is not None and not isinstance(raw_usage, dict):
                raise ValueError("usage 不是对象")
            usage = raw_usage or {}
            return EmbeddingModelOutput(
                vector=normalized,
                input_tokens=_optional_nonnegative_int(
                    usage.get("prompt_tokens", usage.get("total_tokens"))
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ServiceUnavailableError("百炼返回了无效的 embedding 响应") from exc


class BailianAgentChatModel(AgentChatModel):
    """百炼 OpenAI-compatible 流式 Function Calling 适配器。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model_id: str,
        connect_timeout_seconds: int = 5,
        read_timeout_seconds: int = 90,
        client: httpx.Client | None = None,
    ) -> None:
        if not model_id.strip():
            raise ValueError("BAILIAN_AGENT_MODEL 未配置")
        self._model_id = model_id.strip()
        self._api = _BailianClient(
            api_key=api_key,
            base_url=base_url,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            client=client,
        )

    @property
    def provider(self) -> str:
        return "bailian"

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def structured_final_requires_tool_choice_none(self) -> bool:
        return True

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
            raise ValueError("百炼 Agent tool_choice 只允许 auto 或 none")
        message_payloads = [self._message_payload(item) for item in messages]
        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": message_payloads,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": item.name,
                        "description": item.description,
                        "parameters": item.input_schema,
                    },
                }
                for item in tools
            ],
            "tool_choice": tool_choice,
            "temperature": 0,
            "max_tokens": max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            # 治理输出需要短、稳定且可校验。百炼 Qwen 混合思考模型默认可能消耗
            # 大量 reasoning token，并在 JSON 完成前触发 length。
            "enable_thinking": False,
        }
        # 百炼 OpenAI-compatible 的 json_object 会让部分 Qwen 模型把工具调用编码进正文。
        # 工具选择回合保持原生 Function Calling；强制最终回合再启用 JSON，并由服务端
        # Pydantic 执行完整 Schema 校验。
        if response_format is not None and tool_choice == "none":
            payload["response_format"] = {"type": "json_object"}
            schema_instruction = {
                "role": "system",
                "content": (
                    "最终响应只能是符合下列 JSON Schema 的单个 JSON 对象；不得改名、"
                    "省略 required 字段或增加字段：\n"
                    + json.dumps(
                        response_format.json_schema,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                ),
            }
            insert_at = 1 if message_payloads and message_payloads[0]["role"] == "system" else 0
            message_payloads.insert(insert_at, schema_instruction)

        tool_fragments: dict[int, dict[str, str]] = {}
        usage: AgentModelUsage | None = None
        finish_reason: str | None = None
        provider_request_id: str | None = None
        text_parts: list[str] = []
        for chunk in self._api.stream_sse(
            "/chat/completions", payload, max_bytes=MAX_AGENT_RESPONSE_BYTES
        ):
            try:
                raw_id = chunk.get("id")
                if raw_id is not None and not isinstance(raw_id, str):
                    raise ValueError("响应 id 无效")
                provider_request_id = raw_id or provider_request_id
                raw_usage = chunk.get("usage")
                if raw_usage is not None:
                    if not isinstance(raw_usage, dict):
                        raise ValueError("usage 不是对象")
                    usage = AgentModelUsage(
                        input_tokens=_optional_nonnegative_int(
                            raw_usage.get("prompt_tokens")
                        ),
                        output_tokens=_optional_nonnegative_int(
                            raw_usage.get("completion_tokens")
                        ),
                    )
                choices = chunk.get("choices", [])
                if not isinstance(choices, list) or len(choices) > 1:
                    raise ValueError("choices 数量无效")
                if not choices:
                    continue
                choice = choices[0]
                if not isinstance(choice, dict):
                    raise ValueError("choice 不是对象")
                raw_finish = choice.get("finish_reason")
                if raw_finish is not None and not isinstance(raw_finish, str):
                    raise ValueError("finish_reason 无效")
                finish_reason = raw_finish or finish_reason
                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    raise ValueError("delta 不是对象")
                content = delta.get("content")
                if content is not None:
                    if not isinstance(content, str):
                        raise ValueError("content delta 不是字符串")
                    if content:
                        text_parts.append(content)
                raw_calls = delta.get("tool_calls")
                if raw_calls is not None:
                    if not isinstance(raw_calls, list):
                        raise ValueError("tool_calls delta 不是数组")
                    for raw_call in raw_calls:
                        self._merge_tool_fragment(tool_fragments, raw_call)
            except (KeyError, TypeError, ValueError) as exc:
                raise ServiceUnavailableError("百炼返回了无效的 Agent 流式响应") from exc

        if tool_fragments:
            for index in sorted(tool_fragments):
                fragment = tool_fragments[index]
                try:
                    raw_arguments = json.loads(fragment["arguments"] or "{}")
                    if not isinstance(raw_arguments, dict):
                        raise ValueError("工具参数不是对象")
                    call = AgentToolRequest(
                        call_id=fragment["id"],
                        name=fragment["name"],
                        arguments=raw_arguments,
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise ServiceUnavailableError("百炼返回了无效的工具调用") from exc
                yield AgentModelEvent(event_type="tool_call", tool_call=call)
        else:
            for text_part in text_parts:
                yield AgentModelEvent(event_type="text_delta", text=text_part)
        if usage is not None:
            yield AgentModelEvent(event_type="usage", usage=usage)
        yield AgentModelEvent(
            event_type="completed",
            finish_reason=finish_reason,
            provider_request_id=provider_request_id,
        )

    @staticmethod
    def _message_payload(message: AgentChatMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": message.role,
            "content": message.content,
        }
        if message.tool_call_id is not None:
            payload["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            payload["tool_calls"] = message.tool_calls
        return payload

    @staticmethod
    def _merge_tool_fragment(
        fragments: dict[int, dict[str, str]], raw_call: object
    ) -> None:
        if not isinstance(raw_call, dict):
            raise ValueError("tool_call 不是对象")
        index = raw_call.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("tool_call index 无效")
        target = fragments.setdefault(
            index, {"id": "", "name": "", "arguments": ""}
        )
        call_id = raw_call.get("id")
        if call_id is not None:
            if not isinstance(call_id, str):
                raise ValueError("tool_call id 无效")
            target["id"] += call_id
        function = raw_call.get("function")
        if function is not None:
            if not isinstance(function, dict):
                raise ValueError("tool_call function 无效")
            name = function.get("name")
            arguments = function.get("arguments")
            if name is not None:
                if not isinstance(name, str):
                    raise ValueError("tool_call name 无效")
                target["name"] += name
            if arguments is not None:
                if not isinstance(arguments, str):
                    raise ValueError("tool_call arguments 无效")
                target["arguments"] += arguments


def _optional_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
