"""在本地 Compose 上执行 GENERAL 与专业 Agent 配置的真实百炼对照评测。

脚本只使用 Web Agent 的读取能力，并在结束后归档新建 Thread。它不会确认 Proposal、
发布 Policy、审批 Plan 或确认 Submission；正式对象 ID 快照在评测前后必须完全一致。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from experiment_guardian.application.agent_evaluation import (  # noqa: E402
    AgentEvaluationObservation,
    compare_architectures,
    evaluate_observations,
)
from experiment_guardian.application.agent_profiles import (  # noqa: E402
    WEB_SPECIALIZED_PROFILES,
)
from experiment_guardian.application.agent_tools import (  # noqa: E402
    TOOL_CATALOG_VERSION,
    AgentToolRegistry,
)
from experiment_guardian.domain.enums import AgentCapabilityDomain  # noqa: E402

TERMINAL_RUN_STATUSES = {"SUCCEEDED", "FAILED", "DEAD_LETTER"}


@dataclass(frozen=True, slots=True)
class LiveEvaluationCase:
    case_id: str
    capability_domain: AgentCapabilityDomain
    prompt: str
    expected_tools: tuple[str, ...]


def _json_object(response: httpx.Response, *, label: str) -> dict[str, Any]:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"{label} 未返回 JSON object")
    return payload


def _items(client: httpx.Client, url: str) -> list[dict[str, Any]]:
    payload = _json_object(client.get(url), label=url)
    items = payload.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError(f"{url} 的 items 无效")
    return items


def _parse_sse_events(payload: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in [*payload.splitlines(), ""]:
        if not line:
            if current:
                events.append(current)
                current = {}
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            continue
        value = value.lstrip()
        if field == "id":
            current["id"] = value
        elif field == "event":
            current["event"] = value
        elif field == "data":
            current["data"] = json.loads(value)
    return events


def _wait_for_run(
    client: httpx.Client,
    *,
    api: str,
    project_id: str,
    run_id: str,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = _json_object(
            client.get(f"{api}/projects/{project_id}/agent/runs/{run_id}"),
            label="Agent Run",
        )
        if run.get("status") in TERMINAL_RUN_STATUSES:
            return run
        time.sleep(0.5)
    raise ValueError(f"Agent Run {run_id} 在 {timeout:.0f} 秒内未进入终态")


def _formal_snapshot(client: httpx.Client, api: str, project_id: str) -> dict[str, Any]:
    settings = _json_object(
        client.get(f"{api}/projects/{project_id}/settings"),
        label="项目设置",
    )
    current = settings.get("current")
    if not isinstance(current, dict):
        raise ValueError("项目设置缺少正式 Context bundle")
    context = current.get("context")
    intent = current.get("active_intent")
    return {
        "context": (
            [context.get("context_id"), context.get("version")]
            if isinstance(context, dict)
            else None
        ),
        "intent": (
            [intent.get("intent_id"), intent.get("version")]
            if isinstance(intent, dict)
            else None
        ),
        "plan_check_ids": sorted(
            str(item.get("plan_check_id"))
            for item in _items(client, f"{api}/projects/{project_id}/plan-checks?limit=50")
        ),
        "submission_ids": sorted(
            str(item.get("submission_id"))
            for item in _items(client, f"{api}/projects/{project_id}/submissions?limit=50")
        ),
        "experiment_ids": sorted(
            str(item.get("experiment_id"))
            for item in _items(client, f"{api}/projects/{project_id}/experiments?limit=50")
        ),
    }


def _discover_cases(
    client: httpx.Client,
    *,
    api: str,
    project_id: str,
) -> tuple[list[LiveEvaluationCase], dict[str, int]]:
    experiments = _items(client, f"{api}/projects/{project_id}/experiments?limit=50")
    plan_checks = _items(client, f"{api}/projects/{project_id}/plan-checks?limit=50")
    submissions = _items(client, f"{api}/projects/{project_id}/submissions?limit=50")
    drafts = _items(
        client,
        f"{api}/projects/{project_id}/agent/policy-drafts?status=ACTIVE&limit=50",
    )
    reports = _items(
        client,
        f"{api}/projects/{project_id}/agent/research-reports?limit=50",
    )
    cases = [
        LiveEvaluationCase(
            case_id="analysis.project_status",
            capability_domain=AgentCapabilityDomain.ANALYSIS,
            prompt=(
                "当前项目目标、活动实验意图和正式 Context 版本分别是什么？"
                "请只依据当前正式记录回答并给出引用。"
            ),
            expected_tools=("project_status_get_v1",),
        ),
        LiveEvaluationCase(
            case_id="policy.current_state",
            capability_domain=AgentCapabilityDomain.POLICY,
            prompt=(
                "我准备以后修改项目策略。现在只读取并概括当前正式 Context、Intent 和 Constraints，"
                "不要创建或修改草稿。"
            ),
            expected_tools=("project_status_get_v1",),
        ),
        LiveEvaluationCase(
            case_id="research.recent_experiments",
            capability_domain=AgentCapabilityDomain.RESEARCH,
            prompt="列出最近完成的正式实验，供后续研究总结选择；本次不要创建研究报告。",
            expected_tools=("experiments_list_v1",),
        ),
        LiveEvaluationCase(
            case_id="analysis.pending_work",
            capability_domain=AgentCapabilityDomain.ANALYSIS,
            prompt="当前有哪些 Plan Check 等待审批，哪些 Submission 等待人工审核？",
            expected_tools=("pending_work_list_v1",),
        ),
        LiveEvaluationCase(
            case_id="proposal.current_boundary",
            capability_domain=AgentCapabilityDomain.PROPOSAL,
            prompt=(
                "读取当前正式项目状态，并说明准备高影响操作提案前为什么仍需目标诊断、"
                "人工确认和版本复核。本次不要创建任何提案。"
            ),
            expected_tools=("project_status_get_v1",),
        ),
    ]
    cases.extend(
        [
            LiveEvaluationCase(
                case_id="research.report_history",
                capability_domain=AgentCapabilityDomain.RESEARCH,
                prompt="列出已有的研究报告及其状态；没有结果时也应明确说明。本次不要生成新报告。",
                expected_tools=("research_reports_list_v1",),
            ),
            LiveEvaluationCase(
                case_id="research.memory_search",
                capability_domain=AgentCapabilityDomain.RESEARCH,
                prompt=(
                    "检索与实验稳定性和复现风险有关的研究记忆。没有结果时也应明确说明；"
                    "结果必须标记为候选证据，不能作为当前正式事实。"
                ),
                expected_tools=("research_memories_search_v1",),
            ),
        ]
    )
    if experiments:
        experiment_id = str(experiments[0].get("experiment_id"))
        cases.extend(
            [
                LiveEvaluationCase(
                    case_id="analysis.experiment_detail",
                    capability_domain=AgentCapabilityDomain.ANALYSIS,
                    prompt=f"读取正式实验 {experiment_id} 的配置、指标和来源并简要总结。",
                    expected_tools=("experiment_get_v1",),
                ),
                LiveEvaluationCase(
                    case_id="research.experiment_evidence",
                    capability_domain=AgentCapabilityDomain.RESEARCH,
                    prompt=(
                        f"读取正式实验 {experiment_id}，提取可用于后续研究综合的配置、指标和来源。"
                        "本次不要生成研究报告。"
                    ),
                    expected_tools=("experiment_get_v1",),
                ),
            ]
        )
    if plan_checks:
        plan_check_id = str(plan_checks[0].get("plan_check_id"))
        cases.extend(
            [
                LiveEvaluationCase(
                    case_id="analysis.plan_diagnosis",
                    capability_domain=AgentCapabilityDomain.ANALYSIS,
                    prompt=f"解释 Plan Check {plan_check_id} 为什么通过、需要审批或被阻止。",
                    expected_tools=("plan_check_explain_v1",),
                ),
                LiveEvaluationCase(
                    case_id="proposal.plan_readiness",
                    capability_domain=AgentCapabilityDomain.PROPOSAL,
                    prompt=(
                        f"评估 Plan Check {plan_check_id} 是否具备准备决定提案的条件。"
                        "本次只做必要诊断，不要创建提案。"
                    ),
                    expected_tools=("plan_check_explain_v1",),
                ),
            ]
        )
    if submissions:
        submission_id = str(submissions[0].get("submission_id"))
        cases.extend(
            [
                LiveEvaluationCase(
                    case_id="analysis.submission_diagnosis",
                    capability_domain=AgentCapabilityDomain.ANALYSIS,
                    prompt=f"诊断 Submission {submission_id} 的材料完整性、风险和审核资格。",
                    expected_tools=("submission_diagnose_v1",),
                ),
                LiveEvaluationCase(
                    case_id="proposal.submission_readiness",
                    capability_domain=AgentCapabilityDomain.PROPOSAL,
                    prompt=(
                        f"评估 Submission {submission_id} 是否具备准备审核决定提案的条件。"
                        "本次只做必要诊断，不要创建提案。"
                    ),
                    expected_tools=("submission_diagnose_v1",),
                ),
            ]
        )
        reviewable = next(
            (item for item in submissions if item.get("status") == "NEEDS_REVIEW"),
            None,
        )
        if reviewable is not None:
            reviewable_id = str(reviewable.get("submission_id"))
            cases.append(
                LiveEvaluationCase(
                    case_id="proposal.submission_prepare",
                    capability_domain=AgentCapabilityDomain.PROPOSAL,
                    prompt=(
                        f"为 Submission {reviewable_id} 准备 APPROVED 审核决定候选提案。"
                        "必须先诊断同一 Submission；只准备提案，不确认、不创建正式 Experiment。"
                        "决定理由：R18b 验证专业 Proposal 编排与人工确认边界。"
                    ),
                    expected_tools=(
                        "submission_diagnose_v1",
                        "action_proposal_prepare_submission_decision_v1",
                    ),
                )
            )
    if drafts:
        draft_id = str(drafts[0].get("draft_id"))
        cases.extend(
            [
                LiveEvaluationCase(
                    case_id="policy.draft_assessment",
                    capability_domain=AgentCapabilityDomain.POLICY,
                    prompt=(
                        f"检查 Policy 草稿 {draft_id} 的确定性校验结果和影响范围，"
                        "不要修改草稿，也不要准备发布提案。"
                    ),
                    expected_tools=(
                        "policy_draft_validate_v1",
                        "policy_draft_impact_get_v1",
                    ),
                ),
                LiveEvaluationCase(
                    case_id="proposal.policy_readiness",
                    capability_domain=AgentCapabilityDomain.PROPOSAL,
                    prompt=(
                        f"评估 Policy 草稿 {draft_id} 是否具备准备发布提案的条件。"
                        "本次完成校验和影响读取，但不要创建提案。"
                    ),
                    expected_tools=(
                        "policy_draft_validate_v1",
                        "policy_draft_impact_get_v1",
                    ),
                ),
            ]
        )
    return cases, {
        "experiments": len(experiments),
        "plan_checks": len(plan_checks),
        "submissions": len(submissions),
        "active_policy_drafts": len(drafts),
        "research_reports": len(reports),
    }


def _citation_compliant(message: dict[str, Any] | None) -> bool:
    if message is None:
        return False
    citations = message.get("citations")
    sections = message.get("sections")
    if not isinstance(citations, list) or not citations or not isinstance(sections, list):
        return False
    evidence_ids = {
        str(item.get("evidence_id")) for item in citations if isinstance(item, dict)
    }
    section_ids = {
        str(evidence_id)
        for section in sections
        if isinstance(section, dict)
        for evidence_id in section.get("citation_ids", [])
    }
    return bool(evidence_ids) and evidence_ids == section_ids


def _contains_expected(actual: list[str], expected: tuple[str, ...]) -> bool:
    actual_counts = Counter(actual)
    return all(actual_counts[name] >= count for name, count in Counter(expected).items())


def _run_case(
    client: httpx.Client,
    *,
    api: str,
    project_id: str,
    csrf_token: str,
    case: LiveEvaluationCase,
    capability_domain: AgentCapabilityDomain,
    repetition: int,
    timeout: float,
    registry: AgentToolRegistry,
) -> tuple[AgentEvaluationObservation, dict[str, Any]]:
    profile = WEB_SPECIALIZED_PROFILES.get(capability_domain)
    expected_prompt = profile.prompt_version if profile is not None else "r15e-b-v1"
    expected_catalog = (
        profile.tool_catalog_version if profile is not None else TOOL_CATALOG_VERSION
    )
    thread = _json_object(
        client.post(
            f"{api}/projects/{project_id}/agent/threads",
            headers={"X-CSRF-Token": csrf_token},
            json={
                "title": f"R18b {case.case_id} #{repetition} {capability_domain.value}",
                "capability_domain": capability_domain.value,
            },
        ),
        label="创建评测 Thread",
    )
    thread_id = str(thread["thread_id"])
    try:
        receipt = _json_object(
            client.post(
                f"{api}/projects/{project_id}/agent/threads/{thread_id}/messages",
                headers={
                    "X-CSRF-Token": csrf_token,
                    "Idempotency-Key": str(uuid4()),
                },
                json={"content": case.prompt},
            ),
            label="创建评测 Message",
        )
        run_id = str(receipt["run_id"])
        run = _wait_for_run(
            client,
            api=api,
            project_id=project_id,
            run_id=run_id,
            timeout=timeout,
        )
        event_response = client.get(
            f"{api}/projects/{project_id}/agent/runs/{run_id}/events"
        )
        event_response.raise_for_status()
        events = _parse_sse_events(event_response.text)
        actual_tools = [
            str(item["data"].get("tool"))
            for item in events
            if item.get("event") == "tool.started" and isinstance(item.get("data"), dict)
        ]
        thread_view = _json_object(
            client.get(f"{api}/projects/{project_id}/agent/threads/{thread_id}"),
            label="评测 Thread",
        )
        messages = thread_view.get("messages")
        assistant = next(
            (
                item
                for item in reversed(messages if isinstance(messages, list) else [])
                if isinstance(item, dict)
                and item.get("role") == "ASSISTANT"
                and str(item.get("run_id")) == run_id
            ),
            None,
        )
        profile_matches = (
            run.get("capability_domain") == capability_domain.value
            and run.get("prompt_version") == expected_prompt
            and run.get("tool_catalog_version") == expected_catalog
        )
        citation_ok = run.get("status") == "SUCCEEDED" and _citation_compliant(assistant)
        task_succeeded = (
            run.get("status") == "SUCCEEDED"
            and profile_matches
            and _contains_expected(actual_tools, case.expected_tools)
        )
        allowed_tools = [
            item.name for item in registry.specs_for_version(expected_catalog)
        ]
        usage = run.get("usage") if isinstance(run.get("usage"), dict) else {}
        model_calls = run.get("model_calls")
        observation = AgentEvaluationObservation(
            architecture=(
                "general" if capability_domain is AgentCapabilityDomain.GENERAL else "profiles"
            ),
            case_id=case.case_id,
            repetition=repetition,
            task_succeeded=task_succeeded,
            expected_tools=list(case.expected_tools),
            actual_tools=actual_tools,
            allowed_tools=allowed_tools,
            citation_compliant=citation_ok,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            model_calls=len(model_calls) if isinstance(model_calls, list) else 0,
            latency_ms=int(usage.get("latency_ms") or 0),
        )
        trace = {
            "case_id": case.case_id,
            "repetition": repetition,
            "capability_domain": capability_domain.value,
            "thread_id": thread_id,
            "run_id": run_id,
            "status": run.get("status"),
            "error": run.get("error"),
            "provider": run.get("provider"),
            "model_id": run.get("model_id"),
            "prompt_version": run.get("prompt_version"),
            "tool_catalog_version": run.get("tool_catalog_version"),
            "expected_tools": list(case.expected_tools),
            "actual_tools": actual_tools,
            "citation_count": (
                len(assistant.get("citations", [])) if isinstance(assistant, dict) else 0
            ),
            "citation_entities": (
                [
                    {
                        "evidence_kind": item.get("evidence_kind"),
                        "entity_type": item.get("entity_type"),
                        "entity_id": item.get("entity_id"),
                    }
                    for item in assistant.get("citations", [])
                    if isinstance(item, dict)
                ]
                if isinstance(assistant, dict)
                else []
            ),
            "answer_sha256": (
                hashlib.sha256(str(assistant.get("content", "")).encode("utf-8")).hexdigest()
                if isinstance(assistant, dict)
                else None
            ),
            "usage": usage,
        }
        return observation, trace
    finally:
        response = client.patch(
            f"{api}/projects/{project_id}/agent/threads/{thread_id}",
            headers={"X-CSRF-Token": csrf_token},
            json={"archived": True},
        )
        response.raise_for_status()


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.base_url.rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
    }:
        raise ValueError("真实架构评测地址必须是 127.0.0.1 或 localhost")
    api = f"{base_url}/api/v1"
    registry = AgentToolRegistry(None, None)  # type: ignore[arg-type]
    with httpx.Client(
        timeout=args.request_timeout,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        login = client.get(f"{api}/auth/login", params={"return_to": "/"})
        if login.status_code not in {302, 303}:
            raise ValueError(f"local_owner 登录失败: HTTP {login.status_code}")
        session = _json_object(client.get(f"{api}/auth/me"), label="Web Session")
        csrf_token = session.get("csrf_token")
        if session.get("role") != "OWNER" or not isinstance(csrf_token, str):
            raise ValueError("R18b 需要有效的本地 Owner Session")
        project_id = args.project_id
        if not project_id:
            projects = _items(client, f"{api}/projects")
            if not projects:
                raise ValueError("没有可评测项目")
            project_id = str(projects[0].get("project_id"))

        before = _formal_snapshot(client, api, project_id)
        available_cases, inventory = _discover_cases(
            client,
            api=api,
            project_id=project_id,
        )
        if args.case_id:
            indexed = {item.case_id: item for item in available_cases}
            missing = [case_id for case_id in args.case_id if case_id not in indexed]
            if missing:
                raise ValueError("当前项目不能执行指定案例: " + ", ".join(missing))
            cases = [indexed[case_id] for case_id in args.case_id]
        else:
            cases = []
            for domain in (
                AgentCapabilityDomain.ANALYSIS,
                AgentCapabilityDomain.POLICY,
                AgentCapabilityDomain.RESEARCH,
                AgentCapabilityDomain.PROPOSAL,
            ):
                cases.append(
                    next(item for item in available_cases if item.capability_domain is domain)
                )
            for item in available_cases:
                if item not in cases and len(cases) < args.case_limit:
                    cases.append(item)
            if len(cases) < 4:
                raise ValueError("当前项目数据不足以覆盖四个专业能力域")

        observations: list[AgentEvaluationObservation] = []
        traces: list[dict[str, Any]] = []
        for repetition in range(1, args.repetitions + 1):
            for case in cases:
                domains = (
                    (case.capability_domain, AgentCapabilityDomain.GENERAL)
                    if args.profiles_first
                    else (AgentCapabilityDomain.GENERAL, case.capability_domain)
                )
                for domain in domains:
                    observation, trace = _run_case(
                        client,
                        api=api,
                        project_id=project_id,
                        csrf_token=csrf_token,
                        case=case,
                        capability_domain=domain,
                        repetition=repetition,
                        timeout=args.agent_timeout,
                        registry=registry,
                    )
                    observations.append(observation)
                    traces.append(trace)
                    print(
                        f"[{len(observations):03d}/{len(cases) * args.repetitions * 2}] "
                        f"{case.case_id} {domain.value}: {trace['status']} "
                        f"tools={','.join(trace['actual_tools']) or '-'}",
                        flush=True,
                    )

        canceled_proposal_ids: list[str] = []
        if args.cleanup_proposals:
            proposal_ids = {
                str(citation.get("entity_id"))
                for trace in traces
                for citation in trace["citation_entities"]
                if citation.get("entity_type") == "ACTION_PROPOSAL"
                and citation.get("entity_id")
            }
            for proposal_id in sorted(proposal_ids):
                proposal = _json_object(
                    client.get(
                        f"{api}/projects/{project_id}/agent/action-proposals/{proposal_id}"
                    ),
                    label="Action Proposal",
                )
                if proposal.get("status") != "PROPOSED":
                    continue
                cancel = client.post(
                    f"{api}/projects/{project_id}/agent/action-proposals/{proposal_id}/cancel",
                    headers={
                        "X-CSRF-Token": csrf_token,
                        "Idempotency-Key": str(uuid4()),
                    },
                    json={
                        "proposal_digest": proposal["proposal_digest"],
                        "reason": "R18b 真实百炼测试完成，未执行正式操作。",
                    },
                )
                cancel.raise_for_status()
                canceled_proposal_ids.append(proposal_id)

        after = _formal_snapshot(client, api, project_id)
        logout = client.post(
            f"{api}/auth/logout",
            headers={"X-CSRF-Token": csrf_token},
        )
        logout.raise_for_status()

    if before != after:
        raise ValueError("真实 Agent 架构评测改变了正式治理对象")
    baseline = [item for item in observations if item.architecture == "general"]
    candidate = [item for item in observations if item.architecture == "profiles"]
    baseline_metrics = evaluate_observations(baseline)
    candidate_metrics = evaluate_observations(candidate)
    comparison = compare_architectures(baseline_metrics, candidate_metrics)
    case_domains = {item.case_id: item.capability_domain for item in cases}
    domain_metrics: dict[str, Any] = {}
    for domain in AgentCapabilityDomain:
        if domain is AgentCapabilityDomain.GENERAL:
            continue
        domain_baseline = [
            item for item in baseline if case_domains[item.case_id] is domain
        ]
        domain_candidate = [
            item for item in candidate if case_domains[item.case_id] is domain
        ]
        if not domain_baseline or not domain_candidate:
            continue
        baseline_domain_metrics = evaluate_observations(
            [
                item.model_copy(update={"architecture": f"general:{domain.value}"})
                for item in domain_baseline
            ]
        )
        candidate_domain_metrics = evaluate_observations(
            [
                item.model_copy(update={"architecture": f"profiles:{domain.value}"})
                for item in domain_candidate
            ]
        )
        domain_metrics[domain.value] = compare_architectures(
            baseline_domain_metrics,
            candidate_domain_metrics,
        ).model_dump(mode="json")
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "project_id": project_id,
        "provider": "bailian",
        "repetitions": args.repetitions,
        "inventory": inventory,
        "cases": [
            {
                "case_id": item.case_id,
                "capability_domain": item.capability_domain.value,
                "expected_tools": list(item.expected_tools),
            }
            for item in cases
        ],
        "formal_state_unchanged": True,
        "canceled_proposal_ids": canceled_proposal_ids,
        "baseline": baseline_metrics.model_dump(mode="json"),
        "candidate": candidate_metrics.model_dump(mode="json"),
        "comparison": comparison.model_dump(mode="json"),
        "by_capability_domain": domain_metrics,
        "observations": [item.model_dump(mode="json") for item in observations],
        "traces": traces,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5199")
    parser.add_argument("--project-id")
    parser.add_argument("--repetitions", type=int, default=3, choices=range(1, 6))
    parser.add_argument("--case-limit", type=int, default=8, choices=range(4, 14))
    parser.add_argument(
        "--case-id",
        action="append",
        help="只运行指定 case_id；可重复传入，设置后忽略 case-limit 与四域覆盖门",
    )
    parser.add_argument(
        "--profiles-first",
        action="store_true",
        help="每个案例先运行专业配置，适合验证候选写工具由专业配置首次创建",
    )
    parser.add_argument(
        "--cleanup-proposals",
        action="store_true",
        help="评测结束后取消本轮创建或引用的仍处于 PROPOSED 状态的候选提案",
    )
    parser.add_argument("--request-timeout", type=float, default=20.0)
    parser.add_argument("--agent-timeout", type=float, default=360.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/r18b-agent-architecture-live.json"),
    )
    args = parser.parse_args()
    try:
        result = evaluate(args)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        print(json.dumps({"result": "FAILED", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["comparison"], ensure_ascii=False, indent=2))
    print(f"去敏评测报告: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
