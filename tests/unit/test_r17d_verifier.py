from datetime import UTC, datetime

import pytest

from experiment_guardian.application.agent_runtime import GovernanceAgentRuntime
from experiment_guardian.domain.contracts import LocalAttestation
from experiment_guardian.domain.invariant_check import FinalRunEvidence
from scripts.verify_r17d_local import (
    ACCEPTANCE_PROJECT_NAME,
    CONDITION,
    CONFIG_CONTENT,
    _condition_id,
    _final_run_evidence,
    _local_attestation,
    _select_project,
)


def test_r17d_evidence_payloads_validate_with_production_contracts() -> None:
    collected_at = datetime(2026, 7, 29, tzinfo=UTC).isoformat()

    local = LocalAttestation.model_validate(
        _local_attestation(CONFIG_CONTENT, collected_at=collected_at)
    )
    final = FinalRunEvidence.model_validate(
        _final_run_evidence(CONFIG_CONTENT.encode("utf-8"), collected_at=collected_at)
    )

    assert local.config_sha256 is not None
    assert local.environment.cuda is not None
    assert local.environment.cuda.not_applicable_reason
    assert final.invariant_attestations[0].invariant_id == _condition_id(CONDITION)
    assert final.invariant_attestations[0].status == "SATISFIED"


def test_r17d_refuses_to_write_into_a_non_acceptance_project() -> None:
    project_id = "8f16a583-2acf-46c0-ae54-31e6e5616ee1"

    with pytest.raises(ValueError, match="专用验收项目"):
        _select_project(
            [{"project_id": project_id, "name": "Production Research"}], project_id
        )

    selected = _select_project(
        [{"project_id": project_id, "name": ACCEPTANCE_PROJECT_NAME}], project_id
    )
    assert selected["name"] == ACCEPTANCE_PROJECT_NAME


def test_external_agent_uses_a_read_only_run_specific_response_schema() -> None:
    response_format = GovernanceAgentRuntime._answer_response_format(
        {
            "prompt_version": "r17a-external-v2",
            "experiment_plan_input": None,
        }
    )

    properties = response_format.json_schema["properties"]
    assert "research_report" not in properties
    assert "experiment_plan_review" not in properties
    assert response_format.json_schema["$defs"]["AgentEvidenceKind"]["enum"] == [
        "CONFIRMED_FACT",
        "USER_PROVIDED",
        "ANALYSIS",
        "HYPOTHESIS",
    ]
