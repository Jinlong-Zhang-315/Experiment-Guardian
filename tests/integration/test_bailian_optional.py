"""显式开启后才访问真实百炼 API 的最小验收。"""

import os

import pytest

from experiment_guardian.core.config import Settings
from experiment_guardian.infrastructure.bailian import (
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
