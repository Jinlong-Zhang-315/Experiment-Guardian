"""应用层端口定义。

端口描述“业务需要什么”，不描述“CockroachDB/S3/LangGraph 如何完成”。首版先固定边界，
后续实现适配器时不会让 MCP 工具或 FastAPI 路由直接依赖某个云厂商 SDK。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.domain.contracts import (
    ExperimentCheckPlanCommand,
    ExperimentCheckPlanResult,
    ExperimentQueryCommand,
    ExperimentQueryResult,
    PresignedDownload,
    PresignedUpload,
    ProjectContextBundle,
    RunManifestResult,
    StoredObjectMetadata,
    SubmissionFinalizeCommand,
    SubmissionFinalizeResult,
    SubmissionPrepareCommand,
    SubmissionPrepareResult,
    SubmissionStatusResult,
    WorkflowQueueEnvelope,
)


class GuardianUseCases(Protocol):
    """REST 与 MCP 共同依赖的七个 P0 用例。"""

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
        self, command: SubmissionFinalizeCommand, identity: RequestIdentity
    ) -> SubmissionFinalizeResult: ...

    def submission_get_status(
        self, *, submission_id: UUID, identity: RequestIdentity
    ) -> SubmissionStatusResult: ...

    def experiments_query(
        self, command: ExperimentQueryCommand, identity: RequestIdentity
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

    def inspect_object(self, *, object_key: str) -> StoredObjectMetadata | None: ...

    def read_object_version(
        self, *, object_key: str, version_id: str, max_bytes: int
    ) -> bytes | None: ...

    def create_download_url(
        self,
        *,
        object_key: str,
        version_id: str,
        filename: str,
        expires_in: int,
    ) -> PresignedDownload: ...


@dataclass(frozen=True, slots=True)
class OidcIdentity:
    """由托管身份提供商验证后的最小身份声明；Token 不进入应用数据库。"""

    subject: str
    email: str
    email_verified: bool
    authenticated_at: datetime


class OidcProvider(Protocol):
    """Cognito OIDC 适配端口，便于在不访问 AWS 的测试中替换。"""

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        redirect_uri: str,
        prompt: str | None = None,
    ) -> str: ...

    def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        expected_nonce: str,
    ) -> OidcIdentity: ...

    def logout_url(self, *, redirect_uri: str) -> str: ...


class SubmissionWorkflow(Protocol):
    """提交分析工作流调度端口，LangGraph 是计划中的首个实现。"""

    def start(self, submission_id: UUID) -> None: ...

    def resume(self, submission_id: UUID) -> None: ...


@dataclass(frozen=True, slots=True)
class QueueDelivery:
    """队列接收回执；receipt_handle 只能用于本次投递。"""

    message_id: str
    receipt_handle: str
    body: str
    receive_count: int


@dataclass(frozen=True, slots=True)
class SummaryModelOutput:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class EmbeddingModelOutput:
    vector: list[float]
    input_tokens: int | None = None


class SubmissionQueue(Protocol):
    def send(self, envelope: WorkflowQueueEnvelope) -> str: ...

    def receive(self, *, max_messages: int = 1) -> Sequence[QueueDelivery]: ...

    def delete(self, receipt_handle: str) -> None: ...

    def change_visibility(self, receipt_handle: str, timeout_seconds: int) -> None: ...


class SummaryTextGenerator(Protocol):
    """只生成纯文本；风险等级和权限仍由确定性代码决定。"""

    @property
    def model_id(self) -> str: ...

    def generate(self, *, system_prompt: str, user_prompt: str) -> SummaryModelOutput: ...


class EmbeddingGenerator(Protocol):
    """只负责将冻结的检索文档转换成固定维度向量。"""

    @property
    def model_id(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed(self, input_text: str) -> EmbeddingModelOutput: ...
