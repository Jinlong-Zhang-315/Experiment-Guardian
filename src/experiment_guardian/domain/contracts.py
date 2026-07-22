"""领域输入输出契约。

这些 Pydantic 模型描述业务含义，不绑定 FastAPI、MCP 或 SQLAlchemy。接口层只负责把
外部输入转换成这里的对象，核心规则因此可以独立测试，也便于未来复用于异步工作流。
"""

import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from experiment_guardian.domain.enums import (
    ApprovalStatus,
    ArtifactType,
    ArtifactVerificationIssueCode,
    CheckResult,
    ConfigFormat,
    ConstraintSource,
    EvidenceApplicability,
    EvidenceType,
    ExperimentMode,
    ExperimentStatus,
    IntentStatus,
    ProtectionLevel,
    ReviewEligibility,
    RiskSeverity,
    SubmissionStatus,
    SubmittedRunStatus,
    UploadVerificationResult,
    VerificationStatus,
    WorkflowJobStatus,
    WorkflowJobType,
    WorkflowStatus,
    WorkflowStep,
)

MAX_CONFIGURATION_BYTES = 1024 * 1024
MAX_RUN_COMMAND_LENGTH = 8192
MAX_ARTIFACT_BYTES = 20 * 1024 * 1024
MAX_SUBMISSION_BYTES = 100 * 1024 * 1024
SHA256_PATTERN = r"^[0-9a-fA-F]{64}$"
GIT_COMMIT_PATTERN = r"^[0-9a-fA-F]{7,64}$"


class ContractModel(BaseModel):
    """所有外部契约的共同基类：拒绝未声明字段，尽早暴露客户端拼写错误。"""

    model_config = ConfigDict(extra="forbid")


class ConfigurationDocument(ContractModel):
    """待检查的原始配置文件。云端会自行解析并重新计算哈希。"""

    format: ConfigFormat
    content: str = Field(min_length=1, max_length=MAX_CONFIGURATION_BYTES)

    @model_validator(mode="after")
    def limit_encoded_size(self) -> "ConfigurationDocument":
        if len(self.content.encode("utf-8")) > MAX_CONFIGURATION_BYTES:
            raise ValueError("配置文档的 UTF-8 字节数不能超过 1 MiB")
        return self


class FieldEvidence(ContractModel):
    """一个关键字段的值及其证据元数据。

    ``evidence_type`` 只说明验证边界；``source`` 和 ``collection_tool`` 说明由谁、使用什么
    工具采集。风险报告必须原样保留这些信息，不能把 LOCAL_ATTESTED 改写为云端事实。
    """

    value: Any = None
    evidence_type: EvidenceType
    source: str = Field(min_length=1, max_length=500)
    collected_at: datetime
    collection_tool: str = Field(min_length=1, max_length=200)
    applicability: EvidenceApplicability = EvidenceApplicability.APPLICABLE
    not_applicable_reason: str | None = None

    @model_validator(mode="after")
    def require_not_applicable_reason(self) -> "FieldEvidence":
        """显式区分“不适用”和“未采集”，并为审计保存判断依据。"""

        if self.applicability is EvidenceApplicability.NOT_APPLICABLE:
            if not self.not_applicable_reason:
                raise ValueError("NOT_APPLICABLE 证据必须说明不适用原因")
            if self.value is not None:
                raise ValueError("NOT_APPLICABLE 证据不能同时携带实际值")
        if (
            self.applicability is EvidenceApplicability.APPLICABLE
            and self.not_applicable_reason is not None
        ):
            raise ValueError("APPLICABLE 证据不能填写不适用原因")
        if self.applicability is EvidenceApplicability.APPLICABLE and self.value is None:
            raise ValueError("APPLICABLE 证据必须携带实际值")
        return self


class LocalEnvironment(ContractModel):
    python: FieldEvidence | None = None
    cuda: FieldEvidence | None = None
    pytorch: FieldEvidence | None = None


