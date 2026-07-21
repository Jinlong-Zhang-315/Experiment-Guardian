"""Submission 上传完成确认与 S3 对象复核纵向测试。"""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.errors import (
    AuthorizationError,
    ConflictError,
    ServiceUnavailableError,
)
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.services import GuardianApplication
from experiment_guardian.domain.contracts import (
    StoredObjectMetadata,
    SubmissionFinalizeCommand,
    SubmissionPrepareCommand,
)
from experiment_guardian.domain.enums import (
    ArtifactVerificationIssueCode,
    IdempotencyOperationStatus,
    RiskSeverity,
    SubmissionStatus,
    TeamRole,
    UploadVerificationResult,
    WorkflowStatus,
    WorkflowStep,
)
from experiment_guardian.infrastructure.models import (
    Artifact,
    AuditLog,
    ExperimentSubmission,
    IdempotencyRecord,
    SubmissionRisk,
    TeamMember,
    User,
)
from tests.integration.test_submission_prepare_slice import (
    CONFIG_PAYLOAD,
    FakeStorage,
    build_submission_service,
    initialize_manifest,
    prepare_command,
    submission_identity,
)


def finalize_identity(identity: RequestIdentity, project_id: UUID) -> RequestIdentity:
    return RequestIdentity(
        user_id=identity.user_id,
        team_id=identity.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"submission:finalize"}),
    )


def prepare_draft(
    factory: sessionmaker[Session],
) -> tuple[
    RequestIdentity,
    UUID,
    FakeStorage,
    GuardianApplication,
    SubmissionFinalizeCommand,
    SubmissionPrepareCommand,
]:
    owner, project_id, manifest_id = initialize_manifest(factory)
    storage = FakeStorage()
    service = build_submission_service(factory, storage)
    prepare = prepare_command(project_id=project_id, manifest_id=manifest_id)
    prepared = service.submission_prepare(prepare, submission_identity(owner, project_id))
    finalize = SubmissionFinalizeCommand(
        submission_id=prepared.submission_id,
        idempotency_key=uuid4(),
    )
    return owner, project_id, storage, service, finalize, prepare


def test_finalize_persists_cloud_evidence_and_replays_without_new_s3_calls(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, storage, service, command, prepare = prepare_draft(
        plan_check_session_factory
    )
    storage.accept_declared_uploads()
    identity = finalize_identity(owner, project_id)

    first = service.submission_finalize(command, identity)
    inspected = list(storage.inspection_calls)
    read_versions = list(storage.read_calls)
    replay = service.submission_finalize(command, identity)

    assert first.verification_result is UploadVerificationResult.PASS
    assert first.status is SubmissionStatus.UPLOAD_VERIFIED
    assert len(first.artifact_verifications) == 2
    assert replay == first
    assert storage.inspection_calls == inspected
    assert storage.read_calls == read_versions

    prepared_replay = service.submission_prepare(prepare, submission_identity(owner, project_id))
    assert prepared_replay.status is SubmissionStatus.UPLOAD_VERIFIED
    assert prepared_replay.artifact_uploads == []

    with plan_check_session_factory() as session:
        submission = session.get(ExperimentSubmission, command.submission_id)
        assert submission is not None
        assert submission.status is SubmissionStatus.PROCESSING
        assert submission.workflow_status is WorkflowStatus.AWAITING_ENRICHMENT
        assert submission.processing_step is WorkflowStep.RISK_ANALYSIS
        assert submission.processing_error is None
        assert submission.analysis_snapshot["schema_version"] == 1
        assert set(submission.analysis_snapshot["steps"]) == {
            step.value
            for step in (
                WorkflowStep.UPLOAD_VERIFICATION,
                WorkflowStep.CONFIG_PARSE,
                WorkflowStep.MANIFEST_VALIDATION,
                WorkflowStep.DUPLICATE_CHECK,
                WorkflowStep.RISK_ANALYSIS,
            )
        }
        assert submission.upload_verified_by == owner.user_id
        assert submission.upload_verified_at is not None
        assert submission.upload_verification_snapshot["verification_result"] == "PASS"
        assert "analysis" not in submission.upload_verification_snapshot
        artifacts = session.scalars(
            select(Artifact).where(Artifact.submission_id == command.submission_id)
        ).all()
        assert all(item.cloud_hash_verified for item in artifacts)
        assert all(item.verified_at is not None for item in artifacts)
        assert all(
            item.verification_evidence["evidence_type"] == "CLOUD_VERIFIED" for item in artifacts
        )
        assert all(item.s3_version_id == "test-version" for item in artifacts)
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "submission.finalize")
            )
            == 1
        )
        record = session.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.operation == "submission.finalize")
        )
        assert record is not None
        assert record.operation_status is IdempotencyOperationStatus.COMPLETED

    with pytest.raises(ConflictError, match="状态不允许 finalize"):
        service.submission_finalize(
            command.model_copy(update={"idempotency_key": uuid4()}), identity
        )


