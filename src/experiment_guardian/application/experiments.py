"""R13 正式实验确认和结构化/向量查询服务。"""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Float, bindparam, cast, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.async_review import (
    EMBEDDING_DIMENSION,
    EMBEDDING_DOCUMENT_VERSION,
    build_embedding_document,
    review_eligibility_for_risks,
    review_receipt_source_hash,
)
from experiment_guardian.application.errors import (
    AuthorizationError,
    ConflictError,
    RecentAuthenticationRequiredError,
    ResourceNotFoundError,
)
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.ports import EmbeddingGenerator
from experiment_guardian.application.transactions import run_with_serialization_retry
from experiment_guardian.domain.administration import (
    SubmissionDecisionRequest,
    SubmissionDecisionResult,
)
from experiment_guardian.domain.contracts import (
    ExperimentArtifactView,
    ExperimentMetricView,
    ExperimentQueryCommand,
    ExperimentQueryResult,
    GeneratedSummary,
    MaterialProvenance,
    SubmissionReceipt,
    SubmittedResultDocument,
)
from experiment_guardian.domain.enums import (
    ApprovalDecision,
    ApprovalTargetType,
    EvidenceType,
    ExperimentStatus,
    IdempotencyOperationStatus,
    ReviewEligibility,
    SubmissionStatus,
    TeamRole,
    VerificationStatus,
    WorkflowStatus,
    WorkflowStep,
)
from experiment_guardian.infrastructure.models import (
    ApprovalRecord,
    Artifact,
    AuditLog,
    Experiment,
    ExperimentIntent,
    ExperimentMetric,
    ExperimentSubmission,
    IdempotencyRecord,
    Memory,
    PlanCheck,
    ProjectContext,
    RunManifest,
    SubmissionEmbedding,
    SubmissionRisk,
)
from experiment_guardian.infrastructure.models.base import VectorType
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyGovernanceRepository,
    SqlAlchemyProjectRepository,
    SqlAlchemySubmissionRepository,
)

SUBMISSION_DECISION_OPERATION = "submission.decision"
MEMORY_TYPE = "EXPERIMENT_REVIEW_V1"
MEMORY_SOURCE_TYPE = "SUBMISSION_EMBEDDING"
MAX_VECTOR_CANDIDATES = 200


@dataclass(frozen=True, slots=True)
class SubmissionReviewBasis:
    """从正式表读取的 Submission 审核依据，不是新的事实源。"""

    team_id: UUID
    role: TeamRole
    submission: ExperimentSubmission
    receipt: SubmissionReceipt
    risks: tuple[SubmissionRisk, ...]
    eligibility: ReviewEligibility
    manifest: RunManifest | None
    plan: PlanCheck | None
    intent: ExperimentIntent | None
    context: ProjectContext | None
    artifacts: tuple[Artifact, ...]
    embedding: SubmissionEmbedding | None
    approval_material_issues: tuple[str, ...]