class LocalAttestation(ContractModel):
    """本地 Agent 的声明。

    这里的数据会被持久化，但风险报告必须标记为 LOCAL_ATTESTED，不能声称云端已经
    验证工作区、checkpoint 或输出目录的真实状态。
    """

    working_tree_clean: FieldEvidence | None = None
    git_branch: FieldEvidence | None = None
    git_commit: FieldEvidence | None = None
    run_command: FieldEvidence | None = None
    output_directory_exists: FieldEvidence | None = None
    checkpoint_exists: FieldEvidence | None = None
    checkpoint_path: FieldEvidence | None = None
    config_sha256: FieldEvidence | None = None
    git_diff_sha256: FieldEvidence | None = None
    environment: LocalEnvironment = Field(default_factory=LocalEnvironment)

    @model_validator(mode="after")
    def require_local_evidence_type(self) -> "LocalAttestation":
        """校验证据边界，并禁止核心证据通过 NOT_APPLICABLE 绕过。"""

        fields = {
            "working_tree_clean": self.working_tree_clean,
            "git_branch": self.git_branch,
            "git_commit": self.git_commit,
            "run_command": self.run_command,
            "output_directory_exists": self.output_directory_exists,
            "checkpoint_exists": self.checkpoint_exists,
            "checkpoint_path": self.checkpoint_path,
            "config_sha256": self.config_sha256,
            "git_diff_sha256": self.git_diff_sha256,
            "environment.python": self.environment.python,
            "environment.cuda": self.environment.cuda,
            "environment.pytorch": self.environment.pytorch,
        }
        if any(
            item is not None and item.evidence_type is not EvidenceType.LOCAL_ATTESTED
            for item in fields.values()
        ):
            raise ValueError("LocalAttestation 中的证据类型必须为 LOCAL_ATTESTED")

        core_paths = {
            "working_tree_clean",
            "git_branch",
            "git_commit",
            "run_command",
            "output_directory_exists",
            "config_sha256",
            "environment.python",
        }
        invalid_core_paths: list[str] = []
        for path in sorted(core_paths):
            item = fields[path]
            if item is None or item.applicability is EvidenceApplicability.NOT_APPLICABLE:
                invalid_core_paths.append(path)
        if invalid_core_paths:
            raise ValueError(
                "核心本地证据必须提供且不能标记为 NOT_APPLICABLE: " + ", ".join(invalid_core_paths)
            )

        allowed_not_applicable_paths = {
            "checkpoint_exists",
            "checkpoint_path",
            "environment.cuda",
            "environment.pytorch",
        }
        forbidden_not_applicable_paths = [
            path
            for path, item in fields.items()
            if item is not None
            and item.applicability is EvidenceApplicability.NOT_APPLICABLE
            and path not in allowed_not_applicable_paths
        ]
        if forbidden_not_applicable_paths:
            raise ValueError(
                "以下本地证据不允许标记为 NOT_APPLICABLE: "
                + ", ".join(sorted(forbidden_not_applicable_paths))
            )

        checkpoint_exists = self.checkpoint_exists
        checkpoint_path = self.checkpoint_path
        if (
            checkpoint_exists is not None
            and checkpoint_path is not None
            and checkpoint_exists.applicability is not checkpoint_path.applicability
        ):
            raise ValueError("checkpoint_exists 与 checkpoint_path 必须使用相同的适用状态")

        applicable_values = {
            path: item.value
            for path, item in fields.items()
            if item is not None and item.applicability is EvidenceApplicability.APPLICABLE
        }
        boolean_paths = {
            "working_tree_clean",
            "output_directory_exists",
            "checkpoint_exists",
        }
        invalid_booleans = [
            path
            for path in sorted(boolean_paths)
            if path in applicable_values and type(applicable_values[path]) is not bool
        ]
        if invalid_booleans:
            raise ValueError("以下本地证据必须是布尔值: " + ", ".join(invalid_booleans))

        string_limits = {
            "git_branch": 500,
            "git_commit": 64,
            "run_command": MAX_RUN_COMMAND_LENGTH,
            "checkpoint_path": 1500,
            "environment.python": 200,
            "environment.cuda": 200,
            "environment.pytorch": 200,
        }
        invalid_strings = [
            path
            for path, limit in string_limits.items()
            if path in applicable_values
            and (
                not isinstance(applicable_values[path], str)
                or not applicable_values[path].strip()
                or len(applicable_values[path]) > limit
            )
        ]
        if invalid_strings:
            raise ValueError(
                "以下本地证据必须是非空且长度合法的字符串: " + ", ".join(invalid_strings)
            )

        hash_paths = {"config_sha256", "git_diff_sha256"}
        invalid_hashes = [
            path
            for path in sorted(hash_paths)
            if path in applicable_values
            and (
                not isinstance(applicable_values[path], str)
                or re.fullmatch(SHA256_PATTERN, applicable_values[path]) is None
            )
        ]
        if invalid_hashes:
            raise ValueError(
                "以下本地证据必须是 64 位十六进制 SHA-256: " + ", ".join(invalid_hashes)
            )

        git_commit = applicable_values.get("git_commit")
        if isinstance(git_commit, str) and re.fullmatch(GIT_COMMIT_PATTERN, git_commit) is None:
            raise ValueError("git_commit 本地证据必须是 7 到 64 位十六进制值")
        return self


class ParameterConstraint(ContractModel):
    """一个规范化参数路径上的候选或正式约束。

    P0 使用 ``a.b.c`` 形式访问 YAML/JSON 对象，键名中的点和反斜杠使用反斜杠转义。
    数组和通配符路径暂不支持，避免首版约束引擎演变成通用查询语言。只有
    ``verification_status=CONFIRMED`` 的约束能够产生强制 BLOCKED；模型推断或尚待用户
    确认的约束只能产生提醒或 NEEDS_APPROVAL。
    """

    parameter_path: str = Field(min_length=1)
    context_id: UUID | None = None
    context_version: int | None = Field(default=None, gt=0)
    intent_id: UUID | None = None
    intent_version: int | None = Field(default=None, gt=0)
    protection_level: ProtectionLevel
    expected_value: Any = None
    allowed_values: list[Any] | None = None
    minimum: float | None = None
    maximum: float | None = None
    reason: str = ""
    source_type: ConstraintSource = ConstraintSource.EXPLICIT
    verification_status: VerificationStatus = VerificationStatus.PENDING
    original_message: str = Field(min_length=1)
    inference_basis: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    confirmed_by: UUID | None = None
    confirmed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_provenance(self) -> "ParameterConstraint":
        if self.source_type is ConstraintSource.INFERRED and (
            not self.inference_basis or self.confidence is None
        ):
            raise ValueError("INFERRED 约束必须保存推断依据和置信度")
        if self.verification_status is VerificationStatus.CONFIRMED and (
            self.confirmed_by is None or self.confirmed_at is None
        ):
            raise ValueError("CONFIRMED 约束必须保存确认人和确认时间")
        return self


