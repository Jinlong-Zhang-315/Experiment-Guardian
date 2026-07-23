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
    UPLOAD_VERIFIED = "UPLOAD_VERIFIED"
    PROCESSING = "PROCESSING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class WorkflowStatus(StrEnum):
    """Submission 分析游标的持久化状态，不依赖 LangGraph checkpoint。"""

    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    QUEUED = "QUEUED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"
    AWAITING_ENRICHMENT = "AWAITING_ENRICHMENT"
    COMPLETED = "COMPLETED"


class WorkflowStep(StrEnum):
    UPLOAD_VERIFICATION = "UPLOAD_VERIFICATION"
    CONFIG_PARSE = "CONFIG_PARSE"
    MANIFEST_VALIDATION = "MANIFEST_VALIDATION"
    DUPLICATE_CHECK = "DUPLICATE_CHECK"
    RISK_ANALYSIS = "RISK_ANALYSIS"
    SUMMARY_GENERATION = "SUMMARY_GENERATION"
    EMBEDDING_GENERATION = "EMBEDDING_GENERATION"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class WorkflowJobType(StrEnum):
    """异步提交分析任务；每类任务在一个 Submission 下保持单例。"""

    SUBMISSION_SUMMARY = "SUBMISSION_SUMMARY"
    SUBMISSION_REVIEW_PREPARATION = "SUBMISSION_REVIEW_PREPARATION"


class ReviewEligibility(StrEnum):
    """审核回执计算出的确认权限，不等同于用户当前的实际角色。"""

    RESEARCHER_OR_OWNER = "RESEARCHER_OR_OWNER"
    OWNER_ONLY = "OWNER_ONLY"
    BLOCKED = "BLOCKED"


class WorkflowJobStatus(StrEnum):
    """数据库 Job 的生命周期独立于 Submission 的业务状态。"""

    PENDING_DISPATCH = "PENDING_DISPATCH"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    SUCCEEDED = "SUCCEEDED"
    DEAD_LETTER = "DEAD_LETTER"
    FAILED = "FAILED"


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    COMPLETED = "COMPLETED"
    DEAD_LETTER = "DEAD_LETTER"


class ExperimentStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DEPRECATED = "DEPRECATED"
    SUPERSEDED = "SUPERSEDED"


class SubmittedRunStatus(StrEnum):
    """本地 Agent 在上传前声明的单次运行结果。"""

    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class UploadVerificationResult(StrEnum):
    PASS = "PASS"
    FAILED = "FAILED"


class ArtifactVerificationIssueCode(StrEnum):
    OBJECT_MISSING = "OBJECT_MISSING"
    CONTENT_LENGTH_MISMATCH = "CONTENT_LENGTH_MISMATCH"
    CONTENT_TYPE_MISMATCH = "CONTENT_TYPE_MISMATCH"
    CHECKSUM_SHA256_MISSING = "CHECKSUM_SHA256_MISSING"
    CHECKSUM_SHA256_MISMATCH = "CHECKSUM_SHA256_MISMATCH"
    S3_VERSION_ID_MISSING = "S3_VERSION_ID_MISSING"


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


class AgentThreadStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class AgentMessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


class AgentRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"


class AgentCallStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"


class AgentModelCallPurpose(StrEnum):
    AGENT_TURN = "AGENT_TURN"
    CONTEXT_SUMMARY = "CONTEXT_SUMMARY"


class AgentContextSummaryStatus(StrEnum):
    READY = "READY"
    FAILED = "FAILED"


class AgentEvidenceKind(StrEnum):
    CONFIRMED_FACT = "CONFIRMED_FACT"
    USER_PROVIDED = "USER_PROVIDED"
    CANDIDATE_DRAFT = "CANDIDATE_DRAFT"
    ACTION_PROPOSAL = "ACTION_PROPOSAL"
    ANALYSIS = "ANALYSIS"
    HYPOTHESIS = "HYPOTHESIS"


class PolicyDraftStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ABANDONED = "ABANDONED"


class PolicyDraftReadiness(StrEnum):
    READY = "READY"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    INVALID = "INVALID"


class PolicyDraftFreshness(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"


class PolicyDraftSource(StrEnum):
    AGENT = "AGENT"
    WEB = "WEB"


class ActionProposalOperation(StrEnum):
    POLICY_PUBLISH = "POLICY_PUBLISH"


class ActionProposalStatus(StrEnum):
    PROPOSED = "PROPOSED"
    EXECUTED = "EXECUTED"
    CANCELED = "CANCELED"
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class ActionProposalConfirmability(StrEnum):
    READY = "READY"
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    TERMINAL = "TERMINAL"


class ConfigFormat(StrEnum):
    YAML = "yaml"
    JSON = "json"