class ExperimentReviewService:
    """在一个数据库事务中批准或拒绝待审核 Submission。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        project_repository: SqlAlchemyProjectRepository,
        governance_repository: SqlAlchemyGovernanceRepository,
        submission_repository: SqlAlchemySubmissionRepository,
    ) -> None:
        self._session_factory = session_factory
        self._projects = project_repository
        self._governance = governance_repository
        self._submissions = submission_repository

    def decide(
        self,
        *,
        identity: RequestIdentity,
        project_id: UUID,
        submission_id: UUID,
        idempotency_key: UUID,
        request: SubmissionDecisionRequest,
    ) -> SubmissionDecisionResult:
        request_hash = _canonical_hash(
            {
                "project_id": str(project_id),
                "submission_id": str(submission_id),
                "request": request.model_dump(mode="json"),
            }
        )
        return run_with_serialization_retry(
            lambda: self._decide_once(
                identity=identity,
                project_id=project_id,
                submission_id=submission_id,
                idempotency_key=idempotency_key,
                request=request,
                request_hash=request_hash,
            )
        )

    def _decide_once(
        self,
        *,
        identity: RequestIdentity,
        project_id: UUID,
        submission_id: UUID,
        idempotency_key: UUID,
        request: SubmissionDecisionRequest,
        request_hash: str,
    ) -> SubmissionDecisionResult:
        try:
            with self._session_factory() as session, session.begin():
                return self.decide_in_session(
                    session,
                    identity=identity,
                    project_id=project_id,
                    submission_id=submission_id,
                    idempotency_key=idempotency_key,
                    request=request,
                    request_hash=request_hash,
                )
        except IntegrityError as exc:
            replay = self._replay_after_integrity(
                actor_id=identity.user_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
            raise ConflictError("Submission 审核与现有正式记录冲突") from exc

    def decide_in_session(
        self,
        session: Session,
        *,
        identity: RequestIdentity,
        project_id: UUID,
        submission_id: UUID,
        idempotency_key: UUID,
        request: SubmissionDecisionRequest,
        request_hash: str | None = None,
        audit_context: dict[str, object] | None = None,
    ) -> SubmissionDecisionResult:
        """在调用方事务中执行正式审核，供直接 API 与 Proposal 确认共用。"""

        if "submission:review" not in identity.scopes:
            raise AuthorizationError("Token 缺少 submission:review scope")
        if identity.project_id is not None and identity.project_id != project_id:
            raise AuthorizationError("API Token 未绑定当前项目")
        effective_hash = request_hash or _canonical_hash(
            {
                "project_id": str(project_id),
                "submission_id": str(submission_id),
                "request": request.model_dump(mode="json"),
            }
        )
        project = self._projects.require_project_member(
            session,
            project_id=project_id,
            user_id=identity.user_id,
            team_id=identity.team_id,
        )
        role = self._projects.require_member(
            session, user_id=identity.user_id, team_id=project.team_id
        )
        existing = self._governance.find_idempotency(
            session,
            actor_id=identity.user_id,
            operation=SUBMISSION_DECISION_OPERATION,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return self._replay(existing, effective_hash)

        submission = self._submissions.get_submission_for_update(session, submission_id)
        if submission is None or submission.project_id != project_id:
            raise ResourceNotFoundError("项目中不存在该 Submission")
        if role is TeamRole.RESEARCHER and submission.submitted_by != identity.user_id:
            raise AuthorizationError("Researcher 只能审核自己提交的 Submission")
        if (
            submission.status is not SubmissionStatus.NEEDS_REVIEW
            or submission.workflow_status is not WorkflowStatus.COMPLETED
            or submission.processing_step is not WorkflowStep.NEEDS_REVIEW
        ):
            raise ConflictError("只有已完成分析的 NEEDS_REVIEW Submission 可以审核")
        if (
            session.scalar(
                select(ApprovalRecord).where(
                    ApprovalRecord.target_type == ApprovalTargetType.EXPERIMENT_SUBMISSION,
                    ApprovalRecord.target_id == submission.id,
                )
            )
            is not None
        ):
            raise ConflictError("该 Submission 已存在最终审核决定")

        risks = self._submissions.list_risks(session, submission.id)
        eligibility = review_eligibility_for_risks(risks)
        if (
            request.decision is ApprovalDecision.APPROVED
            and role is TeamRole.OWNER
            and eligibility is ReviewEligibility.OWNER_ONLY
            and identity.authentication_method == "WEB_SESSION"
            and not identity.recent_authentication
        ):
            raise RecentAuthenticationRequiredError("Owner 批准 Submission 前需要完成近期身份认证")
        receipt = self._load_receipt(submission)
        if request.decision is ApprovalDecision.APPROVED:
            self._authorize_approval(role, eligibility)

        now = datetime.now(UTC)
        approval = ApprovalRecord(
            project_id=project_id,
            target_type=ApprovalTargetType.EXPERIMENT_SUBMISSION,
            target_id=submission.id,
            approval_type="EXPERIMENT_SUBMISSION_REVIEW",
            status=request.decision,
            requested_by=submission.submitted_by,
            decided_by=identity.user_id,
            request_reason=json.dumps(
                {
                    "objective": receipt.objective,
                    "review_eligibility": eligibility.value,
                    "source_hash": receipt.source_hash,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            decision_reason=request.decision_reason,
            decided_at=now,
        )
        session.add(approval)
        session.flush()

        if request.decision is ApprovalDecision.APPROVED:
            experiment = self._approve(
                session,
                identity=identity,
                submission=submission,
                receipt=receipt,
                risks=risks,
                approval_record_id=approval.id,
            )
            submission.status = SubmissionStatus.APPROVED
        else:
            experiment = None
            submission.status = SubmissionStatus.REJECTED

        result = SubmissionDecisionResult(
            approval_record_id=approval.id,
            project_id=project_id,
            submission_id=submission.id,
            experiment_id=experiment.id if experiment else None,
            decision=request.decision,
            submission_status=submission.status,
            review_eligibility=eligibility,
            requested_by=submission.submitted_by,
            decided_by=identity.user_id,
            decided_at=now,
            decision_reason=request.decision_reason,
        )
        session.add_all(
            [
                AuditLog(
                    team_id=project.team_id,
                    project_id=project_id,
                    actor_type="USER",
                    actor_id=identity.user_id,
                    action=SUBMISSION_DECISION_OPERATION,
                    target_type=ApprovalTargetType.EXPERIMENT_SUBMISSION.value,
                    target_id=submission.id,
                    before_value={"status": SubmissionStatus.NEEDS_REVIEW.value},
                    after_value={
                        **result.model_dump(mode="json"),
                        "token_id": str(identity.token_id),
                        **(audit_context or {}),
                    },
                ),
                IdempotencyRecord(
                    actor_id=identity.user_id,
                    operation=SUBMISSION_DECISION_OPERATION,
                    idempotency_key=idempotency_key,
                    request_hash=effective_hash,
                    response_snapshot=result.model_dump(mode="json"),
                    operation_status=IdempotencyOperationStatus.COMPLETED,
                    expires_at=now + timedelta(days=7),
                ),
            ]
        )
        return result

    def load_proposal_basis_in_session(
        self,
        session: Session,
        *,
        identity: RequestIdentity,
        project_id: UUID,
        submission_id: UUID,
        for_update: bool = False,
    ) -> SubmissionReviewBasis:
        """读取可冻结的审核依据；不创建审批或正式实验。"""

        if "submission:read" not in identity.scopes:
            raise AuthorizationError("当前身份缺少 submission:read scope")
        project = self._projects.require_project_member(
            session,
            project_id=project_id,
            user_id=identity.user_id,
            team_id=identity.team_id,
        )
        role = self._projects.require_member(
            session, user_id=identity.user_id, team_id=project.team_id
        )
        statement = select(ExperimentSubmission).where(
            ExperimentSubmission.id == submission_id,
            ExperimentSubmission.project_id == project_id,
        )
        if for_update:
            statement = statement.with_for_update()
        submission = session.scalar(statement)
        if submission is None or (
            role is TeamRole.RESEARCHER and submission.submitted_by != identity.user_id
        ):
            raise ResourceNotFoundError("项目中不存在当前用户可访问的 Submission")
        if (
            submission.status is not SubmissionStatus.NEEDS_REVIEW
            or submission.workflow_status is not WorkflowStatus.COMPLETED
            or submission.processing_step is not WorkflowStep.NEEDS_REVIEW
        ):
            raise ConflictError("只有已完成分析的 NEEDS_REVIEW Submission 可以准备提案")
        approval = session.scalar(
            select(ApprovalRecord).where(
                ApprovalRecord.target_type == ApprovalTargetType.EXPERIMENT_SUBMISSION,
                ApprovalRecord.target_id == submission.id,
            )
        )
        experiment = session.scalar(
            select(Experiment).where(Experiment.submission_id == submission.id)
        )
        if approval is not None or experiment is not None:
            raise ConflictError("该 Submission 已存在最终审核或正式 Experiment")

        receipt = self._load_receipt(submission)
        risks = tuple(self._submissions.list_risks(session, submission.id))
        eligibility = review_eligibility_for_risks(list(risks))
        if (
            receipt.review_eligibility is not eligibility
            or receipt.source_hash != review_receipt_source_hash(receipt, list(risks))
        ):
            raise ConflictError("Submission 审核回执与当前风险来源不一致")

        manifest = session.get(RunManifest, submission.run_manifest_id)
        plan = session.get(PlanCheck, manifest.plan_check_id) if manifest else None
        intent = session.get(ExperimentIntent, manifest.intent_id) if manifest else None
        context = session.get(ProjectContext, manifest.context_id) if manifest else None
        artifacts = tuple(
            self._submissions.list_artifacts_for_update(session, submission.id)
            if for_update
            else self._submissions.list_artifacts(session, submission.id)
        )
        embedding = self._submissions.get_embedding(session, submission.id, for_update=for_update)
        issues = self._approval_material_issues(
            submission=submission,
            receipt=receipt,
            risks=list(risks),
            manifest=manifest,
            plan=plan,
            intent=intent,
            context=context,
            artifacts=list(artifacts),
            embedding=embedding,
        )
        return SubmissionReviewBasis(
            team_id=project.team_id,
            role=role,
            submission=submission,
            receipt=receipt,
            risks=risks,
            eligibility=eligibility,
            manifest=manifest,
            plan=plan,
            intent=intent,
            context=context,
            artifacts=artifacts,
            embedding=embedding,
            approval_material_issues=tuple(issues),
        )

    @classmethod
    def _approval_material_issues(
        cls,
        *,
        submission: ExperimentSubmission,
        receipt: SubmissionReceipt,
        risks: list[SubmissionRisk],
        manifest: RunManifest | None,
        plan: PlanCheck | None,
        intent: ExperimentIntent | None,
        context: ProjectContext | None,
        artifacts: list[Artifact],
        embedding: SubmissionEmbedding | None,
    ) -> list[str]:
        issues: list[str] = []
        if (
            manifest is None
            or plan is None
            or intent is None
            or context is None
            or manifest.project_id != submission.project_id
            or plan.project_id != submission.project_id
            or intent.project_id != submission.project_id
            or context.project_id != submission.project_id
            or manifest.context_id != plan.context_id
            or manifest.context_version != plan.context_version
            or manifest.intent_id != plan.intent_id
            or manifest.intent_version != plan.intent_version
            or intent.id != manifest.intent_id
            or intent.version != manifest.intent_version
            or context.id != manifest.context_id
            or context.version != manifest.context_version
        ):
            issues.append("Context/Intent/Plan Check/Manifest 追溯链不完整")
        elif (
            submission.manifest_hash != manifest.manifest_hash
            or receipt.submission_id != submission.id
            or receipt.trace.project_id != submission.project_id
            or receipt.trace.context_id != manifest.context_id
            or receipt.trace.context_version != manifest.context_version
            or receipt.trace.intent_id != manifest.intent_id
            or receipt.trace.intent_version != manifest.intent_version
            or receipt.trace.plan_check_id != manifest.plan_check_id
            or receipt.trace.run_manifest_id != manifest.id
            or receipt.trace.manifest_hash != manifest.manifest_hash
            or not cls._receipt_invariant_trace_matches(
                submission=submission,
                receipt=receipt,
                manifest=manifest,
                plan=plan,
            )
        ):
            issues.append("审核回执与 Manifest 追溯不一致")

        if embedding is None:
            issues.append("Submission embedding 缺失")
        else:
            document = build_embedding_document(receipt, risks)
            document_hash = hashlib.sha256(document.encode("utf-8")).hexdigest()
            if (
                embedding.input_text != document
                or embedding.input_sha256 != document_hash
                or embedding.dimension != EMBEDDING_DIMENSION
                or not embedding.normalized
            ):
                issues.append("Submission embedding 与审核来源不一致")
        if not artifacts:
            issues.append("Submission Artifact 缺失")
        elif any(
            not item.cloud_hash_verified
            or item.verified_at is None
            or not item.s3_version_id
            or item.experiment_id is not None
            for item in artifacts
        ):
            issues.append("Submission Artifact 缺少固定版本证据或已被关联")
        try:
            GeneratedSummary.model_validate(submission.generated_summary)
            cls._result_document(submission)
        except ValidationError:
            issues.append("Submission 摘要或结果快照无效")
        return issues

    @staticmethod
    def _receipt_invariant_trace_matches(
        *,
        submission: ExperimentSubmission,
        receipt: SubmissionReceipt,
        manifest: RunManifest,
        plan: PlanCheck,
    ) -> bool:
        if manifest.schema_version == 1:
            return (
                receipt.trace.experiment_plan_decision_id is None
                and receipt.trace.experiment_plan_revision_id is None
                and receipt.trace.invariant_status is None
            )
        if not isinstance(manifest.evidence_snapshot, dict):
            return False
        snapshot = manifest.evidence_snapshot.get("experiment_plan")
        trace = snapshot.get("trace") if isinstance(snapshot, dict) else None
        analysis = submission.analysis_snapshot
        invariant = analysis.get("invariant_validation") if isinstance(analysis, dict) else None
        if not isinstance(trace, dict) or not isinstance(invariant, dict):
            return False
        try:
            revision_id = UUID(str(trace.get("revision_id")))
        except (TypeError, ValueError):
            return False
        return bool(
            plan.experiment_plan_decision_id is not None
            and trace.get("decision_id") == str(plan.experiment_plan_decision_id)
            and receipt.trace.experiment_plan_decision_id
            == plan.experiment_plan_decision_id
            and receipt.trace.experiment_plan_revision_id
            == revision_id
            and receipt.trace.invariant_status == invariant.get("overall_status")
        )

    @staticmethod
    def _load_receipt(submission: ExperimentSubmission) -> SubmissionReceipt:
        try:
            return SubmissionReceipt.model_validate(submission.review_receipt)
        except ValidationError as exc:
            raise ConflictError("Submission 审核回执缺失或已损坏") from exc

    @staticmethod
    def _authorize_approval(role: TeamRole, eligibility: ReviewEligibility) -> None:
        if eligibility is ReviewEligibility.BLOCKED:
            raise ConflictError("存在 CRITICAL 或 blocking 风险，不能批准 Submission")
        if eligibility is ReviewEligibility.OWNER_ONLY and role is not TeamRole.OWNER:
            raise AuthorizationError("该 Submission 包含 HIGH 风险，只能由 Owner 批准")

    def _approve(
        self,
        session: Session,
        *,
        identity: RequestIdentity,
        submission: ExperimentSubmission,
        receipt: SubmissionReceipt,
        risks: list[SubmissionRisk],
        approval_record_id: UUID,
    ) -> Experiment:
        manifest = session.get(RunManifest, submission.run_manifest_id)
        plan = session.get(PlanCheck, manifest.plan_check_id) if manifest else None
        intent = session.get(ExperimentIntent, manifest.intent_id) if manifest else None
        context = session.get(ProjectContext, manifest.context_id) if manifest else None
        embedding = self._submissions.get_embedding(session, submission.id, for_update=True)
        artifacts = self._submissions.list_artifacts_for_update(session, submission.id)
        if (
            manifest is None
            or plan is None
            or intent is None
            or context is None
            or embedding is None
            or manifest.project_id != submission.project_id
            or plan.project_id != submission.project_id
            or intent.project_id != submission.project_id
            or context.project_id != submission.project_id
            or manifest.context_id != plan.context_id
            or manifest.context_version != plan.context_version
            or manifest.intent_id != plan.intent_id
            or manifest.intent_version != plan.intent_version
            or intent.id != manifest.intent_id
            or intent.version != manifest.intent_version
            or context.id != manifest.context_id
            or context.version != manifest.context_version
        ):
            raise ConflictError("Submission 的 Context/Intent/Plan Check/Manifest 追溯链不完整")
        if (
            receipt.submission_id != submission.id
            or receipt.trace.project_id != submission.project_id
            or receipt.trace.context_id != manifest.context_id
            or receipt.trace.context_version != manifest.context_version
            or receipt.trace.intent_id != manifest.intent_id
            or receipt.trace.intent_version != manifest.intent_version
            or receipt.trace.plan_check_id != manifest.plan_check_id
            or receipt.trace.run_manifest_id != manifest.id
            or receipt.trace.manifest_hash != manifest.manifest_hash
            or receipt.review_eligibility != review_eligibility_for_risks(risks)
            or receipt.source_hash != review_receipt_source_hash(receipt, risks)
        ):
            raise ConflictError("Submission 审核回执与正式来源不一致")
        document = build_embedding_document(receipt, risks)
        document_hash = hashlib.sha256(document.encode("utf-8")).hexdigest()
        if (
            embedding.input_text != document
            or embedding.input_sha256 != document_hash
            or embedding.dimension != EMBEDDING_DIMENSION
            or not embedding.normalized
        ):
            raise ConflictError("Submission embedding 与审核来源不一致")
        if not artifacts or any(
            not item.cloud_hash_verified or item.verified_at is None or not item.s3_version_id
            for item in artifacts
        ):
            raise ConflictError("Submission Artifact 缺少云端哈希或不可变版本证据")
        try:
            summary = GeneratedSummary.model_validate(submission.generated_summary)
            result_document = self._result_document(submission)
        except ValidationError as exc:
            raise ConflictError("Submission 摘要或结果快照无效") from exc

        now = datetime.now(UTC)
        experiment = Experiment(
            project_id=submission.project_id,
            intent_id=manifest.intent_id,
            run_manifest_id=manifest.id,
            submission_id=submission.id,
            project_context_id=manifest.context_id,
            project_context_version=manifest.context_version,
            intent_version=manifest.intent_version,
            approval_record_id=approval_record_id,
            experiment_mode=manifest.experiment_mode,
            eligible_as_baseline=False,
            name=intent.name,
            model_name=context.mainline_model,
            dataset=manifest.dataset,
            protocol=manifest.protocol,
            seed=manifest.seed,
            status=ExperimentStatus(result_document.status.value),
            config_hash=manifest.config_hash,
            git_commit=manifest.git_commit,
            checkpoint=manifest.checkpoint,
            command=manifest.command,
            summary_snapshot=summary.model_dump(mode="json"),
            review_receipt_snapshot=receipt.model_dump(mode="json"),
            started_at=result_document.started_at,
            completed_at=result_document.completed_at,
            confirmed_by=identity.user_id,
            confirmed_at=now,
        )
        session.add(experiment)
        session.flush()

        primary_metric = _primary_metric_name(
            plan.context_snapshot,
            context.primary_metric,
        )
        session.add_all(
            [
                ExperimentMetric(
                    experiment_id=experiment.id,
                    name=name,
                    value=value,
                    split="REPORTED",
                    aggregation_type="SINGLE_RUN",
                    epoch=None,
                    is_primary=name == primary_metric,
                )
                for name, value in sorted(result_document.metrics.items())
            ]
        )
        for artifact in artifacts:
            artifact.experiment_id = experiment.id
        session.add(
            Memory(
                project_id=submission.project_id,
                experiment_id=experiment.id,
                protocol=manifest.protocol,
                model_name=context.mainline_model,
                seed=manifest.seed,
                experiment_status=experiment.status,
                current_valid=True,
                memory_type=MEMORY_TYPE,
                content=embedding.input_text,
                embedding=embedding.embedding,
                embedding_provider=embedding.provider,
                embedding_model_id=embedding.model_id,
                embedding_dimension=embedding.dimension,
                embedding_normalized=embedding.normalized,
                document_version=embedding.document_version,
                content_sha256=embedding.input_sha256,
                verification_status=VerificationStatus.CONFIRMED,
                source_type=MEMORY_SOURCE_TYPE,
                source_id=submission.id,
            )
        )
        return experiment

    @staticmethod
    def _result_document(submission: ExperimentSubmission) -> SubmittedResultDocument:
        snapshot = submission.analysis_snapshot
        parsed_documents = snapshot.get("parsed_documents") if isinstance(snapshot, dict) else None
        result = parsed_documents.get("result") if isinstance(parsed_documents, dict) else None
        parsed = result.get("parsed") if isinstance(result, dict) else None
        return SubmittedResultDocument.model_validate(parsed)

    @staticmethod
    def _replay(record: IdempotencyRecord, request_hash: str) -> SubmissionDecisionResult:
        if record.request_hash != request_hash:
            raise ConflictError("相同 Idempotency-Key 已用于不同的 Submission 审核请求")
        if (
            record.operation_status is not IdempotencyOperationStatus.COMPLETED
            or record.response_snapshot is None
        ):
            raise ConflictError("相同 Idempotency-Key 的 Submission 审核仍在处理中")
        return SubmissionDecisionResult.model_validate(record.response_snapshot)

    def _replay_after_integrity(
        self, *, actor_id: UUID, idempotency_key: UUID, request_hash: str
    ) -> SubmissionDecisionResult | None:
        with self._session_factory() as session:
            record = self._governance.find_idempotency(
                session,
                actor_id=actor_id,
                operation=SUBMISSION_DECISION_OPERATION,
                idempotency_key=idempotency_key,
            )
            return None if record is None else self._replay(record, request_hash)


class ExperimentQueryService:
    """正式 Experiment 查询；向量只参与结构化候选集的排序。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        project_repository: SqlAlchemyProjectRepository,
        embedding_generator: EmbeddingGenerator,
    ) -> None:
        self._session_factory = session_factory
        self._projects = project_repository
        self._generator = embedding_generator

    def query(
        self, command: ExperimentQueryCommand, identity: RequestIdentity
    ) -> list[ExperimentQueryResult]:
        self._authorize(command.project_id, identity)
        if command.experiment_id is not None:
            with self._session_factory() as session:
                record = self._load_result(
                    session,
                    project_id=command.project_id,
                    experiment_id=command.experiment_id,
                    include_historical=command.include_historical,
                    detail_level="FULL",
                    similarity=None,
                )
                return [] if record is None else [record]

        with self._session_factory() as session:
            candidate_ids = self._candidate_ids(session, command)
        if not candidate_ids:
            return []
        vector = _validate_query_vector(self._generator.embed(command.query or "").vector)
        with self._session_factory() as session:
            ranked = self._rank(session, candidate_ids, vector, command.top_k)
            return [
                result
                for memory_id, similarity in ranked
                if (
                    result := self._load_result_by_memory(
                        session,
                        project_id=command.project_id,
                        memory_id=memory_id,
                        include_historical=command.include_historical,
                        detail_level="SUMMARY",
                        similarity=similarity,
                    )
                )
                is not None
            ]

    def _authorize(self, project_id: UUID, identity: RequestIdentity) -> None:
        if "experiment:query" not in identity.scopes:
            raise AuthorizationError("Token 缺少 experiment:query scope")
        if identity.project_id != project_id:
            raise AuthorizationError("MCP Token 未绑定当前项目")
        with self._session_factory() as session:
            self._projects.require_project_member(
                session,
                project_id=project_id,
                user_id=identity.user_id,
                team_id=identity.team_id,
            )

    def _candidate_ids(self, session: Session, command: ExperimentQueryCommand) -> list[UUID]:
        statement = select(Memory.id).where(
            Memory.project_id == command.project_id,
            Memory.verification_status == VerificationStatus.CONFIRMED,
            Memory.protocol == command.protocol,
            Memory.experiment_status.in_(command.statuses),
            Memory.embedding_provider == getattr(self._generator, "provider", "bedrock"),
            Memory.embedding_model_id == self._generator.model_id,
            Memory.embedding_dimension == EMBEDDING_DIMENSION,
            Memory.embedding_normalized.is_(True),
            Memory.document_version == EMBEDDING_DOCUMENT_VERSION,
        )
        if not command.include_historical:
            statement = statement.where(Memory.current_valid.is_(True))
        if command.model_name is not None:
            statement = statement.where(Memory.model_name == command.model_name)
        if command.seed is not None:
            statement = statement.where(Memory.seed == command.seed)
        return list(
            session.scalars(
                statement.order_by(Memory.created_at.desc()).limit(MAX_VECTOR_CANDIDATES)
            ).all()
        )

    @staticmethod
    def _rank(
        session: Session, candidate_ids: list[UUID], vector: list[float], top_k: int
    ) -> list[tuple[UUID, float]]:
        if session.bind is not None and session.bind.dialect.name == "sqlite":
            memories = session.scalars(select(Memory).where(Memory.id.in_(candidate_ids))).all()
            ranked = sorted(
                ((item.id, _cosine_similarity(item.embedding, vector)) for item in memories),
                key=lambda item: (-item[1], str(item[0])),
            )
            return ranked[:top_k]
        query_vector = bindparam("query_vector", type_=VectorType(EMBEDDING_DIMENSION))
        distance = cast(
            Memory.embedding.op("<=>")(cast(query_vector, VectorType(EMBEDDING_DIMENSION))),
            Float,
        )
        rows = session.execute(
            select(Memory.id, distance.label("distance"))
            .where(Memory.id.in_(candidate_ids))
            .order_by(distance, Memory.id)
            .limit(top_k),
            {"query_vector": vector},
        ).all()
        return [(row.id, max(-1.0, min(1.0, 1.0 - float(row.distance)))) for row in rows]

    def _load_result_by_memory(
        self,
        session: Session,
        *,
        project_id: UUID,
        memory_id: UUID,
        include_historical: bool,
        detail_level: str,
        similarity: float | None,
    ) -> ExperimentQueryResult | None:
        memory = session.get(Memory, memory_id)
        if memory is None:
            return None
        return self._load_result(
            session,
            project_id=project_id,
            experiment_id=memory.experiment_id,
            include_historical=include_historical,
            detail_level=detail_level,
            similarity=similarity,
            memory=memory,
        )

    @staticmethod
    def _load_result(
        session: Session,
        *,
        project_id: UUID,
        experiment_id: UUID,
        include_historical: bool,
        detail_level: str,
        similarity: float | None,
        memory: Memory | None = None,
    ) -> ExperimentQueryResult | None:
        experiment = session.get(Experiment, experiment_id)
        if experiment is None or experiment.project_id != project_id:
            return None
        if (
            experiment.status in {ExperimentStatus.DEPRECATED, ExperimentStatus.SUPERSEDED}
            and not include_historical
        ):
            return None
        memory = memory or session.scalar(
            select(Memory).where(
                Memory.experiment_id == experiment.id,
                Memory.memory_type == MEMORY_TYPE,
            )
        )
        manifest = session.get(RunManifest, experiment.run_manifest_id)
        if memory is None or manifest is None:
            raise ConflictError("正式 Experiment 的 Memory 或 Manifest 追溯链不完整")
        if memory.project_id != project_id or manifest.project_id != project_id:
            raise ConflictError("正式 Experiment 的项目追溯链不一致")
        if memory.verification_status is not VerificationStatus.CONFIRMED:
            return None
        if not include_historical and not memory.current_valid:
            return None
        if (
            experiment.status in {ExperimentStatus.DEPRECATED, ExperimentStatus.SUPERSEDED}
            and memory.current_valid
        ):
            raise ConflictError("历史 Experiment 不能标记为当前有效")
        try:
            summary = GeneratedSummary.model_validate(experiment.summary_snapshot)
        except ValidationError as exc:
            raise ConflictError("正式 Experiment 摘要快照无效") from exc
        metrics = list(
            session.scalars(
                select(ExperimentMetric)
                .where(ExperimentMetric.experiment_id == experiment.id)
                .order_by(ExperimentMetric.is_primary.desc(), ExperimentMetric.name)
            ).all()
        )
        full = detail_level == "FULL"
        artifacts = (
            list(
                session.scalars(
                    select(Artifact)
                    .where(Artifact.experiment_id == experiment.id)
                    .order_by(Artifact.artifact_type, Artifact.filename)
                ).all()
            )
            if full
            else []
        )
        if any(not item.s3_version_id for item in artifacts):
            raise ConflictError("正式 Experiment 的 Artifact 版本证据不完整")
        return ExperimentQueryResult(
            experiment_id=experiment.id,
            submission_id=experiment.submission_id,
            name=experiment.name,
            experiment_mode=experiment.experiment_mode,
            status=experiment.status,
            dataset=experiment.dataset,
            protocol=experiment.protocol,
            model_name=experiment.model_name,
            seed=experiment.seed,
            current_valid=memory.current_valid,
            verification_status=memory.verification_status,
            manifest_id=manifest.id,
            manifest_hash=manifest.manifest_hash,
            plan_check_id=manifest.plan_check_id,
            context_id=experiment.project_context_id,
            context_version=experiment.project_context_version,
            intent_id=experiment.intent_id,
            intent_version=experiment.intent_version,
            retrieval_role="STRUCTURED_RECORD" if full else "CANDIDATE_EVIDENCE",
            detail_level="FULL" if full else "SUMMARY",
            vector_similarity=similarity,
            summary=summary,
            metrics=[
                ExperimentMetricView(
                    name=item.name,
                    value=item.value,
                    split=item.split,
                    aggregation_type=item.aggregation_type,
                    epoch=item.epoch,
                    is_primary=item.is_primary,
                )
                for item in metrics
            ],
            config_hash=experiment.config_hash,
            config_snapshot=manifest.config_snapshot if full else None,
            git_branch=manifest.git_branch if full else None,
            git_commit=experiment.git_commit,
            command=experiment.command if full else None,
            checkpoint=experiment.checkpoint if full else None,
            artifacts=[
                ExperimentArtifactView(
                    artifact_id=item.id,
                    filename=item.filename,
                    mime_type=item.mime_type,
                    size_bytes=item.size_bytes,
                    sha256=item.sha256,
                    artifact_type=item.artifact_type,
                    s3_version_id=item.s3_version_id or "",
                    evidence_type=EvidenceType.CLOUD_VERIFIED,
                    material_origin=item.material_origin,
                    provenance=_validated_artifact_provenance(item),
                )
                for item in artifacts
            ],
        )