class ParameterChange(ContractModel):
    parameter_path: str
    previous_value: Any = None
    current_value: Any = None
    protection_level: ProtectionLevel | None = None
    constraint_source: ConstraintSource | None = None
    constraint_status: VerificationStatus | None = None
    inference_basis: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    decision: Literal["ALLOWED", "OWNER_APPROVED"] | None = None
    impact: str | None = Field(default=None, max_length=2000)
    evidence_type: EvidenceType | None = None
    evidence_source: str | None = Field(default=None, max_length=500)


class RiskItem(ContractModel):
    code: str
    severity: RiskSeverity
    message: str
    field_path: str | None = None
    previous_value: Any = None
    current_value: Any = None
    expected_value: Any = None
    impact: str | None = None
    blocking: bool = False
    resolved: bool = False
    evidence_type: EvidenceType | None = EvidenceType.CLOUD_VERIFIED
    evidence_source: str | None = None
    collected_at: datetime | None = None
    collection_tool: str | None = None
    constraint_source: ConstraintSource | None = None
    constraint_status: VerificationStatus | None = None
    inference_basis: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    constraint_candidates: list[dict[str, Any]] = Field(default_factory=list)
    recommendation: str | None = None


class PlanEvaluationInput(ContractModel):
    """纯规则引擎输入。

    正式服务会根据 project_id 和 intent_id 从数据库加载 baseline、允许变量与约束；
    这里显式传入它们，是为了让规则引擎不依赖数据库并能进行快速单元测试。
    """

    baseline_config: dict[str, Any]
    candidate: ConfigurationDocument
    constraints: list[ParameterConstraint]
    allowed_variable_paths: set[str] = Field(default_factory=set)
    local_attestation: LocalAttestation | None = None
    experiment_mode: ExperimentMode = ExperimentMode.FORMAL
    git_commit: str | None = None
    run_command: str | None = None
    checkpoint: str | None = None


class PlanEvaluationResult(ContractModel):
    check_result: CheckResult
    approval_status: ApprovalStatus
    config_hash: str
    document_sha256: str
    parsed_config: dict[str, Any]
    changes: list[ParameterChange]
    risks: list[RiskItem]


class ExperimentCheckPlanCommand(ContractModel):
    """MCP/REST 层提交给应用服务的训练前检查命令。"""

    project_id: UUID
    experiment_intent_id: UUID
    idempotency_key: UUID
    configuration: ConfigurationDocument
    command: str = Field(min_length=1, max_length=MAX_RUN_COMMAND_LENGTH)
    git_commit: str = Field(pattern=GIT_COMMIT_PATTERN)
    local_attestation: LocalAttestation


class ExperimentCheckPlanResult(PlanEvaluationResult):
    """已经持久化、可以按 Idempotency-Key 稳定重放的训练前检查结果。"""

    plan_check_id: UUID
    project_id: UUID
    context_id: UUID
    context_version: int = Field(gt=0)
    experiment_intent_id: UUID
    intent_version: int = Field(gt=0)
    experiment_mode: ExperimentMode
    risk_level: RiskSeverity
    missing_information: list[str] = Field(default_factory=list)
    can_create_manifest: bool

    @model_validator(mode="after")
    def validate_manifest_eligibility(self) -> "ExperimentCheckPlanResult":
        eligible = (
            self.check_result is CheckResult.PASS
            and self.approval_status is ApprovalStatus.NOT_REQUIRED
        ) or (
            self.check_result is CheckResult.NEEDS_APPROVAL
            and self.approval_status is ApprovalStatus.APPROVED
        )
        if self.can_create_manifest is not eligible:
            raise ValueError("can_create_manifest 与检查和审批状态不一致")
        return self


class RunManifestResult(ContractModel):
    """由历史 Plan Check 快照生成的不可变运行凭据。"""

    schema_version: Literal[1] = 1
    manifest_id: UUID
    project_id: UUID
    plan_check_id: UUID
    approval_record_id: UUID | None = None
    context_id: UUID
    context_version: int = Field(gt=0)
    experiment_intent_id: UUID
    intent_version: int = Field(gt=0)
    experiment_mode: ExperimentMode
    config_snapshot: dict[str, Any]
    config_hash: str = Field(pattern=SHA256_PATTERN)
    config_document_hash: str = Field(pattern=SHA256_PATTERN)
    git_branch: str = Field(min_length=1, max_length=500)
    git_commit: str = Field(pattern=GIT_COMMIT_PATTERN)
    git_diff_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    dataset: str = Field(min_length=1, max_length=200)
    protocol: str = Field(min_length=1, max_length=200)
    seed: int = Field(strict=True)
    checkpoint: str | None = Field(default=None, max_length=1500)
    command: str = Field(min_length=1, max_length=MAX_RUN_COMMAND_LENGTH)
    environment: dict[str, Any]
    evidence_snapshot: dict[str, Any]
    manifest_hash: str = Field(pattern=SHA256_PATTERN)
    created_by: UUID
    created_at: datetime

    @model_validator(mode="after")
    def reject_boolean_seed(self) -> "RunManifestResult":
        if type(self.seed) is not int:
            raise ValueError("Manifest seed 必须是整数且不能是布尔值")
        return self


