"""R12b embedding 与确定性审核回执的可恢复异步处理。"""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.errors import ServiceUnavailableError
from experiment_guardian.application.ports import (
    EmbeddingGenerator,
    QueueDelivery,
    SubmissionQueue,
)
from experiment_guardian.application.transactions import run_with_serialization_retry
from experiment_guardian.domain.contracts import (
    GeneratedSummary,
    ParameterChange,
    ReviewFact,
    ReviewTrace,
    RiskItem,
    SubmissionReceipt,
    WorkflowQueueEnvelope,
)
from experiment_guardian.domain.enums import (
    ApprovalDecision,
    ApprovalTargetType,
    EvidenceType,
    ProtectionLevel,
    ReviewEligibility,
    RiskSeverity,
    SubmissionStatus,
    WorkflowJobStatus,
    WorkflowJobType,
    WorkflowStatus,
    WorkflowStep,
)
from experiment_guardian.domain.run_manifest import canonical_json_hash
from experiment_guardian.infrastructure.models import (
    ApprovalRecord,
    ExperimentIntent,
    ExperimentSubmission,
    PlanCheck,
    SubmissionEmbedding,
    SubmissionRisk,
    WorkflowJob,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemySubmissionRepository,
    SqlAlchemyWorkflowRepository,
)
from experiment_guardian.workflows.submission import (
    WORKFLOW_ORDER,
    SubmissionWorkflowState,
    build_submission_workflow,
)

EMBEDDING_DOCUMENT_VERSION = "submission-search-v1"
EMBEDDING_DIMENSION = 1024
MAX_EMBEDDING_INPUT_CHARACTERS = 16000
MAX_REVIEW_RECEIPT_BYTES = 64 * 1024
MAX_VALUE_PREVIEW_CHARACTERS = 512
REVIEW_DISCLAIMER = (
    "该回执用于提高实验一致性、可追溯性和风险可见性；它不代表训练行为或实验结果已被完整验证。"
)
RISK_RANK = {
    RiskSeverity.LOW: 0,
    RiskSeverity.MEDIUM: 1,
    RiskSeverity.HIGH: 2,
    RiskSeverity.CRITICAL: 3,
}


class ReviewSourceError(RuntimeError):
    """持久化事实不完整或发生漂移，重试外部模型无法修复。"""


@dataclass(frozen=True, slots=True)
class _ReviewClaim:
    job_id: UUID
    submission_id: UUID
    generation: int
    lease_owner: str


@dataclass(frozen=True, slots=True)
class _ClaimResult:
    action: str
    claim: _ReviewClaim | None = None
    visibility_seconds: int = 0


@dataclass(frozen=True, slots=True)
class _PreparedReview:
    document_text: str
    document_hash: str
    receipt: SubmissionReceipt


