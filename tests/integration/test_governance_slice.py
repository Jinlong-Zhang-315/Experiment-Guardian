"""Plan Check 审批到不可变 Run Manifest 的纵向验收测试。"""

from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.api.dependencies import require_api_identity
from experiment_guardian.api.routes import plan_checks as plan_checks_route
from experiment_guardian.application.errors import AuthorizationError, ConflictError
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.services import GuardianApplication, PlanApprovalService
from experiment_guardian.domain.administration import PlanCheckDecisionRequest
from experiment_guardian.domain.enums import (
    ApprovalDecision,
    ApprovalStatus,
    CheckResult,
    TeamRole,
)
from experiment_guardian.domain.run_manifest import build_manifest_content, canonical_json_hash
from experiment_guardian.infrastructure.models import (
    ApprovalRecord,
    AuditLog,
    IdempotencyRecord,
    PlanCheck,
    ProjectContext,
    RunManifest,
    TeamMember,
    User,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyGovernanceRepository,
    SqlAlchemyPlanCheckRepository,
    SqlAlchemyProjectRepository,
)
from experiment_guardian.main import create_app
from tests.integration.test_plan_check_slice import command, config_yaml, initialize_policy


def approval_service(factory: sessionmaker[Session]) -> PlanApprovalService:
    return PlanApprovalService(
        factory,
        SqlAlchemyProjectRepository(),
        SqlAlchemyGovernanceRepository(),
    )


def owner_api_identity(identity: RequestIdentity) -> RequestIdentity:
    return RequestIdentity(
        user_id=identity.user_id,
        team_id=identity.team_id,
        token_id=uuid4(),
        scopes=frozenset({"plan:approve"}),
    )


def manifest_identity(identity: RequestIdentity, project_id: UUID) -> RequestIdentity:
    return RequestIdentity(
        user_id=identity.user_id,
        team_id=identity.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"manifest:create"}),
    )


def test_owner_approval_is_immutable_idempotent_and_audited(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    identity, project_id, intent_id, guardian = initialize_policy(plan_check_session_factory)
    plan = guardian.experiment_check_plan(
        command(
            project_id=project_id,
            intent_id=intent_id,
            content=config_yaml(backbone="transformer"),
        ),
        identity,
    )
    assert plan.check_result is CheckResult.NEEDS_APPROVAL
    service = approval_service(plan_check_session_factory)
    owner = owner_api_identity(identity)
    key = uuid4()
    request = PlanCheckDecisionRequest(decision=ApprovalDecision.APPROVED, decision_reason="   ")

    first = service.decide(
        identity=owner,
        project_id=project_id,
        plan_check_id=plan.plan_check_id,
        idempotency_key=key,
        request=request,
    )
    replay = service.decide(
        identity=owner,
        project_id=project_id,
        plan_check_id=plan.plan_check_id,
        idempotency_key=key,
        request=request,
    )
    assert replay == first
    assert first.decision is ApprovalDecision.APPROVED
    assert first.decision_reason is None
    assert first.can_create_manifest is True

    with plan_check_session_factory() as session:
        record = session.get(PlanCheck, plan.plan_check_id)
        approval = session.get(ApprovalRecord, first.approval_record_id)
        assert record is not None and approval is not None
        assert record.approval_status is ApprovalStatus.APPROVED
        assert record.approved_by == owner.user_id
        assert approval.requested_by == identity.user_id
        assert "planned_changes" in approval.request_reason
        assert session.scalar(select(func.count()).select_from(ApprovalRecord)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "plan_check.decision")
            )
            == 1
        )

    changed = PlanCheckDecisionRequest(
        decision=ApprovalDecision.REJECTED,
        decision_reason="changed",
    )
    with pytest.raises(ConflictError, match="不同的审批请求"):
        service.decide(
            identity=owner,
            project_id=project_id,
            plan_check_id=plan.plan_check_id,
            idempotency_key=key,
            request=changed,
        )
    with pytest.raises(ConflictError, match="PENDING"):
        service.decide(
            identity=owner,
            project_id=project_id,
            plan_check_id=plan.plan_check_id,
            idempotency_key=uuid4(),
            request=request,
        )


