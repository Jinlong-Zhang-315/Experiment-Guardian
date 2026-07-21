"""领域状态枚举。

所有 API、MCP 工具、数据库模型和工作流都应复用本文件中的枚举。状态只在一处定义，
可以避免接口返回值与数据库状态逐渐发生偏差。
"""

from enum import StrEnum


class TeamRole(StrEnum):
    OWNER = "OWNER"
    RESEARCHER = "RESEARCHER"


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


class CheckResult(StrEnum):
    PASS = "PASS"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    BLOCKED = "BLOCKED"


class ApprovalStatus(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
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
