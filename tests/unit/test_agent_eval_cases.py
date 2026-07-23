"""R15a 轨迹评测清单的覆盖范围保护。"""

import json
from pathlib import Path

from experiment_guardian.application.agent_tools import AgentToolRegistry


def test_r15a_agent_eval_catalog_has_required_trajectory_coverage() -> None:
    path = Path(__file__).parents[1] / "agent_eval_cases" / "r15a_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert 20 <= len(cases) <= 30
    assert len({item["id"] for item in cases}) == len(cases)
    categories = {item["category"] for item in cases}
    assert {
        "project_status",
        "experiments",
        "pending",
        "clarification",
        "refusal",
        "authorization",
        "prompt_injection",
        "scope_limit",
    }.issubset(categories)
    allowed_tools = {
        "project_status_get_v1",
        "experiments_list_v1",
        "experiment_get_v1",
        "pending_work_list_v1",
    }
    assert {
        tool for item in cases for tool in item["expected_tools"]
    }.issubset(allowed_tools)
    assert sum(bool(item["must_refuse"]) for item in cases) >= 8
    assert sum(bool(item["requires_citations"]) for item in cases) >= 10


def test_eval_catalog_tool_names_match_runtime_catalog() -> None:
    # 不初始化数据库连接；specs 构建是纯契约操作。
    registry = AgentToolRegistry(None, None)  # type: ignore[arg-type]
    runtime_names = {item.name for item in registry.specs}
    assert runtime_names == {
        "project_status_get_v1",
        "experiments_list_v1",
        "experiment_get_v1",
        "pending_work_list_v1",
    }
