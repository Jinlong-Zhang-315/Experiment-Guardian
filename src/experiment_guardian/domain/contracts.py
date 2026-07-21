"""领域输入输出契约。

这些 Pydantic 模型描述业务含义，不绑定 FastAPI、MCP 或 SQLAlchemy。接口层只负责把
外部输入转换成这里的对象，核心规则因此可以独立测试，也便于未来复用于异步工作流。
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from experiment_guardian.domain.enums import (
    ApprovalStatus,
    CheckResult,
    ConfigFormat,
    ConstraintSource,
    EvidenceApplicability,
    EvidenceType,
    ExperimentMode,
    ExperimentStatus,
    IntentStatus,
    ProtectionLevel,
    RiskSeverity,
    VerificationStatus,
)


class ContractModel(BaseModel):
    """所有外部契约的共同基类：拒绝未声明字段，尽早暴露客户端拼写错误。"""

    model_config = ConfigDict(extra="forbid")


class ConfigurationDocument(ContractModel):
    """待检查的原始配置文件。云端会自行解析并重新计算哈希。"""

    format: ConfigFormat
    content: str = Field(min_length=1)


class FieldEvidence(ContractModel):
    """一个关键字段的值及其证据元数据。

    ``evidence_type`` 只说明验证边界；``source`` 和 ``collection_tool`` 说明由谁、使用什么
    工具采集。风险报告必须原样保留这些信息，不能把 LOCAL_ATTESTED 改写为云端事实。
    """

    value: Any = None
    evidence_type: EvidenceType
    source: str = Field(min_length=1)
    collected_at: datetime
    collection_tool: str = Field(min_length=1)
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


class SubmissionReceipt(ContractModel):
    """面向人工审核的短回执，低风险详情由界面默认折叠。"""

    submission_id: UUID
    objective: str
    allowed_changes: list[ParameterChange]
    key_results: dict[str, Any]
    highest_risk: RiskSeverity
    highlighted_risks: list[RiskItem]
    collapsed_low_risk_count: int = Field(ge=0)
    can_confirm: bool
    requires_owner: bool
    disclaimer: str = "该回执提高风险可见性，不代表实验行为或结果已被完整验证。"

    @model_validator(mode="after")
    def critical_risk_cannot_be_confirmed(self) -> "SubmissionReceipt":
        if self.highest_risk is RiskSeverity.CRITICAL and self.can_confirm:
            raise ValueError("CRITICAL 风险不能通过普通确认绕过")
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
    actor_id: UUID
    query: str = Field(min_length=1)
    protocol: str = Field(min_length=1)
    model_name: str | None = None
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
        return self


class ExperimentQueryResult(ContractModel):
    """查询结果必须显示结构化状态；向量相似度只表示候选证据。"""

    experiment_id: UUID
    status: ExperimentStatus
    protocol: str
    model_name: str
    seed: int
    current_valid: bool
    verification_status: VerificationStatus
    manifest_id: UUID
    context_id: UUID
    context_version: int = Field(gt=0)
    intent_id: UUID
    intent_version: int = Field(gt=0)
    retrieval_role: Literal["CANDIDATE_EVIDENCE"] = "CANDIDATE_EVIDENCE"
    vector_similarity: float | None = None
    payload: dict[str, Any]

    @model_validator(mode="after")
    def label_historical_and_unconfirmed_results(self) -> "ExperimentQueryResult":
        if self.verification_status is not VerificationStatus.CONFIRMED:
            raise ValueError("正式实验查询不得返回未确认记忆")
        if (
            self.status in {ExperimentStatus.DEPRECATED, ExperimentStatus.SUPERSEDED}
            and self.current_valid
        ):
            raise ValueError("DEPRECATED/SUPERSEDED 结果不能标记为当前有效")
        return self
