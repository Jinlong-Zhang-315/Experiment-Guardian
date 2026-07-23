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
    assert {tool for item in cases for tool in item["expected_tools"]}.issubset(allowed_tools)
    assert sum(bool(item["must_refuse"]) for item in cases) >= 8
    assert sum(bool(item["requires_citations"]) for item in cases) >= 10


def test_eval_catalog_tool_names_match_runtime_catalog_versions() -> None:
    # 不初始化数据库连接；specs 构建是纯契约操作。
    registry = AgentToolRegistry(None, None)  # type: ignore[arg-type]
    r15a_names = {item.name for item in registry.specs_for_version("r15a-v1")}
    assert r15a_names == {
        "project_status_get_v1",
        "experiments_list_v1",
        "experiment_get_v1",
        "pending_work_list_v1",
    }
    r15b_names = r15a_names | {
        "experiments_compare_v1",
        "experiment_group_stats_v1",
        "plan_check_explain_v1",
        "submission_diagnose_v1",
    }
    assert {
        item.name for item in registry.specs_for_version("r15b-v1")
    } == r15b_names
    r15c_names = r15b_names | {
        "policy_draft_create_v1",
        "policy_draft_update_v1",
        "policy_draft_validate_v1",
        "policy_draft_impact_get_v1",
    }
    assert {
        item.name for item in registry.specs_for_version("r15c-v1")
    } == r15c_names
    assert {item.name for item in registry.specs} == r15c_names | {
        "action_proposal_prepare_v1",
    }


def test_r15b_eval_catalog_covers_analysis_diagnosis_and_context_safety() -> None:
    path = Path(__file__).parents[1] / "agent_eval_cases" / "r15b_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert 45 <= len(cases) <= 60
    assert len({item["id"] for item in cases}) == len(cases)
    categories = {item["category"] for item in cases}
    assert {
        "comparison",
        "comparison_gate",
        "statistics",
        "plan_review",
        "diagnosis",
        "evidence_layers",
        "context_summary",
    } <= categories
    allowed_tools = {
        item.name
        for item in AgentToolRegistry(
            None,
            None,  # type: ignore[arg-type]
        ).specs_for_version("r15b-v1")
    }
    assert {tool for item in cases for tool in item["expected_tools"]}.issubset(allowed_tools)
    assert sum(bool(item["must_refuse"]) for item in cases) >= 12
    assert sum(bool(item["requires_citations"]) for item in cases) >= 25


def test_r15c_eval_catalog_covers_draft_lifecycle_and_write_boundaries() -> None:
    path = Path(__file__).parents[1] / "agent_eval_cases" / "r15c_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert 30 <= len(cases) <= 45
    assert len({item["id"] for item in cases}) == len(cases)
    categories = {item["category"] for item in cases}
    assert {
        "draft_create",
        "draft_update",
        "ambiguity",
        "validation",
        "impact",
        "stale",
        "authorization",
        "refusal",
        "prompt_injection",
    } <= categories
    allowed_tools = {
        item.name
        for item in AgentToolRegistry(
            None,
            None,  # type: ignore[arg-type]
        ).specs_for_version("r15c-v1")
    }
    assert {tool for item in cases for tool in item["expected_tools"]}.issubset(
        allowed_tools
    )
    assert sum(bool(item["must_refuse"]) for item in cases) >= 9
    assert sum(bool(item["requires_citations"]) for item in cases) >= 20


def test_r15d_eval_catalog_covers_proposal_confirmation_boundary() -> None:
    path = Path(__file__).parents[1] / "agent_eval_cases" / "r15d_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert 20 <= len(cases) <= 30
    assert len({item["id"] for item in cases}) == len(cases)
    assert {
        "proposal_prepare",
        "readiness_gate",
        "stale",
        "authorization",
        "confirmation",
        "idempotency",
        "prompt_injection",
    } <= {item["category"] for item in cases}
    allowed_tools = {
        item.name
        for item in AgentToolRegistry(
            None,
            None,  # type: ignore[arg-type]
        ).specs_for_version("r15d-v1")
    }
    assert {tool for item in cases for tool in item["expected_tools"]}.issubset(
        allowed_tools
    )
    assert sum(bool(item["must_refuse"]) for item in cases) >= 10
    assert sum(bool(item["requires_citations"]) for item in cases) >= 10
