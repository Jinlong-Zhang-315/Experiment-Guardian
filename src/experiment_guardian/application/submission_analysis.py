"""R11 Submission 分析前缀：以业务表游标驱动五个可恢复步骤。"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.errors import InputValidationError, ServiceUnavailableError
from experiment_guardian.application.ports import ArtifactStorage
from experiment_guardian.application.transactions import run_with_serialization_retry
from experiment_guardian.domain.contracts import SubmissionAnalysisReceipt
from experiment_guardian.domain.enums import (
    ArtifactType,
    EvidenceType,
    RiskSeverity,
    SubmissionStatus,
    SubmittedRunStatus,
    WorkflowStatus,
    WorkflowStep,
)
from experiment_guardian.domain.plan_check import canonical_config_hash
from experiment_guardian.domain.run_manifest import canonical_json_hash
from experiment_guardian.domain.submission_analysis import (
    SubmissionDocumentError,
    parse_submitted_configuration,
    parse_submitted_result,
)
from experiment_guardian.infrastructure.models import (
    ExperimentSubmission,
    ProjectContext,
    RunManifest,
    SubmissionRisk,
)
from experiment_guardian.infrastructure.repositories import SqlAlchemySubmissionRepository
from experiment_guardian.workflows.submission import (
    R11_WORKFLOW_ORDER,
    SubmissionWorkflowState,
    build_submission_workflow,
)

MAX_ANALYZED_DOCUMENT_BYTES = 1024 * 1024
MAX_ANALYSIS_SNAPSHOT_BYTES = 3 * 1024 * 1024
RISK_RANK = {
    RiskSeverity.LOW: 0,
    RiskSeverity.MEDIUM: 1,
    RiskSeverity.HIGH: 2,
    RiskSeverity.CRITICAL: 3,
}
STEP_INDEX = {step: index for index, step in enumerate(R11_WORKFLOW_ORDER)}


@dataclass(frozen=True, slots=True)
class _ArtifactVersion:
    id: UUID
    filename: str
    artifact_type: ArtifactType
    object_key: str
    sha256: str
    version_id: str


class SubmissionAnalysisService:
    """同步运行 R11 前缀；每个节点独立提交，重启后从数据库游标恢复。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        repository: SqlAlchemySubmissionRepository,
        storage: ArtifactStorage,
    ) -> None:
        self._session_factory = session_factory
        self._repository = repository
        self._storage = storage
        handlers = {
            WorkflowStep.UPLOAD_VERIFICATION: self._upload_verification,
            WorkflowStep.CONFIG_PARSE: self._config_parse,
            WorkflowStep.MANIFEST_VALIDATION: self._manifest_validation,
            WorkflowStep.DUPLICATE_CHECK: self._duplicate_check,
            WorkflowStep.RISK_ANALYSIS: self._risk_analysis,
        }
        self._workflow = build_submission_workflow(handlers, steps=R11_WORKFLOW_ORDER)

    def run(self, submission_id: UUID) -> SubmissionAnalysisReceipt:
        """启动或恢复分析；LangGraph 只编排，持久化进度始终来自 Submission。"""

        should_run = run_with_serialization_retry(lambda: self._prepare_run(submission_id))
        if should_run:
            self._workflow.invoke({"submission_id": str(submission_id)})
        return self.get_receipt(submission_id)

    def get_receipt(self, submission_id: UUID) -> SubmissionAnalysisReceipt:
        with self._session_factory() as session:
            submission = self._repository.get_submission(session, submission_id)
            if submission is None:
                raise RuntimeError("分析回执对应的 Submission 不存在")
            risks = self._repository.list_risks(session, submission_id)
            severities = [item.severity for item in risks]
            highest = max(severities, key=RISK_RANK.__getitem__) if severities else None
            snapshot = submission.analysis_snapshot or {}
            duplicates = snapshot.get("duplicates", {}).get("candidates", [])
            return SubmissionAnalysisReceipt(
                submission_status=submission.status,
                workflow_status=submission.workflow_status,
                processing_step=submission.processing_step,
                retryable=submission.workflow_status is WorkflowStatus.RETRYABLE_FAILURE,
                error=submission.processing_error,
                duplicate_count=len(duplicates) if isinstance(duplicates, list) else 0,
                risk_count=len(risks),
                highest_risk=highest,
            )

    def _prepare_run(self, submission_id: UUID) -> bool:
        with self._session_factory() as session, session.begin():
            submission = self._repository.get_submission_for_update(session, submission_id)
            if submission is None:
                raise RuntimeError("待分析的 Submission 不存在")
            if submission.workflow_status in {
                WorkflowStatus.TERMINAL_FAILURE,
                WorkflowStatus.AWAITING_ENRICHMENT,
            }:
                return False
            if submission.status not in {
                SubmissionStatus.UPLOAD_VERIFIED,
                SubmissionStatus.PROCESSING,
            }:
                return False
            submission.status = SubmissionStatus.PROCESSING
            submission.workflow_status = WorkflowStatus.RUNNING
            submission.processing_error = None
            return True

    def _upload_verification(self, state: SubmissionWorkflowState) -> SubmissionWorkflowState:
        submission_id = UUID(state["submission_id"])

        def persist() -> None:
            with self._session_factory() as session, session.begin():
                submission = self._repository.get_submission_for_update(session, submission_id)
                if not self._can_execute(submission, WorkflowStep.UPLOAD_VERIFICATION):
                    return
                assert submission is not None
                artifacts = self._repository.list_artifacts_for_update(session, submission_id)
                counts = {
                    artifact_type: sum(item.artifact_type is artifact_type for item in artifacts)
                    for artifact_type in (ArtifactType.CONFIG, ArtifactType.RESULT)
                }
                evidence_valid = (
                    submission.upload_verified_at is not None
                    and counts[ArtifactType.CONFIG] == 1
                    and counts[ArtifactType.RESULT] == 1
                    and all(
                        item.cloud_hash_verified
                        and item.verified_at is not None
                        and isinstance(item.s3_version_id, str)
                        and bool(item.s3_version_id.strip())
                        and isinstance(item.verification_evidence, dict)
                        for item in artifacts
                    )
                )
                if not evidence_valid:
                    self._terminal_in_session(
                        submission,
                        WorkflowStep.UPLOAD_VERIFICATION,
                        "UPLOAD_EVIDENCE_INVALID",
                        "上传验证证据不完整或已损坏，不能开始内容分析",
                    )
                    return
                assert submission.upload_verified_at is not None
                payload = {
                    "verified_at": submission.upload_verified_at.isoformat(),
                    "artifacts": [
                        {
                            "artifact_id": str(item.id),
                            "artifact_type": item.artifact_type.value,
                            "sha256": item.sha256,
                            "version_id": item.s3_version_id,
                        }
                        for item in artifacts
                    ],
                }
                self._complete_step(
                    submission,
                    WorkflowStep.UPLOAD_VERIFICATION,
                    "upload_verification",
                    payload,
                )

        run_with_serialization_retry(persist)
        return state

    def _config_parse(self, state: SubmissionWorkflowState) -> SubmissionWorkflowState:
        submission_id = UUID(state["submission_id"])
        targets = self._load_parse_targets(submission_id)
        if targets is None:
            return state
        by_type = {item.artifact_type: item for item in targets}
        try:
            config_bytes = self._read_verified_version(by_type[ArtifactType.CONFIG])
            result_bytes = self._read_verified_version(by_type[ArtifactType.RESULT])
            parsed_config = parse_submitted_configuration(
                filename=by_type[ArtifactType.CONFIG].filename,
                payload=config_bytes,
            )
            parsed_result = parse_submitted_result(result_bytes)
        except ServiceUnavailableError as exc:
            self._record_failure(
                submission_id,
                WorkflowStep.CONFIG_PARSE,
                "S3_READ_UNAVAILABLE",
                str(exc),
                retryable=True,
            )
            return state
        except (InputValidationError, SubmissionDocumentError) as exc:
            self._record_failure(
                submission_id,
                WorkflowStep.CONFIG_PARSE,
                "SUBMITTED_DOCUMENT_INVALID",
                str(exc),
                retryable=False,
            )
            return state

        payload = {
            "config": {
                "artifact_id": str(by_type[ArtifactType.CONFIG].id),
                "filename": by_type[ArtifactType.CONFIG].filename,
                "version_id": by_type[ArtifactType.CONFIG].version_id,
                "document_sha256": hashlib.sha256(config_bytes).hexdigest(),
                "canonical_sha256": canonical_config_hash(parsed_config),
                "parsed": parsed_config,
                "evidence_type": EvidenceType.LOCAL_ATTESTED.value,
            },
            "result": {
                "artifact_id": str(by_type[ArtifactType.RESULT].id),
                "filename": by_type[ArtifactType.RESULT].filename,
                "version_id": by_type[ArtifactType.RESULT].version_id,
                "document_sha256": hashlib.sha256(result_bytes).hexdigest(),
                "parsed": parsed_result.model_dump(mode="json"),
                "evidence_type": EvidenceType.LOCAL_ATTESTED.value,
            },
        }

        def persist() -> None:
            with self._session_factory() as session, session.begin():
                submission = self._repository.get_submission_for_update(session, submission_id)
                if not self._can_execute(submission, WorkflowStep.CONFIG_PARSE):
                    return
                assert submission is not None
                self._complete_step(
                    submission,
                    WorkflowStep.CONFIG_PARSE,
                    "parsed_documents",
                    payload,
                )

        run_with_serialization_retry(persist)
        return state

    def _manifest_validation(self, state: SubmissionWorkflowState) -> SubmissionWorkflowState:
        submission_id = UUID(state["submission_id"])

        def persist() -> None:
            with self._session_factory() as session, session.begin():
                submission = self._repository.get_submission_for_update(session, submission_id)
                if not self._can_execute(submission, WorkflowStep.MANIFEST_VALIDATION):
                    return
                assert submission is not None
                snapshot = self._snapshot(submission)
                documents = snapshot.get("parsed_documents")
                manifest = self._repository.get_manifest(session, submission.run_manifest_id)
                context = session.get(ProjectContext, manifest.context_id) if manifest else None
                if (
                    not isinstance(documents, dict)
                    or manifest is None
                    or context is None
                    or manifest.project_id != submission.project_id
                    or manifest.context_version != context.version
                ):
                    self._terminal_in_session(
                        submission,
                        WorkflowStep.MANIFEST_VALIDATION,
                        "TRACEABILITY_CHAIN_INVALID",
                        "Submission、Manifest 或 Context 的追溯链不完整",
                    )
                    return
                config = documents.get("config")
                result = documents.get("result")
                if not isinstance(config, dict) or not isinstance(result, dict):
                    self._terminal_in_session(
                        submission,
                        WorkflowStep.MANIFEST_VALIDATION,
                        "ANALYSIS_SNAPSHOT_INVALID",
                        "已持久化的解析结果结构不完整",
                    )
                    return

                findings: list[dict[str, Any]] = []
                self._compare(
                    findings,
                    code="MANIFEST_HASH_MISMATCH",
                    field_path="submission.manifest_hash",
                    actual=submission.manifest_hash,
                    expected=manifest.manifest_hash,
                    message="Submission 声明的 Manifest 哈希与正式记录不一致",
                )
                self._compare(
                    findings,
                    code="CONFIG_DOCUMENT_HASH_MISMATCH",
                    field_path="config.document_sha256",
                    actual=config.get("document_sha256"),
                    expected=manifest.config_document_hash,
                    message="上传配置原始文件哈希与 Run Manifest 不一致",
                )
                self._compare(
                    findings,
                    code="CONFIG_CANONICAL_HASH_MISMATCH",
                    field_path="config.canonical_sha256",
                    actual=config.get("canonical_sha256"),
                    expected=manifest.config_hash,
                    message="上传配置的结构化内容与 Run Manifest 不一致",
                )
                manifest_parsed = (
                    manifest.config_snapshot.get("parsed")
                    if isinstance(manifest.config_snapshot, dict)
                    else None
                )
                self._compare(
                    findings,
                    code="CONFIG_SNAPSHOT_MISMATCH",
                    field_path="config.parsed",
                    actual=config.get("parsed"),
                    expected=manifest_parsed,
                    message="上传配置与 Manifest 保存的配置快照不一致",
                )
                parsed_result = result.get("parsed")
                if not isinstance(parsed_result, dict):
                    self._terminal_in_session(
                        submission,
                        WorkflowStep.MANIFEST_VALIDATION,
                        "ANALYSIS_SNAPSHOT_INVALID",
                        "已持久化的 result.json 解析结果不完整",
                    )
                    return
                self._compare(
                    findings,
                    code="RESULT_STATUS_MISMATCH",
                    field_path="result.status",
                    actual=parsed_result.get("status"),
                    expected=submission.declared_experiment_status.value,
                    message="result.json 状态与 submission_prepare 声明不一致",
                )
                self._compare(
                    findings,
                    code="RESULT_METRICS_MISMATCH",
                    field_path="result.metrics",
                    actual=parsed_result.get("metrics"),
                    expected=submission.declared_metrics,
                    message="result.json 指标与 submission_prepare 声明不一致",
                )
                primary_metric = context.primary_metric.get("name")
                if (
                    submission.declared_experiment_status is SubmittedRunStatus.COMPLETED
                    and isinstance(primary_metric, str)
                    and primary_metric not in parsed_result.get("metrics", {})
                ):
                    findings.append(
                        self._finding(
                            code="PRIMARY_METRIC_MISSING",
                            field_path=f"result.metrics.{primary_metric}",
                            current=None,
                            expected="PRESENT",
                            message="完成的实验结果缺少项目主指标",
                        )
                    )
                payload = {
                    "passed": not findings,
                    "checks": [
                        "traceability",
                        "manifest_hash",
                        "config_document_hash",
                        "config_canonical_hash",
                        "config_snapshot",
                        "result_status",
                        "result_metrics",
                        "primary_metric",
                    ],
                    "findings": findings,
                }
                self._complete_step(
                    submission,
                    WorkflowStep.MANIFEST_VALIDATION,
                    "manifest_validation",
                    payload,
                )

        run_with_serialization_retry(persist)
        return state

    def _duplicate_check(self, state: SubmissionWorkflowState) -> SubmissionWorkflowState:
        submission_id = UUID(state["submission_id"])

        def persist() -> None:
            with self._session_factory() as session, session.begin():
                submission = self._repository.get_submission_for_update(session, submission_id)
                if not self._can_execute(submission, WorkflowStep.DUPLICATE_CHECK):
                    return
                assert submission is not None
                current_manifest = self._repository.get_manifest(
                    session, submission.run_manifest_id
                )
                current_hashes = self._artifact_hashes(session, submission.id)
                if current_manifest is None or current_hashes is None:
                    self._terminal_in_session(
                        submission,
                        WorkflowStep.DUPLICATE_CHECK,
                        "TRACEABILITY_CHAIN_INVALID",
                        "当前 Submission 缺少可用于查重的 Manifest 或 Artifact 证据",
                    )
                    return
                candidates: list[dict[str, Any]] = []
                for candidate in self._repository.list_analysis_candidates(
                    session,
                    project_id=submission.project_id,
                    exclude_submission_id=submission.id,
                ):
                    hashes = self._artifact_hashes(session, candidate.id)
                    candidate_manifest = self._repository.get_manifest(
                        session, candidate.run_manifest_id
                    )
                    if hashes is None or candidate_manifest is None:
                        continue
                    same_manifest = candidate.manifest_hash == submission.manifest_hash
                    exact = same_manifest and hashes == current_hashes
                    same_conditions = same_manifest or self._same_run_conditions(
                        current_manifest, candidate_manifest
                    )
                    if exact:
                        duplicate_type = "EXACT_DUPLICATE_SUBMISSION"
                        severity = RiskSeverity.MEDIUM
                    elif same_conditions:
                        duplicate_type = "SAME_RUN_CONDITIONS"
                        severity = RiskSeverity.LOW
                    else:
                        continue
                    candidates.append(
                        {
                            "submission_id": str(candidate.id),
                            "duplicate_type": duplicate_type,
                            "severity": severity.value,
                            "blocking": False,
                            "manifest_hash": candidate.manifest_hash,
                            "artifact_hashes": hashes,
                        }
                    )
                self._complete_step(
                    submission,
                    WorkflowStep.DUPLICATE_CHECK,
                    "duplicates",
                    {"candidates": candidates},
                )

        run_with_serialization_retry(persist)
        return state

    def _risk_analysis(self, state: SubmissionWorkflowState) -> SubmissionWorkflowState:
        submission_id = UUID(state["submission_id"])

        def persist() -> None:
            with self._session_factory() as session, session.begin():
                submission = self._repository.get_submission_for_update(session, submission_id)
                if not self._can_execute(submission, WorkflowStep.RISK_ANALYSIS):
                    return
                assert submission is not None
                snapshot = self._snapshot(submission)
                validation = snapshot.get("manifest_validation", {})
                duplicates = snapshot.get("duplicates", {})
                findings = validation.get("findings", []) if isinstance(validation, dict) else []
                candidates = (
                    duplicates.get("candidates", []) if isinstance(duplicates, dict) else []
                )
                if not isinstance(findings, list) or not isinstance(candidates, list):
                    self._terminal_in_session(
                        submission,
                        WorkflowStep.RISK_ANALYSIS,
                        "ANALYSIS_SNAPSHOT_INVALID",
                        "风险分析所需的上游结果不完整",
                    )
                    return

                existing = {
                    item.risk_fingerprint
                    for item in self._repository.list_risks(session, submission.id)
                }
                for raw in [*findings, *candidates]:
                    if not isinstance(raw, dict):
                        continue
                    is_duplicate = "duplicate_type" in raw
                    risk_type = str(raw.get("duplicate_type") or raw.get("code"))
                    severity = RiskSeverity(raw.get("severity", RiskSeverity.CRITICAL.value))
                    fingerprint = canonical_json_hash(
                        {
                            "risk_type": risk_type,
                            "field_path": raw.get("field_path"),
                            "candidate_submission_id": raw.get("submission_id"),
                            "current": raw.get("current"),
                            "expected": raw.get("expected"),
                        }
                    )
                    if fingerprint in existing:
                        continue
                    session.add(
                        SubmissionRisk(
                            submission_id=submission.id,
                            risk_fingerprint=fingerprint,
                            risk_type=risk_type,
                            severity=severity,
                            field_path=raw.get("field_path"),
                            previous_value=None,
                            current_value=(
                                {"submission_id": raw.get("submission_id")}
                                if is_duplicate
                                else raw.get("current")
                            ),
                            expected_value=raw.get("expected"),
                            rule_id=f"R11.{risk_type}",
                            message=(
                                "发现相同上传内容和 Manifest 的历史 Submission"
                                if risk_type == "EXACT_DUPLICATE_SUBMISSION"
                                else "发现运行条件相同的历史 Submission"
                                if risk_type == "SAME_RUN_CONDITIONS"
                                else str(raw.get("message"))
                            ),
                            impact=(
                                "该记录仅作为候选证据，不自动替代正式实验查询"
                                if is_duplicate
                                else "当前提交与正式 Manifest 或提交声明不一致"
                            ),
                            evidence_type=(
                                EvidenceType.CLOUD_VERIFIED
                                if is_duplicate
                                else EvidenceType.LOCAL_ATTESTED
                            ),
                            evidence_source=(
                                "experiment_submissions/artifacts"
                                if is_duplicate
                                else "uploaded CONFIG/RESULT content"
                            ),
                            collected_at=datetime.now(UTC),
                            collection_tool="experiment-guardian-r11-rules",
                            constraint_source=None,
                            constraint_status=None,
                            inference_basis=None,
                            confidence=None,
                            constraint_candidates=[],
                            recommendation=(
                                "确认该运行是否有必要保留为独立实验"
                                if is_duplicate
                                else "修正文件或声明后创建新的 Submission"
                            ),
                            blocking=bool(raw.get("blocking", False)),
                            resolved=False,
                        )
                    )
                    existing.add(fingerprint)
                session.flush()
                risks = self._repository.list_risks(session, submission.id)
                severities = [item.severity for item in risks]
                highest = max(severities, key=RISK_RANK.__getitem__) if severities else None
                self._complete_step(
                    submission,
                    WorkflowStep.RISK_ANALYSIS,
                    "risk_summary",
                    {
                        "risk_ids": [str(item.id) for item in risks],
                        "count": len(risks),
                        "blocking_count": sum(item.blocking for item in risks),
                        "highest_risk": highest.value if highest else None,
                    },
                    final=True,
                )

        run_with_serialization_retry(persist)
        return state

    def _load_parse_targets(
        self, submission_id: UUID
    ) -> tuple[_ArtifactVersion, _ArtifactVersion] | None:
        with self._session_factory() as session, session.begin():
            submission = self._repository.get_submission_for_update(session, submission_id)
            if not self._can_execute(submission, WorkflowStep.CONFIG_PARSE):
                return None
            assert submission is not None
            artifacts = self._repository.list_artifacts(session, submission.id)
            selected = [
                item
                for item in artifacts
                if item.artifact_type in {ArtifactType.CONFIG, ArtifactType.RESULT}
            ]
            if len(selected) != 2 or any(
                not item.cloud_hash_verified or not item.s3_version_id for item in selected
            ):
                self._terminal_in_session(
                    submission,
                    WorkflowStep.CONFIG_PARSE,
                    "UPLOAD_EVIDENCE_INVALID",
                    "CONFIG/RESULT 缺少已验证的不可变对象版本",
                )
                return None
            return tuple(
                _ArtifactVersion(
                    id=item.id,
                    filename=item.filename,
                    artifact_type=item.artifact_type,
                    object_key=item.s3_key,
                    sha256=item.sha256,
                    version_id=str(item.s3_version_id),
                )
                for item in selected
            )  # type: ignore[return-value]

    def _read_verified_version(self, artifact: _ArtifactVersion) -> bytes:
        payload = self._storage.read_object_version(
            object_key=artifact.object_key,
            version_id=artifact.version_id,
            max_bytes=MAX_ANALYZED_DOCUMENT_BYTES,
        )
        if payload is None:
            raise SubmissionDocumentError(
                f"{artifact.artifact_type.value} 的固定 S3 VersionId 已不存在"
            )
        actual_hash = hashlib.sha256(payload).hexdigest()
        if actual_hash != artifact.sha256:
            raise SubmissionDocumentError(
                f"{artifact.artifact_type.value} 下载内容哈希与已验证声明不一致"
            )
        return payload

    def _record_failure(
        self,
        submission_id: UUID,
        step: WorkflowStep,
        code: str,
        message: str,
        *,
        retryable: bool,
    ) -> None:
        def persist() -> None:
            with self._session_factory() as session, session.begin():
                submission = self._repository.get_submission_for_update(session, submission_id)
                if submission is None or self._step_completed(submission, step):
                    return
                if retryable:
                    submission.status = SubmissionStatus.PROCESSING
                    submission.workflow_status = WorkflowStatus.RETRYABLE_FAILURE
                    submission.processing_error = self._error(step, code, message, True)
                else:
                    self._terminal_in_session(submission, step, code, message)

        run_with_serialization_retry(persist)

    @staticmethod
    def _can_execute(submission: ExperimentSubmission | None, step: WorkflowStep) -> bool:
        return bool(
            submission is not None
            and submission.workflow_status is WorkflowStatus.RUNNING
            and not SubmissionAnalysisService._step_completed(submission, step)
        )

    @staticmethod
    def _step_completed(submission: ExperimentSubmission, step: WorkflowStep) -> bool:
        current = submission.processing_step
        return current is not None and STEP_INDEX.get(current, -1) >= STEP_INDEX[step]

    @staticmethod
    def _snapshot(submission: ExperimentSubmission) -> dict[str, Any]:
        value = submission.analysis_snapshot
        return dict(value) if isinstance(value, dict) else {"schema_version": 1, "steps": {}}

    def _complete_step(
        self,
        submission: ExperimentSubmission,
        step: WorkflowStep,
        section: str,
        payload: dict[str, Any],
        *,
        final: bool = False,
    ) -> None:
        snapshot = self._snapshot(submission)
        snapshot[section] = payload
        steps = dict(snapshot.get("steps", {}))
        steps[step.value] = {
            "completed_at": datetime.now(UTC).isoformat(),
            "output_hash": canonical_json_hash(payload),
        }
        snapshot["schema_version"] = 1
        snapshot["steps"] = steps
        encoded = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > MAX_ANALYSIS_SNAPSHOT_BYTES:
            self._terminal_in_session(
                submission,
                step,
                "ANALYSIS_SNAPSHOT_TOO_LARGE",
                "分析快照超过 3 MiB 上限",
            )
            return
        submission.analysis_snapshot = snapshot
        submission.processing_step = step
        submission.processing_error = None
        if final:
            submission.status = SubmissionStatus.PROCESSING
            submission.workflow_status = WorkflowStatus.AWAITING_ENRICHMENT
        else:
            submission.workflow_status = WorkflowStatus.RUNNING

    @staticmethod
    def _terminal_in_session(
        submission: ExperimentSubmission,
        step: WorkflowStep,
        code: str,
        message: str,
    ) -> None:
        submission.status = SubmissionStatus.FAILED
        submission.workflow_status = WorkflowStatus.TERMINAL_FAILURE
        submission.processing_error = SubmissionAnalysisService._error(step, code, message, False)

    @staticmethod
    def _error(step: WorkflowStep, code: str, message: str, retryable: bool) -> dict[str, Any]:
        return {
            "code": code,
            "message": message[:2000],
            "failed_step": step.value,
            "retryable": retryable,
            "occurred_at": datetime.now(UTC).isoformat(),
        }

    @staticmethod
    def _finding(
        *, code: str, field_path: str, current: Any, expected: Any, message: str
    ) -> dict[str, Any]:
        return {
            "code": code,
            "severity": RiskSeverity.CRITICAL.value,
            "field_path": field_path,
            "current": current,
            "expected": expected,
            "message": message,
            "blocking": True,
        }

    @classmethod
    def _compare(
        cls,
        findings: list[dict[str, Any]],
        *,
        code: str,
        field_path: str,
        actual: Any,
        expected: Any,
        message: str,
    ) -> None:
        if canonical_json_hash(actual) != canonical_json_hash(expected):
            findings.append(
                cls._finding(
                    code=code,
                    field_path=field_path,
                    current=actual,
                    expected=expected,
                    message=message,
                )
            )

    def _artifact_hashes(self, session: Session, submission_id: UUID) -> dict[str, str] | None:
        artifacts = self._repository.list_artifacts(session, submission_id)
        selected = {
            item.artifact_type.value: item.sha256
            for item in artifacts
            if item.artifact_type in {ArtifactType.CONFIG, ArtifactType.RESULT}
            and item.cloud_hash_verified
            and item.s3_version_id
        }
        return selected if set(selected) == {"CONFIG", "RESULT"} else None

    @staticmethod
    def _same_run_conditions(left: RunManifest, right: RunManifest) -> bool:
        return (
            left.dataset,
            left.protocol,
            left.seed,
            left.git_commit,
            left.config_hash,
        ) == (
            right.dataset,
            right.protocol,
            right.seed,
            right.git_commit,
            right.config_hash,
        )
