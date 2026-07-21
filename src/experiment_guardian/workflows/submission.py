"""可恢复的实验提交分析工作流骨架。

本模块只固定节点顺序和状态格式。每个节点的真实实现（S3 校验、重复检测、Bedrock 摘要、
向量生成等）通过 handler 注入。这样工作流定义可以稳定，而外部服务适配器可分别测试。
"""

from collections.abc import Callable, Mapping
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from experiment_guardian.domain.enums import WorkflowStep


class SubmissionWorkflowState(TypedDict, total=False):
    """持久化在 LangGraph checkpoint 中的最小状态。

    大文件内容只保存 S3 key，不放入 checkpoint；否则一次日志上传就可能让 CockroachDB
    checkpoint 膨胀。节点输出应保存结构化摘要和数据库记录 ID。
    """

    submission_id: str
    processing_step: str
    artifact_ids: list[str]
    parsed_config: dict[str, Any]
    parsed_metrics: list[dict[str, Any]]
    duplicate_ids: list[str]
    risk_ids: list[str]
    summary: str
    embedding_ready: bool
    error: str | None


WorkflowHandler = Callable[[SubmissionWorkflowState], SubmissionWorkflowState]

WORKFLOW_ORDER = (
    WorkflowStep.UPLOAD_VERIFICATION,
    WorkflowStep.CONFIG_PARSE,
    WorkflowStep.MANIFEST_VALIDATION,
    WorkflowStep.DUPLICATE_CHECK,
    WorkflowStep.RISK_ANALYSIS,
    WorkflowStep.SUMMARY_GENERATION,
    WorkflowStep.EMBEDDING_GENERATION,
    WorkflowStep.NEEDS_REVIEW,
)


def build_submission_workflow(
    handlers: Mapping[WorkflowStep, WorkflowHandler],
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> Any:
    """构建并编译提交分析图。

    调用方必须为每个步骤提供 handler，防止部署时静默跳过风险分析。运行时应使用
    ``submission_id`` 作为 LangGraph ``thread_id``，后端重启后即可从最近 checkpoint 恢复。
    """

    missing = [step.value for step in WORKFLOW_ORDER if step not in handlers]
    if missing:
        raise ValueError(f"缺少工作流节点实现: {', '.join(missing)}")

    graph = StateGraph(SubmissionWorkflowState)
    for step in WORKFLOW_ORDER:
        # LangGraph 1.2 的公开类型重载没有接受等价的 Callable 别名，但运行时接口支持
        # 该节点签名；这里将忽略严格限定在第三方类型声明边界，不扩散到业务代码。
        graph.add_node(step.value, handlers[step])  # type: ignore[call-overload]

    graph.add_edge(START, WORKFLOW_ORDER[0].value)
    for current, following in zip(WORKFLOW_ORDER, WORKFLOW_ORDER[1:], strict=True):
        graph.add_edge(current.value, following.value)
    graph.add_edge(WORKFLOW_ORDER[-1].value, END)
    return graph.compile(checkpointer=checkpointer)