class SubmissionJobProcessor:
    """按数据库 Job 类型路由同一个队列中的最小消息。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        workflow_repository: SqlAlchemyWorkflowRepository,
        queue: SubmissionQueue,
        *,
        summary_processor: Any,
        review_processor: "SubmissionReviewProcessor",
    ) -> None:
        self._session_factory = session_factory
        self._workflows = workflow_repository
        self._queue = queue
        self._summary_processor = summary_processor
        self._review_processor = review_processor

    def process_delivery(self, delivery: QueueDelivery) -> bool:
        try:
            envelope = WorkflowQueueEnvelope.model_validate_json(delivery.body)
        except (ValidationError, ValueError):
            self._queue.delete(delivery.receipt_handle)
            return True
        with self._session_factory() as session:
            job = self._workflows.get_job(session, envelope.job_id)
            job_type = job.job_type if job is not None else None
        if job_type is WorkflowJobType.SUBMISSION_SUMMARY:
            return bool(self._summary_processor.process_delivery(delivery))
        if job_type is WorkflowJobType.SUBMISSION_REVIEW_PREPARATION:
            return self._review_processor.process_delivery(delivery)
        self._queue.delete(delivery.receipt_handle)
        return True


class SubmissionReviewProcessor:
    """生成草稿向量和短审核回执；数据库游标是唯一恢复依据。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        submission_repository: SqlAlchemySubmissionRepository,
        workflow_repository: SqlAlchemyWorkflowRepository,
        queue: SubmissionQueue,
        generator: EmbeddingGenerator,
        *,
        worker_id: str,
        lease_seconds: int = 120,
    ) -> None:
        self._session_factory = session_factory
        self._submissions = submission_repository
        self._workflows = workflow_repository
        self._queue = queue
        self._generator = generator
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        handlers = {step: self._noop for step in WORKFLOW_ORDER[:6]}
        handlers[WorkflowStep.EMBEDDING_GENERATION] = self._embedding_generation
        handlers[WorkflowStep.NEEDS_REVIEW] = self._needs_review
        self._workflow = build_submission_workflow(handlers)

    def process_delivery(self, delivery: QueueDelivery) -> bool:
        try:
            envelope = WorkflowQueueEnvelope.model_validate_json(delivery.body)
        except (ValidationError, ValueError):
            self._queue.delete(delivery.receipt_handle)
            return True
        claim_result = run_with_serialization_retry(lambda: self._claim(envelope))
        if claim_result.action == "DELETE":
            self._queue.delete(delivery.receipt_handle)
            return True
        if claim_result.action == "WAIT":
            self._queue.change_visibility(delivery.receipt_handle, claim_result.visibility_seconds)
            return False
        if claim_result.action == "KEEP":
            self._queue.change_visibility(delivery.receipt_handle, self._lease_seconds)
            return False
        claim = claim_result.claim
        if claim is None:
            raise RuntimeError("Review Job claim 状态无效")

        state = self._workflow.invoke(
            {
                "submission_id": str(claim.submission_id),
                "job_id": str(claim.job_id),
                "generation": claim.generation,
                "lease_owner": claim.lease_owner,
            }
        )
        action = state.get("message_action", "KEEP")
        if action == "DELETE":
            self._queue.delete(delivery.receipt_handle)
            return True
        if action == "RETRY":
            delay = int(state.get("retry_delay_seconds", self._lease_seconds))
            self._queue.change_visibility(delivery.receipt_handle, delay)
            return False
        self._queue.change_visibility(delivery.receipt_handle, self._lease_seconds)
        return False

    @staticmethod
    def _noop(state: SubmissionWorkflowState) -> SubmissionWorkflowState:
        return state

    def _claim(self, envelope: WorkflowQueueEnvelope) -> _ClaimResult:
        with self._session_factory() as session, session.begin():
            job = self._workflows.get_job(session, envelope.job_id, for_update=True)
            if (
                job is None
                or job.submission_id != envelope.submission_id
                or job.generation != envelope.generation
                or job.job_type is not WorkflowJobType.SUBMISSION_REVIEW_PREPARATION
            ):
                return _ClaimResult("DELETE")
            submission = self._submissions.get_submission_for_update(
                session, envelope.submission_id
            )
            if submission is None:
                return _ClaimResult("DELETE")
            if job.status in {WorkflowJobStatus.SUCCEEDED, WorkflowJobStatus.FAILED}:
                return _ClaimResult("DELETE")
            if submission.review_receipt is not None:
                now = datetime.now(UTC)
                job.status = WorkflowJobStatus.SUCCEEDED
                job.completed_at = now
                job.lease_owner = None
                job.lease_expires_at = None
                submission.status = SubmissionStatus.NEEDS_REVIEW
                submission.workflow_status = WorkflowStatus.COMPLETED
                submission.processing_step = WorkflowStep.NEEDS_REVIEW
                submission.processing_error = None
                return _ClaimResult("DELETE")
            if job.status is WorkflowJobStatus.DEAD_LETTER:
                return _ClaimResult("KEEP")

            now = datetime.now(UTC)
            if (
                job.status is WorkflowJobStatus.RUNNING
                and job.lease_expires_at is not None
                and _as_utc(job.lease_expires_at) > now
            ):
                remaining = int((_as_utc(job.lease_expires_at) - now).total_seconds())
                return _ClaimResult("WAIT", visibility_seconds=max(1, remaining))
            if _as_utc(job.available_at) > now:
                remaining = int((_as_utc(job.available_at) - now).total_seconds())
                return _ClaimResult("WAIT", visibility_seconds=max(1, remaining))

            job.status = WorkflowJobStatus.RUNNING
            job.attempt_count += 1
            job.lease_owner = self._worker_id
            job.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
            job.started_at = job.started_at or now
            job.last_error = None
            submission.status = SubmissionStatus.PROCESSING
            submission.workflow_status = WorkflowStatus.RUNNING
            submission.processing_error = None
            return _ClaimResult(
                "RUN",
                claim=_ReviewClaim(
                    job_id=job.id,
                    submission_id=submission.id,
                    generation=job.generation,
                    lease_owner=self._worker_id,
                ),
            )

    def _embedding_generation(self, state: SubmissionWorkflowState) -> SubmissionWorkflowState:
        claim = _claim_from_state(state)
        try:
            prepared = self._prepare_review(claim.submission_id)
            existing = self._load_existing_embedding(claim.submission_id)
            if existing is not None:
                self._validate_existing_embedding(existing, prepared.document_hash)
                run_with_serialization_retry(lambda: self._advance_embedding_cursor(claim))
                state["embedding_ready"] = True
                return state
            output = self._generator.embed(prepared.document_text)
            vector = _validate_vector(output.vector, self._generator.dimension)
            run_with_serialization_retry(
                lambda: self._persist_embedding(
                    claim,
                    prepared,
                    vector,
                    output.input_tokens,
                )
            )
            state["embedding_ready"] = True
            return state
        except ReviewSourceError as exc:
            source_error = exc
            run_with_serialization_retry(
                lambda: self._persist_failure(
                    claim,
                    WorkflowStep.EMBEDDING_GENERATION,
                    "EMBEDDING_SOURCE_INVALID",
                    source_error,
                    retryable=False,
                )
            )
            state["message_action"] = "DELETE"
            return state
        except (ServiceUnavailableError, ValueError, ValidationError) as exc:
            dependency_error = exc
            delay, dead = run_with_serialization_retry(
                lambda: self._persist_failure(
                    claim,
                    WorkflowStep.EMBEDDING_GENERATION,
                    "EMBEDDING_GENERATION_UNAVAILABLE",
                    dependency_error,
                    retryable=True,
                )
            )
            state["message_action"] = "KEEP" if dead else "RETRY"
            state["retry_delay_seconds"] = delay
            return state

    def _needs_review(self, state: SubmissionWorkflowState) -> SubmissionWorkflowState:
        if state.get("message_action") is not None:
            return state
        claim = _claim_from_state(state)
        try:
            prepared = self._prepare_review(claim.submission_id)
            run_with_serialization_retry(lambda: self._persist_receipt(claim, prepared))
            state["message_action"] = "DELETE"
            return state
        except (ReviewSourceError, ValidationError, ValueError) as exc:
            receipt_error = exc
            run_with_serialization_retry(
                lambda: self._persist_failure(
                    claim,
                    WorkflowStep.NEEDS_REVIEW,
                    "REVIEW_RECEIPT_SOURCE_INVALID",
                    receipt_error,
                    retryable=False,
                )
            )
            state["message_action"] = "DELETE"
            return state

    def _prepare_review(self, submission_id: UUID) -> _PreparedReview:
        with self._session_factory() as session:
            submission = self._submissions.get_submission(session, submission_id)
            manifest = (
                self._submissions.get_manifest(session, submission.run_manifest_id)
                if submission is not None
                else None
            )
            intent = session.get(ExperimentIntent, manifest.intent_id) if manifest else None
            plan = session.get(PlanCheck, manifest.plan_check_id) if manifest else None
            approval = (
                session.get(ApprovalRecord, manifest.approval_record_id)
                if manifest is not None and manifest.approval_record_id is not None
                else None
            )
            if (
                submission is None
                or manifest is None
                or intent is None
                or plan is None
                or submission.generated_summary is None
                or manifest.project_id != submission.project_id
                or plan.project_id != submission.project_id
                or intent.project_id != submission.project_id
                or manifest.context_id != plan.context_id
                or manifest.context_version != plan.context_version
                or manifest.intent_id != plan.intent_id
                or manifest.intent_version != plan.intent_version
                or intent.id != manifest.intent_id
                or intent.version != manifest.intent_version
            ):
                raise ReviewSourceError("审核所需的 Submission 追溯链不完整")
            try:
                GeneratedSummary.model_validate(submission.generated_summary)
            except ValidationError as exc:
                raise ReviewSourceError("已持久化摘要契约无效") from exc
            if manifest.approval_record_id is not None and (
                approval is None
                or approval.project_id != submission.project_id
                or approval.target_type is not ApprovalTargetType.PLAN_CHECK
                or approval.target_id != plan.id
                or approval.status is not ApprovalDecision.APPROVED
            ):
                raise ReviewSourceError("Manifest 关联的审批记录无效")
            snapshot = submission.analysis_snapshot
            if not isinstance(snapshot, dict):
                raise ReviewSourceError("审核所需的分析快照不存在")
            parsed_documents = snapshot.get("parsed_documents")
            if not isinstance(parsed_documents, dict):
                raise ReviewSourceError("审核所需的解析结果不存在")
            result_wrapper = parsed_documents.get("result")
            if not isinstance(result_wrapper, dict) or not isinstance(
                result_wrapper.get("parsed"), dict
            ):
                raise ReviewSourceError("审核所需的 result.json 解析结果不存在")
            parsed_result = result_wrapper["parsed"]
            risks = self._submissions.list_risks(session, submission.id)
            receipt = self._build_receipt(
                submission=submission,
                manifest=manifest,
                intent=intent,
                plan=plan,
                parsed_result=parsed_result,
                risks=risks,
                snapshot=snapshot,
                approved=approval is not None,
            )
            document_text = self._build_embedding_document(receipt, risks)
            return _PreparedReview(
                document_text=document_text,
                document_hash=hashlib.sha256(document_text.encode("utf-8")).hexdigest(),
                receipt=receipt,
            )

    def _build_receipt(
        self,
        *,
        submission: ExperimentSubmission,
        manifest: Any,
        intent: ExperimentIntent,
        plan: PlanCheck,
        parsed_result: dict[str, Any],
        risks: list[SubmissionRisk],
        snapshot: dict[str, Any],
        approved: bool,
    ) -> SubmissionReceipt:
        intent_time = _as_utc(intent.confirmed_at or intent.created_at)
        manifest_time = _as_utc(manifest.created_at)
        result_time = _step_time(snapshot, WorkflowStep.CONFIG_PARSE, submission.updated_at)
        objective_evidence = ReviewFact(
            name="objective",
            value=_bounded_value(intent.objective),
            evidence_type=EvidenceType.USER_PROVIDED,
            source="experiment_intents.objective",
            collected_at=intent_time,
            collection_tool="experiment-guardian-intent-confirmation",
        )
        run_conditions = [
            _fact("dataset", manifest.dataset, EvidenceType.CLOUD_VERIFIED, manifest_time),
            _fact("protocol", manifest.protocol, EvidenceType.CLOUD_VERIFIED, manifest_time),
            _fact("seed", manifest.seed, EvidenceType.CLOUD_VERIFIED, manifest_time),
            _fact(
                "config_sha256",
                manifest.config_hash,
                EvidenceType.CLOUD_VERIFIED,
                manifest_time,
            ),
            _fact("checkpoint", manifest.checkpoint, EvidenceType.LOCAL_ATTESTED, manifest_time),
            _fact("git_branch", manifest.git_branch, EvidenceType.LOCAL_ATTESTED, manifest_time),
            _fact("git_commit", manifest.git_commit, EvidenceType.LOCAL_ATTESTED, manifest_time),
            _fact("run_command", manifest.command, EvidenceType.LOCAL_ATTESTED, manifest_time),
            _fact("environment", manifest.environment, EvidenceType.LOCAL_ATTESTED, manifest_time),
        ]
        invariant_validation = snapshot.get("invariant_validation")
        plan_trace = None
        if isinstance(manifest.evidence_snapshot, dict):
            plan_snapshot = manifest.evidence_snapshot.get("experiment_plan")
            if isinstance(plan_snapshot, dict) and isinstance(plan_snapshot.get("trace"), dict):
                plan_trace = plan_snapshot["trace"]
        invariant_status = (
            invariant_validation.get("overall_status")
            if isinstance(invariant_validation, dict)
            else None
        )
        if isinstance(invariant_status, str):
            run_conditions.append(
                _fact(
                    "key_invariant_status",
                    invariant_status,
                    EvidenceType.CLOUD_VERIFIED,
                    result_time,
                )
            )
        allowed_changes = self._allowed_changes(plan, approved)
        metrics = parsed_result.get("metrics")
        if not isinstance(metrics, dict):
            raise ReviewSourceError("result.json 缺少 metrics")
        primary_name = None
        if isinstance(plan.context_snapshot, dict):
            primary_metric = plan.context_snapshot.get("primary_metric")
            if isinstance(primary_metric, dict) and isinstance(primary_metric.get("name"), str):
                primary_name = primary_metric["name"]
        metric_names = sorted(metrics)
        if primary_name in metric_names:
            metric_names.remove(primary_name)
            metric_names.insert(0, primary_name)
        key_results = [
            ReviewFact(
                name="experiment_status",
                value=parsed_result.get("status"),
                evidence_type=EvidenceType.CLOUD_VERIFIED,
                source="versioned RESULT artifact parsed by cloud rules",
                collected_at=result_time,
                collection_tool="experiment-guardian-r11-parser",
            )
        ]
        key_results.extend(
            ReviewFact(
                name=f"metric.{name}",
                value=metrics[name],
                evidence_type=EvidenceType.CLOUD_VERIFIED,
                source="versioned RESULT artifact parsed by cloud rules",
                collected_at=result_time,
                collection_tool="experiment-guardian-r11-parser",
            )
            for name in metric_names
        )
        risk_items = [risk_item_from_model(item) for item in risks]
        unresolved = [item for item in risks if not item.resolved]
        severities = [item.severity for item in unresolved]
        highest = max(severities, key=RISK_RANK.__getitem__) if severities else None
        eligibility = review_eligibility_for_risks(risks)
        highlighted = [
            item
            for item in risk_items
            if item.severity in {RiskSeverity.HIGH, RiskSeverity.CRITICAL}
        ]
        evidence_counts = {item: 0 for item in EvidenceType}
        evidence_counts[objective_evidence.evidence_type] += 1
        for fact in [*run_conditions, *key_results]:
            evidence_counts[fact.evidence_type] += 1
        for change in allowed_changes:
            if change.evidence_type is not None:
                evidence_counts[change.evidence_type] += 1
        for risk in risk_items:
            if risk.evidence_type is not None:
                evidence_counts[risk.evidence_type] += 1

        source_payload = {
            "submission_id": str(submission.id),
            "objective": objective_evidence.model_dump(mode="json"),
            "trace": {
                "project_id": str(submission.project_id),
                "context_id": str(manifest.context_id),
                "context_version": manifest.context_version,
                "intent_id": str(manifest.intent_id),
                "intent_version": manifest.intent_version,
                "plan_check_id": str(manifest.plan_check_id),
                "run_manifest_id": str(manifest.id),
                "manifest_hash": manifest.manifest_hash,
                "experiment_plan_decision_id": (
                    plan_trace.get("decision_id") if plan_trace else None
                ),
                "experiment_plan_revision_id": (
                    plan_trace.get("revision_id") if plan_trace else None
                ),
                "invariant_status": invariant_status,
            },
            "run_conditions": [item.model_dump(mode="json") for item in run_conditions],
            "allowed_changes": [item.model_dump(mode="json") for item in allowed_changes],
            "key_results": [item.model_dump(mode="json") for item in key_results],
            "risks": [item.model_dump(mode="json") for item in risk_items],
            "review_eligibility": eligibility.value,
        }
        receipt = SubmissionReceipt(
            submission_id=submission.id,
            objective=_bounded_text(intent.objective),
            objective_evidence=objective_evidence,
            trace=ReviewTrace(
                project_id=submission.project_id,
                context_id=manifest.context_id,
                context_version=manifest.context_version,
                intent_id=manifest.intent_id,
                intent_version=manifest.intent_version,
                plan_check_id=manifest.plan_check_id,
                run_manifest_id=manifest.id,
                manifest_hash=manifest.manifest_hash,
                experiment_plan_decision_id=(
                    UUID(plan_trace["decision_id"])
                    if plan_trace and isinstance(plan_trace.get("decision_id"), str)
                    else None
                ),
                experiment_plan_revision_id=(
                    UUID(plan_trace["revision_id"])
                    if plan_trace and isinstance(plan_trace.get("revision_id"), str)
                    else None
                ),
                invariant_status=invariant_status,
            ),
            run_conditions=run_conditions,
            allowed_changes=allowed_changes,
            key_results=key_results,
            highest_risk=highest,
            highlighted_risks=highlighted,
            collapsed_low_risk_count=sum(item.severity is RiskSeverity.LOW for item in risks),
            collapsed_medium_risk_count=sum(item.severity is RiskSeverity.MEDIUM for item in risks),
            evidence_counts=evidence_counts,
            review_eligibility=eligibility,
            can_confirm=eligibility is not ReviewEligibility.BLOCKED,
            requires_owner=eligibility is ReviewEligibility.OWNER_ONLY,
            summary_available=True,
            source_hash=canonical_json_hash(source_payload),
            generated_at=datetime.now(UTC),
            disclaimer=REVIEW_DISCLAIMER,
        )
        encoded = json.dumps(
            receipt.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > MAX_REVIEW_RECEIPT_BYTES:
            raise ReviewSourceError("审核回执超过 64 KiB 上限")
        return receipt

    @staticmethod
    def _allowed_changes(plan: PlanCheck, approved: bool) -> list[ParameterChange]:
        changes: list[ParameterChange] = []
        for raw in plan.planned_changes:
            try:
                item = ParameterChange.model_validate(raw)
            except ValidationError as exc:
                raise ReviewSourceError("Plan Check 变化快照无效") from exc
            if item.protection_level is ProtectionLevel.LOCKED:
                raise ReviewSourceError("Manifest 关联的 Plan Check 包含 LOCKED 变化")
            if item.protection_level is ProtectionLevel.APPROVAL_REQUIRED:
                if not approved:
                    raise ReviewSourceError("APPROVAL_REQUIRED 变化缺少审批记录")
                decision = "OWNER_APPROVED"
                impact = "该变化由 Owner 审批后进入 Run Manifest。"
            else:
                decision = "ALLOWED"
                impact = "该变化属于实验意图允许的变量。"
            changes.append(
                item.model_copy(
                    update={
                        "previous_value": _bounded_value(item.previous_value),
                        "current_value": _bounded_value(item.current_value),
                        "decision": decision,
                        "impact": impact,
                        "evidence_type": EvidenceType.CLOUD_VERIFIED,
                        "evidence_source": "plan_checks.planned_changes + run_manifests",
                    }
                )
            )
        return sorted(changes, key=lambda item: item.parameter_path)

    @staticmethod
    def _build_embedding_document(receipt: SubmissionReceipt, risks: list[SubmissionRisk]) -> str:
        highlighted_fingerprints = {
            (item.code, item.field_path, item.message) for item in receipt.highlighted_risks
        }
        lower_risks = [
            risk_item_from_model(item).model_dump(mode="json")
            for item in sorted(
                risks,
                key=lambda item: (
                    -RISK_RANK[item.severity],
                    item.risk_type,
                    item.risk_fingerprint,
                ),
            )
            if (item.risk_type, item.field_path, _bounded_text(item.message))
            not in highlighted_fingerprints
        ]
        mandatory_results = receipt.key_results[:2]
        optional_results = receipt.key_results[2:]
        document: dict[str, Any] = {
            "document_version": EMBEDDING_DOCUMENT_VERSION,
            "trace": receipt.trace.model_dump(mode="json"),
            "objective": receipt.objective_evidence.model_dump(mode="json"),
            "run_conditions": [item.model_dump(mode="json") for item in receipt.run_conditions],
            "allowed_changes": [item.model_dump(mode="json") for item in receipt.allowed_changes],
            "key_results": [item.model_dump(mode="json") for item in mandatory_results],
            "high_critical_risks": [
                item.model_dump(mode="json") for item in receipt.highlighted_risks
            ],
            "lower_risks": [],
            "omitted_metric_count": len(optional_results),
            "omitted_lower_risk_count": len(lower_risks),
        }
        if len(_canonical_text(document)) > MAX_EMBEDDING_INPUT_CHARACTERS:
            raise ReviewSourceError("检索文档必要事实超过 16000 字符上限")
        for result in optional_results:
            document["key_results"].append(result.model_dump(mode="json"))
            document["omitted_metric_count"] -= 1
            if len(_canonical_text(document)) > MAX_EMBEDDING_INPUT_CHARACTERS:
                document["key_results"].pop()
                document["omitted_metric_count"] += 1
                break
        for risk in lower_risks:
            document["lower_risks"].append(risk)
            document["omitted_lower_risk_count"] -= 1
            if len(_canonical_text(document)) > MAX_EMBEDDING_INPUT_CHARACTERS:
                document["lower_risks"].pop()
                document["omitted_lower_risk_count"] += 1
                break
        text = _canonical_text(document)
        if len(text) > MAX_EMBEDDING_INPUT_CHARACTERS:
            raise ReviewSourceError("检索文档超过 16000 字符上限")
        return text

    def _load_existing_embedding(self, submission_id: UUID) -> SubmissionEmbedding | None:
        with self._session_factory() as session:
            return self._submissions.get_embedding(session, submission_id)

    def _validate_existing_embedding(self, embedding: SubmissionEmbedding, input_hash: str) -> None:
        if (
            embedding.input_sha256 != input_hash
            or embedding.provider != getattr(self._generator, "provider", "bedrock")
            or embedding.model_id != self._generator.model_id
            or embedding.dimension != EMBEDDING_DIMENSION
            or not embedding.normalized
            or embedding.document_version != EMBEDDING_DOCUMENT_VERSION
        ):
            raise ReviewSourceError("已持久化 embedding 与当前不可变来源不一致")

    def _persist_embedding(
        self,
        claim: _ReviewClaim,
        prepared: _PreparedReview,
        vector: list[float],
        input_tokens: int | None,
    ) -> None:
        with self._session_factory() as session, session.begin():
            job = self._workflows.get_job(session, claim.job_id, for_update=True)
            submission = self._submissions.get_submission_for_update(session, claim.submission_id)
            if job is None or submission is None or not _owns_claim(job, claim):
                return
            existing = self._submissions.get_embedding(session, submission.id, for_update=True)
            if existing is None:
                session.add(
                    SubmissionEmbedding(
                        submission_id=submission.id,
                        project_id=submission.project_id,
                        embedding=vector,
                        provider=getattr(self._generator, "provider", "bedrock"),
                        model_id=self._generator.model_id,
                        dimension=EMBEDDING_DIMENSION,
                        normalized=True,
                        document_version=EMBEDDING_DOCUMENT_VERSION,
                        input_text=prepared.document_text,
                        input_sha256=prepared.document_hash,
                        input_token_count=input_tokens,
                        generated_at=datetime.now(UTC),
                    )
                )
            else:
                self._validate_existing_embedding(existing, prepared.document_hash)
            submission.processing_step = WorkflowStep.EMBEDDING_GENERATION
            submission.workflow_status = WorkflowStatus.RUNNING
            submission.processing_error = None

    def _advance_embedding_cursor(self, claim: _ReviewClaim) -> None:
        with self._session_factory() as session, session.begin():
            job = self._workflows.get_job(session, claim.job_id, for_update=True)
            submission = self._submissions.get_submission_for_update(session, claim.submission_id)
            if job is None or submission is None or not _owns_claim(job, claim):
                return
            submission.processing_step = WorkflowStep.EMBEDDING_GENERATION
            submission.workflow_status = WorkflowStatus.RUNNING
            submission.processing_error = None

    def _persist_receipt(self, claim: _ReviewClaim, prepared: _PreparedReview) -> None:
        with self._session_factory() as session, session.begin():
            job = self._workflows.get_job(session, claim.job_id, for_update=True)
            submission = self._submissions.get_submission_for_update(session, claim.submission_id)
            if job is None or submission is None or not _owns_claim(job, claim):
                return
            embedding = self._submissions.get_embedding(session, submission.id, for_update=True)
            if embedding is None or embedding.input_sha256 != prepared.document_hash:
                raise ReviewSourceError("审核回执与持久化 embedding 来源不一致")
            if submission.review_receipt is None:
                submission.review_receipt = prepared.receipt.model_dump(mode="json")
            now = datetime.now(UTC)
            submission.status = SubmissionStatus.NEEDS_REVIEW
            submission.workflow_status = WorkflowStatus.COMPLETED
            submission.processing_step = WorkflowStep.NEEDS_REVIEW
            submission.processing_error = None
            job.status = WorkflowJobStatus.SUCCEEDED
            job.completed_at = now
            job.lease_owner = None
            job.lease_expires_at = None
            job.last_error = None

    def _persist_failure(
        self,
        claim: _ReviewClaim,
        step: WorkflowStep,
        code: str,
        error: Exception,
        *,
        retryable: bool,
    ) -> tuple[int, bool]:
        with self._session_factory() as session, session.begin():
            job = self._workflows.get_job(session, claim.job_id, for_update=True)
            submission = self._submissions.get_submission_for_update(session, claim.submission_id)
            if job is None or submission is None or not _owns_claim(job, claim):
                return self._lease_seconds, False
            now = datetime.now(UTC)
            payload = {
                "code": code,
                "message": str(error),
                "retryable": retryable,
                "failed_step": step.value,
            }
            job.last_error = payload
            job.lease_owner = None
            job.lease_expires_at = None
            if not retryable:
                job.status = WorkflowJobStatus.FAILED
                job.completed_at = now
                submission.status = SubmissionStatus.FAILED
                submission.workflow_status = WorkflowStatus.TERMINAL_FAILURE
                submission.processing_error = payload
                return 0, False
            dead = job.attempt_count >= job.max_attempts
            delay = _retry_delay(job.attempt_count)
            job.status = (
                WorkflowJobStatus.DEAD_LETTER if dead else WorkflowJobStatus.RETRYABLE_FAILURE
            )
            job.available_at = now + timedelta(seconds=delay)
            job.completed_at = now if dead else None
            submission.status = SubmissionStatus.PROCESSING
            submission.workflow_status = WorkflowStatus.RETRYABLE_FAILURE
            submission.processing_error = payload
            return delay, dead


def _claim_from_state(state: SubmissionWorkflowState) -> _ReviewClaim:
    return _ReviewClaim(
        job_id=UUID(state["job_id"]),
        submission_id=UUID(state["submission_id"]),
        generation=int(state["generation"]),
        lease_owner=state["lease_owner"],
    )


def _owns_claim(job: WorkflowJob | None, claim: _ReviewClaim) -> bool:
    return bool(
        job is not None
        and job.generation == claim.generation
        and job.job_type is WorkflowJobType.SUBMISSION_REVIEW_PREPARATION
        and job.status is WorkflowJobStatus.RUNNING
        and job.lease_owner == claim.lease_owner
    )


def _validate_vector(vector: object, dimension: int) -> list[float]:
    if dimension != EMBEDDING_DIMENSION or not isinstance(vector, list):
        raise ValueError("embedding 维度契约无效")
    normalized: list[float] = []
    for item in vector:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError("embedding 包含非数值元素")
        number = float(item)
        if not math.isfinite(number):
            raise ValueError("embedding 包含非有限数值")
        normalized.append(number)
    if len(normalized) != dimension:
        raise ValueError("embedding 返回维度错误")
    norm = math.sqrt(sum(item * item for item in normalized))
    if not math.isclose(norm, 1.0, rel_tol=1e-3, abs_tol=1e-3):
        raise ValueError("embedding 未归一化")
    return normalized


def _fact(name: str, value: Any, evidence_type: EvidenceType, collected_at: datetime) -> ReviewFact:
    return ReviewFact(
        name=name,
        value=_bounded_value(value),
        evidence_type=evidence_type,
        source=(
            "run_manifests + versioned CONFIG verification"
            if evidence_type is EvidenceType.CLOUD_VERIFIED
            else "run_manifest local attestation snapshot"
        ),
        collected_at=collected_at,
        collection_tool=(
            "experiment-guardian-r11-rules"
            if evidence_type is EvidenceType.CLOUD_VERIFIED
            else "local-agent"
        ),
    )


def risk_item_from_model(item: SubmissionRisk) -> RiskItem:
    return RiskItem(
        code=item.risk_type,
        severity=item.severity,
        message=_bounded_text(item.message),
        field_path=item.field_path,
        previous_value=_bounded_value(item.previous_value),
        current_value=_bounded_value(item.current_value),
        expected_value=_bounded_value(item.expected_value),
        impact=_bounded_text(item.impact) if item.impact else None,
        blocking=item.blocking,
        resolved=item.resolved,
        evidence_type=item.evidence_type,
        evidence_source=item.evidence_source,
        collected_at=_as_utc(item.collected_at) if item.collected_at else None,
        collection_tool=item.collection_tool,
        constraint_source=item.constraint_source,
        constraint_status=item.constraint_status,
        inference_basis=_bounded_text(item.inference_basis) if item.inference_basis else None,
        confidence=item.confidence,
        constraint_candidates=item.constraint_candidates,
        recommendation=_bounded_text(item.recommendation) if item.recommendation else None,
    )


def review_eligibility_for_risks(risks: list[SubmissionRisk]) -> ReviewEligibility:
    """只依据未解决风险计算确认门禁，不能被已存回执覆盖。"""

    unresolved = [item for item in risks if not item.resolved]
    if any(item.blocking or item.severity is RiskSeverity.CRITICAL for item in unresolved):
        return ReviewEligibility.BLOCKED
    if any(item.severity is RiskSeverity.HIGH for item in unresolved):
        return ReviewEligibility.OWNER_ONLY
    return ReviewEligibility.RESEARCHER_OR_OWNER


def review_receipt_source_hash(
    receipt: SubmissionReceipt, risks: list[SubmissionRisk]
) -> str:
    """按 R12b 原始字段重建回执来源哈希，发现持久化内容漂移。"""

    return canonical_json_hash(
        {
            "submission_id": str(receipt.submission_id),
            "objective": receipt.objective_evidence.model_dump(mode="json"),
            "trace": receipt.trace.model_dump(mode="json"),
            "run_conditions": [item.model_dump(mode="json") for item in receipt.run_conditions],
            "allowed_changes": [
                item.model_dump(mode="json") for item in receipt.allowed_changes
            ],
            "key_results": [item.model_dump(mode="json") for item in receipt.key_results],
            "risks": [risk_item_from_model(item).model_dump(mode="json") for item in risks],
            "review_eligibility": review_eligibility_for_risks(risks).value,
        }
    )


def build_embedding_document(
    receipt: SubmissionReceipt, risks: list[SubmissionRisk]
) -> str:
    """公开确定性文档构造器，供正式确认复算草稿 embedding 来源。"""

    return SubmissionReviewProcessor._build_embedding_document(receipt, risks)


def _bounded_value(value: Any) -> Any:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ReviewSourceError("审核事实包含不可序列化值") from exc
    if len(encoded) <= MAX_VALUE_PREVIEW_CHARACTERS:
        return value
    return {
        "preview": encoded[:MAX_VALUE_PREVIEW_CHARACTERS],
        "truncated": True,
        "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


def _bounded_text(value: str) -> str:
    if len(value) <= MAX_VALUE_PREVIEW_CHARACTERS:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{value[:MAX_VALUE_PREVIEW_CHARACTERS]} [truncated sha256={digest}]"


def _canonical_text(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _step_time(snapshot: dict[str, Any], step: WorkflowStep, fallback: datetime) -> datetime:
    steps = snapshot.get("steps")
    raw = steps.get(step.value) if isinstance(steps, dict) else None
    value = raw.get("completed_at") if isinstance(raw, dict) else None
    if isinstance(value, str):
        try:
            return _as_utc(datetime.fromisoformat(value))
        except ValueError:
            pass
    return _as_utc(fallback)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _retry_delay(attempt_count: int) -> int:
    return int(min(3600, 30 * (4 ** max(0, attempt_count - 1))))