class SubmissionArtifactInput(ContractModel):
    """本地 Agent 对一个待上传文件的声明，内容将在 finalize 阶段由云端复核。"""

    filename: str = Field(min_length=1, max_length=255)
    artifact_type: ArtifactType
    mime_type: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(strict=True, ge=1, le=MAX_ARTIFACT_BYTES)
    sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="before")
    @classmethod
    def normalize_sha256(cls, value: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("sha256"), str):
            return {**value, "sha256": value["sha256"].lower()}
        return value

    @model_validator(mode="after")
    def validate_filename_and_media_type(self) -> "SubmissionArtifactInput":
        if (
            self.artifact_type in {ArtifactType.CONFIG, ArtifactType.RESULT}
            and self.size_bytes > MAX_CONFIGURATION_BYTES
        ):
            raise ValueError("CONFIG 和 RESULT 文件分别不能超过 1 MiB")
        if (
            self.filename in {".", ".."}
            or "/" in self.filename
            or "\\" in self.filename
            or any(ord(char) < 32 or ord(char) == 127 for char in self.filename)
        ):
            raise ValueError("filename 必须是普通文件名，不能包含路径或控制字符")

        suffix = Path(self.filename).suffix.lower()
        allowed: dict[ArtifactType, dict[str, set[str]]] = {
            ArtifactType.CONFIG: {
                ".yaml": {"application/yaml", "application/x-yaml", "text/yaml"},
                ".yml": {"application/yaml", "application/x-yaml", "text/yaml"},
                ".json": {"application/json"},
            },
            ArtifactType.RESULT: {".json": {"application/json"}},
            ArtifactType.LOG: {".txt": {"text/plain"}},
            ArtifactType.NOTE: {".md": {"text/markdown"}},
            ArtifactType.MANIFEST: {".json": {"application/json"}},
        }
        if suffix not in allowed[self.artifact_type]:
            raise ValueError(f"{self.artifact_type.value} 不允许使用扩展名 {suffix or '<none>'}")
        if self.mime_type not in allowed[self.artifact_type][suffix]:
            raise ValueError("mime_type 与 artifact_type/文件扩展名不一致")
        return self


