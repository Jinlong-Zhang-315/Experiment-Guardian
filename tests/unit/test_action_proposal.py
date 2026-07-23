from datetime import UTC, datetime, timedelta
from uuid import uuid4

from experiment_guardian.domain.action_proposal import build_action_proposal_digest
from experiment_guardian.domain.enums import ActionProposalOperation
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
