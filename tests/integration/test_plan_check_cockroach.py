"""CockroachDB 上的 Plan Check 迁移与事务链路验收。"""

import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, inspect, or_, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.async_summary import OutboxDispatcher
from experiment_guardian.application.errors import ConflictError, InputValidationError
from experiment_guardian.application.experiments import ExperimentQueryService
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.ports import EmbeddingModelOutput
from experiment_guardian.application.services import (
    GuardianApplication,
    PlanApprovalService,
    ProjectAdministrationService,
)
from experiment_guardian.core.config import get_settings
from experiment_guardian.domain.administration import (
    PlanCheckDecisionRequest,
    ProjectInitializeRequest,
)
from experiment_guardian.domain.contracts import (
    ConfigurationDocument,
    ExperimentCheckPlanCommand,
    ExperimentQueryCommand,
    FieldEvidence,
    GeneratedSummary,
    LocalAttestation,
    LocalEnvironment,
    PresignedUpload,
    StoredObjectMetadata,
    SubmissionFinalizeCommand,
    SubmissionPrepareCommand,
)
from experiment_guardian.domain.enums import (
    ApprovalDecision,
    ApprovalTargetType,
    ConfigFormat,
    EvidenceType,
    ExperimentStatus,
    OutboxStatus,
    SubmissionStatus,
    TeamRole,
    UploadVerificationResult,
    VerificationStatus,
    WorkflowJobStatus,
    WorkflowStatus,
    WorkflowStep,
)
from experiment_guardian.infrastructure.models import (
    ApprovalRecord,
    Artifact,
    Experiment,
    ExperimentSubmission,
    Memory,
    OutboxEvent,
    PlanCheck,
    RunManifest,
    SubmissionEmbedding,
    Team,
    TeamMember,
    User,
    WorkflowJob,
)
from experiment_guardian.infrastructure.queue import DatabaseOutboxQueue
from experiment_guardian.infrastructure.repositories import (
    SqlAlchemyGovernanceRepository,
    SqlAlchemyPlanCheckRepository,
    SqlAlchemyProjectRepository,
    SqlAlchemySubmissionRepository,
    SqlAlchemyWorkflowRepository,
)

RUN_COCKROACH_INTEGRATION = os.getenv("RUN_COCKROACH_INTEGRATION") == "1"
COLLECTED_AT = datetime(2026, 7, 21, tzinfo=UTC)
GIT_COMMIT = "a1b2c3d4"
RUN_COMMAND = "python train.py --config config.yaml"


class _QueryEmbeddingGenerator:
    model_id = "amazon.titan-embed-text-v2:0"

    @staticmethod
    def embed(_: str) -> EmbeddingModelOutput:
        return EmbeddingModelOutput(vector=[1.0, *([0.0] * 1023)], input_tokens=3)


class _FakeStorage:
    """CockroachDB 验收只关心事务数据，不在此处访问真实 S3。"""

    def __init__(self) -> None:
        self.call_count = 0
        self.declarations: dict[str, tuple[int, str, str]] = {}
        self.objects: dict[str, StoredObjectMetadata] = {}
        self.payload_by_sha256: dict[str, bytes] = {}
        self.version_payloads: dict[tuple[str, str], bytes] = {}

    def create_upload_url(
        self,
        *,
        object_key: str,
        content_type: str,
        content_length: int,
        sha256: str,
        expires_in: int,
    ) -> PresignedUpload:
        del expires_in
        self.call_count += 1
        self.declarations[object_key] = (content_length, content_type, sha256)
        return PresignedUpload(
            upload_url=f"https://upload.example.invalid/{self.call_count}",
            required_headers={"Content-Type": content_type},
        )

    def inspect_object(self, *, object_key: str) -> StoredObjectMetadata | None:
        return self.objects.get(object_key)

    def read_object_version(
        self, *, object_key: str, version_id: str, max_bytes: int
    ) -> bytes | None:
        payload = self.version_payloads.get((object_key, version_id))
        if payload is not None and len(payload) > max_bytes:
            raise InputValidationError("artifact too large")
        return payload

    def accept_declared_uploads(self) -> None:
        for object_key, (content_length, content_type, sha256) in self.declarations.items():
            self.objects[object_key] = StoredObjectMetadata(
                content_length=content_length,
                content_type=content_type,
                checksum_sha256=sha256,
                checksum_type="FULL_OBJECT",
                etag="cockroach-test-etag",
                version_id="cockroach-test-version",
                last_modified=COLLECTED_AT,
                observed_at=COLLECTED_AT,
                evidence_source=f"s3://cockroach-test/{object_key}",
            )
            payload = self.payload_by_sha256.get(sha256)
            if payload is not None:
                self.version_payloads[(object_key, "cockroach-test-version")] = payload


