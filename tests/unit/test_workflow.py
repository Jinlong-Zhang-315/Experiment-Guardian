"""提交分析工作流拓扑测试。"""

import pytest

from experiment_guardian.domain.enums import WorkflowStep
from experiment_guardian.workflows.submission import (
    WORKFLOW_ORDER,
    SubmissionWorkflowState,
    build_submission_workflow,
)


def passthrough(state: SubmissionWorkflowState) -> SubmissionWorkflowState:
    return state


def test_submission_workflow_builds_all_ordered_nodes() -> None:
    handlers = {step: passthrough for step in WORKFLOW_ORDER}

    workflow = build_submission_workflow(handlers)
    graph = workflow.get_graph()

    assert {step.value for step in WORKFLOW_ORDER} <= set(graph.nodes)


def test_submission_workflow_rejects_missing_handler() -> None:
    handlers = {
        step: passthrough for step in WORKFLOW_ORDER if step is not WorkflowStep.RISK_ANALYSIS
    }

    with pytest.raises(ValueError, match="RISK_ANALYSIS"):
        build_submission_workflow(handlers)
