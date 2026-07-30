"""模型工具编排中必须由服务端执行的确定性约束。"""

from collections.abc import Sequence

from experiment_guardian.application.errors import InputValidationError
from experiment_guardian.domain.agent import AgentToolRequest


def require_proposal_prerequisites(
    request: AgentToolRequest,
    completed_calls: Sequence[AgentToolRequest],
) -> None:
    """确保 Proposal 只在同 Run、同目标的确定性读取完成后准备。"""

    requirements: tuple[tuple[str, str], ...]
    target_key: str
    if request.name == "action_proposal_prepare_v1":
        target_key = "draft_id"
        requirements = (
            ("policy_draft_validate_v1", target_key),
            ("policy_draft_impact_get_v1", target_key),
        )
    elif request.name == "action_proposal_prepare_plan_decision_v1":
        target_key = "plan_check_id"
        requirements = (("plan_check_explain_v1", target_key),)
    elif request.name == "action_proposal_prepare_submission_decision_v1":
        target_key = "submission_id"
        requirements = (("submission_diagnose_v1", target_key),)
    else:
        return

    target = request.arguments.get(target_key)
    if target is None:
        raise InputValidationError(f"{request.name} 缺少目标参数 {target_key}")
    missing = [
        tool_name
        for tool_name, argument_name in requirements
        if not any(
            call.name == tool_name and str(call.arguments.get(argument_name)) == str(target)
            for call in completed_calls
        )
    ]
    if missing:
        raise InputValidationError(
            "Proposal 缺少同 Run、同目标的确定性前置读取: " + ", ".join(missing)
        )