def _run_alembic(database_url: str, *arguments: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": database_url},
        timeout=180,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Alembic {' '.join(arguments)} failed:\n{result.stdout}\n{result.stderr}"
        )


def _cleanup_test_database_jobs(connection: object, database_name: str) -> None:
    """只取消随机验收库自己的 Schema Job，避免一次失败让后续测试永久挂起。"""

    rows = connection.execute(  # type: ignore[attr-defined]
        text(
            "SELECT job_id, status, description FROM [SHOW JOBS] "
            "WHERE description LIKE :database_pattern "
            "AND status IN ('running', 'paused', 'pause-requested', 'reverting')"
        ),
        {"database_pattern": f"%{database_name}%"},
    ).mappings()
    for row in rows:
        connection.exec_driver_sql(  # type: ignore[attr-defined]
            f"CANCEL JOB {int(row['job_id'])}"
        )


def _evidence(value: object, source: str) -> FieldEvidence:
    return FieldEvidence(
        value=value,
        evidence_type=EvidenceType.LOCAL_ATTESTED,
        source=source,
        collected_at=COLLECTED_AT,
        collection_tool="cockroach-integration-test/0.1",
    )


def _command(project_id: UUID, intent_id: UUID, content: str) -> ExperimentCheckPlanCommand:
    return ExperimentCheckPlanCommand(
        project_id=project_id,
        experiment_intent_id=intent_id,
        idempotency_key=uuid4(),
        configuration=ConfigurationDocument(format=ConfigFormat.YAML, content=content),
        command=RUN_COMMAND,
        git_commit=GIT_COMMIT,
        local_attestation=LocalAttestation(
            working_tree_clean=_evidence(True, "git status --porcelain"),
            git_branch=_evidence("main", "git branch --show-current"),
            git_commit=_evidence(GIT_COMMIT, "git rev-parse HEAD"),
            run_command=_evidence(RUN_COMMAND, "local run plan"),
            output_directory_exists=_evidence(False, "local filesystem"),
            checkpoint_exists=_evidence(True, "local filesystem"),
            checkpoint_path=_evidence("checkpoints/baseline.pt", "local run plan"),
            config_sha256=_evidence(
                hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "sha256sum config.yaml",
            ),
            git_diff_sha256=_evidence("b" * 64, "git diff"),
            environment=LocalEnvironment(
                python=_evidence("3.12.13", "python --version"),
                cuda=_evidence("12.4", "nvidia-smi"),
                pytorch=_evidence("2.7.0", "python import torch"),
            ),
        ),
    )


