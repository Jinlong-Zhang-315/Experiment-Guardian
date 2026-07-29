"""当前阶段的应用用例实现。"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.async_review import REVIEW_DISCLAIMER
from experiment_guardian.application.async_summary import (
    SUMMARY_DISCLAIMER,
    SubmissionReviewScheduler,
    SubmissionSummaryScheduler,
)
from experiment_guardian.application.errors import (
    AuthorizationError,
    ConflictError,
    InputValidationError,
    RecentAuthenticationRequiredError,
    ResourceNotFoundError,
    ServiceUnavailableError,
)
from experiment_guardian.application.experiments import ExperimentQueryService
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.ports import ArtifactStorage
from experiment_guardian.application.submission_analysis import SubmissionAnalysisService
from experiment_guardian.application.transactions import run_with_serialization_retry
from experiment_guardian.domain.administration import (
    PlanCheckDecisionRequest,
    PlanCheckDecisionResult,
    ProjectInitializeRequest,
    ProjectInitializeResponse,
)
from experiment_guardian.domain.contracts import (
    ArtifactUploadTarget,
    ArtifactVerificationIssue,
    ArtifactVerificationReceipt,
    EmbeddingMetadata,
    ExperimentCheckPlanCommand,
    ExperimentCheckPlanResult,
    ExperimentQueryCommand,
    ExperimentQueryResult,
    GeneratedSummary,
    PlanEvaluationInput,
    ProjectContextBundle,
    RiskItem,
    RunManifestResult,
    StoredObjectMetadata,
    SubmissionFinalizeCommand,
    SubmissionFinalizeResult,
    SubmissionPrepareCommand,
    SubmissionPrepareResult,
    SubmissionReceipt,
    SubmissionStatusResult,
    WorkflowJobReceipt,
)
from experiment_guardian.domain.enums import (
    ApprovalDecision,
    ApprovalStatus,
    ApprovalTargetType,
    ArtifactType,
    ArtifactVerificationIssueCode,
    CheckResult,
    ConstraintSource,
    ContextStatus,
    ExperimentMode,
    ExperimentPlanDecisionType,
    IdempotencyOperationStatus,
    IntentStatus,
    ProtectionLevel,
    RiskSeverity,
    SubmissionStatus,
    SubmittedRunStatus,
    TeamRole,
    UploadVerificationResult,
    VerificationStatus,
    WorkflowJobStatus,
    WorkflowStatus,
    WorkflowStep,
)
from experiment_guardian.domain.experiment_plan import formal_policy_snapshot
from experiment_guardian.domain.invariant_check import (
    ApprovedInvariantSnapshot,
    FinalRunEvidence,
    InvariantAttestation,
    InvariantCheckReport,
    build_approved_invariant_snapshot,
    evaluate_pre_run_invariants,
)
from experiment_guardian.domain.plan_check import (
    ConfigurationError,
    evaluate_plan,
    flatten_configuration,
)
from experiment_guardian.domain.run_manifest import build_manifest_content, canonical_json_hash
from experiment_guardian.infrastructure.models import (
    ApprovalRecord,
    Artifact,
    AuditLog,
    ExperimentIntent,
    ExperimentPlan,
    ExperimentPlanDecision,
    ExperimentPlanRevision,
    ExperimentSubmission,
    IdempotencyRecord,
    PlanCheck,
    Project,
    ProjectContext,
    ProtectedParameter,
    RunManifest,
    WorkflowJob,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyGovernanceRepository,
    SqlAlchemyPlanCheckRepository,
    SqlAlchemyProjectRepository,
    SqlAlchemySubmissionRepository,
    SqlAlchemyWorkflowRepository,
)

INITIALIZE_OPERATION = "project.initialize"
PLAN_DECISION_OPERATION = "plan_check.decision"
RUN_MANIFEST_OPERATION = "run_manifest.create"
SUBMISSION_PREPARE_OPERATION = "submission.prepare"
SUBMISSION_FINALIZE_OPERATION = "submission.finalize"
SUBMISSION_FINALIZE_FAILURE_ACTION = "submission.finalize.failed"
SUBMISSION_ANALYSIS_RESUME_ACTION = "submission.analysis.resume"
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


@dataclass(frozen=True, slots=True)
class _PreparedArtifact:
    id: UUID
    filename: str
    artifact_type: ArtifactType
    mime_type: str
    size_bytes: int
    sha256: str
    s3_key: str


@dataclass(frozen=True, slots=True)
class _PreparedSubmission:
    id: UUID
    project_id: UUID
    run_manifest_id: UUID
    manifest_hash: str
    status: SubmissionStatus
    experiment_status: SubmittedRunStatus
    metrics_summary: dict[str, float]
    created_at: datetime
    artifacts: tuple[_PreparedArtifact, ...]


@dataclass(frozen=True, slots=True)
class _FinalizeSnapshot:
    submission_id: UUID
    project_id: UUID
    declaration_hash: str
    artifacts: tuple[_PreparedArtifact, ...]


class GuardianApplication:
    """已接通 Context、Plan Check、Manifest、Submission Prepare/Finalize 五个用例。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        project_repository: SqlAlchemyProjectRepository,
        plan_check_repository: SqlAlchemyPlanCheckRepository,
        governance_repository: SqlAlchemyGovernanceRepository | None = None,
        submission_repository: SqlAlchemySubmissionRepository | None = None,
        artifact_storage: ArtifactStorage | None = None,
        upload_url_ttl_seconds: int = 900,
        workflow_repository: SqlAlchemyWorkflowRepository | None = None,
        worker_max_attempts: int = 5,
        experiment_query_service: ExperimentQueryService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._projects = project_repository
        self._plan_checks = plan_check_repository
        self._governance = governance_repository or SqlAlchemyGovernanceRepository()
        self._submissions = submission_repository or SqlAlchemySubmissionRepository()
        self._artifact_storage = artifact_storage
        self._upload_url_ttl_seconds = upload_url_ttl_seconds
        self._workflows = workflow_repository or SqlAlchemyWorkflowRepository()
        self._summary_scheduler = SubmissionSummaryScheduler(
            session_factory,
            self._workflows,
            max_attempts=worker_max_attempts,
        )
        self._review_scheduler = SubmissionReviewScheduler(
            session_factory,
            self._workflows,
            max_attempts=worker_max_attempts,
        )
        self._experiment_queries = experiment_query_service
        self._submission_analysis = (
            SubmissionAnalysisService(
                session_factory,
                self._submissions,
                artifact_storage,
                self._summary_scheduler,
            )
            if artifact_storage is not None
            else None
        )

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
        return run_with_serialization_retry(
            lambda: self._experiment_check_plan_once(command, identity)
        )

    def _experiment_check_plan_once(
        self, command: ExperimentCheckPlanCommand, identity: RequestIdentity
    ) -> ExperimentCheckPlanResult:
        if "experiment:check" not in identity.scopes:
            raise AuthorizationError("Token 缺少 experiment:check scope")
        if identity.project_id != command.project_id:
            raise AuthorizationError("MCP Token 未绑定当前项目")

        request_hash = _canonical_hash(command.model_dump(mode="json", exclude={"idempotency_key"}))
        try:
            with self._session_factory() as session, session.begin():
                project = self._projects.require_project_member(
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

                experiment_plan_snapshot: ApprovedInvariantSnapshot | None = None
                invariant_report: InvariantCheckReport | None = None
                if command.experiment_plan_decision_id is not None:
                    experiment_plan_snapshot = self._load_approved_plan_snapshot(
                        session=session,
                        command=command,
                        identity=identity,
                        bundle=bundle,
                        project=project,
                    )

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
                if experiment_plan_snapshot is not None:
                    try:
                        attestations = [
                            InvariantAttestation.model_validate(item)
                            for item in command.invariant_attestations
                        ]
                        checkpoint = self._applicable_evidence_value(
                            command.local_attestation.checkpoint_path
                        )
                        invariant_report = evaluate_pre_run_invariants(
                            snapshot=experiment_plan_snapshot,
                            parsed_config=evaluation.parsed_config,
                            git_commit=command.git_commit,
                            run_command=command.command,
                            checkpoint=checkpoint if isinstance(checkpoint, str) else None,
                            attestations=attestations,
                            deviation_explanation=command.deviation_explanation,
                        )
                    except ValueError as exc:
                        raise InputValidationError(str(exc)) from exc
                    evaluation = self._merge_invariant_evaluation(evaluation, invariant_report)
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
                    | (
                        {
                            item.parameter_path or item.invariant_id
                            for item in invariant_report.checks
                            if item.outcome == "UNVERIFIED"
                        }
                        if invariant_report is not None
                        else set()
                    )
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
                    input_document_hash=evaluation.document_sha256,
                    configuration_document=command.configuration.model_dump(mode="json"),
                    parsed_config=evaluation.parsed_config,
                    context_snapshot={
                        "reference": bundle.context.model_dump(mode="json"),
                        "payload": bundle.context_payload.model_dump(mode="json"),
                    },
                    intent_snapshot={
                        "reference": intent.model_dump(mode="json"),
                        "payload": intent_payload.model_dump(mode="json"),
                    },
                    git_commit=command.git_commit,
                    command=command.command,
                    local_attestation=command.local_attestation.model_dump(mode="json"),
                    constraint_snapshot=[item.model_dump(mode="json") for item in constraints],
                    planned_changes=[item.model_dump(mode="json") for item in evaluation.changes],
                    check_result=evaluation.check_result,
                    approval_status=evaluation.approval_status,
                    risk_level=risk_level,
                    report={},
                    experiment_plan_decision_id=command.experiment_plan_decision_id,
                    experiment_plan_snapshot=(
                        experiment_plan_snapshot.model_dump(mode="json")
                        if experiment_plan_snapshot is not None
                        else None
                    ),
                    invariant_check=(
                        invariant_report.model_dump(mode="json")
                        if invariant_report is not None
                        else None
                    ),
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
                    experiment_plan_decision_id=command.experiment_plan_decision_id,
                    experiment_plan_trace=(
                        experiment_plan_snapshot.trace.model_dump(mode="json")
                        if experiment_plan_snapshot is not None
                        else None
                    ),
                    invariant_check=(
                        invariant_report.model_dump(mode="json")
                        if invariant_report is not None
                        else None
                    ),
                    **evaluation.model_dump(),
                )
                record.report = result.model_dump(
                    mode="json", exclude={"approval_status", "can_create_manifest"}
                )
                session.add(
                    AuditLog(
                        team_id=project.team_id,
                        project_id=project.id,
                        actor_type="USER",
                        actor_id=identity.user_id,
                        action="experiment_check_plan.created",
                        target_type="PLAN_CHECK",
                        target_id=record.id,
                        before_value=None,
                        after_value={
                            "check_result": record.check_result.value,
                            "approval_status": record.approval_status.value,
                            "experiment_plan_decision_id": (
                                str(record.experiment_plan_decision_id)
                                if record.experiment_plan_decision_id
                                else None
                            ),
                            "invariant_status": (
                                invariant_report.overall_status if invariant_report else None
                            ),
                            "credential_id": str(identity.token_id),
                            "authentication_method": identity.authentication_method,
                        },
                    )
                )
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

    def _load_approved_plan_snapshot(
        self,
        *,
        session: Session,
        command: ExperimentCheckPlanCommand,
        identity: RequestIdentity,
        bundle: ProjectContextBundle,
        project: Project,
    ) -> ApprovedInvariantSnapshot:
        decision_id = command.experiment_plan_decision_id
        assert decision_id is not None
        decision = session.get(ExperimentPlanDecision, decision_id)
        revision = (
            session.get(ExperimentPlanRevision, decision.revision_id)
            if decision is not None
            else None
        )
        plan = session.get(ExperimentPlan, decision.plan_id) if decision is not None else None
        if (
            decision is None
            or revision is None
            or plan is None
            or plan.project_id != command.project_id
            or plan.team_id != project.team_id
            or revision.plan_id != plan.id
            or decision.revision_id != revision.id
        ):
            raise ResourceNotFoundError("项目中不存在该已批准实验计划决定")
        if decision.decision not in {
            ExperimentPlanDecisionType.APPROVED,
            ExperimentPlanDecisionType.CONDITIONALLY_APPROVED,
        }:
            raise ConflictError("实验计划尚未获得可执行的用户批准")
        role = self._projects.require_member(
            session, user_id=identity.user_id, team_id=project.team_id
        )
        if role is TeamRole.RESEARCHER and plan.created_by != identity.user_id:
            raise AuthorizationError("Researcher 只能执行自己创建的已批准实验计划")
        intent = bundle.active_intent
        if (
            revision.context_id != bundle.context.context_id
            or revision.context_version != bundle.context.version
            or revision.intent_id != command.experiment_intent_id
            or intent is None
            or revision.intent_version != intent.version
        ):
            raise ConflictError("实验计划批准版本与当前 Context 或 Intent 不一致")
        _, policy_hash = formal_policy_snapshot(bundle)
        if policy_hash != revision.policy_hash:
            raise ConflictError("实验计划所依据的正式策略已经变化，必须重新审核")
        approved_plan = decision.approved_snapshot.get("plan")
        if (
            not isinstance(approved_plan, dict)
            or approved_plan.get("revision_id") != str(revision.id)
            or approved_plan.get("revision") != revision.revision
            or decision.review_hash != decision.approved_snapshot.get("review_hash")
        ):
            raise ConflictError("实验计划决定的不可变批准快照不完整")
        try:
            return build_approved_invariant_snapshot(
                plan_id=plan.id,
                revision_id=revision.id,
                revision=revision.revision,
                decision_id=decision.id,
                decision_hash=decision.decision_hash,
                review_hash=decision.review_hash,
                policy_hash=revision.policy_hash,
                approved_snapshot=decision.approved_snapshot,
            )
        except ValueError as exc:
            raise ConflictError(f"实验计划批准快照无效: {exc}") from exc

    @staticmethod
    def _applicable_evidence_value(evidence: Any) -> Any | None:
        if evidence is None or evidence.applicability.value != "APPLICABLE":
            return None
        return evidence.value

    @staticmethod
    def _merge_invariant_evaluation(evaluation: Any, report: InvariantCheckReport) -> Any:
        risks = list(evaluation.risks)
        for item in report.checks:
            if item.outcome not in {"UNVERIFIED", "VIOLATED"}:
                continue
            critical = item.outcome == "VIOLATED" and item.blocking
            risks.append(
                RiskItem(
                    code=(
                        "APPROVED_PLAN_INVARIANT_VIOLATED"
                        if critical
                        else "APPROVED_PLAN_EXPLANATION_REQUIRED"
                    ),
                    severity=RiskSeverity.CRITICAL if critical else RiskSeverity.HIGH,
                    message=item.message,
                    field_path=item.parameter_path or item.invariant_id,
                    current_value=item.actual_value,
                    expected_value=item.expected_value,
                    impact=(
                        "实际运行证据超出用户批准的关键边界。"
                        if critical
                        else "该差异需要在现有 Plan Check 审批中明确处理。"
                    ),
                    blocking=critical,
                    evidence_type=item.evidence_type,
                    evidence_source=item.evidence_source,
                    collected_at=item.collected_at,
                    collection_tool=item.collection_tool,
                    recommendation=(
                        "提交新的实验计划 revision 并重新审核。"
                        if critical
                        else "补充声明或由 Owner 在现有计划审批页决策。"
                    ),
                )
            )
        if evaluation.check_result is CheckResult.BLOCKED or (
            report.overall_status == "CRITICAL_DEVIATION"
        ):
            check_result = CheckResult.BLOCKED
            approval_status = ApprovalStatus.NOT_REQUIRED
        elif evaluation.check_result is CheckResult.NEEDS_APPROVAL or (
            report.overall_status == "NEEDS_EXPLANATION"
        ):
            check_result = CheckResult.NEEDS_APPROVAL
            approval_status = ApprovalStatus.PENDING
        else:
            check_result = CheckResult.PASS
            approval_status = ApprovalStatus.NOT_REQUIRED
        return evaluation.model_copy(
            update={
                "risks": risks,
                "check_result": check_result,
                "approval_status": approval_status,
            }
        )

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

    def run_manifest_create(
        self, *, plan_check_id: UUID, identity: RequestIdentity, idempotency_key: UUID
    ) -> RunManifestResult:
        return run_with_serialization_retry(
            lambda: self._run_manifest_create_once(
                plan_check_id=plan_check_id,
                identity=identity,
                idempotency_key=idempotency_key,
            )
        )

    def _run_manifest_create_once(
        self, *, plan_check_id: UUID, identity: RequestIdentity, idempotency_key: UUID
    ) -> RunManifestResult:
        if "manifest:create" not in identity.scopes:
            raise AuthorizationError("Token 缺少 manifest:create scope")
        request_hash = _canonical_hash({"plan_check_id": str(plan_check_id)})
        try:
            with self._session_factory() as session, session.begin():
                plan = self._governance.get_plan_for_update(session, plan_check_id)
                if plan is None:
                    raise ResourceNotFoundError("Plan Check 不存在")
                if identity.project_id != plan.project_id:
                    raise AuthorizationError("MCP Token 未绑定 Plan Check 所属项目")
                project = self._projects.require_project_member(
                    session,
                    project_id=plan.project_id,
                    user_id=identity.user_id,
                    team_id=identity.team_id,
                )
                existing_idempotency = self._governance.find_idempotency(
                    session,
                    actor_id=identity.user_id,
                    operation=RUN_MANIFEST_OPERATION,
                    idempotency_key=idempotency_key,
                )
                if existing_idempotency is not None:
                    return self._replay_manifest(existing_idempotency, request_hash)
                if self._governance.find_manifest_by_plan(session, plan.id) is not None:
                    raise ConflictError("该 Plan Check 已使用其他 Idempotency-Key 创建 Manifest")

                approval = self._eligible_approval(session, plan)
                self._validate_experiment_plan_binding_for_manifest(session, plan)
                content = build_manifest_content(plan, approval.id if approval else None)
                manifest = RunManifest(
                    schema_version=content["schema_version"],
                    project_id=plan.project_id,
                    intent_id=plan.intent_id,
                    plan_check_id=plan.id,
                    approval_record_id=approval.id if approval else None,
                    context_id=plan.context_id,
                    context_version=plan.context_version,
                    intent_version=plan.intent_version,
                    experiment_mode=plan.experiment_mode,
                    idempotency_key=idempotency_key,
                    config_snapshot=content["config_snapshot"],
                    config_hash=content["config_hash"],
                    config_document_hash=content["config_document_hash"],
                    git_branch=content["git_branch"],
                    git_commit=content["git_commit"],
                    git_diff_hash=content["git_diff_hash"],
                    dataset=content["dataset"],
                    protocol=content["protocol"],
                    seed=content["seed"],
                    checkpoint=content["checkpoint"],
                    command=content["command"],
                    environment=content["environment"],
                    evidence_snapshot=content["evidence_snapshot"],
                    manifest_hash=canonical_json_hash(content),
                    created_by=identity.user_id,
                )
                session.add(manifest)
                session.flush()
                result = self._manifest_result(manifest)
                session.add_all(
                    [
                        AuditLog(
                            team_id=project.team_id,
                            project_id=plan.project_id,
                            actor_type="USER",
                            actor_id=identity.user_id,
                            action=RUN_MANIFEST_OPERATION,
                            target_type="RUN_MANIFEST",
                            target_id=manifest.id,
                            before_value=None,
                            after_value=result.model_dump(mode="json"),
                        ),
                        IdempotencyRecord(
                            actor_id=identity.user_id,
                            operation=RUN_MANIFEST_OPERATION,
                            idempotency_key=idempotency_key,
                            request_hash=request_hash,
                            response_snapshot=result.model_dump(mode="json"),
                            operation_status=IdempotencyOperationStatus.COMPLETED,
                            expires_at=datetime.now(UTC) + timedelta(days=7),
                        ),
                    ]
                )
                return result
        except IntegrityError as exc:
            replay = self._replay_manifest_after_integrity(
                actor_id=identity.user_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
            raise ConflictError("Manifest 与现有数据冲突") from exc

    def _eligible_approval(self, session: Session, plan: PlanCheck) -> ApprovalRecord | None:
        if (
            plan.check_result is CheckResult.PASS
            and plan.approval_status is ApprovalStatus.NOT_REQUIRED
        ):
            return None
        if (
            plan.check_result is CheckResult.NEEDS_APPROVAL
            and plan.approval_status is ApprovalStatus.APPROVED
        ):
            approval = self._governance.find_plan_approval(session, plan.id)
            if (
                approval is None
                or approval.project_id != plan.project_id
                or approval.status is not ApprovalDecision.APPROVED
            ):
                raise ConflictError("Plan Check 的批准状态缺少匹配的不可变审批记录")
            return approval
        raise ConflictError("Plan Check 当前状态不允许创建 Manifest")

    @staticmethod
    def _validate_experiment_plan_binding_for_manifest(session: Session, plan: PlanCheck) -> None:
        if plan.experiment_plan_decision_id is None:
            return
        decision = session.get(ExperimentPlanDecision, plan.experiment_plan_decision_id)
        if decision is None or decision.decision not in {
            ExperimentPlanDecisionType.APPROVED,
            ExperimentPlanDecisionType.CONDITIONALLY_APPROVED,
        }:
            raise ConflictError("Plan Check 关联的实验计划决定已经无效")
        try:
            snapshot = ApprovedInvariantSnapshot.model_validate(plan.experiment_plan_snapshot)
            report = InvariantCheckReport.model_validate(plan.invariant_check)
        except Exception as exc:
            raise ConflictError("Plan Check 缺少可追溯的实验计划不变量快照") from exc
        if (
            snapshot.trace.decision_id != decision.id
            or snapshot.trace.decision_hash != decision.decision_hash
            or snapshot.trace.revision_id != decision.revision_id
            or report.trace != snapshot.trace
            or report.stage != "PRE_RUN"
        ):
            raise ConflictError("Plan Check 的实验计划决定或不变量报告已失配")

    @staticmethod
    def _manifest_result(record: RunManifest) -> RunManifestResult:
        plan_trace = None
        invariant_check = None
        if record.schema_version == 2 and isinstance(record.evidence_snapshot, dict):
            plan_snapshot = record.evidence_snapshot.get("experiment_plan")
            if isinstance(plan_snapshot, dict):
                plan_trace = plan_snapshot.get("trace")
            candidate_check = record.evidence_snapshot.get("invariant_check")
            if isinstance(candidate_check, dict):
                invariant_check = candidate_check
        return RunManifestResult(
            schema_version=record.schema_version,
            manifest_id=record.id,
            project_id=record.project_id,
            plan_check_id=record.plan_check_id,
            approval_record_id=record.approval_record_id,
            context_id=record.context_id,
            context_version=record.context_version,
            experiment_intent_id=record.intent_id,
            intent_version=record.intent_version,
            experiment_mode=record.experiment_mode,
            config_snapshot=record.config_snapshot,
            config_hash=record.config_hash,
            config_document_hash=record.config_document_hash,
            git_branch=record.git_branch,
            git_commit=record.git_commit,
            git_diff_hash=record.git_diff_hash,
            dataset=record.dataset,
            protocol=record.protocol,
            seed=record.seed,
            checkpoint=record.checkpoint,
            command=record.command,
            environment=record.environment,
            evidence_snapshot=record.evidence_snapshot,
            experiment_plan_trace=plan_trace,
            invariant_check=invariant_check,
            manifest_hash=record.manifest_hash,
            created_by=record.created_by,
            created_at=record.created_at,
        )

    @staticmethod
    def _replay_manifest(record: IdempotencyRecord, request_hash: str) -> RunManifestResult:
        if record.request_hash != request_hash:
            raise ConflictError("相同 Idempotency-Key 已用于不同的 Manifest 请求")
        if (
            record.operation_status is not IdempotencyOperationStatus.COMPLETED
            or record.response_snapshot is None
        ):
            raise ConflictError("相同 Idempotency-Key 的 Manifest 操作仍在处理中")
        return RunManifestResult.model_validate(record.response_snapshot)

    def _replay_manifest_after_integrity(
        self, *, actor_id: UUID, idempotency_key: UUID, request_hash: str
    ) -> RunManifestResult | None:
        with self._session_factory() as session:
            record = self._governance.find_idempotency(
                session,
                actor_id=actor_id,
                operation=RUN_MANIFEST_OPERATION,
                idempotency_key=idempotency_key,
            )
            return None if record is None else self._replay_manifest(record, request_hash)

    def submission_prepare(
        self, command: SubmissionPrepareCommand, identity: RequestIdentity
    ) -> SubmissionPrepareResult:
        prepared = run_with_serialization_retry(
            lambda: self._submission_prepare_once(command, identity)
        )
        return self._issue_upload_targets(prepared)

    def _submission_prepare_once(
        self, command: SubmissionPrepareCommand, identity: RequestIdentity
    ) -> _PreparedSubmission:
        if "submission:create" not in identity.scopes:
            raise AuthorizationError("Token 缺少 submission:create scope")
        if identity.project_id != command.project_id:
            raise AuthorizationError("MCP Token 未绑定当前项目")

        request_hash = _canonical_hash(command.model_dump(mode="json", exclude={"idempotency_key"}))
        try:
            with self._session_factory() as session, session.begin():
                project = self._projects.require_project_member(
                    session,
                    project_id=command.project_id,
                    user_id=identity.user_id,
                    team_id=identity.team_id,
                )
                existing = self._governance.find_idempotency(
                    session,
                    actor_id=identity.user_id,
                    operation=SUBMISSION_PREPARE_OPERATION,
                    idempotency_key=command.idempotency_key,
                )
                if existing is not None:
                    return self._replay_submission(session, existing, request_hash)

                manifest = self._submissions.get_manifest(session, command.run_manifest_id)
                if manifest is None or manifest.project_id != command.project_id:
                    raise ResourceNotFoundError("项目中不存在该 Run Manifest")
                try:
                    final_run_evidence = (
                        FinalRunEvidence.model_validate(command.final_run_evidence)
                        if command.final_run_evidence is not None
                        else None
                    )
                except ValueError as exc:
                    raise InputValidationError(f"最终运行证据无效: {exc}") from exc

                submission = ExperimentSubmission(
                    id=uuid4(),
                    project_id=command.project_id,
                    run_manifest_id=manifest.id,
                    submitted_by=identity.user_id,
                    source_agent=command.source_agent,
                    idempotency_key=command.idempotency_key,
                    request_hash=request_hash,
                    manifest_hash=manifest.manifest_hash,
                    declared_experiment_status=command.experiment_status,
                    declared_metrics=command.metrics_summary,
                    evidence_snapshot=self._submission_evidence(
                        command, manifest, final_run_evidence
                    ),
                    status=SubmissionStatus.RECEIVED,
                )
                session.add(submission)
                # Artifact 没有定义 ORM relationship，因此显式固化父记录，
                # 避免不同数据库的 flush 排序差异导致外键失败。
                session.flush()
                artifacts: list[Artifact] = []
                for item in command.files:
                    artifact_id = uuid4()
                    artifact = Artifact(
                        id=artifact_id,
                        submission_id=submission.id,
                        experiment_id=None,
                        filename=item.filename,
                        mime_type=item.mime_type,
                        size_bytes=item.size_bytes,
                        s3_key=(
                            f"projects/{command.project_id}/submissions/{submission.id}/"
                            f"artifacts/{artifact_id}"
                        ),
                        sha256=item.sha256,
                        artifact_type=item.artifact_type,
                        cloud_hash_verified=False,
                    )
                    session.add(artifact)
                    artifacts.append(artifact)
                session.flush()

                prepared = self._prepared_submission(submission, artifacts)
                session.add_all(
                    [
                        AuditLog(
                            team_id=project.team_id,
                            project_id=command.project_id,
                            actor_type="USER",
                            actor_id=identity.user_id,
                            action=SUBMISSION_PREPARE_OPERATION,
                            target_type="EXPERIMENT_SUBMISSION",
                            target_id=submission.id,
                            before_value=None,
                            after_value={
                                "run_manifest_id": str(manifest.id),
                                "status": submission.status.value,
                                "artifact_ids": [str(item.id) for item in artifacts],
                                "token_id": str(identity.token_id),
                                "source_agent": submission.source_agent,
                            },
                        ),
                        IdempotencyRecord(
                            actor_id=identity.user_id,
                            operation=SUBMISSION_PREPARE_OPERATION,
                            idempotency_key=command.idempotency_key,
                            request_hash=request_hash,
                            response_snapshot={"submission_id": str(submission.id)},
                            operation_status=IdempotencyOperationStatus.COMPLETED,
                            expires_at=datetime.now(UTC) + timedelta(days=7),
                        ),
                    ]
                )
                return prepared
        except IntegrityError as exc:
            replay = self._replay_submission_after_integrity(
                actor_id=identity.user_id,
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
            raise ConflictError("Submission 与现有数据冲突") from exc

    @staticmethod
    def _submission_evidence(
        command: SubmissionPrepareCommand,
        manifest: RunManifest,
        final_run_evidence: FinalRunEvidence | None,
    ) -> dict[str, Any]:
        local_metadata = {
            "evidence_type": "LOCAL_ATTESTED",
            "source": "MCP submission_prepare request",
            "collected_at": command.collected_at.isoformat(),
            "collection_tool": command.source_agent,
        }
        return {
            "experiment_status": {
                **local_metadata,
                "value": command.experiment_status.value,
            },
            "metrics_summary": {**local_metadata, "value": command.metrics_summary},
            "files": [
                {
                    **local_metadata,
                    "filename": item.filename,
                    "artifact_type": item.artifact_type.value,
                    "declared_sha256": item.sha256,
                    "declared_size_bytes": item.size_bytes,
                }
                for item in command.files
            ],
            "run_manifest": {
                "value": {"id": str(manifest.id), "hash": manifest.manifest_hash},
                "evidence_type": "CLOUD_VERIFIED",
                "source": "run_manifests",
                "collected_at": datetime.now(UTC).isoformat(),
                "collection_tool": "experiment-guardian-server",
            },
            "final_run_evidence": (
                final_run_evidence.model_dump(mode="json")
                if final_run_evidence is not None
                else None
            ),
        }

    def _replay_submission(
        self, session: Session, record: IdempotencyRecord, request_hash: str
    ) -> _PreparedSubmission:
        if record.request_hash != request_hash:
            raise ConflictError("相同 Idempotency-Key 已用于不同的 Submission 请求")
        if (
            record.operation_status is not IdempotencyOperationStatus.COMPLETED
            or not record.response_snapshot
            or not isinstance(record.response_snapshot.get("submission_id"), str)
        ):
            raise ConflictError("相同 Idempotency-Key 的 Submission 操作仍在处理中")
        submission = self._submissions.get_submission(
            session, UUID(record.response_snapshot["submission_id"])
        )
        if submission is None or submission.request_hash != request_hash:
            raise ConflictError("Submission 幂等记录与草稿数据不一致")
        artifacts = self._submissions.list_artifacts(session, submission.id)
        if not artifacts:
            raise ConflictError("Submission 缺少 Artifact 上传声明")
        return self._prepared_submission(submission, artifacts)

    def _replay_submission_after_integrity(
        self, *, actor_id: UUID, idempotency_key: UUID, request_hash: str
    ) -> _PreparedSubmission | None:
        with self._session_factory() as session:
            record = self._governance.find_idempotency(
                session,
                actor_id=actor_id,
                operation=SUBMISSION_PREPARE_OPERATION,
                idempotency_key=idempotency_key,
            )
            return (
                None if record is None else self._replay_submission(session, record, request_hash)
            )

    @staticmethod
    def _prepared_submission(
        submission: ExperimentSubmission, artifacts: Sequence[Artifact]
    ) -> _PreparedSubmission:
        # Prepare 的幂等回执描述上传阶段。分析开始后仍应显示“上传已验证”，而不是重新
        # 签发 URL，也不把后续 PROCESSING 状态塞进旧的上传契约。
        upload_status = (
            submission.status
            if submission.status is SubmissionStatus.RECEIVED
            else SubmissionStatus.UPLOAD_VERIFIED
        )
        return _PreparedSubmission(
            id=submission.id,
            project_id=submission.project_id,
            run_manifest_id=submission.run_manifest_id,
            manifest_hash=submission.manifest_hash,
            status=upload_status,
            experiment_status=submission.declared_experiment_status,
            metrics_summary={
                str(name): float(value) for name, value in submission.declared_metrics.items()
            },
            created_at=submission.created_at,
            artifacts=tuple(
                _PreparedArtifact(
                    id=item.id,
                    filename=item.filename,
                    artifact_type=item.artifact_type,
                    mime_type=item.mime_type,
                    size_bytes=item.size_bytes,
                    sha256=item.sha256,
                    s3_key=item.s3_key,
                )
                for item in artifacts
            ),
        )

    def _issue_upload_targets(self, prepared: _PreparedSubmission) -> SubmissionPrepareResult:
        if prepared.status is SubmissionStatus.UPLOAD_VERIFIED:
            return SubmissionPrepareResult(
                submission_id=prepared.id,
                project_id=prepared.project_id,
                run_manifest_id=prepared.run_manifest_id,
                manifest_hash=prepared.manifest_hash,
                status=prepared.status,
                experiment_status=prepared.experiment_status,
                metrics_summary=prepared.metrics_summary,
                artifact_uploads=[],
                created_at=prepared.created_at,
            )
        if prepared.status is not SubmissionStatus.RECEIVED:
            raise ConflictError("当前 Submission 状态不允许继续签发上传地址")
        if self._artifact_storage is None:
            raise ServiceUnavailableError("S3 Artifact Storage 尚未配置")
        expires_at = datetime.now(UTC) + timedelta(seconds=self._upload_url_ttl_seconds)
        uploads: list[ArtifactUploadTarget] = []
        try:
            for artifact in prepared.artifacts:
                signed = self._artifact_storage.create_upload_url(
                    object_key=artifact.s3_key,
                    content_type=artifact.mime_type,
                    content_length=artifact.size_bytes,
                    sha256=artifact.sha256,
                    expires_in=self._upload_url_ttl_seconds,
                )
                uploads.append(
                    ArtifactUploadTarget(
                        artifact_id=artifact.id,
                        filename=artifact.filename,
                        artifact_type=artifact.artifact_type,
                        mime_type=artifact.mime_type,
                        size_bytes=artifact.size_bytes,
                        sha256=artifact.sha256,
                        upload_url=signed.upload_url,
                        required_headers=signed.required_headers,
                        expires_at=expires_at,
                    )
                )
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            raise ServiceUnavailableError("S3 预签名服务暂时不可用") from exc
        return SubmissionPrepareResult(
            submission_id=prepared.id,
            project_id=prepared.project_id,
            run_manifest_id=prepared.run_manifest_id,
            manifest_hash=prepared.manifest_hash,
            status=prepared.status,
            experiment_status=prepared.experiment_status,
            metrics_summary=prepared.metrics_summary,
            artifact_uploads=uploads,
            created_at=prepared.created_at,
        )

    def submission_finalize(
        self, command: SubmissionFinalizeCommand, identity: RequestIdentity
    ) -> SubmissionFinalizeResult:
        request_hash = _canonical_hash(command.model_dump(mode="json", exclude={"idempotency_key"}))
        initial = run_with_serialization_retry(
            lambda: self._load_finalize_snapshot(command, identity, request_hash)
        )
        if isinstance(initial, SubmissionFinalizeResult):
            return self._attach_submission_analysis(initial)

        result = self._inspect_submission_artifacts(initial)
        if result.verification_result is UploadVerificationResult.FAILED:
            return run_with_serialization_retry(
                lambda: self._persist_failed_finalization(
                    command=command,
                    identity=identity,
                    request_hash=request_hash,
                    snapshot=initial,
                    result=result,
                )
            )
        verified = run_with_serialization_retry(
            lambda: self._commit_verified_finalization(
                command=command,
                identity=identity,
                request_hash=request_hash,
                snapshot=initial,
                result=result,
            )
        )
        return self._attach_submission_analysis(verified)

    def submission_get_status(
        self, *, submission_id: UUID, identity: RequestIdentity
    ) -> SubmissionStatusResult:
        """返回动态工作流状态；读取不会触发调度或模型调用。"""

        if "submission:read" not in identity.scopes:
            raise AuthorizationError("Token 缺少 submission:read scope")
        with self._session_factory() as session:
            submission = self._submissions.get_submission(session, submission_id)
            if submission is None:
                raise ResourceNotFoundError("Submission 不存在")
            self._authorize_submission_status(session, submission, identity)
            jobs = self._workflows.list_jobs(session, submission.id)
            stage_order = {
                "SUBMISSION_SUMMARY": 0,
                "SUBMISSION_REVIEW_PREPARATION": 1,
            }
            jobs.sort(key=lambda item: stage_order[item.job_type.value])
            incomplete = [item for item in jobs if item.status is not WorkflowJobStatus.SUCCEEDED]
            job = incomplete[-1] if incomplete else jobs[-1] if jobs else None
            risks = self._submissions.list_risks(session, submission.id)
            severities = [item.severity for item in risks]
            highest = max(severities, key=RISK_PRIORITY.__getitem__) if severities else None
            summary = (
                GeneratedSummary.model_validate(submission.generated_summary)
                if isinstance(submission.generated_summary, dict)
                else None
            )
            embedding_record = self._submissions.get_embedding(session, submission.id)
            embedding = (
                EmbeddingMetadata(
                    provider=embedding_record.provider,
                    model_id=embedding_record.model_id,
                    dimension=embedding_record.dimension,
                    normalized=embedding_record.normalized,
                    document_version=embedding_record.document_version,
                    input_sha256=embedding_record.input_sha256,
                    input_token_count=embedding_record.input_token_count,
                    generated_at=embedding_record.generated_at,
                )
                if embedding_record is not None
                else None
            )
            review_receipt = (
                SubmissionReceipt.model_validate(submission.review_receipt)
                if isinstance(submission.review_receipt, dict)
                else None
            )
            job_receipt = self._job_receipt(job) if job is not None else None
            retryable = bool(
                submission.workflow_status is WorkflowStatus.RETRYABLE_FAILURE
                and job is not None
                and job.status
                in {
                    WorkflowJobStatus.RETRYABLE_FAILURE,
                    WorkflowJobStatus.DEAD_LETTER,
                }
            )
            return SubmissionStatusResult(
                submission_id=submission.id,
                project_id=submission.project_id,
                run_manifest_id=submission.run_manifest_id,
                submission_status=submission.status,
                workflow_status=submission.workflow_status,
                processing_step=submission.processing_step,
                retryable=retryable,
                processing_error=submission.processing_error,
                job=job_receipt,
                jobs=[self._job_receipt(item) for item in jobs],
                risk_count=len(risks),
                highest_risk=highest,
                generated_summary=summary,
                embedding=embedding,
                review_receipt=review_receipt,
                updated_at=submission.updated_at,
                disclaimer=(
                    REVIEW_DISCLAIMER if review_receipt is not None else SUMMARY_DISCLAIMER
                ),
            )

    def _attach_submission_analysis(
        self, result: SubmissionFinalizeResult
    ) -> SubmissionFinalizeResult:
        """保持上传幂等快照不变，并动态附加当前分析状态。"""

        if (
            result.verification_result is not UploadVerificationResult.PASS
            or self._submission_analysis is None
        ):
            return result
        analysis = self._submission_analysis.run(result.submission_id)
        return result.model_copy(update={"analysis": analysis})

    def _load_finalize_snapshot(
        self,
        command: SubmissionFinalizeCommand,
        identity: RequestIdentity,
        request_hash: str,
    ) -> _FinalizeSnapshot | SubmissionFinalizeResult:
        if "submission:finalize" not in identity.scopes:
            raise AuthorizationError("Token 缺少 submission:finalize scope")
        with self._session_factory() as session, session.begin():
            submission = self._submissions.get_submission(session, command.submission_id)
            if submission is None:
                raise ResourceNotFoundError("Submission 不存在")
            self._authorize_finalize(session, submission, identity)
            existing = self._governance.find_idempotency(
                session,
                actor_id=identity.user_id,
                operation=SUBMISSION_FINALIZE_OPERATION,
                idempotency_key=command.idempotency_key,
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise ConflictError("相同 Idempotency-Key 已用于不同的 finalize 请求")
                if existing.operation_status is IdempotencyOperationStatus.COMPLETED:
                    return self._replay_finalize(existing, request_hash)
                if existing.operation_status is IdempotencyOperationStatus.IN_PROGRESS:
                    raise ConflictError("相同 Idempotency-Key 的 finalize 操作仍在处理")

            if (
                submission.status is SubmissionStatus.PROCESSING
                and submission.upload_verified_at is not None
            ):
                if existing is not None:
                    raise ConflictError("恢复提交分析必须使用新的 Idempotency-Key")
                return self._resume_verified_analysis(
                    session=session,
                    submission=submission,
                    identity=identity,
                    command=command,
                    request_hash=request_hash,
                )

            if submission.status is not SubmissionStatus.RECEIVED:
                raise ConflictError("当前 Submission 状态不允许 finalize")
            artifacts = self._submissions.list_artifacts(session, submission.id)
            prepared = self._validate_finalize_artifacts(artifacts)
            return _FinalizeSnapshot(
                submission_id=submission.id,
                project_id=submission.project_id,
                declaration_hash=self._artifact_declaration_hash(prepared),
                artifacts=prepared,
            )

    def _resume_verified_analysis(
        self,
        *,
        session: Session,
        submission: ExperimentSubmission,
        identity: RequestIdentity,
        command: SubmissionFinalizeCommand,
        request_hash: str,
    ) -> SubmissionFinalizeResult:
        """使用已存上传证据恢复摘要，不重新访问 S3。"""

        snapshot = submission.upload_verification_snapshot
        if not isinstance(snapshot, dict):
            raise ConflictError("Submission 缺少可重放的上传验证快照")
        try:
            result = SubmissionFinalizeResult.model_validate(snapshot)
        except Exception as exc:
            raise ConflictError("Submission 上传验证快照已损坏") from exc

        project = self._projects.require_project_member(
            session,
            project_id=submission.project_id,
            user_id=identity.user_id,
            team_id=identity.team_id,
        )
        previous_workflow_status = submission.workflow_status.value
        review_stage = bool(
            submission.generated_summary is not None
            or submission.processing_step
            in {
                WorkflowStep.SUMMARY_GENERATION,
                WorkflowStep.EMBEDDING_GENERATION,
                WorkflowStep.NEEDS_REVIEW,
            }
        )
        previous_job = (
            self._workflows.get_review_job(session, submission.id)
            if review_stage
            else self._workflows.get_summary_job(session, submission.id)
        )
        previous_job_status = previous_job.status.value if previous_job is not None else None
        risk_prefix_complete = submission.processing_step in {
            WorkflowStep.RISK_ANALYSIS,
            WorkflowStep.SUMMARY_GENERATION,
            WorkflowStep.EMBEDDING_GENERATION,
        }
        if review_stage:
            job, rearmed = self._review_scheduler.rearm_in_session(session, submission)
            job_id = str(job.id)
            job_status = job.status.value
            generation = job.generation
            recovery_mode = "R12B_REVIEW_REARM" if rearmed else "R12B_REVIEW_ACTIVE"
        elif previous_job is not None or risk_prefix_complete:
            job, rearmed = self._summary_scheduler.rearm_in_session(session, submission)
            job_id = str(job.id)
            job_status = job.status.value
            generation = job.generation
            recovery_mode = "R12A_SUMMARY_REARM" if rearmed else "R12A_SUMMARY_ACTIVE"
        else:
            # R11 仍在确定性前缀中失败时，只重放前缀，不得提前创建摘要 Job。
            rearmed = False
            job_id = None
            job_status = None
            generation = None
            recovery_mode = "R11_PREFIX_RESUME"
        response = result.model_dump(mode="json", exclude={"analysis"})
        session.add(
            IdempotencyRecord(
                actor_id=identity.user_id,
                operation=SUBMISSION_FINALIZE_OPERATION,
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
                response_snapshot=response,
                operation_status=IdempotencyOperationStatus.COMPLETED,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        session.add(
            AuditLog(
                team_id=project.team_id,
                project_id=submission.project_id,
                actor_type="USER",
                actor_id=identity.user_id,
                action=SUBMISSION_ANALYSIS_RESUME_ACTION,
                target_type="EXPERIMENT_SUBMISSION",
                target_id=submission.id,
                before_value={
                    "workflow_status": previous_workflow_status,
                    "job_status": previous_job_status,
                },
                after_value={
                    "rearmed": rearmed,
                    "job_id": job_id,
                    "job_status": job_status,
                    "generation": generation,
                    "token_id": str(identity.token_id),
                    "source_agent": submission.source_agent,
                    "actor_mode": self._finalizer_mode(submission, identity),
                    "recovery_mode": recovery_mode,
                },
            )
        )
        return result

    def _inspect_submission_artifacts(
        self, snapshot: _FinalizeSnapshot
    ) -> SubmissionFinalizeResult:
        if self._artifact_storage is None:
            raise ServiceUnavailableError("S3 Artifact Storage 尚未配置")

        issues: list[ArtifactVerificationIssue] = []
        receipts: list[ArtifactVerificationReceipt] = []
        for artifact in snapshot.artifacts:
            metadata = self._artifact_storage.inspect_object(object_key=artifact.s3_key)
            if metadata is None:
                issues.append(
                    ArtifactVerificationIssue(
                        artifact_id=artifact.id,
                        filename=artifact.filename,
                        code=ArtifactVerificationIssueCode.OBJECT_MISSING,
                        field="object",
                        expected="PRESENT",
                        actual="MISSING",
                        message="S3 中不存在该 Artifact 对象",
                        evidence_source=f"object_key:{artifact.s3_key}",
                        observed_at=datetime.now(UTC),
                    )
                )
                continue

            artifact_issues = self._compare_artifact_metadata(artifact, metadata)
            issues.extend(artifact_issues)
            if not artifact_issues:
                version_id = metadata.version_id
                if version_id is None or not version_id.strip():
                    raise ConflictError("S3 版本校验结果与 Artifact 回执不一致")
                version_id = version_id.strip()
                if version_id.casefold() == "null":
                    raise ConflictError("S3 版本校验结果与 Artifact 回执不一致")
                receipts.append(
                    ArtifactVerificationReceipt(
                        artifact_id=artifact.id,
                        filename=artifact.filename,
                        artifact_type=artifact.artifact_type,
                        content_length=metadata.content_length,
                        content_type=artifact.mime_type,
                        checksum_sha256=artifact.sha256,
                        etag=metadata.etag,
                        version_id=version_id,
                        last_modified=metadata.last_modified,
                        verified_at=metadata.observed_at,
                        evidence_source=metadata.evidence_source,
                    )
                )

        if issues:
            return SubmissionFinalizeResult(
                submission_id=snapshot.submission_id,
                project_id=snapshot.project_id,
                verification_result=UploadVerificationResult.FAILED,
                status=SubmissionStatus.RECEIVED,
                retryable=True,
                issues=issues,
                reupload_artifact_ids=sorted({issue.artifact_id for issue in issues}, key=str),
                artifact_verifications=[],
                verified_at=None,
            )
        return SubmissionFinalizeResult(
            submission_id=snapshot.submission_id,
            project_id=snapshot.project_id,
            verification_result=UploadVerificationResult.PASS,
            status=SubmissionStatus.UPLOAD_VERIFIED,
            retryable=False,
            artifact_verifications=receipts,
            verified_at=datetime.now(UTC),
        )

    @staticmethod
    def _compare_artifact_metadata(
        artifact: _PreparedArtifact, metadata: StoredObjectMetadata
    ) -> list[ArtifactVerificationIssue]:
        issues: list[ArtifactVerificationIssue] = []
        common = {
            "artifact_id": artifact.id,
            "filename": artifact.filename,
            "evidence_source": metadata.evidence_source,
            "observed_at": metadata.observed_at,
        }
        if metadata.content_length != artifact.size_bytes:
            issues.append(
                ArtifactVerificationIssue(
                    **common,
                    code=ArtifactVerificationIssueCode.CONTENT_LENGTH_MISMATCH,
                    field="content_length",
                    expected=artifact.size_bytes,
                    actual=metadata.content_length,
                    message="S3 对象大小与 Artifact 声明不一致",
                )
            )
        if metadata.content_type != artifact.mime_type:
            issues.append(
                ArtifactVerificationIssue(
                    **common,
                    code=ArtifactVerificationIssueCode.CONTENT_TYPE_MISMATCH,
                    field="content_type",
                    expected=artifact.mime_type,
                    actual=metadata.content_type,
                    message="S3 Content-Type 与 Artifact 声明不一致",
                )
            )
        if metadata.checksum_sha256 is None:
            issues.append(
                ArtifactVerificationIssue(
                    **common,
                    code=ArtifactVerificationIssueCode.CHECKSUM_SHA256_MISSING,
                    field="checksum_sha256",
                    expected=artifact.sha256,
                    actual=None,
                    message="S3 对象缺少可验证的 SHA-256 checksum",
                )
            )
        elif metadata.checksum_sha256 != artifact.sha256:
            issues.append(
                ArtifactVerificationIssue(
                    **common,
                    code=ArtifactVerificationIssueCode.CHECKSUM_SHA256_MISMATCH,
                    field="checksum_sha256",
                    expected=artifact.sha256,
                    actual=metadata.checksum_sha256,
                    message="S3 SHA-256 checksum 与 Artifact 声明不一致",
                )
            )
        version_id = metadata.version_id
        if version_id is None or not version_id.strip() or version_id.strip().casefold() == "null":
            issues.append(
                ArtifactVerificationIssue(
                    **common,
                    code=ArtifactVerificationIssueCode.S3_VERSION_ID_MISSING,
                    field="version_id",
                    expected="NON_NULL_VERSION_ID",
                    actual=metadata.version_id,
                    message="S3 对象没有不可变 VersionId，请启用 Bucket Versioning 后重新上传",
                )
            )
        return issues

    def _persist_failed_finalization(
        self,
        *,
        command: SubmissionFinalizeCommand,
        identity: RequestIdentity,
        request_hash: str,
        snapshot: _FinalizeSnapshot,
        result: SubmissionFinalizeResult,
    ) -> SubmissionFinalizeResult:
        try:
            with self._session_factory() as session, session.begin():
                submission = self._submissions.get_submission_for_update(
                    session, command.submission_id
                )
                if submission is None:
                    raise ResourceNotFoundError("Submission 不存在")
                project = self._authorize_finalize(session, submission, identity)
                existing = self._governance.find_idempotency(
                    session,
                    actor_id=identity.user_id,
                    operation=SUBMISSION_FINALIZE_OPERATION,
                    idempotency_key=command.idempotency_key,
                )
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise ConflictError("相同 Idempotency-Key 已用于不同的 finalize 请求")
                    if existing.operation_status is IdempotencyOperationStatus.COMPLETED:
                        return self._replay_finalize(existing, request_hash)
                artifacts = self._require_unchanged_finalize_target(
                    session, submission, snapshot, lock_artifacts=True
                )
                replacement_artifact_ids = {
                    issue.artifact_id
                    for issue in result.issues
                    if issue.code is not ArtifactVerificationIssueCode.OBJECT_MISSING
                }
                replacement_keys: list[dict[str, str]] = []
                for artifact in artifacts:
                    if artifact.id not in replacement_artifact_ids:
                        continue
                    old_key = artifact.s3_key
                    artifact.s3_key = self._replacement_object_key(
                        project_id=submission.project_id,
                        submission_id=submission.id,
                        artifact_id=artifact.id,
                    )
                    replacement_keys.append(
                        {
                            "artifact_id": str(artifact.id),
                            "previous_s3_key": old_key,
                            "replacement_s3_key": artifact.s3_key,
                        }
                    )
                response = result.model_dump(mode="json", exclude={"analysis"})
                if existing is None:
                    session.add(
                        IdempotencyRecord(
                            actor_id=identity.user_id,
                            operation=SUBMISSION_FINALIZE_OPERATION,
                            idempotency_key=command.idempotency_key,
                            request_hash=request_hash,
                            response_snapshot=response,
                            operation_status=IdempotencyOperationStatus.FAILED,
                            expires_at=datetime.now(UTC) + timedelta(days=7),
                        )
                    )
                else:
                    existing.response_snapshot = response
                    existing.operation_status = IdempotencyOperationStatus.FAILED
                    existing.expires_at = datetime.now(UTC) + timedelta(days=7)
                session.add(
                    AuditLog(
                        team_id=project.team_id,
                        project_id=submission.project_id,
                        actor_type="USER",
                        actor_id=identity.user_id,
                        action=SUBMISSION_FINALIZE_FAILURE_ACTION,
                        target_type="EXPERIMENT_SUBMISSION",
                        target_id=submission.id,
                        before_value={
                            "status": SubmissionStatus.RECEIVED.value,
                            "declaration_hash": snapshot.declaration_hash,
                        },
                        after_value={
                            "verification_result": result.verification_result.value,
                            "issues": [issue.model_dump(mode="json") for issue in result.issues],
                            "reupload_artifact_ids": [
                                str(item) for item in result.reupload_artifact_ids
                            ],
                            "replacement_keys": replacement_keys,
                            "token_id": str(identity.token_id),
                            "source_agent": submission.source_agent,
                            "finalizer_mode": self._finalizer_mode(submission, identity),
                        },
                    )
                )
                return result
        except IntegrityError as exc:
            replay = self._replay_finalize_after_integrity(
                actor_id=identity.user_id,
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
                allow_failed=True,
            )
            if replay is not None:
                return replay
            raise ConflictError("Submission finalize 与现有数据冲突") from exc

    def _commit_verified_finalization(
        self,
        *,
        command: SubmissionFinalizeCommand,
        identity: RequestIdentity,
        request_hash: str,
        snapshot: _FinalizeSnapshot,
        result: SubmissionFinalizeResult,
    ) -> SubmissionFinalizeResult:
        try:
            with self._session_factory() as session, session.begin():
                submission = self._submissions.get_submission_for_update(
                    session, command.submission_id
                )
                if submission is None:
                    raise ResourceNotFoundError("Submission 不存在")
                project = self._authorize_finalize(session, submission, identity)
                existing = self._governance.find_idempotency(
                    session,
                    actor_id=identity.user_id,
                    operation=SUBMISSION_FINALIZE_OPERATION,
                    idempotency_key=command.idempotency_key,
                )
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise ConflictError("相同 Idempotency-Key 已用于不同的 finalize 请求")
                    if existing.operation_status is IdempotencyOperationStatus.COMPLETED:
                        return self._replay_finalize(existing, request_hash)
                artifacts = self._require_unchanged_finalize_target(
                    session, submission, snapshot, lock_artifacts=True
                )
                receipts = {item.artifact_id: item for item in result.artifact_verifications}
                if set(receipts) != {item.id for item in artifacts}:
                    raise ConflictError("S3 复核回执与 Artifact 声明集合不一致")

                for artifact in artifacts:
                    receipt = receipts[artifact.id]
                    artifact.cloud_hash_verified = True
                    artifact.verified_at = receipt.verified_at
                    artifact.s3_version_id = receipt.version_id
                    artifact.verification_evidence = self._verification_evidence(receipt)

                submission.status = SubmissionStatus.UPLOAD_VERIFIED
                submission.upload_verified_at = result.verified_at
                submission.upload_verified_by = identity.user_id
                response = result.model_dump(mode="json", exclude={"analysis"})
                submission.upload_verification_snapshot = response
                if existing is None:
                    session.add(
                        IdempotencyRecord(
                            actor_id=identity.user_id,
                            operation=SUBMISSION_FINALIZE_OPERATION,
                            idempotency_key=command.idempotency_key,
                            request_hash=request_hash,
                            response_snapshot=response,
                            operation_status=IdempotencyOperationStatus.COMPLETED,
                            expires_at=datetime.now(UTC) + timedelta(days=7),
                        )
                    )
                else:
                    existing.response_snapshot = response
                    existing.operation_status = IdempotencyOperationStatus.COMPLETED
                    existing.expires_at = datetime.now(UTC) + timedelta(days=7)
                session.add(
                    AuditLog(
                        team_id=project.team_id,
                        project_id=submission.project_id,
                        actor_type="USER",
                        actor_id=identity.user_id,
                        action=SUBMISSION_FINALIZE_OPERATION,
                        target_type="EXPERIMENT_SUBMISSION",
                        target_id=submission.id,
                        before_value={"status": SubmissionStatus.RECEIVED.value},
                        after_value={
                            "status": SubmissionStatus.UPLOAD_VERIFIED.value,
                            "artifact_ids": [str(item.id) for item in artifacts],
                            "verified_at": result.verified_at.isoformat()
                            if result.verified_at
                            else None,
                            "token_id": str(identity.token_id),
                            "source_agent": submission.source_agent,
                            "finalizer_mode": self._finalizer_mode(submission, identity),
                        },
                    )
                )
                return result
        except IntegrityError as exc:
            replay = self._replay_finalize_after_integrity(
                actor_id=identity.user_id,
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
                allow_failed=False,
            )
            if replay is not None:
                return replay
            raise ConflictError("Submission finalize 与现有数据冲突") from exc

    def _authorize_finalize(
        self, session: Session, submission: ExperimentSubmission, identity: RequestIdentity
    ) -> Project:
        if identity.project_id != submission.project_id:
            raise AuthorizationError("MCP Token 未绑定 Submission 所属项目")
        project = self._projects.require_project_member(
            session,
            project_id=submission.project_id,
            user_id=identity.user_id,
            team_id=identity.team_id,
        )
        role = self._projects.require_member(
            session,
            user_id=identity.user_id,
            team_id=project.team_id,
        )
        if submission.submitted_by != identity.user_id and role is not TeamRole.OWNER:
            raise AuthorizationError("只有 Submission 原提交者或项目 Owner 可以 finalize")
        return project

    def _authorize_submission_status(
        self, session: Session, submission: ExperimentSubmission, identity: RequestIdentity
    ) -> Project:
        if identity.project_id != submission.project_id:
            raise AuthorizationError("MCP Token 未绑定 Submission 所属项目")
        project = self._projects.require_project_member(
            session,
            project_id=submission.project_id,
            user_id=identity.user_id,
            team_id=identity.team_id,
        )
        role = self._projects.require_member(
            session,
            user_id=identity.user_id,
            team_id=project.team_id,
        )
        if submission.submitted_by != identity.user_id and role is not TeamRole.OWNER:
            raise AuthorizationError("只有 Submission 原提交者或项目 Owner 可以读取状态")
        return project

    @staticmethod
    def _job_receipt(job: WorkflowJob) -> WorkflowJobReceipt:
        return WorkflowJobReceipt(
            id=job.id,
            job_type=job.job_type,
            status=job.status,
            generation=job.generation,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            available_at=job.available_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            last_error=job.last_error,
        )

    @staticmethod
    def _finalizer_mode(submission: ExperimentSubmission, identity: RequestIdentity) -> str:
        return (
            "ORIGINAL_SUBMITTER"
            if submission.submitted_by == identity.user_id
            else "OWNER_RECOVERY"
        )

    @staticmethod
    def _replacement_object_key(*, project_id: UUID, submission_id: UUID, artifact_id: UUID) -> str:
        return (
            f"projects/{project_id}/submissions/{submission_id}/artifacts/{artifact_id}/"
            f"attempts/{uuid4()}"
        )

    def _require_unchanged_finalize_target(
        self,
        session: Session,
        submission: ExperimentSubmission,
        snapshot: _FinalizeSnapshot,
        *,
        lock_artifacts: bool = False,
    ) -> list[Artifact]:
        if submission.status is not SubmissionStatus.RECEIVED:
            raise ConflictError("当前 Submission 状态不允许 finalize")
        artifacts = (
            self._submissions.list_artifacts_for_update(session, submission.id)
            if lock_artifacts
            else self._submissions.list_artifacts(session, submission.id)
        )
        prepared = self._validate_finalize_artifacts(artifacts)
        if self._artifact_declaration_hash(prepared) != snapshot.declaration_hash:
            raise ConflictError("Artifact 声明在 S3 复核期间已发生变化")
        return artifacts

    @staticmethod
    def _validate_finalize_artifacts(
        artifacts: Sequence[Artifact],
    ) -> tuple[_PreparedArtifact, ...]:
        if not artifacts or any(item.cloud_hash_verified for item in artifacts):
            raise ConflictError("Submission 的 Artifact 验证状态不完整")
        counts = {
            artifact_type: sum(item.artifact_type is artifact_type for item in artifacts)
            for artifact_type in ArtifactType
        }
        if counts[ArtifactType.CONFIG] != 1 or counts[ArtifactType.RESULT] != 1:
            raise ConflictError("Submission 必须恰好关联一个 CONFIG 和一个 RESULT")
        return tuple(
            _PreparedArtifact(
                id=item.id,
                filename=item.filename,
                artifact_type=item.artifact_type,
                mime_type=item.mime_type,
                size_bytes=item.size_bytes,
                sha256=item.sha256,
                s3_key=item.s3_key,
            )
            for item in artifacts
        )

    @staticmethod
    def _artifact_declaration_hash(artifacts: Sequence[_PreparedArtifact]) -> str:
        return _canonical_hash(
            [
                {
                    "id": str(item.id),
                    "filename": item.filename,
                    "artifact_type": item.artifact_type.value,
                    "mime_type": item.mime_type,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                    "s3_key": item.s3_key,
                }
                for item in artifacts
            ]
        )

    @staticmethod
    def _verification_evidence(receipt: ArtifactVerificationReceipt) -> dict[str, Any]:
        return {
            "value": {
                "content_length": receipt.content_length,
                "content_type": receipt.content_type,
                "checksum_sha256": receipt.checksum_sha256,
                "etag": receipt.etag,
                "version_id": receipt.version_id,
                "last_modified": receipt.last_modified.isoformat()
                if receipt.last_modified
                else None,
            },
            "evidence_type": receipt.evidence_type.value,
            "source": receipt.evidence_source,
            "collected_at": receipt.verified_at.isoformat(),
            "collection_tool": receipt.collection_tool,
        }

    @staticmethod
    def _replay_finalize(record: IdempotencyRecord, request_hash: str) -> SubmissionFinalizeResult:
        if record.request_hash != request_hash:
            raise ConflictError("相同 Idempotency-Key 已用于不同的 finalize 请求")
        if not record.response_snapshot:
            raise ConflictError("finalize 幂等回执不完整")
        return SubmissionFinalizeResult.model_validate(record.response_snapshot)

    def _replay_finalize_after_integrity(
        self,
        *,
        actor_id: UUID,
        idempotency_key: UUID,
        request_hash: str,
        allow_failed: bool,
    ) -> SubmissionFinalizeResult | None:
        with self._session_factory() as session:
            record = self._governance.find_idempotency(
                session,
                actor_id=actor_id,
                operation=SUBMISSION_FINALIZE_OPERATION,
                idempotency_key=idempotency_key,
            )
            if record is None or record.request_hash != request_hash:
                return None
            if record.operation_status is IdempotencyOperationStatus.COMPLETED or (
                allow_failed and record.operation_status is IdempotencyOperationStatus.FAILED
            ):
                return self._replay_finalize(record, request_hash)
            return None

    def experiments_query(
        self, command: ExperimentQueryCommand, identity: RequestIdentity
    ) -> Sequence[ExperimentQueryResult]:
        if self._experiment_queries is None:
            raise RuntimeError("experiments_query 服务未装配")
        return self._experiment_queries.query(command, identity)


class PlanApprovalService:
    """管理端 Owner 对待审批 Plan Check 作出一次性、可审计的最终决定。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        project_repository: SqlAlchemyProjectRepository,
        governance_repository: SqlAlchemyGovernanceRepository,
    ) -> None:
        self._session_factory = session_factory
        self._projects = project_repository
        self._governance = governance_repository

    def decide(
        self,
        *,
        identity: RequestIdentity,
        project_id: UUID,
        plan_check_id: UUID,
        idempotency_key: UUID,
        request: PlanCheckDecisionRequest,
    ) -> PlanCheckDecisionResult:
        return run_with_serialization_retry(
            lambda: self._decide_once(
                identity=identity,
                project_id=project_id,
                plan_check_id=plan_check_id,
                idempotency_key=idempotency_key,
                request=request,
            )
        )

    def _decide_once(
        self,
        *,
        identity: RequestIdentity,
        project_id: UUID,
        plan_check_id: UUID,
        idempotency_key: UUID,
        request: PlanCheckDecisionRequest,
    ) -> PlanCheckDecisionResult:
        request_hash = _canonical_hash(
            {
                "project_id": str(project_id),
                "plan_check_id": str(plan_check_id),
                "request": request.model_dump(mode="json"),
            }
        )
        try:
            with self._session_factory() as session, session.begin():
                return self.decide_in_session(
                    session,
                    identity=identity,
                    project_id=project_id,
                    plan_check_id=plan_check_id,
                    idempotency_key=idempotency_key,
                    request=request,
                    request_hash=request_hash,
                )
        except IntegrityError as exc:
            replay = self._replay_decision_after_integrity(
                actor_id=identity.user_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
            raise ConflictError("Plan Check 审批与现有数据冲突") from exc

    def decide_in_session(
        self,
        session: Session,
        *,
        identity: RequestIdentity,
        project_id: UUID,
        plan_check_id: UUID,
        idempotency_key: UUID,
        request: PlanCheckDecisionRequest,
        request_hash: str | None = None,
        audit_context: dict[str, object] | None = None,
    ) -> PlanCheckDecisionResult:
        """在调用方事务中执行正式决定，供直接 API 与人类确认提案共同复用。"""

        if "plan:approve" not in identity.scopes:
            raise AuthorizationError("Token 缺少 plan:approve scope")
        if identity.authentication_method == "WEB_SESSION" and not identity.recent_authentication:
            raise RecentAuthenticationRequiredError("批准 Plan Check 前需要完成近期身份认证")
        effective_hash = request_hash or _canonical_hash(
            {
                "project_id": str(project_id),
                "plan_check_id": str(plan_check_id),
                "request": request.model_dump(mode="json"),
            }
        )
        project = self._projects.require_project_member(
            session,
            project_id=project_id,
            user_id=identity.user_id,
            team_id=identity.team_id,
        )
        self._projects.require_member(
            session,
            user_id=identity.user_id,
            team_id=project.team_id,
            allowed_roles={TeamRole.OWNER},
        )
        existing_idempotency = self._governance.find_idempotency(
            session,
            actor_id=identity.user_id,
            operation=PLAN_DECISION_OPERATION,
            idempotency_key=idempotency_key,
        )
        if existing_idempotency is not None:
            return self._replay_decision(existing_idempotency, effective_hash)

        plan = self._governance.get_plan_for_update(session, plan_check_id)
        if plan is None or plan.project_id != project_id:
            raise ResourceNotFoundError("项目中不存在该 Plan Check")
        if (
            plan.check_result is not CheckResult.NEEDS_APPROVAL
            or plan.approval_status is not ApprovalStatus.PENDING
        ):
            raise ConflictError("只有 NEEDS_APPROVAL/PENDING 的 Plan Check 可以审批")
        if self._governance.find_plan_approval(session, plan.id) is not None:
            raise ConflictError("该 Plan Check 已存在最终审批记录，不能再次决定")

        now = datetime.now(UTC)
        approval = ApprovalRecord(
            project_id=project_id,
            target_type=ApprovalTargetType.PLAN_CHECK,
            target_id=plan.id,
            approval_type="PLAN_PARAMETER_CHANGE",
            status=request.decision,
            requested_by=plan.requester_id,
            decided_by=identity.user_id,
            request_reason=self._request_reason(plan),
            decision_reason=request.decision_reason,
            decided_at=now,
        )
        before_value = {
            "check_result": plan.check_result.value,
            "approval_status": plan.approval_status.value,
            "approved_by": str(plan.approved_by) if plan.approved_by else None,
            "approved_at": plan.approved_at.isoformat() if plan.approved_at else None,
        }
        if request.decision is ApprovalDecision.APPROVED:
            plan.approval_status = ApprovalStatus.APPROVED
            plan.approved_by = identity.user_id
            plan.approved_at = now
        else:
            plan.approval_status = ApprovalStatus.REJECTED
            plan.approved_by = None
            plan.approved_at = None
        session.add(approval)
        session.flush()

        result = PlanCheckDecisionResult(
            approval_record_id=approval.id,
            project_id=project_id,
            plan_check_id=plan.id,
            decision=approval.status,
            requested_by=approval.requested_by,
            decided_by=approval.decided_by,
            decided_at=approval.decided_at,
            decision_reason=approval.decision_reason,
            can_create_manifest=approval.status is ApprovalDecision.APPROVED,
        )
        session.add_all(
            [
                AuditLog(
                    team_id=project.team_id,
                    project_id=project_id,
                    actor_type="USER",
                    actor_id=identity.user_id,
                    action=PLAN_DECISION_OPERATION,
                    target_type=ApprovalTargetType.PLAN_CHECK.value,
                    target_id=plan.id,
                    before_value=before_value,
                    after_value={
                        "approval_record_id": str(approval.id),
                        "approval_status": plan.approval_status.value,
                        "decision_reason": approval.decision_reason,
                        **(audit_context or {}),
                    },
                ),
                IdempotencyRecord(
                    actor_id=identity.user_id,
                    operation=PLAN_DECISION_OPERATION,
                    idempotency_key=idempotency_key,
                    request_hash=effective_hash,
                    response_snapshot=result.model_dump(mode="json"),
                    operation_status=IdempotencyOperationStatus.COMPLETED,
                    expires_at=now + timedelta(days=7),
                ),
            ]
        )
        return result

    @staticmethod
    def _request_reason(plan: PlanCheck) -> str:
        risks = plan.report.get("risks", []) if isinstance(plan.report, dict) else []
        payload = {
            "risk_level": plan.risk_level.value,
            "risks": [
                {"code": item.get("code"), "message": item.get("message")}
                for item in risks
                if isinstance(item, dict)
            ],
            "planned_changes": plan.planned_changes,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _replay_decision(record: IdempotencyRecord, request_hash: str) -> PlanCheckDecisionResult:
        if record.request_hash != request_hash:
            raise ConflictError("相同 Idempotency-Key 已用于不同的审批请求")
        if (
            record.operation_status is not IdempotencyOperationStatus.COMPLETED
            or record.response_snapshot is None
        ):
            raise ConflictError("相同 Idempotency-Key 的审批操作仍在处理中")
        return PlanCheckDecisionResult.model_validate(record.response_snapshot)

    def _replay_decision_after_integrity(
        self, *, actor_id: UUID, idempotency_key: UUID, request_hash: str
    ) -> PlanCheckDecisionResult | None:
        with self._session_factory() as session:
            record = self._governance.find_idempotency(
                session,
                actor_id=actor_id,
                operation=PLAN_DECISION_OPERATION,
                idempotency_key=idempotency_key,
            )
            return None if record is None else self._replay_decision(record, request_hash)


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

        narrative = self._projects.regenerate_policy_narrative(
            session,
            project_id=project.id,
            context_id=context.id,
            generated_by=identity.user_id,
        )
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
                    "human_readable_status": narrative.status,
                    "human_readable_source_hash": narrative.source_hash,
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