def _validated_artifact_provenance(artifact: Artifact) -> MaterialProvenance:
    try:
        provenance = MaterialProvenance.model_validate(artifact.provenance)
    except ValidationError as exc:
        raise ConflictError("正式 Experiment 的 Artifact provenance 无效") from exc
    if provenance.classification is not artifact.material_origin:
        raise ConflictError("正式 Experiment 的 Artifact 来源分类不一致")
    return provenance


def _primary_metric_name(
    context_snapshot: dict[str, object], context_primary_metric: dict[str, object]
) -> str | None:
    snapshot_primary: object = context_snapshot.get("primary_metric")
    payload = context_snapshot.get("payload")
    if isinstance(payload, dict):
        snapshot_primary = payload.get("primary_metric", snapshot_primary)
    snapshot_name = (
        snapshot_primary.get("name") if isinstance(snapshot_primary, dict) else None
    )
    context_name = context_primary_metric.get("name")
    if snapshot_name is not None and not isinstance(snapshot_name, str):
        raise ConflictError("Plan Check 的主指标快照无效")
    if context_name is not None and not isinstance(context_name, str):
        raise ConflictError("Context 的主指标定义无效")
    if snapshot_name and context_name and snapshot_name != context_name:
        raise ConflictError("Plan Check 与绑定 Context 的主指标不一致")
    return snapshot_name or context_name


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_query_vector(vector: object) -> list[float]:
    if not isinstance(vector, list) or len(vector) != EMBEDDING_DIMENSION:
        raise ValueError("查询 embedding 维度错误")
    normalized: list[float] = []
    for item in vector:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError("查询 embedding 包含非数值元素")
        number = float(item)
        if not math.isfinite(number):
            raise ValueError("查询 embedding 包含非有限数值")
        normalized.append(number)
    norm = math.sqrt(sum(item * item for item in normalized))
    if not math.isclose(norm, 1.0, rel_tol=1e-3, abs_tol=1e-3):
        raise ValueError("查询 embedding 未归一化")
    return normalized


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right, strict=True))))