@pytest.mark.skipif(
    not RUN_COCKROACH_INTEGRATION,
    reason="set RUN_COCKROACH_INTEGRATION=1 to run the isolated CockroachDB test",
)
def test_plan_check_full_chain_on_isolated_cockroach_database() -> None:
    base_url = make_url(get_settings().database_url)
    assert base_url.get_backend_name() == "cockroachdb"
    database_name = f"eg_plan_it_{uuid4().hex}"
    test_url = base_url.set(database=database_name)
    rendered_test_url = test_url.render_as_string(hide_password=False)
    admin_engine = create_engine(base_url, isolation_level="AUTOCOMMIT")
    test_engine = None

    try:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
        _run_alembic(rendered_test_url, "upgrade", "head")

        test_engine = create_engine(test_url)
        assert {
            "approval_records",
            "run_manifests",
            "experiment_submissions",
            "artifacts",
            "submission_risks",
            "web_sessions",
            "oidc_transactions",
            "mcp_oauth_clients",
            "mcp_oauth_grants",
        } <= set(inspect(test_engine).get_table_names())
        assert "cognito_sub" in {
            column["name"] for column in inspect(test_engine).get_columns("users")
        }
        assert "provider" in {
            column["name"]
            for column in inspect(test_engine).get_columns("submission_embeddings")
        }
        assert "embedding_provider" in {
            column["name"] for column in inspect(test_engine).get_columns("memories")
        }
        assert {"target_submission_id", "executed_experiment_id"} <= {
            column["name"]
            for column in inspect(test_engine).get_columns("agent_action_proposals")
        }
        assert {
            "upload_verified_at",
            "upload_verified_by",
            "upload_verification_snapshot",
            "workflow_status",
            "processing_step",
            "processing_error",
            "analysis_snapshot",
            "review_receipt",
        } <= {
            column["name"] for column in inspect(test_engine).get_columns("experiment_submissions")
        }
        status_column = next(
            column
            for column in inspect(test_engine).get_columns("experiment_submissions")
            if column["name"] == "status"
        )
        assert status_column["type"].length == 32
        assert {"verified_at", "verification_evidence", "s3_version_id"} <= {
            column["name"] for column in inspect(test_engine).get_columns("artifacts")
        }
        test_engine.dispose()
        test_engine = None
        _run_alembic(rendered_test_url, "downgrade", "20260721_05")
        revision_05_engine = create_engine(test_url)
        try:
            assert not (
                {
                    "web_sessions",
                    "oidc_transactions",
                    "mcp_oauth_clients",
                    "mcp_oauth_grants",
                }
                & set(inspect(revision_05_engine).get_table_names())
            )
            assert "cognito_sub" not in {
                column["name"] for column in inspect(revision_05_engine).get_columns("users")
            }
            assert not (
                {
                    "upload_verified_at",
                    "upload_verified_by",
                    "upload_verification_snapshot",
                }
                & {
                    column["name"]
                    for column in inspect(revision_05_engine).get_columns("experiment_submissions")
                }
            )
            status_column = next(
                column
                for column in inspect(revision_05_engine).get_columns("experiment_submissions")
                if column["name"] == "status"
            )
            assert status_column["type"].length == 12
            assert not (
                {"verified_at", "verification_evidence", "s3_version_id"}
                & {
                    column["name"]
                    for column in inspect(revision_05_engine).get_columns("artifacts")
                }
            )
        finally:
            revision_05_engine.dispose()
        _run_alembic(rendered_test_url, "upgrade", "head")
        _run_alembic(rendered_test_url, "downgrade", "20260721_03")
        downgrade_engine = create_engine(test_url)
        try:
            assert not (
                {
                    "approval_records",
                    "run_manifests",
                    "experiment_submissions",
                    "artifacts",
                }
                & set(inspect(downgrade_engine).get_table_names())
            )
        finally:
            downgrade_engine.dispose()
        _run_alembic(rendered_test_url, "upgrade", "head")

        test_engine = create_engine(test_url)
        factory = sessionmaker(
            bind=test_engine,
            expire_on_commit=False,
            autoflush=False,
            class_=Session,
        )
        user_id, team_id = uuid4(), uuid4()
        with factory() as session, session.begin():
            session.add(User(id=user_id, name="Owner", email=f"{user_id}@example.invalid"))
            session.flush()
            session.add(Team(id=team_id, name="Cockroach Integration", owner_id=user_id))
            session.flush()
            session.add(TeamMember(team_id=team_id, user_id=user_id, role=TeamRole.OWNER))

        owner = RequestIdentity(
            user_id=user_id,
            team_id=team_id,
            token_id=uuid4(),
            scopes=frozenset({"project:initialize"}),
        )
        projects = SqlAlchemyProjectRepository()
        governance = SqlAlchemyGovernanceRepository()
        administration = ProjectAdministrationService(factory, projects)
        storage = _FakeStorage()
        guardian = GuardianApplication(
            factory,
            projects,
            SqlAlchemyPlanCheckRepository(),
            governance,
            SqlAlchemySubmissionRepository(),
            storage,
            900,
        )
        approvals = PlanApprovalService(factory, projects, governance)
        initialize_request = ProjectInitializeRequest.model_validate_json(
            Path("examples/project-initialize.json").read_text(encoding="utf-8")
        )
        initialized = administration.initialize_project(
            identity=owner,
            idempotency_key=uuid4(),
            request=initialize_request,
        )
        intent = initialized.context_bundle.active_intent
        assert intent is not None
        identity = RequestIdentity(
            user_id=user_id,
            team_id=team_id,
            token_id=uuid4(),
            project_id=initialized.project_id,
            scopes=frozenset(
                {
                    "experiment:check",
                    "manifest:create",
                    "submission:create",
                    "submission:finalize",
                }
            ),
        )

        content = "dataset:\n  protocol: 40/20\nmodel:\n  backbone: shift-gcn\n  fusion: 0.3\n"
        request = _command(initialized.project_id, intent.intent_id, content)
        first = guardian.experiment_check_plan(request, identity)
        replay = guardian.experiment_check_plan(request, identity)
        assert replay.plan_check_id == first.plan_check_id

        changed = request.model_copy(deep=True)
        changed.configuration.content = content.replace("0.3", "0.4")
        with pytest.raises(ConflictError, match="Idempotency-Key"):
            guardian.experiment_check_plan(changed, identity)

        invalid = _command(
            initialized.project_id,
            intent.intent_id,
            content + "  fusion: 0.4\n",
        )
        with pytest.raises(InputValidationError, match="重复字段"):
            guardian.experiment_check_plan(invalid, identity)

        with factory() as session:
            assert session.scalar(select(func.count()).select_from(PlanCheck)) == 1
            persisted = session.get(PlanCheck, first.plan_check_id)
            assert persisted is not None
            assert persisted.context_snapshot["payload"]["protocol"] == "40/20"
            assert persisted.input_document_hash == first.document_sha256

        approval_content = content.replace("backbone: shift-gcn", "backbone: transformer")
        pending = guardian.experiment_check_plan(
            _command(initialized.project_id, intent.intent_id, approval_content), identity
        )
        decision = approvals.decide(
            identity=RequestIdentity(
                user_id=user_id,
                team_id=team_id,
                token_id=uuid4(),
                scopes=frozenset({"plan:approve"}),
            ),
            project_id=initialized.project_id,
            plan_check_id=pending.plan_check_id,
            idempotency_key=uuid4(),
            request=PlanCheckDecisionRequest(decision=ApprovalDecision.APPROVED),
        )
        manifest = guardian.run_manifest_create(
            plan_check_id=pending.plan_check_id,
            identity=identity,
            idempotency_key=uuid4(),
        )
        assert manifest.approval_record_id == decision.approval_record_id

        submission_config = approval_content.encode("utf-8")
        result_payload = json.dumps(
            {
                "schema_version": 1,
                "status": "COMPLETED",
                "metrics": {"top1": 0.83},
                "failure_reason": None,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        storage.payload_by_sha256 = {
            hashlib.sha256(submission_config).hexdigest(): submission_config,
            hashlib.sha256(result_payload).hexdigest(): result_payload,
        }
        submission_request = SubmissionPrepareCommand.model_validate(
            {
                "project_id": initialized.project_id,
                "run_manifest_id": manifest.manifest_id,
                "idempotency_key": uuid4(),
                "source_agent": "cockroach-integration-test/0.1",
                "collected_at": COLLECTED_AT,
                "experiment_status": "COMPLETED",
                "metrics_summary": {"top1": 0.83},
                "files": [
                    {
                        "filename": "config.yaml",
                        "artifact_type": "CONFIG",
                        "mime_type": "application/yaml",
                        "size_bytes": len(submission_config),
                        "sha256": hashlib.sha256(submission_config).hexdigest(),
                    },
                    {
                        "filename": "result.json",
                        "artifact_type": "RESULT",
                        "mime_type": "application/json",
                        "size_bytes": len(result_payload),
                        "sha256": hashlib.sha256(result_payload).hexdigest(),
                    },
                ],
            }
        )
        submission = guardian.submission_prepare(submission_request, identity)
        submission_replay = guardian.submission_prepare(submission_request, identity)
        assert submission_replay.submission_id == submission.submission_id
        assert [item.artifact_id for item in submission_replay.artifact_uploads] == [
            item.artifact_id for item in submission.artifact_uploads
        ]
        assert submission_replay.artifact_uploads[0].upload_url != (
            submission.artifact_uploads[0].upload_url
        )
        storage.accept_declared_uploads()
        finalized = guardian.submission_finalize(
            SubmissionFinalizeCommand(
                submission_id=submission.submission_id,
                idempotency_key=uuid4(),
            ),
            identity,
        )
        assert finalized.verification_result is UploadVerificationResult.PASS
        assert finalized.status is SubmissionStatus.UPLOAD_VERIFIED
        assert finalized.analysis is not None
        assert finalized.analysis.workflow_status is WorkflowStatus.QUEUED
        workflows = SqlAlchemyWorkflowRepository()
        queue_a = DatabaseOutboxQueue(factory, worker_id="cockroach-worker-a")
        queue_b = DatabaseOutboxQueue(factory, worker_id="cockroach-worker-b")
        with factory() as session, session.begin():
            outbox = session.scalar(select(OutboxEvent))
            assert outbox is not None and outbox.status is OutboxStatus.PENDING
            # 使用固定历史时间，避免测试进程与 CockroachDB 节点的亚秒时钟差
            # 影响“已到期”前置条件；生产代码仍使用真实租约时间。
            definitely_available = datetime(2000, 1, 1, tzinfo=UTC)
            outbox.available_at = definitely_available
            workflow_job = session.get(WorkflowJob, outbox.workflow_job_id)
            assert workflow_job is not None
            workflow_job.available_at = definitely_available
        with factory() as session:
            current_time = datetime.now(UTC)
            persisted_outbox = session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.available_at <= current_time,
                    or_(
                        OutboxEvent.status == OutboxStatus.PENDING,
                        (
                            (OutboxEvent.status == OutboxStatus.PUBLISHING)
                            & (OutboxEvent.lease_expires_at <= current_time)
                        ),
                    ),
                )
            )
            assert persisted_outbox is not None
        dispatcher = OutboxDispatcher(
            factory,
            workflows,
            queue_a,
            worker_id="cockroach-dispatcher",
        )
        assert dispatcher.dispatch_once()
        with factory() as session:
            dispatched_outbox = session.scalar(select(OutboxEvent))
            assert dispatched_outbox is not None
            assert dispatched_outbox.status is OutboxStatus.PUBLISHED
            assert dispatched_outbox.lease_owner is None
            dispatched_job = session.get(WorkflowJob, dispatched_outbox.workflow_job_id)
            assert dispatched_job is not None
            assert dispatched_job.status is WorkflowJobStatus.QUEUED
            assert dispatched_job.generation == dispatched_outbox.generation
        claims = []
        for _ in range(10):
            with ThreadPoolExecutor(max_workers=2) as executor:
                claims = list(
                    executor.map(lambda queue: queue.receive(max_messages=1), (queue_a, queue_b))
                )
            if sum(len(items) for items in claims) > 0:
                break
            # SKIP LOCKED 可以在前一事务刚释放锁时暂时返回空；数据库队列按相同方式轮询。
            time.sleep(0.1)
        assert sorted(len(items) for items in claims) == [0, 1]
        prepared_after_finalize = guardian.submission_prepare(submission_request, identity)
        assert prepared_after_finalize.status is SubmissionStatus.UPLOAD_VERIFIED
        assert prepared_after_finalize.artifact_uploads == []
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(ApprovalRecord)) == 1
            assert session.scalar(select(func.count()).select_from(RunManifest)) == 1
            assert session.scalar(select(func.count()).select_from(ExperimentSubmission)) == 1
            assert session.scalar(select(func.count()).select_from(Artifact)) == 2
            persisted_submission = session.get(ExperimentSubmission, submission.submission_id)
            assert persisted_submission is not None
            assert persisted_submission.status is SubmissionStatus.PROCESSING
            assert persisted_submission.workflow_status is WorkflowStatus.QUEUED
            assert persisted_submission.processing_step is WorkflowStep.RISK_ANALYSIS
            assert persisted_submission.upload_verification_snapshot is not None
            persisted_artifacts = session.scalars(select(Artifact)).all()
            assert all(artifact.cloud_hash_verified for artifact in persisted_artifacts)
            assert all(
                artifact.verification_evidence is not None for artifact in persisted_artifacts
            )

        with factory() as session, session.begin():
            session.add(
                SubmissionEmbedding(
                    submission_id=submission.submission_id,
                    project_id=initialized.project_id,
                    embedding=[1.0, *([0.0] * 1023)],
                    model_id="amazon.titan-embed-text-v2:0",
                    dimension=1024,
                    normalized=True,
                    document_version="submission-search-v1",
                    input_text="cockroach vector acceptance",
                    input_sha256="c" * 64,
                    input_token_count=3,
                    generated_at=COLLECTED_AT,
                )
            )
        with factory() as session:
            persisted_embedding = session.scalar(select(SubmissionEmbedding))
            assert persisted_embedding is not None
            assert len(persisted_embedding.embedding) == 1024
            assert persisted_embedding.embedding[0] == 1.0

        with factory() as session, session.begin():
            submission_approval = ApprovalRecord(
                project_id=initialized.project_id,
                target_type=ApprovalTargetType.EXPERIMENT_SUBMISSION,
                target_id=submission.submission_id,
                approval_type="EXPERIMENT_SUBMISSION_REVIEW",
                status=ApprovalDecision.APPROVED,
                requested_by=user_id,
                decided_by=user_id,
                request_reason="cockroach vector acceptance",
                decision_reason=None,
                decided_at=COLLECTED_AT,
            )
            session.add(submission_approval)
            session.flush()
            experiment = Experiment(
                project_id=initialized.project_id,
                intent_id=intent.intent_id,
                run_manifest_id=manifest.manifest_id,
                submission_id=submission.submission_id,
                project_context_id=manifest.context_id,
                project_context_version=manifest.context_version,
                intent_version=manifest.intent_version,
                approval_record_id=submission_approval.id,
                experiment_mode=manifest.experiment_mode,
                eligible_as_baseline=False,
                name="Cockroach query acceptance",
                model_name="shift-gcn",
                dataset=manifest.dataset,
                protocol=manifest.protocol,
                seed=manifest.seed,
                status=ExperimentStatus.COMPLETED,
                config_hash=manifest.config_hash,
                git_commit=manifest.git_commit,
                checkpoint=manifest.checkpoint,
                command=manifest.command,
                summary_snapshot=GeneratedSummary(
                    text="CockroachDB 正式实验向量查询验收。",
                    model_id="test-summary-model",
                    source_hash="d" * 64,
                    generated_at=COLLECTED_AT,
                    disclaimer="测试摘要不代表实验行为已被完整验证。",
                ).model_dump(mode="json"),
                review_receipt_snapshot={},
                confirmed_by=user_id,
                confirmed_at=COLLECTED_AT,
            )
            session.add(experiment)
            session.flush()
            session.add(
                Memory(
                    project_id=initialized.project_id,
                    experiment_id=experiment.id,
                    protocol=manifest.protocol,
                    model_name="shift-gcn",
                    seed=manifest.seed,
                    experiment_status=ExperimentStatus.COMPLETED,
                    current_valid=True,
                    memory_type="EXPERIMENT_REVIEW_V1",
                    content="cockroach vector acceptance",
                    embedding=[1.0, *([0.0] * 1023)],
                    embedding_model_id="amazon.titan-embed-text-v2:0",
                    embedding_dimension=1024,
                    embedding_normalized=True,
                    document_version="submission-search-v1",
                    content_sha256="c" * 64,
                    verification_status=VerificationStatus.CONFIRMED,
                    source_type="SUBMISSION_EMBEDDING",
                    source_id=submission.submission_id,
                )
            )

        query_result = ExperimentQueryService(
            factory,
            projects,
            _QueryEmbeddingGenerator(),
        ).query(
            ExperimentQueryCommand(
                project_id=initialized.project_id,
                query="cockroach vector acceptance",
                protocol=manifest.protocol,
            ),
            RequestIdentity(
                user_id=user_id,
                team_id=team_id,
                token_id=uuid4(),
                project_id=initialized.project_id,
                scopes=frozenset({"experiment:query"}),
            ),
        )
        assert len(query_result) == 1
        assert query_result[0].vector_similarity == pytest.approx(1.0)

        test_engine.dispose()
        test_engine = None
        _run_alembic(rendered_test_url, "downgrade", "20260721_05")
        downgraded_data_engine = create_engine(test_url)
        try:
            with downgraded_data_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text("SELECT status FROM experiment_submissions WHERE id = :submission_id"),
                        {"submission_id": submission.submission_id},
                    )
                    == SubmissionStatus.RECEIVED.value
                )
                verification_flags = connection.scalars(
                    text(
                        "SELECT cloud_hash_verified FROM artifacts "
                        "WHERE submission_id = :submission_id"
                    ),
                    {"submission_id": submission.submission_id},
                ).all()
                assert verification_flags == [False, False]
        finally:
            downgraded_data_engine.dispose()
        _run_alembic(rendered_test_url, "upgrade", "head")
        _run_alembic(rendered_test_url, "downgrade", "base")
        empty_engine = create_engine(test_url)
        try:
            assert set(inspect(empty_engine).get_table_names()) <= {"alembic_version"}
        finally:
            empty_engine.dispose()
    finally:
        if test_engine is not None:
            test_engine.dispose()
        with admin_engine.connect() as connection:
            _cleanup_test_database_jobs(connection, database_name)
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}" CASCADE')
        admin_engine.dispose()
