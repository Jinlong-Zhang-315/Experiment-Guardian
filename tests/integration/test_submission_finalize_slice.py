"""Submission 上传完成确认与 S3 对象复核纵向测试。"""

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
    SubmissionStatus,
    TeamRole,
    UploadVerificationResult,
)
from experiment_guardian.infrastructure.models import (
    Artifact,
    AuditLog,
    ExperimentSubmission,
    IdempotencyRecord,
    TeamMember,
    User,
)
from tests.integration.test_submission_prepare_slice import (
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
    replay = service.submission_finalize(command, identity)

    assert first.verification_result is UploadVerificationResult.PASS
    assert first.status is SubmissionStatus.UPLOAD_VERIFIED
    assert len(first.artifact_verifications) == 2
    assert replay == first
    assert storage.inspection_calls == inspected

    prepared_replay = service.submission_prepare(prepare, submission_identity(owner, project_id))
    assert prepared_replay.status is SubmissionStatus.UPLOAD_VERIFIED
    assert prepared_replay.artifact_uploads == []

    with plan_check_session_factory() as session:
        submission = session.get(ExperimentSubmission, command.submission_id)
        assert submission is not None
        assert submission.status is SubmissionStatus.UPLOAD_VERIFIED
        assert submission.upload_verified_by == owner.user_id
        assert submission.upload_verified_at is not None
        assert submission.upload_verification_snapshot["verification_result"] == "PASS"
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
    first_key = str(first_call["object_key"])
    storage.objects[first_key] = StoredObjectMetadata(
        content_length=int(first_call["content_length"]) + 1,
        content_type="text/plain",
        checksum_sha256=None,
        observed_at=datetime(2026, 7, 22, tzinfo=UTC),
        evidence_source=f"s3://test-bucket/{first_key}",
    )
    identity = finalize_identity(owner, project_id)

    failed = service.submission_finalize(command, identity)
    codes = {item.code for item in failed.issues}
    assert failed.verification_result is UploadVerificationResult.FAILED
    assert failed.status is SubmissionStatus.RECEIVED
    assert failed.retryable
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
            select(Artifact).where(Artifact.submission_id == command.submission_id)
        ).all()
        assert all(not item.cloud_hash_verified for item in artifacts)
        record = session.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.operation == "submission.finalize")
        )
        assert record is not None
        assert record.operation_status is IdempotencyOperationStatus.FAILED

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
