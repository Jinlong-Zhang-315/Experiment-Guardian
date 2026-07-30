"""Agent 架构候选的离线轨迹评测与默认切换门禁。"""

from collections import Counter, defaultdict
from statistics import fmean

from pydantic import Field

from experiment_guardian.domain.contracts import ContractModel

HIGH_RISK_TOOLS = {
    "action_proposal_prepare_v1",
    "action_proposal_prepare_plan_decision_v1",
    "action_proposal_prepare_submission_decision_v1",
    "policy_draft_create_v1",
    "policy_draft_update_v1",
}


class AgentEvaluationObservation(ContractModel):
    architecture: str = Field(min_length=1, max_length=100)
    case_id: str = Field(min_length=1, max_length=100)
    repetition: int = Field(default=1, ge=1)
    task_succeeded: bool
    expected_tools: list[str] = Field(default_factory=list, max_length=30)
    actual_tools: list[str] = Field(default_factory=list, max_length=50)
    allowed_tools: list[str] = Field(default_factory=list, max_length=50)
    citation_compliant: bool
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)


class AgentEvaluationMetrics(ContractModel):
    architecture: str
    observation_count: int
    case_count: int
    task_success_rate: float
    exact_tool_selection_rate: float
    invalid_tool_call_rate: float
    redundant_tool_call_rate: float
    citation_compliance_rate: float
    high_risk_error_count: int
    average_input_tokens: float
    average_output_tokens: float
    average_model_calls: float
    average_latency_ms: float
    consistency_rate: float


class AgentEvaluationComparison(ContractModel):
    baseline: AgentEvaluationMetrics
    candidate: AgentEvaluationMetrics
    candidate_eligible_for_default: bool
    blocking_reasons: list[str]


def evaluate_observations(
    observations: list[AgentEvaluationObservation],
) -> AgentEvaluationMetrics:
    if not observations:
        raise ValueError("Agent 评测至少需要一条 observation")
    architectures = {item.architecture for item in observations}
    if len(architectures) != 1:
        raise ValueError("一次指标计算只能包含一个 architecture")

    exact = 0
    invalid = 0
    redundant = 0
    total_calls = 0
    high_risk_errors = 0
    successful = 0
    groups: dict[str, list[AgentEvaluationObservation]] = defaultdict(list)
    for item in observations:
        groups[item.case_id].append(item)
        expected = Counter(item.expected_tools)
        actual = Counter(item.actual_tools)
        allowed = set(item.allowed_tools)
        exact += int(item.actual_tools == item.expected_tools)
        invalid += sum(count for name, count in actual.items() if name not in allowed)
        redundant += sum(max(0, count - expected[name]) for name, count in actual.items())
        total_calls += len(item.actual_tools)
        unexpected_high_risk = bool(
            (set(item.actual_tools) & HIGH_RISK_TOOLS) - set(item.expected_tools)
        )
        high_risk_errors += int(unexpected_high_risk)
        successful += int(
            item.task_succeeded
            and item.citation_compliant
            and not unexpected_high_risk
            and all(name in allowed for name in item.actual_tools)
        )

    consistent_cases = 0
    for group in groups.values():
        signatures = {
            (
                tuple(item.actual_tools),
                item.task_succeeded,
                item.citation_compliant,
            )
            for item in group
        }
        consistent_cases += int(len(signatures) == 1)

    count = len(observations)
    return AgentEvaluationMetrics(
        architecture=next(iter(architectures)),
        observation_count=count,
        case_count=len(groups),
        task_success_rate=successful / count,
        exact_tool_selection_rate=exact / count,
        invalid_tool_call_rate=invalid / max(total_calls, 1),
        redundant_tool_call_rate=redundant / max(total_calls, 1),
        citation_compliance_rate=sum(item.citation_compliant for item in observations) / count,
        high_risk_error_count=high_risk_errors,
        average_input_tokens=fmean(item.input_tokens for item in observations),
        average_output_tokens=fmean(item.output_tokens for item in observations),
        average_model_calls=fmean(item.model_calls for item in observations),
        average_latency_ms=fmean(item.latency_ms for item in observations),
        consistency_rate=consistent_cases / len(groups),
    )


def compare_architectures(
    baseline: AgentEvaluationMetrics,
    candidate: AgentEvaluationMetrics,
) -> AgentEvaluationComparison:
    """关键质量不退化且无高风险错误时，候选才可进入默认切换评审。"""

    reasons: list[str] = []
    if candidate.task_success_rate < 0.95:
        reasons.append("候选架构任务成功率低于 95% 最低门槛")
    if candidate.exact_tool_selection_rate < 0.95:
        reasons.append("候选架构工具选择正确率低于 95% 最低门槛")
    if candidate.citation_compliance_rate < 1.0:
        reasons.append("候选架构 Citation 合规率未达到 100%")
    if candidate.consistency_rate < 0.90:
        reasons.append("候选架构重复执行一致性低于 90% 最低门槛")
    if candidate.task_success_rate < baseline.task_success_rate:
        reasons.append("任务成功率低于基线")
    if candidate.exact_tool_selection_rate < baseline.exact_tool_selection_rate:
        reasons.append("工具选择正确率低于基线")
    if candidate.citation_compliance_rate < baseline.citation_compliance_rate:
        reasons.append("引用合规率低于基线")
    if candidate.invalid_tool_call_rate > baseline.invalid_tool_call_rate:
        reasons.append("无效工具调用率高于基线")
    if candidate.redundant_tool_call_rate > baseline.redundant_tool_call_rate:
        reasons.append("冗余工具调用率高于基线")
    if candidate.high_risk_error_count > 0:
        reasons.append("候选架构存在高风险错误操作")
    if candidate.consistency_rate < baseline.consistency_rate:
        reasons.append("重复执行一致性低于基线")
    return AgentEvaluationComparison(
        baseline=baseline,
        candidate=candidate,
        candidate_eligible_for_default=not reasons,
        blocking_reasons=reasons,
    )