class SubmissionPrepareCommand(ContractModel):
    project_id: UUID
    run_manifest_id: UUID
    idempotency_key: UUID
    source_agent: str = Field(min_length=1, max_length=300)
    collected_at: datetime
    experiment_status: SubmittedRunStatus
    metrics_summary: dict[str, float] = Field(default_factory=dict)
    files: list[SubmissionArtifactInput] = Field(min_length=2, max_length=10)

    @model_validator(mode="before")
    @classmethod
    def validate_metric_types(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        metrics = value.get("metrics_summary")
        if not isinstance(metrics, dict):
            return value
        normalized: dict[str, float] = {}
        if len(metrics) > 50:
            raise ValueError("metrics_summary 最多包含 50 个指标")
        for name, metric in metrics.items():
            if not isinstance(name, str) or not name.strip() or len(name) > 100:
                raise ValueError("指标名称必须是 1 到 100 字符的非空字符串")
            if type(metric) not in {int, float} or not math.isfinite(metric):
                raise ValueError(f"指标 {name} 必须是有限数值且不能是布尔值")
            normalized[name] = float(metric)
        return {**value, "metrics_summary": normalized}

    @model_validator(mode="after")
    def validate_submission_declaration(self) -> "SubmissionPrepareCommand":
        if self.collected_at.tzinfo is None or self.collected_at.utcoffset() is None:
            raise ValueError("collected_at 必须包含时区")
        if self.experiment_status is SubmittedRunStatus.COMPLETED and not self.metrics_summary:
            raise ValueError("COMPLETED 实验必须提供至少一个指标摘要")

        filenames = [item.filename.casefold() for item in self.files]
        if len(filenames) != len(set(filenames)):
            raise ValueError("同一 Submission 中的 filename 不能重复")
        if sum(item.size_bytes for item in self.files) > MAX_SUBMISSION_BYTES:
            raise ValueError("单次 Submission 文件总大小不能超过 100 MiB")

        counts = {
            artifact_type: sum(item.artifact_type is artifact_type for item in self.files)
            for artifact_type in ArtifactType
        }
        if counts[ArtifactType.CONFIG] != 1 or counts[ArtifactType.RESULT] != 1:
            raise ValueError("Submission 必须恰好包含一个 CONFIG 和一个 RESULT")
        if counts[ArtifactType.NOTE] > 1 or counts[ArtifactType.MANIFEST] > 1:
            raise ValueError("NOTE 和 MANIFEST 最多各包含一个")
        return self


class PresignedUpload(ContractModel):
    upload_url: str = Field(min_length=1)
    required_headers: dict[str, str]


class PresignedDownload(ContractModel):
    """短期下载 URL；只指向云端已经固定并校验过的 S3 VersionId。"""

    download_url: str = Field(min_length=1)
    expires_at: datetime


class ArtifactUploadTarget(ContractModel):
    artifact_id: UUID
    filename: str
    artifact_type: ArtifactType
    mime_type: str
    size_bytes: int
    sha256: str = Field(pattern=SHA256_PATTERN)
    upload_url: str = Field(min_length=1)
    required_headers: dict[str, str]
    expires_at: datetime


class SubmissionPrepareResult(ContractModel):
    submission_id: UUID
    project_id: UUID
    run_manifest_id: UUID
    manifest_hash: str = Field(pattern=SHA256_PATTERN)
    status: Literal[SubmissionStatus.RECEIVED, SubmissionStatus.UPLOAD_VERIFIED] = (
        SubmissionStatus.RECEIVED
    )
    experiment_status: SubmittedRunStatus
    metrics_summary: dict[str, float]
    required_files_check: Literal["PASS"] = "PASS"
    artifact_uploads: list[ArtifactUploadTarget]
    created_at: datetime


class StoredObjectMetadata(ContractModel):
    """Artifact Storage 通过云服务元数据接口观测到的对象状态。"""

    content_length: int = Field(strict=True, ge=0)
    content_type: str | None = None
    checksum_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    checksum_type: str | None = None
    etag: str | None = None
    version_id: str | None = Field(default=None, max_length=1024)
    last_modified: datetime | None = None
    observed_at: datetime
    evidence_source: str = Field(min_length=1, max_length=2000)


class ArtifactVerificationIssue(ContractModel):
    artifact_id: UUID
    filename: str
    code: ArtifactVerificationIssueCode
    field: str
    expected: Any = None
    actual: Any = None
    message: str = Field(min_length=1)
    evidence_source: str = Field(min_length=1, max_length=2000)
    observed_at: datetime
    collection_tool: Literal["boto3.s3.head_object"] = "boto3.s3.head_object"


class ArtifactVerificationReceipt(ContractModel):
    artifact_id: UUID
    filename: str
    artifact_type: ArtifactType
    content_length: int
    content_type: str
    checksum_sha256: str = Field(pattern=SHA256_PATTERN)
    etag: str | None = None
    version_id: str = Field(min_length=1, max_length=1024)
    last_modified: datetime | None = None
    verified_at: datetime
    evidence_type: Literal[EvidenceType.CLOUD_VERIFIED] = EvidenceType.CLOUD_VERIFIED
    evidence_source: str = Field(min_length=1, max_length=2000)
    collection_tool: Literal["boto3.s3.head_object"] = "boto3.s3.head_object"


class SubmissionFinalizeCommand(ContractModel):
    submission_id: UUID
    idempotency_key: UUID


class SubmissionAnalysisReceipt(ContractModel):
    """当前持久化分析状态；幂等重放时从 Submission 动态读取。"""

    submission_status: SubmissionStatus
    workflow_status: WorkflowStatus
    processing_step: WorkflowStep | None = None
    retryable: bool = False
    error: dict[str, Any] | None = None
    duplicate_count: int = Field(default=0, ge=0)
    risk_count: int = Field(default=0, ge=0)
    highest_risk: RiskSeverity | None = None

    @model_validator(mode="after")
    def validate_analysis_state(self) -> "SubmissionAnalysisReceipt":
        if self.workflow_status is WorkflowStatus.RETRYABLE_FAILURE and not self.retryable:
            raise ValueError("RETRYABLE_FAILURE 分析回执必须允许重试")
        if self.workflow_status is WorkflowStatus.TERMINAL_FAILURE and (
            self.submission_status is not SubmissionStatus.FAILED or self.retryable
        ):
            raise ValueError("TERMINAL_FAILURE 必须对应不可重试的 FAILED Submission")
        if self.workflow_status is WorkflowStatus.COMPLETED and (
            self.submission_status is not SubmissionStatus.NEEDS_REVIEW
            or self.processing_step is not WorkflowStep.NEEDS_REVIEW
            or self.retryable
            or self.error is not None
        ):
            raise ValueError("COMPLETED 分析必须对应无错误的 NEEDS_REVIEW 交接状态")
        if (
            self.workflow_status
            in {
                WorkflowStatus.RETRYABLE_FAILURE,
                WorkflowStatus.TERMINAL_FAILURE,
            }
            and self.error is None
        ):
            raise ValueError("失败分析回执必须包含结构化错误")
        return self


class SubmissionFinalizeResult(ContractModel):
    submission_id: UUID
    project_id: UUID
    verification_result: UploadVerificationResult
    status: Literal[SubmissionStatus.RECEIVED, SubmissionStatus.UPLOAD_VERIFIED]
    retryable: bool
    issues: list[ArtifactVerificationIssue] = Field(default_factory=list)
    reupload_artifact_ids: list[UUID] = Field(default_factory=list)
    artifact_verifications: list[ArtifactVerificationReceipt] = Field(default_factory=list)
    verified_at: datetime | None = None
    analysis: SubmissionAnalysisReceipt | None = None

    @model_validator(mode="after")
    def validate_verification_state(self) -> "SubmissionFinalizeResult":
        if self.verification_result is UploadVerificationResult.PASS:
            if (
                self.status is not SubmissionStatus.UPLOAD_VERIFIED
                or self.retryable
                or self.issues
                or self.reupload_artifact_ids
                or not self.artifact_verifications
                or self.verified_at is None
            ):
                raise ValueError("PASS 回执必须表示上传已验证且不可重试")
        elif (
            self.status is not SubmissionStatus.RECEIVED
            or not self.retryable
            or not self.issues
            or set(self.reupload_artifact_ids) != {issue.artifact_id for issue in self.issues}
            or self.artifact_verifications
            or self.verified_at is not None
        ):
            raise ValueError("FAILED 回执必须保持 RECEIVED 并提供可重试问题")
        return self


class SummaryUsage(ContractModel):
    """模型服务返回的可选 token 计数；缺失不影响摘要有效性。"""

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class GeneratedSummary(ContractModel):
    """模型生成的解释性摘要，不属于 CLOUD_VERIFIED 证据。"""

    schema_version: Literal[1] = 1
    text: str = Field(min_length=1, max_length=3000)
    provider: str = Field(default="bedrock", min_length=1, max_length=50)
    model_id: str = Field(min_length=1, max_length=500)
    prompt_version: Literal["submission-summary-v1"] = "submission-summary-v1"
    source_hash: str = Field(pattern=SHA256_PATTERN)
    language_strategy: Literal["AUTO_FROM_INTENT"] = "AUTO_FROM_INTENT"
    generated_at: datetime
    usage: SummaryUsage = Field(default_factory=SummaryUsage)
    disclaimer: str = Field(min_length=1, max_length=1000)


class WorkflowJobReceipt(ContractModel):
    id: UUID
    job_type: WorkflowJobType
    status: WorkflowJobStatus
    generation: int = Field(ge=1)
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    available_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_error: dict[str, Any] | None = None


class WorkflowQueueEnvelope(ContractModel):
    """队列中只保留定位信息，所有业务事实均从数据库重新加载。"""

    schema_version: Literal[1] = 1
    job_id: UUID
    submission_id: UUID
    generation: int = Field(ge=1)


# R12a 客户端和测试仍可使用旧名称；wire shape 没有变化。
SummaryQueueEnvelope = WorkflowQueueEnvelope


class EmbeddingMetadata(ContractModel):
    provider: str = Field(default="bedrock", min_length=1, max_length=50)
    model_id: str = Field(min_length=1, max_length=500)
    dimension: Literal[1024] = 1024
    normalized: Literal[True] = True
    document_version: Literal["submission-search-v1"] = "submission-search-v1"
    input_sha256: str = Field(pattern=SHA256_PATTERN)
    input_token_count: int | None = Field(default=None, ge=0)
    generated_at: datetime


class SubmissionStatusResult(ContractModel):
    """原提交者和 Owner 可见的动态分析状态。"""

    submission_id: UUID
    project_id: UUID
    run_manifest_id: UUID
    submission_status: SubmissionStatus
    workflow_status: WorkflowStatus
    processing_step: WorkflowStep | None = None
    retryable: bool
    processing_error: dict[str, Any] | None = None
    job: WorkflowJobReceipt | None = None
    jobs: list[WorkflowJobReceipt] = Field(default_factory=list)
    risk_count: int = Field(ge=0)
    highest_risk: RiskSeverity | None = None
    generated_summary: GeneratedSummary | None = None
    embedding: EmbeddingMetadata | None = None
    review_receipt: "SubmissionReceipt | None" = None
    updated_at: datetime
    disclaimer: str = Field(min_length=1, max_length=1000)


class SubmittedResultDocument(ContractModel):
    """R11 固定的 ``result.json`` 格式，拒绝隐式类型和额外字段。"""

    schema_version: Literal[1]
    status: SubmittedRunStatus
    metrics: dict[str, float]
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_reason: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="before")
    @classmethod
    def validate_metric_types(cls, value: Any) -> Any:
        if not isinstance(value, dict) or not isinstance(value.get("metrics"), dict):
            return value
        metrics = value["metrics"]
        if len(metrics) > 50:
            raise ValueError("result.metrics 最多包含 50 个指标")
        normalized: dict[str, float] = {}
        for name, metric in metrics.items():
            if not isinstance(name, str) or not name.strip() or len(name) > 100:
                raise ValueError("result.metrics 指标名必须是 1 到 100 字符的非空字符串")
            if type(metric) not in {int, float} or not math.isfinite(metric):
                raise ValueError(f"result.metrics.{name} 必须是有限数值且不能是布尔值")
            normalized[name] = float(metric)
        return {**value, "metrics": normalized}

    @model_validator(mode="after")
    def validate_result_semantics(self) -> "SubmittedResultDocument":
        for field_name, value in (
            ("started_at", self.started_at),
            ("completed_at", self.completed_at),
        ):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{field_name} 必须包含时区")
        if self.started_at and self.completed_at and self.completed_at < self.started_at:
            raise ValueError("completed_at 不能早于 started_at")
        if self.status is SubmittedRunStatus.COMPLETED:
            if not self.metrics:
                raise ValueError("COMPLETED 结果必须包含至少一个指标")
            if self.failure_reason is not None:
                raise ValueError("COMPLETED 结果不能包含 failure_reason")
        elif not self.failure_reason:
            raise ValueError("FAILED 结果必须说明 failure_reason")
        return self


