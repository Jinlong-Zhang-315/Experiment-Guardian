"""领域输入输出契约。

这些 Pydantic 模型描述业务含义，不绑定 FastAPI、MCP 或 SQLAlchemy。接口层只负责把
外部输入转换成这里的对象，核心规则因此可以独立测试，也便于未来复用于异步工作流。
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from experiment_guardian.domain.enums import (
    ApprovalStatus,
    CheckResult,
    ConfigFormat,
    EvidenceType,
    ProtectionLevel,
    RiskSeverity,
)


class ContractModel(BaseModel):
    """所有外部契约的共同基类：拒绝未声明字段，尽早暴露客户端拼写错误。"""

    model_config = ConfigDict(extra="forbid")


class ConfigurationDocument(ContractModel):
    """待检查的原始配置文件。云端会自行解析并重新计算哈希。"""

    format: ConfigFormat
    content: str = Field(min_length=1)


class LocalEnvironment(ContractModel):
    python: str | None = None
    cuda: str | None = None
    pytorch: str | None = None


class LocalAttestation(ContractModel):
    """本地 Agent 的声明。

    这里的数据会被持久化，但风险报告必须标记为 LOCAL_ATTESTED，不能声称云端已经
    验证工作区、checkpoint 或输出目录的真实状态。
    """

    working_tree_clean: bool | None = None
    output_directory_exists: bool | None = None
    checkpoint_exists: bool | None = None
    config_sha256: str | None = None
    git_diff_sha256: str | None = None
    environment: LocalEnvironment = Field(default_factory=LocalEnvironment)


class ParameterConstraint(ContractModel):
    """一个规范化参数路径上的正式约束。

    P0 使用 ``a.b.c`` 形式访问 YAML/JSON 对象。数组和通配符路径暂不支持，避免首版
    约束引擎演变成通用查询语言。
    """

    parameter_path: str = Field(min_length=1)
    protection_level: ProtectionLevel
    expected_value: Any = None
    allowed_values: list[Any] | None = None
    minimum: float | None = None
    maximum: float | None = None
    reason: str = ""


class FieldEvidence(ContractModel):
    value: Any
    evidence_type: EvidenceType
    source: str
    collected_at: datetime


class ParameterChange(ContractModel):
    parameter_path: str
    previous_value: Any = None
    current_value: Any = None
    protection_level: ProtectionLevel | None = None


class RiskItem(ContractModel):
    code: str
    severity: RiskSeverity
    message: str
    field_path: str | None = None
    blocking: bool = False
    evidence_type: EvidenceType = EvidenceType.CLOUD_VERIFIED
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


class PlanEvaluationResult(ContractModel):
    check_result: CheckResult
    approval_status: ApprovalStatus
    config_hash: str
    parsed_config: dict[str, Any]
    changes: list[ParameterChange]
    risks: list[RiskItem]


class ExperimentCheckPlanCommand(ContractModel):
    """MCP/REST 层提交给应用服务的训练前检查命令。"""

    project_id: UUID
    experiment_intent_id: UUID
    requester_id: UUID
    idempotency_key: UUID
    configuration: ConfigurationDocument
    command: str = Field(min_length=1)
    git_commit: str = Field(min_length=7, max_length=64)
    local_attestation: LocalAttestation
