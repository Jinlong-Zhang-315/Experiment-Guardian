"""百炼 OpenAI-compatible 适配器的严格 HTTP mock 测试。"""

import json
import math

import httpx
import pytest

from experiment_guardian.application.errors import ServiceUnavailableError
from experiment_guardian.infrastructure.bailian import (
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
