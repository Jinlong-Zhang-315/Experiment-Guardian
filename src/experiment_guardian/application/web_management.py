"""R14 Web 管理端的薄应用服务，保持现有实验用例边界不变。"""

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.errors import (
    AuthorizationError,
    ConflictError,
    InputValidationError,
    RecentAuthenticationRequiredError,
    ResourceNotFoundError,
)
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.ports import ArtifactStorage
from experiment_guardian.domain.enums import (
    ApprovalStatus,
    CheckResult,
    ConstraintSource,
    ContextStatus,
    ExperimentMode,
    IdempotencyOperationStatus,
    IntentStatus,
    ProtectionLevel,
    ReviewEligibility,
    SubmissionStatus,
    TeamRole,
    VerificationStatus,
    WorkflowStatus,
    WorkflowStep,
)
from experiment_guardian.domain.plan_check import flatten_configuration
from experiment_guardian.domain.web_management import (
    ArtifactDownloadResult,
    ArtifactWebView,
    ContextVersionSummary,
    ExperimentPage,
    ExperimentWebView,
    PlanCheckPage,
    PlanCheckWebView,
    PolicyPublishRequest,
    PolicyPublishResult,
    ProjectList,
    ProjectSettingsView,
    ProjectSummary,
    SubmissionPage,
    SubmissionWebView,
)
from experiment_guardian.infrastructure.models import (
    Artifact,
    AuditLog,
    Experiment,
    ExperimentIntent,
    ExperimentSubmission,
    IdempotencyRecord,
    PlanCheck,
    Project,
    ProjectContext,
    ProtectedParameter,
    SubmissionRisk,
)
from experiment_guardian.infrastructure.repositories import SqlAlchemyProjectRepository

POLICY_PUBLISH_OPERATION = "project.policy.publish"
T = TypeVar("T")


