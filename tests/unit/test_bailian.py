"""百炼 OpenAI-compatible 适配器的严格 HTTP mock 测试。"""

import json
import math

import httpx
import pytest

from experiment_guardian.application.errors import ServiceUnavailableError
from experiment_guardian.domain.agent import (
    AgentChatMessage,
    AgentResponseFormat,
    AgentToolSpec,
)
from experiment_guardian.infrastructure.bailian import (
    BailianAgentChatModel,
    BailianEmbeddingGenerator,
    BailianSummaryGenerator,
)


def _client(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def test_bailian_summary_uses_deterministic_plain_text_request() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "stable summary"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
        )

    generator = BailianSummaryGenerator(
        api_key="secret",
        base_url="https://bailian.example/v1",
        model_id="qwen-summary",
        client=_client(handler),
    )
    result = generator.generate(system_prompt="system", user_prompt="facts")
    assert generator.provider == "bailian"
    assert result.text == "stable summary"
    assert captured["temperature"] == 0
    assert "tools" not in captured


@pytest.mark.parametrize(
    ("status", "payload"),
    [
        (500, {"error": "unavailable"}),
        (200, {"choices": [{"message": {"content": ""}}]}),
        (200, {"choices": [{"message": {"content": "x", "tool_calls": [{}]}}]}),
    ],
)
def test_bailian_summary_rejects_http_empty_and_tool_outputs(
    status: int, payload: dict[str, object]
) -> None:
    generator = BailianSummaryGenerator(
        api_key="secret",
        base_url="https://bailian.example/v1",
        model_id="qwen-summary",
        client=_client(lambda _: httpx.Response(status, json=payload)),
    )
    with pytest.raises(ServiceUnavailableError):
        generator.generate(system_prompt="system", user_prompt="facts")


@pytest.mark.parametrize(
    "payload",
    [
        {"choices": [{"message": None}]},
        {"choices": [{"message": []}]},
        {"choices": [None]},
        {
            "choices": [{"message": {"content": "summary"}}],
            "usage": [],
        },
    ],
)
def test_bailian_summary_normalizes_malformed_success_responses(
    payload: dict[str, object],
) -> None:
    generator = BailianSummaryGenerator(
        api_key="secret",
        base_url="https://bailian.example/v1",
        model_id="qwen-summary",
        client=_client(lambda _: httpx.Response(200, json=payload)),
    )
    with pytest.raises(ServiceUnavailableError, match="无效的摘要响应"):
        generator.generate(system_prompt="system", user_prompt="facts")


def test_bailian_timeout_is_retryable_service_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    generator = BailianSummaryGenerator(
        api_key="secret",
        base_url="https://bailian.example/v1",
        model_id="qwen-summary",
        client=_client(handler),
    )
    with pytest.raises(ServiceUnavailableError, match="暂时不可用"):
        generator.generate(system_prompt="system", user_prompt="facts")


def test_bailian_embedding_validates_dimension_and_normalizes() -> None:
    vector = [2.0, *([0.0] * 1023)]
    generator = BailianEmbeddingGenerator(
        api_key="secret",
        base_url="https://bailian.example/v1",
        model_id="text-embedding",
        client=_client(
            lambda _: httpx.Response(
                200,
                json={"data": [{"embedding": vector}], "usage": {"total_tokens": 8}},
            )
        ),
    )
    output = generator.embed("stable facts")
    assert generator.provider == "bailian"
    assert len(output.vector) == 1024
    assert math.isclose(sum(item * item for item in output.vector), 1.0)
    assert output.input_tokens == 8