class IntentInterpretation(ContractModel):
    """云端 Agent 对自然语言意图的候选解释，不是正式 Experiment Intent。

    Web 端需要先展示本回执和歧义；确认动作随后创建版本化 Intent 与约束。该对象本身
    永远不能直接被训练前检查当成正式事实。
    """

    original_message: str = Field(min_length=1)
    explicit_constraints: list[ParameterConstraint] = Field(default_factory=list)
    inferred_constraints: list[ParameterConstraint] = Field(default_factory=list)
    unresolved_ambiguities: list[str] = Field(default_factory=list)
    intent_receipt: str = Field(min_length=1)

    @model_validator(mode="after")
    def keep_candidates_unconfirmed(self) -> "IntentInterpretation":
        if any(
            item.source_type is not ConstraintSource.EXPLICIT
            or item.verification_status is not VerificationStatus.PENDING
            for item in self.explicit_constraints
        ):
            raise ValueError("explicit_constraints 只能包含待确认的 EXPLICIT 约束")
        if any(
            item.source_type is not ConstraintSource.INFERRED
            or item.verification_status is not VerificationStatus.PENDING
            for item in self.inferred_constraints
        ):
            raise ValueError("inferred_constraints 只能包含待确认的 INFERRED 约束")
        return self


class ProjectContextReference(ContractModel):
    """MCP 上下文响应中不可省略的正式版本元数据。"""

    context_id: UUID
    version: int = Field(gt=0)
    confirmed_by: UUID
    confirmed_at: datetime
    effective_at: datetime
    change_reason: str = Field(min_length=1)


