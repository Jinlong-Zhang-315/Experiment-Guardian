"""应用层端口定义。

端口描述“业务需要什么”，不描述“CockroachDB/S3/LangGraph 如何完成”。首版先固定边界，
后续实现适配器时不会让 MCP 工具或 FastAPI 路由直接依赖某个云厂商 SDK。
"""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol
from uuid import UUID

from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.domain.contracts import (
    ExperimentCheckPlanCommand,
    ExperimentCheckPlanResult,
    ExperimentQueryCommand,
    ExperimentQueryResult,
    PresignedUpload,
    ProjectContextBundle,
    RunManifestResult,
    SubmissionPrepareCommand,
    SubmissionPrepareResult,
)


class GuardianUseCases(Protocol):
    """REST 与 MCP 共同依赖的六个 P0 用例。"""

    def project_get_context(
        self, *, project_id: UUID, identity: RequestIdentity
    ) -> ProjectContextBundle: ...

    def experiment_check_plan(
        self, command: ExperimentCheckPlanCommand, identity: RequestIdentity
    ) -> ExperimentCheckPlanResult: ...

    def run_manifest_create(
        self,
        *,
        plan_check_id: UUID,
        identity: RequestIdentity,
        idempotency_key: UUID,
    ) -> RunManifestResult: ...

    def submission_prepare(
        self, command: SubmissionPrepareCommand, identity: RequestIdentity
    ) -> SubmissionPrepareResult: ...

    def submission_finalize(
        self,
        *,
        submission_id: UUID,
        actor_id: UUID,
        idempotency_key: UUID,
        uploaded_files: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]: ...

    def experiments_query(
        self, command: ExperimentQueryCommand
    ) -> Sequence[ExperimentQueryResult]: ...


class ArtifactStorage(Protocol):
    """S3 适配器需要实现的最小能力。"""

    def create_upload_url(
        self,
        *,
        object_key: str,
        content_type: str,
        content_length: int,
        sha256: str,
        expires_in: int,
    ) -> PresignedUpload: ...


class SubmissionWorkflow(Protocol):
    """提交分析工作流调度端口，LangGraph 是计划中的首个实现。"""

    def start(self, submission_id: UUID) -> None: ...

    def resume(self, submission_id: UUID) -> None: ...
