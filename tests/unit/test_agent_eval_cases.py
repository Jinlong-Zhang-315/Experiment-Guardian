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
    assert {item.name for item in registry.specs_for_version("r15b-v1")} == r15b_names
    r15c_names = r15b_names | {
        "policy_draft_create_v1",
        "policy_draft_update_v1",
        "policy_draft_validate_v1",
        "policy_draft_impact_get_v1",
    }
    assert {item.name for item in registry.specs_for_version("r15c-v1")} == r15c_names
    r15d_names = r15c_names | {
        "action_proposal_prepare_v1",
    }
    assert {item.name for item in registry.specs_for_version("r15d-v1")} == r15d_names
    r15d_b2_names = r15d_names | {
        "action_proposal_prepare_plan_decision_v1",
        "action_proposal_prepare_submission_decision_v1",
    }
    assert {
        item.name for item in registry.specs_for_version("r15d-b2-v1")
    } == r15d_b2_names
    r15e_a_names = r15d_b2_names | {
        "research_report_prepare_v1",
        "research_reports_list_v1",
        "research_report_get_v1",
    }
    assert {
        item.name for item in registry.specs_for_version("r15e-a-v1")
    } == r15e_a_names
    assert {item.name for item in registry.specs} == r15e_a_names | {
        "research_memories_search_v1",
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
    assert {tool for item in cases for tool in item["expected_tools"]}.issubset(allowed_tools)
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
    assert {tool for item in cases for tool in item["expected_tools"]}.issubset(allowed_tools)
    assert sum(bool(item["must_refuse"]) for item in cases) >= 10
    assert sum(bool(item["requires_citations"]) for item in cases) >= 10


def test_r15d_b1_eval_catalog_covers_plan_decision_boundary() -> None:
    path = Path(__file__).parents[1] / "agent_eval_cases" / "r15d_b1_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert 18 <= len(cases) <= 30
    assert len({item["id"] for item in cases}) == len(cases)
    assert {
        "plan_proposal",
        "advice_only",
        "state_gate",
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
        ).specs_for_version("r15d-b1-v1")
    }
    assert {tool for item in cases for tool in item["expected_tools"]}.issubset(allowed_tools)
    assert sum(bool(item["must_refuse"]) for item in cases) >= 10
    assert sum(bool(item["requires_citations"]) for item in cases) >= 8


def test_r15d_b2_eval_catalog_covers_submission_decision_boundary() -> None:
    path = Path(__file__).parents[1] / "agent_eval_cases" / "r15d_b2_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert 20 <= len(cases) <= 30
    assert len({item["id"] for item in cases}) == len(cases)
    assert {
        "submission_proposal",
        "advice_only",
        "risk_gate",
        "authorization",
        "confirmation",
        "stale",
        "prompt_injection",
    } <= {item["category"] for item in cases}
    allowed_tools = {
        item.name
        for item in AgentToolRegistry(
            None,
            None,  # type: ignore[arg-type]
        ).specs_for_version("r15d-b2-v1")
    }
    assert {tool for item in cases for tool in item["expected_tools"]}.issubset(allowed_tools)
    assert sum(bool(item["must_refuse"]) for item in cases) >= 10
    assert sum(bool(item["requires_citations"]) for item in cases) >= 8


def test_r15e_a_eval_catalog_covers_research_report_boundaries() -> None:
    path = Path(__file__).parents[1] / "agent_eval_cases" / "r15e_a_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert 20 <= len(cases) <= 30
    assert len({item["id"] for item in cases}) == len(cases)
    assert {
        "report_prepare",
        "clarification",
        "mixed_status",
        "historical",
        "comparability",
        "statistics",
        "citation_integrity",
        "report_read",
        "formal_boundary",
        "prompt_injection",
        "authorization",
    } <= {item["category"] for item in cases}
    allowed_tools = {
        item.name
        for item in AgentToolRegistry(
            None,
            None,  # type: ignore[arg-type]
        ).specs_for_version("r15e-a-v1")
    }
    assert {tool for item in cases for tool in item["expected_tools"]}.issubset(
        allowed_tools
    )
    assert sum(bool(item["must_refuse"]) for item in cases) >= 10
    assert sum(bool(item["requires_citations"]) for item in cases) >= 12


def test_r15e_b_eval_catalog_covers_candidate_memory_boundaries() -> None:
    path = Path(__file__).parents[1] / "agent_eval_cases" / "r15e_b_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert 18 <= len(cases) <= 30
    assert len({item["id"] for item in cases}) == len(cases)
    assert {
        "memory_search",
        "structured_filter",
        "stale",
        "candidate_boundary",
        "embedding_failure",
        "authorization",
        "prompt_injection",
    } <= {item["category"] for item in cases}
    allowed_tools = {
        item.name
        for item in AgentToolRegistry(
            None,
            None,  # type: ignore[arg-type]
        ).specs_for_version("r15e-b-v1")
    }
    assert {tool for item in cases for tool in item["expected_tools"]}.issubset(
        allowed_tools
    )
    assert sum(bool(item["must_refuse"]) for item in cases) >= 8
    assert sum(bool(item["requires_citations"]) for item in cases) >= 10


def test_r15e_c_provider_catalog_is_shared_by_bailian_and_bedrock() -> None:
    path = Path(__file__).parents[1] / "agent_eval_cases" / "r15e_c_provider_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert 12 <= len(cases) <= 24
    assert len({item["id"] for item in cases}) == len(cases)
    assert {
        "structured_output",
        "tool_call",
        "multi_turn",
        "refusal",
        "citation_integrity",
        "malformed_response",
        "provider_failure",
    } <= {item["category"] for item in cases}
    assert all(item["providers"] == ["bailian", "bedrock"] for item in cases)
    allowed_tools = {
        item.name
        for item in AgentToolRegistry(
            None,
            None,  # type: ignore[arg-type]
        ).specs_for_version("r15e-b-v1")
    }
    allowed_tools.add("research_memories_search_v1")
    assert {tool for item in cases for tool in item["expected_tools"]}.issubset(
        allowed_tools
    )
    assert sum(bool(item["must_refuse"]) for item in cases) >= 8
    assert sum(bool(item["requires_citations"]) for item in cases) >= 8