def test_rejection_cannot_create_manifest_and_keeps_plan_approval_actor_empty(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    identity, project_id, intent_id, guardian = initialize_policy(plan_check_session_factory)
    plan = guardian.experiment_check_plan(
        command(
            project_id=project_id,
            intent_id=intent_id,
            content=config_yaml(backbone="transformer"),
        ),
        identity,
    )
    result = approval_service(plan_check_session_factory).decide(
        identity=owner_api_identity(identity),
        project_id=project_id,
        plan_check_id=plan.plan_check_id,
        idempotency_key=uuid4(),
        request=PlanCheckDecisionRequest(
            decision=ApprovalDecision.REJECTED,
            decision_reason="风险不可接受",
        ),
    )
    assert result.can_create_manifest is False
    with plan_check_session_factory() as session:
        record = session.get(PlanCheck, plan.plan_check_id)
        assert record is not None
        assert record.approval_status is ApprovalStatus.REJECTED
        assert record.approved_by is None and record.approved_at is None

    with pytest.raises(ConflictError, match="不允许创建"):
        guardian.run_manifest_create(
            plan_check_id=plan.plan_check_id,
            identity=manifest_identity(identity, project_id),
            idempotency_key=uuid4(),
        )


def test_manifest_uses_plan_snapshots_and_one_plan_allows_only_one_key(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    identity, project_id, intent_id, guardian = initialize_policy(plan_check_session_factory)
    plan = guardian.experiment_check_plan(
        command(project_id=project_id, intent_id=intent_id), identity
    )
    assert plan.check_result is CheckResult.PASS

    # 当前 Context 后续漂移不能改变已经检查过的历史运行凭据。
    with plan_check_session_factory() as session, session.begin():
        context = session.get(ProjectContext, plan.context_id)
        assert context is not None
        context.dataset = "CHANGED"
        context.protocol = "48/12"
        context.default_seeds = [99]

    mcp_identity = manifest_identity(identity, project_id)
    key = uuid4()
    first = guardian.run_manifest_create(
        plan_check_id=plan.plan_check_id,
        identity=mcp_identity,
        idempotency_key=key,
    )
    restarted = GuardianApplication(
        plan_check_session_factory,
        SqlAlchemyProjectRepository(),
        SqlAlchemyPlanCheckRepository(),
        SqlAlchemyGovernanceRepository(),
    )
    replay = restarted.run_manifest_create(
        plan_check_id=plan.plan_check_id,
        identity=mcp_identity,
        idempotency_key=key,
    )
    assert replay == first
    assert first.schema_version == 1
    assert first.dataset == "NTU60"
    assert first.protocol == "40/20"
    assert first.seed == 1
    assert first.config_hash == plan.config_hash
    assert first.config_document_hash == plan.document_sha256
    assert first.config_snapshot["parsed"] == plan.parsed_config
    assert first.evidence_snapshot["local_attestation"]["git_branch"]["value"] == "main"
    with plan_check_session_factory() as session:
        record = session.get(PlanCheck, plan.plan_check_id)
        assert record is not None
        assert canonical_json_hash(build_manifest_content(record, None)) == first.manifest_hash

    with pytest.raises(ConflictError, match="其他 Idempotency-Key"):
        guardian.run_manifest_create(
            plan_check_id=plan.plan_check_id,
            identity=mcp_identity,
            idempotency_key=uuid4(),
        )
    with plan_check_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RunManifest)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "run_manifest.create")
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(IdempotencyRecord.operation == "run_manifest.create")
            )
            == 1
        )


