"""R15c Policy Bundle 草稿的确定性校验、diff 和阅读表示测试。"""

from experiment_guardian.domain.enums import PolicyDraftReadiness
from experiment_guardian.domain.policy_draft import (
    PolicyDraftAmbiguity,
    PolicyDraftCandidate,
    diff_policy_candidates,
    render_policy_draft_narrative,
    validate_policy_candidate,
)
from tests.integration.test_foundation_slice import initial_request


def _candidate() -> PolicyDraftCandidate:
    request = initial_request()
    return PolicyDraftCandidate(
        context=request.context,
        intent=request.intent,
        constraints=request.constraints,
    )


def test_policy_draft_diff_is_strict_and_constraint_order_is_stable() -> None:
    base = _candidate()
    candidate = base.model_copy(deep=True)
    candidate.context.goal = "验证新目标"
    candidate.context.active_config["model"]["fusion"] = True
    candidate.constraints[1].expected_value = True
    candidate.constraints = list(reversed(candidate.constraints))

    diff = diff_policy_candidates(base, candidate)
    paths = {item.field_path for item in diff}
    assert "context.goal" in paths
    assert "context.active_config" in paths
    assert "constraints.model.fusion" in paths
    assert len([item for item in diff if item.field_path.startswith("constraints.")]) == 1
    goal_change = next(item for item in diff if item.field_path == "context.goal")
    assert goal_change.attention_level == "HIGH"


def test_policy_draft_validation_persists_semantic_conflicts_and_ambiguities() -> None:
    invalid = _candidate()
    invalid.constraints.append(invalid.constraints[0].model_copy(deep=True))
    invalid.intent.allowed_variables = []
    validation = validate_policy_candidate(invalid, [])
    assert validation.readiness is PolicyDraftReadiness.INVALID
    assert {item.code for item in validation.issues} >= {
        "DUPLICATE_CONSTRAINT_PATH",
        "ALLOWED_VARIABLES_MISMATCH",
    }

    ambiguous = _candidate()
    validation = validate_policy_candidate(
        ambiguous,
        [
            PolicyDraftAmbiguity(
                field_path="context.mainline_model",
                question="是否替换正式主线？",
                source_text="试一下别的主线",
            )
        ],
    )
    assert validation.readiness is PolicyDraftReadiness.NEEDS_CLARIFICATION
    narrative = render_policy_draft_narrative(
        ambiguous,
        diff_policy_candidates(_candidate(), ambiguous),
        validation,
    )
    assert narrative.status == "READY"
    assert narrative.authoritative is False
    assert "尚未生效" in (narrative.content or "")


def test_policy_draft_expected_value_uses_strict_json_types() -> None:
    candidate = _candidate()
    candidate.context.active_config["model"]["fusion"] = True
    candidate.constraints[1].expected_value = 1
    validation = validate_policy_candidate(candidate, [])
    assert validation.readiness is PolicyDraftReadiness.INVALID
    assert any(item.code == "EXPECTED_VALUE_MISMATCH" for item in validation.issues)