class ExperimentIntentReference(ContractModel):
    intent_id: UUID
    version: int = Field(gt=0)
    context_id: UUID
    context_version: int = Field(gt=0)
    status: IntentStatus
    mode: ExperimentMode


class ProjectContextPayload(ContractModel):
    project_id: UUID
    project_name: str
    description: str
    repository_url: str | None = None
    goal: str
    non_goals: list[Any]
    mainline_model: str
    baseline: dict[str, Any]
    dataset: str
    protocol: str
    primary_metric: dict[str, Any]
    default_seeds: list[int]
    active_branch: str
    active_config: dict[str, Any]
    deprecated_items: list[Any]
    key_decisions: list[Any]


class ExperimentIntentPayload(ContractModel):
    name: str
    objective: str
    hypothesis: str
    allowed_variables: list[str]
    controlled_variables: list[str]
    expected_outputs: list[str]
    acceptance_criteria: list[str]
    original_message: str
    intent_receipt: str


class ProjectContextBundle(ContractModel):
    """``project_get_context`` 的稳定返回骨架，明确当前生效版本。"""

    context: ProjectContextReference
    active_intent: ExperimentIntentReference | None
    constraints: list[ParameterConstraint]
    context_payload: ProjectContextPayload
    intent_payload: ExperimentIntentPayload | None

    @model_validator(mode="after")
    def expose_only_formal_facts(self) -> "ProjectContextBundle":
        """本地 Agent 读取接口不得混入模型候选或已失效约束。"""

        if self.active_intent is not None and self.active_intent.status is not IntentStatus.ACTIVE:
            raise ValueError("active_intent 必须是 ACTIVE 版本")
        if (self.active_intent is None) != (self.intent_payload is None):
            raise ValueError("active_intent 与 intent_payload 必须同时存在或同时为空")
        if any(
            item.verification_status is not VerificationStatus.CONFIRMED
            for item in self.constraints
        ):
            raise ValueError("project_get_context 只能返回 CONFIRMED 约束")
        if any(
            item.context_id != self.context.context_id
            or item.context_version != self.context.version
            for item in self.constraints
        ):
            raise ValueError("返回约束必须绑定当前生效的 context 版本")
        return self


class ReviewTrace(ContractModel):
    project_id: UUID
    context_id: UUID
    context_version: int = Field(gt=0)
    intent_id: UUID
    intent_version: int = Field(gt=0)
    plan_check_id: UUID
    run_manifest_id: UUID
    manifest_hash: str = Field(pattern=SHA256_PATTERN)


class ReviewFact(ContractModel):
    name: str = Field(min_length=1, max_length=200)
    value: Any
    evidence_type: EvidenceType
    source: str = Field(min_length=1, max_length=500)
    collected_at: datetime
    collection_tool: str = Field(min_length=1, max_length=200)


class SubmissionReceipt(ContractModel):
    """面向人工审核的短回执，低风险详情由界面默认折叠。"""

    schema_version: Literal[1] = 1
    submission_id: UUID
    objective: str
    objective_evidence: ReviewFact
    trace: ReviewTrace
    run_conditions: list[ReviewFact]
    allowed_changes: list[ParameterChange]
    key_results: list[ReviewFact]
    highest_risk: RiskSeverity | None
    highlighted_risks: list[RiskItem]
    collapsed_low_risk_count: int = Field(ge=0)
    collapsed_medium_risk_count: int = Field(ge=0)
    evidence_counts: dict[EvidenceType, int]
    review_eligibility: ReviewEligibility
    can_confirm: bool
    requires_owner: bool
    summary_available: bool
    source_hash: str = Field(pattern=SHA256_PATTERN)
    generated_at: datetime
    disclaimer: str = "该回执提高风险可见性，不代表实验行为或结果已被完整验证。"

    @model_validator(mode="after")
    def critical_risk_cannot_be_confirmed(self) -> "SubmissionReceipt":
        expected_can_confirm = self.review_eligibility is not ReviewEligibility.BLOCKED
        expected_requires_owner = self.review_eligibility is ReviewEligibility.OWNER_ONLY
        if self.can_confirm != expected_can_confirm:
            raise ValueError("can_confirm 必须由 review_eligibility 推导")
        if self.requires_owner != expected_requires_owner:
            raise ValueError("requires_owner 必须由 review_eligibility 推导")
        if self.highest_risk is RiskSeverity.CRITICAL and (
            self.review_eligibility is not ReviewEligibility.BLOCKED
        ):
            raise ValueError("CRITICAL 风险必须阻断确认")
        if any(
            item.severity not in {RiskSeverity.HIGH, RiskSeverity.CRITICAL}
            for item in self.highlighted_risks
        ):
            raise ValueError("highlighted_risks 只用于强制展开 HIGH/CRITICAL 风险")
        if (
            self.highest_risk in {RiskSeverity.HIGH, RiskSeverity.CRITICAL}
            and not self.highlighted_risks
        ):
            raise ValueError("HIGH/CRITICAL 风险必须在回执中强制展开")
        return self