def test_approved_plan_creates_manifest_with_matching_approval_record(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    identity, project_id, intent_id, guardian = initialize_policy(plan_check_session_factory)
    plan = guardian.experiment_check_plan(
        command(
            project_id=project_id,
            intent_id=intent_id,
            content=config_yaml(backbone="transformer"),
        ),
        identity,
    )
    decision = approval_service(plan_check_session_factory).decide(
        identity=owner_api_identity(identity),
        project_id=project_id,
        plan_check_id=plan.plan_check_id,
        idempotency_key=uuid4(),
        request=PlanCheckDecisionRequest(decision=ApprovalDecision.APPROVED),
    )
    manifest = guardian.run_manifest_create(
        plan_check_id=plan.plan_check_id,
        identity=manifest_identity(identity, project_id),
        idempotency_key=uuid4(),
    )
    assert manifest.approval_record_id == decision.approval_record_id


def test_approval_requires_owner_scope_and_team_role(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    identity, project_id, intent_id, guardian = initialize_policy(plan_check_session_factory)
    plan = guardian.experiment_check_plan(
        command(
            project_id=project_id,
            intent_id=intent_id,
            content=config_yaml(backbone="transformer"),
        ),
        identity,
    )
    service = approval_service(plan_check_session_factory)
    request = PlanCheckDecisionRequest(decision=ApprovalDecision.APPROVED)
    missing_scope = owner_api_identity(identity)
    missing_scope = RequestIdentity(
        user_id=missing_scope.user_id,
        team_id=missing_scope.team_id,
        token_id=missing_scope.token_id,
        scopes=frozenset(),
    )
    with pytest.raises(AuthorizationError, match="plan:approve"):
        service.decide(
            identity=missing_scope,
            project_id=project_id,
            plan_check_id=plan.plan_check_id,
            idempotency_key=uuid4(),
            request=request,
        )

    researcher_id = uuid4()
    with plan_check_session_factory() as session, session.begin():
        session.add(
            User(id=researcher_id, name="Researcher", email="approval-researcher@example.com")
        )
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
        scopes=frozenset({"plan:approve"}),
    )
    with pytest.raises(AuthorizationError):
        service.decide(
            identity=researcher,
            project_id=project_id,
            plan_check_id=plan.plan_check_id,
            idempotency_key=uuid4(),
            request=request,
        )


@pytest.mark.asyncio
async def test_plan_decision_api_uses_authenticated_identity(
    plan_check_session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    identity, project_id, intent_id, guardian = initialize_policy(plan_check_session_factory)
    plan = guardian.experiment_check_plan(
        command(
            project_id=project_id,
            intent_id=intent_id,
            content=config_yaml(backbone="transformer"),
        ),
        identity,
    )
    owner = owner_api_identity(identity)
    service = approval_service(plan_check_session_factory)
    monkeypatch.setattr(plan_checks_route, "get_plan_approval_service", lambda: service)
    app = create_app()

    async def override_identity() -> RequestIdentity:
        return owner

    app.dependency_overrides[require_api_identity] = override_identity
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/projects/{project_id}/plan-checks/{plan.plan_check_id}/decision",
            headers={"Idempotency-Key": str(uuid4())},
            json={"decision": "APPROVED", "decision_reason": "Owner reviewed"},
        )

    assert response.status_code == 201
    assert response.json()["decision"] == "APPROVED"
    assert response.json()["decided_by"] == str(owner.user_id)


def test_incomplete_plan_snapshot_cannot_create_manifest(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    identity, project_id, intent_id, guardian = initialize_policy(plan_check_session_factory)
    plan = guardian.experiment_check_plan(
        command(project_id=project_id, intent_id=intent_id), identity
    )
    with plan_check_session_factory() as session, session.begin():
        record = session.get(PlanCheck, plan.plan_check_id)
        assert record is not None
        record.context_snapshot = {}

    with pytest.raises(ConflictError, match="完整历史快照"):
        guardian.run_manifest_create(
            plan_check_id=plan.plan_check_id,
            identity=manifest_identity(identity, project_id),
            idempotency_key=uuid4(),
        )


@pytest.mark.parametrize("seed_value", [True, "7"])
def test_manifest_rejects_non_integer_explicit_seed(
    plan_check_session_factory: sessionmaker[Session], seed_value: object
) -> None:
    identity, project_id, intent_id, guardian = initialize_policy(plan_check_session_factory)
    rendered_seed = "true" if seed_value is True else f'"{seed_value}"'
    plan = guardian.experiment_check_plan(
        command(
            project_id=project_id,
            intent_id=intent_id,
            content=config_yaml() + f"seed: {rendered_seed}\n",
        ),
        identity,
    )
    assert plan.check_result is CheckResult.NEEDS_APPROVAL
    approval_service(plan_check_session_factory).decide(
        identity=owner_api_identity(identity),
        project_id=project_id,
        plan_check_id=plan.plan_check_id,
        idempotency_key=uuid4(),
        request=PlanCheckDecisionRequest(decision=ApprovalDecision.APPROVED),
    )

    with pytest.raises(ConflictError, match="seed 必须是整数"):
        guardian.run_manifest_create(
            plan_check_id=plan.plan_check_id,
            identity=manifest_identity(identity, project_id),
            idempotency_key=uuid4(),
        )


def test_manifest_prefers_explicit_integer_seed(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    identity, project_id, intent_id, guardian = initialize_policy(plan_check_session_factory)
    plan = guardian.experiment_check_plan(
        command(
            project_id=project_id,
            intent_id=intent_id,
            content=config_yaml() + "seed: 7\n",
        ),
        identity,
    )
    approval_service(plan_check_session_factory).decide(
        identity=owner_api_identity(identity),
        project_id=project_id,
        plan_check_id=plan.plan_check_id,
        idempotency_key=uuid4(),
        request=PlanCheckDecisionRequest(decision=ApprovalDecision.APPROVED),
    )
    manifest = guardian.run_manifest_create(
        plan_check_id=plan.plan_check_id,
        identity=manifest_identity(identity, project_id),
        idempotency_key=uuid4(),
    )
    assert manifest.seed == 7


def test_manifest_requires_exactly_one_default_seed_when_config_omits_it(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    identity, project_id, intent_id, guardian = initialize_policy(plan_check_session_factory)
    with plan_check_session_factory() as session, session.begin():
        context = session.scalar(
            select(ProjectContext).where(ProjectContext.project_id == project_id)
        )
        assert context is not None
        context.default_seeds = [1, 2]
    plan = guardian.experiment_check_plan(
        command(project_id=project_id, intent_id=intent_id), identity
    )

    with pytest.raises(ConflictError, match="只能提供一个 default seed"):
        guardian.run_manifest_create(
            plan_check_id=plan.plan_check_id,
            identity=manifest_identity(identity, project_id),
            idempotency_key=uuid4(),
        )


def test_pass_and_blocked_plan_checks_cannot_be_approved(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    identity, project_id, intent_id, guardian = initialize_policy(plan_check_session_factory)
    passed = guardian.experiment_check_plan(
        command(project_id=project_id, intent_id=intent_id), identity
    )
    blocked = guardian.experiment_check_plan(
        command(
            project_id=project_id,
            intent_id=intent_id,
            content=config_yaml(protocol="48/12"),
        ),
        identity,
    )
    service = approval_service(plan_check_session_factory)
    for plan in (passed, blocked):
        with pytest.raises(ConflictError, match="PENDING"):
            service.decide(
                identity=owner_api_identity(identity),
                project_id=project_id,
                plan_check_id=plan.plan_check_id,
                idempotency_key=uuid4(),
                request=PlanCheckDecisionRequest(decision=ApprovalDecision.APPROVED),
            )
