"""Run Manifest 到 S3 上传草稿的纵向验收测试。"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.errors import (
    AuthorizationError,
    ConflictError,
    ResourceNotFoundError,
    ServiceUnavailableError,
)
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.services import GuardianApplication
from experiment_guardian.domain.contracts import (
    PresignedUpload,
    StoredObjectMetadata,
    SubmissionPrepareCommand,
)
from experiment_guardian.domain.enums import TeamRole
from experiment_guardian.infrastructure.models import (
    Artifact,
    AuditLog,
    ExperimentSubmission,
    IdempotencyRecord,
    TeamMember,
    User,
)
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyGovernanceRepository,
    SqlAlchemyPlanCheckRepository,
    SqlAlchemyProjectRepository,
    SqlAlchemySubmissionRepository,
)
from tests.integration.test_governance_slice import manifest_identity
from tests.integration.test_plan_check_slice import command, initialize_policy


class FakeStorage:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.inspection_calls: list[str] = []
        self.objects: dict[str, StoredObjectMetadata] = {}
        self.inspection_error: Exception | None = None
        self.fail = False

    def create_upload_url(
        self,
        *,
        object_key: str,
        content_type: str,
        content_length: int,
        sha256: str,
        expires_in: int,
    ) -> PresignedUpload:
        self.calls.append(
            {
                "object_key": object_key,
                "content_type": content_type,
                "content_length": content_length,
                "sha256": sha256,
                "expires_in": expires_in,
            }
        )
        if self.fail:
            raise RuntimeError("signing unavailable")
        return PresignedUpload(
            upload_url=f"https://upload.example.invalid/{len(self.calls)}",
            required_headers={"Content-Type": content_type},
        )

    def inspect_object(self, *, object_key: str) -> StoredObjectMetadata | None:
        self.inspection_calls.append(object_key)
        if self.inspection_error is not None:
            raise self.inspection_error
        return self.objects.get(object_key)

    def accept_declared_uploads(self) -> None:
        for call in self.calls:
            object_key = str(call["object_key"])
            self.objects[object_key] = StoredObjectMetadata(
                content_length=int(call["content_length"]),
                content_type=str(call["content_type"]),
                checksum_sha256=str(call["sha256"]),
                checksum_type="FULL_OBJECT",
                etag="test-etag",
                version_id="test-version",
                last_modified=datetime(2026, 7, 22, tzinfo=UTC),
                observed_at=datetime(2026, 7, 22, tzinfo=UTC),
                evidence_source=f"s3://test-bucket/{object_key}",
            )


def build_submission_service(
    factory: sessionmaker[Session], storage: FakeStorage
) -> GuardianApplication:
    return GuardianApplication(
        factory,
        SqlAlchemyProjectRepository(),
        SqlAlchemyPlanCheckRepository(),
        SqlAlchemyGovernanceRepository(),
        SqlAlchemySubmissionRepository(),
        storage,
        900,
    )


def prepare_command(
    *, project_id: UUID, manifest_id: UUID, idempotency_key: UUID | None = None
) -> SubmissionPrepareCommand:
    return SubmissionPrepareCommand.model_validate(
        {
            "project_id": project_id,
            "run_manifest_id": manifest_id,
            "idempotency_key": idempotency_key or uuid4(),
            "source_agent": "experiment-guardian-local/0.1",
            "collected_at": datetime(2026, 7, 21, tzinfo=UTC),
            "experiment_status": "COMPLETED",
            "metrics_summary": {"top1": 0.83},
            "files": [
                {
                    "filename": "config.yaml",
                    "artifact_type": "CONFIG",
                    "mime_type": "application/yaml",
                    "size_bytes": 128,
                    "sha256": "a" * 64,
                },
                {
                    "filename": "result.json",
                    "artifact_type": "RESULT",
                    "mime_type": "application/json",
                    "size_bytes": 256,
                    "sha256": "b" * 64,
                },
            ],
        }
    )


def initialize_manifest(
    factory: sessionmaker[Session],
) -> tuple[RequestIdentity, UUID, UUID]:
    identity, project_id, intent_id, guardian = initialize_policy(factory)
    plan = guardian.experiment_check_plan(
        command(project_id=project_id, intent_id=intent_id), identity
    )
    manifest = guardian.run_manifest_create(
        plan_check_id=plan.plan_check_id,
        identity=manifest_identity(identity, project_id),
        idempotency_key=uuid4(),
    )
    return identity, project_id, manifest.manifest_id


def submission_identity(identity: RequestIdentity, project_id: UUID) -> RequestIdentity:
    return RequestIdentity(
        user_id=identity.user_id,
        team_id=identity.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"submission:create"}),
    )


def test_researcher_can_prepare_persisted_submission_with_fresh_replay_urls(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, manifest_id = initialize_manifest(plan_check_session_factory)
    researcher_id = uuid4()
    with plan_check_session_factory() as session, session.begin():
        session.add(
            User(
                id=researcher_id,
                name="Researcher",
                email="submission-researcher@example.com",
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
    identity = RequestIdentity(
        user_id=researcher_id,
        team_id=owner.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"submission:create"}),
    )
    storage = FakeStorage()
    service = build_submission_service(plan_check_session_factory, storage)
    request = prepare_command(project_id=project_id, manifest_id=manifest_id)

    first = service.submission_prepare(request, identity)
    replay = service.submission_prepare(request, identity)

    assert replay.submission_id == first.submission_id
    assert [item.artifact_id for item in replay.artifact_uploads] == [
        item.artifact_id for item in first.artifact_uploads
    ]
    assert replay.artifact_uploads[0].upload_url != first.artifact_uploads[0].upload_url
    assert all(item.expires_at > datetime.now(UTC) for item in replay.artifact_uploads)
    assert len(storage.calls) == 4
    object_keys = [str(call["object_key"]) for call in storage.calls]
    assert all("config.yaml" not in key and "result.json" not in key for key in object_keys)

    with plan_check_session_factory() as session:
        submission = session.get(ExperimentSubmission, first.submission_id)
        assert submission is not None
        assert submission.status.value == "RECEIVED"
        assert submission.evidence_snapshot["metrics_summary"]["evidence_type"] == (
            "LOCAL_ATTESTED"
        )
        assert session.scalar(select(func.count()).select_from(ExperimentSubmission)) == 1
        assert session.scalar(select(func.count()).select_from(Artifact)) == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "submission.prepare")
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(IdempotencyRecord.operation == "submission.prepare")
            )
            == 1
        )
        artifacts = session.scalars(select(Artifact)).all()
        assert all(not item.cloud_hash_verified for item in artifacts)

    changed = request.model_copy(deep=True)
    changed.metrics_summary["top1"] = 0.84
    with pytest.raises(ConflictError, match="不同的 Submission 请求"):
        service.submission_prepare(changed, identity)

    second_run = request.model_copy(update={"idempotency_key": uuid4()})
    second = service.submission_prepare(second_run, identity)
    assert second.submission_id != first.submission_id


def test_presign_failure_keeps_one_received_draft_and_same_key_recovers(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, manifest_id = initialize_manifest(plan_check_session_factory)
    identity = submission_identity(owner, project_id)
    storage = FakeStorage()
    storage.fail = True
    service = build_submission_service(plan_check_session_factory, storage)
    request = prepare_command(project_id=project_id, manifest_id=manifest_id)

    with pytest.raises(ServiceUnavailableError, match="预签名"):
        service.submission_prepare(request, identity)
    with plan_check_session_factory() as session:
        stored = session.scalar(select(ExperimentSubmission))
        assert stored is not None and stored.status.value == "RECEIVED"
        submission_id = stored.id
        assert session.scalar(select(func.count()).select_from(ExperimentSubmission)) == 1

    storage.fail = False
    recovered = service.submission_prepare(request, identity)
    assert recovered.submission_id == submission_id
    with plan_check_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ExperimentSubmission)) == 1


def test_submission_scope_project_team_and_manifest_are_enforced(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    owner, project_id, manifest_id = initialize_manifest(plan_check_session_factory)
    service = build_submission_service(plan_check_session_factory, FakeStorage())
    request = prepare_command(project_id=project_id, manifest_id=manifest_id)

    missing_scope = RequestIdentity(
        user_id=owner.user_id,
        team_id=owner.team_id,
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset(),
    )
    with pytest.raises(AuthorizationError, match="submission:create"):
        service.submission_prepare(request, missing_scope)

    wrong_project = RequestIdentity(
        user_id=owner.user_id,
        team_id=owner.team_id,
        token_id=uuid4(),
        project_id=uuid4(),
        scopes=frozenset({"submission:create"}),
    )
    with pytest.raises(AuthorizationError, match="未绑定"):
        service.submission_prepare(request, wrong_project)

    wrong_team = RequestIdentity(
        user_id=owner.user_id,
        team_id=uuid4(),
        token_id=uuid4(),
        project_id=project_id,
        scopes=frozenset({"submission:create"}),
    )
    with pytest.raises(AuthorizationError, match="团队"):
        service.submission_prepare(request, wrong_team)

    missing_manifest = request.model_copy(
        update={"run_manifest_id": uuid4(), "idempotency_key": uuid4()}
    )
    with pytest.raises(ResourceNotFoundError, match="Run Manifest"):
        service.submission_prepare(missing_manifest, submission_identity(owner, project_id))
