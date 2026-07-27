"""显式开启后验收真实本地 Compose -> 百炼治理 Agent 闭环。"""

import os
from argparse import Namespace

import pytest

from scripts.verify_r16_local import verify


@pytest.mark.skipif(
    os.getenv("RUN_BAILIAN_AGENT_INTEGRATION") != "1",
    reason="设置 RUN_BAILIAN_AGENT_INTEGRATION=1 后才访问真实百炼 Agent 模型",
)
def test_real_local_bailian_agent_run_is_cited_audited_and_read_only() -> None:
    result = verify(
        Namespace(
            base_url=os.getenv("R16_LOCAL_BASE_URL", "http://127.0.0.1:5199"),
            env_file=os.getenv("R16_LOCAL_ENV_FILE", ".env.local"),
            live_bailian=True,
            report=None,
            timeout=15.0,
            agent_timeout=180.0,
        )
    )
    assert result["result"] == "PASS"
    live = result["checks"]["live_bailian_agent"]
    assert live["status"] == "PASS"
    assert live["provider"] == "bailian"
    assert live["tool_names"] == ["project_status_get_v1"]
    assert live["citation_count"] >= 1
