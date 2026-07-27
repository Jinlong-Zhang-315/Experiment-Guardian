"""Bedrock ConverseStream Agent 适配器的 provider 契约测试。"""

from collections.abc import Iterator
from typing import Any

import pytest

from experiment_guardian.application.errors import ServiceUnavailableError
from experiment_guardian.domain.agent import (
    AgentChatMessage,
    AgentResponseFormat,
    AgentToolSpec,
)
from experiment_guardian.infrastructure.bedrock import BedrockAgentChatModel


class FakeBedrockAgentClient:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.request: dict[str, Any] | None = None

    def converse_stream(self, **request: Any) -> dict[str, Any]:
        self.request = request
        return {
            "stream": iter(self.events),
            "ResponseMetadata": {"RequestId": "bedrock-request-1"},
        }


def _format() -> AgentResponseFormat:
    return AgentResponseFormat(
        name="TestAnswer",
        description="Strict test answer.",
        json_schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
    )


def _model(events: list[dict[str, Any]]) -> tuple[BedrockAgentChatModel, FakeBedrockAgentClient]:
    client = FakeBedrockAgentClient(events)
    return (
        BedrockAgentChatModel(
            model_id="us.anthropic.test-model-v1:0",
            region="us-east-1",
            client=client,
        ),
        client,
    )


def test_bedrock_agent_streams_strict_json_usage_and_request_id() -> None:
    model, client = _model(
        [
            {"messageStart": {"role": "assistant"}},
            {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"text": '{"answer":"ok"}'},
                }
            },
            {"contentBlockStop": {"contentBlockIndex": 0}},
            {"messageStop": {"stopReason": "end_turn"}},
            {
                "metadata": {
                    "usage": {"inputTokens": 11, "outputTokens": 4},
                    "metrics": {"latencyMs": 25},
                }
            },
        ]
    )
    events = list(
        model.stream_turn(
            messages=[
                AgentChatMessage(role="system", content="system"),
                AgentChatMessage(role="user", content="status"),
            ],
            tools=[],
            tool_choice="none",
            max_output_tokens=200,
            response_format=_format(),
        )
    )
    assert model.provider == "bedrock"
    assert "".join(item.text or "" for item in events) == '{"answer":"ok"}'
    usage = next(item.usage for item in events if item.usage is not None)
    assert usage.input_tokens == 11
    completed = events[-1]
    assert completed.finish_reason == "end_turn"
    assert completed.provider_request_id == "bedrock-request-1"
    assert client.request is not None
    assert client.request["system"] == [{"text": "system"}]
    assert client.request["outputConfig"]["textFormat"]["type"] == "json_schema"
    assert "toolConfig" not in client.request


def test_bedrock_agent_maps_tools_and_assembles_fragmented_arguments() -> None:
    model, client = _model(
        [
            {
                "contentBlockStart": {
                    "contentBlockIndex": 0,
                    "start": {
                        "toolUse": {
                            "toolUseId": "call-2",
                            "name": "project_status_get_v1",
                        }
                    },
                }
            },
            {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"toolUse": {"input": "{"}},
                }
            },
            {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"toolUse": {"input": "}"}},
                }
            },
            {"contentBlockStop": {"contentBlockIndex": 0}},
            {"messageStop": {"stopReason": "tool_use"}},
        ]
    )
    events = list(
        model.stream_turn(
            messages=[AgentChatMessage(role="user", content="读取项目状态")],
            tools=[
                AgentToolSpec(
                    name="project_status_get_v1",
                    version="1",
                    description="Read project status.",
                    input_schema={"type": "object", "additionalProperties": False},
                )
            ],
            tool_choice="auto",
            max_output_tokens=200,
            response_format=_format(),
        )
    )
    tool_call = next(item.tool_call for item in events if item.tool_call is not None)
    assert tool_call.call_id == "call-2"
    assert tool_call.name == "project_status_get_v1"
    assert tool_call.arguments == {}
    assert client.request is not None
    tool_spec = client.request["toolConfig"]["tools"][0]["toolSpec"]
    assert tool_spec["strict"] is True
    assert tool_spec["inputSchema"]["json"]["type"] == "object"


def test_bedrock_agent_maps_prior_tool_call_and_result() -> None:
    model, client = _model([{"messageStop": {"stopReason": "end_turn"}}])
    list(
        model.stream_turn(
            messages=[
                AgentChatMessage(role="user", content="status"),
                AgentChatMessage(
                    role="assistant",
                    content="",
                    tool_calls=[
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "project_status_get_v1",
                                "arguments": "{}",
                            },
                        }
                    ],
                ),
                AgentChatMessage(
                    role="tool",
                    tool_call_id="call-1",
                    content='{"status":"ACTIVE"}',
                ),
            ],
            tools=[],
            tool_choice="none",
            max_output_tokens=200,
            response_format=_format(),
        )
    )
    assert client.request is not None
    messages = client.request["messages"]
    assert messages[1]["content"][0]["toolUse"]["toolUseId"] == "call-1"
    result = messages[2]["content"][0]["toolResult"]
    assert result["toolUseId"] == "call-1"
    assert result["content"] == [{"json": {"status": "ACTIVE"}}]


@pytest.mark.parametrize(
    "events",
    [
        [],
        [{"unknown": {}}],
        [{"validationException": {"message": "invalid"}}],
        [
            {
                "contentBlockStart": {
                    "contentBlockIndex": 0,
                    "start": {"toolUse": {"toolUseId": "x", "name": "tool"}},
                }
            },
            {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"toolUse": {"input": "{"}},
                }
            },
            {"messageStop": {"stopReason": "tool_use"}},
        ],
    ],
)
def test_bedrock_agent_normalizes_malformed_streams(
    events: list[dict[str, Any]],
) -> None:
    model, _ = _model(events)
    with pytest.raises(ServiceUnavailableError, match="Bedrock"):
        list(
            model.stream_turn(
                messages=[AgentChatMessage(role="user", content="status")],
                tools=[],
                tool_choice="none",
                max_output_tokens=200,
                response_format=_format(),
            )
        )


def test_bedrock_agent_normalizes_sdk_failure() -> None:
    class BrokenClient:
        def converse_stream(self, **request: Any) -> Iterator[dict[str, Any]]:
            del request
            raise TimeoutError("timeout")

    model = BedrockAgentChatModel(
        model_id="model",
        region="us-east-1",
        client=BrokenClient(),
    )
    with pytest.raises(ServiceUnavailableError, match="暂时不可用"):
        list(
            model.stream_turn(
                messages=[AgentChatMessage(role="user", content="status")],
                tools=[],
                tool_choice="none",
                max_output_tokens=200,
                response_format=_format(),
            )
        )


def test_bedrock_agent_rejects_prompt_only_json_mode() -> None:
    model, _ = _model([])
    with pytest.raises(ValueError, match="Structured Outputs"):
        list(
            model.stream_turn(
                messages=[AgentChatMessage(role="user", content="status")],
                tools=[],
                tool_choice="none",
                max_output_tokens=200,
            )
        )
