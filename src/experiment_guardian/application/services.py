"""当前阶段的应用用例实现。"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.errors import (
    AuthorizationError,
    ConflictError,
    FeatureUnavailableError,
    InputValidationError,
    ServiceUnavailableError,
)
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.domain.administration import (
    ProjectInitializeRequest,
    ProjectInitializeResponse,
)
from experiment_guardian.domain.contracts import (
    ExperimentCheckPlanCommand,
    ExperimentCheckPlanResult,
    ExperimentQueryCommand,
    ExperimentQueryResult,
    PlanEvaluationInput,
    ProjectContextBundle,
)
from experiment_guardian.domain.enums import (
    CheckResult,
    ConstraintSource,
    ContextStatus,
    ExperimentMode,
    IdempotencyOperationStatus,
    IntentStatus,
    ProtectionLevel,
    RiskSeverity,
    TeamRole,
    VerificationStatus,
)
from experiment_guardian.domain.plan_check import (
    ConfigurationError,
    evaluate_plan,
    flatten_configuration,
)
from experiment_guardian.infrastructure.models import (
    AuditLog,
    ExperimentIntent,
    IdempotencyRecord,
    PlanCheck,
    Project,
    ProjectContext,
    ProtectedParameter,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyPlanCheckRepository,
    SqlAlchemyProjectRepository,
)

INITIALIZE_OPERATION = "project.initialize"
RISK_PRIORITY = {
    RiskSeverity.LOW: 0,
    RiskSeverity.MEDIUM: 1,
    RiskSeverity.HIGH: 2,
    RiskSeverity.CRITICAL: 3,
}
MISSING_INFORMATION_CODES = {
    "LOCAL_ATTESTATION_MISSING",
    "CORE_LOCAL_ATTESTATION_REQUIRED",
    "LOCAL_ATTESTATION_FIELD_MISSING",
    "GIT_DIFF_ATTESTATION_MISSING",
}


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _same_json_value(left: Any, right: Any) -> bool:
    return _canonical_hash(left) == _canonical_hash(right)


class GuardianApplication:
    """当前已经接通正式上下文读取和训练前检查，后续工具继续明确失败。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        project_repository: SqlAlchemyProjectRepository,
        plan_check_repository: SqlAlchemyPlanCheckRepository,
    ) -> None:
        self._session_factory = session_factory
        self._projects = project_repository
        self._plan_checks = plan_check_repository

    def project_get_context(
        self, *, project_id: UUID, identity: RequestIdentity
    ) -> ProjectContextBundle:
        if "project:read" not in identity.scopes:
            raise AuthorizationError("Token 缺少 project:read scope")
        if identity.project_id != project_id:
            raise AuthorizationError("MCP Token 未绑定当前项目")
        with self._session_factory() as session:
            return self._projects.load_context_bundle(
                session,
                project_id=project_id,
                user_id=identity.user_id,
                team_id=identity.team_id,
            )

    def experiment_check_plan(
        self, command: ExperimentCheckPlanCommand, identity: RequestIdentity
    ) -> ExperimentCheckPlanResult:
        if "experiment:check" not in identity.scopes:
            raise AuthorizationError("Token 缺少 experiment:check scope")
        if identity.project_id != command.project_id:
            raise AuthorizationError("MCP Token 未绑定当前项目")

        request_hash = _canonical_hash(command.model_dump(mode="json", exclude={"idempotency_key"}))
        try:
            with self._session_factory() as session, session.begin():
                self._projects.require_project_member(
                    session,
                    project_id=command.project_id,
                    user_id=identity.user_id,
                    team_id=identity.team_id,
                )
                existing = self._plan_checks.find_by_idempotency(
                    session,
                    requester_id=identity.user_id,
                    idempotency_key=command.idempotency_key,
                )
                if existing is not None:
                    return self._plan_checks.replay(existing, request_hash=request_hash)

                bundle = self._projects.load_context_bundle(
                    session,
                    project_id=command.project_id,
                    user_id=identity.user_id,
                    team_id=identity.team_id,
                )
                intent = bundle.active_intent
                intent_payload = bundle.intent_payload
                if intent is None or intent_payload is None:
                    raise ConflictError("项目没有可用于配置检查的 Active Intent")
                if intent.intent_id != command.experiment_intent_id:
                    raise ConflictError("请求的 Experiment Intent 不是当前 Active 版本")

                pending_constraints = self._projects.load_pending_constraints(
                    session,
                    project_id=command.project_id,
                    context_id=bundle.context.context_id,
                    context_version=bundle.context.version,
                    intent_id=intent.intent_id,
                    intent_version=intent.version,
                )
                constraints = [*bundle.constraints, *pending_constraints]
                evaluation = evaluate_plan(
                    PlanEvaluationInput(
                        baseline_config=bundle.context_payload.active_config,
                        candidate=command.configuration,
                        constraints=constraints,
                        allowed_variable_paths=set(intent_payload.allowed_variables),
                        local_attestation=command.local_attestation,
                        experiment_mode=intent.mode,
                        git_commit=command.git_commit,
                        run_command=command.command,
                    )
                )
                risk_level = max(
                    (item.severity for item in evaluation.risks),
                    key=RISK_PRIORITY.__getitem__,
                    default=RiskSeverity.LOW,
                )
                missing_information = sorted(
                    {
                        item.field_path or "local_attestation"
                        for item in evaluation.risks
                        if item.code in MISSING_INFORMATION_CODES
                    }
                )

                record = PlanCheck(
                    project_id=command.project_id,
                    intent_id=intent.intent_id,
                    context_id=bundle.context.context_id,
                    context_version=bundle.context.version,
                    intent_version=intent.version,
                    experiment_mode=intent.mode,
                    requester_id=identity.user_id,
                    idempotency_key=command.idempotency_key,
                    request_hash=request_hash,
                    input_config_hash=evaluation.config_hash,
                    parsed_config=evaluation.parsed_config,
                    git_commit=command.git_commit,
                    command=command.command,
                    local_attestation=command.local_attestation.model_dump(mode="json"),
                    constraint_snapshot=[item.model_dump(mode="json") for item in constraints],
                    planned_changes=[item.model_dump(mode="json") for item in evaluation.changes],
                    check_result=evaluation.check_result,
                    approval_status=evaluation.approval_status,
                    risk_level=risk_level,
                    report={},
                )
                session.add(record)
                session.flush()

                result = ExperimentCheckPlanResult(
                    plan_check_id=record.id,
                    project_id=command.project_id,
                    context_id=bundle.context.context_id,
                    context_version=bundle.context.version,
                    experiment_intent_id=intent.intent_id,
                    intent_version=intent.version,
                    experiment_mode=intent.mode,
                    risk_level=risk_level,
                    missing_information=missing_information,
                    can_create_manifest=(evaluation.check_result is CheckResult.PASS),
                    **evaluation.model_dump(),
                )
                record.report = result.model_dump(mode="json")
                session.flush()
                return result
        except ConfigurationError as exc:
            raise InputValidationError(str(exc)) from exc
        except IntegrityError as exc:
            replay = self._replay_plan_after_integrity_conflict(
                requester_id=identity.user_id,
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
            raise ConflictError("Plan Check 与现有数据冲突") from exc
        except DBAPIError as exc:
            sqlstate = getattr(exc.orig, "sqlstate", None)
            if sqlstate == "40001":
                raise ServiceUnavailableError(
                    "CockroachDB 事务发生并发冲突，请使用相同 Idempotency-Key 重试"
                ) from exc
            raise ServiceUnavailableError("数据库暂时不可用") from exc

    def _replay_plan_after_integrity_conflict(
        self, *, requester_id: UUID, idempotency_key: UUID, request_hash: str
    ) -> ExperimentCheckPlanResult | None:
        with self._session_factory() as session:
            existing = self._plan_checks.find_by_idempotency(
                session,
                requester_id=requester_id,
                idempotency_key=idempotency_key,
            )
            if existing is None:
                return None
            return self._plan_checks.replay(existing, request_hash=request_hash)

    @staticmethod
    def run_manifest_create(
        *, plan_check_id: UUID, actor_id: UUID, idempotency_key: UUID
    ) -> Mapping[str, Any]:
        del plan_check_id, actor_id, idempotency_key
        raise FeatureUnavailableError("run_manifest_create 当前阶段尚未实现")

    @staticmethod
    def submission_prepare(
        *,
        project_id: UUID,
        run_manifest_id: UUID,
        actor_id: UUID,
        idempotency_key: UUID,
        files: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        del project_id, run_manifest_id, actor_id, idempotency_key, files
        raise FeatureUnavailableError("submission_prepare 当前阶段尚未实现")

    @staticmethod
    def submission_finalize(
        *,
        submission_id: UUID,
        actor_id: UUID,
        idempotency_key: UUID,
        uploaded_files: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        del submission_id, actor_id, idempotency_key, uploaded_files
        raise FeatureUnavailableError("submission_finalize 当前阶段尚未实现")

    @staticmethod
    def experiments_query(_: ExperimentQueryCommand) -> Sequence[ExperimentQueryResult]:
        raise FeatureUnavailableError("experiments_query 当前阶段尚未实现")


class ProjectAdministrationService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        project_repository: SqlAlchemyProjectRepository,
    ) -> None:
        self._session_factory = session_factory
        self._projects = project_repository

    def initialize_project(
        self,
        *,
        identity: RequestIdentity,
        idempotency_key: UUID,
        request: ProjectInitializeRequest,
    ) -> ProjectInitializeResponse:
        if "project:initialize" not in identity.scopes:
            raise AuthorizationError("Token 缺少 project:initialize scope")
        request_hash = _canonical_hash(request.model_dump(mode="json"))
        self._validate_formal_configuration(request)

        try:
            with self._session_factory() as session, session.begin():
                existing = self._get_idempotency(session, identity.user_id, idempotency_key)
                if existing is not None:
                    return self._replay(existing, request_hash)

                self._projects.require_member(
                    session,
                    user_id=identity.user_id,
                    team_id=identity.team_id,
                    allowed_roles={TeamRole.OWNER},
                )
                duplicate = session.scalar(
                    select(Project).where(
                        Project.team_id == identity.team_id,
                        Project.name == request.project.name,
                    )
                )
                if duplicate is not None:
                    raise ConflictError("团队中已存在同名项目")

                result = self._create_bundle(session, identity, request)
                session.add(
                    IdempotencyRecord(
                        actor_id=identity.user_id,
                        operation=INITIALIZE_OPERATION,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                        response_snapshot=result.model_dump(mode="json"),
                        operation_status=IdempotencyOperationStatus.COMPLETED,
                        expires_at=datetime.now(UTC) + timedelta(days=7),
                    )
                )
                return result
        except IntegrityError as exc:
            replay = self._replay_after_integrity_conflict(
                identity.user_id, idempotency_key, request_hash
            )
            if replay is not None:
                return replay
            raise ConflictError("项目初始化与现有数据冲突") from exc
        except DBAPIError as exc:
            sqlstate = getattr(exc.orig, "sqlstate", None)
            if sqlstate == "40001":
                raise ServiceUnavailableError(
                    "CockroachDB 事务发生并发冲突，请使用相同 Idempotency-Key 重试"
                ) from exc
            raise ServiceUnavailableError("数据库暂时不可用") from exc

    @staticmethod
    def _validate_formal_configuration(request: ProjectInitializeRequest) -> None:
        flattened = flatten_configuration(request.context.active_config)
        for constraint in request.constraints:
            if constraint.parameter_path not in flattened:
                raise InputValidationError(
                    f"约束路径 {constraint.parameter_path} 不存在于 active_config"
                )
            if not _same_json_value(
                flattened[constraint.parameter_path], constraint.expected_value
            ):
                raise InputValidationError(
                    f"约束 {constraint.parameter_path} 的 expected_value 与 active_config 不一致"
                )

    @staticmethod
    def _get_idempotency(
        session: Session, actor_id: UUID, idempotency_key: UUID
    ) -> IdempotencyRecord | None:
        return session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.actor_id == actor_id,
                IdempotencyRecord.operation == INITIALIZE_OPERATION,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
        )

    @staticmethod
    def _replay(record: IdempotencyRecord, request_hash: str) -> ProjectInitializeResponse:
        if record.request_hash != request_hash:
            raise ConflictError("相同 Idempotency-Key 已用于不同请求")
        if (
            record.operation_status is not IdempotencyOperationStatus.COMPLETED
            or record.response_snapshot is None
        ):
            raise ConflictError("相同 Idempotency-Key 的操作仍在处理中")
        return ProjectInitializeResponse.model_validate(record.response_snapshot)

    def _replay_after_integrity_conflict(
        self, actor_id: UUID, idempotency_key: UUID, request_hash: str
    ) -> ProjectInitializeResponse | None:
        with self._session_factory() as session:
            record = self._get_idempotency(session, actor_id, idempotency_key)
            return None if record is None else self._replay(record, request_hash)

    def _create_bundle(
        self,
        session: Session,
        identity: RequestIdentity,
        request: ProjectInitializeRequest,
    ) -> ProjectInitializeResponse:
        now = datetime.now(UTC)
        project = Project(
            team_id=identity.team_id,
            name=request.project.name,
            description=request.project.description,
            repository_url=request.project.repository_url,
            active=True,
        )
        session.add(project)
        session.flush()

        context_input = request.context
        context = ProjectContext(
            project_id=project.id,
            version=1,
            goal=context_input.goal,
            non_goals=context_input.non_goals,
            mainline_model=context_input.mainline_model,
            baseline=context_input.baseline,
            dataset=context_input.dataset,
            protocol=context_input.protocol,
            primary_metric=context_input.primary_metric,
            default_seeds=context_input.default_seeds,
            active_branch=context_input.active_branch,
            active_config=context_input.active_config,
            deprecated_items=context_input.deprecated_items,
            key_decisions=context_input.key_decisions,
            change_reason=context_input.change_reason,
            status=ContextStatus.ACTIVE,
            created_by=identity.user_id,
            confirmed_by=identity.user_id,
            confirmed_at=now,
            effective_at=now,
        )
        session.add(context)
        session.flush()

        intent_input = request.intent
        intent = ExperimentIntent(
            project_id=project.id,
            context_id=context.id,
            context_version=1,
            version=1,
            experiment_mode=ExperimentMode.FORMAL,
            name=intent_input.name,
            objective=intent_input.objective,
            hypothesis=intent_input.hypothesis,
            allowed_variables=intent_input.allowed_variables,
            controlled_variables=intent_input.controlled_variables,
            expected_outputs=intent_input.expected_outputs,
            acceptance_criteria=intent_input.acceptance_criteria,
            source_type=ConstraintSource.EXPLICIT,
            verification_status=VerificationStatus.CONFIRMED,
            original_message=intent_input.original_message,
            unresolved_ambiguities=[],
            intent_receipt="Owner 已确认该正式实验意图及其允许变量。",
            status=IntentStatus.ACTIVE,
            created_by=identity.user_id,
            confirmed_by=identity.user_id,
            confirmed_at=now,
            activated_by=identity.user_id,
            activated_at=now,
        )
        session.add(intent)
        session.flush()

        for constraint_input in request.constraints:
            intent_scoped = constraint_input.protection_level is ProtectionLevel.EXPERIMENT_VARIABLE
            allowed_range = {
                "allowed_values": constraint_input.allowed_values,
                "minimum": constraint_input.minimum,
                "maximum": constraint_input.maximum,
            }
            session.add(
                ProtectedParameter(
                    project_id=project.id,
                    context_id=context.id,
                    context_version=1,
                    intent_id=intent.id if intent_scoped else None,
                    intent_version=1 if intent_scoped else None,
                    version=1,
                    parameter_path=constraint_input.parameter_path,
                    protection_level=constraint_input.protection_level,
                    expected_value=constraint_input.expected_value,
                    allowed_range=allowed_range,
                    reason=constraint_input.reason,
                    source_type=ConstraintSource.EXPLICIT,
                    verification_status=VerificationStatus.CONFIRMED,
                    original_message=constraint_input.original_message,
                    created_by=identity.user_id,
                    confirmed_by=identity.user_id,
                    confirmed_at=now,
                    active=True,
                )
            )
        session.flush()

        session.add(
            AuditLog(
                team_id=identity.team_id,
                project_id=project.id,
                actor_type="USER",
                actor_id=identity.user_id,
                action="PROJECT_INITIALIZED",
                target_type="PROJECT",
                target_id=project.id,
                after_value={
                    "context_id": str(context.id),
                    "context_version": 1,
                    "intent_id": str(intent.id),
                    "intent_version": 1,
                    "constraint_count": len(request.constraints),
                },
            )
        )
        session.flush()
        bundle = self._projects.load_context_bundle(
            session,
            project_id=project.id,
            user_id=identity.user_id,
            team_id=identity.team_id,
        )
        return ProjectInitializeResponse(project_id=project.id, context_bundle=bundle)