def test_failed_verification_is_complete_and_same_key_can_succeed_after_repair(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, storage, service, command, prepare = prepare_draft(
        plan_check_session_factory
    )
    first_call = storage.calls[0]
    second_call = storage.calls[1]
    first_key = str(first_call["object_key"])
    second_key = str(second_call["object_key"])
    storage.objects[first_key] = StoredObjectMetadata(
        content_length=int(first_call["content_length"]) + 1,
        content_type="text/plain",
        checksum_sha256=None,
        version_id="bad-version",
        observed_at=datetime(2026, 7, 22, tzinfo=UTC),
        evidence_source=f"s3://test-bucket/{first_key}",
    )
    identity = finalize_identity(owner, project_id)

    failed = service.submission_finalize(command, identity)
    codes = {item.code for item in failed.issues}
    assert failed.verification_result is UploadVerificationResult.FAILED
    assert failed.status is SubmissionStatus.RECEIVED
    assert failed.retryable
    assert set(failed.reupload_artifact_ids) == {item.artifact_id for item in failed.issues}
    assert codes == {
        ArtifactVerificationIssueCode.CONTENT_LENGTH_MISMATCH,
        ArtifactVerificationIssueCode.CONTENT_TYPE_MISMATCH,
        ArtifactVerificationIssueCode.CHECKSUM_SHA256_MISSING,
        ArtifactVerificationIssueCode.OBJECT_MISSING,
    }
    with plan_check_session_factory() as session:
        submission = session.get(ExperimentSubmission, command.submission_id)
        assert submission is not None and submission.status is SubmissionStatus.RECEIVED
        artifacts = session.scalars(
            select(Artifact)
            .where(Artifact.submission_id == command.submission_id)
            .order_by(Artifact.artifact_type, Artifact.filename)
        ).all()
        assert all(not item.cloud_hash_verified for item in artifacts)
        artifact_keys = {item.filename: item.s3_key for item in artifacts}
        assert artifact_keys["config.yaml"] != first_key
        assert artifact_keys["result.json"] == second_key
        record = session.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.operation == "submission.finalize")
        )
        assert record is not None
        assert record.operation_status is IdempotencyOperationStatus.FAILED
        failure_audit = session.scalar(
            select(AuditLog).where(AuditLog.action == "submission.finalize.failed")
        )
        assert failure_audit is not None
        assert failure_audit.after_value["token_id"] == str(identity.token_id)
        assert failure_audit.after_value["source_agent"] == ("experiment-guardian-local/0.1")
        assert len(failure_audit.after_value["replacement_keys"]) == 1

    repeated_failure = service.submission_finalize(command, identity)
    assert repeated_failure.verification_result is UploadVerificationResult.FAILED
    with plan_check_session_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "submission.finalize.failed")
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(IdempotencyRecord.operation == "submission.finalize")
            )
            == 1
        )

    reissued = service.submission_prepare(prepare, submission_identity(owner, project_id))
    reissued_keys = {
        item.filename: str(storage.calls[index + 2]["object_key"])
        for index, item in enumerate(reissued.artifact_uploads)
    }
    assert reissued_keys["config.yaml"] != first_key
    assert reissued_keys["result.json"] == second_key

    second_prepare = prepare.model_copy(update={"idempotency_key": uuid4()})
    second_submission = service.submission_prepare(
        second_prepare, submission_identity(owner, project_id)
    )
    with pytest.raises(ConflictError, match="不同的 finalize 请求"):
        service.submission_finalize(
            command.model_copy(update={"submission_id": second_submission.submission_id}),
            identity,
        )

    storage.accept_declared_uploads()
    recovered = service.submission_finalize(command, identity)
    assert recovered.verification_result is UploadVerificationResult.PASS
    with plan_check_session_factory() as session:
        records = session.scalars(
            select(IdempotencyRecord).where(IdempotencyRecord.operation == "submission.finalize")
        ).all()
        assert len(records) == 1
        assert records[0].operation_status is IdempotencyOperationStatus.COMPLETED
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "submission.finalize.failed")
            )
            == 2
        )


