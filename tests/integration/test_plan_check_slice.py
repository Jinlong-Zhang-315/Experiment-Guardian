"""训练前检查持久化切片的纵向验收测试。"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.errors import (
    AuthorizationError,
    ConflictError,
    InputValidationError,
)
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.services import (
    GuardianApplication,
    ProjectAdministrationService,
)
from experiment_guardian.domain.administration import ProjectInitializeRequest
from experiment_guardian.domain.contracts import (
    ConfigurationDocument,
    ExperimentCheckPlanCommand,
    FieldEvidence,
    LocalAttestation,
    LocalEnvironment,
)
from experiment_guardian.domain.enums import (
    ApprovalStatus,
    CheckResult,
    ConfigFormat,
    ConstraintSource,
    EvidenceType,
    ProtectionLevel,
    RiskSeverity,
    TeamRole,
    VerificationStatus,
)
from experiment_guardian.infrastructure.models import (
    ExperimentIntent,
    PlanCheck,
    ProjectContext,
    ProtectedParameter,
    Team,
    TeamMember,
    User,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyPlanCheckRepository,
    SqlAlchemyProjectRepository,
)

COLLECTED_AT = datetime(2026, 7, 21, tzinfo=UTC)
GIT_COMMIT = "a1b2c3d4"
RUN_COMMAND = "python train.py --config config.yaml"


def evidence(value: object, source: str) -> FieldEvidence:
    return FieldEvidence(
        value=value,
        evidence_type=EvidenceType.LOCAL_ATTESTED,
        source=source,
        collected_at=COLLECTED_AT,
        collection_tool="experiment-guardian-local-preflight/0.1",
    )


def complete_attestation() -> LocalAttestation:
    return LocalAttestation(
        working_tree_clean=evidence(True, "git status --porcelain"),
        git_branch=evidence("main", "git branch --show-current"),
        git_commit=evidence(GIT_COMMIT, "git rev-parse HEAD"),
        run_command=evidence(RUN_COMMAND, "local agent run plan"),
        output_directory_exists=evidence(False, "local filesystem"),
        checkpoint_exists=evidence(True, "local filesystem"),
        checkpoint_path=evidence("checkpoints/baseline.pt", "local run plan"),
        config_sha256=evidence("a" * 64, "sha256sum config.yaml"),
        git_diff_sha256=evidence("b" * 64, "git diff"),
        environment=LocalEnvironment(
            python=evidence("3.12.13", "python --version"),
            cuda=evidence("12.4", "nvidia-smi"),
            pytorch=evidence("2.7.0", "python import torch"),
        ),
    )


def config_yaml(
    *, protocol: str = "40/20", backbone: str = "shift-gcn", fusion: float = 0.3
) -> str:
    return f"dataset:\n  protocol: {protocol}\nmodel:\n  backbone: {backbone}\n  fusion: {fusion}\n"


def command(
    *,
    project_id: UUID,
    intent_id: UUID,
    content: str | None = None,
    idempotency_key: UUID | None = None,
) -> ExperimentCheckPlanCommand:
    return ExperimentCheckPlanCommand(
        project_id=project_id,
        experiment_intent_id=intent_id,
        idempotency_key=idempotency_key or uuid4(),
        configuration=ConfigurationDocument(
            format=ConfigFormat.YAML,
            content=content or config_yaml(),
        ),
        command=RUN_COMMAND,
        git_commit=GIT_COMMIT,
        local_attestation=complete_attestation(),
    )


def seed_owner(factory: sessionmaker[Session]) -> RequestIdentity:
    user_id, team_id = uuid4(), uuid4()
    with factory() as session, session.begin():
        session.add(User(id=user_id, name="Owner", email="owner-plan@example.com"))
        session.flush()
        session.add(Team(id=team_id, name="Plan Lab", owner_id=user_id))
        session.flush()
        session.add(TeamMember(team_id=team_id, user_id=user_id, role=TeamRole.OWNER))
    return RequestIdentity(
        user_id=user_id,
        team_id=team_id,
        token_id=uuid4(),
        scopes=frozenset({"project:initialize"}),
    )


def build_services(
    factory: sessionmaker[Session],
) -> tuple[ProjectAdministrationService, GuardianApplication]:
    projects = SqlAlchemyProjectRepository()
    plan_checks = SqlAlchemyPlanCheckRepository()
    return (
        ProjectAdministrationService(factory, projects),
        GuardianApplication(factory, projects, plan_checks),
    )


def initialize_policy(
    factory: sessionmaker[Session],
) -> tuple[RequestIdentity, UUID, UUID, GuardianApplication]:
    owner = seed_owner(factory)
    administration, guardian = build_services(factory)
    request = ProjectInitializeRequest.model_validate_json(
        Path("examples/project-initialize.json").read_text(encoding="utf-8")
    )
    initialized = administration.initialize_project(
        identity=owner,
        idempotency_key=uuid4(),
        request=request,
    )
    intent = initialized.context_bundle.active_intent
    assert intent is not None
    mcp_identity = RequestIdentity(
        user_id=owner.user_id,
        team_id=owner.team_id,
        token_id=uuid4(),
        project_id=initialized.project_id,
        scopes=frozenset({"experiment:check", "project:read"}),
    )
    return mcp_identity, initialized.project_id, intent.intent_id, guardian


def test_pass_is_persisted_and_replayed_without_re_evaluation(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    identity, project_id, intent_id, guardian = initialize_policy(plan_check_session_factory)
    idempotency_key = uuid4()
    request = command(
        project_id=project_id,
        intent_id=intent_id,
        idempotency_key=idempotency_key,
    )

    first = guardian.experiment_check_plan(request, identity)
    assert first.check_result is CheckResult.PASS
    assert first.approval_status is ApprovalStatus.NOT_REQUIRED
    assert first.risk_level is RiskSeverity.LOW
    assert first.can_create_manifest is True
    assert first.missing_information == []

    with plan_check_session_factory() as session, session.begin():
        record = session.scalar(select(PlanCheck))
        context = session.get(ProjectContext, first.context_id)
        assert record is not None and context is not None
        assert record.report["plan_check_id"] == str(first.plan_check_id)
        assert record.input_config_hash == first.config_hash
        assert record.local_attestation["git_commit"]["evidence_type"] == "LOCAL_ATTESTED"
        assert len(record.constraint_snapshot) == 3
        context.active_config = {
            "dataset": {"protocol": "48/12"},
            "model": {"backbone": "shift-gcn", "fusion": 0.2},
        }

    _, restarted = build_services(plan_check_session_factory)
    replay = restarted.experiment_check_plan(request, identity)
    assert replay == first

    changed = request.model_copy(deep=True)
    changed.configuration.content = config_yaml(fusion=0.4)
    with pytest.raises(ConflictError, match="不同的配置检查请求"):
        restarted.experiment_check_plan(changed, identity)
    with plan_check_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PlanCheck)) == 1


def test_locked_and_approval_required_changes_persist_distinct_states(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    identity, project_id, intent_id, guardian = initialize_policy(plan_check_session_factory)

    blocked = guardian.experiment_check_plan(
        command(
            project_id=project_id,
            intent_id=intent_id,
            content=config_yaml(protocol="48/12"),
        ),
        identity,
    )
    assert blocked.check_result is CheckResult.BLOCKED
    assert blocked.approval_status is ApprovalStatus.NOT_REQUIRED
    assert blocked.risk_level is RiskSeverity.CRITICAL
    assert blocked.can_create_manifest is False
    assert "LOCKED_PARAMETER_CHANGED" in {item.code for item in blocked.risks}

    approval = guardian.experiment_check_plan(
        command(
            project_id=project_id,
            intent_id=intent_id,
            content=config_yaml(backbone="transformer"),
        ),
        identity,
    )
    assert approval.check_result is CheckResult.NEEDS_APPROVAL
    assert approval.approval_status is ApprovalStatus.PENDING
    assert approval.risk_level is RiskSeverity.HIGH
    assert approval.can_create_manifest is False
    with plan_check_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PlanCheck)) == 2


def test_pending_constraint_is_snapshotted_but_cannot_block(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    identity, project_id, intent_id, guardian = initialize_policy(plan_check_session_factory)
    with plan_check_session_factory() as session, session.begin():
        intent = session.get(ExperimentIntent, intent_id)
        assert intent is not None
        session.add(
            ProtectedParameter(
                project_id=project_id,
                context_id=intent.context_id,
                context_version=intent.context_version,
                intent_id=intent.id,
                intent_version=intent.version,
                version=1,
                parameter_path="model.dropout",
                protection_level=ProtectionLevel.LOCKED,
                expected_value=0.0,
                reason="自然语言候选约束",
                source_type=ConstraintSource.INFERRED,
                verification_status=VerificationStatus.PENDING,
                original_message="保持其他局部行为不变",
                inference_basis="将局部行为解释为 dropout 不变",
                confidence=0.7,
                created_by=identity.user_id,
                active=True,
            )
        )

    content = config_yaml() + "  dropout: 0.1\n"
    result = guardian.experiment_check_plan(
        command(project_id=project_id, intent_id=intent_id, content=content),
        identity,
    )
    assert result.check_result is CheckResult.NEEDS_APPROVAL
    assert result.approval_status is ApprovalStatus.PENDING
    risk = next(item for item in result.risks if item.code == "UNCONFIRMED_CONSTRAINT_CANDIDATE")
    assert risk.blocking is False
    assert risk.constraint_status is VerificationStatus.PENDING
    with plan_check_session_factory() as session:
        record = session.get(PlanCheck, result.plan_check_id)
        assert record is not None
        snapshot = {item["parameter_path"]: item for item in record.constraint_snapshot}
        assert snapshot["model.dropout"]["source_type"] == "INFERRED"


def test_scope_project_membership_and_active_intent_are_enforced(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    identity, project_id, intent_id, guardian = initialize_policy(plan_check_session_factory)
    request = command(project_id=project_id, intent_id=intent_id)

    missing_scope = RequestIdentity(
        user_id=identity.user_id,
        team_id=identity.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"project:read"}),
    )
    with pytest.raises(AuthorizationError, match="experiment:check"):
        guardian.experiment_check_plan(request, missing_scope)

    wrong_project = RequestIdentity(
        user_id=identity.user_id,
        team_id=identity.team_id,
        token_id=uuid4(),
        project_id=uuid4(),
        scopes=frozenset({"experiment:check"}),
    )
    with pytest.raises(AuthorizationError, match="未绑定"):
        guardian.experiment_check_plan(request, wrong_project)

    wrong_team = RequestIdentity(
        user_id=identity.user_id,
        team_id=uuid4(),
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"experiment:check"}),
    )
    with pytest.raises(AuthorizationError, match="团队"):
        guardian.experiment_check_plan(request, wrong_team)

    stale = request.model_copy(update={"experiment_intent_id": uuid4(), "idempotency_key": uuid4()})
    with pytest.raises(ConflictError, match="不是当前 Active"):
        guardian.experiment_check_plan(stale, identity)

    researcher_id = uuid4()
    with plan_check_session_factory() as session, session.begin():
        session.add(User(id=researcher_id, name="Researcher", email="researcher-plan@example.com"))
        session.flush()
        session.add(
            TeamMember(
                team_id=identity.team_id,
                user_id=researcher_id,
                role=TeamRole.RESEARCHER,
            )
        )
    researcher = RequestIdentity(
        user_id=researcher_id,
        team_id=identity.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"experiment:check"}),
    )
    result = guardian.experiment_check_plan(
        command(project_id=project_id, intent_id=intent_id),
        researcher,
    )
    with plan_check_session_factory() as session:
        record = session.get(PlanCheck, result.plan_check_id)
        assert record is not None and record.requester_id == researcher_id


def test_invalid_config_is_atomic_and_drifted_baseline_is_blocked(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    identity, project_id, intent_id, guardian = initialize_policy(plan_check_session_factory)
    duplicate_yaml = (
        "dataset:\n  protocol: 40/20\nmodel:\n  backbone: shift-gcn\n  fusion: 0.3\n  fusion: 0.4\n"
    )
    with pytest.raises(InputValidationError, match="重复字段"):
        guardian.experiment_check_plan(
            command(project_id=project_id, intent_id=intent_id, content=duplicate_yaml),
            identity,
        )
    with plan_check_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PlanCheck)) == 0

    with plan_check_session_factory() as session, session.begin():
        context = session.scalar(
            select(ProjectContext).where(ProjectContext.project_id == project_id)
        )
        assert context is not None
        context.active_config = {
            "dataset": {"protocol": "48/12"},
            "model": {"backbone": "shift-gcn", "fusion": 0.2},
        }

    drifted = guardian.experiment_check_plan(
        command(
            project_id=project_id,
            intent_id=intent_id,
            content=config_yaml(protocol="48/12", fusion=0.2),
        ),
        identity,
    )
    assert drifted.check_result is CheckResult.BLOCKED
    assert "FORMAL_BASELINE_CONSTRAINT_MISMATCH" in {item.code for item in drifted.risks}
