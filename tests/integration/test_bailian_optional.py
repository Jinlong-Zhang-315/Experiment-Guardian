"""显式开启后才访问真实百炼 API 的最小验收。"""

import os

import pytest

from experiment_guardian.core.config import Settings
from experiment_guardian.domain.agent import AgentChatMessage, AgentToolSpec
from experiment_guardian.infrastructure.bailian import (
    BailianAgentChatModel,
    BailianEmbeddingGenerator,
    BailianSummaryGenerator,
)


def _local_settings() -> Settings:
    """环境变量优先，本地验收默认补充读取不入库的 .env.local。"""

    return Settings(_env_file=".env.local")  # type: ignore[call-arg]


@pytest.mark.skipif(
    os.getenv("RUN_BAILIAN_INTEGRATION") != "1",
    reason="设置 RUN_BAILIAN_INTEGRATION=1 后才访问真实百炼",
)
def test_real_bailian_embedding_has_fixed_dimension() -> None:
    settings = _local_settings()
    api_key = settings.bailian_api_key
    generator = BailianEmbeddingGenerator(
        api_key=api_key.get_secret_value() if api_key else "",
        base_url=settings.bailian_base_url,
        model_id=settings.bailian_embedding_model,
        dimension=settings.bailian_embedding_dimension,
    )
    output = generator.embed("Experiment Guardian integration verification")
    assert len(output.vector) == 1024


@pytest.mark.skipif(
    os.getenv("RUN_BAILIAN_INTEGRATION") != "1",
    reason="设置 RUN_BAILIAN_INTEGRATION=1 后才访问真实百炼",
)
def test_real_bailian_summary_returns_plain_text_without_tools() -> None:
    settings = _local_settings()
    api_key = settings.bailian_api_key
    generator = BailianSummaryGenerator(
        api_key=api_key.get_secret_value() if api_key else "",
        base_url=settings.bailian_base_url,
        model_id=settings.bailian_summary_model,
        connect_timeout_seconds=settings.bailian_connect_timeout_seconds,
        read_timeout_seconds=settings.bailian_read_timeout_seconds,
    )
    output = generator.generate(
        system_prompt="Return one factual sentence. Do not call tools.",
        user_prompt="Objective: verify the configured Bailian summary model is callable.",
    )
    assert output.text.strip()


@pytest.mark.skipif(
    os.getenv("RUN_BAILIAN_AGENT_INTEGRATION") != "1",
    reason="设置 RUN_BAILIAN_AGENT_INTEGRATION=1 后才访问真实百炼 Agent 模型",
)
def test_real_bailian_agent_supports_function_calling() -> None:
    """显式验收所选 Agent 模型支持流式 OpenAI-compatible Function Calling。"""

    settings = _local_settings()
    api_key = settings.bailian_api_key
    model = BailianAgentChatModel(
        api_key=api_key.get_secret_value() if api_key else "",
        base_url=settings.bailian_base_url,
        model_id=settings.bailian_agent_model,
        connect_timeout_seconds=settings.bailian_connect_timeout_seconds,
        read_timeout_seconds=settings.bailian_read_timeout_seconds,
    )
    events = list(
        model.stream_turn(
            messages=[
                AgentChatMessage(
                    role="system",
                    content=(
                        "You are a protocol integration verifier. Call the provided "
                        "project_status_get_v1 tool exactly once and do not answer directly."
                    ),
                ),
                AgentChatMessage(
                    role="user",
                    content="Read the current project status.",
                ),
            ],
            tools=[
                AgentToolSpec(
                    name="project_status_get_v1",
                    version="1",
                    description="Read the current project status.",
                    input_schema={
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                )
            ],
            tool_choice="auto",
            max_output_tokens=256,
        )
    )
    tool_calls = [
        event.tool_call for event in events if event.event_type == "tool_call"
    ]
    assert len(tool_calls) == 1
    assert tool_calls[0] is not None
    assert tool_calls[0].name == "project_status_get_v1"
    assert tool_calls[0].arguments == {}