class ExperimentQueryCommand(ContractModel):
    """结构化过滤先于向量相似度的实验查询命令。"""

    project_id: UUID
    experiment_id: UUID | None = None
    query: str | None = Field(default=None, min_length=1, max_length=2000)
    protocol: str | None = Field(default=None, min_length=1, max_length=200)
    model_name: str | None = Field(default=None, min_length=1, max_length=300)
    seed: int | None = None
    statuses: set[ExperimentStatus] = Field(
        default_factory=lambda: {ExperimentStatus.COMPLETED, ExperimentStatus.FAILED}
    )
    verification_status: VerificationStatus = VerificationStatus.CONFIRMED
    include_historical: bool = False
    top_k: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def query_only_confirmed_records(self) -> "ExperimentQueryCommand":
        if self.verification_status is not VerificationStatus.CONFIRMED:
            raise ValueError("experiments_query 只能检索 CONFIRMED 记忆")
        historical = {ExperimentStatus.DEPRECATED, ExperimentStatus.SUPERSEDED}
        if not self.include_historical and self.statuses & historical:
            raise ValueError("DEPRECATED/SUPERSEDED 结果必须显式启用 include_historical")
        if self.experiment_id is None:
            if self.query is None or self.protocol is None:
                raise ValueError("向量候选查询必须同时提供 query 和 protocol")
        elif self.query is not None or self.protocol is not None:
            raise ValueError("experiment_id 详情模式不能同时提供 query 或 protocol")
        return self


class ExperimentMetricView(ContractModel):
    name: str
    value: float
    split: str
    aggregation_type: str
    epoch: int | None = None
    is_primary: bool


class ExperimentArtifactView(ContractModel):
    artifact_id: UUID
    filename: str
    mime_type: str
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=SHA256_PATTERN)
    artifact_type: ArtifactType
    s3_version_id: str
    evidence_type: Literal[EvidenceType.CLOUD_VERIFIED] = EvidenceType.CLOUD_VERIFIED


class ExperimentQueryResult(ContractModel):
    """查询结果必须显示结构化状态；向量相似度只表示候选证据。"""

    experiment_id: UUID
    submission_id: UUID
    name: str
    experiment_mode: ExperimentMode
    status: ExperimentStatus
    dataset: str
    protocol: str
    model_name: str
    seed: int
    current_valid: bool
    verification_status: VerificationStatus
    manifest_id: UUID
    manifest_hash: str = Field(pattern=SHA256_PATTERN)
    plan_check_id: UUID
    context_id: UUID
    context_version: int = Field(gt=0)
    intent_id: UUID
    intent_version: int = Field(gt=0)
    retrieval_role: Literal["CANDIDATE_EVIDENCE", "STRUCTURED_RECORD"]
    detail_level: Literal["SUMMARY", "FULL"]
    vector_similarity: float | None = Field(default=None, ge=-1.0, le=1.0)
    summary: GeneratedSummary
    metrics: list[ExperimentMetricView]
    config_hash: str = Field(pattern=SHA256_PATTERN)
    config_snapshot: dict[str, Any] | None = None
    git_branch: str | None = None
    git_commit: str
    command: str | None = None
    checkpoint: str | None = None
    artifacts: list[ExperimentArtifactView] = Field(default_factory=list)

    @model_validator(mode="after")
    def label_historical_and_unconfirmed_results(self) -> "ExperimentQueryResult":
        if self.verification_status is not VerificationStatus.CONFIRMED:
            raise ValueError("正式实验查询不得返回未确认记忆")
        if (
            self.status in {ExperimentStatus.DEPRECATED, ExperimentStatus.SUPERSEDED}
            and self.current_valid
        ):
            raise ValueError("DEPRECATED/SUPERSEDED 结果不能标记为当前有效")
        if self.detail_level == "FULL":
            if self.config_snapshot is None or self.command is None:
                raise ValueError("FULL 查询结果必须包含配置和运行命令")
            if self.retrieval_role != "STRUCTURED_RECORD" or self.vector_similarity is not None:
                raise ValueError("FULL 详情必须是无向量分数的 STRUCTURED_RECORD")
        elif self.config_snapshot is not None or self.command is not None or self.artifacts:
            raise ValueError("SUMMARY 查询结果不能携带完整配置、命令或 Artifact")
        elif self.retrieval_role != "CANDIDATE_EVIDENCE":
            raise ValueError("SUMMARY 查询结果必须标记为 CANDIDATE_EVIDENCE")
        return self
