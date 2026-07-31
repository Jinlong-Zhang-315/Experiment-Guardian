"""R15b 计划解释与提交诊断工具的权限和动态状态测试。"""

import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.agent_tools import AgentToolRegistry
from experiment_guardian.application.errors import AuthorizationError
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.domain.enums import TeamRole, WorkflowJobStatus
from experiment_guardian.infrastructure.models import (
    ProjectContext,
    TeamMember,
    User,
    WorkflowJob,
)
from experiment_guardian.infrastructure.repositories import SqlAlchemyProjectRepository
from tests.integration.test_async_review_slice import prepare_summary
from tests.integration.test_plan_check_slice import (
    command,
    initialize_policy,
)


def test_plan_review_policy_projection_omits_large_active_config(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    identity, project_id, _, _ = initialize_policy(plan_check_session_factory)
    with plan_check_session_factory() as session, session.begin():
        context = session.scalar(
            select(ProjectContext).where(ProjectContext.project_id == project_id)
        )
        assert context is not None
        context.active_config = {
            **context.active_config,
            "large_uncontrolled_section": {
                f"field_{index}": "x" * 120 for index in range(400)
            },
        }

    result = AgentToolRegistry(
        plan_check_session_factory, SqlAlchemyProjectRepository()
    ).execute(
        tool_name="project_status_get_v1",
        arguments={},
        project_id=project_id,
        identity=identity,
        evidence_prefix="ev_1",
        catalog_version="r17b-plan-review-v3",
    )
    serialized = json.dumps(
        result.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    policy = result.content["policy"]
    summary = policy["active_config_summary"]
    governed = {item["parameter_path"]: item for item in summary["governed_values"]}

    assert len(serialized) < 32768
    assert policy["projection"] == "COMPACT_AGENT_POLICY_V1"
    assert policy["authoritative"] is True
    assert len(policy["policy_hash"]) == 64
    assert "active_config" not in policy["context_payload"]
    assert summary["body_omitted"] is True
    assert summary["path_count"] >= 403
    assert governed["dataset.protocol"]["current_value"] == "40/20"
    assert governed["model.backbone"]["current_value"] == "shift-gcn"
    assert governed["model.fusion"]["current_value"] == 0.2
    assert result.evidence[0].entity_type == "POLICY_BUNDLE"
    assert "active_config" not in result.evidence[0].payload


def test_plan_explanation_uses_current_approval_columns_not_stale_report(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    identity, project_id, intent_id, guardian = initialize_policy(plan_check_session_factory)
    plan = guardian.experiment_check_plan(
        command(project_id=project_id, intent_id=intent_id), identity
    )
    owner = RequestIdentity(
        user_id=identity.user_id,
        team_id=identity.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"plan:read"}),
    )
    result = AgentToolRegistry(plan_check_session_factory, SqlAlchemyProjectRepository()).execute(
        tool_name="plan_check_explain_v1",
        arguments={"plan_check_id": str(plan.plan_check_id)},
        project_id=project_id,
        identity=owner,
        evidence_prefix="ev_1",
        catalog_version="r15b-v1",
    )
    assert result.content["approval_status"] == "NOT_REQUIRED"
    assert result.content["governance_allows_manifest"] is True
    assert result.content["can_create_manifest_now"] is True
    assert result.content["manifest"] is None
    assert result.evidence[0].evidence_kind.value == "CONFIRMED_FACT"


def test_submission_diagnosis_is_read_only_and_researcher_cannot_read_others(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, _, finalize_command, _ = prepare_summary(plan_check_session_factory)
    with plan_check_session_factory() as session, session.begin():
        job = session.scalar(select(WorkflowJob).order_by(WorkflowJob.created_at.desc()))
        assert job is not None
        job.status = WorkflowJobStatus.DEAD_LETTER
        job.last_error = {"code": "MODEL_TIMEOUT"}
        researcher_id = uuid4()
        session.add(
            User(
                id=researcher_id,
                name="Researcher",
                email="agent-tools-researcher@example.com",
            )
        )
        session.flush()
        session.add(
            TeamMember(
                team_id=owner.team_id,
                user_id=researcher_id,
                role=TeamRole.RESEARCHER,
            )
        )
    registry = AgentToolRegistry(plan_check_session_factory, SqlAlchemyProjectRepository())
    owner_identity = RequestIdentity(
        user_id=owner.user_id,
        team_id=owner.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"submission:read"}),
    )
    result = registry.execute(
        tool_name="submission_diagnose_v1",
        arguments={"submission_id": str(finalize_command.submission_id)},
        project_id=project_id,
        identity=owner_identity,
        evidence_prefix="ev_1",
        catalog_version="r15b-v1",
    )
    assert any(item["code"] == "BACKGROUND_JOB_FAILED" for item in result.content["findings"])
    assert len(result.evidence) == 2
    assert result.evidence[1].evidence_kind.value == "ANALYSIS"

    researcher = RequestIdentity(
        user_id=researcher_id,
        team_id=owner.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"submission:read"}),
    )
    with pytest.raises(AuthorizationError, match="只能诊断自己"):
        registry.execute(
            tool_name="submission_diagnose_v1",
            arguments={"submission_id": str(finalize_command.submission_id)},
            project_id=project_id,
            identity=researcher,
            evidence_prefix="ev_2",
            catalog_version="r15b-v1",
        )