@pytest.mark.parametrize(
    "vector",
    [
        [1.0, *([0.0] * 1022)],
        [float("nan"), *([0.0] * 1023)],
        [float("inf"), *([0.0] * 1023)],
        [0.0] * 1024,
    ],
)
def test_bailian_embedding_rejects_invalid_vectors(vector: list[float]) -> None:
    generator = BailianEmbeddingGenerator(
        api_key="secret",
        base_url="https://bailian.example/v1",
        model_id="text-embedding",
        client=_client(
            lambda _: httpx.Response(
                200,
                content=json.dumps(
                    {"data": [{"embedding": vector}]}, allow_nan=True
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
        ),
    )
    with pytest.raises(ServiceUnavailableError, match="embedding"):
        generator.embed("stable facts")


@pytest.mark.parametrize(
    "payload",
    [
        {"data": [{}]},
        {"data": [None]},
        {"data": [[]]},
        {"data": [{"embedding": [1.0, *([0.0] * 1023)]}], "usage": []},
    ],
)
def test_bailian_embedding_normalizes_malformed_success_responses(
    payload: dict[str, object],
) -> None:
    generator = BailianEmbeddingGenerator(
        api_key="secret",
        base_url="https://bailian.example/v1",
        model_id="text-embedding",
        client=_client(lambda _: httpx.Response(200, json=payload)),
    )
    with pytest.raises(ServiceUnavailableError, match="无效的 embedding 响应"):
        generator.embed("stable facts")


def _sse(*chunks: dict[str, object]) -> bytes:
    return (
        "".join(
            f"data: {json.dumps(item, ensure_ascii=False)}\n\n" for item in chunks
        )
        + "data: [DONE]\n\n"
    ).encode()


def test_bailian_agent_streams_text_usage_and_json_mode() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            content=_sse(
                {
                    "id": "req-1",
                    "choices": [{"delta": {"content": '{"answer_markdown":"ok"'}}],
                },
                {
                    "id": "req-1",
                    "choices": [
                        {
                            "delta": {"content": "}"},
                            "finish_reason": "stop",
                        }
                    ],
                },
                {
                    "choices": [],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3},
                },
            ),
            headers={"Content-Type": "text/event-stream"},
        )

    model = BailianAgentChatModel(
        api_key="secret",
        base_url="https://bailian.example/v1",
        model_id="qwen-agent",
        client=_client(handler),
    )
    events = list(
        model.stream_turn(
            messages=[AgentChatMessage(role="user", content="status")],
            tools=[],
            tool_choice="none",
            max_output_tokens=200,
            response_format=AgentResponseFormat(
                name="TestAnswer",
                description="Test JSON answer.",
                json_schema={"type": "object"},
            ),
        )
    )
    assert "".join(item.text or "" for item in events) == '{"answer_markdown":"ok"}'
    assert captured["stream"] is True
    assert captured["response_format"] == {"type": "json_object"}
    assert any(
        item.usage is not None and item.usage.input_tokens == 12 for item in events
    )


def test_bailian_agent_assembles_fragmented_tool_call() -> None:
    model = BailianAgentChatModel(
        api_key="secret",
        base_url="https://bailian.example/v1",
        model_id="qwen-agent",
        client=_client(
            lambda _: httpx.Response(
                200,
                content=_sse(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_1",
                                            "function": {
                                                "name": "experiment_",
                                                "arguments": '{"experiment_',
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "function": {
                                            "name": "get_v1",
                                            "arguments": (
                                                'id":"00000000-0000-0000-0000-'
                                                '000000000001"}'
                                            ),
                                            },
                                        }
                                    ]
                                },
                                "finish_reason": "tool_calls",
                            }
                        ]
                    },
                ),
                headers={"Content-Type": "text/event-stream"},
            )
        ),
    )
    events = list(
        model.stream_turn(
            messages=[AgentChatMessage(role="user", content="get")],
            tools=[
                AgentToolSpec(
                    name="experiment_get_v1",
                    version="1",
                    description="get",
                    input_schema={"type": "object"},
                )
            ],
            tool_choice="auto",
            max_output_tokens=200,
        )
    )
    call = next(item.tool_call for item in events if item.tool_call is not None)
    assert call.name == "experiment_get_v1"
    assert str(call.arguments["experiment_id"]).endswith("0001")


@pytest.mark.parametrize(
    "content",
    [
        b"data: not-json\n\ndata: [DONE]\n\n",
        b'data: {"choices":[]}\n\n',
        _sse({"choices": [{"delta": None}]}),
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call",
                                    "function": {"name": "x", "arguments": "{"},
                                }
                            ]
                        }
                    }
                ]
            }
        ),
    ],
)
def test_bailian_agent_normalizes_malformed_streams(content: bytes) -> None:
    model = BailianAgentChatModel(
        api_key="secret",
        base_url="https://bailian.example/v1",
        model_id="qwen-agent",
        client=_client(
            lambda _: httpx.Response(
                200, content=content, headers={"Content-Type": "text/event-stream"}
            )
        ),
    )
    with pytest.raises(ServiceUnavailableError):
        list(
            model.stream_turn(
                messages=[AgentChatMessage(role="user", content="x")],
                tools=[],
                tool_choice="auto",
                max_output_tokens=100,
            )
        )
