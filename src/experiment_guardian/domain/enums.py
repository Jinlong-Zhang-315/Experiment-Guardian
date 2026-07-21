"""领域状态枚举。

所有 API、MCP 工具、数据库模型和工作流都应复用本文件中的枚举。状态只在一处定义，
可以避免接口返回值与数据库状态逐渐发生偏差。
"""

from enum import StrEnum


class TeamRole(StrEnum):
    OWNER = "OWNER"
    RESEARCHER = "RESEARCHER"


class TokenAudience(StrEnum):
    API = "API"
    MCP = "MCP"


class ContextStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


class IntentStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class ProtectionLevel(StrEnum):
    LOCKED = "LOCKED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    EXPERIMENT_VARIABLE = "EXPERIMENT_VARIABLE"


class ConstraintSource(StrEnum):
    """结构化约束来自用户明确表达，还是模型基于上下文推断。"""

    EXPLICIT = "EXPLICIT"
    INFERRED = "INFERRED"


class VerificationStatus(StrEnum):
    """候选事实或约束的人工确认生命周期。"""

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class ExperimentMode(StrEnum):
    """正式复现与独立探索必须在意图创建时明确区分。"""

    FORMAL = "FORMAL"
    EXPLORATORY = "EXPLORATORY"


class CheckResult(StrEnum):
    PASS = "PASS"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    BLOCKED = "BLOCKED"


class ApprovalStatus(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalDecision(StrEnum):
    """管理端可提交的最终决策，不暴露中间审批状态。"""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalTargetType(StrEnum):
    PLAN_CHECK = "PLAN_CHECK"
    EXPERIMENT_SUBMISSION = "EXPERIMENT_SUBMISSION"


class RiskSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EvidenceType(StrEnum):
    CLOUD_VERIFIED = "CLOUD_VERIFIED"
    LOCAL_ATTESTED = "LOCAL_ATTESTED"
    USER_PROVIDED = "USER_PROVIDED"


class EvidenceApplicability(StrEnum):
    """证据字段适用于本次实验，或已明确说明不适用。"""

    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class SubmissionStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class WorkflowStep(StrEnum):
    UPLOAD_VERIFICATION = "UPLOAD_VERIFICATION"
    CONFIG_PARSE = "CONFIG_PARSE"
    MANIFEST_VALIDATION = "MANIFEST_VALIDATION"
    DUPLICATE_CHECK = "DUPLICATE_CHECK"
    RISK_ANALYSIS = "RISK_ANALYSIS"
    SUMMARY_GENERATION = "SUMMARY_GENERATION"
    EMBEDDING_GENERATION = "EMBEDDING_GENERATION"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class ExperimentStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DEPRECATED = "DEPRECATED"
    SUPERSEDED = "SUPERSEDED"


class ArtifactType(StrEnum):
    CONFIG = "CONFIG"
    LOG = "LOG"
    RESULT = "RESULT"
    NOTE = "NOTE"
    MANIFEST = "MANIFEST"


class IdempotencyOperationStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ConfigFormat(StrEnum):
    YAML = "yaml"
    JSON = "json"