def _hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _same_json_value(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_json_value(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json_value(a, b) for a, b in zip(left, right, strict=True)
        )
    return bool(left == right)


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        value = int(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise InputValidationError("分页 cursor 无效") from exc
    if value < 0:
        raise InputValidationError("分页 cursor 无效")
    return value


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii")


class WebManagementService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        projects: SqlAlchemyProjectRepository,
        artifact_storage: ArtifactStorage,
        download_expires_seconds: int,
    ) -> None:
        self._session_factory = session_factory
        self._projects = projects
        self._artifact_storage = artifact_storage
        self._download_expires_seconds = download_expires_seconds

    def list_projects(self, identity: RequestIdentity) -> ProjectList:
        self._require_scope(identity, "project:read")
        with self._session_factory() as session:
            projects = session.scalars(
                select(Project)
                .where(Project.team_id == identity.team_id, Project.active.is_(True))
                .order_by(Project.name, Project.id)
            ).all()
            return ProjectList(items=[self._project_summary(item) for item in projects])

    def get_settings(self, *, project_id: UUID, identity: RequestIdentity) -> ProjectSettingsView:
        self._require_scope(identity, "project:read")
        with self._session_factory() as session:
            project = self._require_project(session, project_id, identity)
            bundle = self._projects.load_context_bundle(
                session,
                project_id=project_id,
                user_id=identity.user_id,
                team_id=identity.team_id,
            )
            contexts = session.scalars(
                select(ProjectContext)
                .where(ProjectContext.project_id == project_id)
                .order_by(ProjectContext.version.desc())
            ).all()
            return ProjectSettingsView(
                project=self._project_summary(project),
                current=bundle,
                context_history=[
                    ContextVersionSummary(
                        context_id=item.id,
                        version=item.version,
                        status=item.status.value,
                        change_reason=item.change_reason,
                        created_by=item.created_by,
                        confirmed_by=item.confirmed_by,
                        confirmed_at=item.confirmed_at,
                        effective_at=item.effective_at,
                        created_at=item.created_at,
                    )
                    for item in contexts
                ],
            )

    def publish_policy(
        self,
        *,
        project_id: UUID,
        identity: RequestIdentity,
        idempotency_key: UUID,
        request: PolicyPublishRequest,
    ) -> PolicyPublishResult:
        self._require_scope(identity, "project:write")
        if identity.authentication_method == "WEB_SESSION" and not identity.recent_authentication:
            raise RecentAuthenticationRequiredError("发布正式策略前需要完成近期身份认证")
        self._validate_configuration(request)
        request_hash = _hash(request.model_dump(mode="json"))
        with self._session_factory() as session, session.begin():
            project = self._require_project(session, project_id, identity, owner=True)
            existing = session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.actor_id == identity.user_id,
                    IdempotencyRecord.operation == POLICY_PUBLISH_OPERATION,
                    IdempotencyRecord.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_hash != request_hash or not existing.response_snapshot:
                    raise ConflictError("相同 Idempotency-Key 已用于不同的策略发布请求")
                return PolicyPublishResult.model_validate(existing.response_snapshot)

            current = session.scalar(
                select(ProjectContext)
                .where(
                    ProjectContext.project_id == project_id,
                    ProjectContext.status == ContextStatus.ACTIVE,
                )
                .with_for_update()
            )
            if current is None:
                raise ConflictError("项目没有可替换的 ACTIVE Context")
            if current.version != request.expected_context_version:
                raise ConflictError(
                    f"Context 版本已变化，当前为 {current.version}，请刷新后重新确认"
                )
            intents = session.scalars(
                select(ExperimentIntent).where(
                    ExperimentIntent.project_id == project_id,
                    ExperimentIntent.status == IntentStatus.ACTIVE,
                )
            ).all()
            if len(intents) != 1:
                raise ConflictError("项目 ACTIVE Intent 数量异常")
            old_intent = intents[0]
            old_constraints = session.scalars(
                select(ProtectedParameter).where(
                    ProtectedParameter.project_id == project_id,
                    ProtectedParameter.active.is_(True),
                )
            ).all()
            now = datetime.now(UTC)
            current.status = ContextStatus.SUPERSEDED
            old_intent.status = IntentStatus.CLOSED
            for item in old_constraints:
                item.active = False
                item.verification_status = VerificationStatus.SUPERSEDED

            context_input = request.context
            context = ProjectContext(
                project_id=project_id,
                version=current.version + 1,
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
                supersedes_context_id=current.id,
                created_by=identity.user_id,
                confirmed_by=identity.user_id,
                confirmed_at=now,
                effective_at=now,
            )
            session.add(context)
            session.flush()
            intent_input = request.intent
            latest_intent_version = (
                session.scalar(
                    select(func.max(ExperimentIntent.version)).where(
                        ExperimentIntent.project_id == project_id
                    )
                )
                or 0
            )
            intent = ExperimentIntent(
                project_id=project_id,
                context_id=context.id,
                context_version=context.version,
                version=latest_intent_version + 1,
                supersedes_intent_id=old_intent.id,
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
            old_by_path = {item.parameter_path: item for item in old_constraints}
            for constraint_input in request.constraints:
                predecessor = old_by_path.get(constraint_input.parameter_path)
                version = predecessor.version + 1 if predecessor else 1
                intent_scoped = (
                    constraint_input.protection_level is ProtectionLevel.EXPERIMENT_VARIABLE
                )
                session.add(
                    ProtectedParameter(
                        project_id=project_id,
                        context_id=context.id,
                        context_version=context.version,
                        intent_id=intent.id if intent_scoped else None,
                        intent_version=intent.version if intent_scoped else None,
                        version=version,
                        supersedes_constraint_id=predecessor.id if predecessor else None,
                        parameter_path=constraint_input.parameter_path,
                        protection_level=constraint_input.protection_level,
                        expected_value=constraint_input.expected_value,
                        allowed_range={
                            "allowed_values": constraint_input.allowed_values,
                            "minimum": constraint_input.minimum,
                            "maximum": constraint_input.maximum,
                        },
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
            bundle = self._projects.load_context_bundle(
                session,
                project_id=project_id,
                user_id=identity.user_id,
                team_id=identity.team_id,
            )
            result = PolicyPublishResult(
                project_id=project_id,
                previous_context_version=current.version,
                context_bundle=bundle,
            )
            session.add_all(
                [
                    AuditLog(
                        team_id=project.team_id,
                        project_id=project_id,
                        actor_type="USER",
                        actor_id=identity.user_id,
                        action=POLICY_PUBLISH_OPERATION,
                        target_type="PROJECT_CONTEXT",
                        target_id=context.id,
                        before_value={"context_id": str(current.id), "version": current.version},
                        after_value={
                            "context_id": str(context.id),
                            "version": context.version,
                            "intent_id": str(intent.id),
                            "intent_version": intent.version,
                            "session_id": str(identity.token_id),
                        },
                    ),
                    IdempotencyRecord(
                        actor_id=identity.user_id,
                        operation=POLICY_PUBLISH_OPERATION,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                        response_snapshot=result.model_dump(mode="json"),
                        operation_status=IdempotencyOperationStatus.COMPLETED,
                        expires_at=now + timedelta(days=7),
                    ),
                ]
            )
            return result

    def list_plan_checks(
        self,
        *,
        project_id: UUID,
        identity: RequestIdentity,
        cursor: str | None,
        limit: int,
    ) -> PlanCheckPage:
        self._require_scope(identity, "plan:read")
        offset = _decode_cursor(cursor)
        with self._session_factory() as session:
            project = self._require_project(session, project_id, identity)
            role = self._projects.require_member(
                session, user_id=identity.user_id, team_id=project.team_id
            )
            statement = select(PlanCheck).where(PlanCheck.project_id == project_id)
            if role is TeamRole.RESEARCHER:
                statement = statement.where(PlanCheck.requester_id == identity.user_id)
            rows = session.scalars(
                statement.order_by(PlanCheck.created_at.desc(), PlanCheck.id.desc())
                .offset(offset)
                .limit(limit + 1)
            ).all()
            items = [self._plan_view(row, role) for row in rows[:limit]]
            return PlanCheckPage(
                items=items,
                next_cursor=_encode_cursor(offset + limit) if len(rows) > limit else None,
            )

    def get_plan_check(
        self, *, project_id: UUID, plan_check_id: UUID, identity: RequestIdentity
    ) -> PlanCheckWebView:
        with self._session_factory() as session:
            self._require_scope(identity, "plan:read")
            project = self._require_project(session, project_id, identity)
            role = self._projects.require_member(
                session, user_id=identity.user_id, team_id=project.team_id
            )
            row = session.get(PlanCheck, plan_check_id)
            if row is None or row.project_id != project_id:
                raise ResourceNotFoundError("项目中不存在该 Plan Check")
            if role is TeamRole.RESEARCHER and row.requester_id != identity.user_id:
                raise AuthorizationError("Researcher 只能查看自己的 Plan Check")
            return self._plan_view(row, role)

    def list_submissions(
        self,
        *,
        project_id: UUID,
        identity: RequestIdentity,
        cursor: str | None,
        limit: int,
    ) -> SubmissionPage:
        self._require_scope(identity, "submission:read")
        offset = _decode_cursor(cursor)
        with self._session_factory() as session:
            project = self._require_project(session, project_id, identity)
            role = self._projects.require_member(
                session, user_id=identity.user_id, team_id=project.team_id
            )
            statement = select(ExperimentSubmission).where(
                ExperimentSubmission.project_id == project_id
            )
            if role is TeamRole.RESEARCHER:
                statement = statement.where(ExperimentSubmission.submitted_by == identity.user_id)
            rows = session.scalars(
                statement.order_by(
                    ExperimentSubmission.created_at.desc(), ExperimentSubmission.id.desc()
                )
                .offset(offset)
                .limit(limit + 1)
            ).all()
            items = [self._submission_view(session, row, role) for row in rows[:limit]]
            return SubmissionPage(
                items=items,
                next_cursor=_encode_cursor(offset + limit) if len(rows) > limit else None,
            )

    def get_submission(
        self, *, project_id: UUID, submission_id: UUID, identity: RequestIdentity
    ) -> SubmissionWebView:
        self._require_scope(identity, "submission:read")
        with self._session_factory() as session:
            project = self._require_project(session, project_id, identity)
            role = self._projects.require_member(
                session, user_id=identity.user_id, team_id=project.team_id
            )
            row = session.get(ExperimentSubmission, submission_id)
            if row is None or row.project_id != project_id:
                raise ResourceNotFoundError("项目中不存在该 Submission")
            if role is TeamRole.RESEARCHER and row.submitted_by != identity.user_id:
                raise AuthorizationError("Researcher 只能查看自己的 Submission")
            return self._submission_view(session, row, role)

    def list_experiments(
        self,
        *,
        project_id: UUID,
        identity: RequestIdentity,
        cursor: str | None,
        limit: int,
    ) -> ExperimentPage:
        self._require_scope(identity, "experiment:read")
        offset = _decode_cursor(cursor)
        with self._session_factory() as session:
            self._require_project(session, project_id, identity)
            rows = session.scalars(
                select(Experiment)
                .where(Experiment.project_id == project_id)
                .order_by(Experiment.created_at.desc(), Experiment.id.desc())
                .offset(offset)
                .limit(limit + 1)
            ).all()
            return ExperimentPage(
                items=[self._experiment_view(row) for row in rows[:limit]],
                next_cursor=_encode_cursor(offset + limit) if len(rows) > limit else None,
            )

    def get_experiment(
        self, *, project_id: UUID, experiment_id: UUID, identity: RequestIdentity
    ) -> ExperimentWebView:
        self._require_scope(identity, "experiment:read")
        with self._session_factory() as session:
            self._require_project(session, project_id, identity)
            row = session.get(Experiment, experiment_id)
            if row is None or row.project_id != project_id:
                raise ResourceNotFoundError("项目中不存在该 Experiment")
            return self._experiment_view(row)

    def create_artifact_download(
        self, *, project_id: UUID, artifact_id: UUID, identity: RequestIdentity
    ) -> ArtifactDownloadResult:
        self._require_scope(identity, "artifact:read")
        with self._session_factory() as session, session.begin():
            project = self._require_project(session, project_id, identity)
            artifact = session.get(Artifact, artifact_id)
            if artifact is None:
                raise ResourceNotFoundError("Artifact 不存在")
            submission = session.get(ExperimentSubmission, artifact.submission_id)
            if submission is None or submission.project_id != project_id:
                raise ResourceNotFoundError("项目中不存在该 Artifact")
            role = self._projects.require_member(
                session, user_id=identity.user_id, team_id=project.team_id
            )
            if (
                role is TeamRole.RESEARCHER
                and submission.submitted_by != identity.user_id
                and artifact.experiment_id is None
            ):
                raise AuthorizationError("Researcher 不能下载其他成员的草稿 Artifact")
            if not artifact.cloud_hash_verified or not artifact.s3_version_id:
                raise ConflictError("Artifact 尚未完成云端哈希和不可变版本校验")
            download = self._artifact_storage.create_download_url(
                object_key=artifact.s3_key,
                version_id=artifact.s3_version_id,
                filename=artifact.filename,
                expires_in=self._download_expires_seconds,
            )
            session.add(
                AuditLog(
                    team_id=project.team_id,
                    project_id=project_id,
                    actor_type="USER",
                    actor_id=identity.user_id,
                    action="artifact.download_url.created",
                    target_type="ARTIFACT",
                    target_id=artifact.id,
                    before_value=None,
                    after_value={
                        "s3_version_id": artifact.s3_version_id,
                        "expires_at": download.expires_at.isoformat(),
                        "session_or_token_id": str(identity.token_id),
                    },
                )
            )
            return ArtifactDownloadResult(artifact_id=artifact.id, **download.model_dump())

    def _require_project(
        self,
        session: Session,
        project_id: UUID,
        identity: RequestIdentity,
        *,
        owner: bool = False,
    ) -> Project:
        if identity.project_id is not None and identity.project_id != project_id:
            raise AuthorizationError("当前凭据未绑定该项目")
        project = self._projects.require_project_member(
            session,
            project_id=project_id,
            user_id=identity.user_id,
            team_id=identity.team_id,
        )
        if owner:
            self._projects.require_member(
                session,
                user_id=identity.user_id,
                team_id=project.team_id,
                allowed_roles={TeamRole.OWNER},
            )
        return project

    @staticmethod
    def _require_scope(identity: RequestIdentity, scope: str) -> None:
        if scope not in identity.scopes:
            raise AuthorizationError(f"当前身份缺少 {scope} scope")

    @staticmethod
    def _validate_configuration(request: PolicyPublishRequest) -> None:
        flattened = flatten_configuration(request.context.active_config)
        for item in request.constraints:
            if item.parameter_path not in flattened:
                raise InputValidationError(f"约束路径 {item.parameter_path} 不存在于 active_config")
            if not _same_json_value(flattened[item.parameter_path], item.expected_value):
                raise InputValidationError(
                    f"约束 {item.parameter_path} 的 expected_value 与 active_config 不一致"
                )

    @staticmethod
    def _project_summary(project: Project) -> ProjectSummary:
        return ProjectSummary(
            project_id=project.id,
            name=project.name,
            description=project.description,
            repository_url=project.repository_url,
            active=project.active,
        )

    @staticmethod
    def _plan_view(row: PlanCheck, role: TeamRole) -> PlanCheckWebView:
        can_manifest = row.check_result is CheckResult.PASS or (
            row.check_result is CheckResult.NEEDS_APPROVAL
            and row.approval_status is ApprovalStatus.APPROVED
        )
        report = {
            **row.report,
            "approval_status": row.approval_status.value,
            "can_create_manifest": can_manifest,
        }
        actions = []
        if role is TeamRole.OWNER and row.approval_status is ApprovalStatus.PENDING:
            actions = ["APPROVE", "REJECT"]
        return PlanCheckWebView(
            plan_check_id=row.id,
            project_id=row.project_id,
            requester_id=row.requester_id,
            context_id=row.context_id,
            context_version=row.context_version,
            intent_id=row.intent_id,
            intent_version=row.intent_version,
            experiment_mode=row.experiment_mode,
            check_result=row.check_result,
            approval_status=row.approval_status,
            risk_level=row.risk_level,
            planned_changes=row.planned_changes,
            report=report,
            git_commit=row.git_commit,
            command=row.command,
            created_at=row.created_at,
            allowed_actions=actions,
        )

    @staticmethod
    def _submission_view(
        session: Session, row: ExperimentSubmission, role: TeamRole
    ) -> SubmissionWebView:
        risks = session.scalars(
            select(SubmissionRisk)
            .where(SubmissionRisk.submission_id == row.id)
            .order_by(SubmissionRisk.severity.desc(), SubmissionRisk.created_at)
        ).all()
        artifacts = session.scalars(
            select(Artifact).where(Artifact.submission_id == row.id).order_by(Artifact.filename)
        ).all()
        actions: list[str] = []
        if (
            row.status is SubmissionStatus.NEEDS_REVIEW
            and row.workflow_status is WorkflowStatus.COMPLETED
            and row.processing_step is WorkflowStep.NEEDS_REVIEW
        ):
            eligibility_value = (row.review_receipt or {}).get("review_eligibility")
            if eligibility_value != ReviewEligibility.BLOCKED.value and (
                role is TeamRole.OWNER or eligibility_value != ReviewEligibility.OWNER_ONLY.value
            ):
                actions.append("APPROVE")
            actions.append("REJECT")
        return SubmissionWebView(
            submission_id=row.id,
            project_id=row.project_id,
            run_manifest_id=row.run_manifest_id,
            submitted_by=row.submitted_by,
            source_agent=row.source_agent,
            status=row.status,
            workflow_status=row.workflow_status,
            processing_step=row.processing_step,
            processing_error=row.processing_error,
            generated_summary=row.generated_summary,
            review_receipt=row.review_receipt,
            risks=[
                {
                    "risk_id": str(item.id),
                    "severity": item.severity.value,
                    "risk_type": item.risk_type,
                    "field_path": item.field_path,
                    "previous_value": item.previous_value,
                    "current_value": item.current_value,
                    "expected_value": item.expected_value,
                    "message": item.message,
                    "impact": item.impact,
                    "evidence_type": item.evidence_type.value if item.evidence_type else None,
                    "evidence_source": item.evidence_source,
                    "blocking": item.blocking,
                    "resolved": item.resolved,
                }
                for item in risks
            ],
            artifacts=[
                ArtifactWebView(
                    artifact_id=item.id,
                    filename=item.filename,
                    mime_type=item.mime_type,
                    size_bytes=item.size_bytes,
                    sha256=item.sha256,
                    artifact_type=item.artifact_type.value,
                    cloud_hash_verified=item.cloud_hash_verified,
                    s3_version_id=item.s3_version_id,
                )
                for item in artifacts
            ],
            created_at=row.created_at,
            updated_at=row.updated_at,
            allowed_actions=actions,
        )

    @staticmethod
    def _experiment_view(row: Experiment) -> ExperimentWebView:
        return ExperimentWebView(
            experiment_id=row.id,
            project_id=row.project_id,
            submission_id=row.submission_id,
            run_manifest_id=row.run_manifest_id,
            name=row.name,
            model_name=row.model_name,
            dataset=row.dataset,
            protocol=row.protocol,
            seed=row.seed,
            experiment_mode=row.experiment_mode,
            status=row.status,
            context_id=row.project_context_id,
            context_version=row.project_context_version,
            intent_id=row.intent_id,
            intent_version=row.intent_version,
            config_hash=row.config_hash,
            git_commit=row.git_commit,
            summary=row.summary_snapshot,
            confirmed_at=row.confirmed_at,
            created_at=row.created_at,
        )