def test_finalize_requires_a_non_null_s3_version_and_rotates_unversioned_object(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, storage, service, command, _ = prepare_draft(plan_check_session_factory)
    storage.accept_declared_uploads()
    first_key = str(storage.calls[0]["object_key"])
    storage.objects[first_key] = storage.objects[first_key].model_copy(update={"version_id": None})

    result = service.submission_finalize(command, finalize_identity(owner, project_id))

    assert ArtifactVerificationIssueCode.S3_VERSION_ID_MISSING in {
        issue.code for issue in result.issues
    }
    with plan_check_session_factory() as session:
        artifact = session.scalar(
            select(Artifact).where(
                Artifact.submission_id == command.submission_id,
                Artifact.filename == "config.yaml",
            )
        )
        assert artifact is not None
        assert artifact.s3_key != first_key


def test_storage_outage_does_not_persist_partial_verification(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, storage, service, command, _ = prepare_draft(plan_check_session_factory)
    storage.inspection_error = ServiceUnavailableError("S3 暂时不可用")

    with pytest.raises(ServiceUnavailableError, match="暂时不可用"):
        service.submission_finalize(command, finalize_identity(owner, project_id))

    with plan_check_session_factory() as session:
        submission = session.get(ExperimentSubmission, command.submission_id)
        assert submission is not None
        assert submission.status is SubmissionStatus.RECEIVED
        assert submission.upload_verified_at is None
        artifacts = session.scalars(
            select(Artifact).where(Artifact.submission_id == command.submission_id)
        ).all()
        assert all(not artifact.cloud_hash_verified for artifact in artifacts)
        assert all(artifact.verification_evidence is None for artifact in artifacts)
        assert (
            session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(IdempotencyRecord.operation == "submission.finalize")
            )
            == 0
        )


def test_finalize_scope_project_and_original_submitter_are_enforced(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, storage, service, command, _ = prepare_draft(plan_check_session_factory)
    storage.accept_declared_uploads()

    missing_scope = RequestIdentity(
        user_id=owner.user_id,
        team_id=owner.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset(),
    )
    with pytest.raises(AuthorizationError, match="submission:finalize"):
        service.submission_finalize(command, missing_scope)

    wrong_project = RequestIdentity(
        user_id=owner.user_id,
        team_id=owner.team_id,
        token_id=uuid4(),
        project_id=uuid4(),
        scopes=frozenset({"submission:finalize"}),
    )
    with pytest.raises(AuthorizationError, match="未绑定"):
        service.submission_finalize(command, wrong_project)

    other_user_id = uuid4()
    with plan_check_session_factory() as session, session.begin():
        session.add(
            User(
                id=other_user_id,
                name="Other Researcher",
                email="other-finalizer@example.com",
            )
        )
        session.flush()
        session.add(
            TeamMember(
                team_id=owner.team_id,
                user_id=other_user_id,
                role=TeamRole.RESEARCHER,
            )
        )
    other = RequestIdentity(
        user_id=other_user_id,
        team_id=owner.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"submission:finalize"}),
    )
    with pytest.raises(AuthorizationError, match="原提交者"):
        service.submission_finalize(command, other)


