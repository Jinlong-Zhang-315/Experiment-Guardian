from datetime import UTC, datetime, timedelta
from uuid import uuid4

from experiment_guardian.domain.action_proposal import (
    build_action_proposal_digest,
    build_plan_decision_proposal_digest,
)
from experiment_guardian.domain.administration import PlanCheckDecisionRequest
from experiment_guardian.domain.enums import ActionProposalOperation, ApprovalDecision
from tests.integration.test_web_management_slice import _publish_request


def test_action_proposal_digest_is_stable_and_type_sensitive() -> None:
    expires_at = datetime.now(UTC) + timedelta(hours=24)
    request = _publish_request()
    proposal_id = uuid4()
    project_id = uuid4()
    draft_id = uuid4()
    revision_id = uuid4()
    arguments = {
        "proposal_id": proposal_id,
        "operation": ActionProposalOperation.POLICY_PUBLISH,
        "project_id": project_id,
        "payload": request,
        "source_draft_id": draft_id,
        "source_draft_revision_id": revision_id,
        "source_draft_revision": 1,
        "source_candidate_hash": "a" * 64,
        "base_policy_hash": "b" * 64,
        "pending_state_hash": "c" * 64,
        "expires_at": expires_at,
    }
    first = build_action_proposal_digest(**arguments)
    assert build_action_proposal_digest(**arguments) == first

    changed = request.model_copy(deep=True)
    changed.context.active_config["seed"] = True
    assert build_action_proposal_digest(**{**arguments, "payload": changed}) != first


def test_action_proposal_digest_binds_expiry() -> None:
    request = _publish_request()
    now = datetime.now(UTC)
    arguments = {
        "proposal_id": uuid4(),
        "operation": ActionProposalOperation.POLICY_PUBLISH,
        "project_id": uuid4(),
        "payload": request,
        "source_draft_id": uuid4(),
        "source_draft_revision_id": uuid4(),
        "source_draft_revision": 3,
        "source_candidate_hash": "a" * 64,
        "base_policy_hash": "b" * 64,
        "pending_state_hash": "c" * 64,
    }
    assert build_action_proposal_digest(
        **arguments,
        expires_at=now,
    ) != build_action_proposal_digest(
        **arguments,
        expires_at=now + timedelta(seconds=1),
    )


def test_plan_decision_digest_binds_decision_reason_state_and_expiry() -> None:
    arguments = {
        "proposal_id": uuid4(),
        "project_id": uuid4(),
        "plan_check_id": uuid4(),
        "payload": PlanCheckDecisionRequest(
            decision=ApprovalDecision.APPROVED,
            decision_reason="批准当前变化",
        ),
        "target_state_hash": "d" * 64,
        "base_context_id": uuid4(),
        "base_context_version": 3,
        "base_intent_id": uuid4(),
        "base_intent_version": 2,
        "expires_at": datetime.now(UTC) + timedelta(hours=24),
    }
    digest = build_plan_decision_proposal_digest(**arguments)
    assert build_plan_decision_proposal_digest(**arguments) == digest
    assert (
        build_plan_decision_proposal_digest(
            **{
                **arguments,
                "payload": PlanCheckDecisionRequest(
                    decision=ApprovalDecision.REJECTED,
                    decision_reason="拒绝当前变化",
                ),
            }
        )
        != digest
    )
    assert (
        build_plan_decision_proposal_digest(
            **{**arguments, "target_state_hash": "e" * 64}
        )
        != digest
    )
    assert (
        build_plan_decision_proposal_digest(
            **{
                **arguments,
                "expires_at": arguments["expires_at"] + timedelta(seconds=1),
            }
        )
        != digest
    )