def test_owner_can_recover_finalize_for_a_researcher_submission(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, manifest_id = initialize_manifest(plan_check_session_factory)
    researcher_id = uuid4()
    with plan_check_session_factory() as session, session.begin():
        session.add(
            User(
                id=researcher_id,
                name="Unavailable Researcher",
                email="unavailable-researcher@example.com",
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
    researcher = RequestIdentity(
        user_id=researcher_id,
        team_id=owner.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"submission:create"}),
    )
    storage = FakeStorage()
    service = build_submission_service(plan_check_session_factory, storage)
    prepared = service.submission_prepare(
        prepare_command(project_id=project_id, manifest_id=manifest_id), researcher
    )
    storage.accept_declared_uploads()
    owner_identity = finalize_identity(owner, project_id)

    result = service.submission_finalize(
        SubmissionFinalizeCommand(
            submission_id=prepared.submission_id,
            idempotency_key=uuid4(),
        ),
        owner_identity,
    )

    assert result.status is SubmissionStatus.UPLOAD_VERIFIED
    with plan_check_session_factory() as session:
        audit = session.scalar(select(AuditLog).where(AuditLog.action == "submission.finalize"))
        assert audit is not None
        assert audit.after_value["finalizer_mode"] == "OWNER_RECOVERY"
        assert audit.after_value["token_id"] == str(owner_identity.token_id)
        assert audit.after_value["source_agent"] == ("experiment-guardian-local/0.1")


def test_analysis_transient_s3_failure_resumes_with_same_finalize_key(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, storage, service, command, _ = prepare_draft(plan_check_session_factory)
    storage.accept_declared_uploads()
    storage.read_error = ServiceUnavailableError("temporary get failure")
    identity = finalize_identity(owner, project_id)

    failed = service.submission_finalize(command, identity)
    inspected = list(storage.inspection_calls)
    assert failed.analysis is not None
    assert failed.analysis.workflow_status is WorkflowStatus.RETRYABLE_FAILURE
    assert failed.analysis.processing_step is WorkflowStep.UPLOAD_VERIFICATION
    assert failed.analysis.retryable
    assert failed.analysis.error is not None
    assert failed.analysis.error["code"] == "S3_READ_UNAVAILABLE"

    storage.read_error = None
    resumed = service.submission_finalize(command, identity)
    assert resumed.analysis is not None
    assert resumed.analysis.workflow_status is WorkflowStatus.AWAITING_ENRICHMENT
    assert resumed.analysis.processing_step is WorkflowStep.RISK_ANALYSIS
    assert storage.inspection_calls == inspected
    assert len(storage.read_calls) == 3


def test_changed_immutable_version_is_a_terminal_analysis_failure(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, storage, service, command, _ = prepare_draft(plan_check_session_factory)
    storage.accept_declared_uploads()
    result_key = str(storage.calls[1]["object_key"])
    storage.payloads[(result_key, "test-version")] = b"{}"

    result = service.submission_finalize(command, finalize_identity(owner, project_id))

    assert result.analysis is not None
    assert result.analysis.submission_status is SubmissionStatus.FAILED
    assert result.analysis.workflow_status is WorkflowStatus.TERMINAL_FAILURE
    assert result.analysis.retryable is False
    assert result.analysis.error is not None
    assert result.analysis.error["code"] == "SUBMITTED_DOCUMENT_INVALID"


def _prepare_with_result_payload(
    *,
    storage: FakeStorage,
    service: GuardianApplication,
    owner: RequestIdentity,
    project_id: UUID,
    manifest_id: UUID,
    payload: bytes,
) -> tuple[SubmissionFinalizeCommand, RequestIdentity]:
    raw = prepare_command(project_id=project_id, manifest_id=manifest_id).model_dump(mode="python")
    parsed_result = json.loads(payload)
    raw["metrics_summary"] = parsed_result["metrics"]
    raw["files"][1]["size_bytes"] = len(payload)
    raw["files"][1]["sha256"] = hashlib.sha256(payload).hexdigest()
    command = SubmissionPrepareCommand.model_validate(raw)
    prepared = service.submission_prepare(command, submission_identity(owner, project_id))
    storage.accept_declared_uploads()
    result_call = next(
        item
        for item in reversed(storage.calls)
        if item["sha256"] == hashlib.sha256(payload).hexdigest()
    )
    storage.payloads[(str(result_call["object_key"]), "test-version")] = payload
    return (
        SubmissionFinalizeCommand(
            submission_id=prepared.submission_id,
            idempotency_key=uuid4(),
        ),
        finalize_identity(owner, project_id),
    )


def test_manifest_mismatch_becomes_blocking_critical_risk_not_terminal_failure(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, manifest_id = initialize_manifest(plan_check_session_factory)
    storage = FakeStorage()
    service = build_submission_service(plan_check_session_factory, storage)
    changed_config = CONFIG_PAYLOAD.replace(b"fusion: 0.3", b"fusion: 0.4")
    raw = prepare_command(project_id=project_id, manifest_id=manifest_id).model_dump(mode="python")
    raw["files"][0]["size_bytes"] = len(changed_config)
    raw["files"][0]["sha256"] = hashlib.sha256(changed_config).hexdigest()
    prepare = SubmissionPrepareCommand.model_validate(raw)
    prepared = service.submission_prepare(prepare, submission_identity(owner, project_id))
    storage.accept_declared_uploads()
    config_call = next(
        item
        for item in storage.calls
        if item["sha256"] == hashlib.sha256(changed_config).hexdigest()
    )
    storage.payloads[(str(config_call["object_key"]), "test-version")] = changed_config

    result = service.submission_finalize(
        SubmissionFinalizeCommand(
            submission_id=prepared.submission_id,
            idempotency_key=uuid4(),
        ),
        finalize_identity(owner, project_id),
    )

    assert result.analysis is not None
    assert result.analysis.workflow_status is WorkflowStatus.AWAITING_ENRICHMENT
    assert result.analysis.highest_risk is RiskSeverity.CRITICAL
    with plan_check_session_factory() as session:
        risks = session.scalars(
            select(SubmissionRisk).where(SubmissionRisk.submission_id == prepared.submission_id)
        ).all()
        assert {item.risk_type for item in risks} >= {
            "CONFIG_DOCUMENT_HASH_MISMATCH",
            "CONFIG_CANONICAL_HASH_MISMATCH",
            "CONFIG_SNAPSHOT_MISMATCH",
        }
        assert all(item.blocking for item in risks)
        assert all(item.evidence_type.value == "LOCAL_ATTESTED" for item in risks)


def test_duplicate_detection_is_project_scoped_and_uses_nonblocking_severity(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, manifest_id = initialize_manifest(plan_check_session_factory)
    storage = FakeStorage()
    service = build_submission_service(plan_check_session_factory, storage)

    first_prepare = prepare_command(project_id=project_id, manifest_id=manifest_id)
    first = service.submission_prepare(first_prepare, submission_identity(owner, project_id))
    storage.accept_declared_uploads()
    service.submission_finalize(
        SubmissionFinalizeCommand(submission_id=first.submission_id, idempotency_key=uuid4()),
        finalize_identity(owner, project_id),
    )

    second_prepare = prepare_command(project_id=project_id, manifest_id=manifest_id)
    second = service.submission_prepare(second_prepare, submission_identity(owner, project_id))
    storage.accept_declared_uploads()
    exact = service.submission_finalize(
        SubmissionFinalizeCommand(submission_id=second.submission_id, idempotency_key=uuid4()),
        finalize_identity(owner, project_id),
    )
    assert exact.analysis is not None
    assert exact.analysis.highest_risk is RiskSeverity.MEDIUM

    different_result = json.dumps(
        {
            "schema_version": 1,
            "status": "COMPLETED",
            "metrics": {"top1": 0.84},
            "failure_reason": None,
        },
        separators=(",", ":"),
    ).encode()
    command, identity = _prepare_with_result_payload(
        storage=storage,
        service=service,
        owner=owner,
        project_id=project_id,
        manifest_id=manifest_id,
        payload=different_result,
    )
    same_conditions = service.submission_finalize(command, identity)
    assert same_conditions.analysis is not None
    assert same_conditions.analysis.highest_risk is RiskSeverity.LOW

    with plan_check_session_factory() as session:
        exact_risks = session.scalars(
            select(SubmissionRisk).where(SubmissionRisk.submission_id == second.submission_id)
        ).all()
        condition_risks = session.scalars(
            select(SubmissionRisk).where(SubmissionRisk.submission_id == command.submission_id)
        ).all()
        assert {item.risk_type for item in exact_risks} == {"EXACT_DUPLICATE_SUBMISSION"}
        assert all(item.severity is RiskSeverity.MEDIUM for item in exact_risks)
        assert {item.risk_type for item in condition_risks} == {"SAME_RUN_CONDITIONS"}
        assert all(item.severity is RiskSeverity.LOW for item in condition_risks)
        assert all(not item.blocking for item in [*exact_risks, *condition_risks])
